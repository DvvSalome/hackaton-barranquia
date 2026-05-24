from __future__ import annotations

from typing import Dict, List

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from prompts import CORE_SYNTHESIS, CORE_SYSTEM

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def core_reply(messages: List[Dict[str, str]]) -> str:
    """Una pasada del Core sobre el historial de mensajes del check-in."""
    full = [{"role": "system", "content": CORE_SYSTEM}] + messages
    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=full,
        temperature=0.5,
        max_tokens=300,
    )
    return (resp.choices[0].message.content or "").strip()


async def core_synthesis(agent_results: List[Dict]) -> str:
    """Pide al Core que sintetice las 6 lecturas en 3-4 frases."""
    readings_lines = []
    for r in agent_results:
        if "error" in r:
            continue
        name = r.get("name", r.get("agent", "?"))
        insight = r.get("insight", "").strip()
        score = r.get("score")
        if not insight:
            continue
        score_str = f" (score {score})" if score is not None else ""
        readings_lines.append(f"- {name}{score_str}: {insight}")
    readings = "\n".join(readings_lines) if readings_lines else "(sin lecturas)"

    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": CORE_SYNTHESIS.format(readings=readings)},
            {"role": "user", "content": "Entrega la síntesis ahora."},
        ],
        temperature=0.6,
        max_tokens=300,
    )
    return (resp.choices[0].message.content or "").strip()
