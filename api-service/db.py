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
    profile_id: Optional[str] = None,
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
    if profile_id:
        row["profile_id"] = profile_id
    res = db().table("agent_signals").insert(row).execute()
    return res.data[0] if res.data else {}


def create_check_in(
    profile_id: Optional[str] = None,
    transcript: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {"status": "in_progress"}
    if transcript is not None:
        row["transcript"] = transcript
    if profile_id:
        row["profile_id"] = profile_id
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


# ────────────── Check-in turns (Q&A dinámicas) ──────────────
def save_check_in_turn(
    *,
    check_in_id: str,
    profile_id: Optional[str],
    agent: Optional[str],
    position: int,
    question: str,
    chips: Optional[List[Dict[str, Any]]] = None,
    answer: Optional[str] = None,
    answer_value: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "check_in_id": check_in_id,
        "position": position,
        "question": question,
    }
    if profile_id:
        row["profile_id"] = profile_id
    if agent:
        row["agent"] = agent
    if chips is not None:
        row["chips"] = chips
    if answer is not None:
        row["answer"] = answer
        row["answered_at"] = "now()"
    if answer_value is not None:
        row["answer_value"] = answer_value
    res = db().table("check_in_turns").insert(row).execute()
    return res.data[0] if res.data else {}


def update_check_in_turn_answer(
    turn_id: str,
    *,
    answer: str,
    answer_value: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {"answer": answer, "answered_at": "now()"}
    if answer_value is not None:
        patch["answer_value"] = answer_value
    res = db().table("check_in_turns").update(patch).eq("id", turn_id).execute()
    return res.data[0] if res.data else {}


def list_recent_agent_turns(
    profile_id: str,
    agent: Optional[str] = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Últimos turnos respondidos del usuario (opcionalmente filtrados por agente)."""
    q = (
        db()
        .table("check_in_turns")
        .select("agent,question,answer,answer_value,asked_at")
        .eq("profile_id", profile_id)
        .not_.is_("answered_at", "null")
        .order("asked_at", desc=True)
        .limit(limit)
    )
    if agent:
        q = q.eq("agent", agent)
    res = q.execute()
    return res.data or []


def list_recent_agent_signals(
    profile_id: str,
    agent: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    q = (
        db()
        .table("agent_signals")
        .select("agent,score,insight,signals,created_at")
        .eq("profile_id", profile_id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if agent:
        q = q.eq("agent", agent)
    res = q.execute()
    return res.data or []


def list_recent_check_ins(profile_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("check_ins")
        .select("id,date,status,core_summary,core_insight,completed_at")
        .eq("profile_id", profile_id)
        .order("date", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


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


# ────────────── Profiles + onboarding ──────────────
def get_profile_by_email(email: str) -> Optional[Dict[str, Any]]:
    email_lc = email.strip().lower()
    res = (
        db().table("profiles").select("*").eq("email", email_lc).limit(1).execute()
    )
    return res.data[0] if res.data else None


def create_profile(email: str, name: Optional[str] = None) -> Dict[str, Any]:
    """Crea un profile nuevo. Lanza si el email ya existe (unique constraint)."""
    email_lc = email.strip().lower()
    payload: Dict[str, Any] = {"email": email_lc, "last_login": "now()"}
    if name:
        payload["name"] = name
    res = db().table("profiles").insert(payload).execute()
    return res.data[0] if res.data else {}


def touch_profile(profile_id: str) -> None:
    db().table("profiles").update({"last_login": "now()"}).eq("id", profile_id).execute()


def upsert_profile(email: str, name: Optional[str] = None) -> Dict[str, Any]:
    """Crea o devuelve un profile por email (compat con /auth/mock-login)."""
    existing = get_profile_by_email(email)
    if existing:
        touch_profile(existing["id"])
        return existing
    return create_profile(email, name)


def save_assessment(
    profile_id: str,
    *,
    kind: str,
    score: int,
    max_score: int,
    severity: str,
    answers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    row = {
        "profile_id": profile_id,
        "kind": kind,
        "score": score,
        "max_score": max_score,
        "severity": severity,
        "answers": answers,
    }
    res = db().table("assessment_results").insert(row).execute()
    return res.data[0] if res.data else {}


def list_latest_assessments(profile_id: str) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("latest_assessment")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
    )
    return res.data or []


# ────────────── Digital signals (extensión) ──────────────
def insert_browsing_sessions(
    profile_id: Optional[str],
    sessions: List[Dict[str, Any]],
) -> int:
    """Bulk insert. Devuelve cuántas filas se persistieron."""
    if not sessions:
        return 0
    rows: List[Dict[str, Any]] = []
    for s in sessions:
        dur = int(s.get("duration_sec") or 0)
        dom = (s.get("domain") or "").strip().lower()
        started = s.get("started_at")
        if not dom or dur <= 0 or not started:
            continue
        row = {
            "domain": dom,
            "url": s.get("url"),
            "title": s.get("title"),
            "category": s.get("category") or "other",
            "started_at": started,
            "ended_at": s.get("ended_at"),
            "duration_sec": dur,
            "active": bool(s.get("active", True)),
            "source": s.get("source") or "extension",
        }
        if profile_id:
            row["profile_id"] = profile_id
        rows.append(row)
    if not rows:
        return 0
    res = db().table("browsing_sessions").insert(rows).execute()
    return len(res.data or [])


def insert_search_queries(
    profile_id: Optional[str],
    queries: List[Dict[str, Any]],
) -> int:
    if not queries:
        return 0
    rows: List[Dict[str, Any]] = []
    for q in queries:
        text = (q.get("query") or "").strip()
        ts = q.get("ts")
        if not text or not ts:
            continue
        row = {
            "engine": (q.get("engine") or "other").strip().lower(),
            "query": text[:300],
            "ts": ts,
            "source": q.get("source") or "extension",
        }
        if profile_id:
            row["profile_id"] = profile_id
        rows.append(row)
    if not rows:
        return 0
    res = db().table("search_queries").insert(rows).execute()
    return len(res.data or [])


def fetch_sessions_for_day(
    profile_id: str, day_iso: str
) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("browsing_sessions")
        .select("domain,url,category,duration_sec,active,started_at")
        .eq("profile_id", profile_id)
        .gte("started_at", day_iso + "T00:00:00Z")
        .lt("started_at", day_iso + "T23:59:59Z")
        .limit(5000)
        .execute()
    )
    return res.data or []


def fetch_sessions_between(
    profile_id: str, start_iso: str, end_iso: str
) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("browsing_sessions")
        .select("domain,url,category,duration_sec,active,started_at,ended_at")
        .eq("profile_id", profile_id)
        .gte("started_at", start_iso)
        .lt("started_at", end_iso)
        .limit(5000)
        .execute()
    )
    return res.data or []


def fetch_queries_for_day(
    profile_id: str, day_iso: str
) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("search_queries")
        .select("engine,query,ts")
        .eq("profile_id", profile_id)
        .gte("ts", day_iso + "T00:00:00Z")
        .lt("ts", day_iso + "T23:59:59Z")
        .limit(2000)
        .execute()
    )
    return res.data or []


def upsert_digital_metric(
    profile_id: str,
    day_iso: str,
    metric: Dict[str, Any],
) -> Dict[str, Any]:
    mins = metric.get("minutes_by_category", {})
    scores = metric.get("scores", {})
    row = {
        "profile_id": profile_id,
        "date": day_iso,
        "minutes_social": mins.get("social", 0),
        "minutes_entertainment": mins.get("entertainment", 0),
        "minutes_news": mins.get("news", 0),
        "minutes_work": mins.get("work", 0),
        "minutes_education": mins.get("education", 0),
        "minutes_shopping": mins.get("shopping", 0),
        "minutes_search": mins.get("search", 0),
        "minutes_ai": mins.get("ai", 0),
        "minutes_other": mins.get("other", 0),
        "score_social": scores.get("social"),
        "score_focus": scores.get("focus"),
        "score_balance": scores.get("balance"),
        "score_digital_overall": scores.get("digital_overall"),
        "top_domains": metric.get("top_domains", []),
        "search_themes": metric.get("search_themes", []),
        "context_snapshot": {
            "total_minutes": metric.get("total_minutes", 0),
        },
        "computed_at": "now()",
    }
    res = (
        db()
        .table("digital_metrics")
        .upsert(row, on_conflict="profile_id,date")
        .execute()
    )
    return res.data[0] if res.data else {}


def get_latest_digital_metric(profile_id: str) -> Optional[Dict[str, Any]]:
    res = (
        db()
        .table("latest_digital_metric")
        .select("*")
        .eq("profile_id", profile_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_digital_metrics(
    profile_id: str, limit: int = 14
) -> List[Dict[str, Any]]:
    res = (
        db()
        .table("digital_metrics")
        .select("date,score_social,score_focus,score_balance,score_digital_overall,"
                "minutes_social,minutes_entertainment,minutes_work,minutes_education")
        .eq("profile_id", profile_id)
        .order("date", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


# ────────────── Recomendaciones ──────────────
def insert_recommendations(
    profile_id: str,
    items: List[Dict[str, Any]],
    model: Optional[str] = None,
    source_metrics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not items:
        return []
    rows: List[Dict[str, Any]] = []
    for it in items:
        row = {
            "profile_id": profile_id,
            "kind": it.get("kind", "tip"),
            "title": it.get("title"),
            "body": it.get("body"),
            "rationale": it.get("rationale"),
            "score_impact": it.get("score_impact"),
            "source_metrics": source_metrics or {},
            "model": model,
        }
        if it.get("habit_proposal"):
            row["habit_proposal"] = it["habit_proposal"]
        rows.append(row)
    res = db().table("recommendations").insert(rows).execute()
    return res.data or []


def list_recommendations(
    profile_id: str,
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    q = (
        db()
        .table("recommendations")
        .select("*")
        .eq("profile_id", profile_id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        q = q.eq("status", status)
    res = q.execute()
    return res.data or []


def update_recommendation_status(
    rec_id: str,
    status: str,
    linked_habit_id: Optional[str] = None,
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {"status": status, "acted_at": "now()"}
    if linked_habit_id:
        patch["linked_habit_id"] = linked_habit_id
    res = (
        db()
        .table("recommendations")
        .update(patch)
        .eq("id", rec_id)
        .execute()
    )
    return res.data[0] if res.data else {}


def create_habit_from_proposal(
    profile_id: str,
    proposal: Dict[str, Any],
) -> Dict[str, Any]:
    row = {
        "profile_id": profile_id,
        "name": proposal.get("name") or "Hábito",
        "emoji": proposal.get("emoji") or "✨",
        "frequency": proposal.get("frequency") or "daily",
        "target_per_week": proposal.get("target_per_week"),
        "description": proposal.get("trigger"),
    }
    res = db().table("habits").insert(row).execute()
    return res.data[0] if res.data else {}
