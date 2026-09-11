"""Pure presentation-envelope tests; no network, weights, or model claims."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from lyte.intelligence.forecast_inspection import inspection_envelope

REVISION = "a" * 40  # Synthetic source identity for contract tests, not release evidence.


def inputs():
    return {"signal_id": "test.λ", "values": [1.0, None, -0.0], "provider": "baseline"}


def forecast():
    return {
        "execution_authority": "NONE",
        "risk_window": {
            "execution_authority": "NONE", "production_admitted": False,
            "calibration_status": "NOT_ESTABLISHED",
        },
    }


def test_exact_python_float_and_unicode_bytes_are_preserved():
    envelope = inspection_envelope(inputs(), forecast(), revision=REVISION)
    canonical = envelope["canonical_json"]
    assert "1.0" in canonical and "-0.0" in canonical and "λ" in canonical
    assert envelope["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    body = json.loads(canonical)
    assert body["request"] == inputs()
    assert body["persisted"] is False
    assert body["source"]["revision"] == REVISION
    assert body["input_provenance"] == "CALLER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED"
    assert envelope["hash_semantics"] == "CONTENT_INTEGRITY_NOT_SIGNATURE_OR_ACCURACY"


def test_envelope_is_deterministic_and_binds_both_request_and_source():
    first = inspection_envelope(inputs(), forecast(), revision=REVISION)
    assert first == inspection_envelope(inputs(), forecast(), revision=REVISION)
    changed = {**inputs(), "signal_id": "other"}
    assert first["sha256"] != inspection_envelope(changed, forecast(), revision=REVISION)["sha256"]
    assert first["sha256"] != inspection_envelope(inputs(), forecast(), revision="b" * 40)["sha256"]


@pytest.mark.parametrize("revision", [None, "", "main", "abc123", "A" * 40, "z" * 40])
def test_unbound_or_malformed_source_is_rejected(revision):
    with pytest.raises(ValueError, match="source revision"):
        inspection_envelope(inputs(), forecast(), revision=revision)


@pytest.mark.parametrize("field,value", [
    ("execution_authority", "EXECUTE"), ("production_admitted", True),
    ("production_admitted", 0), ("calibration_status", "CALIBRATED"),
])
def test_risk_authority_cannot_be_amplified(field, value):
    result = forecast()
    result["risk_window"][field] = value
    with pytest.raises(ValueError, match="authority"):
        inspection_envelope(inputs(), result, revision=REVISION)


def test_unexpected_outer_authority_is_rejected():
    with pytest.raises(ValueError, match="authority"):
        inspection_envelope(
            inputs(), {**forecast(), "execution_authority": "EXECUTE"}, revision=REVISION,
        )


def test_missing_risk_and_granite_are_not_silent_fallbacks():
    with pytest.raises(ValueError, match="threshold-risk"):
        inspection_envelope(inputs(), {"execution_authority": "NONE"}, revision=REVISION)
    with pytest.raises(ValueError, match="baseline only"):
        inspection_envelope({**inputs(), "provider": "granite"}, forecast(), revision=REVISION)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_data_cannot_be_sealed(value):
    with pytest.raises(ValueError):
        inspection_envelope({**inputs(), "values": [value]}, forecast(), revision=REVISION)


def test_serialization_does_not_modify_inputs():
    request, result = inputs(), forecast()
    before = copy.deepcopy((request, result))
    inspection_envelope(request, result, revision=REVISION)
    assert (request, result) == before
