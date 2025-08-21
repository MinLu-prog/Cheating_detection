# views.py
import base64
import cv2
import numpy as np
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .ai_proctor import DetectionSystem

# Initialize a single DetectionSystem instance (keep it running)
ds = DetectionSystem(process_with_camera=False)  # We'll feed frames from browser

# -------------------- Receive frames from browser --------------------
@csrf_exempt
def quiz_ai_stream(request, quiz_id):
    """
    Accepts a base64 frame from browser and processes it.
    Expects JSON: { "frame": "<base64_string>" }
    """
    if request.method == "POST":
        data = request.POST.get("frame")
        if data:
            # Decode base64 image
            header, encoded = data.split(",", 1)
            img_bytes = base64.b64decode(encoded)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            # Process frame with DetectionSystem
            annotated_frame, _ = ds.process_frame(frame)

            # Return annotated frame as base64
            ret, buffer = cv2.imencode('.jpg', annotated_frame)
            frame_b64 = base64.b64encode(buffer).decode('utf-8')
            return JsonResponse({"frame": f"data:image/jpeg;base64,{frame_b64}"})

    return JsonResponse({"error": "POST required"}, status=400)

# -------------------- AI status polling --------------------
def quiz_ai_status(request, quiz_id):
    """
    Returns current AI status for frontend polling.
    Example: active alerts, total alerts, session duration.
    """
    status = ds.get_status()
    return JsonResponse(status)
