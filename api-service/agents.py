from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL
from prompts import AGENT_DEFINITIONS, AGENT_QUESTION_PROMPT, SPECIALIST_PROMPT


class AgentError(Exception):
    pass


openrouter = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://kairos.local",
        "X-Title": "Kairos",
    },
    timeout=60.0,
)

# Fallbacks que OpenRouter intenta si el primario está rate-limited / caído.
FALLBACK_MODELS: List[str] = [
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
    "minimax/minimax-m2.5:free",
]


def _extract_json(raw: str) -> str:
    """Devuelve el primer bloque {...} del texto. Tolera reasoning previo o ``` fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        raw = raw[first_nl + 1 :] if first_nl != -1 else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]


def _coerce_signals(raw_signals: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_signals, list):
        return []
    out: List[Dict[str, Any]] = []
    for s in raw_signals[:5]:
        if not isinstance(s, dict):
            continue
        key = s.get("key")
        value = s.get("value")
        weight = s.get("weight", 0.5)
        if not isinstance(key, str) or value is None:
            continue
        try:
            w = float(weight)
        except (TypeError, ValueError):
            w = 0.5
        out.append({"key": key, "value": str(value), "weight": max(0.0, min(1.0, w))})
    return out


async def run_specialist(kind: str, context: str) -> Dict[str, Any]:
    if kind not in AGENT_DEFINITIONS:
        raise AgentError(f"agente desconocido: {kind}")
    spec = AGENT_DEFINITIONS[kind]
    system = SPECIALIST_PROMPT.format(name=spec["name"], focus=spec["focus"])

    fallbacks = [m for m in FALLBACK_MODELS if m != OPENROUTER_MODEL]
    last_err: Exception
    for attempt in range(3):
        try:
            resp = await openrouter.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
                max_tokens=800,
                extra_body={"models": fallbacks},
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            if "429" in msg or "rate" in msg:
                await asyncio.sleep(2 ** attempt)
                continue
            raise AgentError(f"openrouter falló: {e}") from e
    else:
        raise AgentError(f"openrouter rate-limited después de 3 intentos: {last_err}")

    raw = resp.choices[0].message.content or ""
    used_model = getattr(resp, "model", OPENROUTER_MODEL)
    cleaned = _extract_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AgentError(
            f"agente {kind} no devolvió JSON válido: {raw[:400]}"
        ) from e

    score = data.get("score")
    if score is not None:
        try:
            score = float(score)
            if not (0 <= score <= 100):
                score = None
        except (TypeError, ValueError):
            score = None

    return {
        "agent": kind,
        "name": spec["name"],
        "score": score,
        "insight": str(data.get("insight", "")).strip(),
        "signals": _coerce_signals(data.get("signals")),
        "model": used_model,
    }


def _coerce_chips(raw_chips: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_chips, list):
        return []
    out: List[Dict[str, str]] = []
    seen: set = set()
    for c in raw_chips[:4]:
        if not isinstance(c, dict):
            continue
        v = str(c.get("v") or "").strip().lower().replace(" ", "_")
        tx = str(c.get("tx") or "").strip()
        if not v or not tx or v in seen:
            continue
        seen.add(v)
        out.append({"v": v[:32], "tx": tx[:48]})
    return out


async def generate_agent_question(
    kind: str,
    *,
    baseline: Optional[str] = None,
    recent_turns: Optional[List[Dict[str, Any]]] = None,
    recent_signals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Pide al LLM una pregunta + 4 chips para el agente, considerando historial."""
    if kind not in AGENT_DEFINITIONS:
        raise AgentError(f"agente desconocido: {kind}")
    spec = AGENT_DEFINITIONS[kind]

    parts: List[str] = []
    if baseline:
        parts.append(f"Baseline del onboarding:\n{baseline}")
    if recent_turns:
        lines = []
        for t in recent_turns[:5]:
            q = (t.get("question") or "").strip()
            a = (t.get("answer") or "").strip()
            if q:
                lines.append(f"- Antes preguntaste: \"{q}\" → respondió: \"{a or '—'}\"")
        if lines:
            parts.append("Historial reciente de tus preguntas:\n" + "\n".join(lines))
    if recent_signals:
        lines = []
        for s in recent_signals[:3]:
            ins = (s.get("insight") or "").strip()
            if ins:
                lines.append(f"- {ins}")
        if lines:
            parts.append("Tus últimas lecturas del usuario:\n" + "\n".join(lines))
    context = "\n\n".join(parts) if parts else "(primera vez con este usuario, sin historial)"

    system = AGENT_QUESTION_PROMPT.format(
        name=spec["name"], focus=spec["focus"], context=context
    )

    fallbacks = [m for m in FALLBACK_MODELS if m != OPENROUTER_MODEL]
    last_err: Exception
    for attempt in range(3):
        try:
            resp = await openrouter.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "Genera la pregunta y las chips ahora."},
                ],
                temperature=0.85,  # más variedad
                max_tokens=400,
                extra_body={"models": fallbacks},
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            if "429" in msg or "rate" in msg:
                await asyncio.sleep(2 ** attempt)
                continue
            raise AgentError(f"openrouter falló: {e}") from e
    else:
        raise AgentError(f"openrouter rate-limited: {last_err}")

    raw = resp.choices[0].message.content or ""
    used_model = getattr(resp, "model", OPENROUTER_MODEL)
    cleaned = _extract_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AgentError(f"pregunta {kind} no devolvió JSON válido: {raw[:300]}") from e

    question = str(data.get("question") or "").strip()
    chips = _coerce_chips(data.get("chips"))
    if not question or len(chips) < 2:
        raise AgentError(f"pregunta {kind} incompleta: {data}")
    return {
        "agent": kind,
        "name": spec["name"],
        "question": question,
        "chips": chips,
        "model": used_model,
    }
