#!/usr/bin/env python3
"""Static accessibility, locality, responsiveness, and bundle-budget gate."""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "lyte" / "ui"


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append((tag, values))
        # Navigation links may point to the canonical source repository; only
        # runtime-loaded assets can create an external dependency or tracker.
        asset_attributes = ("src", "href") if tag in {"script", "img", "link"} else ()
        for name in asset_attributes:
            value = values.get(name) or ""
            if value.startswith(("http://", "https://", "//")):
                self.external.append(value)


def verify(mode: str) -> list[str]:
    findings: list[str] = []
    html_path, css_path, js_path = UI / "index.html", UI / "styles.css", UI / "app.js"
    for path in (html_path, css_path, js_path):
        if not path.is_file():
            findings.append(f"missing:{path.name}")
    if findings:
        return findings
    html = html_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")
    parser = ContractParser()
    parser.feed(html)
    if parser.external:
        findings.append("external-assets-or-trackers")
    if re.search(r"<script(?:\s[^>]*)?>\s*[^<\s]", html, re.I):
        findings.append("inline-script")
    if re.search(r"<style(?:\s[^>]*)?>", html, re.I):
        findings.append("inline-style")

    if mode in {"all", "accessibility"}:
        tags = parser.tags
        if not any(
            t == "a"
            and "skip-link" in (a.get("class") or "").split()
            and (a.get("href") == "#main-content")
            for t, a in tags
        ):
            findings.append("missing-skip-link")
        for required in ("main", "nav", "h1", "table"):
            if not any(tag == required for tag, _ in tags):
                findings.append(f"missing-semantic-{required}")
        if 'aria-live="polite"' not in html and "aria-live='polite'" not in html:
            findings.append("missing-live-region")
        if ":focus-visible" not in css:
            findings.append("missing-visible-focus")
        if "forced-colors" not in css:
            findings.append("missing-forced-colors")
        if "prefers-reduced-motion" not in css:
            findings.append("missing-reduced-motion")

    if mode in {"all", "responsive"}:
        if "@media" not in css or "320px" not in css:
            findings.append("missing-320px-responsive-contract")
        if "overflow-x: hidden" not in css and "overflow-x:hidden" not in css:
            findings.append("missing-horizontal-overflow-guard")
        if not re.search(r"min-(?:height|block-size)\s*:\s*(?:44|48)px", css):
            findings.append("missing-pointer-target-contract")
        for scene in ("executive", "services", "journeys", "agents", "incidents"):
            if f'data-scene="{scene}"' not in html:
                findings.append(f"missing-scene-{scene}")

    if mode in {"all", "bundle"}:
        sizes = {
            "html": len(html_path.read_bytes()),
            "css": len(css_path.read_bytes()),
            "js": len(js_path.read_bytes()),
        }
        if sizes["html"] >= 150_000:
            findings.append("html-budget")
        if sizes["css"] >= 150_000:
            findings.append("css-budget")
        if sizes["js"] >= 300_000:
            findings.append("js-budget")
    if "requestAnimationFrame" in js and "document.hidden" not in js:
        findings.append("unbounded-hidden-tab-animation")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("all", "accessibility", "responsive", "bundle"), default="all"
    )
    args = parser.parse_args()
    findings = verify(args.mode)
    if findings:
        print(f"frontend-{args.mode}: FAILED")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"frontend-{args.mode}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
