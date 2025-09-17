import base64, re, time, logging
from django.core.files.base import ContentFile
from django.db import connection, OperationalError, ProgrammingError
from .models import ProctorEvent

logger = logging.getLogger(__name__)

_DATAURL_RE = re.compile(r'^data:image/\w+;base64,')
_COOLDOWN_SEC = 3.0
_last_log = {}  # (user_id, quiz_id, event_type) -> last_ts
_TABLE_READY = None

def _table_exists():
    global _TABLE_READY
    if _TABLE_READY is True:
        return True
    try:
        tables = connection.introspection.table_names()
        _TABLE_READY = (ProctorEvent._meta.db_table in tables)
        return _TABLE_READY
    except Exception:
        return False

def log_event(user, quiz, event_type, message='', severity='warn', metadata=None, frame_b64=None, throttle=True):
    """Persist a proctoring event; optional base64 dataURL snapshot."""
    # Skip cleanly if table is not ready (e.g., before migrations)
    if not _table_exists():
        logger.warning("ProctorEvent table missing; skipping log. Run migrations.")
        return None

    key = (user.id, quiz.id, event_type)
    now = time.time()
    if throttle:
        last = _last_log.get(key, 0)
        if (now - last) < _COOLDOWN_SEC:
            return None
        _last_log[key] = now

    try:
        ev = ProctorEvent.objects.create(
            quiz=quiz,
            student=user,
            event_type=event_type,
            severity=severity,
            message=message or '',
            metadata=metadata or {},
        )
        if frame_b64 and _DATAURL_RE.match(frame_b64):
            raw = base64.b64decode(_DATAURL_RE.sub('', frame_b64))
            ev.frame.save(f'evt_{ev.id}.jpg', ContentFile(raw), save=True)
        return ev
    except (OperationalError, ProgrammingError) as e:
        # If a race happens during first boot, don’t bring the app down
        logger.warning("ProctorEvent write skipped (DB not ready): %s", e)
        return None

# proctor/proctor_log.py
import base64, uuid, re
import numpy as np
import cv2
from django.core.files.base import ContentFile
from django.utils import timezone
from .models import ProctorEvent

def _b64_to_bytes(data_url_or_b64: str) -> bytes | None:
    if not data_url_or_b64:
        return None
    # strip data URL prefix if present
    m = re.match(r'^data:image/[^;]+;base64,(.*)$', data_url_or_b64)
    b64 = m.group(1) if m else data_url_or_b64
    try:
        return base64.b64decode(b64)
    except Exception:
        return None

def _npframe_to_jpeg_bytes(frame: np.ndarray) -> bytes | None:
    try:
        ok, buf = cv2.imencode('.jpg', frame)
        return buf.tobytes() if ok else None
    except Exception:
        return None

def log_event(user, quiz, event_type, message="", severity="warn",
              metadata=None, frame: np.ndarray | None = None,
              frame_b64: str | None = None, filename: str | None = None):
    """
    Create a ProctorEvent and (optionally) save a snapshot into ImageField.
    - Pass either `frame` (np.ndarray, BGR) or `frame_b64` (data URL or raw b64).
    """
    pe = ProctorEvent(
        quiz=quiz,
        student=user,
        event_type=event_type,
        severity=severity,
        message=message or "",
        metadata=metadata or {},
    )

    img_bytes = None
    if frame is not None:
        img_bytes = _npframe_to_jpeg_bytes(frame)
    elif frame_b64:
        img_bytes = _b64_to_bytes(frame_b64)

    if img_bytes:
        name = filename or f"{event_type}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
        pe.frame.save(name, ContentFile(img_bytes), save=False)

    pe.save()
    return pe
