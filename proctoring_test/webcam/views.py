# views.py
import base64
import cv2
import numpy as np
import os
import time
import json
import re
import traceback
import face_recognition
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
from django.conf import settings

from .ai_proctor import DetectionSystem
from proctor.proctor_log import log_event
from proctor.models import Quiz
from .proctor_instance import get_detector
from proctor.models import ProctorAlert
from django.core.files.base import ContentFile




# -------------------- DetectionSystem --------------------
# Single instance, can process frames from browser
ds = get_detector()



@csrf_exempt
def quiz_ai_stream(request, quiz_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth_required"}, status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id)
     # --- Set student and quiz for DetectionSystem alerts ---
    ds.current_student = request.user
    ds.current_quiz_id = quiz.id

    try:
        # -------------------- Load references per user --------------------
        import os
        from django.conf import settings


        user_folder = os.path.join(str(settings.BASE_DIR), "faces", str(request.user.username))
        print("[DEBUG] Looking for face references in:", user_folder)

        if getattr(ds, "_loaded_user", None) != request.user.username:
            if not ds.reference_encodings:
                print(f"[DEBUG] Loading reference images for {request.user.username}...")
                ds.load_reference_images(user_folder)
                ds._loaded_user = request.user.username
            else:
                print(f"[DEBUG] Using cached encodings for {ds._loaded_user}")


        # -------------------- Decode incoming frame --------------------
        data = request.POST.get("frame")
        if not data or "," not in data:
            return JsonResponse({"error": "missing_or_invalid_frame"}, status=400)

        _, encoded = data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # BGR uint8

        if frame is None:
            log_event(
                request.user, quiz, "decode_error",
                message="cv2.imdecode returned None",
                severity="error",
                metadata={"where": "quiz_ai_stream"},
                frame_b64=data,
            )
            return JsonResponse({"error": "decode_failed"}, status=400)

        # -------------------- Main frame processing --------------------
        try:
            annotated_frame, meta = ds.process_frame(frame)
        except Exception as e:
            print("[DEBUG] process_frame error:", e)
            annotated_frame, meta = frame, {}

        # Log main alerts
        if isinstance(meta, dict):
            for code in meta.get("active_alerts", []):
                log_event(
                    request.user, quiz, code,
                    message=f"Alert: {code}",
                    severity="warn",
                    metadata=meta,
                )
            if meta.get("terminated"):
                log_event(
                    request.user, quiz, "terminated",
                    message=meta.get("reason", "terminated"),
                    severity="error",
                    metadata=meta,
                    frame_b64=data,
                )

        # -------------------- Multi-face detection --------------------
        if hasattr(ds, "process_multiface"):
            try:
                multi_meta = ds.process_multiface(frame)
                if isinstance(multi_meta, dict):
                    for code in multi_meta.get("active_alerts", []):
                        log_event(
                            request.user, quiz, code,
                            message=f"Multi-face Alert: {code}",
                            severity="warn",
                            metadata=multi_meta,
                        )
                    if multi_meta.get("terminated"):
                        log_event(
                            request.user, quiz, "terminated",
                            message=multi_meta.get("reason", "terminated"),
                            severity="error",
                            metadata=multi_meta,
                            frame_b64=data,
                        )
            except Exception as e:
                log_event(
                    request.user, quiz, "multiface_error",
                    message=str(e),
                    severity="error",
                    metadata={"where": "quiz_ai_stream_multiface"},
                )

        # -------------------- Flexible head pose --------------------
        if hasattr(ds, "detect_head_pose"):
            try:
                head_pose_meta = ds.detect_head_pose(frame)
                if isinstance(head_pose_meta, dict):
                    for code in head_pose_meta.get("active_alerts", []):
                        log_event(
                            request.user, quiz, code,
                            message=f"Head Pose Alert: {code}",
                            severity="warn",
                            metadata=head_pose_meta,
                        )
            except Exception as e:
                log_event(
                    request.user, quiz, "headpose_error",
                    message=str(e),
                    severity="error",
                    metadata={"where": "quiz_ai_stream_headpose"},
                )

        # -------------------- Audio detection --------------------
        if hasattr(ds, "audio_detector"):
            try:
                audio_meta = ds.audio_detector.process_audio(request.user)
                if isinstance(audio_meta, dict):
                    for code in audio_meta.get("active_alerts", []):
                        log_event(
                            request.user, quiz, code,
                            message=f"Audio Alert: {code}",
                            severity="warn",
                            metadata=audio_meta,
                        )
                    if audio_meta.get("terminated"):
                        log_event(
                            request.user, quiz, "terminated",
                            message=audio_meta.get("reason", "terminated"),
                            severity="error",
                            metadata=audio_meta,
                        )
            except Exception as e:
                log_event(
                    request.user, quiz, "audio_error",
                    message=str(e),
                    severity="error",
                    metadata={"where": "quiz_ai_stream_audio"},
                )

        # -------------------- Encode annotated frame --------------------
        ok, buffer = cv2.imencode('.jpg', annotated_frame)
        if not ok:
            log_event(
                request.user, quiz, "encode_error",
                message="cv2.imencode failed",
                severity="error",
                metadata={"where": "quiz_ai_stream"},
            )
            return JsonResponse({"error": "encode_failed"}, status=500)

        frame_b64 = base64.b64encode(buffer).decode('utf-8')
        return JsonResponse({"frame": f"data:image/jpeg;base64,{frame_b64}"})

    except Exception as e:
        traceback_str = traceback.format_exc()
        print("[STREAM ERROR]", traceback_str)  # <-- print full error
        log_event(
            request.user, quiz, "stream_error",
            message=str(e),
            severity="error",
            metadata={
                "where": "quiz_ai_stream",
                "trace": traceback_str[:1500],
                "frame_present": bool(request.POST.get("frame"))
            },
            frame_b64=request.POST.get("frame"),
        )
        return JsonResponse({"error": "server_exception", "detail": repr(e)}, status=500)

# -------------------- AI status polling --------------------
def quiz_ai_status(request, quiz_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth_required"}, status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id)

    # --- Get current AI status ---
    status = ds.get_status() or {}

    # --- Log any alerts ---
    for code in status.get("active_alerts", []):
        log_event(
            request.user, quiz, code,
            message=f"Alert: {code}",
            severity="warn",
            metadata=status,
        )

    if status.get("terminated"):
        log_event(
            request.user, quiz, "terminated",
            message=status.get("reason", "terminated"),
            severity="error",
            metadata=status,
        )

    return JsonResponse(status)


# -------------------- Face capture & verification --------------------
FACE_RECOGNITION_ENABLED = False

_face_dataset_cache = None
_labels_cache = None
_names_cache = None
_trainset_cache = None
_last_cache_time = 0
_dynamic_threshold = None
CACHE_TIMEOUT = 300

def _load_face_dataset():
    global _face_dataset_cache, _labels_cache, _names_cache, _trainset_cache, _last_cache_time, _dynamic_threshold
    
    current_time = time.time()
    if (_face_dataset_cache is not None and current_time - _last_cache_time < CACHE_TIMEOUT):
        return _face_dataset_cache, _labels_cache, _names_cache, _trainset_cache, _dynamic_threshold
    
    dataset_path = os.path.join(settings.BASE_DIR, 'Real-time-Face-Recognition-Project', 'face_dataset')
    face_data, labels, names, class_id = [], [], {}, 0

    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Face dataset directory not found: {dataset_path}")

    for fx in os.listdir(dataset_path):
        if fx.endswith('.npy'):
            username = fx[:-4]
            names[class_id] = username
            data_item = np.load(os.path.join(dataset_path, fx))
            face_data.append(data_item)
            labels.append(class_id * np.ones((data_item.shape[0],)))
            class_id += 1

    if len(face_data) == 0:
        raise ValueError("No face datasets found in the directory")

    face_dataset = np.concatenate(face_data, axis=0)
    face_labels = np.concatenate(labels, axis=0).reshape((-1, 1))
    trainset = np.concatenate((face_dataset, face_labels), axis=1)
    
    _dynamic_threshold = 2000
    _face_dataset_cache = face_dataset
    _labels_cache = face_labels
    _names_cache = names
    _trainset_cache = trainset
    _last_cache_time = current_time
    
    return face_dataset, face_labels, names, trainset, _dynamic_threshold


@csrf_exempt
def capture_face(request):
    if request.method != 'POST':
        return JsonResponse({"status": "error", "message": "Invalid request"})

    if not request.user.is_authenticated:
        return JsonResponse({"status": "error", "message": "User not logged in"})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"})

    image_data = body.get("image")
    index = body.get("index", 0)

    if not image_data:
        return JsonResponse({"status": "error", "message": "No image data"})

    image_data = re.sub(r'^data:image/.+;base64,', '', image_data)
    try:
        image_bytes = base64.b64decode(image_data)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid base64 data"})

    username = request.user.username
    user_folder = os.path.join(settings.MEDIA_ROOT, "faces", username)
    os.makedirs(user_folder, exist_ok=True)
    filename = os.path.join(user_folder, f"face_{index}.png")
    with open(filename, "wb") as f:
        f.write(image_bytes)

    return JsonResponse({"status": "success", "file": filename})

