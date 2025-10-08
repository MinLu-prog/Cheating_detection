import os, base64, re
from django.conf import settings
import time
import cv2
import dlib
import numpy as np
from datetime import datetime
from collections import deque, defaultdict
from typing import Tuple, List
from ultralytics import YOLO
import speech_recognition as sr
import threading
import queue
import face_recognition
from io import BytesIO
from proctor.models import ProctorAlert
from django.core.files.base import ContentFile



# Make sure you have the reference encoding loaded first

# ================= CONFIGURATION =================


class Config:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Django project root
    LANDMARKS_MODEL = os.path.join(BASE_DIR, "webcam", "models", "shape_predictor_68_face_landmarks.dat")
    YOLO_MODEL = os.path.join(BASE_DIR, "webcam", "models", "yolov8n.pt")

    # Enhanced Head Pose Thresholds (More Flexible)
    HEAD_YAW_THRESHOLD = 35        # Increased from 25 for more flexibility
    HEAD_PITCH_THRESHOLD = 30      # Added pitch threshold
    LOOKING_DOWN_THRESHOLD = 40    # More tolerant for natural movements
    LOOKING_UP_THRESHOLD = -25     # More tolerant for natural movements

    # Enhanced Gaze Thresholds (More Flexible)
    GAZE_THRESHOLD_LEFT = 2.0      # More tolerant
    GAZE_THRESHOLD_RIGHT = 0.5     # More tolerant
    OBJECT_CONFIDENCE = 0.4

    # Enhanced Multi-face Detection (More Realistic)
    MIN_FACE_SIZE_RATIO = 0.03     # Smaller minimum size for distant faces
    FACE_DISTANCE_THRESHOLD = 150  # Increased for better tracking
    FACE_OVERLAP_THRESHOLD = 0.4   # More tolerant of overlaps
    MULTI_FACE_GRACE_FRAMES = 15   # Grace period for temporary multiple faces
    FACE_CONFIDENCE_THRESHOLD = 0.7  # Lower threshold for realistic detection

    # Time-based gating (FPS-agnostic)
    ALERT_DURATION = 3.0           # Longer duration for more realistic detection
    EVIDENCE_WINDOW_SECONDS = 3.0  # Longer window for evidence
    EVIDENCE_MIN_TRUE_RATIO = 0.7  # Higher ratio needed
    ALERT_CLEAR_HOLD_SECONDS = 2.0  # Longer hold time
    ALERT_COOLDOWN = 8.0           # Longer cooldown between alerts

    # Enhanced Audio Detection
    AUDIO_SENSITIVITY = 0.6
    LISTEN_TIMEOUT = 2.0
    PHRASE_TIME_LIMIT = 4
    KEYWORD_CONFIDENCE_THRESHOLD = 0.6

    CONSECUTIVE_FRAMES = 5
    SNAPSHOT_DIR = "proctoring_snapshots"
    LOG_FILE = "proctoring_log.txt"
    FONT = cv2.FONT_HERSHEY_SIMPLEX

    # Comprehensive Enhanced Audio Keywords
    ENHANCED_CHEAT_KEYWORDS = {
        'search_platforms': [
            'google', 'bing', 'yahoo', 'duckduckgo', 'search', 'find', 'lookup',
            'look up', 'yandex', 'baidu', 'ask', 'startpage', 'ecosia', 'searx',
            'brave search', 'perplexity', 'you.com', 'swisscows'
        ],
        'ai_assistants': [
            'chatgpt', 'gpt', 'claude', 'bard', 'copilot', 'gemini', 'openai',
            'ai assistant', 'artificial intelligence', 'machine learning',
            'neural network', 'language model', 'anthropic', 'hugging face',
            'midjourney', 'dall-e', 'stable diffusion', 'llama', 'palm'
        ],
        'communication_apps': [
            'whatsapp', 'telegram', 'messenger', 'discord', 'skype', 'zoom',
            'teams', 'slack', 'viber', 'wechat', 'line', 'snapchat',
            'instagram', 'facebook', 'twitter', 'tiktok', 'signal', 'facetime',
            'hangouts', 'meet', 'webex', 'duo', 'imo', 'kik'
        ],
        'voice_commands': [
            'hey siri', 'ok google', 'alexa', 'cortana', 'hey google',
            'call', 'phone', 'dial', 'text', 'message', 'send', 'voice message',
            'record', 'voice assistant', 'smart speaker', 'voice search',
            'bixby', 'hey bixby', 'ok bixby'
        ],
        'help_seeking': [
            'help', 'answer', 'solution', 'assist', 'explain', 'tell me',
            'show me', 'how to', 'what is', 'give me', 'need help',
            'can you help', 'please help', 'help me with', 'i need',
            'dont know', "don't know", 'confused', 'stuck', 'lost',
            'what should i do', 'how do i solve', 'give me the answer',
            'what does this mean', 'i dont understand', 'clarify this'
        ],
        'academic_dishonesty': [
            'cheat', 'copy', 'paste', 'share', 'collaborate', 'work together',
            'give answer', 'share answer', 'send answer', 'tell answer',
            'what did you get', 'whats the answer', "what's the answer",
            'duplicate', 'plagiarize', 'steal', 'take from', 'academic fraud',
            'homework help', 'exam help', 'test answers', 'quiz answers'
        ],
        'study_platforms': [
            'chegg', 'coursehero', 'course hero', 'bartleby', 'studyblue',
            'quizlet', 'brainly', 'photomath', 'mathway', 'wolframalpha',
            'wolfram alpha', 'symbolab', 'khan academy', 'stack overflow',
            'reddit', 'yahoo answers', 'study guides', 'solution manual',
            'studocu', 'scribd', 'academia edu', 'researchgate'
        ],
        'suspicious_phrases': [
            'quick search', 'fast search', 'real quick', 'one second',
            'wait a minute', 'hold on', 'let me check', 'just checking',
            'bathroom break', 'technical issue', 'connection problem',
            'someone at door', 'be right back', 'brb', 'phone call',
            'emergency', 'urgent', 'family emergency', 'technical difficulties'
        ],
        'calculation_help': [
            'calculator', 'calculate', 'compute', 'math solver', 'equation solver',
            'formula', 'derivative', 'integral', 'solve this', 'work this out',
            'step by step', 'solution steps', 'math help', 'calculation help'
        ],
        'translation_help': [
            'translate', 'translation', 'google translate', 'deepl', 'linguee',
            'what does this mean in english', 'translate this', 'meaning of',
            'definition', 'dictionary', 'thesaurus', 'synonym', 'antonym'
        ],
        'coding_help': [
            'github', 'stackoverflow', 'stack overflow', 'coding help',
            'programming help', 'debug this', 'syntax error', 'compile error',
            'runtime error', 'code review', 'algorithm help', 'data structure'
        ],
        'exam_specific': [
            'previous year', 'past paper', 'sample question', 'model answer',
            'marking scheme', 'exam pattern', 'syllabus', 'curriculum',
            'study material', 'reference book', 'textbook solution'
        ]
    }

# ================= ENHANCED MULTI-FACE DETECTION =================


class EnhancedMultiFaceDetector:
    """More realistic multi-face detection with improved filtering"""

    def __init__(self, config: Config):
        self.config = config
        self.face_detector = dlib.get_frontal_face_detector()
        self.face_tracker = {}
        self.next_face_id = 0
        self.multi_face_grace_counter = 0
        self.legitimate_secondary_faces = set()  # Track legitimate additional faces

    def detect_realistic_multiface(self, frame) -> Tuple[List, bool]:
        """Enhanced multi-face detection with realistic filtering"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = frame.shape[:2]

        # Apply enhanced preprocessing for better detection
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        # Detect faces with multiple scales
        faces = self.face_detector(gray, 1)

        # Filter faces by realistic criteria
        valid_faces = self._filter_realistic_faces(faces, height, width, gray)

        # Enhanced multi-face analysis
        is_violation = self._analyze_multiface_pattern(valid_faces, frame)

        return valid_faces, is_violation

    def _filter_realistic_faces(self, faces, height, width, gray):
        """Filter faces using realistic criteria"""
        min_face_area = height * width * self.config.MIN_FACE_SIZE_RATIO
        valid_faces = []

        for face in faces:
            # Size filtering
            face_area = (face.right() - face.left()) * \
                (face.bottom() - face.top())
            if face_area < min_face_area:
                continue

            # Quality assessment
            face_roi = gray[face.top():face.bottom(), face.left():face.right()]
            if not self._assess_face_quality(face_roi):
                continue

            # Position filtering (exclude edge cases that might be false positives)
            if self._is_edge_detection(face, width, height):
                continue

            valid_faces.append(face)

        # Remove overlapping detections
        return self._remove_overlapping_faces(valid_faces)

    def _assess_face_quality(self, face_roi):
        """Assess if detected region is actually a face"""
        if face_roi.size == 0:
            return False

        # Check contrast and texture
        contrast = np.std(face_roi)
        if contrast < 15:  # Too uniform, likely false positive
            return False

        # Check for face-like features using simple heuristics
        # Calculate edge density (faces have more edges)
        edges = cv2.Canny(face_roi, 50, 150)
        edge_density = np.sum(edges > 0) / face_roi.size

        return edge_density > 0.02  # Minimum edge density for face-like region

    def _is_edge_detection(self, face, width, height):
        """Check if face detection is too close to frame edges (likely false positive)"""
        margin = 20
        return (face.left() < margin or face.top() < margin or
                face.right() > width - margin or face.bottom() > height - margin)

    def _remove_overlapping_faces(self, faces):
        """Remove overlapping face detections intelligently"""
        if len(faces) <= 1:
            return faces

        keep_faces = []
        for i, face1 in enumerate(faces):
            should_keep = True
            for j, face2 in enumerate(faces):
                if i != j:
                    overlap = self._calculate_overlap(face1, face2)
                    if overlap > self.config.FACE_OVERLAP_THRESHOLD:
                        # Keep the more centered and larger face
                        score1 = self._calculate_face_score(face1)
                        score2 = self._calculate_face_score(face2)
                        if score1 < score2:
                            should_keep = False
                            break
            if should_keep:
                keep_faces.append(face1)

        return keep_faces

    def _calculate_overlap(self, face1, face2):
        """Calculate overlap ratio between two faces"""
        x1 = max(face1.left(), face2.left())
        y1 = max(face1.top(), face2.top())
        x2 = min(face1.right(), face2.right())
        y2 = min(face1.bottom(), face2.bottom())

        if x1 >= x2 or y1 >= y2:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (face1.right() - face1.left()) * (face1.bottom() - face1.top())
        area2 = (face2.right() - face2.left()) * (face2.bottom() - face2.top())

        return intersection / min(area1, area2)

    def _calculate_face_score(self, face):
        """Calculate face quality score (higher is better)"""
        area = (face.right() - face.left()) * (face.bottom() - face.top())
        # Prefer larger faces
        return area

    def _analyze_multiface_pattern(self, faces, frame):
        """Analyze if multiple faces represent a realistic violation"""
        if len(faces) <= 1:
            self.multi_face_grace_counter = 0
            return False

        # Apply grace period for temporary multiple faces
        self.multi_face_grace_counter += 1

        if self.multi_face_grace_counter < self.config.MULTI_FACE_GRACE_FRAMES:
            return False  # Grace period, not yet a violation

        # Additional analysis for legitimate scenarios
        if self._could_be_legitimate_multiface(faces, frame):
            return False

        return True

    def _could_be_legitimate_multiface(self, faces, frame):
        """Check if multiple faces could be legitimate (e.g., photo in background)"""
        if len(faces) != 2:
            return False  # Only consider two-face scenarios as potentially legitimate

        # Analyze face sizes (background faces are typically smaller)
        face_areas = []
        for face in faces:
            area = (face.right() - face.left()) * (face.bottom() - face.top())
            face_areas.append(area)

        face_areas.sort(reverse=True)

        # If secondary face is much smaller, might be photo/poster
        if len(face_areas) >= 2 and face_areas[1] < face_areas[0] * 0.3:
            return True

        return False

# ================= ENHANCED HEAD POSE ESTIMATION =================


class FlexibleHeadPoseEstimator:
    """More flexible head pose estimation with natural movement tolerance"""

    def __init__(self, config: Config, landmarks_path: str):
        self.config = config
        self.predictor = dlib.shape_predictor(landmarks_path)
        # Increased history for better smoothing
        self.pose_history = deque(maxlen=15)
        self.movement_baseline = None
        self.calibration_frames = 0
        self.baseline_established = False

        # Enhanced 3D model points
        self.model_points = np.array([
            (0.0, 0.0, 0.0),             # Nose tip
            (0.0, -330.0, -65.0),        # Chin
            (-225.0, 170.0, -135.0),     # Left eye left corner
            (225.0, 170.0, -135.0),      # Right eye right corner
            (-150.0, -150.0, -125.0),    # Left mouth corner
            (150.0, -150.0, -125.0),     # Right mouth corner
        ], dtype="double")

    def estimate_flexible_pose(self, face_rect, gray, frame_shape) -> Tuple[float, float, bool]:
        """Estimate head pose with flexible thresholds and natural movement consideration"""
        landmarks = self.predictor(gray, face_rect)

        # Extract facial landmarks
        image_points = np.array([
            (landmarks.part(30).x, landmarks.part(30).y),  # Nose tip
            (landmarks.part(8).x, landmarks.part(8).y),    # Chin
            (landmarks.part(36).x, landmarks.part(36).y),  # Left eye left corner
            (landmarks.part(45).x, landmarks.part(45).y),  # Right eye right corner
            (landmarks.part(48).x, landmarks.part(48).y),  # Left mouth corner
            (landmarks.part(54).x, landmarks.part(54).y),  # Right mouth corner
        ], dtype="double")

        # Enhanced camera matrix calculation
        h, w = frame_shape[:2]
        focal_length = w * 1.1  # Slightly improved focal length
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype="double")

        dist_coeffs = np.zeros((4, 1))

        try:
            success, rotation_vector, translation_vector = cv2.solvePnP(
                self.model_points, image_points, camera_matrix, dist_coeffs,
                flags=cv2.SOLVEPNP_ITERATIVE
            )

            if not success:
                return 0.0, 0.0, False

            # Enhanced angle calculation
            rotation_matrix, _ = cv2.Rodrigues(rotation_vector)

            # Calculate Euler angles with improved method
            sy = np.sqrt(rotation_matrix[0, 0] * rotation_matrix[0, 0] +
                         rotation_matrix[1, 0] * rotation_matrix[1, 0])

            singular = sy < 1e-6

            if not singular:
                yaw = np.degrees(np.arctan2(
                    rotation_matrix[1, 0], rotation_matrix[0, 0]))
                pitch = np.degrees(np.arctan2(-rotation_matrix[2, 0], sy))
            else:
                yaw = np.degrees(
                    np.arctan2(-rotation_matrix[1, 2], rotation_matrix[1, 1]))
                pitch = np.degrees(np.arctan2(-rotation_matrix[2, 0], sy))

            # Apply enhanced smoothing and filtering
            smoothed_yaw, smoothed_pitch = self._apply_flexible_smoothing(
                yaw, pitch)

            # Determine if this represents a violation using flexible criteria
            is_violation = self._is_flexible_violation(
                smoothed_yaw, smoothed_pitch)

            return smoothed_yaw, smoothed_pitch, is_violation

        except Exception as e:
            return 0.0, 0.0, False

    def _apply_flexible_smoothing(self, yaw, pitch):
        """Apply intelligent smoothing that adapts to natural movements"""
        current_pose = {'yaw': yaw, 'pitch': pitch, 'timestamp': time.time()}
        self.pose_history.append(current_pose)

        if len(self.pose_history) < 3:
            return yaw, pitch

        # Adaptive smoothing based on movement pattern
        recent_poses = list(self.pose_history)[-8:]  # Use more recent history

        # Calculate movement variance
        yaw_values = [p['yaw'] for p in recent_poses]
        pitch_values = [p['pitch'] for p in recent_poses]

        yaw_variance = np.var(yaw_values)
        pitch_variance = np.var(pitch_values)

        # Adaptive smoothing weights
        if yaw_variance > 50:  # High movement
            yaw_weights = [0.1, 0.2, 0.3, 0.4]  # Less smoothing
        else:  # Stable movement
            yaw_weights = [0.05, 0.1, 0.25, 0.6]  # More smoothing

        if pitch_variance > 50:
            pitch_weights = [0.1, 0.2, 0.3, 0.4]
        else:
            pitch_weights = [0.05, 0.1, 0.25, 0.6]

        # Apply weighted smoothing
        if len(recent_poses) >= 4:
            smoothed_yaw = sum(p['yaw'] * w for p,
                               w in zip(recent_poses[-4:], yaw_weights))
            smoothed_pitch = sum(p['pitch'] * w for p,
                                 w in zip(recent_poses[-4:], pitch_weights))
        else:
            smoothed_yaw = np.mean(yaw_values)
            smoothed_pitch = np.mean(pitch_values)

        return smoothed_yaw, smoothed_pitch

    def _is_flexible_violation(self, yaw, pitch):
        """Determine violation using flexible, context-aware criteria"""
        # Establish baseline natural movement if not done
        if not self.baseline_established:
            self._update_movement_baseline(yaw, pitch)
            if self.calibration_frames < 30:  # Need more frames for baseline
                return False

        # Enhanced violation detection with context
        violation_factors = []

        # Yaw-based violations (left/right head turn)
        abs_yaw = abs(yaw)
        if abs_yaw > self.config.HEAD_YAW_THRESHOLD:
            severity = min(
                1.0, (abs_yaw - self.config.HEAD_YAW_THRESHOLD) / 20.0)
            violation_factors.append(severity)

        # Pitch-based violations (up/down head movement)
        if pitch > self.config.LOOKING_DOWN_THRESHOLD:
            severity = min(
                1.0, (pitch - self.config.LOOKING_DOWN_THRESHOLD) / 15.0)
            violation_factors.append(severity)
        elif pitch < self.config.LOOKING_UP_THRESHOLD:
            severity = min(
                1.0, (abs(pitch) - abs(self.config.LOOKING_UP_THRESHOLD)) / 15.0)
            violation_factors.append(severity)

        # Sustained movement check
        if self._is_sustained_movement(yaw, pitch):
            violation_factors.append(0.7)

        # Overall violation decision
        if not violation_factors:
            return False

        # Require higher confidence for violations
        max_severity = max(violation_factors)
        avg_severity = np.mean(violation_factors)

        return max_severity > 0.6 and avg_severity > 0.4

    def _update_movement_baseline(self, yaw, pitch):
        """Update baseline natural movement patterns"""
        self.calibration_frames += 1

        if self.movement_baseline is None:
            self.movement_baseline = {'yaw_range': [], 'pitch_range': []}

        self.movement_baseline['yaw_range'].append(abs(yaw))
        self.movement_baseline['pitch_range'].append(abs(pitch))

        if self.calibration_frames >= 30:
            self.baseline_established = True

    def _is_sustained_movement(self, current_yaw, current_pitch):
        """Check if current pose represents sustained suspicious movement"""
        if len(self.pose_history) < 8:
            return False

        recent_poses = list(self.pose_history)[-8:]

        # Check for sustained deviation
        sustained_yaw = all(abs(p['yaw']) > self.config.HEAD_YAW_THRESHOLD * 0.7
                            for p in recent_poses)
        sustained_pitch = all(abs(p['pitch']) > self.config.LOOKING_DOWN_THRESHOLD * 0.7
                              for p in recent_poses)

        return sustained_yaw or sustained_pitch

# ================= ENHANCED AUDIO DETECTION =================
import time
from collections import deque, defaultdict
from typing import Optional, Dict, Any, List

try:
    import speech_recognition as sr
except Exception:
    sr = None  # allow graceful degradation


class ComprehensiveAudioDetector:
    """Enhanced audio detection with comprehensive keyword analysis (robust + backward compatible)."""

    def __init__(self, config):
        self.config = config

        # Fall-back defaults if Config lacks fields
        self.LISTEN_TIMEOUT = getattr(self.config, "LISTEN_TIMEOUT", 2.0)
        self.PHRASE_TIME_LIMIT = getattr(self.config, "PHRASE_TIME_LIMIT", 5.0)
        self.KEYWORD_CONFIDENCE_THRESHOLD = getattr(self.config, "KEYWORD_CONFIDENCE_THRESHOLD", 0.55)
        self.ENHANCED_CHEAT_KEYWORDS: Dict[str, List[str]] = getattr(
            self.config,
            "ENHANCED_CHEAT_KEYWORDS",
            {
                "collaboration": ["help me", "what's the answer", "hey google", "siri", "alexa"],
                "web_search": ["open browser", "search", "google", "stack overflow", "chatgpt"],
                "devices": ["phone", "earbuds", "airpods", "bluetooth", "speaker"],
            },
        )

        self.recognizer = sr.Recognizer() if sr else None
        self.microphone: Optional["sr.Microphone"] = None
        self.keyword_history = deque(maxlen=100)
        self.context_patterns = deque(maxlen=50)
        self.audio_violations: List[Dict[str, Any]] = []
        self.last_detection_time = defaultdict(float)

        if self.recognizer:
            self.recognizer.energy_threshold = 300
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold = 0.8

        self._initialize_microphone()

        # --- hard runtime alias so instance always has it ---
        self.process_audio = self._process_audio_alias  # bound method on every instance

    # class-level method (covers class-attribute lookups)
    def process_audio(self, *args, **kwargs):  # noqa: F811
        return self.listen_and_analyze_comprehensive()

    # runtime alias used above
    def _process_audio_alias(self, *args, **kwargs):
        return self.listen_and_analyze_comprehensive()

    def _initialize_microphone(self):
        if not self.recognizer or not sr:
            print("[Audio] speech_recognition not available; audio disabled")
            self.microphone = None
            return
        try:
            self.microphone = sr.Microphone()
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as e:
            print(f"[Audio] Microphone initialization failed: {e}")
            self.microphone = None

    def listen_and_analyze_comprehensive(self) -> Optional[Dict[str, Any]]:
        if not self.recognizer or not self.microphone:
            return None
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(
                    source,
                    timeout=self.LISTEN_TIMEOUT,
                    phrase_time_limit=self.PHRASE_TIME_LIMIT
                )

            transcript = self._enhanced_speech_recognition(audio)
            if not transcript:
                return None

            violations = self._comprehensive_keyword_analysis(transcript)
            filtered = self._filter_with_context(violations, transcript)

            if filtered:
                payload = {
                    "transcript": transcript,
                    "violations": filtered,
                    "timestamp": time.time(),
                }
                self.audio_violations.append(payload)
                return payload

        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(f"[Audio] analysis error: {e}")
            return None

    # ---------- helpers ----------

    def _enhanced_speech_recognition(self, audio) -> Optional[str]:
        if not self.recognizer:
            return None
        try:
            t = self.recognizer.recognize_google(audio)
            if t:
                return t.lower()
        except Exception:
            pass
        try:
            t = self.recognizer.recognize_google(audio, language="en-US", show_all=False)
            if t:
                return t.lower()
        except Exception:
            pass
        return None

    def _comprehensive_keyword_analysis(self, transcript: str) -> List[Dict[str, Any]]:
        violations: List[Dict[str, Any]] = []
        now = time.time()
        txt = f" {transcript} "
        for category, keywords in self.ENHANCED_CHEAT_KEYWORDS.items():
            for kw in keywords:
                if f" {kw} " in txt or kw in txt:
                    cooldown_key = f"{category}:{kw}"
                    if now - self.last_detection_time[cooldown_key] < 10:
                        continue
                    conf = self._calculate_keyword_confidence(transcript, kw, category)
                    if conf >= self.KEYWORD_CONFIDENCE_THRESHOLD:
                        violations.append({
                            "type": "audio_violation",
                            "category": category,
                            "keyword": kw,
                            "confidence": round(conf, 3),
                            "transcript": transcript,
                            "severity": self._determine_severity(category, conf),
                            "timestamp": now,
                        })
                        self.last_detection_time[cooldown_key] = now
        return violations

    def _filter_with_context(self, violations: List[Dict[str, Any]], transcript: str) -> List[Dict[str, Any]]:
        if not violations:
            return []
        seen = set()
        filtered = []
        for v in violations:
            key = (v["category"], v["keyword"])
            if key in seen:
                continue
            seen.add(key)
            filtered.append(v)
        benign = ["music", "noise", "background", "song"]
        if any(b in transcript for b in benign):
            filtered = [v for v in filtered if v["category"] != "collaboration"]
        return filtered

    def _calculate_keyword_confidence(self, transcript: str, keyword: str, category: str) -> float:
        base = 0.6
        t = transcript.lower()
        cue_boost = 0.0
        if category == "collaboration":
            for cue in ("tell me", "answer", "help", "please", "what is"):
                if cue in t:
                    cue_boost = max(cue_boost, 0.15)
        elif category == "web_search":
            for cue in ("open", "search", "result", "browser", "tab"):
                if cue in t:
                    cue_boost = max(cue_boost, 0.12)
        elif category == "devices":
            for cue in ("turn on", "connect", "pair", "call"):
                if cue in t:
                    cue_boost = max(cue_boost, 0.1)
        length_penalty = min(0.2, max(0.0, (len(t.split()) - 16) * 0.01))
        score = base + cue_boost - length_penalty
        return max(0.0, min(1.0, score))

    def _determine_severity(self, category: str, confidence: float) -> str:
        if confidence >= 0.8:
            return "high"
        if confidence >= 0.65:
            return "medium"
        return "low"

    def _filter_with_context(self, violations, transcript):
        """Filter violations based on context and patterns"""
        filtered = []

        for violation in violations:
            # Additional context filtering can be added here
            if self._is_likely_violation(violation, transcript):
                filtered.append(violation)

        return filtered

    def _is_likely_violation(self, violation, transcript):
        """Determine if violation is likely genuine based on context"""
        # Simple filtering - can be enhanced
        confidence = violation.get('confidence', 0)
        return confidence > self.config.KEYWORD_CONFIDENCE_THRESHOLD

# ================= ENHANCED ALERT MANAGER =================


class AlertManager:
    """Enhanced FPS-agnostic time-gated alerting with comprehensive violation tracking"""

    def __init__(self, config: Config):
        self.config = config
        self.alert_states = {}
        self.last_alert_times = {}
        self.alert_history = []
        os.makedirs(self.config.SNAPSHOT_DIR, exist_ok=True)

        self.alert_messages = {
            'head_pose': "⚠️ HEAD TURNED - Look at screen!",
            'eye_gaze': "⚠️ EYES LOOKING AWAY - Look at center!",
            'multi_face': "⚠️ MULTIPLE FACES DETECTED!",
            'phone': "⚠️ PHONE DETECTED - Remove phone!",
            'book': "⚠️ UNAUTHORIZED MATERIAL DETECTED!",
            'earphones': "⚠️ EARPHONES DETECTED - Remove earphones!",
            'no_face': "⚠️ NO FACE DETECTED - Show your face!",
            'audio_violation': "⚠️ SUSPICIOUS AUDIO - No external help allowed!",
            'looking_down': "⚠️ LOOKING DOWN - Possible cheating behavior!",
            'looking_up': "⚠️ LOOKING UP - Stay focused on screen!",
            'rapid_movement': "⚠️ EXCESSIVE MOVEMENT - Suspicious activity!",
            'face_verification': "⚠️ Face does not match the student!",
            'face_verification_match': "✅ Face verified as the student"

        }

        self.alert_colors = {
            'head_pose': (0, 0, 255),
            'eye_gaze': (0, 165, 255),
            'multi_face': (0, 0, 255),
            'phone': (0, 165, 255),
            'book': (0, 255, 255),
            'earphones': (255, 0, 255),
            'no_face': (128, 128, 128),
            'audio_violation': (255, 0, 0),
            'looking_down': (0, 0, 255),
            'looking_up': (0, 165, 255),
            'rapid_movement': (0, 255, 255),
            'face_verification': (0, 0, 255),          # red for mismatch
            'face_verification_match': (0, 255, 0)     # green for match
        }

        # Sliding-window samples: per-type deque[(t_monotonic, bool)]
        self.samples = defaultdict(lambda: deque())

    def _state(self, alert_type: str):
        if alert_type not in self.alert_states:
            self.alert_states[alert_type] = {
                'phase': 'idle',
                'armed_at': None,
                'last_true_at': None,
                'last_triggered_at': -1e9,
            }
            self.last_alert_times[alert_type] = -1e9
        return self.alert_states[alert_type]

    def check_alert(self, alert_type: str, condition: bool, frame, student=None, quiz_id=None) -> bool:
        """Enhanced alert checking with time-based gating"""
        now = time.monotonic()
        st = self._state(alert_type)

        # --- Custom timing for face verification alerts ---
        if alert_type in ('face_verification', 'face_verification_match'):
            alert_duration = 1.0
            alert_hold = 6.0
            alert_cooldown = 6.0
        else:
            alert_duration = self.config.ALERT_DURATION
            alert_hold = self.config.ALERT_CLEAR_HOLD_SECONDS
            alert_cooldown = self.config.ALERT_COOLDOWN

        # Record sample & prune window
        dq = self.samples[alert_type]
        dq.append((now, condition))
        win = self.config.EVIDENCE_WINDOW_SECONDS
        while dq and (now - dq[0][0]) > win:
            dq.popleft()

        # Evidence ratio in window
        true_ratio = (sum(1 for t, c in dq if c) / len(dq)) if dq else 0.0
        if condition:
            st['last_true_at'] = now

        cooldown_ok = (now - st['last_triggered_at']) >= alert_cooldown
        enough_evidence = (true_ratio >= self.config.EVIDENCE_MIN_TRUE_RATIO)

        # State machine
        if enough_evidence:
            if st['phase'] in ('idle', 'cooldown'):
                st['phase'] = 'arming'
                st['armed_at'] = now
            elif st['phase'] == 'arming':
                if (now - (st['armed_at'] or now)) >= alert_duration and cooldown_ok:
                    # ✅ FIXED: pass student and quiz_id here
                    self.trigger_alert(alert_type, frame, student=student, quiz_id=quiz_id)
                    st['phase'] = 'triggered'
                    st['last_triggered_at'] = now
        else:
            st['armed_at'] = None
            if st['phase'] == 'arming':
                st['phase'] = 'idle'
            elif st['phase'] == 'triggered':
                last_true = st['last_true_at'] or now
                if (now - last_true) >= alert_hold:
                    st['phase'] = 'cooldown'

        if st['phase'] == 'cooldown' and not enough_evidence:
            st['phase'] = 'idle'

        return st['phase'] in ('arming', 'triggered')

    def trigger_alert(self, alert_type: str, frame, student=None, quiz_id=None) -> None:
        timestamp = datetime.now()
        snapshot_path = self.save_snapshot_to_db(alert_type, frame, student, quiz_id, timestamp)
        print(f"[ALERT] {timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.alert_messages.get(alert_type, alert_type)}")

    def save_snapshot_to_db(self, alert_type, frame, student, quiz_id, timestamp):
        """Save frame directly to database as binary data"""
        try:
            import cv2
            from proctor.models import ProctorAlert
            from proctor.models import Quiz

            success, buffer = cv2.imencode('.jpg', frame)
            if not success:
                print("[ERROR] Failed to encode frame.")
                return None

            image_bytes = buffer.tobytes()
            quiz = Quiz.objects.get(id=quiz_id)  #  get instance

            alert = ProctorAlert.objects.create(
                student=student,
                quiz=quiz,  #  pass the instance
                alert_type=alert_type,
                message=self.alert_messages.get(alert_type, "⚠️ ALERT!"),
                snapshot_data=image_bytes,
                snapshot_mime='image/jpeg'
            )

            print(f"[INFO] Snapshot saved directly in DB for {alert_type}.")
            return alert.id
        except Exception as e:
            print(f"[ERROR] Could not save snapshot to DB: {e}")
            return None

# ================= MAIN ENHANCED DETECTION SYSTEM =================


import cv2
import dlib
import time
import queue
import threading
import numpy as np
from collections import deque
from typing import Tuple, List
from ultralytics import YOLO
import time
import logging

class DetectionSystem:
    """Main enhanced detection system integrating all components"""

    def __init__(self, process_with_camera: bool = False, camera_index: int = 0, reference_image_path: str = None):
        self.config = Config()
        self.alert_manager = AlertManager(self.config)
        self._using_camera = False
        self.cap = None
        self.reference_encodings = []  # list of all sample encodings
        self.face_verification_tolerance = 0.5

        # Initialize enhanced components
        self.multi_face_detector = EnhancedMultiFaceDetector(self.config)
        self.head_pose_estimator = FlexibleHeadPoseEstimator(self.config, self.config.LANDMARKS_MODEL)
        self.audio_detector = ComprehensiveAudioDetector(self.config)

        # Disable any server-drawn overlays (banners). We’ll also no-op the painter below.
        self.paint_server_overlays = False

        # Audio processing
        self.audio_queue = queue.Queue()
        self.audio_thread = None
        self.audio_processing_active = False

        self._last_face_verify_time = 0
        self._face_verify_interval = 3.0  # seconds
        


        # Load reference encodings (optional)
        if reference_image_path:
            self.load_reference_images(reference_image_path)

        if process_with_camera:
            self.start_camera(camera_index)

        # Models (keep original YOLO for object detection)
        self.face_detector = dlib.get_frontal_face_detector()
        self.predictor = dlib.shape_predictor(self.config.LANDMARKS_MODEL)
        try:
            self.yolo = YOLO(self.config.YOLO_MODEL)
            # Force CPU for stability on Windows unless you explicitly want CUDA
            self._yolo_device = 'cpu'
        except Exception as e:
            print(f"YOLO initialization failed: {e}")
            self.yolo = None
            self._yolo_device = None

        # Safety: make server painter a no-op so no banner is ever burned in
        if hasattr(self.alert_manager, "draw_alerts"):
            self.alert_manager.draw_alerts = lambda f, a: f

        self.yaw_history = deque(maxlen=10)
        self.gaze_history = deque(maxlen=5)
        self.stats = {'session_start': time.time()}

        # Vision lock (avoid native crashes from concurrent calls)
        self._vision_lock = threading.Lock()

    # ---------------- CAMERA CONTROL ----------------
    def start_camera(self, camera_index: int = 0):
        if not self._using_camera:
            self.cap = cv2.VideoCapture(camera_index)
            self._using_camera = True

    def stop_camera(self):
        if self._using_camera and self.cap:
            self.cap.release()
            self._using_camera = False

    # ---------------- AUDIO THREAD ----------------
    def start_audio_processing(self):
        """Start audio processing in separate thread"""
        if not self.audio_processing_active:
            self.audio_processing_active = True
            self.audio_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
            self.audio_thread.start()

    def stop_audio_processing(self):
        """Stop audio processing thread"""
        self.audio_processing_active = False
        if self.audio_thread:
            self.audio_thread.join(timeout=2.0)

    def _audio_processing_loop(self):
        """Audio processing loop running in separate thread"""
        while self.audio_processing_active:
            try:
                audio_result = self.audio_detector.listen_and_analyze_comprehensive()
                if audio_result:
                    self.audio_queue.put(audio_result)
                time.sleep(0.1)
            except Exception as e:
                print(f"Audio processing error: {e}")
                time.sleep(1.0)

    # ---------------- RECT SAFETY ----------------
    def _to_dlib_rect(self, box, frame_shape):
        """Clamp any rectangle (various formats) safely inside image bounds."""
        h, w = frame_shape[:2]
        if isinstance(box, dlib.rectangle):
            l, t, r, b = box.left(), box.top(), box.right(), box.bottom()
        elif isinstance(box, (tuple, list)) and len(box) == 4:
            a, b_, c, d = box
            # Heuristic: if c,d look like width/height
            if c >= 1 and d >= 1 and a >= 0 and b_ >= 0:
                l, t, r, b = int(a), int(b_), int(a + c), int(b_ + d)   # (x,y,w,h)
            else:
                t, r, b, l = int(a), int(b_), int(c), int(d)            # (t,r,b,l)
        else:
            l = int(getattr(box, 'left', 0));  t = int(getattr(box, 'top', 0))
            r = int(getattr(box, 'right', 1)); b = int(getattr(box, 'bottom', 1))
        l = max(0, min(l, w - 1)); r = max(0, min(r, w - 1))
        t = max(0, min(t, h - 1)); b = max(0, min(b, h - 1))
        if r <= l: r = min(w - 1, l + 1)
        if b <= t: b = min(h - 1, t + 1)
        return dlib.rectangle(l, t, r, b)

    # ---------------- MAIN FRAME PROCESS ----------------
    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, List[str]]:
        with self._vision_lock:
            if frame is None:
                return frame, []

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            active_alerts = []

            student = getattr(self, "current_student", None)
            quiz_id = getattr(self, "current_quiz_id", None)

            # -------- Multi-face detection --------
            faces, is_multiface_violation = self.multi_face_detector.detect_realistic_multiface(frame)
            if is_multiface_violation:
                if self.alert_manager.check_alert(
                    'multi_face', True, frame,
                    student=self.current_student, quiz_id=self.current_quiz_id
                ):
                    active_alerts.append('multi_face')
            else:
                self.alert_manager.check_alert(
                    'multi_face', False, frame,
                    student=self.current_student, quiz_id=self.current_quiz_id
                )

            # -------- No face detection --------
            if len(faces) == 0:
                if self.alert_manager.check_alert(
                    'no_face', True, frame,
                    student=self.current_student, quiz_id=self.current_quiz_id
                ):
                    active_alerts.append('no_face')
            else:
                self.alert_manager.check_alert(
                    'no_face', False, frame,
                    student=self.current_student, quiz_id=self.current_quiz_id
                )

            # -------- Head pose / gaze analysis --------
            if len(faces) > 0:
                primary_face = self._to_dlib_rect(faces[0], frame.shape)

                try:
                    yaw, pitch, is_pose_violation = self.head_pose_estimator.estimate_flexible_pose(
                        primary_face, gray, frame.shape
                    )
                except Exception:
                    yaw, pitch, is_pose_violation = 0.0, 0.0, False

                if is_pose_violation:
                    if abs(yaw) > self.config.HEAD_YAW_THRESHOLD:
                        if self.alert_manager.check_alert(
                            'head_pose', True, frame,
                            student=self.current_student, quiz_id=self.current_quiz_id
                        ):
                            active_alerts.append('head_pose')
                    elif pitch > self.config.LOOKING_DOWN_THRESHOLD:
                        if self.alert_manager.check_alert(
                            'looking_down', True, frame,
                            student=self.current_student, quiz_id=self.current_quiz_id
                        ):
                            active_alerts.append('looking_down')
                    elif pitch < self.config.LOOKING_UP_THRESHOLD:
                        if self.alert_manager.check_alert(
                            'looking_up', True, frame,
                            student=self.current_student, quiz_id=self.current_quiz_id
                        ):
                            active_alerts.append('looking_up')
                else:
                    self.alert_manager.check_alert(
                        'head_pose', False, frame,
                        student=self.current_student, quiz_id=self.current_quiz_id
                    )
                    self.alert_manager.check_alert(
                        'looking_down', False, frame,
                        student=self.current_student, quiz_id=self.current_quiz_id
                    )
                    self.alert_manager.check_alert(
                        'looking_up', False, frame,
                        student=self.current_student, quiz_id=self.current_quiz_id
                    )

                # -------- Eye gaze --------
                try:
                    landmarks = self.predictor(gray, primary_face)
                    direction, ratio = self.calculate_eye_gaze(landmarks, gray)
                except Exception:
                    direction, ratio = "CENTER", 1.0

                if direction in ("LEFT", "RIGHT"):
                    if self.alert_manager.check_alert(
                        'eye_gaze', True, frame,
                        student=self.current_student, quiz_id=self.current_quiz_id
                    ):
                        active_alerts.append('eye_gaze')
                else:
                    self.alert_manager.check_alert(
                        'eye_gaze', False, frame,
                        student=self.current_student, quiz_id=self.current_quiz_id
                    )

                # -------- Face verification (every 3 seconds) --------
                now = time.time()
                face_match = None

                if (now - getattr(self, "_last_face_verify_time", 0)) >= getattr(self, "_face_verify_interval", 3.0):
                    self._last_face_verify_time = now
                    face_match = self.verify_face(frame)
                    print("[DEBUG] verify_face result:", face_match)

                    # Clear both states before re-checking
                    self.alert_manager.check_alert('face_verification_match', False, frame)
                    self.alert_manager.check_alert('face_verification', False, frame)

                    if face_match is None:
                        pass  # no face detected
                    elif face_match:
                        self.alert_manager.trigger_alert(
                            'face_verification_match', frame,
                            student=self.current_student, quiz_id=self.current_quiz_id
                        )
                        active_alerts.append('face_verification_match')
                    else:
                        self.alert_manager.trigger_alert(
                            'face_verification', frame,
                            student=self.current_student, quiz_id=self.current_quiz_id
                        )
                        active_alerts.append('face_verification')



            return frame, active_alerts



    # ---------------- HELPERS ----------------
    def calculate_eye_gaze(self, landmarks, gray) -> Tuple[str, float]:
        """Original eye gaze calculation (unchanged)"""
        def get_ratio(eye_points):
            region = np.array([(landmarks.part(p).x, landmarks.part(p).y)
                              for p in eye_points], np.int32)
            mask = np.zeros_like(gray)
            cv2.fillPoly(mask, [region], 255)
            eye = cv2.bitwise_and(gray, gray, mask=mask)
            _, thresh = cv2.threshold(eye, 70, 255, cv2.THRESH_BINARY)
            hh, ww = thresh.shape
            if hh == 0 or ww == 0:
                return 1.0
            left = thresh[:, :ww//2]
            right = thresh[:, ww//2:]
            return (cv2.countNonZero(left)+1)/(cv2.countNonZero(right)+1)

        left_ratio = get_ratio([36, 37, 38, 39, 40, 41])
        right_ratio = get_ratio([42, 43, 44, 45, 46, 47])
        hratio = (left_ratio + right_ratio) / 2.0

        direction = "CENTER"
        if hratio < self.config.GAZE_THRESHOLD_RIGHT:
            direction = "RIGHT"
        elif hratio > self.config.GAZE_THRESHOLD_LEFT:
            direction = "LEFT"
        return direction, hratio

    def detect_objects(self, frame):
        """Keep YOLO drawing (boxes/labels), but no banners."""
        detected = {'persons': 0, 'phones': 0, 'books': 0, 'earphones': 0}
        annotated = frame
        try:
            results = self.yolo.predict(
                frame,
                conf=self.config.OBJECT_CONFIDENCE,
                verbose=False,
                device=getattr(self, "_yolo_device", 'cpu')
            )
            if results and len(results) > 0 and getattr(results[0], "boxes", None) is not None:
                boxes = results[0].boxes
                classes = boxes.cls.cpu().numpy().astype(int)
                names_map = getattr(self.yolo, 'names', {})
                names = [names_map.get(int(c), str(int(c))) for c in classes]
                detected['phones'] = names.count('Phone')
                detected['books'] = names.count('Book')
                detected['earphones'] = names.count('Ear_phones')
                annotated = results[0].plot()  # draws only detection boxes/labels
        except Exception:
            pass
        return detected, annotated

    def draw_stats(self, frame):
        """Disabled stats banner"""
        return frame  # no session time / active alerts text

    def get_status(self):
        """Get current system status (for JSON meta)"""
        active_now = [
            atype
            for atype, st in self.alert_manager.alert_states.items()
            if st.get('phase') in ('arming', 'triggered')
        ]
        print("[DEBUG] Active alerts in get_status():", active_now)  # 👈 Add this
        return {"terminated": False, "reason": "", "warning": None, "active_alerts": active_now}

    def run_yield(self):
        """Generator function that yields processed frames from camera."""
        if not self._using_camera or not self.cap:
            self.start_camera()
        self.start_audio_processing()
        while self._using_camera:
            ret, frame = self.cap.read()
            if not ret:
                break
            processed_frame, _ = self.process_frame(frame)
            yield processed_frame

    def verify_face(self, frame) -> bool | None:
            """
            Return:
            - True if face matches any reference,
            - False if mismatch,
            - None if no face detected.
            Also prints debug info for testing.
            """
            if not self.reference_encodings:
                print("[DEBUG] No reference encodings, verification disabled")
                return None  # No samples → verification disabled

            import face_recognition

            # Convert frame to RGB (required by face_recognition)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Detect faces
            face_locations = face_recognition.face_locations(rgb_frame)
            if len(face_locations) == 0:
                print("[DEBUG] No face detected")
                return None

            # Encode first face
            face_encoding = face_recognition.face_encodings(rgb_frame, [face_locations[0]])
            if len(face_encoding) == 0:
                print("[DEBUG] Failed to encode face")
                return None

            # Compare with stored references
            matches = face_recognition.compare_faces(
                self.reference_encodings,
                face_encoding[0],
                tolerance=self.face_verification_tolerance
            )

            if any(matches):
                print("[DEBUG] Face matches reference")
                return True
            else:
                print("[DEBUG] Face does NOT match reference")
                return False

    def load_reference_images(self, folder_path: str, max_images: int = 5):
        """
        Load up to `max_images` face encodings from the given folder.
        """
        import face_recognition, os
        self.reference_encodings = []  # reset old references
        if os.path.isdir(folder_path):
            count = 0
            for file in os.listdir(folder_path):
                if file.lower().endswith((".jpg", ".jpeg", ".png")):
                    img_path = os.path.join(folder_path, file)
                    try:
                        img = face_recognition.load_image_file(img_path)
                        encs = face_recognition.face_encodings(img)
                        if len(encs) > 0:
                            self.reference_encodings.append(encs[0])
                            count += 1
                            if count >= max_images:
                                break
                    except Exception as e:
                        print(f"[WARN] Failed to encode {img_path}: {e}")
        print(f"[DEBUG] Loaded {len(self.reference_encodings)} reference encodings from {folder_path}")
