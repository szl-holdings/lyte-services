from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from lyte.app import create_app


def test_forecast_baseline_is_live_and_receipted() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/lyte/v2/forecast",
            json={
                "signal_id": "cpu.utilization",
                "values": [40.0, 41.0, 42.0, 43.0, 44.0],
                "horizon": 2,
                "quantiles": [0.1, 0.5, 0.9],
                "provider": "baseline",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_authority"] == "NONE"
    assert payload["receipt"]["provider"] == "szl.robust-drift/v1"
    assert len(payload["receipt"]["input_sha256"]) == 64
    assert len(payload["receipt"]["output_sha256"]) == 64


def test_granite_fails_closed_until_admitted() -> None:
    with patch.dict(os.environ, {"LYTE_GRANITE_ENABLED": "false"}, clear=False):
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/lyte/v2/forecast",
                json={
                    "signal_id": "cpu.utilization",
                    "values": [40.0, 41.0, 42.0, 43.0],
                    "horizon": 1,
                    "quantiles": [0.1, 0.5, 0.9],
                    "provider": "granite",
                },
            )
    assert response.status_code == 503
    assert response.json()["detail"] == "Granite provider is not admitted in this deployment"
