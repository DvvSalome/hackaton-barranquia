"""Carga variables desde ../.env"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _req(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise RuntimeError(f"falta variable de entorno: {key}")
    return v


SUPABASE_URL = _req("SUPABASE_URL")
SUPABASE_ANON_KEY = _req("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = _req("SUPABASE_SERVICE_KEY")

OPENAI_API_KEY = _req("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

OPENROUTER_API_KEY = _req("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
)
