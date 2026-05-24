"""Specialist consultant chains — each is an LLM with a focused system prompt.

Each specialist receives context (transcript + CV signals + digital signals) and
returns a structured JSON: { score, insight, signals }.
The orchestrator collects all specialist outputs before the final synthesis.
"""
from __future__ import annotations

import json
import asyncio
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from knowledge_base import get_agent_context


SPECIALIST_DEFINITIONS: dict[str, dict[str, str]] = {
    "animo": {
        "name": "Ánimo",
        "color": "#f87171",
        "focus": "emociones, estados de humor, momentos de tensión, alegría o frustración",
        "icon": "😊",
    },
    "sueno": {
        "name": "Sueño",
        "color": "#a78bfa",
        "focus": "horas de sueño, calidad subjetiva, hora de dormir/despertar, fatiga residual",
        "icon": "😴",
    },
    "foco": {
        "name": "Foco",
        "color": "#60a5fa",
        "focus": "claridad mental, bloques de trabajo profundo, distracciones, interrupciones",
        "icon": "🎯",
    },
    "energia": {
        "name": "Energía",
        "color": "#fbbf24",
        "focus": "nivel de energía a lo largo del día, picos y caídas, fatiga física",
        "icon": "⚡",
    },
    "pantalla": {
        "name": "Pantalla",
        "color": "#34d399",
        "focus": "tiempo de pantalla, scroll pasivo vs uso intencional, apps que más consumen atención",
        "icon": "📱",
    },
    "rachas": {
        "name": "Rachas",
        "color": "#fb923c",
        "focus": "constancia con hábitos, días seguidos cumpliendo objetivos, momentum y caídas",
        "icon": "🔥",
    },
}


SPECIALIST_SYSTEM = """\
Eres el consultor especialista "{name}" de Kairós, un copiloto de bienestar digital.

Tu único foco de análisis: {focus}.

{knowledge_block}

Recibirás un bloque de contexto con:
- Texto libre del día del usuario (respuestas al check-in)
- Señales de computer vision (postura, somnolencia, distracciones detectadas por cámara)
- Señales digitales de la extensión de Chrome (apps usadas, tiempo en cada categoría)
- Resultados de evaluaciones previas (PHQ-9, GAD-7)
- Entradas recientes del diario (texto libre + estado de ánimo: bien/neutral/difícil)
- Sesiones de meditación del día (duración acumulada)
- Pista de Spotify activa (canción, artista, género)
- Eventos del calendario de hoy (título y hora)
- Datos de salud (pasos, horas de sueño, frecuencia cardíaca)
- Tareas de Notion (pendientes y completadas)
- Última actividad de Strava (deporte, duración, distancia)

Tu tarea: analizar ÚNICAMENTE lo que corresponde a {name} y devolver este JSON exacto:

{{
  "score": <número 0–100 o null si no hay datos suficientes>,
  "insight": "<1-2 frases en español, voz cálida y específica, sin emojis, sin signos de exclamación>",
  "signals": [
    {{"key": "<snake_case>", "value": "<string>", "weight": <0.0–1.0>}}
  ],
  "recommendations": ["<acción concreta 1>", "<acción concreta 2>"]
}}

Reglas estrictas:
- Solo analizas {name}. No opines sobre los demás especialistas.
- Si la información es escasa: score = null, signals = [], recommendations = [].
- Máximo 5 signals cuando haya información suficiente.
- Las recommendations deben ser específicas y accionables (no genéricas).
- Responde EXCLUSIVAMENTE el JSON. Sin markdown, sin ```, sin texto extra.
"""




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


async def run_specialist(kind: str, context: str) -> dict[str, Any]:
    """Run a specialist chain and return parsed result."""
    if kind not in SPECIALIST_DEFINITIONS:
        raise ValueError(f"specialist desconocido: {kind}")

    # Inject relevant knowledge chunks into the system prompt
    kb_context = get_agent_context(kind, context)
    knowledge_block = (
        f"Conocimiento de referencia (usa solo si es relevante):\n{kb_context}"
        if kb_context else ""
    )
    spec = SPECIALIST_DEFINITIONS[kind]
    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        temperature=0.3,
        max_tokens=600,
        default_headers={"HTTP-Referer": "https://kairos.local", "X-Title": "Kairos-Specialist"},
    )
    system_prompt = SPECIALIST_SYSTEM.format(
        name=spec["name"], focus=spec["focus"], knowledge_block=knowledge_block
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{context}"),
    ])
    chain = prompt | llm | StrOutputParser()
    raw = await chain.ainvoke({"context": context})
    cleaned = _extract_json(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "agent": kind,
            "name": SPECIALIST_DEFINITIONS[kind]["name"],
            "score": None,
            "insight": "No pude analizar esta sesión.",
            "signals": [],
            "recommendations": [],
            "error": f"JSON inválido: {raw[:200]}",
        }

    score = data.get("score")
    if score is not None:
        try:
            score = float(score)
            score = max(0.0, min(100.0, score))
        except (TypeError, ValueError):
            score = None

    return {
        "agent": kind,
        "name": SPECIALIST_DEFINITIONS[kind]["name"],
        "score": score,
        "insight": str(data.get("insight", "")).strip(),
        "signals": data.get("signals", [])[:5],
        "recommendations": data.get("recommendations", [])[:3],
    }


async def run_all_specialists(context: str) -> list[dict[str, Any]]:
    """Run all 6 specialists in parallel and collect results."""
    kinds = list(SPECIALIST_DEFINITIONS.keys())
    results = await asyncio.gather(
        *[run_specialist(k, context) for k in kinds],
        return_exceptions=True,
    )
    out = []
    for k, r in zip(kinds, results):
        if isinstance(r, Exception):
            out.append({"agent": k, "name": SPECIALIST_DEFINITIONS[k]["name"], "error": str(r)})
        else:
            out.append(r)
    return out
