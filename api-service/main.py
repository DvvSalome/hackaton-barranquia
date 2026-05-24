from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db as dbmod
from agents import AGENT_DEFINITIONS, AgentError, run_specialist
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
    save: bool = False


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
    save: bool = False
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
async def checkin_start() -> Dict[str, Any]:
    try:
        row = await asyncio.to_thread(dbmod.create_check_in)
        return {"id": row.get("id"), "row": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"db error: {e}")


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
@app.post("/auth/mock-login")
async def mock_login(req: LoginReq) -> Dict[str, Any]:
    """Acepta cualquier email/contraseña. Solo valida formato mínimo."""
    if "@" not in req.email or "." not in req.email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="email inválido")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="contraseña debe tener 6+ caracteres")
    try:
        profile = await asyncio.to_thread(dbmod.upsert_profile, req.email, req.name)
    except Exception as e:  # noqa: BLE001
        # Si Supabase no tiene la migración aplicada, devolvemos un profile efímero
        # para que el flujo del frontend siga funcionando.
        return {
            "id": None,
            "email": req.email.lower(),
            "name": req.name,
            "ephemeral": True,
            "warn": f"no persistido: {e}",
        }
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
