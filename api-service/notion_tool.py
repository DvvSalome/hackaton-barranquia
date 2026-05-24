"""Notion mock — stores tasks (title, deadline, done) per profile."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

_tasks: dict[str, list[dict[str, Any]]] = {}


def add_task(
    profile_id: str,
    title: str,
    deadline: str | None = None,
) -> dict[str, Any]:
    bucket = _tasks.setdefault(profile_id, [])
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "deadline": deadline,
        "done": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    bucket.append(task)
    return task


def toggle_task(profile_id: str, task_id: str) -> dict[str, Any] | None:
    for t in _tasks.get(profile_id, []):
        if t["id"] == task_id:
            t["done"] = not t["done"]
            return t
    return None


def get_tasks(profile_id: str) -> list[dict[str, Any]]:
    return list(_tasks.get(profile_id, []))


def get_context_for_llm(profile_id: str) -> str | None:
    tasks = get_tasks(profile_id)
    if not tasks:
        return None
    pending = [t for t in tasks if not t["done"]]
    done = [t for t in tasks if t["done"]]
    parts = [f"Notion: {len(pending)} tarea(s) pendiente(s), {len(done)} completada(s)."]
    if pending:
        titles = ", ".join(f'"{t["title"]}"' for t in pending[:3])
        parts.append(f"Pendientes: {titles}.")
    return " ".join(parts)
