from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_FALLBACKS: list[str] = [
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
    "minimax/minimax-m2.5:free",
]

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

CV_SERVICE_URL: str = os.getenv("CV_SERVICE_URL", "http://localhost:8002")
API_SERVICE_URL: str = os.getenv("API_SERVICE_URL", "http://localhost:8001")

ML_MODEL_PATH: str = os.getenv("ML_MODEL_PATH", "ml_model/wellbeing_model.pkl")
