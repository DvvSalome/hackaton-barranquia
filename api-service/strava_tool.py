"""Strava mock — stores last activity per profile."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_activities: dict[str, dict[str, Any]] = {}


def log_activity(
    profile_id: str,
    sport: str,
    duration_min: int,
    distance_km: float = 0.0,
) -> dict[str, Any]:
    record = {
        "sport": sport,
        "duration_min": duration_min,
        "distance_km": distance_km,
        "logged_at": datetime.utcnow().isoformat(),
    }
    _activities[profile_id] = record
    return record


def get_activity(profile_id: str) -> dict[str, Any] | None:
    return _activities.get(profile_id)


def get_context_for_llm(profile_id: str) -> str | None:
    a = get_activity(profile_id)
    if not a:
        return None
    parts = [f"Strava — última actividad: {a['sport']}, {a['duration_min']} min"]
    if a.get("distance_km"):
        parts.append(f", {a['distance_km']} km")
    return "".join(parts) + "."
