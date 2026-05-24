from __future__ import annotations
from datetime import datetime
from typing import Any

_sessions: dict[str, list[dict[str, Any]]] = {}


def log_session(profile_id: str, duration_minutes: float, session_type: str = "breathing") -> dict[str, Any]:
    bucket = _sessions.setdefault(profile_id, [])
    session = {
        "id": f"s{len(bucket) + 1}",
        "profile_id": profile_id,
        "duration_minutes": duration_minutes,
        "session_type": session_type,
        "logged_at": datetime.utcnow().isoformat(),
    }
    bucket.append(session)
    return session


def get_stats(profile_id: str) -> dict[str, Any]:
    sessions = _sessions.get(profile_id, [])
    total_minutes = sum(s["duration_minutes"] for s in sessions)
    today = datetime.utcnow().date().isoformat()
    today_sessions = [s for s in sessions if s["logged_at"][:10] == today]
    return {
        "total_sessions": len(sessions),
        "total_minutes": total_minutes,
        "average_minutes": total_minutes / len(sessions) if sessions else 0,
        "today_sessions": len(today_sessions),
        "today_minutes": sum(s["duration_minutes"] for s in today_sessions),
        "recent_sessions": list(reversed(sessions))[:5],
    }


def get_context_for_llm(profile_id: str) -> str | None:
    stats = get_stats(profile_id)
    if stats["total_sessions"] == 0:
        return None
    return (
        f"Meditación hoy: {stats['today_sessions']} sesión(es), {stats['today_minutes']} min. "
        f"Histórico: {stats['total_sessions']} sesiones, {stats['total_minutes']} min acumulados."
    )
