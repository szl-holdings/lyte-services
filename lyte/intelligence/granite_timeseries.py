"""IBM Granite Time Series adapter for Lyte Forecast Loom.

This module is an integration boundary, not a fork or rebrand. It loads the
upstream checkpoint with Granite-TSFM and translates its dataframe output into
Forecast Loom's provider-neutral quantile contract. Governance, receipts,
admission, and execution authority remain SZL-owned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MODEL_ID = "ibm-granite/granite-timeseries-patchtst-fm-r2"
MIN_GRANITE_TSFM_VERSION = "0.3.9"


class GraniteAdapterError(RuntimeError):
    """Raised when Granite cannot satisfy the Forecast Loom provider contract."""


@dataclass
class GranitePatchTSTProvider:
    """Lazy-loading PatchTST-FM-r2 provider.

    The dependency is optional so Lyte core remains light. Install
    ``granite-tsfm>=0.3.9`` and pandas only in the evaluation/runtime lane that
    elects to use this provider.
    """

    model_id: str = MODEL_ID
    context_length: int = 512
    frequency: str = "1h"
    name: str = "hf.ibm-granite.patchtst-fm-r2"

    def __post_init__(self) -> None:
        if self.context_length < 16 or self.context_length > 8192:
            raise GraniteAdapterError("context_length must be in [16, 8192]")
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from tsfm_public import PatchTSTFMForPrediction
        except ImportError as exc:
            raise GraniteAdapterError(
                "Granite adapter requires granite-tsfm>=0.3.9"
            ) from exc
        self._model = PatchTSTFMForPrediction.from_pretrained(self.model_id)
        return self._model

    def forecast(
        self,
        values: Sequence[float],
        *,
        horizon: int,
        quantiles: Sequence[float],
    ) -> Sequence[dict[float, float]]:
        try:
            import pandas as pd
            from tsfm_public import TimeSeriesForecastingPipeline
        except ImportError as exc:
            raise GraniteAdapterError(
                "Granite adapter requires pandas and granite-tsfm>=0.3.9"
            ) from exc

        model = self._load()
        context = list(values[-min(len(values), self.context_length) :])
        start = datetime(2026, 1, 1, tzinfo=UTC)
        frame = pd.DataFrame(
            {
                "timestamp": [start + timedelta(hours=i) for i in range(len(context))],
                "value": context,
            }
        )
        pipe = TimeSeriesForecastingPipeline(
            model=model,
            id_columns=[],
            timestamp_column="timestamp",
            target_columns=["value"],
            max_context_length=model.config.context_length,
            context_length=len(context),
            prediction_length=horizon,
            impute_method=None,
            quantile_levels=list(quantiles),
            explode_forecasts=True,
            freq=self.frequency,
        )
        forecast = pipe(frame)
        if len(forecast) != horizon:
            raise GraniteAdapterError(
                f"Granite returned {len(forecast)} rows for horizon {horizon}"
            )

        rows: list[dict[float, float]] = []
        for _, record in forecast.iterrows():
            row: dict[float, float] = {}
            for q in quantiles:
                candidates = (
                    f"value_q{q}",
                    f"value_q{q:g}",
                    f"value_{q}",
                    f"value_{q:g}",
                    f"q{q}",
                    f"q{q:g}",
                )
                column = next((name for name in candidates if name in forecast.columns), None)
                if column is None:
                    suffixes = (str(q), f"{q:g}")
                    matches = [
                        name
                        for name in forecast.columns
                        if any(str(name).endswith(suffix) for suffix in suffixes)
                    ]
                    if len(matches) != 1:
                        raise GraniteAdapterError(
                            f"unable to resolve Granite quantile column for {q}: {matches}"
                        )
                    column = matches[0]
                row[float(q)] = float(record[column])
            rows.append(row)
        return rows
