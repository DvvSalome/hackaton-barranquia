from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL


@lru_cache(maxsize=1)
def db() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def save_agent_signal(
    *,
    agent: str,
    score: Optional[float],
    insight: str,
    signals: List[Dict[str, Any]],
    raw_context: str,
    model: str,
    check_in_id: Optional[str] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "agent": agent,
        "score": score,
        "insight": insight,
        "signals": signals,
        "raw_context": raw_context,
        "model": model,
    }
    if check_in_id:
        row["check_in_id"] = check_in_id
    res = db().table("agent_signals").insert(row).execute()
    return res.data[0] if res.data else {}


def create_check_in(transcript: Optional[List[Dict]] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {"status": "in_progress"}
    if transcript is not None:
        row["transcript"] = transcript
    res = db().table("check_ins").insert(row).execute()
    return res.data[0] if res.data else {}


def complete_check_in(
    check_in_id: str,
    *,
    summary: str,
    insight: Optional[str] = None,
    transcript: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {
        "status": "completed",
        "completed_at": "now()",
        "core_summary": summary,
    }
    if insight:
        patch["core_insight"] = insight
    if transcript is not None:
        patch["transcript"] = transcript
    res = db().table("check_ins").update(patch).eq("id", check_in_id).execute()
    return res.data[0] if res.data else {}


def list_habits() -> List[Dict[str, Any]]:
    res = (
        db()
        .table("habits")
        .select("*")
        .is_("archived_at", "null")
        .order("position")
        .execute()
    )
    return res.data or []


def log_habit(habit_id: str, completed: bool = True, note: Optional[str] = None) -> Dict:
    row = {"habit_id": habit_id, "completed": completed}
    if note:
        row["note"] = note
    res = (
        db()
        .table("habit_logs")
        .upsert(row, on_conflict="habit_id,date")
        .execute()
    )
    return res.data[0] if res.data else {}
