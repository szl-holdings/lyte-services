"""Scientific boundary regressions; providers below are explicit synthetic fixtures."""

import copy
import json
import math

import pytest

from benchmarks.forecast_loom_admission import (
    compare_providers,
    evaluate_provider,
    load_series,
)


class LastValueFixture:
    name = "synthetic.last-value-fixture"

    def __init__(self):
        self.contexts = []

    def forecast(self, values, *, horizon, quantiles):
        self.contexts.append(tuple(values))
        return [{q: values[-1] for q in quantiles} for _ in range(horizon)]


def measured(scale=1):
    provider = LastValueFixture()
    series = {"fixture": [float(i * scale) for i in range(32)]}
    return evaluate_provider(provider, series, 4, 16), provider


def test_providers_receive_identical_bounded_training_context():
    before, baseline = measured()
    after, candidate = measured()
    expected = tuple(float(i) for i in range(12, 28))
    assert baseline.contexts == candidate.contexts == [expected, expected]
    assert before["signals"][0]["context_points"] == 16
    assert compare_providers(before, after)["admission"] == "NOT_ADMITTED"


def test_unit_rescaling_does_not_change_normalized_loss():
    meters, _ = measured()
    millimeters, _ = measured(1000)
    assert meters["signals"][0]["mase"] == millimeters["signals"][0]["mase"]
    assert meters["raw_cross_signal_average"] is None


def test_large_unit_improvement_cannot_hide_another_signal_regression():
    baseline, _ = measured()
    first = baseline["signals"][0]
    first.update(signal="large-unit", mae=1000, mase=1000)
    second = copy.deepcopy(first)
    second.update(signal="small-unit", mae=1, mase=1)
    baseline["signals"].append(second)
    candidate = copy.deepcopy(baseline)
    candidate["signals"][0].update(mae=990, mase=990)
    candidate["signals"][1].update(mae=2, mase=2)
    # The former pooled raw loss rule accepts a reduction from 1001 to 992.
    report = compare_providers(baseline, candidate)
    assert report["admission"] == "NOT_ADMITTED"
    assert [row["improved"] for row in report["pairs"]] == [True, False]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "context", "input", "scale"])
def test_pair_coverage_and_identity_are_required(fault):
    baseline, _ = measured()
    candidate = copy.deepcopy(baseline)
    if fault == "missing":
        candidate["signals"] = []
    elif fault == "duplicate":
        candidate["signals"].append(copy.deepcopy(candidate["signals"][0]))
    elif fault == "input":
        candidate["signals"][0]["receipt"]["input_sha256"] = "0" * 64
    else:
        key = "context_points" if fault == "context" else "training_scale"
        candidate["signals"][0][key] += 1
    with pytest.raises(ValueError):
        compare_providers(baseline, candidate)


def test_nondeterministic_baseline_cannot_qualify_a_candidate():
    baseline, _ = measured()
    candidate = copy.deepcopy(baseline)
    candidate["signals"][0]["mase"] = 0
    baseline["deterministic"] = False
    assert compare_providers(baseline, candidate)["admission"] == "NOT_ADMITTED"


@pytest.mark.parametrize("values", [[1.0] * 32, [math.inf] * 32, [True] * 32, [1.0] * 19])
def test_undefined_scale_and_invalid_or_incomplete_data_hold(values):
    with pytest.raises(ValueError):
        evaluate_provider(LastValueFixture(), {"fixture": values}, 4, 16)


def test_supplied_bytes_are_hashed_and_duplicate_keys_rejected(tmp_path):
    import hashlib

    path = tmp_path / "series.json"
    raw = json.dumps({"supplied": list(range(32))}).encode()
    path.write_bytes(raw)
    supplied, digest = load_series(path)
    assert supplied["supplied"] == list(range(32))
    assert digest == hashlib.sha256(raw).hexdigest()
    path.write_bytes(b'{"x":[],"x":[]}')
    with pytest.raises(ValueError, match="duplicate"):
        load_series(path)
