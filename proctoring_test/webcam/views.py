import subprocess
import os
import sys
from django.http import HttpResponse

def proctoring_launcher(request):
    script_path = os.path.join(os.path.dirname(__file__), 'proctoring_runner.py')

    # Use the Python interpreter from the current virtual environment
    python_executable = sys.executable  # This points to .venv\Scripts\python.exe

    try:
        subprocess.Popen([python_executable, script_path])
        return HttpResponse("✅ Proctoring test started. Look for a new window.")
    except Exception as e:
        return HttpResponse(f"❌ Error launching proctoring: {e}")

from django.http import StreamingHttpResponse
import cv2

def gen_frames():
    cap = cv2.VideoCapture(0)

    while True:
        success, frame = cap.read()
        if not success:
            break
        else:
            # Resize frame for smaller display
            frame = cv2.resize(frame, (200, 150))

            # Encode frame
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

def video_feed(request):
    return StreamingHttpResponse(gen_frames(), content_type='multipart/x-mixed-replace; boundary=frame')
