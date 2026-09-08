#!/usr/bin/env python3
"""Fail-closed source checks for Lyte's fixed security boundary."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_findings() -> list[str]:
    findings: list[str] = []
    for path in sorted((ROOT / "lyte").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec"}:
                    findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:dynamic-execution")
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
                        if keyword.value.value is True:
                            findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:shell-true")
    return findings


def contract_findings() -> list[str]:
    findings: list[str] = []
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if not re.search(r"(?m)^USER\s+10001:10001\s*$", dockerfile):
        findings.append("Dockerfile:non-root-user-not-fixed")
    app = (ROOT / "lyte" / "app.py").read_text(encoding="utf-8")
    required = (
        "Content-Security-Policy",
        "frame-ancestors 'self' https://huggingface.co https://*.huggingface.co",
        "effectors_enabled",
        "Cross-Origin-Resource-Policy",
        "Cache-Control",
    )
    for value in required:
        if value not in app:
            findings.append(f"lyte/app.py:missing-{value}")
    source = json.loads((ROOT / "SZL_ESTATE_BINDING.json").read_text(encoding="utf-8"))
    if source.get("hub_surface") != "SZLHOLDINGS/lyte":
        findings.append("SZL_ESTATE_BINDING.json:wrong-hub-surface")
    return findings


def main() -> int:
    findings = python_findings() + contract_findings()
    if findings:
        print("security-gate: FAILED")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("security-gate: PASS arbitrary_url_fetch=false effectors_enabled=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
