# ruff: noqa: E501
"""Capture reproducible Lyte browser, responsive, and keyboard evidence.

This utility drives a real Chromium browser over the Chrome DevTools Protocol.
It intentionally does not treat HTML/CSS inspection as rendered UI evidence.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any

from websockets.sync.client import connect

VIEWPORTS = (
    (320, 568),
    (375, 812),
    (430, 932),
    (768, 1024),
    (1024, 768),
    (1440, 900),
    (1920, 1080),
)
WINDOWS_BROWSER_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _http_json(url: str, *, method: str = "GET") -> Any:
    request = urllib.request.Request(url, method=method)  # noqa: S310
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _find_browser(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"browser executable not found: {candidate}")
    for candidate in WINDOWS_BROWSER_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()
    for command in ("msedge", "microsoft-edge", "google-chrome", "chromium"):
        discovered = shutil.which(command)
        if discovered:
            return Path(discovered).resolve()
    raise FileNotFoundError("no supported Edge, Chrome, or Chromium executable found")


@dataclass
class CdpSession:
    websocket: Any
    next_id: int = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        call_id = self.next_id
        self.next_id += 1
        self.websocket.send(
            json.dumps(
                {"id": call_id, "method": method, "params": params or {}},
                separators=(",", ":"),
            )
        )
        while True:
            message = json.loads(self.websocket.recv(timeout=15))
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"CDP {method} returned an invalid result")
            return result

    def evaluate(self, expression: str, *, await_promise: bool = False) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        remote = result.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError(f"browser evaluation failed: {result['exceptionDetails']}")
        return remote.get("value")


def _wait_for_debug_port(profile: Path, process: subprocess.Popen[bytes]) -> int:
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = (
                process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            )
            raise RuntimeError(f"browser exited before CDP became ready: {stderr[-1000:]}")
        if marker.is_file():
            first_line = marker.read_text(encoding="utf-8").splitlines()[0]
            return int(first_line)
        time.sleep(0.05)
    raise TimeoutError("browser CDP port was not ready within 20 seconds")


def _stop_browser_process(
    process: subprocess.Popen[bytes],
    *,
    graceful_close_requested: bool,
    platform_name: str | None = None,
) -> None:
    """Close Chromium without orphaning profile-locking child processes on Windows."""

    platform_name = platform_name or os.name
    if platform_name == "nt" and process.poll() is None:
        taskkill = shutil.which("taskkill")
        if taskkill:
            subprocess.run(  # noqa: S603 -- exact PID belongs to the spawned browser
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            try:
                process.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                pass
    if graceful_close_requested:
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _remove_browser_profile(profile: Path) -> None:
    """Remove only our validated temporary profile, retrying transient Windows locks."""

    resolved = profile.resolve()
    temporary_root = Path(gettempdir()).resolve()
    if resolved.parent != temporary_root or not resolved.name.startswith("lyte-browser-profile-"):
        raise RuntimeError("refusing to remove an unexpected browser profile path")
    deadline = time.monotonic() + 10
    while resolved.exists():
        try:
            shutil.rmtree(resolved)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _wait_for_document(cdp: CdpSession, expected_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = cdp.evaluate(
            "JSON.stringify({ready:document.readyState,url:location.href,app:Boolean(document.querySelector('[data-scene=executive]'))})"
        )
        parsed = json.loads(state) if isinstance(state, str) else {}
        if (
            parsed.get("ready") == "complete"
            and parsed.get("app") is True
            and str(parsed.get("url", "")).startswith(expected_url)
        ):
            time.sleep(0.25)
            return
        time.sleep(0.05)
    raise TimeoutError(f"Lyte UI did not finish loading at {expected_url}")


def _navigate(cdp: CdpSession, url: str) -> None:
    result = cdp.call("Page.navigate", {"url": url})
    if result.get("errorText"):
        raise RuntimeError(f"navigation failed: {result['errorText']}")
    _wait_for_document(cdp, url)


def _dispatch_key(cdp: CdpSession, key: str) -> None:
    metadata = {
        "Tab": ("Tab", 9),
        "Enter": ("Enter", 13),
        "Escape": ("Escape", 27),
        "ArrowLeft": ("ArrowLeft", 37),
        "ArrowUp": ("ArrowUp", 38),
        "ArrowRight": ("ArrowRight", 39),
        "ArrowDown": ("ArrowDown", 40),
    }
    code, virtual_key = metadata[key]
    common = {
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }
    keydown = {"type": "keyDown" if key == "Enter" else "rawKeyDown", **common}
    if key == "Enter":
        keydown.update({"text": "\r", "unmodifiedText": "\r"})
    cdp.call("Input.dispatchKeyEvent", keydown)
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", **common})
    time.sleep(0.05)


_FOCUS_EXPRESSION = """
(() => {
  const element = document.activeElement;
  if (!element) return null;
  return {
    tag: element.tagName.toLowerCase(),
    id: element.id || null,
    text: (element.innerText || element.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
    sceneTarget: element.dataset ? element.dataset.sceneTarget || null : null,
    nodeId: element.dataset ? element.dataset.nodeId || null : null
  };
})()
"""


def _focus(cdp: CdpSession) -> dict[str, Any] | None:
    value = cdp.evaluate(_FOCUS_EXPRESSION)
    return value if isinstance(value, dict) else None


def _keyboard_evidence(cdp: CdpSession, base_url: str) -> dict[str, Any]:
    _navigate(cdp, f"{base_url}#executive")
    cdp.evaluate("document.querySelector('.skip-link').focus()")
    focus_order: list[dict[str, Any] | None] = [_focus(cdp)]
    for _ in range(24):
        _dispatch_key(cdp, "Tab")
        focus_order.append(_focus(cdp))

    steps: list[dict[str, Any]] = []

    def record(name: str, passed: bool, observed: Any) -> None:
        steps.append({"name": name, "passed": bool(passed), "observed": observed})

    scene_results: dict[str, bool] = {}
    for scene in ("executive", "services", "journeys", "agents", "incidents"):
        focused = cdp.evaluate(
            f"document.querySelector('.primary-nav [data-scene-target=\"{scene}\"]').focus(); true"
        )
        _dispatch_key(cdp, "Enter")
        active = cdp.evaluate(
            f"!document.querySelector('[data-scene=\"{scene}\"]').hidden && location.hash === '#{scene}'"
        )
        scene_results[scene] = bool(focused and active)
    record(
        "activate all five operating lenses with Enter", all(scene_results.values()), scene_results
    )

    cdp.evaluate("document.querySelector('[data-scene-target=\"executive\"]').focus()")
    _dispatch_key(cdp, "Enter")
    node_ready = cdp.evaluate(
        "(() => { const n=document.querySelector('[data-node-id][tabindex=\"0\"]'); if(!n) return false; n.focus(); return true; })()"
    )
    before_node = _focus(cdp)
    _dispatch_key(cdp, "ArrowRight")
    after_node = _focus(cdp)
    arrow_moved = bool(
        node_ready
        and before_node
        and after_node
        and before_node.get("nodeId")
        and after_node.get("nodeId")
        and before_node.get("nodeId") != after_node.get("nodeId")
    )
    record(
        "move between lattice nodes with ArrowRight",
        arrow_moved,
        {"before": before_node, "after": after_node},
    )

    _dispatch_key(cdp, "Enter")
    drawer_open = bool(cdp.evaluate("!document.querySelector('#signal-drawer').hidden"))
    record("open focused lattice evidence with Enter", drawer_open, {"drawerOpen": drawer_open})
    _dispatch_key(cdp, "Escape")
    time.sleep(0.3)
    drawer_closed = bool(cdp.evaluate("document.querySelector('#signal-drawer').hidden"))
    record(
        "close lattice evidence with Escape",
        drawer_closed,
        {"drawerClosed": drawer_closed, "focus": _focus(cdp)},
    )

    cdp.evaluate("document.querySelector('#ask-open').focus()")
    _dispatch_key(cdp, "Enter")
    ask_open = bool(cdp.evaluate("document.querySelector('#ask-dialog').open"))
    record("open Ask Lyte dialog with Enter", ask_open, {"dialogOpen": ask_open})
    _dispatch_key(cdp, "Escape")
    ask_closed = not bool(cdp.evaluate("document.querySelector('#ask-dialog').open"))
    record(
        "close Ask Lyte dialog with Escape",
        ask_closed,
        {"dialogClosed": ask_closed, "focus": _focus(cdp)},
    )

    focus_ids = {(item or {}).get("id") or (item or {}).get("sceneTarget") for item in focus_order}
    tab_reached_navigation = any((item or {}).get("sceneTarget") for item in focus_order)
    tab_reached_command = "ask-open" in focus_ids or "source-button" in focus_ids
    record(
        "Tab reaches navigation and a global command",
        tab_reached_navigation and tab_reached_command,
        {
            "navigationReached": tab_reached_navigation,
            "globalCommandReached": tab_reached_command,
        },
    )
    return {
        "viewport": {"width": 1440, "height": 900},
        "required_sequence": [
            "Tab through global controls and scene navigation",
            "Enter on every operating lens",
            "ArrowRight between visible Signal Lattice nodes",
            "Enter to inspect focused node",
            "Escape to close evidence drawer",
            "Enter and Escape on Ask Lyte dialog",
        ],
        "focus_order": focus_order,
        "steps": steps,
        "completed": all(step["passed"] for step in steps),
    }


def _viewport_evidence(
    cdp: CdpSession,
    base_url: str,
    output_dir: Path,
    width: int,
    height: int,
) -> dict[str, Any]:
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": width < 768,
            "screenWidth": width,
            "screenHeight": height,
        },
    )
    _navigate(cdp, f"{base_url}#executive")
    layout = cdp.evaluate(
        """
(() => {
  const root = document.documentElement;
  const body = document.body;
  const resources = performance.getEntriesByType('resource').map(x => x.name);
  const external = resources.filter(value => new URL(value, location.href).origin !== location.origin);
  const all = Array.from(document.querySelectorAll('body *')).filter(element => {
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length;
  });
  const rightmost = all.reduce((maximum, element) => Math.max(maximum, element.getBoundingClientRect().right), 0);
  return {
    url: location.href,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    documentClientWidth: root.clientWidth,
    documentScrollWidth: root.scrollWidth,
    bodyClientWidth: body.clientWidth,
    bodyScrollWidth: body.scrollWidth,
    rightmostRenderedPixel: Math.round(rightmost * 1000) / 1000,
    externalResources: external,
    horizontalOverflowPx: Math.max(0, root.scrollWidth - root.clientWidth),
    activeScene: document.querySelector('[data-scene]:not([hidden])')?.dataset.scene || null,
    visibleTruthLabels: Array.from(document.querySelectorAll('.truth-badge')).filter(x => x.getClientRects().length).map(x => x.textContent.trim()),
    reducedMotionQuery: matchMedia('(prefers-reduced-motion: reduce)').matches,
    forcedColorsQuery: matchMedia('(forced-colors: active)').matches
  };
})()
"""
    )
    if not isinstance(layout, dict):
        raise RuntimeError("browser returned invalid viewport evidence")
    image = cdp.call(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
    )
    screenshot = output_dir / f"lyte-{width}x{height}.png"
    screenshot.write_bytes(base64.b64decode(image["data"]))
    return {
        "width": width,
        "height": height,
        **layout,
        "screenshot": screenshot.as_posix(),
        "passed": layout["horizontalOverflowPx"] == 0 and not layout["externalResources"],
    }


def capture(
    *,
    base_url: str,
    output_dir: Path,
    json_output: Path,
    browser_path: Path | None = None,
) -> dict[str, Any]:
    normalized_url = base_url.rstrip("/") + "/static/lyte/index.html"
    executable = _find_browser(browser_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="lyte-browser-profile-", ignore_cleanup_errors=True
    ) as profile_name:
        profile = Path(profile_name)
        command = [
            str(executable),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "about:blank",
        ]
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        graceful_close_requested = False
        try:
            port = _wait_for_debug_port(profile, process)
            root = f"http://127.0.0.1:{port}"
            targets = _http_json(f"{root}/json/list")
            target = next((row for row in targets if row.get("type") == "page"), None)
            if target is None:
                target = _http_json(
                    f"{root}/json/new?{urllib.parse.quote('about:blank', safe='')}",
                    method="PUT",
                )
            websocket_url = target["webSocketDebuggerUrl"]
            with connect(websocket_url, open_timeout=10, close_timeout=5) as websocket:
                cdp = CdpSession(websocket)
                cdp.call("Page.enable")
                cdp.call("Runtime.enable")
                version = cdp.call("Browser.getVersion")
                viewports = [
                    _viewport_evidence(cdp, normalized_url, output_dir, width, height)
                    for width, height in VIEWPORTS
                ]
                cdp.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 1440,
                        "height": 900,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                keyboard = _keyboard_evidence(cdp, normalized_url)
                source_identity = cdp.evaluate(
                    "fetch('/api/build-info').then(r => r.json()).catch(error => ({error:String(error)}))",
                    await_promise=True,
                )
                maximum_overflow = max(row["horizontalOverflowPx"] for row in viewports)
                external_resources = sorted(
                    {resource for row in viewports for resource in row["externalResources"]}
                )
                document = {
                    "schema": "szl.lyte-ui-browser-evidence/v1",
                    "captured_at": _now(),
                    "base_url": base_url.rstrip("/"),
                    "page_url": normalized_url,
                    "browser": {
                        "product": version.get("product"),
                        "revision": version.get("revision"),
                        "user_agent": version.get("userAgent"),
                        "js_version": version.get("jsVersion"),
                        "executable": str(executable),
                    },
                    "source_identity": source_identity,
                    "viewports": viewports,
                    "keyboard": keyboard,
                    "summary": {
                        "viewport_count": len(viewports),
                        "maximum_horizontal_overflow_px": maximum_overflow,
                        "no_horizontal_overflow": maximum_overflow == 0,
                        "keyboard_completed": keyboard["completed"],
                        "external_resources": external_resources,
                        "local_assets_only": not external_resources,
                        "passed": maximum_overflow == 0
                        and keyboard["completed"]
                        and not external_resources,
                    },
                    "limitations": [
                        "This run covers one Chromium implementation; Safari and Firefox are not inferred.",
                        "Forced-colors and reduced-motion support remain separately checked by static contract tests unless those OS preferences are active for this browser run.",
                        "Screenshots contain the public SAMPLE / MODELED scenario and no customer telemetry.",
                    ],
                }
                json_output.write_text(_json_text(document), encoding="utf-8")
                # POSIX Chromium closes its complete process tree through CDP. On Windows,
                # retain the live parent until the finally block can terminate its exact
                # PID tree; closing only the launcher can orphan profile-locking children.
                if os.name != "nt":
                    graceful_close_requested = True
                    cdp.call("Browser.close")
                return document
        finally:
            _stop_browser_process(
                process,
                graceful_close_requested=graceful_close_requested,
            )
            _remove_browser_profile(profile)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7860")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--browser", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    namespace = _parser().parse_args(argv)
    document = capture(
        base_url=namespace.base_url,
        output_dir=namespace.output_dir.resolve(),
        json_output=namespace.json_output.resolve(),
        browser_path=namespace.browser,
    )
    print(
        json.dumps(
            {
                "json_output": str(namespace.json_output.resolve()),
                "screenshots": len(document["viewports"]),
                "passed": document["summary"]["passed"],
            },
            sort_keys=True,
        )
    )
    return 0 if document["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
