from __future__ import annotations

import gzip
import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from lyte.app import create_app

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "lyte" / "ui"
INDEX = UI / "index.html"
STYLES = UI / "styles.css"
SCRIPT = UI / "app.js"
SPACE_INDEX = ROOT / "space" / "index.html"


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.references: list[tuple[str, str]] = []
        self.resources: list[tuple[str, str]] = []
        self.inline_scripts = 0
        self.style_elements = 0
        self.inline_style_attributes = 0
        self.event_handler_attributes: list[str] = []
        self.scenes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if values.get("id"):
            self.ids.append(values["id"])
        if values.get("data-scene"):
            self.scenes.append(values["data-scene"])
        for name in ("for", "aria-controls", "aria-labelledby", "aria-describedby"):
            for target in values.get(name, "").split():
                self.references.append((name, target))
        for name in values:
            if name.startswith("on"):
                self.event_handler_attributes.append(name)
        if "style" in values:
            self.inline_style_attributes += 1
        if tag == "script":
            if values.get("src"):
                self.resources.append(("script", values["src"]))
            else:
                self.inline_scripts += 1
        elif tag == "style":
            self.style_elements += 1
        elif tag == "link" and set(values.get("rel", "").split()) & {
            "stylesheet",
            "preload",
            "icon",
            "manifest",
        }:
            self.resources.append(("link", values.get("href", "")))
        elif tag in {"img", "audio", "video", "source", "iframe", "object", "embed"}:
            self.resources.append((tag, values.get("src") or values.get("data", "")))


def parse_html(path: Path) -> ContractParser:
    parser = ContractParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def test_five_scene_product_shell_is_semantic_and_reference_complete() -> None:
    parser = parse_html(INDEX)
    assert parser.scenes == ["executive", "services", "journeys", "agents", "incidents"]
    assert len(parser.ids) == len(set(parser.ids)), "HTML IDs must be unique"
    for attribute, target in parser.references:
        assert target in parser.ids, f"{attribute} points to missing ID {target!r}"

    html = INDEX.read_text(encoding="utf-8")
    assert 'class="skip-link"' in html
    assert 'id="signal-lattice"' in html
    assert "Signal Lattice text alternative" in html
    assert 'id="signal-drawer"' in html
    assert 'id="incident-scrubber"' in html
    assert 'id="ask-dialog"' in html
    assert "Evidence receipt tape" in html
    assert "Truth language" in html
    assert "SAMPLE workspace" in html
    assert "Revenue / currently at risk" in html
    assert "Cost / AI agent retries" in html
    assert "Service / critical journey availability" in html
    assert "Risk / material incidents" in html


def test_frontend_has_no_inline_csp_exceptions_or_external_runtime_assets() -> None:
    for path in (INDEX, SPACE_INDEX):
        parser = parse_html(path)
        assert parser.inline_scripts == 0
        assert parser.style_elements == 0
        assert parser.inline_style_attributes == 0
        assert parser.event_handler_attributes == []
        assert parser.resources, f"{path.name} should load at least one local asset"
        for tag, url in parser.resources:
            assert url.startswith("/static/lyte/"), f"external or unscoped {tag} asset: {url}"
            assert not url.startswith("//")

    space = SPACE_INDEX.read_text(encoding="utf-8")
    assert 'content="0; url=/static/lyte/index.html"' in space


def test_local_controller_wires_real_endpoints_and_all_interactions() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    required_contracts = {
        'ask: "/api/lyte/v2/ask"',
        'build: "/api/build-info"',
        'sources: "/api/lyte/v2/sources"',
        "data-lattice-filter",
        "data-journey-step",
        "data-service-filter",
        "data-playback-index",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "visibilitychange",
        "prefers-reduced-motion: reduce",
        "causality is not claimed",
        "No answer was fabricated",
    }
    for contract in required_contracts:
        assert contract in script

    forbidden_sinks = (".innerHTML", ".outerHTML", "document.write", "eval(", "new Function")
    for sink in forbidden_sinks:
        assert sink not in script
    assert "textContent" in script
    assert "replaceChildren" in script
    assert 'credentials: "same-origin"' in script


def test_responsive_accessibility_and_render_budgets_are_enforced() -> None:
    css = STYLES.read_text(encoding="utf-8")
    for contract in (
        "min-width: 320px",
        "overflow-x: hidden",
        "min-height: 44px",
        "min-height: 48px",
        "@media (max-width: 430px)",
        "@media (max-width: 900px)",
        "@media (hover: none) and (pointer: coarse)",
        "@media (prefers-reduced-motion: reduce)",
        "@media (forced-colors: active)",
        ":focus-visible",
        ".table-scroll",
    ):
        assert contract in css

    budgets = ((INDEX, 150_000), (STYLES, 150_000), (SCRIPT, 300_000))
    for path, compressed_limit in budgets:
        compressed = gzip.compress(path.read_bytes(), compresslevel=9)
        assert len(compressed) < compressed_limit, (
            f"{path.name} is {len(compressed)} compressed bytes; limit is {compressed_limit}"
        )


def test_fastapi_serves_frontend_assets_under_csp(monkeypatch) -> None:
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    with TestClient(create_app()) as client:
        page = client.get("/")
        css = client.get("/static/lyte/styles.css")
        script = client.get("/static/lyte/app.js")

    assert page.status_code == css.status_code == script.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert css.headers["content-type"].startswith("text/css")
    assert "javascript" in script.headers["content-type"]
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert "script-src 'self' https:" not in csp
    assert "style-src 'self' https:" not in csp
    assert "font-src 'self' https:" not in csp
    assert "frame-ancestors 'self' https://huggingface.co https://*.huggingface.co" in csp
    assert re.search(r'<script\s+src="/static/lyte/app\.js"\s+defer></script>', page.text)
