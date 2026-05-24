from __future__ import annotations

from typing import Dict, List

from typing import Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from prompts import CORE_BASELINE_BLOCK, CORE_SYNTHESIS, CORE_SYSTEM

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def _system_with_context(profile_context: Optional[str]) -> str:
    if not profile_context:
        return CORE_SYSTEM
    return CORE_SYSTEM + "\n\n" + CORE_BASELINE_BLOCK.format(baseline=profile_context)


async def core_reply(
    messages: List[Dict[str, str]],
    profile_context: Optional[str] = None,
) -> str:
    """Una pasada del Core sobre el historial de mensajes del check-in."""
    full = [{"role": "system", "content": _system_with_context(profile_context)}] + messages
    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=full,
        temperature=0.5,
        max_tokens=300,
    )
    return (resp.choices[0].message.content or "").strip()


async def core_synthesis(
    agent_results: List[Dict],
    profile_context: Optional[str] = None,
) -> str:
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

    system = CORE_SYNTHESIS.format(readings=readings)
    if profile_context:
        system = system + "\n\n" + CORE_BASELINE_BLOCK.format(baseline=profile_context)
    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "Entrega la síntesis ahora."},
        ],
        temperature=0.6,
        max_tokens=300,
    )
    return (resp.choices[0].message.content or "").strip()
