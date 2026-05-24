"""Health mock — stores step count, sleep hours, and heart rate per profile."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_records: dict[str, dict[str, Any]] = {}


def set_health(
    profile_id: str,
    steps: int = 0,
    sleep_hours: float = 0.0,
    heart_rate: int = 0,
) -> dict[str, Any]:
    record = {
        "steps": steps,
        "sleep_hours": sleep_hours,
        "heart_rate": heart_rate,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _records[profile_id] = record
    return record


def get_health(profile_id: str) -> dict[str, Any] | None:
    return _records.get(profile_id)


def get_context_for_llm(profile_id: str) -> str | None:
    r = get_health(profile_id)
    if not r:
        return None
    parts = []
    if r.get("steps"):
        parts.append(f"{r['steps']:,} pasos")
    if r.get("sleep_hours"):
        parts.append(f"{r['sleep_hours']} h de sueño")
    if r.get("heart_rate"):
        parts.append(f"FC {r['heart_rate']} bpm")
    if not parts:
        return None
    return "Salud hoy: " + ", ".join(parts) + "."
