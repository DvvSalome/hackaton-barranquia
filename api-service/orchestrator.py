"""Kairós Orchestrator — LangChain AgentExecutor that coordinates all specialists.

The orchestrator:
1. Receives conversation context + all external signals (CV, Chrome extension, assessments)
2. Runs all 6 specialist chains in parallel
3. Calls the WellbeingPredictorTool (ML model)
4. Synthesizes a final response for the user
"""
from __future__ import annotations

import json
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from specialists import run_all_specialists, SPECIALIST_DEFINITIONS
from ml_tool import predict_wellbeing
from langchain_tools import run_diagnostic_pipeline
import triage_history
import diary as diary_store
import meditation as meditation_store
import spotify_tool
import calendar_tool
import health_tool
import notion_tool
import strava_tool


ORCHESTRATOR_SYSTEM = """\
Eres Kairós Core, el copiloto de bienestar digital del usuario.

Tu rol:
- Integras lecturas de 6 consultores especialistas (ánimo, sueño, foco, energía, pantalla, rachas).
- Tienes acceso a señales de computer vision (postura, somnolencia detectada por cámara).
- Tienes acceso a datos de comportamiento digital (apps usadas, tiempo en redes sociales).
- Tienes el resultado del modelo predictivo de bienestar (riesgo: bajo/moderado/alto).
- Tienes resultados de evaluaciones validadas (PHQ-9, GAD-7).
- Tienes datos de herramientas conectadas: diario personal, sesiones de meditación,
  música en Spotify, eventos del calendario, métricas de salud (pasos, sueño, FC),
  tareas de Notion y actividad física de Strava.

Tu síntesis debe:
1. Conectar los hilos de los especialistas de forma coherente (3-5 frases).
2. Mencionar el nivel de riesgo si es moderado o alto (con sensibilidad, sin alarmar).
3. Dar 1-2 acciones concretas y pequeñas para las próximas 24 horas.
4. Usar español neutro, voz cálida, sin emojis, sin signos de exclamación.
5. NUNCA mencionar diagnósticos clínicos ni citar scores directamente al usuario.

Lecturas de los especialistas:
{specialist_readings}

Predicción ML de bienestar:
{ml_prediction}

Señales de CV (computer vision):
{cv_signals}

Señales digitales (Chrome extension):
{digital_signals}

Contexto del onboarding (baseline):
{baseline_context}
"""


def _format_specialists(readings: list[dict[str, Any]]) -> str:
    lines = []
    for r in readings:
        name = r.get("name", r.get("agent", "?"))
        score = r.get("score")
        insight = r.get("insight", "sin datos")
        score_str = f"{score:.0f}/100" if score is not None else "sin dato"
        lines.append(f"- {name} [{score_str}]: {insight}")
    return "\n".join(lines) if lines else "sin lecturas"


def _format_cv(cv_data: dict[str, Any] | None) -> str:
    if not cv_data:
        return "sin datos de cámara"
    ws = cv_data.get("wellness_summary", {})
    alerts = ws.get("wellness_alerts", [])
    positives = ws.get("positive_signals", [])
    posture = ws.get("posture", "sin_datos")
    parts = [f"postura: {posture}"]
    if alerts:
        parts.append(f"alertas: {', '.join(alerts)}")
    if positives:
        parts.append(f"positivos: {', '.join(positives)}")
    return "; ".join(parts)


def _format_digital(digital: dict[str, Any] | None) -> str:
    if not digital:
        return "sin datos de extensión Chrome"
    total_h = digital.get("total_hours", 0)
    by_cat = digital.get("by_category", {})
    social_h = by_cat.get("social", 0)
    work_h = by_cat.get("work", 0)
    top_domains = digital.get("top_domains", [])[:3]
    parts = [
        f"tiempo total: {total_h:.1f}h",
        f"redes sociales: {social_h:.1f}h",
        f"trabajo: {work_h:.1f}h",
    ]
    if top_domains:
        parts.append(f"sitios principales: {', '.join(top_domains)}")
    return "; ".join(parts)


def _build_ml_signals(
    specialists: list[dict[str, Any]],
    cv_data: dict[str, Any] | None,
    digital: dict[str, Any] | None,
    assessments: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Aggregate all signals into the ML model input format."""
    scores_by_agent = {r["agent"]: r.get("score") for r in specialists if "agent" in r}

    phq9_row = next((a for a in (assessments or []) if a.get("kind") == "phq9"), None)
    gad7_row = next((a for a in (assessments or []) if a.get("kind") == "gad7"), None)
    screen_row = next((a for a in (assessments or []) if a.get("kind") == "screen"), None)
    habits_row = next((a for a in (assessments or []) if a.get("kind") == "habits"), None)

    cv_ws = (cv_data or {}).get("wellness_summary", {})
    drowsy = 1 if cv_ws.get("drowsiness_detected") else len(cv_ws.get("wellness_alerts", []))

    return {
        "phq9_score": phq9_row["score"] if phq9_row else 0,
        "gad7_score": gad7_row["score"] if gad7_row else 0,
        "screen_score": screen_row["score"] if screen_row else 0,
        "habits_score": habits_row["score"] if habits_row else 0,
        "sleep_score": scores_by_agent.get("sueno") or 50,
        "energy_score": scores_by_agent.get("energia") or 50,
        "focus_score": scores_by_agent.get("foco") or 50,
        "mood_score": scores_by_agent.get("animo") or 50,
        "daily_screen_hours": (digital or {}).get("total_hours", 4),
        "social_pct": (
            ((digital or {}).get("by_category", {}).get("social", 0) /
             max((digital or {}).get("total_hours", 1), 0.01)) * 100
        ),
        "drowsiness_count": drowsy,
        "distraction_count": len(cv_ws.get("wellness_alerts", [])),
    }


async def synthesize(
    transcript: str,
    cv_data: dict[str, Any] | None = None,
    digital_signals: dict[str, Any] | None = None,
    assessments: list[dict[str, Any]] | None = None,
    baseline_context: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Full synthesis pipeline: specialists + ML + orchestrator summary."""

    # Build rich context for specialists
    context_parts = [f"Transcript del check-in:\n{transcript}"]
    if cv_data:
        context_parts.append(f"Computer Vision:\n{_format_cv(cv_data)}")
    if digital_signals:
        context_parts.append(f"Comportamiento digital:\n{_format_digital(digital_signals)}")
    if baseline_context:
        context_parts.append(f"Baseline del usuario:\n{baseline_context}")

    # Auto-inject tool data by profile_id
    if profile_id:
        diary_ctx = diary_store.get_context_for_llm(profile_id)
        if diary_ctx:
            context_parts.append(diary_ctx)
        med_ctx = meditation_store.get_context_for_llm(profile_id)
        if med_ctx:
            context_parts.append(med_ctx)
        spotify_ctx = spotify_tool.get_context_for_llm(profile_id)
        if spotify_ctx:
            context_parts.append(spotify_ctx)
        cal_ctx = calendar_tool.get_context_for_llm(profile_id)
        if cal_ctx:
            context_parts.append(cal_ctx)
        health_ctx = health_tool.get_context_for_llm(profile_id)
        if health_ctx:
            context_parts.append(health_ctx)
        notion_ctx = notion_tool.get_context_for_llm(profile_id)
        if notion_ctx:
            context_parts.append(notion_ctx)
        strava_ctx = strava_tool.get_context_for_llm(profile_id)
        if strava_ctx:
            context_parts.append(strava_ctx)
    full_context = "\n\n".join(context_parts)

    # Run specialists in parallel
    specialist_results = await run_all_specialists(full_context)

    # Run ML predictor
    ml_signals = _build_ml_signals(specialist_results, cv_data, digital_signals, assessments)
    ml_prediction = predict_wellbeing(ml_signals)

    # Inject historical trend so SummaryTool can escalate if pattern is worsening
    history_ctx = triage_history.get_context_for_llm(profile_id or "anon")
    if history_ctx:
        ml_signals["_history_context"] = history_ctx
    trend = triage_history.get_trend(profile_id or "anon")
    if trend.get("escalate_referral"):
        ml_signals["_escalate_referral"] = True

    # Run diagnostic pipeline (DiagnosticTool → SolutionTool → ReferralTool → SummaryTool)
    triage = await run_diagnostic_pipeline(ml_signals, specialist_results)

    # Build composite score (weighted average of specialist scores)
    valid_scores = [r["score"] for r in specialist_results if r.get("score") is not None]
    composite_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else None

    # Orchestrator final synthesis
    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.4,
        max_tokens=500,
        default_headers={
            "HTTP-Referer": "https://kairos.local",
            "X-Title": "Kairos-Core",
        },
    )
    # Use raw messages (not ChatPromptTemplate) to avoid LangChain parsing
    # JSON curly braces like {risk_level} as template variables
    system_text = ORCHESTRATOR_SYSTEM.format(
        specialist_readings=_format_specialists(specialist_results),
        ml_prediction=json.dumps(ml_prediction, ensure_ascii=False),
        cv_signals=_format_cv(cv_data),
        digital_signals=_format_digital(digital_signals),
        baseline_context=baseline_context or "no disponible",
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content="Entrega la síntesis del día."),
    ]
    summary = await (llm | StrOutputParser()).ainvoke(messages)

    # Persist triage result for temporal trend tracking
    if profile_id:
        triage_history.save_triage(
            profile_id,
            triage,
            composite_score=composite_score,
            risk_level=ml_prediction.get("risk_level"),
        )

    return {
        "summary": summary,
        "specialists": specialist_results,
        "ml_prediction": ml_prediction,
        "composite_score": composite_score,
        "signals_used": ml_signals,
        "diagnosis": triage["diagnosis"],
        "solution": triage["solution"],
        "referral": triage["referral"],
        "professional_summary": triage["professional_summary"],
        "trend": trend,
    }
