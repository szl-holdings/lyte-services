#!/usr/bin/env python3
"""Reproducible admission benchmark for Forecast Loom providers.

Runs deterministic synthetic workloads plus optional supplied JSON series.
Both providers receive identical context. Qualification requires an improvement
on every signal in training-normalized units, complete paired coverage, and
deterministic receipts. This is local evaluation, not statistical certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict
from pathlib import Path

from lyte.intelligence.forecast_loom import ForecastRequest, RobustDriftProvider, run_forecast

MAX_INPUT_BYTES = 2 * 1024 * 1024


def _data_hash(context: list[float], truth: list[float]) -> str:
    raw = json.dumps({"context": context, "truth": truth}, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


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


def evaluate_provider(provider, series: dict[str, list[float]], horizon: int,
                      context_length: int = 512) -> dict:
    if type(horizon) is not int or not 1 <= horizon <= 168:
        raise ValueError("horizon must be an integer in [1, 168]")
    if type(context_length) is not int or not 16 <= context_length <= 8192:
        raise ValueError("context length must be an integer in [16, 8192]")
    if not series or len(series) > 32:
        raise ValueError("expected 1 to 32 signals")
    rows = []
    for name, values in series.items():
        if (not isinstance(name, str) or not name or len(name) > 256
                or not isinstance(values, list) or not context_length + horizon <= len(values) <= 10000
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in values)):
            raise ValueError("signal needs a bounded identifier and sufficient finite numeric values")
        context, truth = values[-horizon - context_length:-horizon], values[-horizon:]
        training_scale = statistics.fmean(abs(a - b) for a, b in zip(context[1:], context[:-1]))
        if not math.isfinite(training_scale) or training_scale <= 0:
            raise ValueError("training-only normalization scale must be positive and finite")
        req = ForecastRequest(signal_id=name, values=tuple(context), horizon=horizon, quantiles=(0.1, 0.5, 0.9))
        result = run_forecast(req, provider)
        repeated = run_forecast(req, provider)
        medians = [point.quantiles["q50"] for point in result.points]
        loss = mae(truth, medians)
        normalized = loss / training_scale
        if not math.isfinite(normalized):
            raise ValueError("normalized loss overflow")
        rows.append({
            "signal": name,
            "context_points": len(context),
            "data_sha256": _data_hash(context, truth),
            "mae": loss,
            "training_scale": training_scale,
            "mase": normalized,
            "deterministic_receipt": result.receipt.output_sha256 == repeated.receipt.output_sha256,
            "identity_repeat_output_sha256": repeated.receipt.output_sha256,
            "receipt": asdict(result.receipt),
        })
    return {
        "provider": provider.name,
        "signals": rows,
        "raw_cross_signal_average": None,
        "deterministic": all(row["deterministic_receipt"] for row in rows),
    }


def compare_providers(baseline: dict, candidate: dict) -> dict:
    before = {row["signal"]: row for row in baseline["signals"]}
    after = {row["signal"]: row for row in candidate["signals"]}
    if (len(before) != len(baseline["signals"]) or len(after) != len(candidate["signals"])
            or not before or set(before) != set(after)):
        raise ValueError("paired signal coverage must be complete and unique")
    pairs = []
    for name, reference in before.items():
        treatment = after[name]
        if any(reference[key] != treatment[key] for key in (
                "data_sha256", "training_scale", "context_points")):
            raise ValueError("providers were not measured on identical context, truth, and scale")
        if reference["receipt"]["input_sha256"] != treatment["receipt"]["input_sha256"]:
            raise ValueError("paired request identity differs")
        delta = reference["mase"] - treatment["mase"]
        if not math.isfinite(delta):
            raise ValueError("normalized paired improvement overflow")
        pairs.append({"signal": name, "normalized_improvement": delta,
                      "baseline_mase": reference["mase"], "candidate_mase": treatment["mase"],
                      "improved": delta > 0})
    qualified = baseline["deterministic"] and candidate["deterministic"] and all(
        row["improved"] for row in pairs)
    return {"schema": "szl.lyte.forecast-admission/v2", "baseline": baseline,
            "candidate": candidate, "pairs": pairs,
            "admission": "ADMITTED_FOR_EVALUATION" if qualified else "NOT_ADMITTED",
            "production_authority": "NONE", "statistical_independence_verified": False,
            "limitations": [
                "Synthetic workloads are local comparison evidence, not production telemetry.",
                "No significance test, independent replication, or generalization claim is established.",
                "Production admission requires representative telemetry and deployment SLO evidence."]}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_series(path: Path) -> tuple[dict[str, list[float]], str]:
    with path.open("rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("supplied series exceed 2 MiB")
    supplied = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(supplied, dict) or not supplied or len(supplied) > 26:
        raise ValueError("supplied series must map 1 to 26 signal identifiers to numeric arrays")
    return supplied, hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=("granite", "baseline"), default="granite")
    parser.add_argument("--horizon", type=int, default=48)
    parser.add_argument("--context-length", type=int, default=512)
    parser.add_argument("--revision", help="Immutable Granite revision; defaults to LYTE_GRANITE_REVISION")
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/forecast-loom-admission.json"))
    args = parser.parse_args()

    series = workloads()
    try:
        input_hash = None
        if args.input_json:
            supplied, input_hash = load_series(args.input_json)
            if set(supplied) & set(series):
                raise ValueError("supplied identifiers must not replace synthetic workload identifiers")
            series.update(supplied)
        baseline = evaluate_provider(RobustDriftProvider(), series, args.horizon, args.context_length)
        if args.candidate == "baseline":
            candidate = baseline
        else:
            from lyte.intelligence.granite_timeseries import GranitePatchTSTProvider
            provider = GranitePatchTSTProvider(context_length=args.context_length, revision=args.revision)
            candidate = evaluate_provider(provider, series, args.horizon, args.context_length)
        report = compare_providers(baseline, candidate)
        report["input_scope"] = "MIXED_SYNTHETIC_AND_SUPPLIED" if input_hash else "SYNTHETIC"
        report["supplied_input_sha256"] = input_hash
        report["benchmark_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except (ValueError, TypeError, OverflowError, RecursionError, OSError) as exc:
        print(json.dumps({"admission": "INVALID_INPUT", "production_authority": "NONE",
                          "error": str(exc)}, allow_nan=False))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["admission"] == "ADMITTED_FOR_EVALUATION" or args.candidate == "baseline" else 2


if __name__ == "__main__":
    raise SystemExit(main())
