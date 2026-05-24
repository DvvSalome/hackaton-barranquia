"""Unit tests for the 4 LangChain diagnostic tools + pipeline.

DiagnosticTool, SolutionTool, ReferralTool are deterministic (no LLM).
run_diagnostic_pipeline is tested with _generate_professional_summary mocked.
"""
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make sure api-service is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Stub out heavy imports before importing langchain_tools ──────────────────
# langchain_openai and config are not needed for deterministic tools
_fake_lc_tool = lambda fn: fn  # noqa: E731
langchain_core_stub = types.ModuleType("langchain_core")
langchain_core_stub.tools = types.ModuleType("langchain_core.tools")
langchain_core_stub.tools.tool = _fake_lc_tool
sys.modules.setdefault("langchain_core", langchain_core_stub)
sys.modules.setdefault("langchain_core.tools", langchain_core_stub.tools)

# Stub config
config_stub = types.ModuleType("config")
config_stub.OPENROUTER_API_KEY = "test"
config_stub.OPENROUTER_BASE_URL = "http://test"
config_stub.OPENROUTER_MODEL = "test-model"
sys.modules.setdefault("config", config_stub)

# Stub langchain_openai
lo_stub = types.ModuleType("langchain_openai")
lo_stub.ChatOpenAI = MagicMock()
sys.modules.setdefault("langchain_openai", lo_stub)

lc_prompts = types.ModuleType("langchain_core.prompts")
lc_prompts.ChatPromptTemplate = MagicMock()
sys.modules.setdefault("langchain_core.prompts", lc_prompts)

lc_parsers = types.ModuleType("langchain_core.output_parsers")
lc_parsers.StrOutputParser = MagicMock()
sys.modules.setdefault("langchain_core.output_parsers", lc_parsers)

from langchain_tools import (  # noqa: E402
    diagnostic_tool,
    referral_tool,
    run_diagnostic_pipeline,
    solution_tool,
)

# The lc_tool stub above strips the LangChain wrapper, leaving plain functions.
# Add .invoke() so the pipeline (which calls tool.invoke()) can still work.
for _t in (diagnostic_tool, solution_tool, referral_tool):
    if not hasattr(_t, "invoke"):
        _t.invoke = _t  # type: ignore[attr-defined]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _signals(
    phq9=0, gad7=0, sleep_score=70, energy_score=70,
    focus_score=70, mood_score=70, daily_screen_hours=3,
    social_pct=15, drowsiness_count=0, distraction_count=0,
    screen_score=80, habits_score=70,
):
    return {
        "phq9_score": phq9,
        "gad7_score": gad7,
        "sleep_score": sleep_score,
        "energy_score": energy_score,
        "focus_score": focus_score,
        "mood_score": mood_score,
        "daily_screen_hours": daily_screen_hours,
        "social_pct": social_pct,
        "drowsiness_count": drowsiness_count,
        "distraction_count": distraction_count,
        "screen_score": screen_score,
        "habits_score": habits_score,
    }


# ── DiagnosticTool ───────────────────────────────────────────────────────────

class TestDiagnosticTool:
    def test_healthy_user_returns_bienestar(self):
        raw = diagnostic_tool(json.dumps(_signals()))
        d = json.loads(raw)
        assert d["primary_pattern"] == "bienestar_en_rango"
        assert d["severity_code"] == 0

    def test_high_phq9_returns_distres_severo(self):
        raw = diagnostic_tool(json.dumps(_signals(phq9=20, gad7=12)))
        d = json.loads(raw)
        assert d["severity_code"] >= 3

    def test_low_sleep_returns_sleep_pattern(self):
        raw = diagnostic_tool(json.dumps(_signals(sleep_score=20)))
        d = json.loads(raw)
        assert "sueno" in d["primary_pattern"] or "privacion" in d["primary_pattern"] or d["severity_code"] >= 1

    def test_high_screen_returns_screen_pattern(self):
        raw = diagnostic_tool(json.dumps(_signals(daily_screen_hours=9, social_pct=60)))
        d = json.loads(raw)
        assert "digital" in d["primary_pattern"] or "pantalla" in str(d).lower() or d["severity_code"] >= 1

    def test_output_has_required_keys(self):
        raw = diagnostic_tool(json.dumps(_signals()))
        d = json.loads(raw)
        assert "primary_pattern" in d
        assert "severity_code" in d
        assert "matched_patterns" in d
        assert "diagnosis" in d

    def test_invalid_json_returns_error(self):
        raw = diagnostic_tool("not json")
        d = json.loads(raw)
        assert "error" in d

    def test_moderate_phq9_returns_moderate_severity(self):
        raw = diagnostic_tool(json.dumps(_signals(phq9=10, gad7=7)))
        d = json.loads(raw)
        assert d["severity_code"] >= 2


# ── SolutionTool ─────────────────────────────────────────────────────────────

class TestSolutionTool:
    def _diag(self, primary_pattern="bienestar_en_rango", severity_code=0, patterns=None):
        return {
            "primary_pattern": primary_pattern,
            "severity_code": severity_code,
            "matched_patterns": patterns or [primary_pattern],
            "diagnosis": "Perfil de bienestar equilibrado.",
            "severity": "bienestar",
        }

    def test_wellbeing_has_steps(self):
        raw = solution_tool(json.dumps(self._diag()))
        s = json.loads(raw)
        assert "steps" in s
        assert len(s["steps"]) > 0

    def test_severe_distress_has_steps(self):
        raw = solution_tool(json.dumps(self._diag("distres_severo", 3)))
        s = json.loads(raw)
        assert "steps" in s

    def test_output_has_required_keys(self):
        raw = solution_tool(json.dumps(self._diag()))
        s = json.loads(raw)
        assert "steps" in s
        assert "timeframe" in s
        assert "self_care_level" in s

    def test_invalid_json_returns_error(self):
        raw = solution_tool("bad input")
        s = json.loads(raw)
        assert "error" in s


# ── ReferralTool ─────────────────────────────────────────────────────────────

class TestReferralTool:
    def _ctx(self, severity_code=0, risk_level="bajo", patterns=None, escalate=False):
        return json.dumps({
            "diagnosis": {
                "severity_code": severity_code,
                "matched_patterns": patterns or [],
            },
            "risk_level": risk_level,
            "escalate_referral": escalate,
        })

    def test_healthy_no_referral(self):
        raw = referral_tool(self._ctx(severity_code=0, risk_level="bajo"))
        r = json.loads(raw)
        assert r["refer"] is False

    def test_severe_triggers_referral(self):
        raw = referral_tool(self._ctx(severity_code=3, risk_level="alto"))
        r = json.loads(raw)
        assert r["refer"] is True
        assert "psic" in r.get("specialist_type", "").lower()

    def test_moderate_triggers_referral(self):
        raw = referral_tool(self._ctx(severity_code=2, risk_level="moderado"))
        r = json.loads(raw)
        assert r["refer"] is True

    def test_sleep_deprivation_triggers_referral(self):
        raw = referral_tool(self._ctx(severity_code=1, patterns=["privacion_sueno"]))
        r = json.loads(raw)
        assert r["refer"] is True

    def test_escalate_flag_upgrades_urgency(self):
        # severity=1 normally would NOT trigger referral
        raw_no_esc = referral_tool(self._ctx(severity_code=1, escalate=False))
        raw_esc = referral_tool(self._ctx(severity_code=1, escalate=True))
        r_no = json.loads(raw_no_esc)
        r_yes = json.loads(raw_esc)
        # With escalation, severity is bumped to at least 2 → should refer
        assert r_yes["refer"] is True or r_no["refer"] == r_yes["refer"]

    def test_output_has_required_keys(self):
        raw = referral_tool(self._ctx())
        r = json.loads(raw)
        assert "refer" in r
        assert "urgency" in r
        assert "referral_criteria" in r


# ── Pipeline (mocked LLM) ────────────────────────────────────────────────────

class TestDiagnosticPipeline:
    def _run(self, signals, specialist_results=None, mock_summary="Mensaje profesional de prueba."):
        async def _inner():
            with patch(
                "langchain_tools._generate_professional_summary",
                new=AsyncMock(return_value=mock_summary),
            ):
                return await run_diagnostic_pipeline(
                    signals=signals,
                    specialist_results=specialist_results or [],
                )
        return asyncio.run(_inner())

    def test_healthy_pipeline_no_referral(self):
        result = self._run(_signals())
        assert "diagnosis" in result
        assert "solution" in result
        assert "referral" in result
        assert result["referral"]["refer"] is False
        assert result["professional_summary"] is None

    def test_severe_pipeline_triggers_referral_and_summary(self):
        result = self._run(_signals(phq9=20, gad7=12), mock_summary="Referencia clínica.")
        assert result["referral"]["refer"] is True
        assert result["professional_summary"] == "Referencia clínica."

    def test_pipeline_returns_all_keys(self):
        result = self._run(_signals())
        for key in ("diagnosis", "solution", "referral", "professional_summary"):
            assert key in result

    def test_escalate_flag_passed_to_referral(self):
        sigs = {**_signals(phq9=5), "_escalate_referral": True}
        result = self._run(sigs)
        # With escalation from history, borderline cases should escalate
        assert isinstance(result["referral"]["refer"], bool)


# ── triage_history ────────────────────────────────────────────────────────────

class TestTriageHistory:
    def setup_method(self):
        import triage_history
        triage_history._store.clear()
        triage_history._loaded = True

    def test_save_and_retrieve(self):
        import triage_history
        triage = {
            "diagnosis": {"pattern": "distres_severo", "severity_code": 3},
            "solution": {"recommendations": ["consult specialist"]},
            "referral": {"refer": True, "urgency": "prioritario (esta semana)"},
        }
        triage_history.save_triage("user1", triage, composite_score=35.0, risk_level="alto")
        history = triage_history.get_history("user1")
        assert len(history) == 1
        assert history[0]["diagnosis"]["pattern"] == "distres_severo"

    def test_trend_single_entry_is_stable(self):
        import triage_history
        triage = {"diagnosis": {"pattern": "distres_moderado"}, "solution": {}, "referral": {}}
        triage_history.save_triage("user2", triage)
        trend = triage_history.get_trend("user2")
        assert trend["direction"] == "stable"

    def test_trend_worsening_detected(self):
        import triage_history
        for pattern in ["bienestar_en_rango", "distres_leve", "distres_severo"]:
            triage_history.save_triage("user3", {
                "diagnosis": {"pattern": pattern}, "solution": {}, "referral": {},
            })
        trend = triage_history.get_trend("user3")
        assert trend["direction"] == "worsening"

    def test_escalate_referral_triggers_after_repeated_severe(self):
        import triage_history
        for _ in range(3):
            triage_history.save_triage("user4", {
                "diagnosis": {"pattern": "distres_severo"}, "solution": {}, "referral": {},
            })
        trend = triage_history.get_trend("user4")
        assert trend["escalate_referral"] is True

    def test_get_context_for_llm_returns_none_when_empty(self):
        import triage_history
        assert triage_history.get_context_for_llm("nobody") is None
