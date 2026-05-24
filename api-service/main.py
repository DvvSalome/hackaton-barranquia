from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db as dbmod
from agents import (
    AGENT_DEFINITIONS,
    AgentError,
    generate_agent_question,
    run_specialist,
)
from assessments import ASSESSMENTS, profile_context_summary, score_assessment
from core import core_reply, core_synthesis

from digital import compute_daily_metric, metric_summary_for_llm
from recommender import RecommenderError, generate_recommendations

app = FastAPI(title="Kairós api-service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────── Schemas ───────────
class SpecialistReq(BaseModel):
    context: str
    check_in_id: Optional[str] = None
    profile_id: Optional[str] = None
    save: bool = False


class CheckInStartReq(BaseModel):
    profile_id: Optional[str] = None


class NextQuestionReq(BaseModel):
    check_in_id: Optional[str] = None
    profile_id: Optional[str] = None
    agent: str
    position: int = 0
    profile_context: Optional[str] = None
    save: bool = True


class SaveTurnReq(BaseModel):
    check_in_id: Optional[str] = None
    profile_id: Optional[str] = None
    agent: Optional[str] = None
    position: int = 0
    question: Optional[str] = None
    chips: Optional[List[Dict[str, Any]]] = None
    answer: Optional[str] = None
    answer_value: Optional[Dict[str, Any]] = None
    turn_id: Optional[str] = None   # si viene, hace UPDATE


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    messages: List[ChatMessage]
    profile_id: Optional[str] = None
    profile_context: Optional[str] = None


class SynthesizeReq(BaseModel):
    transcript: str
    check_in_id: Optional[str] = None
    save: bool = True   # por defecto guardamos (antes era False)
    profile_id: Optional[str] = None
    profile_context: Optional[str] = None


class LoginReq(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class AssessmentAnswer(BaseModel):
    key: str
    value: int
    label: Optional[str] = None


class AssessmentSubmitReq(BaseModel):
    profile_id: str
    kind: str
    answers: List[AssessmentAnswer]


class HabitLogReq(BaseModel):
    habit_id: str
    completed: bool = True
    note: Optional[str] = None


class BrowsingSession(BaseModel):
    domain: str
    url: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    started_at: str           # ISO 8601
    ended_at: Optional[str] = None
    duration_sec: int
    active: bool = True
    source: Optional[str] = "extension"


class SearchEvent(BaseModel):
    engine: str
    query: str
    ts: str                   # ISO 8601
    source: Optional[str] = "extension"


class ExtensionIngestReq(BaseModel):
    profile_id: Optional[str] = None
    sessions: List[BrowsingSession] = []
    queries: List[SearchEvent] = []
    recompute_today: bool = True


class RecommendReq(BaseModel):
    profile_id: str
    include_chat_excerpt: Optional[str] = None
    max_items: int = 5
    save: bool = True


class RecActionReq(BaseModel):
    status: str               # accepted | dismissed | snoozed


# ─────────── Health & metadata ───────────
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "kairos-api"}


@app.get("/agents")
async def list_agents() -> Dict[str, Any]:
    return AGENT_DEFINITIONS


# ─────────── Specialist endpoint ───────────
@app.post("/agents/{kind}")
async def specialist(kind: str, req: SpecialistReq) -> Dict[str, Any]:
    try:
        out = await run_specialist(kind, req.context)
    except AgentError as e:
        raise HTTPException(status_code=400, detail=str(e))

    out["saved"] = False
    if req.save:
        try:
            saved = await asyncio.to_thread(
                dbmod.save_agent_signal,
                agent=out["agent"],
                score=out["score"],
                insight=out["insight"],
                signals=out["signals"],
                raw_context=req.context,
                model=out["model"],
                check_in_id=req.check_in_id,
                profile_id=req.profile_id,
            )
            out["saved"] = True
            out["row_id"] = saved.get("id")
        except Exception as e:  # noqa: BLE001
            out["saved"] = False
            out["save_error"] = str(e)
    return out


# ─────────── Chat con Core ───────────
@app.post("/chat")
async def chat(req: ChatReq) -> Dict[str, Any]:
    msgs = [m.model_dump() for m in req.messages]
    context = req.profile_context
    if not context and req.profile_id:
        try:
            results = await asyncio.to_thread(dbmod.list_latest_assessments, req.profile_id)
            context = profile_context_summary(results)
        except Exception:  # noqa: BLE001
            context = None
    reply = await core_reply(msgs, profile_context=context)
    return {"reply": reply}


# ─────────── Síntesis end-of-checkin ───────────
@app.post("/checkin/synthesize")
async def synthesize(req: SynthesizeReq) -> Dict[str, Any]:
    """Corre los 6 agentes en paralelo sobre el transcript y sintetiza."""
    kinds = list(AGENT_DEFINITIONS.keys())
    results = await asyncio.gather(
        *[run_specialist(k, req.transcript) for k in kinds],
        return_exceptions=True,
    )

    agents_out: List[Dict[str, Any]] = []
    for k, r in zip(kinds, results):
        if isinstance(r, Exception):
            agents_out.append({"agent": k, "error": str(r)})
        else:
            agents_out.append(r)

    context = req.profile_context
    if not context and req.profile_id:
        try:
            results = await asyncio.to_thread(dbmod.list_latest_assessments, req.profile_id)
            context = profile_context_summary(results)
        except Exception:  # noqa: BLE001
            context = None
    summary = await core_synthesis(agents_out, profile_context=context)

    saved_ids: List[str] = []
    save_error: Optional[str] = None
    if req.save:
        try:
            for r in agents_out:
                if "error" in r:
                    continue
                saved = await asyncio.to_thread(
                    dbmod.save_agent_signal,
                    agent=r["agent"],
                    score=r["score"],
                    insight=r["insight"],
                    signals=r["signals"],
                    raw_context=req.transcript,
                    model=r["model"],
                    check_in_id=req.check_in_id,
                    profile_id=req.profile_id,
                )
                if saved.get("id"):
                    saved_ids.append(saved["id"])
            if req.check_in_id:
                await asyncio.to_thread(
                    dbmod.complete_check_in,
                    req.check_in_id,
                    summary=summary,
                )
        except Exception as e:  # noqa: BLE001
            save_error = str(e)

    return {
        "agents": agents_out,
        "summary": summary,
        "saved_ids": saved_ids,
        "save_error": save_error,
    }


# ─────────── Check-in lifecycle ───────────
@app.post("/checkin/start")
async def checkin_start(req: CheckInStartReq) -> Dict[str, Any]:
    try:
        row = await asyncio.to_thread(dbmod.create_check_in, profile_id=req.profile_id)
        return {"id": row.get("id"), "row": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")


@app.post("/checkin/turn")
async def checkin_turn(req: SaveTurnReq) -> Dict[str, Any]:
    """Guarda o actualiza un turno (pregunta + respuesta) del check-in.
    Si viene turn_id, UPDATE; si no, INSERT (requiere check_in_id + question)."""
    try:
        if req.turn_id:
            if req.answer is None:
                return {"saved": False, "error": "answer requerido para update"}
            row = await asyncio.to_thread(
                dbmod.update_check_in_turn_answer,
                req.turn_id,
                answer=req.answer,
                answer_value=req.answer_value,
            )
        else:
            if not req.check_in_id or not req.question:
                return {"saved": False, "error": "check_in_id y question requeridos"}
            row = await asyncio.to_thread(
                dbmod.save_check_in_turn,
                check_in_id=req.check_in_id,
                profile_id=req.profile_id,
                agent=req.agent,
                position=req.position,
                question=req.question,
                chips=req.chips,
                answer=req.answer,
                answer_value=req.answer_value,
            )
        return {"saved": True, "id": row.get("id"), "row": row}
    except Exception as e:  # noqa: BLE001
        return {"saved": False, "error": str(e)}


@app.post("/checkin/next-question")
async def checkin_next_question(req: NextQuestionReq) -> Dict[str, Any]:
    """Genera la siguiente pregunta dinámica para un agente, basándose en
    baseline + historial real del usuario."""
    if req.agent not in AGENT_DEFINITIONS:
        raise HTTPException(status_code=400, detail=f"agente desconocido: {req.agent}")

    baseline = req.profile_context
    recent_turns: List[Dict[str, Any]] = []
    recent_signals: List[Dict[str, Any]] = []

    if req.profile_id:
        if not baseline:
            try:
                results = await asyncio.to_thread(
                    dbmod.list_latest_assessments, req.profile_id
                )
                baseline = profile_context_summary(results)
            except Exception:  # noqa: BLE001
                baseline = None
        try:
            recent_turns = await asyncio.to_thread(
                dbmod.list_recent_agent_turns, req.profile_id, req.agent, 6
            )
        except Exception:  # noqa: BLE001
            recent_turns = []
        try:
            recent_signals = await asyncio.to_thread(
                dbmod.list_recent_agent_signals, req.profile_id, req.agent, 3
            )
        except Exception:  # noqa: BLE001
            recent_signals = []

    try:
        out = await generate_agent_question(
            req.agent,
            baseline=baseline,
            recent_turns=recent_turns,
            recent_signals=recent_signals,
        )
    except AgentError as e:
        raise HTTPException(status_code=502, detail=str(e))

    out["saved_turn"] = None
    if req.save and req.check_in_id:
        try:
            saved = await asyncio.to_thread(
                dbmod.save_check_in_turn,
                check_in_id=req.check_in_id,
                profile_id=req.profile_id,
                agent=req.agent,
                position=req.position,
                question=out["question"],
                chips=out["chips"],
            )
            out["saved_turn"] = saved.get("id")
        except Exception as e:  # noqa: BLE001
            out["save_error"] = str(e)
    return out


# ─────────── Habits ───────────
@app.get("/habits")
async def get_habits() -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(dbmod.list_habits)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")


@app.post("/habits/log")
async def post_habit_log(req: HabitLogReq) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            dbmod.log_habit, req.habit_id, req.completed, req.note
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")


# ─────────── Auth mock + onboarding ───────────
def _validate_credentials(email: str, password: str) -> None:
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="email inválido")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="contraseña debe tener 6+ caracteres")


def _ephemeral_profile(email: str, name: Optional[str], reason: str) -> Dict[str, Any]:
    return {
        "id": None,
        "email": email.lower(),
        "name": name,
        "ephemeral": True,
        "warn": f"no persistido: {reason}",
    }


@app.post("/auth/register")
async def auth_register(req: LoginReq) -> Dict[str, Any]:
    """Crea un profile nuevo. 409 si el email ya existe."""
    _validate_credentials(req.email, req.password)
    try:
        existing = await asyncio.to_thread(dbmod.get_profile_by_email, req.email)
    except Exception as e:  # noqa: BLE001
        return _ephemeral_profile(req.email, req.name, str(e))
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Este email ya está registrado. Inicia sesión.",
        )
    try:
        profile = await asyncio.to_thread(dbmod.create_profile, req.email, req.name)
    except Exception as e:  # noqa: BLE001
        return _ephemeral_profile(req.email, req.name, str(e))
    return {**profile, "ephemeral": False, "registered": True}


@app.post("/auth/login")
async def auth_login(req: LoginReq) -> Dict[str, Any]:
    """Inicia sesión. 404 si el email no existe."""
    _validate_credentials(req.email, req.password)
    try:
        profile = await asyncio.to_thread(dbmod.get_profile_by_email, req.email)
    except Exception as e:  # noqa: BLE001
        return _ephemeral_profile(req.email, req.name, str(e))
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="No encontramos una cuenta con ese email. Regístrate primero.",
        )
    try:
        await asyncio.to_thread(dbmod.touch_profile, profile["id"])
    except Exception:  # noqa: BLE001
        pass
    return {**profile, "ephemeral": False}


@app.post("/auth/mock-login")
async def mock_login(req: LoginReq) -> Dict[str, Any]:
    """Compat: acepta cualquier email/contraseña (upsert)."""
    _validate_credentials(req.email, req.password)
    try:
        profile = await asyncio.to_thread(dbmod.upsert_profile, req.email, req.name)
    except Exception as e:  # noqa: BLE001
        return _ephemeral_profile(req.email, req.name, str(e))
    return {**profile, "ephemeral": False}


@app.get("/onboarding/tests")
async def onboarding_tests() -> Dict[str, Any]:
    """Devuelve la definición de los 4 tests para que el frontend los pinte."""
    return {
        kind: {
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "prompt": spec["prompt"],
            "options": spec["options"],
            "questions": spec["questions"],
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
    saved: Dict[str, Any] = {"saved": False}
    try:
        row = await asyncio.to_thread(
            dbmod.save_assessment,
            req.profile_id,
            kind=req.kind,
            score=scoring["score"],
            max_score=scoring["max_score"],
            severity=scoring["severity"],
            answers=answers,
        )
        saved = {"saved": True, "id": row.get("id")}
    except Exception as e:  # noqa: BLE001
        saved = {"saved": False, "error": str(e)}
    return {**scoring, **saved, "kind": req.kind}


@app.get("/onboarding/{profile_id}")
async def onboarding_status(profile_id: str) -> Dict[str, Any]:
    try:
        results = await asyncio.to_thread(dbmod.list_latest_assessments, profile_id)
    except Exception as e:  # noqa: BLE001
        return {"results": [], "context": None, "error": str(e)}
    return {
        "results": results,
        "context": profile_context_summary(results),
    }


# ─────────── Extensión: ingest + métricas digitales ───────────
def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _recompute_digital_metric(profile_id: str, day_iso: str) -> Dict[str, Any]:
    sessions = await asyncio.to_thread(dbmod.fetch_sessions_for_day, profile_id, day_iso)
    queries = await asyncio.to_thread(dbmod.fetch_queries_for_day, profile_id, day_iso)
    metric = compute_daily_metric(sessions, queries)
    await asyncio.to_thread(dbmod.upsert_digital_metric, profile_id, day_iso, metric)
    return metric


@app.post("/extension/ingest")
async def extension_ingest(req: ExtensionIngestReq) -> Dict[str, Any]:
    """La extensión empuja sesiones de navegación + búsquedas en lote.
    Si hay profile_id, además recalculamos la métrica diaria."""
    try:
        sessions_payload = [s.model_dump() for s in req.sessions]
        queries_payload = [q.model_dump() for q in req.queries]
        n_sessions = await asyncio.to_thread(
            dbmod.insert_browsing_sessions, req.profile_id, sessions_payload
        )
        n_queries = await asyncio.to_thread(
            dbmod.insert_search_queries, req.profile_id, queries_payload
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "saved_sessions": 0,
            "saved_queries": 0,
            "error": str(e),
        }

    metric: Optional[Dict[str, Any]] = None
    metric_error: Optional[str] = None
    if req.profile_id and req.recompute_today:
        try:
            metric = await _recompute_digital_metric(req.profile_id, _today_iso())
        except Exception as e:  # noqa: BLE001
            metric_error = str(e)

    return {
        "ok": True,
        "saved_sessions": n_sessions,
        "saved_queries": n_queries,
        "metric": metric,
        "metric_error": metric_error,
    }


@app.get("/digital/{profile_id}/today")
async def digital_today(profile_id: str) -> Dict[str, Any]:
    try:
        metric = await asyncio.to_thread(dbmod.get_latest_digital_metric, profile_id)
    except Exception as e:  # noqa: BLE001
        return {"metric": None, "error": str(e)}
    return {"metric": metric}


@app.post("/digital/{profile_id}/recompute")
async def digital_recompute(profile_id: str) -> Dict[str, Any]:
    try:
        metric = await _recompute_digital_metric(profile_id, _today_iso())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"recompute: {e}")
    return {"ok": True, "metric": metric}


@app.get("/digital/{profile_id}/history")
async def digital_history(profile_id: str, days: int = 14) -> Dict[str, Any]:
    try:
        history = await asyncio.to_thread(
            dbmod.list_digital_metrics, profile_id, max(1, min(60, days))
        )
    except Exception as e:  # noqa: BLE001
        return {"history": [], "error": str(e)}
    return {"history": history}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _local_day_bounds(tz_offset_min: int, days_ago: int = 0) -> tuple:
    """JS getTimezoneOffset: UTC - local. Colombia/Mac suele ser 300."""
    now_local = datetime.now(timezone.utc) - timedelta(minutes=tz_offset_min)
    target_date = now_local.date() - timedelta(days=days_ago)
    local_midnight = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    start_utc = local_midnight + timedelta(minutes=tz_offset_min)
    return start_utc, start_utc + timedelta(days=1), target_date.isoformat()


def _session_minutes(session: Dict[str, Any]) -> int:
    seconds = int(session.get("duration_sec") or 0)
    if session.get("active") is False:
        seconds = seconds // 2
    return max(0, round(seconds / 60))


def _hourly_buckets(
    sessions: List[Dict[str, Any]],
    day_iso: str,
    tz_offset_min: int,
) -> List[Dict[str, Any]]:
    local_day = datetime.fromisoformat(day_iso).date()
    bucket_hours = list(range(8, 23))
    seconds_by_hour = {h: 0.0 for h in bucket_hours}

    for s in sessions:
        start_utc = _parse_dt(s.get("started_at"))
        if not start_utc:
            continue
        end_utc = _parse_dt(s.get("ended_at"))
        if not end_utc:
            end_utc = start_utc + timedelta(seconds=int(s.get("duration_sec") or 0))
        if end_utc <= start_utc:
            continue
        # Convertimos a "hora local" usando el offset del navegador, pero
        # mantenemos timezone UTC para poder comparar datetimes sin mezclar tz.
        start_local = start_utc - timedelta(minutes=tz_offset_min)
        end_local = end_utc - timedelta(minutes=tz_offset_min)
        weight = 0.5 if s.get("active") is False else 1.0

        for hour in bucket_hours:
            b0 = datetime.combine(local_day, time(hour), tzinfo=timezone.utc)
            b1 = b0 + timedelta(hours=1)
            overlap = max(0.0, (min(end_local, b1) - max(start_local, b0)).total_seconds())
            if overlap:
                seconds_by_hour[hour] += overlap * weight

    max_minutes = max(60, *(round(v / 60) for v in seconds_by_hour.values()))
    return [
        {
            "hour": h,
            "label": f"{h}h",
            "minutes": round(seconds_by_hour[h] / 60),
            "ratio": round(min(1.0, (seconds_by_hour[h] / 60) / max_minutes), 3),
        }
        for h in bucket_hours
    ]


def _screen_dashboard_payload(
    today_sessions: List[Dict[str, Any]],
    yesterday_sessions: List[Dict[str, Any]],
    day_iso: str,
    tz_offset_min: int,
    target_minutes: int,
) -> Dict[str, Any]:
    total_today = sum(_session_minutes(s) for s in today_sessions)
    total_yesterday = sum(_session_minutes(s) for s in yesterday_sessions)
    delta = total_today - total_yesterday
    domain_minutes: Counter = Counter()
    for s in today_sessions:
        domain = s.get("domain") or "otro"
        domain_minutes[domain] += _session_minutes(s)
    top_domains = [
        {"domain": d, "minutes": m} for d, m in domain_minutes.most_common(5) if m > 0
    ]
    if total_today <= target_minutes:
        status = "Vas por buen camino"
    elif total_today <= target_minutes + 30:
        status = "Cerca del objetivo"
    else:
        status = "Conviene bajar el ritmo"
    return {
        "date": day_iso,
        "target_minutes": target_minutes,
        "total_minutes": total_today,
        "delta_minutes": delta,
        "delta_label": (
            "igual que ayer"
            if delta == 0
            else f"{abs(delta)}m {'más' if delta > 0 else 'menos'} que ayer"
        ),
        "delta_direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
        "status": status,
        "hourly": _hourly_buckets(today_sessions, day_iso, tz_offset_min),
        "top_domains": top_domains,
    }


@app.get("/digital/{profile_id}/dashboard")
async def digital_dashboard(
    profile_id: str,
    tz_offset_min: int = 0,
    target_minutes: int = 240,
) -> Dict[str, Any]:
    """Resumen para la tarjeta 'Pantalla hoy' del dashboard.

    Usa sesiones crudas de la extensión para total del día local, delta vs ayer
    y buckets 8h-22h. `tz_offset_min` viene de JS getTimezoneOffset().
    """
    start_today, end_today, day_iso = _local_day_bounds(tz_offset_min)
    start_yesterday, _, _ = _local_day_bounds(tz_offset_min, days_ago=1)
    try:
        today_sessions, yesterday_sessions = await asyncio.gather(
            asyncio.to_thread(
                dbmod.fetch_sessions_between,
                profile_id,
                start_today.isoformat(),
                end_today.isoformat(),
            ),
            asyncio.to_thread(
                dbmod.fetch_sessions_between,
                profile_id,
                start_yesterday.isoformat(),
                start_today.isoformat(),
            ),
        )
    except Exception as e:  # noqa: BLE001
        return {"screen": None, "error": str(e)}
    return {
        "screen": _screen_dashboard_payload(
            today_sessions,
            yesterday_sessions,
            day_iso,
            tz_offset_min,
            max(1, min(1440, target_minutes)),
        )
    }


# ─────────── Recomendaciones generadas por LLM ───────────
@app.post("/recommendations/generate")
async def recommendations_generate(req: RecommendReq) -> Dict[str, Any]:
    """Junta todo el contexto del usuario y pide al LLM 3-5 recomendaciones."""

    async def _safe(coro):
        try:
            return await coro
        except Exception:  # noqa: BLE001
            return None

    assessments = (
        await _safe(asyncio.to_thread(dbmod.list_latest_assessments, req.profile_id))
        or []
    )
    profile_summary = profile_context_summary(assessments) if assessments else None

    metric_row = await _safe(
        asyncio.to_thread(dbmod.get_latest_digital_metric, req.profile_id)
    )
    digital_summary = None
    metric_for_prompt = None
    if metric_row:
        metric_for_prompt = {
            "minutes_by_category": {
                "social": metric_row.get("minutes_social", 0),
                "entertainment": metric_row.get("minutes_entertainment", 0),
                "news": metric_row.get("minutes_news", 0),
                "work": metric_row.get("minutes_work", 0),
                "education": metric_row.get("minutes_education", 0),
                "shopping": metric_row.get("minutes_shopping", 0),
                "search": metric_row.get("minutes_search", 0),
                "ai": metric_row.get("minutes_ai", 0),
                "other": metric_row.get("minutes_other", 0),
            },
            "scores": {
                "social": metric_row.get("score_social"),
                "focus": metric_row.get("score_focus"),
                "balance": metric_row.get("score_balance"),
                "digital_overall": metric_row.get("score_digital_overall"),
            },
            "top_domains": metric_row.get("top_domains") or [],
            "search_themes": metric_row.get("search_themes") or [],
            "total_minutes": sum(
                metric_row.get(k, 0) or 0
                for k in [
                    "minutes_social", "minutes_entertainment", "minutes_news",
                    "minutes_work", "minutes_education", "minutes_shopping",
                    "minutes_search", "minutes_ai", "minutes_other",
                ]
            ),
        }
        digital_summary = metric_summary_for_llm(metric_for_prompt)

    signals = (
        await _safe(
            asyncio.to_thread(dbmod.list_recent_agent_signals, req.profile_id, None, 8)
        )
        or []
    )
    habits = await _safe(asyncio.to_thread(dbmod.list_habits)) or []

    try:
        out = await generate_recommendations(
            profile_summary=profile_summary,
            digital_summary=digital_summary,
            agent_signals=signals,
            habits=habits,
            recent_chat_excerpt=req.include_chat_excerpt,
            max_items=req.max_items,
        )
    except RecommenderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    saved_rows: List[Dict[str, Any]] = []
    save_error: Optional[str] = None
    if req.save:
        try:
            saved_rows = await asyncio.to_thread(
                dbmod.insert_recommendations,
                req.profile_id,
                out["items"],
                out["model"],
                {
                    "digital": metric_for_prompt,
                    "agent_signals": signals[:5],
                    "assessments_summary": profile_summary,
                },
            )
        except Exception as e:  # noqa: BLE001
            save_error = str(e)

    return {
        "items": out["items"],
        "model": out["model"],
        "saved": saved_rows,
        "save_error": save_error,
    }


@app.get("/recommendations/{profile_id}")
async def recommendations_list(
    profile_id: str, status: Optional[str] = None
) -> Dict[str, Any]:
    try:
        rows = await asyncio.to_thread(
            dbmod.list_recommendations, profile_id, status, 30
        )
    except Exception as e:  # noqa: BLE001
        return {"items": [], "error": str(e)}
    return {"items": rows}


@app.post("/recommendations/{rec_id}/action")
async def recommendation_action(rec_id: str, req: RecActionReq) -> Dict[str, Any]:
    valid = {"accepted", "dismissed", "snoozed"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"status inválido: {req.status}")
    try:
        row = await asyncio.to_thread(
            dbmod.update_recommendation_status, rec_id, req.status
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "row": row}


@app.post("/recommendations/{rec_id}/accept-habit")
async def recommendation_accept_habit(rec_id: str) -> Dict[str, Any]:
    """Crea un hábito a partir de la propuesta de la recomendación y la marca aceptada."""
    try:
        rec_rows = await asyncio.to_thread(
            lambda: dbmod.db()
            .table("recommendations")
            .select("*")
            .eq("id", rec_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")
    if not rec_rows:
        raise HTTPException(status_code=404, detail="recomendación no encontrada")
    rec = rec_rows[0]
    proposal = rec.get("habit_proposal") or {}
    if rec.get("kind") != "habit" or not proposal:
        raise HTTPException(
            status_code=400, detail="esta recomendación no es un hábito"
        )
    try:
        habit = await asyncio.to_thread(
            dbmod.create_habit_from_proposal, rec["profile_id"], proposal
        )
        await asyncio.to_thread(
            dbmod.update_recommendation_status,
            rec_id,
            "accepted",
            habit.get("id"),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")
    return {"ok": True, "habit": habit, "recommendation_id": rec_id}
