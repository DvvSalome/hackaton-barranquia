"""Kairós — 4 LangChain Tools para triage clínico.

Pipeline:
  DiagnosticTool  (determinista) → árbol de decisiones sobre signals
  SolutionTool    (determinista) → pasos accionables por diagnóstico
  ReferralTool    (determinista) → ¿derivar? ¿a quién? con criterios
  SummaryTool     (LLM)         → mensaje estructurado para el profesional
                                   solo se ejecuta cuando refer=True

Punto de entrada: run_diagnostic_pipeline(signals, specialist_results)
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool as lc_tool

# ─── Constantes ───────────────────────────────────────────────────────────────

_SEVERITY_LABELS = {0: "sin_indicadores", 1: "leve", 2: "moderado", 3: "severo"}

_PATTERN_LABELS: dict[str, str] = {
    "distres_severo": "Distrés psicológico severo",
    "distres_moderado": "Distrés psicológico moderado",
    "distres_leve": "Distrés psicológico leve",
    "fatiga_digital": "Fatiga digital con impacto emocional",
    "uso_digital_elevado": "Uso digital elevado",
    "privacion_sueno": "Privación de sueño significativa",
    "sueno_deficiente": "Calidad de sueño deficiente",
    "baja_energia": "Energía reducida",
    "deficit_foco": "Déficit de foco y concentración",
    "dependencia_redes": "Uso excesivo de redes sociales",
    "bienestar_en_rango": "Bienestar en rango normal",
}

_SOLUTIONS: dict[str, dict[str, Any]] = {
    "distres_severo": {
        "steps": [
            "Consultar con un psicólogo o psiquiatra en los próximos 3-5 días.",
            "Desactivar notificaciones no esenciales para reducir carga cognitiva.",
            "Fijar una hora de dormir y despertar constante esta semana.",
            "Caminata de 20 minutos diarios — sin teléfono.",
        ],
        "timeframe": "inmediato (1-3 días)",
        "self_care_level": "bajo — priorizar atención profesional",
    },
    "distres_moderado": {
        "steps": [
            "Agendar cita con psicólogo esta semana.",
            "Reducir exposición a redes sociales a 30 minutos diarios.",
            "Incorporar una pausa activa de 5 min cada 90 min de trabajo.",
            "Llevar registro breve de estado de ánimo cada noche.",
        ],
        "timeframe": "esta semana",
        "self_care_level": "medio — combinar autocuidado con acompañamiento profesional",
    },
    "distres_leve": {
        "steps": [
            "Implementar rutina de desconexión digital 1 hora antes de dormir.",
            "Ejercicio aeróbico ligero 3 veces esta semana.",
            "Revisar carga de trabajo y priorizar tareas de alto impacto.",
        ],
        "timeframe": "esta semana",
        "self_care_level": "alto — autocuidado suficiente si síntomas no escalan",
    },
    "fatiga_digital": {
        "steps": [
            "Limitar tiempo de pantalla pasiva a menos de 2 horas diarias.",
            "Activar modo escala de grises en el teléfono para reducir estímulo.",
            "Tomar descansos de pantalla de 10 min cada hora de trabajo.",
        ],
        "timeframe": "inmediato",
        "self_care_level": "alto",
    },
    "uso_digital_elevado": {
        "steps": [
            "Establecer horarios sin pantalla (comidas, primera hora del día).",
            "Revisar qué apps consumen más tiempo e instalar límites.",
        ],
        "timeframe": "esta semana",
        "self_care_level": "alto",
    },
    "privacion_sueno": {
        "steps": [
            "Consultar con médico general si la privación supera 1 semana.",
            "Aplicar higiene del sueño: oscuridad total, temperatura fresca, sin pantallas 1h antes.",
            "Evitar cafeína después de las 14:00.",
        ],
        "timeframe": "inmediato",
        "self_care_level": "medio",
    },
    "sueno_deficiente": {
        "steps": [
            "Mantener hora fija de acostarse y despertarse incluso los fines de semana.",
            "Reducir estimulantes (cafeína, alcohol) en la tarde.",
        ],
        "timeframe": "esta semana",
        "self_care_level": "alto",
    },
    "baja_energia": {
        "steps": [
            "Revisar hábitos alimenticios — incluir proteína en el desayuno.",
            "Hidratación: mínimo 2 litros de agua diarios.",
            "Caminata de 15 minutos al mediodía.",
        ],
        "timeframe": "esta semana",
        "self_care_level": "alto",
    },
    "deficit_foco": {
        "steps": [
            "Técnica Pomodoro: 25 min trabajo enfocado + 5 min pausa.",
            "Desactivar notificaciones durante bloques de trabajo profundo.",
            "Definir las 3 tareas prioritarias la noche anterior.",
        ],
        "timeframe": "inmediato",
        "self_care_level": "alto",
    },
    "dependencia_redes": {
        "steps": [
            "Instalar límites de uso en redes sociales (Screen Time / Digital Wellbeing).",
            "Designar 2 momentos fijos del día para revisar redes (no de forma reactiva).",
        ],
        "timeframe": "esta semana",
        "self_care_level": "alto",
    },
    "bienestar_en_rango": {
        "steps": [
            "Mantener la rutina actual — los indicadores son positivos.",
            "Registrar hábitos para sostener el momentum.",
        ],
        "timeframe": "sin urgencia",
        "self_care_level": "alto",
    },
}


# ─── Diagnostic model loader (trained DecisionTree, lazy) ─────────────────────

_DIAGNOSTIC_BUNDLE: dict | None = None
_DIAGNOSTIC_MODEL_PATH = (
    __import__("pathlib").Path(__file__).parent / "ml_model" / "diagnostic_model.pkl"
)


def _load_diagnostic_model() -> dict | None:
    global _DIAGNOSTIC_BUNDLE
    if _DIAGNOSTIC_BUNDLE is not None:
        return _DIAGNOSTIC_BUNDLE
    if _DIAGNOSTIC_MODEL_PATH.exists():
        import pickle
        with open(_DIAGNOSTIC_MODEL_PATH, "rb") as f:
            _DIAGNOSTIC_BUNDLE = pickle.load(f)
    return _DIAGNOSTIC_BUNDLE


def _rule_based_diagnostic(signals: dict) -> dict:
    """Fallback rule-based diagnostic when the trained model is not available."""
    phq9 = float(signals.get("phq9_score", 0))
    gad7 = float(signals.get("gad7_score", 0))
    sleep = float(signals.get("sleep_score", 50))
    energy = float(signals.get("energy_score", 50))
    focus = float(signals.get("focus_score", 50))
    mood = float(signals.get("mood_score", 50))
    screen_h = float(signals.get("daily_screen_hours", 4))
    social_pct = float(signals.get("social_pct", 30))

    patterns: list[str] = []
    severity = 0

    if phq9 >= 15 or gad7 >= 10:
        patterns.append("distres_severo")
        severity = max(severity, 3)
    elif phq9 >= 10 or gad7 >= 7:
        patterns.append("distres_moderado")
        severity = max(severity, 2)
    elif phq9 >= 5 or gad7 >= 5:
        patterns.append("distres_leve")
        severity = max(severity, 1)

    if screen_h >= 7 and mood < 40:
        patterns.append("fatiga_digital")
        severity = max(severity, 2)
    elif screen_h >= 5:
        patterns.append("uso_digital_elevado")
        severity = max(severity, 1)

    if sleep < 30:
        patterns.append("privacion_sueno")
        severity = max(severity, 2)
    elif sleep < 50:
        patterns.append("sueno_deficiente")
        severity = max(severity, 1)

    if energy < 30:
        patterns.append("baja_energia")
        severity = max(severity, 1)
    if focus < 30:
        patterns.append("deficit_foco")
        severity = max(severity, 1)
    if social_pct > 60:
        patterns.append("dependencia_redes")
        severity = max(severity, 1)

    if not patterns:
        patterns.append("bienestar_en_rango")

    primary = patterns[0]
    return {
        "diagnosis": _PATTERN_LABELS.get(primary, primary),
        "primary_pattern": primary,
        "matched_patterns": patterns,
        "severity": _SEVERITY_LABELS[severity],
        "severity_code": severity,
        "model": "rules",
    }


def _model_based_diagnostic(signals: dict, bundle: dict) -> dict:
    """Diagnostic using the trained DecisionTreeClassifier."""
    import numpy as np

    phq9 = float(signals.get("phq9_score", 0))
    gad7 = float(signals.get("gad7_score", 0))
    sleep = float(signals.get("sleep_score", 50))
    energy = float(signals.get("energy_score", 50))
    focus = float(signals.get("focus_score", 50))
    mood = float(signals.get("mood_score", 50))
    screen_h = float(signals.get("daily_screen_hours", 4))
    social_pct = float(signals.get("social_pct", 30))

    X = np.array([[phq9, gad7, sleep, energy, focus, mood, screen_h, social_pct]])
    model = bundle["model"]
    le: any = bundle["label_encoder"]

    primary = le.inverse_transform(model.predict(X))[0]
    proba = model.predict_proba(X)[0]
    confidence = float(proba.max())

    # Secondary patterns via rules (model gives primary; rules catch co-occurring)
    rule_result = _rule_based_diagnostic(signals)
    matched = [primary] + [p for p in rule_result["matched_patterns"] if p != primary]

    severity_map = {
        "distres_severo": 3, "distres_moderado": 2, "privacion_sueno": 2,
        "fatiga_digital": 2, "distres_leve": 1, "baja_energia": 1,
        "uso_digital_elevado": 1, "sueno_deficiente": 1, "deficit_foco": 1,
        "dependencia_redes": 1, "bienestar_en_rango": 0,
    }
    severity = severity_map.get(primary, 0)

    return {
        "diagnosis": _PATTERN_LABELS.get(primary, primary),
        "primary_pattern": primary,
        "matched_patterns": matched[:5],
        "severity": _SEVERITY_LABELS[severity],
        "severity_code": severity,
        "confidence": round(confidence, 3),
        "model": "decision_tree",
    }


# ─── Tool 1: DiagnosticTool ───────────────────────────────────────────────────

@lc_tool
def diagnostic_tool(signals_json: str) -> str:
    """Árbol de decisiones clínico sobre señales de bienestar.

    Usa el modelo entrenado (diagnostic_model.pkl) si está disponible;
    cae a reglas deterministas si no existe el archivo.

    Input: JSON con phq9_score, gad7_score, sleep_score, energy_score,
           focus_score, mood_score, daily_screen_hours, social_pct.
    Output: JSON con diagnosis, primary_pattern, matched_patterns,
            severity, severity_code, model (decision_tree | rules).
    """
    try:
        signals = json.loads(signals_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "JSON inválido"})

    bundle = _load_diagnostic_model()
    if bundle is not None:
        result = _model_based_diagnostic(signals, bundle)
    else:
        result = _rule_based_diagnostic(signals)

    return json.dumps(result, ensure_ascii=False)


# ─── Tool 2: SolutionTool ─────────────────────────────────────────────────────

@lc_tool
def solution_tool(diagnosis_json: str) -> str:
    """Mapea un diagnóstico de DiagnosticTool a pasos accionables.

    Input: JSON con primary_pattern y severity_code (output de DiagnosticTool).
    Output: JSON con steps[], timeframe, self_care_level.
    """
    try:
        diag = json.loads(diagnosis_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "JSON inválido"})

    primary = diag.get("primary_pattern", "bienestar_en_rango")
    solution = _SOLUTIONS.get(primary, _SOLUTIONS["bienestar_en_rango"])
    return json.dumps(solution, ensure_ascii=False)


# ─── Tool 3: ReferralTool ─────────────────────────────────────────────────────

@lc_tool
def referral_tool(context_json: str) -> str:
    """Decide si el usuario debe ser derivado a un especialista y a cuál.

    Input: JSON con:
      diagnosis   → output de DiagnosticTool
      risk_level  → "bajo" | "moderado" | "alto" (del ML predictor)
    Output: JSON con refer (bool), specialist_type, urgency, referral_criteria[].
    """
    try:
        ctx = json.loads(context_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "JSON inválido"})

    diagnosis = ctx.get("diagnosis", {})
    risk_level = ctx.get("risk_level", "bajo")
    escalate = ctx.get("escalate_referral", False)

    severity_code = diagnosis.get("severity_code", 0)
    patterns = diagnosis.get("matched_patterns", [])

    # Auto-escalate if temporal trend shows repeated severe sessions
    if escalate and severity_code < 3:
        severity_code = max(severity_code, 2)

    refer = False
    specialist_type: str | None = None
    urgency = "no_aplica"
    criteria: list[str] = []

    if severity_code >= 3 or risk_level == "alto":
        refer = True
        specialist_type = "psicólogo o psiquiatra"
        urgency = "prioritario (esta semana)"
        criteria.append("Indicadores clínicos en rango severo (PHQ-9 ≥15 o GAD-7 ≥10)")
    if escalate and refer:
        criteria.append("Patrón severo repetido en sesiones anteriores — derivación urgente")
    elif severity_code >= 2 or risk_level == "moderado":
        refer = True
        specialist_type = "psicólogo"
        urgency = "próximas dos semanas"
        criteria.append("Distrés moderado con impacto funcional sostenido")

    if "privacion_sueno" in patterns:
        criteria.append("Privación de sueño significativa (score <30/100)")
        if not refer:
            refer = True
            specialist_type = "médico general"
            urgency = "esta semana"

    if "fatiga_digital" in patterns and severity_code >= 2:
        criteria.append("Fatiga digital severa con impacto en estado de ánimo")
        if not refer:
            refer = True
            specialist_type = "médico general"
            urgency = "esta semana"

    return json.dumps({
        "refer": refer,
        "specialist_type": specialist_type,
        "urgency": urgency,
        "referral_criteria": criteria,
    }, ensure_ascii=False)


# ─── Tool 4: SummaryTool (LLM) ───────────────────────────────────────────────

async def _generate_professional_summary(
    diagnosis: dict[str, Any],
    solution: dict[str, Any],
    referral: dict[str, Any],
    specialist_results: list[dict[str, Any]],
    signals: dict[str, Any],
) -> str:
    """LLM call que genera el mensaje estructurado para el profesional.
    Solo se invoca cuando referral['refer'] es True.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_core.output_parsers import StrOutputParser
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

    specialist_lines = "\n".join(
        f"- {r.get('name', r.get('agent', '?'))} "
        f"[{r.get('score', 'sin dato')}/100]: {r.get('insight', '')}"
        for r in specialist_results
        if not r.get("error")
    )

    SUMMARY_SYSTEM = """\
Eres un asistente clínico de Kairós. Tu tarea es redactar un mensaje de derivación \
para un profesional de la salud (psicólogo, psiquiatra o médico general).

El mensaje debe ser:
- Conciso y profesional (no más de 200 palabras).
- En español neutro.
- Estructurado con estas secciones:
  1. Motivo de derivación (1-2 frases con el diagnóstico principal).
  2. Patrones detectados (lista de señales objetivas con valores concretos).
  3. Evolución observada (basada en lecturas de los especialistas).
  4. Pregunta orientadora para el profesional (1 pregunta concreta que facilite \
la primera consulta).

NO inventes datos. Solo usa la información que se te proporciona.
"""

    SUMMARY_HUMAN = """\
Diagnóstico principal: {diagnosis}
Patrones detectados: {patterns}
Severidad: {severity}
Especialista sugerido: {specialist_type}
Urgencia: {urgency}
Criterios de derivación: {criteria}

Lecturas de especialistas Kairós:
{specialist_lines}

Señales objetivas:
- PHQ-9: {phq9} | GAD-7: {gad7}
- Sueño: {sleep}/100 | Energía: {energy}/100 | Foco: {focus}/100
- Horas de pantalla: {screen_h}h/día | % redes sociales: {social_pct}%
{history_block}
"""

    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.3,
        max_tokens=400,
        default_headers={
            "HTTP-Referer": "https://kairos.local",
            "X-Title": "Kairos-Triage",
        },
    )
    human_text = SUMMARY_HUMAN.format(
        diagnosis=diagnosis.get("diagnosis", ""),
        patterns=", ".join(diagnosis.get("matched_patterns", [])),
        severity=diagnosis.get("severity", ""),
        specialist_type=referral.get("specialist_type", ""),
        urgency=referral.get("urgency", ""),
        criteria="; ".join(referral.get("referral_criteria", [])),
        specialist_lines=specialist_lines or "sin lecturas disponibles",
        phq9=signals.get("phq9_score", 0),
        gad7=signals.get("gad7_score", 0),
        sleep=signals.get("sleep_score", 50),
        energy=signals.get("energy_score", 50),
        focus=signals.get("focus_score", 50),
        screen_h=round(signals.get("daily_screen_hours", 4), 1),
        social_pct=round(signals.get("social_pct", 0), 1),
        history_block=signals.get("_history_context", ""),
    )
    messages = [SystemMessage(content=SUMMARY_SYSTEM), HumanMessage(content=human_text)]
    return await (llm | StrOutputParser()).ainvoke(messages)


# ─── Pipeline público ─────────────────────────────────────────────────────────

async def run_diagnostic_pipeline(
    signals: dict[str, Any],
    specialist_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ejecuta las 4 tools en secuencia y devuelve el triage completo.

    Args:
        signals: feature vector del ML predictor (mismas keys que predict_wellbeing).
        specialist_results: lista de outputs de run_all_specialists().

    Returns:
        {
          "diagnosis": {...},
          "solution": {...},
          "referral": {...},
          "professional_summary": "<texto>" | None,
        }
    """
    # Tool 1: diagnóstico determinista
    diag_raw = diagnostic_tool.invoke(json.dumps(signals))
    diagnosis: dict[str, Any] = json.loads(diag_raw)

    # Tool 2: solución determinista
    sol_raw = solution_tool.invoke(json.dumps(diagnosis))
    solution: dict[str, Any] = json.loads(sol_raw)

    # Tool 3: decisión de derivación determinista
    ref_ctx = {
        "diagnosis": diagnosis,
        "risk_level": _infer_risk_from_severity(diagnosis.get("severity_code", 0)),
        "escalate_referral": bool(signals.get("_escalate_referral", False)),
    }
    ref_raw = referral_tool.invoke(json.dumps(ref_ctx))
    referral: dict[str, Any] = json.loads(ref_raw)

    # Tool 4: mensaje para el profesional (LLM, solo si refer=True)
    professional_summary: str | None = None
    if referral.get("refer"):
        professional_summary = await _generate_professional_summary(
            diagnosis, solution, referral, specialist_results, signals
        )

    return {
        "diagnosis": diagnosis,
        "solution": solution,
        "referral": referral,
        "professional_summary": professional_summary,
    }


def _infer_risk_from_severity(severity_code: int) -> str:
    return {0: "bajo", 1: "bajo", 2: "moderado", 3: "alto"}.get(severity_code, "bajo")
