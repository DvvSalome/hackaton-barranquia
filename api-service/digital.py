"""Kairós — scoring digital.

Toma sesiones de navegación + búsquedas crudas (provenientes de la extensión)
y produce métricas por categoría + 3 sub-scores (social, foco, balance) +
score global digital, en escala 0-100 donde más alto = mejor.

La idea no es moralizar: 0 horas de social NO es 100. La curva es óptimo-en-medio
para social/entretenimiento y monotónica para foco/trabajo.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


# ─────────── Categorización de dominios ───────────
# Lista pragmática, no exhaustiva. La extensión también puede mandar la
# categoría ya resuelta; el backend la respeta si viene válida.

CATEGORY_MAP: Dict[str, str] = {
    # social
    "instagram.com": "social",
    "tiktok.com": "social",
    "twitter.com": "social",
    "x.com": "social",
    "facebook.com": "social",
    "reddit.com": "social",
    "snapchat.com": "social",
    "threads.net": "social",
    "bsky.app": "social",
    "pinterest.com": "social",
    "linkedin.com": "social",  # debatible, lo dejamos en social por scroll
    # entretenimiento
    "youtube.com": "entertainment",
    "youtu.be": "entertainment",
    "netflix.com": "entertainment",
    "twitch.tv": "entertainment",
    "primevideo.com": "entertainment",
    "disneyplus.com": "entertainment",
    "spotify.com": "entertainment",
    "soundcloud.com": "entertainment",
    "9gag.com": "entertainment",
    # news
    "elpais.com": "news",
    "eltiempo.com": "news",
    "semana.com": "news",
    "bbc.com": "news",
    "cnn.com": "news",
    "nytimes.com": "news",
    "elheraldo.co": "news",
    "infobae.com": "news",
    # work / productividad
    "mail.google.com": "work",
    "gmail.com": "work",
    "outlook.com": "work",
    "outlook.office.com": "work",
    "slack.com": "work",
    "notion.so": "work",
    "trello.com": "work",
    "asana.com": "work",
    "atlassian.net": "work",
    "jira.com": "work",
    "github.com": "work",
    "gitlab.com": "work",
    "linear.app": "work",
    "figma.com": "work",
    "docs.google.com": "work",
    "drive.google.com": "work",
    "calendar.google.com": "work",
    # education / docs
    "wikipedia.org": "education",
    "stackoverflow.com": "education",
    "coursera.org": "education",
    "udemy.com": "education",
    "khanacademy.org": "education",
    "developer.mozilla.org": "education",
    "edx.org": "education",
    "scholar.google.com": "education",
    "arxiv.org": "education",
    # shopping
    "amazon.com": "shopping",
    "mercadolibre.com.co": "shopping",
    "mercadolibre.com": "shopping",
    "ebay.com": "shopping",
    "aliexpress.com": "shopping",
    "shein.com": "shopping",
    # search
    "google.com": "search",
    "bing.com": "search",
    "duckduckgo.com": "search",
    "perplexity.ai": "search",
    "kagi.com": "search",
    # ai
    "chat.openai.com": "ai",
    "chatgpt.com": "ai",
    "claude.ai": "ai",
    "gemini.google.com": "ai",
    "bard.google.com": "ai",
    "copilot.microsoft.com": "ai",
    "poe.com": "ai",
}

VALID_CATEGORIES = {
    "social",
    "entertainment",
    "news",
    "work",
    "education",
    "shopping",
    "search",
    "ai",
    "other",
}


def normalize_domain(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        try:
            host = urlparse(raw).netloc
        except Exception:  # noqa: BLE001
            host = raw
    else:
        host = raw.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def categorize_domain(domain: str, hint: Optional[str] = None) -> str:
    """Resuelve categoría. Si la extensión manda hint válido, lo respeta."""
    if hint and hint in VALID_CATEGORIES:
        return hint
    d = normalize_domain(domain)
    if d in CATEGORY_MAP:
        return CATEGORY_MAP[d]
    # match por sufijo (subdominios)
    for known, cat in CATEGORY_MAP.items():
        if d.endswith("." + known):
            return cat
    return "other"


# ─────────── Curvas de score ───────────
# Devuelven 0-100 dadas minutos en categoría. Más alto = mejor para wellbeing.


def _bell(minutes: float, optimum: float, half_width: float) -> float:
    """Curva tipo campana: 100 en el óptimo, cae suave a los lados.
    half_width = distancia donde el score baja a ~50."""
    if half_width <= 0:
        return 0.0
    x = (minutes - optimum) / half_width
    val = 100.0 / (1.0 + x * x)
    return max(0.0, min(100.0, val))


def _decay_above(minutes: float, threshold: float, full_drop: float) -> float:
    """100 hasta `threshold`, luego baja linealmente y llega a 0 en `threshold + full_drop`."""
    if minutes <= threshold:
        return 100.0
    if minutes >= threshold + full_drop:
        return 0.0
    return 100.0 * (1.0 - (minutes - threshold) / full_drop)


def _growth_until(minutes: float, target: float) -> float:
    """0 con 0 minutos, 100 cuando se alcanza `target`, plateau después."""
    if target <= 0:
        return 100.0
    return max(0.0, min(100.0, (minutes / target) * 100.0))


def score_social(minutes: float) -> float:
    """Óptimo ~25min/día. 0 está bien (60), >120 está mal."""
    if minutes <= 25:
        return max(60.0, _bell(minutes, 25, 25))
    return _decay_above(minutes, 30, 120)


def score_entertainment(minutes: float) -> float:
    """Óptimo ~45min, tolerable hasta ~120, pésimo >240."""
    if minutes <= 45:
        return max(70.0, _bell(minutes, 45, 45))
    return _decay_above(minutes, 60, 240)


def score_focus_block(minutes_work: float, minutes_education: float) -> float:
    """Crece hasta 180min combinados de trabajo/estudio, plateau."""
    return _growth_until(minutes_work + minutes_education, 180)


def score_balance(minutes_by_cat: Dict[str, int]) -> float:
    """Penaliza concentración total en una sola categoría no productiva."""
    total = sum(minutes_by_cat.values()) or 1
    unproductive = (
        minutes_by_cat.get("social", 0)
        + minutes_by_cat.get("entertainment", 0)
        + minutes_by_cat.get("shopping", 0)
    )
    ratio = unproductive / total
    # 0-30% → 100, 30-70% baja, ≥70% → 20
    if ratio <= 0.30:
        return 100.0
    if ratio >= 0.80:
        return 20.0
    return 100.0 - ((ratio - 0.30) / 0.50) * 80.0


def overall_digital_score(
    s_social: float, s_focus: float, s_balance: float
) -> float:
    return round(0.35 * s_social + 0.35 * s_focus + 0.30 * s_balance, 2)


# ─────────── Agregación ───────────


def aggregate_sessions(
    sessions: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    """Devuelve (minutos_por_categoria, top_domains[10])."""
    minutes_by_cat: Dict[str, int] = defaultdict(int)
    minutes_by_domain: Counter = Counter()
    cat_by_domain: Dict[str, str] = {}

    for s in sessions:
        dom = normalize_domain(s.get("domain") or s.get("url") or "")
        if not dom:
            continue
        cat = categorize_domain(dom, s.get("category"))
        dur = int(s.get("duration_sec") or 0)
        if dur <= 0:
            continue
        # Si la sesión es de fondo / idle, la contamos a la mitad.
        if s.get("active") is False:
            dur = dur // 2
        minutes_by_cat[cat] += dur // 60
        minutes_by_domain[dom] += dur // 60
        cat_by_domain[dom] = cat

    top_domains = [
        {"domain": dom, "minutes": mins, "category": cat_by_domain.get(dom, "other")}
        for dom, mins in minutes_by_domain.most_common(10)
    ]
    return dict(minutes_by_cat), top_domains


# ─────────── Temas de búsqueda ───────────

_STOPWORDS = {
    "a", "al", "como", "con", "cual", "de", "del", "el", "en", "es", "esta",
    "este", "for", "in", "is", "la", "las", "lo", "los", "mi", "no", "o", "para",
    "por", "que", "se", "si", "su", "sus", "te", "un", "una", "uno", "unas",
    "unos", "y", "the", "to", "and", "of", "or", "i", "you", "it", "this", "that",
    "with", "on", "at", "by", "from", "be", "are", "was", "were", "what", "how",
    "why", "when", "where", "do", "does", "did", "have", "has",
}


def summarize_search_themes(
    queries: Iterable[Dict[str, Any]], top_k: int = 6
) -> List[Dict[str, Any]]:
    """Cuenta palabras clave y devuelve top temas con queries de muestra."""
    word_count: Counter = Counter()
    samples: Dict[str, List[str]] = defaultdict(list)
    for q in queries:
        text = (q.get("query") or "").lower().strip()
        if not text:
            continue
        tokens = [
            re.sub(r"[^a-záéíóúñü0-9]+", "", t)
            for t in text.split()
        ]
        tokens = [t for t in tokens if t and len(t) > 2 and t not in _STOPWORDS]
        for tok in tokens:
            word_count[tok] += 1
            if text not in samples[tok] and len(samples[tok]) < 3:
                samples[tok].append(text)
    return [
        {"theme": tok, "n": n, "sample_queries": samples[tok][:3]}
        for tok, n in word_count.most_common(top_k)
        if n >= 2
    ]


# ─────────── Cálculo end-to-end ───────────


def compute_daily_metric(
    sessions: List[Dict[str, Any]],
    queries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    minutes_by_cat, top_domains = aggregate_sessions(sessions)

    def m(cat: str) -> int:
        return int(minutes_by_cat.get(cat, 0))

    s_soc = round(score_social(m("social")), 2)
    s_focus = round(
        score_focus_block(m("work"), m("education")), 2
    )
    s_bal = round(score_balance(minutes_by_cat), 2)
    s_overall = overall_digital_score(s_soc, s_focus, s_bal)
    themes = summarize_search_themes(queries)

    return {
        "minutes_by_category": {
            "social": m("social"),
            "entertainment": m("entertainment"),
            "news": m("news"),
            "work": m("work"),
            "education": m("education"),
            "shopping": m("shopping"),
            "search": m("search"),
            "ai": m("ai"),
            "other": m("other"),
        },
        "scores": {
            "social": s_soc,
            "focus": s_focus,
            "balance": s_bal,
            "digital_overall": s_overall,
        },
        "top_domains": top_domains,
        "search_themes": themes,
        "total_minutes": sum(minutes_by_cat.values()),
    }


def metric_summary_for_llm(metric: Dict[str, Any]) -> str:
    """Convierte una métrica diaria al texto que verá el LLM recomendador."""
    if not metric:
        return "(sin datos digitales todavía)"
    mins = metric.get("minutes_by_category", {})
    scores = metric.get("scores", {})
    top = metric.get("top_domains", [])[:5]
    themes = metric.get("search_themes", [])[:4]
    lines: List[str] = []
    lines.append(
        f"Total hoy: {metric.get('total_minutes', 0)} min de navegación activa."
    )
    if scores:
        lines.append(
            "Scores 0-100 (más alto = mejor): "
            f"social {scores.get('social', '-')}, foco {scores.get('focus', '-')}, "
            f"balance {scores.get('balance', '-')}, "
            f"digital global {scores.get('digital_overall', '-')}."
        )
    cat_line = ", ".join(
        f"{k}: {v}min"
        for k, v in mins.items()
        if isinstance(v, int) and v > 0
    )
    if cat_line:
        lines.append("Minutos por categoría: " + cat_line + ".")
    if top:
        lines.append(
            "Top dominios: "
            + ", ".join(f"{d['domain']} ({d['minutes']}min)" for d in top)
            + "."
        )
    if themes:
        lines.append(
            "Temas que buscó: "
            + ", ".join(
                f"{t['theme']}×{t['n']}" for t in themes
            )
            + "."
        )
    return "\n".join(lines)
