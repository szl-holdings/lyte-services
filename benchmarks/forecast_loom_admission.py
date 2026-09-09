#!/usr/bin/env python3
"""Reproducible admission benchmark for Forecast Loom providers.

Runs deterministic operational workloads plus optional user-supplied JSON
series. A candidate is admitted only when it improves aggregate MAE over the
SZL robust-drift baseline, preserves Forecast Loom's quantile contract, and
returns deterministic receipts for identical requests.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import asdict
from pathlib import Path

from lyte.intelligence.forecast_loom import ForecastRequest, RobustDriftProvider, run_forecast


def workloads(seed: int = 17) -> dict[str, list[float]]:
    rng = random.Random(seed)
    n = 768
    return {
        "cpu_utilization": [45 + 12 * math.sin(i / 18) + rng.gauss(0, 1.5) for i in range(n)],
        "transaction_rate": [900 + i * 0.35 + 70 * math.sin(i / 24) + rng.gauss(0, 8) for i in range(n)],
        "incident_volume": [max(0.0, 4 + 2 * math.sin(i / 35) + rng.gauss(0, 0.7)) for i in range(n)],
        "capacity": [60 + i * 0.03 + 4 * math.sin(i / 48) for i in range(n)],
        "cloud_cost": [1500 + i * 0.6 + 130 * math.sin(i / 72) + rng.gauss(0, 12) for i in range(n)],
        "business_kpi": [100 + 8 * math.sin(i / 14) + 0.02 * i + rng.gauss(0, 1.2) for i in range(n)],
    }


def mae(actual: list[float], forecast: list[float]) -> float:
    return statistics.fmean(abs(a - b) for a, b in zip(actual, forecast, strict=True))


def evaluate_provider(provider, series: dict[str, list[float]], horizon: int) -> dict:
    rows = []
    for name, values in series.items():
        context, truth = values[:-horizon], values[-horizon:]
        req = ForecastRequest(signal_id=name, values=tuple(context), horizon=horizon, quantiles=(0.1, 0.5, 0.9))
        result = run_forecast(req, provider)
        repeated = run_forecast(req, provider)
        medians = [point.quantiles["q50"] for point in result.points]
        rows.append({
            "signal": name,
            "mae": mae(truth, medians),
            "deterministic_receipt": result.receipt.output_sha256 == repeated.receipt.output_sha256,
            "receipt": asdict(result.receipt),
        })
    return {
        "provider": provider.name,
        "signals": rows,
        "aggregate_mae": statistics.fmean(row["mae"] for row in rows),
        "deterministic": all(row["deterministic_receipt"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=("granite", "baseline"), default="granite")
    parser.add_argument("--horizon", type=int, default=48)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/forecast-loom-admission.json"))
    args = parser.parse_args()

    series = workloads()
    if args.input_json:
        supplied = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise SystemExit("input JSON must be an object mapping signal names to numeric arrays")
        for key, values in supplied.items():
            series[str(key)] = [float(value) for value in values]

    baseline = evaluate_provider(RobustDriftProvider(), series, args.horizon)
    if args.candidate == "baseline":
        candidate = baseline
    else:
        from lyte.intelligence.granite_timeseries import GranitePatchTSTProvider
        candidate = evaluate_provider(GranitePatchTSTProvider(context_length=512), series, args.horizon)

    relative_improvement = (
        (baseline["aggregate_mae"] - candidate["aggregate_mae"]) / baseline["aggregate_mae"]
        if baseline["aggregate_mae"] else 0.0
    )
    admitted = bool(candidate["deterministic"] and relative_improvement > 0.0)
    report = {
        "schema": "szl.lyte.forecast-admission/v1",
        "baseline": baseline,
        "candidate": candidate,
        "relative_mae_improvement": relative_improvement,
        "admission": "ADMITTED_FOR_EVALUATION" if admitted else "NOT_ADMITTED",
        "production_authority": "NONE",
        "limitations": [
            "Synthetic workloads are qualification evidence, not production telemetry.",
            "Production admission requires representative Lyte telemetry and deployment SLO evidence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if admitted or args.candidate == "baseline" else 2


if __name__ == "__main__":
    raise SystemExit(main())
