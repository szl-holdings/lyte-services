#!/usr/bin/env python3
"""Qualify static Lyte presentation without starting its backend or submitting actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (320, 390, 768, 1280, 1920)
SCENES = ("executive", "services", "journeys", "agents", "incidents")
ICON_SHA = "6e2b7d72a3af3d9602b79de41df0194ede6496a9661f057108ec224c0efc92e6"
PAGES = frozenset((*SCENES[1:4], "outcomes", "incidents", "decisions", "playback", "receipts"))


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    files = {
        f"/static/lyte/{name}": (ROOT / "lyte/ui" / name).read_bytes()
        for name in ("index.html", "styles.css", "app.js")
    }
    files["/"] = files["/static/lyte/index.html"]
    mode = {"value": "unavailable"}
    writes, outside, errors = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            page_name = path.removeprefix("/api/lyte/v2/")
            if path in files:
                body = files[path]
                self.send_response(200)
                if path in ("/", "/static/lyte/index.html"):
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                elif path == "/static/lyte/styles.css":
                    self.send_header("Content-Type", "text/css; charset=utf-8")
                else:
                    self.send_header("Content-Type", "text/javascript; charset=utf-8")
            elif page_name in PAGES and mode["value"] in ("sample-empty", "partial"):
                if mode["value"] == "partial" and page_name == "receipts":
                    self.send_response(503)
                    body = b'{"error":"LOCAL unavailable receipt fixture"}'
                else:
                    self.send_response(200)
                    schema = "receipt-page" if page_name == "receipts" else "operational-page"
                    body = json.dumps({
                        "schema": f"szl.lyte.{schema}/v1", "data_mode": "SAMPLE",
                        "items": [], "offset": 0, "next_offset": None,
                        "fixture": "LOCAL PRESENTATION ONLY; no app or persistence started",
                    }).encode()
                self.send_header("Content-Type", "application/json")
            else:
                body = b'{"error":"LOCAL unavailable fixture; no backend started"}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            writes.append(self.path)
            self.send_error(405)

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    checks = []

    def check(name, passed, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=os.environ.get("CHROME_PATH"),
            args=["--disable-dev-shm-usage", "--use-gl=angle", "--use-angle=swiftshader",
                  "--enable-unsafe-swiftshader"],
        )
        context = browser.new_context(reduced_motion="reduce")

        def intercept(route):
            if route.request.method != "GET":
                writes.append(route.request.url)
                route.abort()
            elif route.request.url.startswith(origin + "/"):
                route.continue_()
            else:
                outside.append(route.request.url)
                route.abort()

        context.route("**/*", intercept)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto(origin, wait_until="domcontentloaded")
            page.locator('html[data-workspace-state="settled"]').wait_for(state="attached")
            page.keyboard.press("Tab")
            check(f"{width}: skip link first", page.locator(".skip-link").evaluate(
                "element=>document.activeElement===element"))
            page.keyboard.press("Enter")
            check(f"{width}: skip focuses workspace", page.evaluate(
                "document.activeElement.id==='main-content'"))
            check(f"{width}: missing receipts remain unknown", page.locator(
                "#receipt-count").text_content() == "—")
            check(f"{width}: absent proof has no proportional chart", page.evaluate("""() =>
              document.querySelector('#proof-coverage-summary').textContent==='UNAVAILABLE'&&
              getComputedStyle(document.querySelector('.coverage-ring')).backgroundImage==='none'
            """))
            check(f"{width}: first paint honors reduced motion", page.evaluate(
                "document.documentElement.dataset.animate==='false'"))
            for scene in SCENES:
                if width <= 900:
                    page.locator("#mobile-menu").click()
                page.locator(f'.primary-nav [data-scene-target="{scene}"]').click()
                layout = page.evaluate("""() => {
                  const shown=e=>e.getClientRects().length&&
                    getComputedStyle(e).visibility!=='hidden';
                  const roots=[...document.querySelectorAll(
                    'main,.scene:not([hidden]),.panel,.topbar,.topbar-actions,.workspace-bar'
                  )].filter(shown);
                  const controls=[...document.querySelectorAll(
                    'button,summary,.szl-endorsement'
                  )].filter(shown);
                  return {width:innerWidth,scroll:document.documentElement.scrollWidth,
                    overflow:roots.filter(e=>e.getBoundingClientRect().right>innerWidth+1)
                      .map(e=>e.id||e.className),
                    small:controls.filter(e=>e.getBoundingClientRect().height<43.5)
                      .map(e=>e.id||e.className),
                    active:document.activeElement.closest('[data-scene]')?.dataset.scene,
                    header:getComputedStyle(document.querySelector('.topbar')).backgroundColor};
                }""")
                check(f"{width}/{scene}: native layout and heading focus",
                      layout["scroll"] <= width + 1 and not layout["overflow"]
                      and not layout["small"] and layout["active"] == scene
                      and layout["header"] == "rgb(11, 16, 26)", layout)
            if width <= 900:
                page.locator("#mobile-menu").click()
                check(f"{width}: navigation moves focus", page.evaluate(
                    "document.activeElement.matches('.primary-nav button')"))
                page.keyboard.press("Escape")
                check(f"{width}: navigation closes and returns focus", page.evaluate("""() =>
                  document.activeElement.id==='mobile-menu'&&
                  document.querySelector('#primary-sidebar').inert&&
                  document.querySelector('#mobile-menu').getAttribute('aria-expanded')==='false'
                """))
            page.locator("[data-open-source]").first.click()
            check(f"{width}: native source dialog opens", page.locator(
                "#source-dialog").evaluate("element=>element.open"))
            check(f"{width}: source fields are labeled", page.locator(
                "#source-dialog").evaluate("element=>[...element.querySelectorAll('input')]"
                                          ".every(input=>input.labels.length>0)"))
            page.keyboard.press("Escape")
            check(f"{width}: source dialog Escape closes", not page.locator(
                "#source-dialog").evaluate("element=>element.open"))
            page.keyboard.press("Control+k")
            page.locator("#ask-input:focus").wait_for(state="attached")
            check(f"{width}: command palette receives focus", page.evaluate("""() =>
                document.querySelector('#ask-dialog').open&&
                document.activeElement.id==='ask-input'
            """))
            page.keyboard.press("Escape")
            if width <= 900:
                page.locator("#mobile-menu").click()
            page.locator('.primary-nav [data-scene-target="executive"]').click()
            page.locator(".text-alternative summary").focus()
            page.keyboard.press("Space")
            check(f"{width}: native table disclosure", page.locator(
                ".text-alternative").evaluate("element=>element.open"))
            page.locator(".lattice-stage").focus()
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            check(f"{width}: lattice viewport keyboard scroll", page.locator(
                ".lattice-stage").evaluate("e=>e.scrollWidth<=e.clientWidth||e.scrollLeft>0"))
            (output / "progress.json").write_text(
                json.dumps({"last_width": width, "checks": checks}, indent=2) + "\n",
                encoding="utf-8",
            )
            if width in (320, 1280):
                page.evaluate("() => {document.activeElement.blur();scrollTo(0,0)}")
                page.screenshot(
                    path=str(output / f"lyte-{width}.png"), full_page=False, timeout=45000,
                )
        for value, expected in (("sample-empty", "0"), ("partial", "—")):
            mode["value"] = value
            page.goto(origin, wait_until="domcontentloaded")
            page.locator('html[data-workspace-state="settled"]').wait_for(state="attached")
            result = {"mode": page.locator("#workspace-data-mode").text_content(),
                      "receipts": page.locator("#receipt-count").text_content()}
            check(f"{value}: empty and unavailable receipts remain distinct",
                  result == {"mode": "SAMPLE", "receipts": expected}, result)
        page.emulate_media(forced_colors="active")
        page.locator("[data-open-source]").first.focus()
        forced = page.evaluate("""() => ({
          outline:getComputedStyle(document.activeElement).outlineWidth,
          shadow:getComputedStyle(document.activeElement).boxShadow,
          panel:getComputedStyle(document.querySelector('.panel')).backgroundImage
        })""")
        check("forced colors retain actual focus outline",
              forced == {"outline": "3px", "shadow": "none", "panel": "none"}, forced)
        browser.close()
    server.shutdown()
    server.server_close()
    icon = re.search(
        rb'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600".*?</svg>',
        files["/"], re.S,
    )
    check("initial proof legend stays unknown",
          "Canonical evidence <b>—</b>".encode() in files["/"])
    check("exact accepted native Orbit SVG",
          icon is not None and hashlib.sha256(icon.group(0) + b"\n").hexdigest() == ICON_SHA)
    check("no page errors or attempted writes or external requests", not errors and not writes
          and not outside, {"errors": errors, "writes": writes, "external": outside})
    report = {"observed_at": datetime.now(UTC).isoformat(),
              "scope": "STATIC SOURCE ONLY; LOCAL EXPLICIT FIXTURES; NO BACKEND OR ACTIONS",
              "source_hashes": {name: hashlib.sha256(body).hexdigest()
                                for name, body in files.items()},
              "checks": checks, "passed": all(item["passed"] for item in checks)}
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(checks), "output": str(output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output))
