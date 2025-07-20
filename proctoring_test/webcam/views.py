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
