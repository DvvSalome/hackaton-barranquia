"""Spotify mock — stores current track per profile and exposes LLM context."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_tracks: dict[str, dict[str, Any]] = {}


def set_track(
    profile_id: str,
    track: str,
    artist: str,
    genre: str = "",
) -> dict[str, Any]:
    record = {
        "track": track,
        "artist": artist,
        "genre": genre,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _tracks[profile_id] = record
    return record


def get_track(profile_id: str) -> dict[str, Any] | None:
    return _tracks.get(profile_id)


def get_context_for_llm(profile_id: str) -> str | None:
    t = get_track(profile_id)
    if not t:
        return None
    parts = [f"Escuchando: \"{t['track']}\" de {t['artist']}"]
    if t.get("genre"):
        parts.append(f"(género: {t['genre']})")
    return " ".join(parts)
