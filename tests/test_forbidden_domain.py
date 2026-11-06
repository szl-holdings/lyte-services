"""The forbidden-domain gate (tools/forbidden_domain.py) and its negative controls."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools import forbidden_domain as gate

# Built at runtime so this test file is not itself a finding.
DOMAIN = "a11oy" + ".com"
GIT = shutil.which("git")


def _violations(text: str) -> tuple[list[str], int]:
    result = gate.ScanResult()
    gate.scan_text("sample.md", text, result)
    return result.violations, result.guarded


def test_repository_carries_no_forbidden_domain_reference(capsys) -> None:
    assert gate.main() == 0
    assert capsys.readouterr().out.startswith("forbidden-domain: PASS files=")


def test_line_rules_match_the_estate_gate() -> None:
    line = f"see https://{DOMAIN}/docs"
    assert _violations(line + "\n") == ([f"sample.md:1: {line}"], 0)
    assert _violations(f"x{DOMAIN} is a lookalike too\n")[0]
    assert _violations("product https://a-11-oy.com, proof https://a11oy.net\n") == ([], 0)
    for guard in ("never", "Forbidden", "not in", "assertNotIn", "does not appear",
                  "is not a surface", "blocklist"):
        assert _violations(f"{guard}: {DOMAIN}\n") == ([], 1), guard
    assert _violations(f"ok\n\n{DOMAIN}\n")[0] == [f"sample.md:3: {DOMAIN}"]


def test_scan_skips_binary_symlinks_and_missing_paths(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(f"visit {DOMAIN}\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0" + DOMAIN.encode())
    (tmp_path / "link.md").symlink_to(tmp_path / "doc.md")
    result = gate.scan(tmp_path, ["doc.md", "blob.bin", "link.md", "deleted.md"])
    assert result.scanned == 1
    assert result.violations == [f"doc.md:1: visit {DOMAIN}"]


@pytest.mark.skipif(GIT is None, reason="git executable is unavailable")
def test_main_fails_on_a_violation_and_on_an_empty_listing(tmp_path: Path, capsys) -> None:
    def git(*args: str) -> None:
        subprocess.run([GIT, *args], cwd=tmp_path, check=True, capture_output=True)  # noqa: S603

    git("init", "-q")
    assert gate.main(tmp_path) == 1
    assert "refusing to pass an empty scan" in capsys.readouterr().out
    (tmp_path / "README.md").write_text(f"Product: https://{DOMAIN}\n", encoding="utf-8")
    git("add", "README.md")
    assert gate.main(tmp_path) == 1
    assert f"- README.md:1: Product: https://{DOMAIN}" in capsys.readouterr().out


def test_main_fails_closed_when_files_cannot_be_listed(monkeypatch, capsys) -> None:
    def broken(_root: Path) -> list[str]:
        raise subprocess.CalledProcessError(128, ["git", "ls-files"])

    monkeypatch.setattr(gate, "listed_files", broken)
    assert gate.main() == 1
    assert "could not list repository files" in capsys.readouterr().out
