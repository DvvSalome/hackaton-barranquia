"""Definiciones canónicas de los 4 tests del onboarding.

PHQ-9 y GAD-7 son instrumentos validados y de dominio público (Pfizer Inc.).
Hábitos y Pantalla son escalas simples diseñadas para el hackathon.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


LIKERT_FREQ = [
    {"v": 0, "label": "Para nada", "emoji": "🌿"},
    {"v": 1, "label": "Varios días", "emoji": "🌤️"},
    {"v": 2, "label": "Más de la mitad de los días", "emoji": "🌥️"},
    {"v": 3, "label": "Casi todos los días", "emoji": "🌧️"},
]

LIKERT_AGREE = [
    {"v": 0, "label": "Nunca", "emoji": "🌿"},
    {"v": 1, "label": "A veces", "emoji": "🌤️"},
    {"v": 2, "label": "Frecuentemente", "emoji": "🌥️"},
    {"v": 3, "label": "Casi siempre", "emoji": "🌧️"},
]


ASSESSMENTS: Dict[str, Dict[str, Any]] = {
    "phq9": {
        "title": "PHQ-9",
        "subtitle": "Indicadores de ánimo",
        "prompt": "En las últimas 2 semanas, ¿con qué frecuencia te has sentido afectado/a por…",
        "options": LIKERT_FREQ,
        "questions": [
            "Poco interés o placer en hacer las cosas",
            "Sentirte decaído/a, deprimido/a o sin esperanzas",
            "Dificultad para dormir, o dormir demasiado",
            "Sentirte cansado/a o con poca energía",
            "Poco apetito o comer en exceso",
            "Sentirte mal contigo mismo/a — o que has fallado",
            "Dificultad para concentrarte (leer, ver, etc.)",
            "Moverte o hablar tan despacio que otros lo notaron — o lo contrario, inquieto/a",
            "Pensamientos de que estarías mejor sin estar aquí, o hacerte daño",
        ],
    },
    "gad7": {
        "title": "GAD-7",
        "subtitle": "Indicadores de ansiedad",
        "prompt": "En las últimas 2 semanas, ¿con qué frecuencia te ha molestado…",
        "options": LIKERT_FREQ,
        "questions": [
            "Sentirte nervioso/a, ansioso/a o con los nervios de punta",
            "No poder dejar de preocuparte o controlar la preocupación",
            "Preocuparte demasiado por diferentes cosas",
            "Dificultad para relajarte",
            "Estar tan inquieto/a que te resulta difícil quedarte quieto/a",
            "Irritarte o enojarte con facilidad",
            "Sentir miedo, como si algo terrible fuera a pasar",
        ],
    },
    "habits": {
        "title": "Hábitos digitales",
        "subtitle": "Check-in inicial",
        "prompt": "Sobre tus últimas 2 semanas…",
        "options": LIKERT_AGREE,
        "questions": [
            "Me cuesta dormir 7 horas seguidas",
            "Reviso el teléfono apenas me despierto",
            "Me cuesta hacer pausas durante mi jornada",
            "Cancelo planes o ejercicio por falta de energía",
            "Llego al final del día con la sensación de no haber avanzado",
        ],
    },
    "screen": {
        "title": "Tiempo en pantalla",
        "subtitle": "Baseline de uso",
        "prompt": "Sobre tu relación con el celular y las pantallas…",
        "options": LIKERT_AGREE,
        "questions": [
            "Uso el teléfono más de 5 horas al día",
            "Hago scroll pasivo en redes sin saber por qué",
            "Reviso notificaciones cada pocos minutos",
            "Uso pantallas en la última hora antes de dormir",
            "Pierdo la noción del tiempo cuando uso ciertas apps",
        ],
    },
}


# Severidad canónica
def _severity_phq9(score: int) -> str:
    if score <= 4:
        return "minimal"
    if score <= 9:
        return "mild"
    if score <= 14:
        return "moderate"
    if score <= 19:
        return "moderately_severe"
    return "severe"


def _severity_gad7(score: int) -> str:
    if score <= 4:
        return "minimal"
    if score <= 9:
        return "mild"
    if score <= 14:
        return "moderate"
    return "severe"


def _severity_screen(score: int, max_score: int) -> str:
    # 5 ítems × 3 = 15 máx
    pct = score / max_score if max_score else 0
    if pct <= 0.25:
        return "minimal"
    if pct <= 0.5:
        return "mild"
    if pct <= 0.75:
        return "moderate"
    return "severe"


def score_assessment(kind: str, answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    if kind not in ASSESSMENTS:
        raise ValueError(f"test desconocido: {kind}")
    spec = ASSESSMENTS[kind]
    questions = spec["questions"]

    total = 0
    for a in answers:
        v = a.get("value")
        if isinstance(v, (int, float)):
            total += int(v)
    max_score = len(questions) * 3

    if kind == "phq9":
        severity = _severity_phq9(total)
    elif kind == "gad7":
        severity = _severity_gad7(total)
    else:
        severity = _severity_screen(total, max_score)

    return {
        "score": total,
        "max_score": max_score,
        "severity": severity,
    }


SEVERITY_LABELS_ES = {
    "minimal": "mínimo",
    "mild": "leve",
    "moderate": "moderado",
    "moderately_severe": "moderadamente severo",
    "severe": "severo",
    "unknown": "sin medir",
}


def profile_context_summary(results: List[Dict[str, Any]]) -> Optional[str]:
    """Convierte los resultados en un texto breve que el Core puede usar como contexto."""
    if not results:
        return None
    by_kind = {r["kind"]: r for r in results}
    parts = []
    if "phq9" in by_kind:
        r = by_kind["phq9"]
        parts.append(
            f"PHQ-9 (ánimo) = {r['score']}/{r['max_score']} → {SEVERITY_LABELS_ES.get(r['severity'], r['severity'])}"
        )
    if "gad7" in by_kind:
        r = by_kind["gad7"]
        parts.append(
            f"GAD-7 (ansiedad) = {r['score']}/{r['max_score']} → {SEVERITY_LABELS_ES.get(r['severity'], r['severity'])}"
        )
    if "habits" in by_kind:
        r = by_kind["habits"]
        parts.append(
            f"hábitos digitales = {r['score']}/{r['max_score']} → {SEVERITY_LABELS_ES.get(r['severity'], r['severity'])}"
        )
    if "screen" in by_kind:
        r = by_kind["screen"]
        parts.append(
            f"uso de pantalla = {r['score']}/{r['max_score']} → {SEVERITY_LABELS_ES.get(r['severity'], r['severity'])}"
        )
    return "Baseline del usuario (onboarding):\n- " + "\n- ".join(parts)
