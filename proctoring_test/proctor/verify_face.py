import numpy as np
import cv2
import base64
import os
from django.conf import settings

# KNN logic
def distance(v1, v2):
    return np.sqrt(((v1 - v2) ** 2).sum())

def knn(train, test, k=5):
    dist = []
    for i in range(train.shape[0]):
        ix = train[i, :-1]
        iy = train[i, -1]
        d = distance(test, ix)
        dist.append([d, iy])
    dk = sorted(dist, key=lambda x: x[0])[:k]
    labels = np.array(dk)[:, -1]
    output = np.unique(labels, return_counts=True)
    index = np.argmax(output[1])
    return output[0][index]

def load_face_dataset():
    dataset_path = os.path.join(settings.BASE_DIR, 'Real-time-Face-Recognition-Project', 'face_dataset')

    face_data = []
    labels = []
    class_id = 0
    names = {}

    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")

    for fx in os.listdir(dataset_path):
        if fx.endswith('.npy'):
            names[class_id] = fx[:-4]
            data_item = np.load(os.path.join(dataset_path, fx))
            face_data.append(data_item)
            target = class_id * np.ones((data_item.shape[0],))
            labels.append(target)
            class_id += 1

    if len(face_data) == 0:
        raise ValueError("No face data found in dataset directory.")

    face_dataset = np.concatenate(face_data, axis=0)
    face_labels = np.concatenate(labels, axis=0).reshape((-1, 1))
    trainset = np.concatenate((face_dataset, face_labels), axis=1)

    return trainset, names

def verify_face_from_base64(base64_data):
    try:
        trainset, names = load_face_dataset()

        # Decode base64 to image
        img_data = base64.b64decode(base64_data.split(',')[1])
        np_arr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml')
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        if len(faces) == 0:
            return "No face detected"

        # Only verify the first detected face
        x, y, w, h = faces[0]
        offset = 5
        face_section = img[y-offset:y+h+offset, x-offset:x+w+offset]
        face_section = cv2.resize(face_section, (100, 100))

        predicted_class_id = knn(trainset, face_section.flatten())
        return names[int(predicted_class_id)]
    except Exception as e:
        return f"Error: {str(e)}"
