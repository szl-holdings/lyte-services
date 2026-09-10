"""Governed forecasting API for Lyte."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lyte.intelligence.forecast_loom import (
    MAX_QUANTILES,
    ForecastError,
    ForecastRequest,
    run_forecast,
)

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


class ForecastBody(BaseModel):
    signal_id: str = Field(min_length=1, max_length=256)
    values: list[float | None] = Field(min_length=1, max_length=8192)
    horizon: int = Field(ge=1, le=1024)
    quantiles: list[float] = Field(
        default_factory=lambda: [0.1, 0.5, 0.9], min_length=1, max_length=MAX_QUANTILES
    )
    provider: Literal["baseline", "granite"] = "baseline"


def _provider(name: str):
    if name == "baseline":
        return None
    if os.getenv("LYTE_GRANITE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=503,
            detail="Granite provider is not admitted in this deployment",
        )
    try:
        from lyte.intelligence.granite_timeseries import GranitePatchTSTProvider

        return GranitePatchTSTProvider()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Granite provider unavailable") from exc


@router.post("")
def forecast(body: ForecastBody) -> dict[str, object]:
    """Return an advisory forecast with deterministic proof receipt."""
    try:
        result = run_forecast(
            ForecastRequest(
                signal_id=body.signal_id,
                values=tuple(body.values),
                horizon=body.horizon,
                quantiles=tuple(body.quantiles),
            ),
            provider=_provider(body.provider),
        )
    except ForecastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Lazy provider loading occurs during forecast(), not construction.
        # Do not expose model/dependency internals as an unhandled API error.
        if body.provider != "granite":
            raise
        raise HTTPException(status_code=503, detail="Granite provider unavailable") from exc
    return {
        "points": [
            {"step": point.step, "quantiles": point.quantiles}
            for point in result.points
        ],
        "receipt": {
            "contract": result.receipt.contract,
            "signal_id": result.receipt.signal_id,
            "provider": result.receipt.provider,
            "context_points": result.receipt.context_points,
            "imputed_points": result.receipt.imputed_points,
            "horizon": result.receipt.horizon,
            "quantiles": result.receipt.quantiles,
            "input_sha256": result.receipt.input_sha256,
            "output_sha256": result.receipt.output_sha256,
            "raw_input_sha256": result.receipt.raw_input_sha256,
            "original_context_points": result.receipt.original_context_points,
            "truncated_points": result.receipt.truncated_points,
            "confidence": result.receipt.confidence,
            "limitations": result.receipt.limitations,
        },
        "execution_authority": "NONE",
    }
