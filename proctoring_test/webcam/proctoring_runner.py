import cv2
import dlib
import numpy as np
import math
import time
import csv
from collections import deque
from playsound import playsound
import os


CHEATING_YAW_THRESHOLD = 10  # degrees
CONSECUTIVE_FRAMES_THRESHOLD = 10
NO_FACE_GRACE_PERIOD = 10
MIN_FACE_SIZE_RATIO = 0.05
FACE_DISTANCE_THRESHOLD = 80
YAW_SMOOTHING_WINDOW = 5
KALMAN_Q = 0.01
KALMAN_R = 0.5
LOG_COOLDOWN = 2  # seconds

ALARM_SOUND_PATH = r'C:\Users\Asus\OneDrive\Desktop\OpenCV\snapshots\alarm.mp3'

predictor_path = os.path.join(os.path.dirname(__file__), 'shape_predictor_68_face_landmarks.dat')
predictor = dlib.shape_predictor(predictor_path)
face_detector = dlib.get_frontal_face_detector()

model_points = np.array([
    (0.0, 0.0, 0.0),
    (0.0, -330.0, -65.0),
    (-225.0, 170.0, -135.0),
    (225.0, 170.0, -135.0),
    (-150.0, -150.0, -125.0),
    (150.0, -150.0, -125.0)
])


face_states = {}
next_face_id = 0
no_face_counter = 0
last_log_time = 0
log = []


def create_kalman_filter():
    kf = cv2.KalmanFilter(1, 1)
    kf.transitionMatrix = np.array([[1.]], np.float32)
    kf.measurementMatrix = np.array([[1.]], np.float32)
    kf.processNoiseCov = np.array([[KALMAN_Q]], np.float32)
    kf.measurementNoiseCov = np.array([[KALMAN_R]], np.float32)
    kf.errorCovPost = np.array([[1.]], np.float32)
    kf.statePost = np.array([[0.]], np.float32)
    return kf


cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

fps_counter = 0
fps = 0
start_time = time.time()


if not os.path.exists('snapshots'):
    os.makedirs('snapshots')

while True:
    ret, frame = cap.read()
    if not ret:
        break

    fps_counter += 1
    if time.time() - start_time >= 1.0:
        fps = fps_counter
        fps_counter = 0
        start_time = time.time()

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = frame.shape[:2]
    MIN_FACE_SIZE = int(height * MIN_FACE_SIZE_RATIO)**2

    faces = face_detector(gray, 0)
    current_faces = []

    for face in faces:
        fw = face.right() - face.left()
        fh = face.bottom() - face.top()
        if fw * fh >= MIN_FACE_SIZE:
            current_faces.append(face)

    active_face_ids = []

    for face_rect in current_faces:
        startX, startY, endX, endY = face_rect.left(
        ), face_rect.top(), face_rect.right(), face_rect.bottom()
        face_center = ((startX + endX) // 2, (startY + endY) // 2)
        face_size = (endX - startX) * (endY - startY)

        face_id = None
        min_distance = float('inf')
        for fid, state in face_states.items():
            distance = math.dist(face_center, state['center'])
            size_ratio = max(
                face_size, state['size']) / min(face_size, state['size'])
            if distance < FACE_DISTANCE_THRESHOLD and size_ratio < 1.5 and distance < min_distance:
                min_distance = distance
                face_id = fid

        if face_id is None:
            face_id = next_face_id
            next_face_id += 1
            face_states[face_id] = {
                'center': face_center,
                'size': face_size,
                'yaw_history': deque(maxlen=YAW_SMOOTHING_WINDOW),
                'cheating_counter': 0,
                'cheating_flag': False,
                'kalman': create_kalman_filter(),
                'last_log_time': 0
            }
        else:
            face_states[face_id]['center'] = face_center
            face_states[face_id]['size'] = face_size

        active_face_ids.append(face_id)

        cv2.rectangle(frame, (startX, startY), (endX, endY), (255, 0, 0), 2)
        cv2.putText(frame, f"ID: {face_id}", (startX, startY - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

        landmarks = predictor(gray, face_rect)
        image_points = np.array([
            (landmarks.part(30).x, landmarks.part(30).y),
            (landmarks.part(8).x, landmarks.part(8).y),
            (landmarks.part(36).x, landmarks.part(36).y),
            (landmarks.part(45).x, landmarks.part(45).y),
            (landmarks.part(48).x, landmarks.part(48).y),
            (landmarks.part(54).x, landmarks.part(54).y)
        ], dtype="double")

        focal_length = width
        center_cam = (width/2, height/2)
        camera_matrix = np.array([[focal_length, 0, center_cam[0]],
                                  [0, focal_length, center_cam[1]],
                                  [0, 0, 1]], dtype="double")
        dist_coeffs = np.zeros((4, 1))

        success, rotation_vector, translation_vector = cv2.solvePnP(
            model_points, image_points, camera_matrix, dist_coeffs)

        rmat, _ = cv2.Rodrigues(rotation_vector)
        sy = math.sqrt(rmat[0, 0]**2 + rmat[1, 0]**2)
        singular = sy < 1e-6
        yaw = math.degrees(math.atan2(
            rmat[1, 0], rmat[0, 0])) if not singular else 0

        kf = face_states[face_id]['kalman']
        kf.predict()
        measurement = np.array([[yaw]], dtype=np.float32)
        kf.correct(measurement)
        smoothed_yaw = kf.statePost[0, 0]
        face_states[face_id]['yaw_history'].append(smoothed_yaw)
        final_yaw = np.mean(face_states[face_id]['yaw_history'])

        nose_end_point3D = np.array([[0, 0, 1000.0]])
        nose_end_point2D, _ = cv2.projectPoints(
            nose_end_point3D, rotation_vector, translation_vector, camera_matrix, dist_coeffs)
        p1 = (int(image_points[0][0]), int(image_points[0][1]))
        p2 = (int(nose_end_point2D[0][0][0]), int(nose_end_point2D[0][0][1]))
        cv2.line(frame, p1, p2, (0, 255, 0), 2)

        for i in range(0, 68):
            x = landmarks.part(i).x
            y = landmarks.part(i).y
            cv2.circle(frame, (x, y), 1, (0, 0, 255), -1)

        if abs(final_yaw) > CHEATING_YAW_THRESHOLD:
            face_states[face_id]['cheating_counter'] += 1
            if (face_states[face_id]['cheating_counter'] > CONSECUTIVE_FRAMES_THRESHOLD and
                not face_states[face_id]['cheating_flag'] and
                    time.time() - face_states[face_id]['last_log_time'] > LOG_COOLDOWN):
                face_states[face_id]['cheating_flag'] = True
                face_states[face_id]['last_log_time'] = time.time()
                # === SOUND ALERT ===
                playsound(ALARM_SOUND_PATH, block=False)
                # === SNAPSHOT ===
                face_img = frame[startY:endY, startX:endX]
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                cv2.imwrite(
                    f'snapshots/face_{face_id}_{timestamp}.png', face_img)
                # === LOG ===
                log.append((time.strftime("%H:%M:%S"),
                           f"Face {face_id} CHEATING! Yaw: {final_yaw:.1f}°"))
        else:
            face_states[face_id]['cheating_counter'] = max(
                0, face_states[face_id]['cheating_counter'] - 1)
            if face_states[face_id]['cheating_counter'] < CONSECUTIVE_FRAMES_THRESHOLD // 2:
                face_states[face_id]['cheating_flag'] = False

        status_color = (
            0, 255, 0) if not face_states[face_id]['cheating_flag'] else (0, 0, 255)
        status_text = "Normal" if not face_states[face_id]['cheating_flag'] else "CHEATING!"
        cv2.putText(frame, f"ID {face_id}: {status_text} (Yaw: {final_yaw:.1f}°)",
                    (10, 30 + face_id * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)

    for fid in list(face_states.keys()):
        if fid not in active_face_ids:
            del face_states[fid]

    if len(current_faces) == 0:
        no_face_counter += 1
        if no_face_counter > NO_FACE_GRACE_PERIOD and time.time() - last_log_time > LOG_COOLDOWN:
            log.append((time.strftime("%H:%M:%S"), "No face detected!"))
            last_log_time = time.time()
    else:
        no_face_counter = 0

    cv2.putText(frame, f"FPS: {fps}", (width - 150, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    cv2.imshow("Proctoring System", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()


with open('proctoring_log.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Time', 'Event'])
    for t, msg in log:
        writer.writerow([t, msg])

print("\n=== PROCTORING LOG ===")
for t, msg in log:
    print(f"[{t}] {msg}")

print("\nLog saved to proctoring_log.csv and snapshots saved in snapshots/ folder.")
