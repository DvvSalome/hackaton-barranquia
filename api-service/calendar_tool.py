"""Calendario mock — stores today's events per profile and exposes LLM context."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

_events: dict[str, list[dict[str, Any]]] = {}


def add_event(
    profile_id: str,
    title: str,
    time: str,
    duration_min: int = 60,
) -> dict[str, Any]:
    bucket = _events.setdefault(profile_id, [])
    event = {
        "id": str(uuid.uuid4()),
        "title": title,
        "time": time,
        "duration_min": duration_min,
        "date": datetime.utcnow().date().isoformat(),
    }
    bucket.append(event)
    return event


def get_today_events(profile_id: str) -> list[dict[str, Any]]:
    today = datetime.utcnow().date().isoformat()
    return [e for e in _events.get(profile_id, []) if e["date"] == today]


def get_context_for_llm(profile_id: str) -> str | None:
    events = get_today_events(profile_id)
    if not events:
        return None
    items = ", ".join(f"{e['title']} a las {e['time']}" for e in events)
    return f"Calendario hoy: {len(events)} evento(s) — {items}."
