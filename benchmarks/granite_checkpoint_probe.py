#!/usr/bin/env python3
"""Measure an existing byte-verified official checkpoint offline; never download it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

from benchmarks.forecast_loom_admission import compare_providers, evaluate_provider, workloads

REVISION = "b125275f9204d37cbb81fe47b9cf5e08a521e829"
BUNDLE_SHA256 = {
    "config.json": "e66341b9bfa9f61eb38c435ec7d7aec353fad68857ae93182698c387213d662d",
    "model.safetensors": "32f7730abd711fecf5431ff0296e4d5939e876c9e8992168b5e539aa409cae6f",
    "LICENSE": "3fe50bcae6f8a3c8aac0eb641f2cdf62f066f1967a95bb4303eefd7e123ff0f1",
    "README.md": "50df22ffebb8f2e8b1a45b23e754fa7a595a6fb615e59732a8a553c4b4480b9c",
}


def verify_bundle(directory: Path) -> dict[str, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("checkpoint must be an existing regular directory")
    observed = {}
    for name, expected in BUNDLE_SHA256.items():
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("checkpoint member is missing or a symlink: " + name)
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("checkpoint member digest mismatch: " + name)
        observed[name] = actual
    return observed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing evidence: choose a new output path")
    started = time.monotonic()
    bundle = verify_bundle(args.model_dir)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    from lyte.intelligence.forecast_loom import RobustDriftProvider
    from lyte.intelligence.granite_timeseries import GranitePatchTSTProvider, MODEL_ID
    import torch
    from tsfm_public import PatchTSTFMForPrediction

    torch.manual_seed(17)
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    model = PatchTSTFMForPrediction.from_pretrained(
        str(args.model_dir), local_files_only=True, use_safetensors=True)
    model.eval()
    provider = GranitePatchTSTProvider(revision=REVISION, context_length=512)
    # The provider owns forecasting. The probe supplies the verified local model
    # to its existing cache slot so its production network loader is not called.
    object.__setattr__(provider, "_model", model)
    reports = []
    for horizon in (12, 48):
        series = workloads(seed=17)
        baseline = evaluate_provider(RobustDriftProvider(), series, horizon, 512)
        with torch.inference_mode():
            candidate = evaluate_provider(provider, series, horizon, 512)
        report = compare_providers(baseline, candidate)
        report["horizon"] = horizon
        reports.append(report)
        print(json.dumps({"phase": "horizon_completed", "horizon": horizon,
                          "admission": report["admission"]}), flush=True)
    result = {
        "schema": "szl.lyte.local-checkpoint-probe/v1",
        "execution": "LOCAL_CPU_REAL_CHECKPOINT_SYNTHETIC_INPUTS",
        "production_authority": "NONE", "model_id": MODEL_ID, "model_revision": REVISION,
        "checkpoint_bytes": bundle, "seed": 17, "context_points": 512, "reports": reports,
        "python": platform.python_version(), "elapsed_seconds": time.monotonic() - started,
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).with_name("forecast_loom_admission.py").read_bytes()).hexdigest(),
        "dependencies": {name: importlib.metadata.version(name) for name in (
            "torch", "granite-tsfm", "transformers", "pandas", "safetensors")},
        "limitations": ["The model is real; all input signals are deterministic synthetic data.",
                        "The model's Apache-2.0 license option is retained; SZL does not claim its authorship.",
                        "Observed dependencies are not a verified complete production lock.",
                        "No significance, independent replication, deployment, or production admission is established.",
                        "Measure actual worker resources separately; this probe does not enforce a memory budget."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n")
    return 0 if all(report["admission"] == "ADMITTED_FOR_EVALUATION" for report in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
