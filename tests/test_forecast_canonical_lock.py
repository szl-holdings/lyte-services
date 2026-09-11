"""Pin the canonical Forecast Loom fixture hashes.

These digests bind content. They are not signatures, not a Hugging Face
source-revision match, and not production admission of Granite.
"""
from __future__ import annotations

from pathlib import Path
import json

from lyte.intelligence.forecast_loom import ForecastRequest, run_forecast

LOCK_PATH = Path(__file__).resolve().parents[1] / "frontier" / "forecast-loom-canonical-lock.json"
LOCK = json.loads(LOCK_PATH.read_text())


def test_canonical_publisher_fixture_hashes() -> None:
    forecast = run_forecast(
        ForecastRequest(
            signal_id="qualification.canonical-publisher",
            values=(10.0, 11.0, None, 13.0, 14.0),
            horizon=3,
            quantiles=(0.1, 0.5, 0.9),
        )
    )
    assert forecast.receipt.raw_input_sha256 == LOCK["raw_input_sha256"]
    assert forecast.receipt.input_sha256 == LOCK["input_sha256"]
    assert forecast.receipt.output_sha256 == LOCK["output_sha256"]
    assert forecast.receipt.provider == "szl.robust-drift/v1"
    assert forecast.receipt.imputed_points == 1
    assert forecast.receipt.contract == "szl.lyte.forecast-loom/v1"


def test_canonical_lock_file_is_self_consistent() -> None:
    assert LOCK["schema"] == "szl.lyte.forecast-canonical-lock/v1"
    assert LOCK["publisher_pin"] == "dd17d9f524b76c8f0e260d7ec1e084cc079dfc43"
    assert LOCK["execution_authority"] == "NONE"
    assert len(LOCK["raw_input_sha256"]) == 64
