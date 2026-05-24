from __future__ import annotations

import asyncio
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
