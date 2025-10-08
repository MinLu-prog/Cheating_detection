# webcam/utils/proctor_instance.py
from .ai_proctor import DetectionSystem
import threading

_lock = threading.Lock()
_detector = None

def get_detector():
    """
    Thread-safe singleton accessor for the global DetectionSystem.
    Creates it once and reuses it across requests even with Django reloader.
    """
    global _detector
    if _detector is None:
        with _lock:
            if _detector is None:  # double-check locking
                _detector = DetectionSystem(process_with_camera=False)
                _detector._loaded_user = None
                print("[INIT] DetectionSystem singleton created")
    return _detector
