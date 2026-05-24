"""Kairós — generador de recomendaciones.

Combina: assessments del onboarding + lecturas de agentes + métrica digital del día
+ hábitos actuales, y le pide al LLM (OpenAI por defecto, mismo cliente que el Core)
una lista de 3-5 recomendaciones, donde al menos 1 es un hábito accionable.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from prompts import RECOMMENDER_PROMPT


_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


class RecommenderError(Exception):
    pass


_VALID_KINDS = {"habit", "tip", "warning", "reflection"}
_VALID_FREQ = {"daily", "weekly", "custom"}


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        raw = raw[first_nl + 1:] if first_nl != -1 else raw[3:]
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
                return raw[start:i + 1]
    return raw[start:]


def build_context(
    *,
    profile_summary: Optional[str],
    digital_summary: Optional[str],
    agent_signals: List[Dict[str, Any]],
    habits: List[Dict[str, Any]],
    recent_chat_excerpt: Optional[str] = None,
) -> str:
    parts: List[str] = []
    if profile_summary:
        parts.append("[Onboarding / assessments]\n" + profile_summary)
    if digital_summary:
        parts.append("[Métrica digital de hoy]\n" + digital_summary)
    if agent_signals:
        lines = []
        for s in agent_signals[:10]:
            name = s.get("agent") or "?"
            score = s.get("score")
            ins = (s.get("insight") or "").strip()
            score_str = f" ({score})" if score is not None else ""
            if ins:
                lines.append(f"- {name}{score_str}: {ins}")
        if lines:
            parts.append("[Lecturas recientes de los agentes]\n" + "\n".join(lines))
    if habits:
        names = ", ".join(
            f"{h.get('emoji', '')} {h.get('name', '?')}".strip()
            for h in habits[:8]
        )
        if names:
            parts.append("[Hábitos actuales del usuario]\n" + names)
    if recent_chat_excerpt:
        parts.append("[Último intercambio con Core]\n" + recent_chat_excerpt[:600])
    return "\n\n".join(parts) if parts else "(sin contexto disponible)"


def _coerce_item(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _VALID_KINDS:
        return None
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not title or not body:
        return None
    rationale = str(raw.get("rationale") or "").strip() or None
    try:
        impact = float(raw.get("score_impact"))
        impact = max(0.0, min(100.0, impact))
    except (TypeError, ValueError):
        impact = None

    item: Dict[str, Any] = {
        "kind": kind,
        "title": title[:80],
        "body": body[:400],
        "rationale": rationale,
        "score_impact": impact,
    }
    if kind == "habit":
        hp_raw = raw.get("habit_proposal") or {}
        if not isinstance(hp_raw, dict):
            return None
        name = str(hp_raw.get("name") or title).strip()[:60]
        emoji = str(hp_raw.get("emoji") or "✨").strip()[:4]
        freq = str(hp_raw.get("frequency") or "daily").strip().lower()
        if freq not in _VALID_FREQ:
            freq = "daily"
        try:
            tpw_raw = hp_raw.get("target_per_week")
            tpw = int(tpw_raw) if tpw_raw is not None else None
            if tpw is not None and (tpw < 1 or tpw > 21):
                tpw = None
        except (TypeError, ValueError):
            tpw = None
        trigger = str(hp_raw.get("trigger") or "").strip()[:120] or None
        item["habit_proposal"] = {
            "name": name,
            "emoji": emoji,
            "frequency": freq,
            "target_per_week": tpw,
            "trigger": trigger,
        }
    return item


async def generate_recommendations(
    *,
    profile_summary: Optional[str],
    digital_summary: Optional[str],
    agent_signals: List[Dict[str, Any]],
    habits: List[Dict[str, Any]],
    recent_chat_excerpt: Optional[str] = None,
    max_items: int = 5,
) -> Dict[str, Any]:
    context = build_context(
        profile_summary=profile_summary,
        digital_summary=digital_summary,
        agent_signals=agent_signals,
        habits=habits,
        recent_chat_excerpt=recent_chat_excerpt,
    )
    system = RECOMMENDER_PROMPT.format(context=context)

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = await _client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": "Devuelve la lista JSON ahora.",
                    },
                ],
                temperature=0.55,
                max_tokens=900,
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 2:
                raise RecommenderError(f"openai falló: {e}") from e
            await asyncio.sleep(2 ** attempt)
    else:
        raise RecommenderError(f"sin respuesta: {last_err}")

    raw = resp.choices[0].message.content or ""
    used_model = getattr(resp, "model", OPENAI_MODEL)
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as e:
        raise RecommenderError(
            f"el recomendador no devolvió JSON: {raw[:400]}"
        ) from e

    items_raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items_raw, list):
        raise RecommenderError("el JSON no tiene 'items' lista")

    items: List[Dict[str, Any]] = []
    for r in items_raw:
        coerced = _coerce_item(r)
        if coerced:
            items.append(coerced)
        if len(items) >= max_items:
            break

    if not items:
        raise RecommenderError("ninguna recomendación válida")

    return {"items": items, "model": used_model, "context_used": context}
