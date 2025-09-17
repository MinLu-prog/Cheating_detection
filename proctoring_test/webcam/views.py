from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
import base64, traceback
import numpy as np
import cv2

from .ai_proctor import DetectionSystem
from proctor.proctor_log import log_event
from proctor.models import Quiz

# Reuse a single detector instance (avoid reloading models each request)
ds = DetectionSystem(process_with_camera=False)


@csrf_exempt
def quiz_ai_stream(request, quiz_id):
    """
    Accept a base64 data URL from the browser (POST 'frame'),
    run AI proctoring, log alerts (with snapshots), and return
    the annotated frame as a base64 data URL along with meta.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth_required"}, status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id)

    try:
        data = request.POST.get("frame")
        if not data or "," not in data:
            return JsonResponse({"error": "missing_or_invalid_frame"}, status=400)

        # ---- Decode base64 image (data URL) ----
        try:
            _, encoded = data.split(",", 1)
            img_bytes = base64.b64decode(encoded)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # BGR
        except Exception:
            log_event(
                request.user, quiz, "decode_error",
                message="Failed to base64-decode or imdecode frame",
                severity="error",
                metadata={"where": "quiz_ai_stream"},
                frame_b64=data,
            )
            return JsonResponse({"error": "decode_failed"}, status=400)

        if frame is None:
            log_event(
                request.user, quiz, "decode_error",
                message="cv2.imdecode returned None",
                severity="error",
                metadata={"where": "quiz_ai_stream"},
                frame_b64=data,
            )
            return JsonResponse({"error": "decode_failed"}, status=400)

        # ---- Run AI Proctor detection ----
        annotated_frame, active_alerts = ds.process_frame(frame)
        meta = {
            "active_alerts": active_alerts,
            **(ds.get_status() or {}),  # may include terminated/reason/warning
        }

        # ---- Log alerts WITH SNAPSHOT ----
        for code in meta.get("active_alerts", []):
            log_event(
                request.user, quiz, code,
                message=f"Alert: {code}",
                severity="warn",
                metadata=meta,
                frame=annotated_frame,   # <- save snapshot to ImageField
            )

        # ---- Log termination (if any) WITH SNAPSHOT ----
        if meta.get("terminated"):
            log_event(
                request.user, quiz, "terminated",
                message=meta.get("reason", "terminated"),
                severity="error",
                metadata=meta,
                frame=annotated_frame,   # <- include snapshot
            )

        # ---- Encode annotated frame to base64 for the browser ----
        ok, buffer = cv2.imencode(".jpg", annotated_frame)
        if not ok:
            log_event(
                request.user, quiz, "encode_error",
                message="cv2.imencode failed",
                severity="error",
                metadata={"where": "quiz_ai_stream"},
            )
            return JsonResponse({"error": "encode_failed"}, status=500)

        frame_b64 = base64.b64encode(buffer).decode("utf-8")
        return JsonResponse({
            "frame": f"data:image/jpeg;base64,{frame_b64}",
            "meta": meta
        })

    except Exception as e:
        # Unexpected server-side exception
        log_event(
            request.user, quiz, "stream_error",
            message=str(e),
            severity="error",
            metadata={"where": "quiz_ai_stream", "trace": traceback.format_exc()[:1500]},
            frame_b64=request.POST.get("frame"),  # best-effort attach original
        )
        return JsonResponse({"error": "server_exception", "detail": repr(e)}, status=500)

def quiz_ai_status(request, quiz_id):
    """Return current AI status for frontend polling."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth_required"}, status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id)
    status = ds.get_status() or {}

    # Log alerts on polling
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

import os
import time
import json
import base64
import numpy as np
import cv2
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

# Toggle face recognition on/off
FACE_RECOGNITION_ENABLED = False  # Set to True to enable recognition again

# ----------------- Improved KNN Helper with distance -----------------
def distance(v1, v2):
    return np.linalg.norm(v1 - v2)

def knn_with_distance(train, test, k=5):
    distances = np.linalg.norm(train[:, :-1] - test, axis=1)
    nearest_indices = np.argsort(distances)[:k]
    nearest_labels = train[nearest_indices, -1]
    min_dist = np.min(distances[nearest_indices])
    
    unique_labels, counts = np.unique(nearest_labels, return_counts=True)
    predicted_class = unique_labels[np.argmax(counts)]
    
    return int(predicted_class), min_dist

# Cache for face datasets
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
    if (_face_dataset_cache is not None and 
        current_time - _last_cache_time < CACHE_TIMEOUT):
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
    
    _dynamic_threshold = 2000  # Fixed threshold
    
    _face_dataset_cache = face_dataset
    _labels_cache = face_labels
    _names_cache = names
    _trainset_cache = trainset
    _last_cache_time = current_time
    
    return face_dataset, face_labels, names, trainset, _dynamic_threshold

@csrf_exempt
@login_required
def verify_face(request):
    if request.method != 'POST':
        return JsonResponse({'valid': False, 'error': 'Invalid request method'})

    current_username = request.user.username

    # --- TEMPORARY BYPASS ---
    if not FACE_RECOGNITION_ENABLED:
        return JsonResponse({
            'valid': True,
            'status': 'Face recognition temporarily disabled',
            'username': current_username,
            'confidence': 1.0,
            'distance': 0.0,
            'threshold': 0.0
        })

    # Step 1: Decode image from frontend
    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        if not image_data or ',' not in image_data:
            return JsonResponse({'valid': False, 'error': 'Invalid image format'})
        
        image_data = image_data.split(',')[1]
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return JsonResponse({'valid': False, 'error': 'Failed to decode image'})
            
    except json.JSONDecodeError:
        return JsonResponse({'valid': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Failed to process image: {str(e)}'})

    # Step 2: Load face datasets
    try:
        face_dataset, face_labels, names, trainset, dynamic_threshold = _load_face_dataset()
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Failed to load face dataset: {str(e)}'})

    # Check if current user's face data exists
    user_face_exists = any(current_username == name for name in names.values())
    if not user_face_exists:
        return JsonResponse({
            'valid': False, 
            'error': f'Your face is not registered. Please register your face first.'
        })

    # Step 3: Detect face in current frame
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml')
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Face detection failed: {str(e)}'})

    if len(faces) == 0:
        print("DEBUG: NO FACE DETECTED - triggering mismatch")
        return _handle_face_mismatch(request, "No face detected", 9999)

    x, y, w, h = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
    face_section = frame[y:y+h, x:x+w]
    face_section = cv2.resize(face_section, (100, 100)).flatten()

    # Step 4: KNN prediction with distance
    try:
        predicted_class, min_dist = knn_with_distance(trainset, face_section)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Face recognition failed: {str(e)}'})

    predicted_name = names.get(int(predicted_class), "Unknown")
    
    print(f"DEBUG: Distance: {min_dist:.2f}, Threshold: {dynamic_threshold:.2f}")
    print(f"DEBUG: Predicted: {predicted_name}, Expected: {current_username}")

    if min_dist > dynamic_threshold:
        predicted_name = "Unknown"
        print(f"DEBUG: UNKNOWN FACE (distance {min_dist:.2f} > threshold {dynamic_threshold:.2f})")
        return _handle_face_mismatch(request, "Unknown face", min_dist)
    else:
        print(f"DEBUG: RECOGNIZED as {predicted_name} (distance {min_dist:.2f})")

    if predicted_name == current_username:
        session = request.session
        session_key_prefix = f'face_verify_{request.user.id}_'
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
        session.modified = True
        
        confidence = max(0, min(1, 1 - (min_dist / dynamic_threshold)))
        
        return JsonResponse({
            'valid': True, 
            'status': 'Face matched successfully',
            'username': current_username,
            'confidence': float(confidence),
            'distance': float(min_dist),
            'threshold': float(dynamic_threshold)
        })
    else:
        print(f"DEBUG: WRONG USER - expected {current_username}, got {predicted_name}")
        return _handle_face_mismatch(request, f"Wrong user: {predicted_name}", min_dist)


def _handle_face_mismatch(request, reason, distance_value):
    session = request.session
    session_key_prefix = f'face_verify_{request.user.id}_'
    current_time = time.time()
    
    if f'{session_key_prefix}mismatch_count' not in session:
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
    
    mismatch_count = session[f'{session_key_prefix}mismatch_count']
    last_warning_time = session[f'{session_key_prefix}last_warning_time']
    
    session[f'{session_key_prefix}mismatch_count'] = mismatch_count + 1
    session[f'{session_key_prefix}last_warning_time'] = current_time
    session.modified = True
    
    new_mismatch_count = mismatch_count + 1
    print(f"DEBUG: Mismatch count: {new_mismatch_count}/3, Reason: {reason}")
    
    if new_mismatch_count == 1:
        return JsonResponse({
            'valid': False, 
            'warning': '⚠️ First warning: Face mismatch detected',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value),
            'remaining_chances': 2
        })
    
    elif new_mismatch_count == 2:
        return JsonResponse({
            'valid': False, 
            'warning': '❗️ Strong warning: Face mismatch detected again',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value),
            'remaining_chances': 1
        })
    
    elif new_mismatch_count >= 3:
        print("DEBUG: THIRD MISMATCH - TERMINATING EXAM")
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
        session.modified = True
        
        return JsonResponse({
            'valid': False, 
            'terminated': True,
            'error': '⛔️ Exam terminated due to repeated face mismatches',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'redirect_url': reverse('student_dashboard')
        })
    
    else:
        return JsonResponse({
            'valid': False, 
            'error': 'Face mismatch detected',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value)
        })