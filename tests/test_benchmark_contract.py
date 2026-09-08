from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmarks.observability.run_benchmark import (
    _browser_results,
    _read_browser_evidence,
)
from tools.capture_ui_evidence import VIEWPORTS, _stop_browser_process

REVISION = "a" * 40


def _evidence() -> dict:
    return {
        "schema": "szl.lyte-ui-browser-evidence/v1",
        "browser": {"product": "Edg/test"},
        "source_identity": {"build": {"revision": REVISION}},
        "summary": {
            "maximum_horizontal_overflow_px": 0,
            "local_assets_only": True,
        },
        "keyboard": {
            "completed": True,
            "viewport": {"width": 1440, "height": 900},
            "steps": [{"name": "open scene", "passed": True}],
        },
        "viewports": [
            {
                "width": width,
                "height": height,
                "horizontalOverflowPx": 0,
                "screenshot": f"lyte-{width}x{height}.png",
            }
            for width, height in VIEWPORTS
        ],
        "limitations": ["Chromium evidence only."],
    }


def test_browser_capture_declares_every_required_viewport() -> None:
    assert VIEWPORTS == (
        (320, 568),
        (375, 812),
        (430, 932),
        (768, 1024),
        (1024, 768),
        (1440, 900),
        (1920, 1080),
    )


def test_graceful_browser_shutdown_waits_before_forced_termination() -> None:
    process = Mock()
    process.wait.return_value = 0

    _stop_browser_process(
        process,
        graceful_close_requested=True,
        platform_name="posix",
    )

    process.wait.assert_called_once_with(timeout=10)
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_stuck_browser_shutdown_escalates_to_kill() -> None:
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="browser", timeout=10),
        subprocess.TimeoutExpired(cmd="browser", timeout=10),
        0,
    ]

    _stop_browser_process(
        process,
        graceful_close_requested=True,
        platform_name="posix",
    )

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()


def test_windows_browser_shutdown_targets_the_spawned_process_tree(monkeypatch) -> None:
    process = Mock(pid=4242)
    process.poll.return_value = None
    process.wait.return_value = 0
    run = Mock()
    monkeypatch.setattr(
        "tools.capture_ui_evidence.shutil.which",
        lambda command: "C:/Windows/System32/taskkill.exe" if command == "taskkill" else None,
    )
    monkeypatch.setattr("tools.capture_ui_evidence.subprocess.run", run)

    _stop_browser_process(
        process,
        graceful_close_requested=False,
        platform_name="nt",
    )

    assert run.call_args.args[0] == [
        "C:/Windows/System32/taskkill.exe",
        "/PID",
        "4242",
        "/T",
        "/F",
    ]
    process.wait.assert_called_once_with(timeout=10)
    process.terminate.assert_not_called()


def test_browser_evidence_is_source_bound(tmp_path: Path) -> None:
    path = tmp_path / "browser.json"
    path.write_text(json.dumps(_evidence()), encoding="utf-8")
    assert _read_browser_evidence(path, REVISION)["summary"]["maximum_horizontal_overflow_px"] == 0
    with pytest.raises(ValueError, match="does not match"):
        _read_browser_evidence(path, "b" * 40)


def test_browser_dimensions_are_measured_only_from_browser_evidence() -> None:
    targets = {
        "ui_keyboard_completion": {
            "operator": "eq",
            "value": 1.0,
            "unit": "completed",
            "statistic": "all_declared_sequences",
        },
        "mobile_overflow": {
            "operator": "eq",
            "value": 0.0,
            "unit": "overflow_px",
            "statistic": "maximum",
        },
    }
    measured = _browser_results(_evidence(), targets)
    assert [row["result_label"] for row in measured] == [
        "MEASURED_PARITY",
        "MEASURED_PARITY",
    ]
    assert [row["measurement"]["value"] for row in measured] == [1.0, 0.0]

    not_tested = _browser_results(None, targets)
    assert [row["result_label"] for row in not_tested] == [
        "NOT_TESTED",
        "NOT_TESTED",
    ]
    assert all(row["measurement"] is None for row in not_tested)
