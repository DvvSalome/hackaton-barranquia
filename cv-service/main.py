"""Kairós CV Service — computer vision microservice.

Endpoints:
  POST /cv/analyze      — full analysis: objects + persons + gestures + face
  POST /cv/objects      — only object/person detection (YOLOv8n)
  POST /cv/gestures     — only hand gestures + body pose (MediaPipe)
  POST /cv/face         — only face + drowsiness + gaze (MediaPipe FaceMesh)
  GET  /cv/health       — liveness check + model load status
  GET  /cv/models       — list loaded models and their status

Input: multipart/form-data with field "image" (JPEG/PNG/WebP)
       OR JSON body with field "image_b64" (base64-encoded image)

All detectors are lazy-loaded on first call and cached in memory.
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Kairós CV Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Model status tracking ─────────────────────────────────────────────────────

_model_status: dict[str, str] = {
    "yolov8n": "not_loaded",
    "mediapipe_hands": "not_loaded",
    "mediapipe_pose": "not_loaded",
    "mediapipe_face_mesh": "not_loaded",
}


def _mark_loaded(model: str) -> None:
    _model_status[model] = "loaded"


# ── Input helpers ─────────────────────────────────────────────────────────────

class B64Request(BaseModel):
    image_b64: str
    options: Optional[dict] = None


async def _bytes_from_upload(image: UploadFile) -> bytes:
    data = await image.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB)")
    return data


def _bytes_from_b64(b64: str) -> bytes:
    try:
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {exc}") from exc


# ── Wrapper helpers (run sync detectors in thread pool) ───────────────────────

async def _run_objects(image_bytes: bytes) -> dict[str, Any]:
    from detectors import objects as obj_det
    result = await asyncio.to_thread(obj_det.detect, image_bytes)
    _mark_loaded("yolov8n")
    return result


async def _run_gestures(image_bytes: bytes) -> dict[str, Any]:
    from detectors import gestures as gest_det
    result = await asyncio.to_thread(gest_det.detect, image_bytes)
    _mark_loaded("mediapipe_hands")
    _mark_loaded("mediapipe_pose")
    return result


async def _run_face(image_bytes: bytes) -> dict[str, Any]:
    from detectors import face as face_det
    result = await asyncio.to_thread(face_det.detect, image_bytes)
    _mark_loaded("mediapipe_face_mesh")
    return result


# ── Wellness summary from combined results ────────────────────────────────────

def _build_wellness_summary(
    objects: dict,
    gestures: dict,
    face: dict,
) -> dict[str, Any]:
    """Aggregate CV signals into wellness-relevant insights for Kairós."""
    alerts: list[str] = []
    positives: list[str] = []

    # Face signals
    if face.get("drowsiness_detected"):
        alerts.append("somnolencia_detectada — considera un descanso")
    if face.get("distraction_detected"):
        alerts.append("distraccion_detectada — foco reducido")

    # Pose signals
    pose = gestures.get("pose", {})
    for sig in pose.get("stress_signals", []):
        alerts.append(sig)

    # Environment
    env = objects.get("environment_clues", [])
    if "ambiente_trabajo" in env:
        positives.append("entorno_de_trabajo_detectado")
    if "ambiente_estudio" in env:
        positives.append("ambiente_de_estudio_detectado")
    if "alimentos_visibles" in env:
        positives.append("momento_de_alimentacion")
    if "objeto_distraccion" in env:
        alerts.append("objeto_distractor_visible")

    # Gesture
    positive_gestures = {"thumbs_up", "ok", "open_hand"}
    if gestures.get("dominant_gesture") in positive_gestures:
        positives.append(f"gesto_positivo: {gestures['dominant_gesture']}")

    posture = pose.get("posture", "sin_datos")

    return {
        "wellness_alerts": alerts,
        "positive_signals": positives,
        "posture": posture,
        "environment": env,
        "person_count": objects.get("person_count", 0),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/cv/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "kairos-cv", "models": _model_status}


@app.get("/cv/models")
def models() -> dict[str, Any]:
    return {
        "models": {
            "yolov8n": {
                "status": _model_status["yolov8n"],
                "task": "object + person detection",
                "source": "ultralytics/assets (auto-download ~6 MB)",
                "classes": 80,
            },
            "mediapipe_hands": {
                "status": _model_status["mediapipe_hands"],
                "task": "hand landmark + gesture recognition",
                "source": "google mediapipe (bundled)",
            },
            "mediapipe_pose": {
                "status": _model_status["mediapipe_pose"],
                "task": "body pose + posture analysis",
                "source": "google mediapipe (bundled)",
            },
            "mediapipe_face_mesh": {
                "status": _model_status["mediapipe_face_mesh"],
                "task": "face mesh + drowsiness + gaze",
                "source": "google mediapipe (bundled)",
            },
        }
    }


@app.post("/cv/analyze")
async def analyze_full(image: Optional[UploadFile] = File(default=None),
                       image_b64: Optional[str] = Form(default=None)) -> dict[str, Any]:
    """Full analysis: objects + persons + gestures + face — all in parallel."""
    if image is not None:
        data = await _bytes_from_upload(image)
    elif image_b64:
        data = _bytes_from_b64(image_b64)
    else:
        raise HTTPException(status_code=422, detail="Provide 'image' file or 'image_b64' field")

    t0 = time.perf_counter()
    objects_res, gestures_res, face_res = await asyncio.gather(
        _run_objects(data),
        _run_gestures(data),
        _run_face(data),
        return_exceptions=False,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    wellness = _build_wellness_summary(objects_res, gestures_res, face_res)

    return {
        "inference_ms": elapsed_ms,
        "wellness_summary": wellness,
        "objects": objects_res,
        "gestures": gestures_res,
        "face": face_res,
    }


@app.post("/cv/analyze/b64")
async def analyze_full_b64(req: B64Request) -> dict[str, Any]:
    """Same as /cv/analyze but accepts JSON body with base64 image."""
    data = _bytes_from_b64(req.image_b64)
    t0 = time.perf_counter()
    objects_res, gestures_res, face_res = await asyncio.gather(
        _run_objects(data),
        _run_gestures(data),
        _run_face(data),
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    wellness = _build_wellness_summary(objects_res, gestures_res, face_res)
    return {
        "inference_ms": elapsed_ms,
        "wellness_summary": wellness,
        "objects": objects_res,
        "gestures": gestures_res,
        "face": face_res,
    }


@app.post("/cv/objects")
async def detect_objects(image: UploadFile = File(...)) -> dict[str, Any]:
    data = await _bytes_from_upload(image)
    return await _run_objects(data)


@app.post("/cv/objects/b64")
async def detect_objects_b64(req: B64Request) -> dict[str, Any]:
    return await _run_objects(_bytes_from_b64(req.image_b64))


@app.post("/cv/gestures")
async def detect_gestures(image: UploadFile = File(...)) -> dict[str, Any]:
    data = await _bytes_from_upload(image)
    return await _run_gestures(data)


@app.post("/cv/gestures/b64")
async def detect_gestures_b64(req: B64Request) -> dict[str, Any]:
    return await _run_gestures(_bytes_from_b64(req.image_b64))


@app.post("/cv/face")
async def detect_face(image: UploadFile = File(...)) -> dict[str, Any]:
    data = await _bytes_from_upload(image)
    return await _run_face(data)


@app.post("/cv/face/b64")
async def detect_face_b64(req: B64Request) -> dict[str, Any]:
    return await _run_face(_bytes_from_b64(req.image_b64))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
