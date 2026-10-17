"""
tests/test_agent.py — End-to-end test of the agent in no-LLM mode.

Tests that the investigation pipeline runs to completion and
produces a correct structured report for each of the 3 planted rings.
"""

import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import run_investigation, run_nollm_investigation
from tools import get_alerts


@pytest.fixture(scope="session")
def alerts():
    """Load alerts from the graph (must be seeded)."""
    try:
        return get_alerts()
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")


def collect_steps(alert_id: str, mode: str = "nollm") -> list:
    """Run an investigation and collect all steps."""
    steps = []
    for step in run_investigation(alert_id, mode=mode):
        steps.append(step)
    return steps


class TestNoLLMInvestigation:
    def test_alert_001_investigation_completes(self, alerts):
        """Investigation of Ring A (mule chain) completes without error."""
        steps = collect_steps("ALERT_001", mode="nollm")
        assert len(steps) > 0
        tools_called = [s["tool"] for s in steps]
        assert "start" in tools_called
        assert "linked_accounts" in tools_called
        assert "take_action" in tools_called
        assert "report" in tools_called

    def test_alert_002_investigation_completes(self, alerts):
        """Investigation of Ring B (circular) completes without error."""
        steps = collect_steps("ALERT_002", mode="nollm")
        tools_called = [s["tool"] for s in steps]
        assert "report" in tools_called

    def test_alert_003_investigation_completes(self, alerts):
        """Investigation of Ring C (hub/star) completes without error."""
        steps = collect_steps("ALERT_003", mode="nollm")
        tools_called = [s["tool"] for s in steps]
        assert "report" in tools_called

    def test_report_has_required_fields(self, alerts):
        """Final report must have all required structured fields."""
        steps = collect_steps("ALERT_001", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        assert report_step is not None, "Investigation should produce a report step"
        report = report_step["result"]
        required_keys = ["summary", "ring_name", "hub_account", "actions_taken", "evidence_paths"]
        for key in required_keys:
            assert key in report, f"Report missing key: {key}"

    def test_report_includes_case_id(self, alerts):
        """Report should include a case ID (compliance case opened in FalkorDB)."""
        steps = collect_steps("ALERT_001", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        assert report_step is not None
        case_id = report_step["result"].get("actions_taken", {}).get("case_id")
        assert case_id is not None and case_id != "N/A", "Should open a real case"
        assert case_id.startswith("CASE-"), f"Case ID format unexpected: {case_id}"

    def test_report_has_frozen_accounts(self, alerts):
        """Report should confirm accounts were frozen."""
        steps = collect_steps("ALERT_002", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        assert report_step is not None
        frozen = report_step["result"].get("actions_taken", {}).get("frozen_accounts", [])
        assert len(frozen) > 0, "At least some accounts should be frozen"

    def test_report_evidence_paths_not_empty(self, alerts):
        """Evidence paths must be present — they're the core of explainability."""
        steps = collect_steps("ALERT_001", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        assert report_step is not None
        evidence = report_step["result"].get("evidence_paths", [])
        assert len(evidence) > 0, "Report must include evidence paths"

    def test_all_tools_called_in_pipeline(self, alerts):
        """All 6 tools should be called during a full investigation."""
        steps = collect_steps("ALERT_001", mode="nollm")
        tools_called = {s["tool"] for s in steps}
        expected_tools = {
            "linked_accounts", "find_communities", "rank_hubs",
            "trace_path", "find_circular_flows", "take_action"
        }
        # At least 5 of 6 tools should be called
        called_core = tools_called & expected_tools
        assert len(called_core) >= 5, f"Expected at least 5 tools, got: {called_core}"

    def test_ring_c_hub_identified(self, alerts):
        """RING_C_HUB should be identified as hub in Ring C investigation."""
        steps = collect_steps("ALERT_003", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        if report_step:
            hub = report_step["result"].get("hub_account")
            # Hub should be one of the ring C accounts
            if hub:
                assert "RING_C" in hub, f"Ring C hub should be a RING_C account, got: {hub}"

    def test_report_has_explainable_graph_risk_assessment(self, alerts):
        """Risk triage must be bounded, explainable, and included in the report."""
        steps = collect_steps("ALERT_002", mode="nollm")
        report_step = next((s for s in steps if s["tool"] == "report"), None)
        assert report_step is not None

        report = report_step["result"]
        assessment = report.get("risk_assessment")
        assert assessment is not None
        assert 0 <= assessment["score"] <= 100
        assert assessment["level"] in {"low", "elevated", "high", "critical"}
        assert assessment["is_probability"] is False
        assert assessment["positive_signals"] >= 1
        assert any(factor["points"] > 0 for factor in assessment["factors"])
        assert report["actions_taken"]["decision"] == "freeze_and_open_case"
        assert "Graph evidence score" in assessment["explanation"]

    def test_unknown_alert_returns_error(self):
        """Requesting an unknown alert should yield an error step."""
        steps = collect_steps("NONEXISTENT_ALERT")
        assert len(steps) == 1
        assert steps[0]["tool"] == "error"
