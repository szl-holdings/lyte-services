"""Pinned IBM Granite integration; governance and decisions remain SZL-owned."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

MODEL_ID = "ibm-granite/granite-timeseries-patchtst-fm-r2"
MIN_GRANITE_TSFM_VERSION = "0.3.9"


class GraniteAdapterError(RuntimeError):
    """Raised when Granite cannot satisfy the Forecast Loom provider contract."""


def _quantile_column(columns: Sequence[object], quantile: float) -> str:
    """Use exact known target columns, never ambiguous suffix/rounded matching."""
    candidates = (f"value_q{quantile}", f"value_{quantile}", f"q{quantile}")
    matches = [name for name in columns if name in candidates]
    if len(matches) != 1:
        raise GraniteAdapterError(f"unable to resolve unique quantile column for {quantile}")
    return str(matches[0])


@dataclass(frozen=True)
class GranitePatchTSTProvider:
    """Optional challenger, pinned to an operator-selected 40-character Hub SHA.

    ``LYTE_GRANITE_REVISION`` supplies the revision when not passed explicitly.
    Selecting a revision is NOT model admission. The runtime's separate enabled
    gate remains unchanged. No mutable Hub branch or tag is accepted.
    """

    model_id: str = MODEL_ID
    context_length: int = 512
    frequency: str = "1h"
    revision: str | None = None
    name: str = field(init=False)
    _model: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.context_length, bool)
            or not isinstance(self.context_length, int)
            or not 16 <= self.context_length <= 8192
        ):
            raise GraniteAdapterError("context_length must be an integer in [16, 8192]")
        if self.model_id != MODEL_ID:
            raise GraniteAdapterError("this adapter only supports the attributed IBM checkpoint")
        revision = self.revision
        if revision is None:
            revision = os.getenv("LYTE_GRANITE_REVISION", "")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise GraniteAdapterError("Granite requires an immutable 40-character Hub revision")
        object.__setattr__(self, "revision", revision.lower())
        if not isinstance(self.frequency, str) or not self.frequency.strip():
            raise GraniteAdapterError("frequency must be a positive fixed sampling interval")
        # Existing forecast receipts already bind provider.name. Include the
        # actual checkpoint and configured preprocessing, not merely its brand.
        object.__setattr__(self, "name", (
            f"hf.{self.model_id}@{self.revision}"
            f";context={self.context_length};frequency={self.frequency}"
        ))

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from tsfm_public import PatchTSTFMForPrediction
        except ImportError as exc:
            raise GraniteAdapterError(
                "Granite adapter requires granite-tsfm>=0.3.9"
            ) from exc
        model = PatchTSTFMForPrediction.from_pretrained(self.model_id, revision=self.revision)
        object.__setattr__(self, "_model", model)
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

        context = list(values[-min(len(values), self.context_length) :])
        if not context:
            raise GraniteAdapterError("Granite context cannot be empty")
        try:
            offset = pd.tseries.frequencies.to_offset(self.frequency)
            if offset.nanos <= 0:
                raise ValueError("non-positive frequency")
            timestamps = pd.date_range(
                start=datetime(2026, 1, 1, tzinfo=UTC), periods=len(context), freq=offset
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise GraniteAdapterError(
                "frequency must be a positive fixed sampling interval"
            ) from exc
        # Synthetic index only: values must already be regularly sampled in
        # this frequency. This adapter does not claim original event timestamps.
        frame = pd.DataFrame({"timestamp": timestamps, "value": context})
        model = self._load()
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
        columns = {q: _quantile_column(list(forecast.columns), q) for q in quantiles}
        if len(set(columns.values())) != len(quantiles):
            raise GraniteAdapterError("Granite quantiles must map to distinct columns")
        return [
            {float(q): float(record[column]) for q, column in columns.items()}
            for _, record in forecast.iterrows()
        ]
