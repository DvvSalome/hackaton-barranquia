"""Kairós API Service — FastAPI app (LangChain edition, lite version).

Services wired:
  /health                    — liveness
  /agents                    — list specialist definitions
  /checkin/synthesize        — run all specialists + ML + orchestrator
  /chat                      — core chat (LangChain)
  /extension/ingest          — Chrome extension digital data
  /digital/summary           — aggregated digital signals
  /cv/analyze-proxy          — proxy to cv-service
  /onboarding/tests          — assessment definitions
  /onboarding/submit         — submit and score an assessment
  /onboarding/{profile_id}   — get profile assessment context
  /auth/register             — register profile
  /auth/login                — login
  /recommendations           — LLM-based recommendations
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import diary
import meditation
import spotify_tool
import calendar_tool
import health_tool
import notion_tool
import strava_tool
import digital_signals as ds
from assessments import ASSESSMENTS, profile_context_summary, score_assessment
from config import CV_SERVICE_URL
from digital import compute_daily_metric, metric_summary_for_llm
from ml_tool import predict_wellbeing
from orchestrator import synthesize
from specialists import SPECIALIST_DEFINITIONS, run_specialist
from langchain_tools import run_diagnostic_pipeline

app = FastAPI(title="Kairós API Service — LangChain Edition", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory stores (lite version — swap for Supabase in prod) ──────────────
_profiles: Dict[str, Dict[str, Any]] = {}
_assessments: Dict[str, List[Dict[str, Any]]] = {}


# ─── Request schemas ──────────────────────────────────────────────────────────

class SynthesizeReq(BaseModel):
    transcript: str
    profile_id: Optional[str] = None
    cv_data: Optional[Dict[str, Any]] = None
    digital_override: Optional[Dict[str, Any]] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    messages: List[ChatMessage]
    profile_id: Optional[str] = None
    profile_context: Optional[str] = None


class ExtensionIngestReq(BaseModel):
    profile_id: Optional[str] = None
    sessions: List[Dict[str, Any]] = []
    queries: List[Dict[str, Any]] = []
    recompute_today: bool = True


class AssessmentAnswer(BaseModel):
    key: str
    value: int
    label: Optional[str] = None


class AssessmentSubmitReq(BaseModel):
    profile_id: str
    kind: str
    answers: List[AssessmentAnswer]


class LoginReq(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class RecommendReq(BaseModel):
    profile_id: Optional[str] = None
    profile_context: Optional[str] = None
    max_items: int = 4


class DiaryEntryReq(BaseModel):
    profile_id: str
    content: str
    title: Optional[str] = None
    mood: Optional[str] = None  # "bien" | "neutral" | "dificil"


class MeditationLogReq(BaseModel):
    profile_id: str
    duration_minutes: int
    session_type: str = "libre"


# ─── Health & metadata ────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "kairos-api-lc", "version": "0.2.0"}


@app.get("/agents")
async def list_agents() -> Dict[str, Any]:
    return SPECIALIST_DEFINITIONS


# ─── Specialist (single) ──────────────────────────────────────────────────────

@app.post("/agents/{kind}")
async def specialist_endpoint(kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
    context = body.get("context", "")
    if not context:
        raise HTTPException(status_code=400, detail="context requerido")
    try:
        result = await run_specialist(kind, context)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ─── Full check-in synthesis ──────────────────────────────────────────────────

@app.post("/checkin/synthesize")
async def checkin_synthesize(req: SynthesizeReq) -> Dict[str, Any]:
    """Run all specialists + ML model + orchestrator synthesis."""
    baseline: Optional[str] = None
    if req.profile_id and req.profile_id in _assessments:
        baseline = profile_context_summary(_assessments[req.profile_id])

    # Resolve digital signals from extension store
    digital = req.digital_override
    if digital is None and req.profile_id:
        digital = ds.get_summary(req.profile_id)

    result = await synthesize(
        transcript=req.transcript,
        cv_data=req.cv_data,
        digital_signals=digital,
        assessments=_assessments.get(req.profile_id or "", []),
        baseline_context=baseline,
        profile_id=req.profile_id,
    )
    return result


# ─── Chat with Core ───────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatReq) -> Dict[str, Any]:
    """Simple chat with the Kairós Core (LangChain chain)."""
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

    CORE_SYSTEM = """\
Eres Kairós Core, el copiloto de bienestar digital del usuario.

Tu rol en el check-in diario:
1) Saludas brevemente y preguntas cómo estuvo el día.
2) Haces máximo 3 preguntas, UNA POR TURNO, esperando respuesta entre cada una.
3) Tras recopilar suficiente contexto, sintetizas y das una acción concreta.

Voz: español neutro, cercano, breve. Sin emojis. Sin signos de exclamación.
Una sola pregunta por turno. No prometas diagnósticos clínicos ni integraciones.
{baseline_block}
"""
    baseline_block = ""
    if req.profile_context:
        baseline_block = f"\nContexto del usuario:\n{req.profile_context}"
    elif req.profile_id and req.profile_id in _assessments:
        ctx = profile_context_summary(_assessments[req.profile_id])
        if ctx:
            baseline_block = f"\nContexto del usuario:\n{ctx}"

    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.5,
        max_tokens=400,
        default_headers={
            "HTTP-Referer": "https://kairos.local",
            "X-Title": "Kairos-Core",
        },
    )

    msgs = [("system", CORE_SYSTEM.format(baseline_block=baseline_block))]
    for m in req.messages:
        msgs.append((m.role, m.content))

    prompt = ChatPromptTemplate.from_messages(msgs)
    chain = prompt | llm | StrOutputParser()
    reply = await chain.ainvoke({})
    return {"reply": reply}


# ─── Chrome extension ingest ──────────────────────────────────────────────────

@app.post("/extension/ingest")
async def extension_ingest(req: ExtensionIngestReq) -> Dict[str, Any]:
    """Receive browsing sessions + search queries from Chrome extension."""
    if not req.sessions and not req.queries:
        return {"ok": True, "skipped": True}

    # Compute digital metrics using the scoring engine from digital.py
    metric = compute_daily_metric(req.sessions, req.queries)

    # Store using legacy digital_signals store for summary lookups
    if req.profile_id:
        domains_sec: Dict[str, int] = {}
        for s in req.sessions:
            dom = s.get("domain") or s.get("url", "")
            dur = int(s.get("duration_sec", 0))
            if dom and dur > 0:
                domains_sec[dom] = domains_sec.get(dom, 0) + dur
        ds.ingest({
            "profile_id": req.profile_id,
            "domains": domains_sec,
        })

    return {
        "ok": True,
        "sessions_received": len(req.sessions),
        "queries_received": len(req.queries),
        "metric": metric,
        "llm_summary": metric_summary_for_llm(metric),
    }


@app.get("/digital/summary")
async def digital_summary(profile_id: Optional[str] = None) -> Dict[str, Any]:
    return ds.get_summary(profile_id)


# ─── CV proxy ────────────────────────────────────────────────────────────────

@app.post("/cv/analyze-proxy")
async def cv_proxy(body: Dict[str, Any]) -> Dict[str, Any]:
    """Proxy to cv-service. Accepts JSON with image_b64."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{CV_SERVICE_URL}/cv/analyze/b64",
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"cv-service error: {e}")


@app.post("/cv/analyze-and-predict")
async def cv_analyze_and_predict(body: Dict[str, Any]) -> Dict[str, Any]:
    """CV analysis + ML wellbeing prediction in one call.

    Accepts JSON: { image_b64, profile_id? }
    Returns cv-service output merged with ML risk prediction.
    """
    profile_id: Optional[str] = body.get("profile_id")

    # Forward to cv-service
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{CV_SERVICE_URL}/cv/analyze/b64",
                json={"image_b64": body.get("image_b64", "")},
            )
            resp.raise_for_status()
            cv = resp.json()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"cv-service error: {e}")

    # Build ML feature vector from CV signals
    face = cv.get("face", {})
    gestures = cv.get("gestures", {})
    pose = gestures.get("pose", {})

    # Pull assessment scores if profile exists
    assessment_data = _assessments.get(profile_id or "", [])
    phq9 = next((a["score"] for a in assessment_data if a["kind"] == "phq9"), 0)
    gad7 = next((a["score"] for a in assessment_data if a["kind"] == "gad7"), 0)

    # Digital signals for this profile
    digital = ds.get_summary(profile_id)
    daily_hours = digital.get("today_minutes", 0) / 60.0
    cats = digital.get("today_minutes_by_cat", {})
    total_cat_min = sum(cats.values()) or 1
    social_pct = round(cats.get("social", 0) / total_cat_min * 100, 1)

    # Derive CV-based scores
    avg_ear = 0.0
    if face.get("faces"):
        avg_ear = face["faces"][0].get("eye_openness_ratio", 0.2)
    eye_pct = min(100, round(avg_ear / 0.3 * 100))  # 0.3 EAR = fully open

    stress_signals = pose.get("stress_signals", [])
    posture_ok = pose.get("posture", "sin_datos") == "erguido"

    signals = {
        "phq9_score": phq9,
        "gad7_score": gad7,
        "screen_score": max(0, 100 - daily_hours * 8),  # penalize >12.5 h/day
        "habits_score": 70 if posture_ok else 45,
        "sleep_score": 65,  # no sleep data in lite version
        "energy_score": eye_pct,
        "focus_score": 80 if face.get("distraction_detected") is False else 40,
        "mood_score": 70,
        "daily_screen_hours": round(daily_hours, 2),
        "social_pct": social_pct,
        "drowsiness_count": 1 if face.get("drowsiness_detected") else 0,
        "distraction_count": 1 if face.get("distraction_detected") else 0,
    }

    ml_result = predict_wellbeing(signals)

    # Append stress count to alerts
    alerts = cv.get("wellness_summary", {}).get("wellness_alerts", [])

    return {
        **cv,
        "ml_prediction": {
            **ml_result,
            "feature_vector": signals,
        },
        "cv_risk_summary": {
            "risk_level": ml_result.get("risk_level", "bajo"),
            "confidence": ml_result.get("confidence", 0),
            "alerts": alerts,
            "stress_signals": stress_signals,
            "posture": pose.get("posture", "sin_datos"),
            "eye_openness_pct": eye_pct,
        },
    }


# ─── Recommendations ─────────────────────────────────────────────────────────

@app.post("/recommendations")
async def get_recommendations(req: RecommendReq) -> Dict[str, Any]:
    """Generate LLM-based recommendations from all available signals."""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_core.output_parsers import StrOutputParser
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

    context_parts: List[str] = []
    if req.profile_context:
        context_parts.append(req.profile_context)
    elif req.profile_id and req.profile_id in _assessments:
        ctx = profile_context_summary(_assessments[req.profile_id])
        if ctx:
            context_parts.append(ctx)

    dig = ds.get_summary(req.profile_id)
    if dig.get("session_count", 0) > 0:
        context_parts.append(f"Digital: {json.dumps(dig, ensure_ascii=False)}")

    context = "\n\n".join(context_parts) or "(sin contexto disponible)"

    system_text = (
        f"Eres el Recomendador de Kairós. Propón entre 3 y {req.max_items} recomendaciones accionables para mañana.\n\n"
        "Reglas:\n"
        "- Español neutro, cálido, breve. SIN emojis. SIN signos de exclamación.\n"
        "- No moralices. No diagnostiques. Cada recomendación cita UNA métrica concreta.\n"
        "- Al menos 1 debe ser un hábito accionable (tipo \"habit\").\n"
        "- El resto: tip / warning / reflection.\n\n"
        'Devuelve EXCLUSIVAMENTE este JSON (sin markdown):\n'
        '{"items": [{"kind": "habit", "title": "...", "body": "...", "rationale": "..."}]}\n\n'
        f"CONTEXTO DEL USUARIO:\n{context}"
    )

    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.5,
        max_tokens=800,
        default_headers={
            "HTTP-Referer": "https://kairos.local",
            "X-Title": "Kairos-Recommender",
        },
    )
    messages = [SystemMessage(content=system_text), HumanMessage(content="Genera las recomendaciones ahora.")]
    parser = StrOutputParser()
    raw = parser.invoke(await llm.ainvoke(messages))

    # Parse JSON response
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        if not data.get("items"):
            raise ValueError("empty items")
    except (json.JSONDecodeError, ValueError):
        data = _static_recommendations()

    return data


def _static_recommendations() -> Dict[str, Any]:
    """Fallback recommendations when LLM is unavailable or returns bad JSON."""
    return {
        "items": [
            {
                "kind": "habit",
                "title": "Pausa digital de 5 minutos",
                "body": "Cada 90 minutos de pantalla, tómate 5 minutos sin dispositivos. Reduce la fatiga visual y mejora el foco.",
                "rationale": "Tu tiempo de pantalla acumulado supera el umbral recomendado.",
            },
            {
                "kind": "tip",
                "title": "Revisión de redes en bloque",
                "body": "Agrupa el uso de redes sociales en dos bloques fijos al día en lugar de revisarlas continuamente.",
                "rationale": "El uso fragmentado aumenta el nivel de distracciones hasta un 40%.",
            },
            {
                "kind": "reflection",
                "title": "¿Qué te aportó la pantalla hoy?",
                "body": "Dedica 2 minutos al final del día a evaluar si el tiempo digital fue intencional o reactivo.",
                "rationale": "La conciencia del uso es el primer paso para cambiarlo.",
            },
            {
                "kind": "habit",
                "title": "Sin pantalla 30 min antes de dormir",
                "body": "Apaga dispositivos 30 minutos antes de acostarte para mejorar la calidad del sueño.",
                "rationale": "La luz azul retrasa la producción de melatonina.",
            },
        ]
    }


# ─── Assessments / onboarding ─────────────────────────────────────────────────

@app.get("/onboarding/tests")
async def onboarding_tests() -> Dict[str, Any]:
    return {
        kind: {
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "questions": spec["questions"],
            "options": spec["options"],
            "num_questions": len(spec["questions"]),
        }
        for kind, spec in ASSESSMENTS.items()
    }


@app.post("/onboarding/submit")
async def onboarding_submit(req: AssessmentSubmitReq) -> Dict[str, Any]:
    if req.kind not in ASSESSMENTS:
        raise HTTPException(status_code=400, detail=f"test desconocido: {req.kind}")
    answers = [a.model_dump() for a in req.answers]
    scoring = score_assessment(req.kind, answers)

    row = {
        "kind": req.kind,
        "score": scoring["score"],
        "max_score": scoring["max_score"],
        "severity": scoring["severity"],
        "answers": answers,
    }
    _assessments.setdefault(req.profile_id, [])
    _assessments[req.profile_id] = [
        r for r in _assessments[req.profile_id] if r["kind"] != req.kind
    ]
    _assessments[req.profile_id].append(row)

    return {**scoring, "kind": req.kind, "saved": True}


@app.get("/onboarding/{profile_id}")
async def onboarding_status(profile_id: str) -> Dict[str, Any]:
    results = _assessments.get(profile_id, [])
    return {
        "results": results,
        "context": profile_context_summary(results),
    }


# ─── ML predictor direct endpoint ────────────────────────────────────────────

@app.post("/ml/predict")
async def ml_predict(signals: Dict[str, Any]) -> Dict[str, Any]:
    """Direct access to the wellbeing ML predictor."""
    return predict_wellbeing(signals)


# ─── Auth (in-memory, lite) ───────────────────────────────────────────────────

def _validate_credentials(email: str, password: str) -> None:
    if "@" not in email:
        raise HTTPException(status_code=400, detail="email inválido")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="contraseña debe tener 6+ caracteres")


@app.post("/auth/register")
async def auth_register(req: LoginReq) -> Dict[str, Any]:
    _validate_credentials(req.email, req.password)
    if req.email in _profiles:
        raise HTTPException(status_code=409, detail="Email ya registrado. Inicia sesión.")
    profile = {"id": req.email, "email": req.email, "name": req.name}
    _profiles[req.email] = profile
    return {**profile, "registered": True}


@app.post("/auth/login")
async def auth_login(req: LoginReq) -> Dict[str, Any]:
    _validate_credentials(req.email, req.password)
    if req.email not in _profiles:
        raise HTTPException(status_code=404, detail="No encontramos esa cuenta. Regístrate.")
    return {**_profiles[req.email], "logged_in": True}


@app.post("/auth/mock-login")
async def mock_login(req: LoginReq) -> Dict[str, Any]:
    _validate_credentials(req.email, req.password)
    profile = _profiles.setdefault(req.email, {
        "id": req.email, "email": req.email, "name": req.name,
    })
    return {**profile, "ephemeral": False}


# ─── Diario ──────────────────────────────────────────────────────────────────

@app.post("/diary/entries")
async def diary_add(req: DiaryEntryReq) -> Dict[str, Any]:
    entry = diary.add_entry(
        profile_id=req.profile_id,
        content=req.content,
        title=req.title,
        mood=req.mood,
    )
    return {"ok": True, "entry": entry}


@app.get("/diary/entries")
async def diary_list(profile_id: str, limit: int = 20) -> Dict[str, Any]:
    entries = diary.get_entries(profile_id, limit)
    return {"entries": entries, "count": len(entries)}


# ─── Meditación ───────────────────────────────────────────────────────────────

@app.post("/meditation/log")
async def meditation_log(req: MeditationLogReq) -> Dict[str, Any]:
    if req.duration_minutes <= 0:
        raise HTTPException(status_code=400, detail="duration_minutes debe ser mayor a 0")
    session = meditation.log_session(
        profile_id=req.profile_id,
        duration_minutes=req.duration_minutes,
        session_type=req.session_type,
    )
    return {"ok": True, "session": session}


@app.get("/meditation/stats")
async def meditation_stats(profile_id: str) -> Dict[str, Any]:
    return meditation.get_stats(profile_id)


# ─── Spotify mock ────────────────────────────────────────────────────────────

class SpotifyTrackReq(BaseModel):
    profile_id: str
    track: str
    artist: str
    genre: str = ""


@app.post("/spotify/track")
async def spotify_set_track(req: SpotifyTrackReq) -> Dict[str, Any]:
    record = spotify_tool.set_track(
        profile_id=req.profile_id,
        track=req.track,
        artist=req.artist,
        genre=req.genre,
    )
    return {"ok": True, "track": record}


@app.get("/spotify/track")
async def spotify_get_track(profile_id: str) -> Dict[str, Any]:
    track = spotify_tool.get_track(profile_id)
    return {"track": track}


# ─── Calendario mock ──────────────────────────────────────────────────────────

class CalendarEventReq(BaseModel):
    profile_id: str
    title: str
    time: str
    duration_min: int = 60


@app.post("/calendar/events")
async def calendar_add_event(req: CalendarEventReq) -> Dict[str, Any]:
    event = calendar_tool.add_event(
        profile_id=req.profile_id,
        title=req.title,
        time=req.time,
        duration_min=req.duration_min,
    )
    return {"ok": True, "event": event}


@app.get("/calendar/today")
async def calendar_today(profile_id: str) -> Dict[str, Any]:
    events = calendar_tool.get_today_events(profile_id)
    return {"events": events, "count": len(events)}


# ─── Triage standalone ───────────────────────────────────────────────────────

class TriageReq(BaseModel):
    signals: Dict[str, Any]
    specialist_results: Optional[List[Dict[str, Any]]] = None


@app.post("/checkin/triage")
async def checkin_triage(req: TriageReq) -> Dict[str, Any]:
    """Diagnóstico + derivación standalone, sin correr el check-in completo.

    Útil para llamar directamente con señales ya calculadas (ML signals).
    El campo specialist_results es opcional — sin él el SummaryTool omite las
    lecturas de especialistas en el mensaje al profesional.
    """
    triage = await run_diagnostic_pipeline(
        signals=req.signals,
        specialist_results=req.specialist_results or [],
    )
    return triage


# ─── Salud mock ──────────────────────────────────────────────────────────────

class HealthDataReq(BaseModel):
    profile_id: str
    steps: int = 0
    sleep_hours: float = 0.0
    heart_rate: int = 0


@app.post("/health-data/set")
async def health_set(req: HealthDataReq) -> Dict[str, Any]:
    record = health_tool.set_health(
        profile_id=req.profile_id,
        steps=req.steps,
        sleep_hours=req.sleep_hours,
        heart_rate=req.heart_rate,
    )
    return {"ok": True, "record": record}


@app.get("/health-data/get")
async def health_get(profile_id: str) -> Dict[str, Any]:
    record = health_tool.get_health(profile_id)
    return {"record": record}


# ─── Notion mock ─────────────────────────────────────────────────────────────

class NotionTaskReq(BaseModel):
    profile_id: str
    title: str
    deadline: Optional[str] = None


@app.post("/notion/tasks")
async def notion_add_task(req: NotionTaskReq) -> Dict[str, Any]:
    task = notion_tool.add_task(
        profile_id=req.profile_id,
        title=req.title,
        deadline=req.deadline,
    )
    return {"ok": True, "task": task}


@app.get("/notion/tasks")
async def notion_get_tasks(profile_id: str) -> Dict[str, Any]:
    tasks = notion_tool.get_tasks(profile_id)
    return {"tasks": tasks, "count": len(tasks)}


@app.patch("/notion/tasks/{task_id}/toggle")
async def notion_toggle_task(task_id: str, profile_id: str) -> Dict[str, Any]:
    task = notion_tool.toggle_task(profile_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True, "task": task}


# ─── Strava mock ─────────────────────────────────────────────────────────────

class StravaActivityReq(BaseModel):
    profile_id: str
    sport: str
    duration_min: int
    distance_km: float = 0.0


@app.post("/strava/activity")
async def strava_log_activity(req: StravaActivityReq) -> Dict[str, Any]:
    record = strava_tool.log_activity(
        profile_id=req.profile_id,
        sport=req.sport,
        duration_min=req.duration_min,
        distance_km=req.distance_km,
    )
    return {"ok": True, "activity": record}


@app.get("/strava/activity")
async def strava_get_activity(profile_id: str) -> Dict[str, Any]:
    activity = strava_tool.get_activity(profile_id)
    return {"activity": activity}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
