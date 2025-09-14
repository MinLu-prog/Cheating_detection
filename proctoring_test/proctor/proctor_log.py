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
