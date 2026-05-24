"""Hand gesture + body pose detector using MediaPipe Tasks API (0.10+).

Detects:
  - Hand gestures: thumbs_up, thumbs_down, ok, peace, fist, open_hand, pointing
  - Body pose: presence + dominant posture (upright, hunched, reclined)
  - Stress signals: head tilt, hand-to-face contact, slouching
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_MODELS_DIR = Path(__file__).parent.parent / "models"

_hands_landmarker = None
_pose_landmarker = None


def _get_hands():
    global _hands_landmarker
    if _hands_landmarker is None:
        import mediapipe as mp
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(_MODELS_DIR / "hand_landmarker.task")
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        _hands_landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
    return _hands_landmarker


def _get_pose():
    global _pose_landmarker
    if _pose_landmarker is None:
        import mediapipe as mp
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(_MODELS_DIR / "pose_landmarker.task")
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        _pose_landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
    return _pose_landmarker


def _to_mp_image(image_bytes: bytes):
    import mediapipe as mp
    rgb = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


# ── Gesture classification ────────────────────────────────────────────────────

def _classify_hand_gesture(lm: list) -> str:
    """Classify gesture from 21 MediaPipe hand landmarks (Tasks API list)."""

    thumb_extended = lm[4].x < lm[3].x
    fingers_extended = [thumb_extended]
    for tip_id, pip_id in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        fingers_extended.append(lm[tip_id].y < lm[pip_id].y)

    thumb, index, middle, ring, pinky = fingers_extended

    if not any([index, middle, ring, pinky]) and thumb:
        return "thumbs_up" if lm[4].y < lm[9].y else "thumbs_down"
    if all([index, middle, ring, pinky]) and not thumb:
        return "open_hand"
    if not any(fingers_extended):
        return "fist"
    if index and middle and not ring and not pinky:
        dist = math.dist((lm[8].x, lm[8].y), (lm[12].x, lm[12].y))
        return "peace" if dist > 0.08 else "scissors"
    if index and not middle and not ring and not pinky:
        return "pointing"
    thumb_index_dist = math.dist((lm[4].x, lm[4].y), (lm[8].x, lm[8].y))
    if thumb_index_dist < 0.06 and middle and ring and pinky:
        return "ok"
    return "unknown"


# ── Pose analysis ─────────────────────────────────────────────────────────────

# Pose landmark indices (same as old API)
_NOSE = 0
_LEFT_EAR = 7
_RIGHT_EAR = 8
_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12
_LEFT_HIP = 23
_RIGHT_HIP = 24
_LEFT_WRIST = 15
_RIGHT_WRIST = 16


def _analyze_pose(lm: list) -> dict[str, Any]:
    nose = lm[_NOSE]
    l_shoulder = lm[_LEFT_SHOULDER]
    r_shoulder = lm[_RIGHT_SHOULDER]
    l_hip = lm[_LEFT_HIP]
    r_hip = lm[_RIGHT_HIP]
    l_wrist = lm[_LEFT_WRIST]
    r_wrist = lm[_RIGHT_WRIST]

    shoulder_y_diff = abs(l_shoulder.y - r_shoulder.y)
    shoulder_tilt = shoulder_y_diff > 0.05

    mid_shoulder_x = (l_shoulder.x + r_shoulder.x) / 2
    head_forward = abs(nose.x - mid_shoulder_x) > 0.1

    mid_hip_y = (l_hip.y + r_hip.y) / 2
    mid_shoulder_y = (l_shoulder.y + r_shoulder.y) / 2
    slouching = (mid_hip_y - mid_shoulder_y) < 0.15

    face_touch = any([
        math.dist((l_wrist.x, l_wrist.y), (nose.x, nose.y)) < 0.12,
        math.dist((r_wrist.x, r_wrist.y), (nose.x, nose.y)) < 0.12,
    ])

    posture = "erguido"
    if slouching or head_forward:
        posture = "encorvado"
    if shoulder_tilt:
        posture = "inclinado"

    stress_signals: list[str] = []
    if head_forward:
        stress_signals.append("cabeza_adelante")
    if face_touch:
        stress_signals.append("mano_cerca_cara")
    if slouching:
        stress_signals.append("hombros_caidos")

    return {
        "posture": posture,
        "stress_signals": stress_signals,
        "details": {
            "shoulder_tilt": shoulder_tilt,
            "head_forward": head_forward,
            "slouching": slouching,
            "hand_to_face": face_touch,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def detect(image_bytes: bytes) -> dict[str, Any]:
    mp_img = _to_mp_image(image_bytes)
    hands_model = _get_hands()
    pose_model = _get_pose()

    hands_result = hands_model.detect(mp_img)
    pose_result = pose_model.detect(mp_img)

    hands: list[dict] = []
    for i, hand_lm in enumerate(hands_result.hand_landmarks):
        handedness = "unknown"
        if hands_result.handedness and i < len(hands_result.handedness):
            cats = hands_result.handedness[i]
            if cats:
                handedness = cats[0].category_name.lower()
        gesture = _classify_hand_gesture(hand_lm)
        hands.append({"hand": handedness, "gesture": gesture})

    pose: dict[str, Any] = {"posture": "sin_datos", "stress_signals": [], "details": {}}
    if pose_result.pose_landmarks:
        try:
            pose = _analyze_pose(pose_result.pose_landmarks[0])
        except Exception:
            pass

    gestures = [h["gesture"] for h in hands if h["gesture"] != "unknown"]
    dominant_gesture = gestures[0] if gestures else None

    return {
        "hands": hands,
        "hand_count": len(hands),
        "dominant_gesture": dominant_gesture,
        "pose": pose,
    }
