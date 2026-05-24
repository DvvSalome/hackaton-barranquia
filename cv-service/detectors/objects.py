"""Object + person detector using YOLOv8n (pre-trained on COCO, 80 classes).

Model is lazy-loaded on first call and cached. No training required.
"""
from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

_model = None

PERSON_CLASS_ID = 0  # COCO class 0 = person


def _get_model():
    global _model
    if _model is None:
        from ultralytics import YOLO
        # yolov8n = nano (6 MB) — fastest CPU inference, ~30 FPS on modern CPU
        _model = YOLO("yolov8n.pt")
    return _model


def _pil_to_np(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))


def detect(image_bytes: bytes, conf: float = 0.35) -> dict[str, Any]:
    """Run YOLOv8n on image_bytes.

    Returns:
        persons: list of person bounding boxes with confidence
        objects: list of non-person detections
        person_count: int
        environment_clues: list[str]  high-level scene tags
    """
    img = Image.open(io.BytesIO(image_bytes))
    frame = _pil_to_np(img)
    model = _get_model()

    results = model(frame, conf=conf, verbose=False)[0]
    names = results.names

    persons: list[dict] = []
    objects: list[dict] = []

    for box in results.boxes:
        cls_id = int(box.cls[0])
        label = names[cls_id]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        entry = {
            "label": label,
            "confidence": round(confidence, 3),
            "bbox": {"x1": round(x1), "y1": round(y1), "x2": round(x2), "y2": round(y2)},
        }
        if cls_id == PERSON_CLASS_ID:
            persons.append(entry)
        else:
            objects.append(entry)

    # Sort by confidence descending
    persons.sort(key=lambda x: x["confidence"], reverse=True)
    objects.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "person_count": len(persons),
        "persons": persons,
        "objects": objects[:15],  # cap to avoid huge payloads
        "environment_clues": _environment_clues(objects),
    }


# ── Environment context ───────────────────────────────────────────────────────

_INDOOR_OBJECTS = {"chair", "couch", "bed", "dining table", "toilet", "tv", "laptop",
                   "keyboard", "mouse", "book", "clock", "vase", "refrigerator",
                   "microwave", "oven", "sink"}
_OUTDOOR_OBJECTS = {"car", "truck", "bus", "bicycle", "motorcycle", "traffic light",
                    "stop sign", "bench", "tree", "umbrella"}
_WORK_OBJECTS = {"laptop", "keyboard", "mouse", "monitor", "desk", "book", "backpack"}
_FOOD_OBJECTS = {"pizza", "sandwich", "apple", "banana", "orange", "cake",
                 "carrot", "hot dog", "donut", "cup", "bottle", "wine glass"}

def _environment_clues(objects: list[dict]) -> list[str]:
    detected_labels = {o["label"] for o in objects}
    clues: list[str] = []
    if detected_labels & _INDOOR_OBJECTS:
        clues.append("interior")
    if detected_labels & _OUTDOOR_OBJECTS:
        clues.append("exterior")
    if detected_labels & _WORK_OBJECTS:
        clues.append("ambiente_trabajo")
    if detected_labels & _FOOD_OBJECTS:
        clues.append("alimentos_visibles")
    if not clues:
        clues.append("entorno_desconocido")
    return clues
