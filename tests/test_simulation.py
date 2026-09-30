"""Scenario tests over the full simulated loop."""

from __future__ import annotations

import pytest

from app.core.config import HARD_MONTHLY_CEILING_USD, Settings
from app.simulation.runner import run_simulation


@pytest.fixture(scope="module")
def report():
    settings = Settings(_env_file=None, nexus_mode="simulation", database_url="sqlite+pysqlite:///:memory:")
    return run_simulation("sqlite+pysqlite:///:memory:", days=6, settings=settings)


def test_simulation_runs_without_credentials(report):
    assert report["objective_id"]
    assert report["cycles"]


def test_pipeline_moves_through_the_lifecycle(report):
    stages = set(report["pipeline"])
    assert stages & {"outreach", "response", "negotiation", "follow_up", "lost", "commercial_review"}
    assert report["metrics"]["opportunities"] > 0


def test_messages_are_sent_and_replies_processed(report):
    assert report["messages"].get("outbound:sent", 0) > 0
    assert report["messages"].get("inbound:processed", 0) > 0


def test_every_claim_has_an_evidence_record(report):
    assert report["evidence_records"] > 0


def test_spending_stays_inside_the_ceiling(report):
    budget = report["budget"]
    assert budget["committed_usd"] <= HARD_MONTHLY_CEILING_USD
    assert budget["remaining_usd"] >= 0
    for category in budget["categories"].values():
        assert category["used"] <= category["limit"] + 1e-9


def test_controls_actually_fire_during_a_run(report):
    assert report["escalations"] + report["blocked_actions"] > 0


def test_loop_always_terminates(report):
    for cycle in report["cycles"]:
        assert cycle["loop"]["stop_reason"] in {
            "queue_empty", "max_iterations", "max_duration", "max_cost", "max_actions",
            "no_progress", "budget_hard_stop", "budget_exhausted",
        }
