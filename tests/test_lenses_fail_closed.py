# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from lyte.api.routes_catalog import catalog
from lyte_engine.service import derive_lenses


class _Runtime:
    demo_mode = True


class _App:
    state = type("State", (), {"runtime": _Runtime()})()


class _Request:
    app = _App()


def test_derived_lenses_are_blocked_without_measured_services() -> None:
    lenses = derive_lenses(
        services=[{"lambda_advisory": {"score": 0.91}, "business": {"revenue_at_risk_usd": 12}, "slo": {"burn_rate": 0.1}}],
        outcomes=[{"attainment": 0.9}],
        trace_summary={"success_rate": 0.99},
        graph_edges=[{"from": "a", "to": "b"}],
        action_queue=[],
    )
    assert lenses
    assert {row["status"] for row in lenses} == {"BLOCKED"}
    assert "HEALTHY" not in {row["status"] for row in lenses}
    assert {row["truth_label"] for row in lenses} == {"MODELED"}


def test_derived_lenses_are_measured_only_from_measured_services() -> None:
    lenses = derive_lenses(
        services=[{
            "truth_label": "MEASURED",
            "lambda_advisory": {"score": 0.91},
            "business": {"revenue_at_risk_usd": 12},
            "slo": {"burn_rate": 0.1},
        }],
        outcomes=[{"attainment": 0.9}],
        trace_summary={"success_rate": 0.99},
        graph_edges=[{"from": "a", "to": "b"}],
        action_queue=[],
    )
    assert {row["status"] for row in lenses} == {"MEASURED"}
    assert {row["truth_label"] for row in lenses} == {"MEASURED"}


def test_catalog_operating_lenses_stay_blocked_in_demo() -> None:
    body = catalog(_Request())
    assert {row["id"] for row in body["lenses"]} == {
        "service", "journey", "business", "agent", "delivery", "decision"
    }
    assert {row["status"] for row in body["lenses"]} == {"BLOCKED"}
    assert {row["truth_label"] for row in body["lenses"]} == {"SAMPLE"}
