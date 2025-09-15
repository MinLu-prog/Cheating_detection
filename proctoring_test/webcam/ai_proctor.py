import os
import time
import cv2
import dlib
import numpy as np
from datetime import datetime
from collections import deque, defaultdict
from typing import Tuple, List
from ultralytics import YOLO

# ================= CONFIGURATION =================
class Config:
    LANDMARKS_MODEL = "webcam/shape_predictor_68_face_landmarks.dat"
    YOLO_MODEL = "webcam/yolov8n.pt"

    # Thresholds
    HEAD_YAW_THRESHOLD = 25
    GAZE_THRESHOLD_LEFT = 1.5
    GAZE_THRESHOLD_RIGHT = 0.8
    OBJECT_CONFIDENCE = 0.4

    # ---- Time-based gating (FPS-agnostic) ----
    ALERT_DURATION = 2.0           # seconds of sustained evidence required to TRIGGER
    EVIDENCE_WINDOW_SECONDS = 2.0  # sliding window (seconds) for evidence ratio
    EVIDENCE_MIN_TRUE_RATIO = 0.6  # fraction of 'True' samples within window
    ALERT_CLEAR_HOLD_SECONDS = 1.0 # linger overlay after evidence clears (seconds)
    ALERT_COOLDOWN = 5.0           # min seconds between snapshot/logs per alert type

    # (Legacy; no longer used, kept for compatibility)
    CONSECUTIVE_FRAMES = 5

    SNAPSHOT_DIR = "proctoring_snapshots"
    LOG_FILE = "proctoring_log.txt"
    FONT = cv2.FONT_HERSHEY_SIMPLEX


# ================= ALERT MANAGER =================
class AlertManager:
    """
    FPS-agnostic time-gated alerting:
      - Samples each alert_type as (timestamp, condition) in a sliding window.
      - Triggers when evidence ratio in the last EVIDENCE_WINDOW_SECONDS >= threshold
        and condition persists for at least ALERT_DURATION (arming -> triggered).
      - Snapshot/logs obey ALERT_COOLDOWN per alert type.
      - Overlay remains visible while arming/triggered, and lingers for ALERT_CLEAR_HOLD_SECONDS.
    """
    def __init__(self, config: Config):
        self.config = config
        self.alert_states = {}           # per-type state dicts
        self.last_alert_times = {}       # per-type last trigger wall time (monotonic)
        self.alert_history = []          # [{type, timestamp, snapshot}]
        os.makedirs(self.config.SNAPSHOT_DIR, exist_ok=True)

        self.alert_messages = {
            'head_pose': "⚠️ HEAD TURNED - Look at screen!",
            'eye_gaze': "⚠️ EYES LOOKING AWAY - Look at center!",
            'multi_face': "⚠️ MULTIPLE FACES DETECTED!",
            'phone': "⚠️ PHONE DETECTED - Remove phone!",
            'book': "⚠️ UNAUTHORIZED MATERIAL DETECTED!",
            'no_face': "⚠️ NO FACE DETECTED - Show your face!",
        }
        self.alert_colors = {
            'head_pose': (0, 0, 255),
            'eye_gaze': (0, 165, 255),
            'multi_face': (0, 0, 255),
            'phone': (0, 165, 255),
            'book': (0, 255, 255),
            'no_face': (128, 128, 128),
        }

        # Sliding-window samples: per-type deque[(t_monotonic, bool)]
        self.samples = defaultdict(lambda: deque())

    def _state(self, alert_type: str):
        if alert_type not in self.alert_states:
            self.alert_states[alert_type] = {
                'phase': 'idle',            # idle | arming | triggered | cooldown
                'armed_at': None,           # monotonic time when arming started
                'last_true_at': None,       # last time condition was True
                'last_triggered_at': -1e9,  # monotonic time of last trigger
            }
            self.last_alert_times[alert_type] = -1e9
        return self.alert_states[alert_type]

    def check_alert(self, alert_type: str, condition: bool, frame) -> bool:
        """
        Returns True if overlay should be visible ('arming' or 'triggered').
        Triggers snapshot/log when transitioning to 'triggered' (respecting cooldown).
        """
        now = time.monotonic()
        st = self._state(alert_type)

        # Record sample & prune window
        dq = self.samples[alert_type]
        dq.append((now, condition))
        win = self.config.EVIDENCE_WINDOW_SECONDS
        while dq and (now - dq[0][0]) > win:
            dq.popleft()

        # Evidence ratio in window
        true_ratio = (sum(1 for t, c in dq if c) / len(dq)) if dq else 0.0
        if condition:
            st['last_true_at'] = now

        cooldown_ok = (now - st['last_triggered_at']) >= self.config.ALERT_COOLDOWN
        enough_evidence = (true_ratio >= self.config.EVIDENCE_MIN_TRUE_RATIO)

        # State machine
        if enough_evidence:
            if st['phase'] in ('idle', 'cooldown'):
                st['phase'] = 'arming'
                st['armed_at'] = now
            elif st['phase'] == 'arming':
                if (now - (st['armed_at'] or now)) >= self.config.ALERT_DURATION and cooldown_ok:
                    self.trigger_alert(alert_type, frame)
                    st['phase'] = 'triggered'
                    st['last_triggered_at'] = now
        else:
            st['armed_at'] = None
            if st['phase'] == 'arming':
                st['phase'] = 'idle'
            elif st['phase'] == 'triggered':
                last_true = st['last_true_at'] or now
                if (now - last_true) >= self.config.ALERT_CLEAR_HOLD_SECONDS:
                    st['phase'] = 'cooldown'

        if st['phase'] == 'cooldown' and not enough_evidence:
            st['phase'] = 'idle'

        return st['phase'] in ('arming', 'triggered')

    def trigger_alert(self, alert_type: str, frame) -> None:
        timestamp = datetime.now()
        snapshot_path = self.save_snapshot(frame, alert_type, timestamp)
        self.log_alert(alert_type, timestamp, snapshot_path)
        self.alert_history.append({'type': alert_type, 'timestamp': timestamp, 'snapshot': snapshot_path})
        print(f"[ALERT] {timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.alert_messages.get(alert_type, alert_type)}")

    def save_snapshot(self, frame, alert_type: str, timestamp: datetime) -> str:
        fname = f"{alert_type}_{timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = os.path.join(self.config.SNAPSHOT_DIR, fname)
        try:
            cv2.imwrite(filepath, frame)
        except Exception:
            # best-effort; if disk fails, still continue
            pass
        return filepath

    def log_alert(self, alert_type: str, timestamp: datetime, snapshot_path: str) -> None:
        entry = f"{timestamp.isoformat()} - {alert_type.upper()} - {snapshot_path}\n"
        try:
            with open(self.config.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            # best-effort logging
            pass

    def draw_alerts(self, frame, active_alerts: List[str]):
        y_offset = 30
        for alert_type in active_alerts:
            color = self.alert_colors.get(alert_type, (255, 255, 255))
            message = self.alert_messages.get(alert_type, "⚠️ ALERT!")
            text_size = cv2.getTextSize(message, self.config.FONT, 0.7, 2)[0]
            cv2.rectangle(frame, (10, y_offset - 25), (20 + text_size[0], y_offset + 5), (0, 0, 0), -1)
            cv2.putText(frame, message, (15, y_offset), self.config.FONT, 0.7, color, 2)
            y_offset += 40
        return frame


# ================= DETECTION SYSTEM =================
class DetectionSystem:
    def __init__(self, process_with_camera: bool = False, camera_index: int = 0):
        self.config = Config()
        self.alert_manager = AlertManager(self.config)
        self._using_camera = False
        self.cap = None
        if process_with_camera:
            self.start_camera(camera_index)

        # Models
        self.face_detector = dlib.get_frontal_face_detector()
        self.predictor = dlib.shape_predictor(self.config.LANDMARKS_MODEL)
        self.yolo = YOLO(self.config.YOLO_MODEL)

        self.yaw_history = deque(maxlen=10)
        self.gaze_history = deque(maxlen=5)
        self.stats = {'session_start': time.time()}

    def start_camera(self, camera_index: int = 0):
        if not self._using_camera:
            self.cap = cv2.VideoCapture(camera_index)
            self._using_camera = True

    def stop_camera(self):
        if self._using_camera and self.cap:
            self.cap.release()
            self._using_camera = False

    # ---------------- CORE ----------------
    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, List[str]]:
        if frame is None:
            return frame, []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_detector(gray, 0)
        active_alerts = []

        # No face / multi-face
        if len(faces) == 0:
            if self.alert_manager.check_alert('no_face', True, frame):
                active_alerts.append('no_face')
        else:
            self.alert_manager.check_alert('no_face', False, frame)
            if len(faces) > 1:
                if self.alert_manager.check_alert('multi_face', True, frame):
                    active_alerts.append('multi_face')
            else:
                self.alert_manager.check_alert('multi_face', False, frame)

        # Head/Gaze alerts
        if len(faces) > 0:
            face = faces[0]
            landmarks = self.predictor(gray, face)

            # Head pose
            yaw = self.calculate_head_pose(landmarks, frame.shape)
            if abs(yaw) > self.config.HEAD_YAW_THRESHOLD:
                if self.alert_manager.check_alert('head_pose', True, frame):
                    active_alerts.append('head_pose')
            else:
                self.alert_manager.check_alert('head_pose', False, frame)

            # Eye gaze
            direction, ratio = self.calculate_eye_gaze(landmarks, gray)
            if direction in ("LEFT", "RIGHT"):
                if self.alert_manager.check_alert('eye_gaze', True, frame):
                    active_alerts.append('eye_gaze')
            else:
                self.alert_manager.check_alert('eye_gaze', False, frame)

        # YOLO object detection
        detected, annotated_frame = self.detect_objects(frame)
        frame = annotated_frame
        for alert_type, key in [('phone', 'phones'), ('book', 'books')]:
            if detected.get(key, 0) > 0:
                if self.alert_manager.check_alert(alert_type, True, frame):
                    active_alerts.append(alert_type)
            else:
                self.alert_manager.check_alert(alert_type, False, frame)

        frame = self.alert_manager.draw_alerts(frame, active_alerts)
        self.draw_stats(frame)

        return frame, active_alerts

    # ---------------- STATUS FOR FRONTEND ----------------
    def get_status(self):
        active_now = [
            atype
            for atype, st in self.alert_manager.alert_states.items()
            if st.get('phase') in ('arming', 'triggered')
        ]
        return {
            "terminated": False,
            "reason": "",
            "warning": None,
            "active_alerts": active_now
        }

    # ---------------- HELPERS ----------------
    def calculate_head_pose(self, landmarks, frame_shape) -> float:
        # simplified PnP head pose
        image_points = np.array([
            (landmarks.part(30).x, landmarks.part(30).y),
            (landmarks.part(8).x, landmarks.part(8).y),
            (landmarks.part(36).x, landmarks.part(36).y),
            (landmarks.part(45).x, landmarks.part(45).y),
            (landmarks.part(48).x, landmarks.part(48).y),
            (landmarks.part(54).x, landmarks.part(54).y),
        ], dtype="double")

        h, w = frame_shape[:2]
        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype="double")
        dist_coeffs = np.zeros((4, 1))

        model_points = np.array([
            (0.0, 0.0, 0.0),
            (0.0, -330.0, -65.0),
            (-225.0, 170.0, -135.0),
            (225.0, 170.0, -135.0),
            (-150.0, -150.0, -125.0),
            (150.0, -150.0, -125.0),
        ])

        try:
            success, rvec, tvec = cv2.solvePnP(
                model_points, image_points, camera_matrix, dist_coeffs,
                flags=cv2.SOLVEPNP_ITERATIVE
            )
        except Exception:
            return 0.0
        if not success:
            return 0.0
        rmat, _ = cv2.Rodrigues(rvec)
        proj = np.hstack((rmat, tvec))
        angles = cv2.decomposeProjectionMatrix(proj)[6]
        yaw = float(angles[1])
        return yaw

    def calculate_eye_gaze(self, landmarks, gray) -> Tuple[str, float]:
        # simplified horizontal gaze ratio
        def get_ratio(eye_points):
            region = np.array([(landmarks.part(p).x, landmarks.part(p).y) for p in eye_points], np.int32)
            mask = np.zeros_like(gray)
            cv2.fillPoly(mask, [region], 255)
            eye = cv2.bitwise_and(gray, gray, mask=mask)
            _, thresh = cv2.threshold(eye, 70, 255, cv2.THRESH_BINARY)
            hh, ww = thresh.shape
            if hh == 0 or ww == 0:
                return 1.0
            left = thresh[:, :ww//2]
            right = thresh[:, ww//2:]
            return (cv2.countNonZero(left)+1)/(cv2.countNonZero(right)+1)

        left_ratio = get_ratio([36, 37, 38, 39, 40, 41])
        right_ratio = get_ratio([42, 43, 44, 45, 46, 47])
        hratio = (left_ratio + right_ratio) / 2.0

        direction = "CENTER"
        if hratio < 0.8:
            direction = "RIGHT"
        elif hratio > 1.5:
            direction = "LEFT"
        return direction, hratio

    def detect_objects(self, frame):
        detected = {'persons': 0, 'phones': 0, 'books': 0}
        annotated = frame
        try:
            results = self.yolo.predict(frame, conf=self.config.OBJECT_CONFIDENCE, verbose=False)
            if results and len(results) > 0 and getattr(results[0], "boxes", None) is not None:
                boxes = results[0].boxes
                classes = boxes.cls.cpu().numpy().astype(int)
                names_map = getattr(self.yolo, 'names', {})
                names = [names_map.get(int(c), str(int(c))) for c in classes]
                detected['persons'] = names.count('person')
                detected['phones'] = names.count('cell phone')
                detected['books'] = names.count('book')
                annotated = results[0].plot()
        except Exception:
            # YOLO may fail gracefully; keep original frame
            pass
        return detected, annotated

    def draw_stats(self, frame):
        try:
            h, w = frame.shape[:2]
            cv2.rectangle(frame, (10, h - 100), (280, h - 10), (0, 0, 0), -1)
            cv2.putText(frame, f"Session Time: {int(time.time() - self.stats['session_start'])}s",
                        (15, h-70), self.config.FONT, 0.5, (255, 255, 255), 1)
            cv2.putText(frame, f"Active Alerts: {len(self.alert_manager.alert_history)}",
                        (15, h-40), self.config.FONT, 0.5, (0, 255, 255), 1)
        except Exception:
            pass


# --------------- Optional: quick camera loop for local testing ---------------
if __name__ == "__main__":
    ds = DetectionSystem(process_with_camera=True, camera_index=0)
    try:
        while True:
            ok, frame = ds.cap.read()
            if not ok:
                break
            frame, _ = ds.process_frame(frame)
            cv2.imshow("Proctoring (time-gated)", frame)
            if cv2.waitKey(1) & 0xFF == 27:  # ESC to exit
                break
    finally:
        ds.stop_camera()
        cv2.destroyAllWindows()
