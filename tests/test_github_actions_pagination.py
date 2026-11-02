"""A complete Actions observation requires distinct runs and consistent counts."""

from __future__ import annotations

import httpx
import pytest

from lyte.connectors import ConnectorState, GitHubActionsConnector, GitHubActionsLimits
from lyte.domain import TruthLabel


def _run(run_id: int) -> dict:
    return {
        "id": run_id,
        "name": "CI",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "event": "push",
        "head_sha": "a" * 40,
        "created_at": "2026-09-04T12:00:00Z",
        "updated_at": "2026-09-04T12:01:30Z",
    }


def _fetch(pages: list[dict], *, per_page: int = 2, max_pages: int = 3):
    requested = []

    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested.append(page)
        assert page <= len(pages), "unexpected retry or extra page"
        return httpx.Response(200, json=pages[page - 1])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = GitHubActionsConnector(
            {"szl-holdings/lyte-services"},
            client=client,
            limits=GitHubActionsLimits(per_page=per_page, max_pages=max_pages),
        ).fetch_workflow_runs("szl-holdings/lyte-services")
    return result, requested


@pytest.mark.parametrize(
    "pages",
    [
        [{"total_count": 2, "workflow_runs": [_run(1), _run(1)]}],
        [
            {"total_count": 4, "workflow_runs": [_run(1), _run(2)]},
            {"total_count": 4, "workflow_runs": [_run(2), _run(3)]},
        ],
        [
            {"total_count": 4, "workflow_runs": [_run(1), _run(2)]},
            {"total_count": 4, "workflow_runs": [{**_run(2), "conclusion": "failure"}, _run(3)]},
        ],
        [{"total_count": 0, "workflow_runs": [_run(1)]}],
        [{"total_count": 1, "workflow_runs": [_run(1), _run(2)]}],
        [
            {"total_count": 3, "workflow_runs": [_run(1), _run(2)]},
            {"total_count": 3, "workflow_runs": [_run(3), _run(4)]},
        ],
        [{"total_count": True, "workflow_runs": [_run(1)]}],
        [{"total_count": False, "workflow_runs": []}],
    ],
    ids=[
        "duplicate-within-page", "overlapping-pages", "changed-duplicate",
        "zero-with-run", "overfull-first-page", "overfull-final-page", "true-count", "false-count",
    ],
)
def test_inconsistent_census_is_unavailable(pages: list[dict]) -> None:
    result, requested = _fetch(pages)
    assert result.state is ConnectorState.UNAVAILABLE
    assert result.reason == "transport_or_payload_failure"
    assert result.complete is False
    assert result.runs == ()
    assert result.receipt.truth_label is TruthLabel.UNAVAILABLE
    assert result.receipt.payload["run_count"] == 0
    assert result.receipt.payload["complete"] is False
    assert requested == list(range(1, len(pages) + 1))


def test_distinct_pages_form_a_complete_census() -> None:
    result, requested = _fetch([
        {"total_count": 3, "workflow_runs": [_run(1), _run(2)]},
        {"total_count": 3, "workflow_runs": [_run(3)]},
    ])
    assert result.state is ConnectorState.OBSERVED
    assert result.complete is True
    assert [run.run_id for run in result.runs] == [1, 2, 3]
    assert result.receipt.payload["run_count"] == 3
    assert requested == [1, 2]


def test_page_budget_remains_an_honest_partial_observation() -> None:
    result, requested = _fetch(
        [{"total_count": 3, "workflow_runs": [_run(1), _run(2)]}], max_pages=1,
    )
    assert result.state is ConnectorState.OBSERVED_PARTIAL
    assert result.complete is False
    assert [run.run_id for run in result.runs] == [1, 2]
    assert result.receipt.payload["run_count"] == 2
    assert requested == [1]


def test_empty_census_remains_complete() -> None:
    result, requested = _fetch([{"total_count": 0, "workflow_runs": []}])
    assert result.state is ConnectorState.OBSERVED
    assert result.complete is True
    assert result.runs == ()
    assert requested == [1]
