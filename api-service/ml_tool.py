"""Wellbeing Predictor — scikit-learn model exposed as a LangChain Tool.

The model predicts wellbeing risk level from multimodal signals:
- PHQ-9 and GAD-7 assessment scores
- Agent-reported scores (sleep, energy, focus, mood, screen, streaks)
- Digital behavior signals from Chrome extension (daily screen hours, social %)
- CV signals (drowsiness, distraction counts)

Dataset: trained on a calibrated synthetic dataset derived from published
research correlations (PHQ-9/GAD-7 vs digital behavior, validated thresholds).
A real public dataset download is attempted first (Student Mental Health Survey).
"""
from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np

MODEL_PATH = Path(os.getenv("ML_MODEL_PATH", "ml_model/wellbeing_model.pkl"))

RISK_LABELS = {0: "bajo", 1: "moderado", 2: "alto"}

RISK_RECS: dict[int, list[str]] = {
    0: [
        "Mantén tu rutina actual — los indicadores son positivos.",
        "Considera registrar tus hábitos para mantener el momentum.",
    ],
    1: [
        "Reduce el tiempo en redes sociales 30 minutos antes de dormir.",
        "Programa una pausa activa de 5 minutos cada 90 minutos de trabajo.",
        "Evalúa si tu carga de trabajo se puede redistribuir esta semana.",
    ],
    2: [
        "Considera hablar con un profesional de salud mental.",
        "Reduce el tiempo de pantalla pasiva a menos de 2 horas diarias.",
        "Prioriza el sueño: apunta a 7-8 horas con hora fija de acostarte.",
        "Desactiva notificaciones no esenciales para recuperar foco.",
    ],
}


def _feature_vector(data: dict[str, Any]) -> np.ndarray:
    """Convert a signal dict to a feature vector matching the trained model."""
    phq9 = float(data.get("phq9_score", 0))
    gad7 = float(data.get("gad7_score", 0))
    screen_score = float(data.get("screen_score", 0))
    habits_score = float(data.get("habits_score", 0))
    sleep_score = float(data.get("sleep_score", 50)) / 100.0 * 8
    energy_score = float(data.get("energy_score", 50))
    focus_score = float(data.get("focus_score", 50))
    mood_score = float(data.get("mood_score", 50))
    daily_screen_h = float(data.get("daily_screen_hours", 4))
    social_pct = float(data.get("social_pct", 30))
    drowsy_count = float(data.get("drowsiness_count", 0))
    distraction_count = float(data.get("distraction_count", 0))

    return np.array([[
        phq9,
        gad7,
        screen_score,
        habits_score,
        sleep_score,
        energy_score / 100.0,
        focus_score / 100.0,
        mood_score / 100.0,
        daily_screen_h,
        social_pct / 100.0,
        drowsy_count,
        distraction_count,
    ]])


def predict_wellbeing(signals: dict[str, Any]) -> dict[str, Any]:
    """Predict wellbeing risk from multimodal signals.

    Returns risk_level (bajo/moderado/alto), confidence, and recommendations.
    Falls back to rule-based logic if model file is not found.
    """
    if MODEL_PATH.exists():
        try:
            with open(MODEL_PATH, "rb") as f:
                model = pickle.load(f)
            X = _feature_vector(signals)
            pred = int(model.predict(X)[0])
            proba = model.predict_proba(X)[0]
            confidence = float(proba[pred])
        except Exception:
            pred, confidence = _rule_based(signals)
    else:
        pred, confidence = _rule_based(signals)

    return {
        "risk_level": RISK_LABELS[pred],
        "risk_code": pred,
        "confidence": round(confidence, 2),
        "recommendations": RISK_RECS[pred],
    }


def _rule_based(signals: dict[str, Any]) -> tuple[int, float]:
    """Simple rule-based fallback using clinical cutoffs."""
    phq9 = float(signals.get("phq9_score", 0))
    gad7 = float(signals.get("gad7_score", 0))
    screen_h = float(signals.get("daily_screen_hours", 4))
    sleep_s = float(signals.get("sleep_score", 50))
    energy_s = float(signals.get("energy_score", 50))

    risk = 0
    if phq9 >= 15 or gad7 >= 10:
        risk = 2
    elif phq9 >= 10 or gad7 >= 7 or screen_h >= 7 or sleep_s < 30 or energy_s < 30:
        risk = 1
    elif phq9 >= 5 or gad7 >= 5 or screen_h >= 5:
        risk = max(risk, 1)

    return risk, 0.75


# ── LangChain Tool integration ────────────────────────────────────────────────

try:
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def wellbeing_predictor(signals_json: str) -> str:
        """Predict wellbeing risk level from multimodal signals.

        Input: JSON string with keys:
          phq9_score (0-27), gad7_score (0-21), screen_score (0-15),
          habits_score (0-15), sleep_score (0-100), energy_score (0-100),
          focus_score (0-100), mood_score (0-100),
          daily_screen_hours (float), social_pct (0-100),
          drowsiness_count (int), distraction_count (int)

        Output: JSON with risk_level, confidence, recommendations.
        """
        try:
            signals = json.loads(signals_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "JSON inválido"})
        result = predict_wellbeing(signals)
        return json.dumps(result, ensure_ascii=False)

    WELLBEING_TOOL = wellbeing_predictor

except ImportError:
    WELLBEING_TOOL = None
