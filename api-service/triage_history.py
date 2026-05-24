"""Triage history store — persists diagnostic results across check-ins.

Enables temporal trend detection: if a user receives 3+ severe readings,
the referral urgency auto-escalates in the SummaryTool context.

Storage: in-memory dict (runtime) + JSON file (api-service/data/triage_history.json).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"
_HISTORY_FILE = _DATA_DIR / "triage_history.json"

_store: dict[str, list[dict[str, Any]]] = {}
_loaded = False

_SEVERITY_ORDER = {
    "bienestar_en_rango": 0,
    "distres_leve": 1,
    "uso_digital_elevado": 1,
    "fatiga_digital": 1,
    "sueno_deficiente": 2,
    "privacion_sueno": 2,
    "distres_moderado": 3,
    "distres_severo": 4,
}


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if _HISTORY_FILE.exists():
        try:
            data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            for pid, entries in data.items():
                _store[pid] = entries
        except Exception:
            pass


def _persist() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(
        json.dumps(_store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_triage(
    profile_id: str,
    triage: dict[str, Any],
    *,
    composite_score: float | None = None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    _load()
    entry = {
        "id": f"t{len(_store.get(profile_id, [])) + 1}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diagnosis": triage.get("diagnosis", {}),
        "solution": triage.get("solution", {}),
        "referral": triage.get("referral", {}),
        "composite_score": composite_score,
        "risk_level": risk_level,
    }
    _store.setdefault(profile_id, []).append(entry)
    try:
        _persist()
    except Exception:
        pass
    return entry


def get_history(profile_id: str, limit: int = 10) -> list[dict[str, Any]]:
    _load()
    entries = _store.get(profile_id, [])
    return list(reversed(entries))[:limit]


def get_trend(profile_id: str, window: int = 5) -> dict[str, Any]:
    """Return temporal trend for the last `window` check-ins.

    Returns:
        direction: "improving" | "worsening" | "stable"
        severity_series: list of severity integers (0-4, most recent last)
        consecutive_severe: how many of the last N were distres_severo
        escalate_referral: True if 3+ of last 5 were severity >= 3
    """
    _load()
    recent = get_history(profile_id, window)
    if not recent:
        return {"direction": "stable", "severity_series": [], "consecutive_severe": 0, "escalate_referral": False}

    series = []
    for entry in reversed(recent):
        pattern = (entry.get("diagnosis") or {}).get("pattern", "bienestar_en_rango")
        series.append(_SEVERITY_ORDER.get(pattern, 0))

    consecutive_severe = sum(1 for s in series[-3:] if s >= 3)
    escalate_referral = consecutive_severe >= 2

    if len(series) >= 2:
        delta = series[-1] - series[0]
        direction = "worsening" if delta > 0 else ("improving" if delta < 0 else "stable")
    else:
        direction = "stable"

    return {
        "direction": direction,
        "severity_series": series,
        "consecutive_severe": consecutive_severe,
        "escalate_referral": escalate_referral,
    }


def get_context_for_llm(profile_id: str, limit: int = 3) -> str | None:
    """Return a short string summary suitable for LLM context injection."""
    recent = get_history(profile_id, limit)
    if not recent:
        return None
    trend = get_trend(profile_id)
    lines = []
    for e in recent:
        date = e["timestamp"][:10]
        diag = (e.get("diagnosis") or {}).get("pattern", "?")
        ref = (e.get("referral") or {}).get("refer", False)
        score = e.get("composite_score")
        score_str = f" | score={score:.0f}" if score is not None else ""
        ref_str = " [DERIVACION]" if ref else ""
        lines.append(f"[{date}] {diag}{score_str}{ref_str}")
    header = f"Historial diagnóstico (últimas {len(lines)} sesiones, tendencia: {trend['direction']}):"
    return header + "\n" + "\n".join(lines)
