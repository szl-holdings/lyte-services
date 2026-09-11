"""Byte-preserving presentation of existing Forecast Loom results.

This module does not predict, calibrate, admit models, or persist caller data.
Python emits the exact canonical bytes because JSON numbers do not preserve
Python's float representation when round-tripped through a browser.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SOURCE_REPOSITORY = "szl-holdings/lyte-services"
SCHEMA = "szl.lyte.forecast-inspection/v1"
ENVELOPE_SCHEMA = "szl.lyte.forecast-inspection-envelope/v1"


def inspection_envelope(
    request: Mapping[str, Any], forecast: Mapping[str, Any], *, revision: str,
) -> dict[str, str]:
    """Bind request, actual forecast, and reported source without signing them."""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("an exact observed source revision is required")
    risk = forecast.get("risk_window")
    if not isinstance(risk, Mapping):
        raise ValueError("inspection requires a threshold-risk result")
    if (forecast.get("execution_authority") != "NONE"
            or risk.get("execution_authority") != "NONE"
            or risk.get("production_admitted") is not False
            or risk.get("calibration_status") != "NOT_ESTABLISHED"):
        raise ValueError("inspection cannot amplify forecast authority")
    if request.get("provider") != "baseline":
        raise ValueError("the public workbench inspects the baseline only")
    payload = {
        "schema": SCHEMA,
        "source": {"repository": SOURCE_REPOSITORY, "revision": revision},
        "request": dict(request),
        "forecast": dict(forecast),
        "input_provenance": "CALLER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED",
        "execution_authority": "NONE",
        "persisted": False,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return {
        "schema": ENVELOPE_SCHEMA,
        "canonical_json": canonical,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "hash_semantics": "CONTENT_INTEGRITY_NOT_SIGNATURE_OR_ACCURACY",
    }
