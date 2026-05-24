"""Face detector + wellness signals using MediaPipe Tasks API (0.10+).

Detects:
  - Face presence + count
  - Eye openness (drowsiness signal)
  - Gaze direction (looking at screen vs away)
  - Head tilt and stress signals
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_MODELS_DIR = Path(__file__).parent.parent / "models"

_face_landmarker = None


def _get_face_landmarker():
    global _face_landmarker
    if _face_landmarker is None:
        import mediapipe as mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(_MODELS_DIR / "face_landmarker.task")
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=4,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
        )
        _face_landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
    return _face_landmarker


def _to_mp_image(image_bytes: bytes):
    import mediapipe as mp
    rgb = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


# MediaPipe FaceLandmarker landmark indices (same as FaceMesh)
_LEFT_EYE_TOP = 159
_LEFT_EYE_BOTTOM = 145
_LEFT_EYE_LEFT = 33
_LEFT_EYE_RIGHT = 133
_RIGHT_EYE_TOP = 386
_RIGHT_EYE_BOTTOM = 374
_RIGHT_EYE_LEFT = 362
_RIGHT_EYE_RIGHT = 263
_NOSE_TIP = 1
_LEFT_CHEEK = 234
_RIGHT_CHEEK = 454
_UPPER_LIP = 13
_LOWER_LIP = 14


def _ear(lm: list, top: int, bottom: int, left: int, right: int) -> float:
    vertical = math.dist((lm[top].x, lm[top].y), (lm[bottom].x, lm[bottom].y))
    horizontal = math.dist((lm[left].x, lm[left].y), (lm[right].x, lm[right].y))
    return vertical / (horizontal + 1e-6)


def _analyze_face(lm: list) -> dict[str, Any]:
    ear_left = _ear(lm, _LEFT_EYE_TOP, _LEFT_EYE_BOTTOM, _LEFT_EYE_LEFT, _LEFT_EYE_RIGHT)
    ear_right = _ear(lm, _RIGHT_EYE_TOP, _RIGHT_EYE_BOTTOM, _RIGHT_EYE_LEFT, _RIGHT_EYE_RIGHT)
    avg_ear = (ear_left + ear_right) / 2
    eyes_open = avg_ear > 0.15
    drowsy = avg_ear < 0.10

    nose = lm[_NOSE_TIP]
    mid_x = (lm[_LEFT_CHEEK].x + lm[_RIGHT_CHEEK].x) / 2
    head_tilt_x = abs(nose.x - mid_x)
    looking_at_camera = head_tilt_x < 0.06

    lip_gap = abs(lm[_UPPER_LIP].y - lm[_LOWER_LIP].y)
    mouth_open = lip_gap > 0.02

    signals: list[str] = []
    if drowsy:
        signals.append("somnolencia_detectada")
    elif not eyes_open:
        signals.append("ojos_cerrados")
    if not looking_at_camera:
        signals.append("distraccion_detectada")
    if head_tilt_x > 0.1:
        signals.append("cabeza_girada")

    return {
        "eyes_open": eyes_open,
        "drowsy": drowsy,
        "looking_at_camera": looking_at_camera,
        "mouth_open": mouth_open,
        "eye_openness_ratio": round(avg_ear, 3),
        "wellness_signals": signals,
    }


# Sparse key landmark indices for frontend rendering
_KEY_LM = [
    33, 133, 159, 145, 362, 263, 386, 374,  # eyes
    1, 4, 5,                                  # nose
    61, 291, 13, 14,                          # mouth
    10, 234, 454, 152,                        # face outline
    70, 63, 105, 300, 293, 334,              # eyebrows
]


def detect(image_bytes: bytes) -> dict[str, Any]:
    mp_img = _to_mp_image(image_bytes)
    landmarker = _get_face_landmarker()
    result = landmarker.detect(mp_img)

    faces: list[dict] = []
    landmarks_list: list[list[dict]] = []

    for face_lm in result.face_landmarks:
        try:
            faces.append(_analyze_face(face_lm))
        except Exception:
            faces.append({"error": "analysis_failed"})

        # Sparse key points + every 3rd landmark for frontend overlay
        pts: list[dict] = []
        for i in _KEY_LM:
            if i < len(face_lm):
                pts.append({"i": i, "x": round(face_lm[i].x, 4), "y": round(face_lm[i].y, 4)})
        for i in range(0, min(468, len(face_lm)), 3):
            pts.append({"i": i, "x": round(face_lm[i].x, 4), "y": round(face_lm[i].y, 4)})
        landmarks_list.append(pts)

    return {
        "face_count": len(faces),
        "faces": faces,
        "drowsiness_detected": any(f.get("drowsy", False) for f in faces),
        "distraction_detected": any(not f.get("looking_at_camera", True) for f in faces),
        "face_landmarks": landmarks_list,
    }
