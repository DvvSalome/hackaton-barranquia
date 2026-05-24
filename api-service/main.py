"""Kairós API Service — FastAPI app (LangChain edition, lite version).

Services wired:
  /health                          — liveness
  /agents                          — list specialist definitions
  /checkin/synthesize              — all specialists + ML + orchestrator
  /checkin/triage                  — triage pipeline standalone
  /chat                            — core chat (LangChain)
  /extension/ingest                — Chrome extension digital data
  /digital/summary                 — aggregated digital signals
  /cv/analyze-proxy                — proxy to cv-service
  /cv/analyze-and-predict          — CV + ML prediction combined
  /onboarding/tests                — assessment definitions
  /onboarding/submit               — submit and score an assessment
  /onboarding/{profile_id}         — profile assessment context
  /auth/register | /auth/login     — auth (in-memory)
  /recommendations                 — LLM-based recommendations
  /diary/entries                   — diary CRUD
  /meditation/log | /stats         — meditation sessions
  /spotify/track                   — current track (mock)
  /calendar/events | /today        — calendar events (mock)
  /health-data/set | /get          — health metrics (mock)
  /notion/tasks | /{id}/toggle     — Notion task list (mock)
  /strava/activity                 — last activity (mock)
  /ml/predict                      — ML wellbeing predictor
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
import triage_history as th

# Supabase persistence — optional, fails gracefully if not installed or misconfigured
try:
    import db as _db
    _DB_AVAILABLE = True
except Exception:
    _db = None
    _DB_AVAILABLE = False

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


# ─── Dynamic agent question generator ────────────────────────────────────────

@app.post("/agents/{kind}/question")
async def agent_question(kind: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate a dynamic, contextual opening question from a specialist agent."""
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

    spec = SPECIALIST_DEFINITIONS.get(kind)
    if not spec:
        raise HTTPException(status_code=404, detail=f"agente '{kind}' no encontrado")

    context = (body or {}).get("context", "")

    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.7,
        max_tokens=80,
        default_headers={"HTTP-Referer": "https://kairos.local", "X-Title": "Kairos-Question"},
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            f"Eres {spec['name']}, agente especialista de Kairós.\n"
            f"Tu foco: {spec['focus']}.\n"
            "Genera UNA pregunta de check-in para el usuario. Reglas estrictas:\n"
            "- Máximo 12 palabras\n"
            "- Español neutro, tono cálido pero directo\n"
            "- Sin emojis, sin signos de exclamación\n"
            "- Varía respecto a '¿cómo te sientes?'. Sé específico a tu foco.\n"
            "Responde SOLO con la pregunta. Sin prefijos, sin explicaciones."
        )),
        ("human", context if context else "Primera vez del día, sin contexto previo."),
    ])
    chain = prompt | llm | StrOutputParser()
    question = await chain.ainvoke({})
    return {"question": question.strip().rstrip(".")}


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

    # Persist check-in to Supabase (non-blocking, best-effort)
    if _DB_AVAILABLE and req.profile_id:
        try:
            # Resolve UUID for profile (check_ins.profile_id is UUID type)
            sb_pid: Optional[str] = None
            try:
                sb_profile = _db.get_profile_by_email(req.profile_id) or \
                             _db.upsert_profile(req.profile_id)
                sb_pid = sb_profile.get("id")
            except Exception:
                pass

            check_in = _db.create_check_in(
                profile_id=sb_pid,
                transcript=[{"role": "user", "content": req.transcript}],
            )
            cid = check_in.get("id")
            if cid:
                for r in result.get("specialists", []):
                    try:
                        _db.save_agent_signal(
                            agent=r.get("agent", r.get("name", "unknown")),
                            score=r.get("score"),
                            insight=r.get("insight", ""),
                            signals=[],
                            raw_context=req.transcript[:500],
                            model="synthesize",
                            check_in_id=cid,
                            profile_id=sb_pid,
                        )
                    except Exception:
                        pass
                _db.complete_check_in(
                    cid,
                    summary=result.get("summary", ""),
                    insight=result.get("diagnosis", {}).get("pattern", ""),
                )
        except Exception:
            pass  # Supabase errors never break the endpoint

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
    profile: Dict[str, Any] = {"id": req.email, "email": req.email, "name": req.name}
    # Persist to Supabase if available
    if _DB_AVAILABLE:
        try:
            sb = _db.upsert_profile(req.email, req.name)
            if sb.get("id"):
                profile["id"] = sb["id"]
        except Exception:
            pass
    _profiles[req.email] = {**profile, "_pw": req.password}
    return {**profile, "registered": True}


@app.post("/auth/login")
async def auth_login(req: LoginReq) -> Dict[str, Any]:
    _validate_credentials(req.email, req.password)
    local = _profiles.get(req.email)
    if local:
        if local.get("_pw") and local["_pw"] != req.password:
            raise HTTPException(status_code=401, detail="Contraseña incorrecta.")
        return {k: v for k, v in local.items() if k != "_pw"} | {"logged_in": True}
    # Server restart: look up existing profile in Supabase (password not re-verified)
    if _DB_AVAILABLE:
        try:
            sb = _db.get_profile_by_email(req.email)
            if sb:
                profile: Dict[str, Any] = {
                    "id": sb.get("id", req.email),
                    "email": req.email,
                    "name": sb.get("name"),
                }
                _profiles[req.email] = {**profile, "_pw": req.password}
                return {**profile, "logged_in": True}
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="No encontramos esa cuenta. Regístrate.")


@app.post("/auth/mock-login")
async def mock_login(req: LoginReq) -> Dict[str, Any]:
    _validate_credentials(req.email, req.password)
    if req.email not in _profiles:
        profile: Dict[str, Any] = {"id": req.email, "email": req.email, "name": req.name}
        if _DB_AVAILABLE:
            try:
                sb = _db.upsert_profile(req.email, req.name)
                if sb.get("id"):
                    profile["id"] = sb["id"]
            except Exception:
                pass
        _profiles[req.email] = {**profile, "_pw": req.password}
    return {k: v for k, v in _profiles[req.email].items() if k != "_pw"} | {"ephemeral": False}


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


# ─── Triage history ───────────────────────────────────────────

@app.get("/history/triage/{profile_id}")
async def history_triage(profile_id: str, limit: int = 10) -> Dict[str, Any]:
    """Return triage history + trend for a given profile."""
    entries = th.get_history(profile_id, limit)
    trend = th.get_trend(profile_id)
    return {"entries": entries, "count": len(entries), "trend": trend}


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


# ─── /api/v1/* compatibility layer for Next.js frontend ──────────────────────

_habits_store: Dict[str, List[Dict[str, Any]]] = {}


@app.get("/api/v1/dashboard")
async def v1_dashboard(profile_id: Optional[str] = None) -> Dict[str, Any]:
    dig = ds.get_summary(profile_id)
    assessments = _assessments.get(profile_id or "", [])
    phq9 = next((a["score"] for a in assessments if a["kind"] == "phq9"), None)
    gad7 = next((a["score"] for a in assessments if a["kind"] == "gad7"), None)
    habits = _habits_store.get(profile_id or "", [])
    return {
        "today_usage_min": dig.get("today_minutes", 0),
        "active_habits": len([h for h in habits if h.get("active", True)]),
        "total_habit_completions_today": sum(1 for h in habits if h.get("completed_today")),
        "top_domains": [
            {"domain": d, "minutes": round(s * 60)}
            for d, s in (dig.get("top_domains_hours") or {}).items()
        ][:5],
        "last_phq9_score": phq9,
        "last_phq9_date": None,
        "last_gad7_score": gad7,
        "last_gad7_date": None,
        "last_survey_date": None,
        "onboarding_completed": len(assessments) > 0,
        "ml_risk_level": "low",
        "ml_anomaly_score": 0.1,
        "ml_phq9_direction": "stable",
        "ml_confidence": 0.85,
    }


@app.get("/api/v1/dashboard/weekly-usage")
async def v1_weekly_usage(profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    import datetime
    today = datetime.date.today()
    days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    base = [45, 80, 120, 60, 95, 150, 110]
    result = []
    for i in range(7):
        d = today - datetime.timedelta(days=6 - i)
        result.append({
            "day": d.isoformat(),
            "label": days[d.weekday()],
            "minutes": base[i],
        })
    return result


@app.get("/api/v1/habits")
async def v1_get_habits(profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _habits_store.get(profile_id or "", [])


@app.post("/api/v1/habits")
async def v1_create_habit(body: Dict[str, Any]) -> Dict[str, Any]:
    import uuid
    profile_id = body.get("profile_id", "")
    habit = {
        "id": str(uuid.uuid4()),
        "name": body.get("name", ""),
        "playbook_slug": None,
        "frequency": body.get("frequency", "daily"),
        "active": True,
        "current_streak": 0,
        "completed_today": False,
    }
    _habits_store.setdefault(profile_id, []).append(habit)
    return habit


@app.post("/api/v1/habits/{habit_id}/complete")
async def v1_complete_habit(habit_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    for habits in _habits_store.values():
        for h in habits:
            if h["id"] == habit_id:
                h["completed_today"] = True
                h["current_streak"] = h.get("current_streak", 0) + 1
                return {"streak": h["current_streak"], "message": "Hábito completado"}
    return {"streak": 0, "message": "ok"}


@app.post("/api/v1/ml/run")
async def v1_ml_run(profile_id: Optional[str] = None) -> Dict[str, Any]:
    dig = ds.get_summary(profile_id)
    signals = {
        "phq9_score": 0, "gad7_score": 0,
        "screen_score": max(0, 100 - dig.get("today_minutes", 0) / 60 * 8),
        "habits_score": 70, "sleep_score": 65, "energy_score": 75,
        "focus_score": 70, "mood_score": 70,
        "daily_screen_hours": round(dig.get("today_minutes", 0) / 60, 2),
        "social_pct": 0, "drowsiness_count": 0, "distraction_count": 0,
    }
    return predict_wellbeing(signals)


@app.get("/api/v1/report")
async def v1_report(profile_id: Optional[str] = None) -> Dict[str, Any]:
    import datetime
    dig = ds.get_summary(profile_id)
    assessments = _assessments.get(profile_id or "", [])
    phq9_scores = [{"date": datetime.date.today().isoformat(), "score": a["score"], "survey_type": "phq9"}
                   for a in assessments if a["kind"] == "phq9"]
    gad7_scores = [{"date": datetime.date.today().isoformat(), "score": a["score"], "survey_type": "gad7"}
                   for a in assessments if a["kind"] == "gad7"]
    return {
        "wellness_score": 72,
        "wellness_label": "Bueno",
        "wellness_color": "#4FFFB0",
        "phq9_history": phq9_scores,
        "gad7_history": gad7_scores,
        "ml_history": [],
        "avg_screen_time_min": dig.get("today_minutes", 0),
        "screen_time_trend": "estable",
        "habits_completed_30d": 18,
        "habits_total_possible": 30,
        "top_domains": [
            {"domain": d, "minutes": round(s * 60)}
            for d, s in (dig.get("top_domains_hours") or {}).items()
        ][:5],
        "risk_flags": [],
        "positive_signals": ["Tiempo en trabajo superior al promedio"],
        "report_date": datetime.date.today().isoformat(),
    }


@app.post("/api/v1/surveys/{survey_type}")
async def v1_survey(survey_type: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "type": survey_type, "saved": True}


@app.post("/api/v1/demo/seed")
async def v1_demo_seed(profile_id: str = "demo") -> Dict[str, Any]:
    """Seed realistic demo data for all tools so the dashboard looks live on first load."""
    # Health metrics
    health_tool.set_health(profile_id, steps=7842, sleep_hours=7.2, heart_rate=62)

    # Meditation sessions (3 sessions)
    for mins in [10, 15, 10]:
        meditation.log_session(profile_id, duration_minutes=mins)

    # Diary entries
    diary.add_entry(profile_id, "Mañana tranquila. Me siento con energía después del café.", title="Inicio del día", mood="bien")
    diary.add_entry(profile_id, "Tarde un poco cargada. Varias reuniones seguidas. Necesito pausas.", title="Tarde intensa", mood="neutral")
    diary.add_entry(profile_id, "Buena sesión de trabajo enfocado esta mañana. Logré avanzar en el proyecto.", title="Foco matutino", mood="bien")

    # Spotify track
    spotify_tool.set_track(profile_id, track="Lo-Fi Hip Hop Radio", artist="ChilledCow", genre="Lo-Fi")

    # Calendar events
    calendar_tool.add_event(profile_id, title="Stand-up equipo", time="9:00", duration_min=30)
    calendar_tool.add_event(profile_id, title="Bloque de foco profundo", time="10:00", duration_min=90)
    calendar_tool.add_event(profile_id, title="Revisión de sprint", time="15:00", duration_min=60)

    # Notion tasks
    notion_tool.add_task(profile_id, title="Finalizar análisis de datos Q2", deadline="2026-05-30")
    notion_tool.add_task(profile_id, title="Preparar presentación para el cliente", deadline="2026-05-28")
    notion_tool.add_task(profile_id, title="Revisar propuesta de arquitectura", deadline="2026-06-01")
    # Toggle first task as done
    tasks = notion_tool.get_tasks(profile_id)
    if tasks:
        notion_tool.toggle_task(profile_id, tasks[0]["id"])

    # Strava activity
    strava_tool.log_activity(profile_id, sport="Carrera", duration_min=35, distance_km=5.2)

    return {
        "ok": True,
        "profile_id": profile_id,
        "seeded": {
            "health": True,
            "meditation": 3,
            "diary": 3,
            "spotify": True,
            "calendar": 3,
            "notion": 3,
            "strava": True,
        },
    }


@app.delete("/api/v1/demo/reset")
async def v1_demo_reset(profile_id: str = "demo") -> Dict[str, Any]:
    """Clear all demo data for a profile (in-memory only)."""
    # Each store's internal dicts — clear just the demo profile
    for store_dict in [
        getattr(health_tool, '_records', {}),
        getattr(meditation, '_sessions', {}),
        getattr(diary, '_entries', {}),
        getattr(spotify_tool, '_tracks', {}),
        getattr(calendar_tool, '_events', {}),
        getattr(notion_tool, '_tasks', {}),
        getattr(strava_tool, '_activities', {}),
    ]:
        store_dict.pop(profile_id, None)
    return {"ok": True, "profile_id": profile_id}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
