from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db as dbmod
from agents import AGENT_DEFINITIONS, AgentError, run_specialist
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


class SynthesizeReq(BaseModel):
    transcript: str
    check_in_id: Optional[str] = None
    save: bool = False


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
    reply = await core_reply(msgs)
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

    summary = await core_synthesis(agents_out)

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
