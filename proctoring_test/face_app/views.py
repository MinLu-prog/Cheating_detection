from django.shortcuts import render
import os, base64, re
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

# -------------------- FRONTEND CAPTURE PAGE --------------------
def test_face(request):
    """
    Renders the capture page with video + JS.
    Users open this page to start capturing faces.
    """
    return render(request, "face_app/capture_face.html")


def live_face(request):
    """
    Optional: render another page for live display if needed.
    """
    return render(request, "face_app/live_face.html")

# views.py
def verify_face_page(request):
    return render(request, "face_app/verify_face.html")


# -------------------- BACKEND POST ENDPOINT --------------------
@csrf_exempt
def capture_face(request):
    """
    Receives POST requests from the frontend capture page.
    Saves the base64 image to MEDIA_ROOT/faces/anonymous/
    """
    if request.method == 'POST':
        import json
        try:
            body = json.loads(request.body.decode('utf-8'))
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid JSON"})

        image_data = body.get("image")
        index = body.get("index", 0)

        if not image_data:
            return JsonResponse({"status": "error", "message": "No image data"})

        # Remove "data:image/png;base64," part if present
        image_data = re.sub(r'^data:image/.+;base64,', '', image_data)
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid base64 data"})

        # Save to anonymous folder
        user_folder = os.path.join(settings.MEDIA_ROOT, "faces", "anonymous")
        os.makedirs(user_folder, exist_ok=True)

        filename = os.path.join(user_folder, f"face_{index}.png")
        with open(filename, "wb") as f:
            f.write(image_bytes)

        return JsonResponse({"status": "success", "file": filename})

    # Only POST allowed
    return JsonResponse({"status": "error", "message": "Invalid request"})


# -------------------- OPTIONAL FACE VERIFICATION --------------------
import os, base64, re, numpy as np
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import face_recognition
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def verify_face(request):
    if request.method == "POST":
        import json
        body = json.loads(request.body.decode('utf-8'))
        image_data = body.get("image")

        if not image_data:
            return JsonResponse({"status": "error", "message": "No image data"})

        # Remove "data:image/png;base64," part
        image_data = re.sub('^data:image/.+;base64,', '', image_data)
        image_bytes = base64.b64decode(image_data)

        # Save temporary image
        temp_path = os.path.join(settings.MEDIA_ROOT, "faces", "temp_verify.png")
        with open(temp_path, "wb") as f:
            f.write(image_bytes)

        # Load and get descriptor
        image = face_recognition.load_image_file(temp_path)
        face_locations = face_recognition.face_locations(image)
        if len(face_locations) == 0:
            return JsonResponse({"status": "no_face", "message": "No face detected"})

        face_encodings = face_recognition.face_encodings(image, face_locations)
        live_descriptor = face_encodings[0]  # take first face if multiple

        # Compare against dataset
        dataset_folder = os.path.join(settings.MEDIA_ROOT, "faces", "anonymous")
        found_match = False
        for filename in os.listdir(dataset_folder):
            if filename.endswith(".png"):
                known_image = face_recognition.load_image_file(os.path.join(dataset_folder, filename))
                known_encodings = face_recognition.face_encodings(known_image)
                if known_encodings:
                    match = face_recognition.compare_faces([known_encodings[0]], live_descriptor, tolerance=0.6)
                    if match[0]:
                        found_match = True
                        break

        if found_match:
            return JsonResponse({"status": "same_face", "message": "Face matches dataset"})
        else:
            return JsonResponse({"status": "different_face", "message": "Face does not match dataset"})

    return JsonResponse({"status": "error", "message": "Invalid request"})
