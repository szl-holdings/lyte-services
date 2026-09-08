"""Execute syntax checks against the canonical external browser program."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lyte" / "ui" / "app.js"


def test_shipped_external_javascript_parses() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to verify the browser program"
    checked = subprocess.run(  # noqa: S603
        [node, "--check", str(SCRIPT)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_agent_surfaces_preserve_sample_and_modeled_truth() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for agent_id in ("policy-agent", "resolution-agent"):
        start = source.index(f'id: "{agent_id}"')
        record = source[start : source.index("},", start) + 2]
        assert 'truth: "SAMPLE"' in record
        assert 'truth: "MEASURED"' not in record
    assert 'TRUTH_LABELS.has(payload?.truth_label) ? payload.truth_label : "UNAVAILABLE"' in source
