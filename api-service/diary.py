from __future__ import annotations
from datetime import datetime
from typing import Any

_entries: dict[str, list[dict[str, Any]]] = {}


def add_entry(profile_id: str, content: str, title: str | None = None, mood: str | None = None) -> dict[str, Any]:
    bucket = _entries.setdefault(profile_id, [])
    entry = {
        "id": f"e{len(bucket) + 1}",
        "profile_id": profile_id,
        "content": content,
        "title": title or content[:40],
        "mood": mood or "neutral",
        "created_at": datetime.utcnow().isoformat(),
    }
    bucket.append(entry)
    return entry


def get_entries(profile_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list(reversed(_entries.get(profile_id, [])))[:limit]


def get_context_for_llm(profile_id: str, limit: int = 3) -> str | None:
    recent = get_entries(profile_id, limit)
    if not recent:
        return None
    lines = []
    for e in recent:
        date = e["created_at"][:10]
        mood_tag = f" [{e['mood']}]" if e.get("mood") else ""
        title_tag = f" — {e['title']}" if e.get("title") else ""
        lines.append(f"[{date}{title_tag}{mood_tag}] {e['content']}")
    return "Entradas recientes del diario:\n" + "\n".join(lines)
