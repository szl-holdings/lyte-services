"""Opt-in browser verification against the actual loopback Lyte application.

Install playwright and its Chromium runtime in an isolated test environment.
This harness never contacts Hugging Face or writes a production deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.source_revision) is None:
        parser.error("source revision must be an exact 40-character Git SHA")
    args.output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    for key in ("GITHUB_SHA", "SOURCE_REVISION", "LYTE_GRANITE_ENABLED"):
        environment.pop(key, None)
    environment.update({
        "LYTE_ENV": "test", "LYTE_DEMO_MODE": "true",
        "LYTE_SOURCE_REVISION": args.source_revision,
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    })
    results = []
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - fixed local test server, no shell
            [sys.executable, "-m", "uvicorn", "lyte.app:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port)],
            env=environment, stdout=log, stderr=log,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("local application exited before readiness")
                try:
                    with urlopen(origin + "/readyz", timeout=1) as response:  # noqa: S310
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("local application did not become ready")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for width, height in ((320, 568), (375, 812), (768, 1024), (1440, 900)):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.goto(origin + "/api/lyte/v2/forecast/workbench")
                        expect(page.locator("#forecast-output")).to_be_hidden()
                        page.locator("#run").click()
                        expect(page.locator("#forecast-output")).to_be_visible()
                        expect(page.locator("#source")).to_have_text(args.source_revision)
                        expect(page.locator("#forecast-table tr")).to_have_count(12)
                        expect(page.locator("#input-origin")).to_have_text("SAMPLE")
                        assert page.locator(".chart-ribbon").get_attribute("points")
                        page.locator("#step").focus()
                        page.keyboard.press("ArrowRight")
                        expect(page.locator("#step-detail")).to_contain_text("Step 2")
                        for summary in page.locator("details summary").all():
                            summary.click()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        ), f"horizontal overflow at {width}"
                        with page.expect_download() as download:
                            page.locator("#export").click()
                        target = args.output / f"evidence-{width}.json"
                        download.value.save_as(target)
                        envelope = json.loads(target.read_text())
                        assert envelope["sha256"] == hashlib.sha256(
                            envelope["canonical_json"].encode()
                        ).hexdigest()
                        assert "NOT_ESTABLISHED" in envelope["canonical_json"]
                        page.emulate_media(reduced_motion="reduce")
                        page.screenshot(
                            path=str(args.output / f"workbench-{width}.png"), full_page=True,
                        )
                        page.locator("#threshold").fill("90")
                        expect(page.locator("#forecast-output")).to_be_hidden()
                        page.locator("#run").click()
                        expect(page.locator("#forecast-output")).to_be_visible()
                        page.emulate_media(forced_colors="active")
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        assert not errors, errors
                        results.append({"width": width, "height": height,
                                        "same_origin_python": True, "overflow": False,
                                        "keyboard_step": True, "evidence_export": True,
                                        "stale_output_hidden": True, "page_errors": errors})
                        page.close()
                    page = browser.new_page()
                    page.goto(origin + "/api/lyte/v2/forecast/workbench")

                    def corrupt(route):
                        response = route.fetch()
                        value = response.json()
                        value["canonical_json"] += " "
                        route.fulfill(response=response, json=value)

                    page.route("**/forecast/inspect", corrupt)
                    page.locator("#run").click()
                    expect(page.locator("#status")).to_contain_text("do not match")
                    expect(page.locator("#forecast-output")).to_be_hidden()
                    page.unroute("**/forecast/inspect")
                    page.locator("#values").fill("1,,2")
                    page.locator("#run").click()
                    expect(page.locator("#status")).to_contain_text("empty values are not zero")
                    page.close()
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    report = {
        "schema": "szl.lyte.workbench-browser/v1", "source_revision": args.source_revision,
        "scope": "ISOLATED_LOCAL_APPLICATION_NOT_PUBLIC_DEPLOYMENT", "viewports": results,
        "corrupt_response_rejected": True, "invalid_input_rejected": True,
        "model_inference": "DETERMINISTIC_BASELINE_ONLY", "production_admitted": False,
    }
    (args.output / "browser-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
