# ai_proctor.py
import os
import time
import cv2
import dlib
import numpy as np
from datetime import datetime
from collections import deque, defaultdict
from typing import Tuple, List, Dict, Any
from ultralytics import YOLO


# ================= CONFIGURATION =================
class Config:
    LANDMARKS_MODEL = "webcam/shape_predictor_68_face_landmarks.dat"
    YOLO_MODEL = "webcam/best.pt"   # your custom model
    OBJECT_CONFIDENCE = 0.4

    # Head / gaze thresholds
    HEAD_YAW_THRESHOLD = 25
    GAZE_LEFT_THRESHOLD = 1.5
    GAZE_RIGHT_THRESHOLD = 0.8

    # Alert gating (simple)
    ALERT_DURATION = 2.0           # seconds condition must persist
    ALERT_COOLDOWN = 5.0           # seconds between triggers of the same type
    CONSEC_FRAMES = 5              # minimum frames during the duration

    # Server overlay switches (Option 1 = OFF)
    DRAW_SERVER_OVERLAY = False
    DRAW_STATS_OVERLAY  = False

    SNAPSHOT_DIR = "proctoring_snapshots"
    LOG_FILE = "proctoring_log.txt"
    FONT = cv2.FONT_HERSHEY_SIMPLEX


# ================= ALERT MANAGER =================
class AlertManager:
    """
    Lightweight time-gated alert manager:
      - per-type state with 'active' True only while condition is present
      - triggers (snapshot/log) if condition persisted for ALERT_DURATION and >= CONSEC_FRAMES
      - cooldown between triggers per type
    """
    def __init__(self, config: Config):
        self.config = config
        self.states: Dict[str, Dict[str, Any]] = {}       # {type: {active,start_time,frames,last_trigger}}
        self.history: List[Dict[str, Any]] = []           # [{type,timestamp,snapshot}]
        os.makedirs(self.config.SNAPSHOT_DIR, exist_ok=True)

        self.messages = {
            'head_pose': "⚠️ HEAD TURNED - Look at screen!",
            'eye_gaze':  "⚠️ EYES LOOKING AWAY - Look at center!",
            'multi_face':"⚠️ MULTIPLE FACES DETECTED!",
            'no_face':   "⚠️ NO FACE DETECTED - Show your face!",
            'phone':     "⚠️ PHONE DETECTED - Remove phone!",
            'earphone':  "⚠️ EARPHONES DETECTED - Remove earphones!",
            'book':      "⚠️ UNAUTHORIZED MATERIAL DETECTED!",
        }
        self.colors = {
            'head_pose': (0, 0, 255),
            'eye_gaze':  (0, 165, 255),
            'multi_face':(0, 0, 255),
            'no_face':   (128, 128, 128),
            'phone':     (0, 165, 255),
            'earphone':  (255, 0, 255),
            'book':      (0, 255, 255),
        }

    def _get(self, t: str) -> Dict[str, Any]:
        if t not in self.states:
            self.states[t] = {
                'active': False,
                'start_time': None,
                'frames': 0,
                'last_trigger': -1e9,
            }
        return self.states[t]

    def check(self, alert_type: str, condition: bool, frame) -> bool:
        """Return True if alert should be considered active *this frame*."""
        st = self._get(alert_type)
        now = time.time()

        if condition:
            if not st['active']:
                st['active'] = True
                st['start_time'] = now
                st['frames'] = 1
            else:
                st['frames'] += 1

            # trigger?
            dur_ok = (now - (st['start_time'] or now)) >= self.config.ALERT_DURATION
            frames_ok = st['frames'] >= self.config.CONSEC_FRAMES
            cooldown_ok = (now - st['last_trigger']) >= self.config.ALERT_COOLDOWN
            if dur_ok and frames_ok and cooldown_ok:
                self._trigger(alert_type, frame)
                st['last_trigger'] = now
        else:
            st['active'] = False
            st['start_time'] = None
            st['frames'] = 0

        return st['active']

    def _trigger(self, alert_type: str, frame):
        ts = datetime.now()
        path = self._save_snapshot(frame, alert_type, ts)
        self._log(alert_type, ts, path)
        self.history.append({'type': alert_type, 'timestamp': ts, 'snapshot': path})
        print(f"[ALERT] {ts:%Y-%m-%d %H:%M:%S} - {self.messages.get(alert_type, alert_type)}")

    def _save_snapshot(self, frame, alert_type: str, ts: datetime) -> str:
        fname = f"{alert_type}_{ts:%Y%m%d_%H%M%S}.jpg"
        fpath = os.path.join(self.config.SNAPSHOT_DIR, fname)
        try:
            cv2.imwrite(fpath, frame)
        except Exception:
            pass
        return fpath

    def _log(self, alert_type: str, ts: datetime, snap: str):
        try:
            with open(self.config.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{ts.isoformat()} - {alert_type.upper()} - {snap}\n")
        except Exception:
            pass

    # (kept for Option 2, not used in Option 1)
    def draw_alerts(self, frame, active_alerts: List[str]):
        if not active_alerts:
            return frame
        # Small, top-right stacked chips (transparent)
        h, w = frame.shape[:2]
        scale = max(0.45, min(0.7, w / 1600))
        thick = 1
        pad = int(6 * (w / 1280 + h / 720))
        x_right = w - 10
        y = 10

        show = active_alerts[:3]
        more = len(active_alerts) - len(show)
        if more > 0:
            show.append(f"+{more} more")

        for at in show:
            msg = self.messages.get(at, at)
            color = self.colors.get(at, (255, 255, 255))
            (tw, th), _ = cv2.getTextSize(msg, Config.FONT, scale, thick)
            bw, bh = tw + 2 * pad, th + 2 * pad
            x1, y1 = x_right - bw, y
            x2, y2 = x_right, y + bh
            overlay = frame.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)
            cv2.rectangle(frame, (x1, y1), (x1 + 4, y2), color, -1)
            cv2.putText(frame, msg, (x1 + pad + 6, y1 + pad + th),
                        Config.FONT, scale, (255, 255, 255), thick, cv2.LINE_AA)
            y += bh + 6
        return frame

    def active_list(self) -> List[str]:
        return [k for k, st in self.states.items() if st.get('active')]


# ================= DETECTION SYSTEM =================
class DetectionSystem:
    def __init__(self, process_with_camera: bool = False, camera_index: int = 0):
        self.cfg = Config()
        self.alerts = AlertManager(self.cfg)

        self._using_camera = False
        self.cap = None
        if process_with_camera:
            self.start_camera(camera_index)

        # Models
        self.face_detector = dlib.get_frontal_face_detector()
        self.predictor = dlib.shape_predictor(self.cfg.LANDMARKS_MODEL)
        self.yolo = YOLO(self.cfg.YOLO_MODEL)

        self.stats = {'session_start': time.time()}

    def start_camera(self, camera_index: int = 0):
        if not self._using_camera:
            self.cap = cv2.VideoCapture(camera_index)
            self._using_camera = True

    def stop_camera(self):
        if self._using_camera and self.cap:
            self.cap.release()
            self._using_camera = False

    # --------------- MAIN FRAME PROCESS ---------------
    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, List[str]]:
        if frame is None:
            return frame, []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_detector(gray, 0)
        active: List[str] = []

        # no / multi face
        if len(faces) == 0:
            if self.alerts.check('no_face', True, frame): active.append('no_face')
        else:
            self.alerts.check('no_face', False, frame)
            if len(faces) > 1:
                if self.alerts.check('multi_face', True, frame): active.append('multi_face')
            else:
                self.alerts.check('multi_face', False, frame)

        # head pose + gaze (use largest face)
        if len(faces) > 0:
            face = faces[0]
            lm = self.predictor(gray, face)

            yaw = self._head_yaw(lm, frame.shape)
            if abs(yaw) > self.cfg.HEAD_YAW_THRESHOLD:
                if self.alerts.check('head_pose', True, frame): active.append('head_pose')
            else:
                self.alerts.check('head_pose', False, frame)

            direction, _ = self._eye_dir(lm, gray)
            if direction in ("LEFT", "RIGHT"):
                if self.alerts.check('eye_gaze', True, frame): active.append('eye_gaze')
            else:
                self.alerts.check('eye_gaze', False, frame)

        # object detection (custom best.pt)
        detected, annotated = self._detect_objects(frame)
        frame = annotated
        for atype, key in (('phone', 'phones'), ('earphone', 'earphones'), ('book', 'books')):
            if detected.get(key, 0) > 0:
                if self.alerts.check(atype, True, frame): active.append(atype)
            else:
                self.alerts.check(atype, False, frame)

        # Option 1: do NOT draw server-side alert chips
        if self.cfg.DRAW_SERVER_OVERLAY:
            frame = self.alerts.draw_alerts(frame, active)

        if self.cfg.DRAW_STATS_OVERLAY:
            self._draw_stats(frame)

        # Always return the *client* list (small chips will render there)
        return frame, self.alerts.active_list()

    def get_status(self) -> Dict[str, Any]:
        return {
            "terminated": False,         # you can add your termination logic later
            "reason": "",
            "warning": None,
            "active_alerts": self.alerts.active_list(),
        }

    # --------------- HELPERS ---------------
    def _head_yaw(self, landmarks, frame_shape) -> float:
        image_points = np.array([
            (landmarks.part(30).x, landmarks.part(30).y),
            (landmarks.part(8).x,  landmarks.part(8).y),
            (landmarks.part(36).x, landmarks.part(36).y),
            (landmarks.part(45).x, landmarks.part(45).y),
            (landmarks.part(48).x, landmarks.part(48).y),
            (landmarks.part(54).x, landmarks.part(54).y),
        ], dtype="double")

        h, w = frame_shape[:2]
        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype="double")
        dist = np.zeros((4, 1))
        model_points = np.array([
            (0.0, 0.0, 0.0),
            (0.0, -330.0, -65.0),
            (-225.0, 170.0, -135.0),
            (225.0, 170.0, -135.0),
            (-150.0, -150.0, -125.0),
            (150.0, -150.0, -125.0),
        ])

        try:
            ok, rvec, tvec = cv2.solvePnP(model_points, image_points, camera_matrix, dist,
                                           flags=cv2.SOLVEPNP_ITERATIVE)
            if not ok:
                return 0.0
        except Exception:
            return 0.0

        rmat, _ = cv2.Rodrigues(rvec)
        proj = np.hstack((rmat, tvec))
        angles = cv2.decomposeProjectionMatrix(proj)[6]
        return float(angles[1])  # yaw

    def _eye_dir(self, landmarks, gray) -> Tuple[str, float]:
        def ratio(eye_idx):
            pts = np.array([(landmarks.part(p).x, landmarks.part(p).y) for p in eye_idx], np.int32)
            mask = np.zeros_like(gray)
            cv2.fillPoly(mask, [pts], 255)
            eye = cv2.bitwise_and(gray, gray, mask=mask)
            _, thr = cv2.threshold(eye, 70, 255, cv2.THRESH_BINARY)
            hh, ww = thr.shape
            if hh == 0 or ww == 0:
                return 1.0
            left = thr[:, :ww // 2]
            right = thr[:, ww // 2:]
            return (cv2.countNonZero(left) + 1) / (cv2.countNonZero(right) + 1)

        l = ratio([36, 37, 38, 39, 40, 41])
        r = ratio([42, 43, 44, 45, 46, 47])
        hratio = (l + r) / 2.0
        if hratio < Config.GAZE_RIGHT_THRESHOLD:
            return "RIGHT", hratio
        elif hratio > Config.GAZE_LEFT_THRESHOLD:
            return "LEFT", hratio
        return "CENTER", hratio

    def _detect_objects(self, frame):
        counts = {'phones': 0, 'earphones': 0, 'books': 0}
        annotated = frame
        try:
            res = self.yolo.predict(frame, conf=self.cfg.OBJECT_CONFIDENCE, verbose=False)
            if not res or getattr(res[0], "boxes", None) is None:
                print("YOLO detected: []")
                return counts, frame

            names_map = getattr(self.yolo, 'names', {}) or {}
            boxes = res[0].boxes
            classes = boxes.cls.cpu().numpy().astype(int).tolist()
            names = [names_map.get(int(c), str(int(c))) for c in classes]

            # accept a few common synonyms
            for n in names:
                nn = n.lower().strip()
                if nn in ("phone", "cell phone", "cellphone", "mobile phone"):
                    counts['phones'] += 1
                elif nn in ("earphone", "earphones", "earbud", "earbuds", "headphone", "headphones"):
                    counts['earphones'] += 1
                elif nn in ("book", "books"):
                    counts['books'] += 1

            annotated = res[0].plot()
            print(f"YOLO detected: {names}")
        except Exception as e:
            print(f"YOLO error: {e}")

        return counts, annotated

    def _draw_stats(self, frame):
        try:
            h, w = frame.shape[:2]
            cv2.rectangle(frame, (10, h - 80), (250, h - 10), (0, 0, 0), -1)
            cv2.putText(frame, f"Session: {int(time.time() - self.stats['session_start'])}s",
                        (15, h - 50), Config.FONT, 0.5, (255, 255, 255), 1)
            cv2.putText(frame, f"Alerts: {len(self.alerts.history)}",
                        (15, h - 25), Config.FONT, 0.5, (0, 255, 255), 1)
        except Exception:
            pass
