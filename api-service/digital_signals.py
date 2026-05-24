"""Digital signals storage — receives data from the Chrome extension.

The Chrome extension sends POST /extension/ingest with domain-level
usage data. This module stores it in memory (lite version) and exposes
aggregated summaries for the orchestrator and dashboard.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

# In-memory store for lite version; replace with DB in production
_store: list[dict[str, Any]] = []

DOMAIN_CATEGORIES: dict[str, str] = {
    # Social
    "instagram.com": "social",
    "tiktok.com": "social",
    "facebook.com": "social",
    "twitter.com": "social",
    "x.com": "social",
    "snapchat.com": "social",
    "youtube.com": "social",
    "linkedin.com": "social",
    "reddit.com": "social",
    "pinterest.com": "social",
    "threads.net": "social",
    "whatsapp.com": "social",
    "telegram.org": "social",
    "t.me": "social",
    # Work / productivity
    "github.com": "work",
    "gitlab.com": "work",
    "notion.so": "work",
    "asana.com": "work",
    "linear.app": "work",
    "slack.com": "work",
    "zoom.us": "work",
    "meet.google.com": "work",
    "docs.google.com": "work",
    "sheets.google.com": "work",
    "office.com": "work",
    "figma.com": "work",
    "vercel.app": "work",
    "localhost": "work",
    "127.0.0.1": "work",
    # Entertainment
    "netflix.com": "entertainment",
    "hbomax.com": "entertainment",
    "disneyplus.com": "entertainment",
    "twitch.tv": "entertainment",
    "spotify.com": "entertainment",
    "primevideo.com": "entertainment",
    "crunchyroll.com": "entertainment",
    # News
    "cnn.com": "news",
    "bbc.com": "news",
    "eltiempo.com": "news",
    "nytimes.com": "news",
    "semana.com": "news",
    "infobae.com": "news",
    # Shopping
    "amazon.com": "shopping",
    "mercadolibre.com": "shopping",
    "ebay.com": "shopping",
    # AI
    "claude.ai": "ai",
    "chat.openai.com": "ai",
    "gemini.google.com": "ai",
    "perplexity.ai": "ai",
    # Education
    "coursera.org": "education",
    "udemy.com": "education",
    "khanacademy.org": "education",
    "medium.com": "education",
    "stackoverflow.com": "education",
    "dev.to": "education",
}


def classify_domain(domain: str) -> str:
    domain = domain.lower().lstrip("www.")
    for key, cat in DOMAIN_CATEGORIES.items():
        if domain == key or domain.endswith("." + key):
            return cat
    return "other"


def ingest(payload: dict[str, Any]) -> dict[str, Any]:
    """Store a batch of domain-time records from the extension."""
    record = {
        "ts": time.time(),
        "profile_id": payload.get("profile_id"),
        "session_id": payload.get("session_id"),
        "domains": payload.get("domains", {}),  # {domain: seconds}
        "date": payload.get("date"),
    }
    _store.append(record)
    return {"saved": True, "record_count": len(_store)}


def get_summary(profile_id: str | None = None, last_n: int = 20) -> dict[str, Any]:
    """Return aggregated digital signals. Returns minutes for dashboard compatibility."""
    # Include profile-specific records AND the demo seed (profile_id=None) as baseline
    records = [
        r for r in _store
        if profile_id is None or r.get("profile_id") == profile_id or r.get("session_id") == "demo-seed"
    ]
    if last_n:
        records = records[-last_n:]

    by_cat_sec: dict[str, float] = defaultdict(float)
    by_domain_sec: dict[str, float] = defaultdict(float)

    for rec in records:
        for domain, seconds in rec.get("domains", {}).items():
            cat = classify_domain(domain)
            by_cat_sec[cat] += float(seconds)
            clean = domain.lstrip("www.")
            by_domain_sec[clean] += float(seconds)

    total_sec = sum(by_cat_sec.values())
    total_min = round(total_sec / 60)
    total_hours = round(total_sec / 3600, 2)

    by_cat_min = {k: round(v / 60) for k, v in by_cat_sec.items()}
    top_domains = sorted(by_domain_sec.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        # Fields expected by dashboard JS
        "today_minutes": total_min,
        "today_minutes_by_cat": by_cat_min,
        "session_count": len(records),
        # Backward-compat fields for orchestrator
        "total_hours": total_hours,
        "by_category": {k: round(v / 3600, 2) for k, v in by_cat_sec.items()},
        "top_domains": [d for d, _ in top_domains],
        "top_domains_hours": {d: round(s / 3600, 2) for d, s in top_domains},
    }


def seed_demo_data() -> None:
    """Seed realistic demo data so dashboard shows content before extension connects."""
    if _store:
        return  # already has data

    demo_domains: dict[str, int] = {
        # Social (62 min total)
        "instagram.com": 2220,
        "youtube.com": 1500,
        "twitter.com": 540,
        # Work (94 min total)
        "github.com": 3240,
        "notion.so": 1440,
        "slack.com": 960,
        # Entertainment (28 min)
        "netflix.com": 1680,
        # Education (18 min)
        "stackoverflow.com": 720,
        "medium.com": 360,
        # AI (12 min)
        "claude.ai": 720,
        # News (8 min)
        "eltiempo.com": 480,
    }

    _store.append({
        "ts": time.time(),
        "profile_id": None,
        "session_id": "demo-seed",
        "domains": demo_domains,
        "date": None,
    })


# Seed demo data on import so dashboard is never empty
seed_demo_data()
