# SPDX-License-Identifier: Apache-2.0
"""Prevent the exact Hub README validation failures from run 33923214101.

This is a regression contract for Lyte's checked-in, unquoted scalar metadata,
not a replacement for the provider's complete YAML or emoji validator.
Reference: https://huggingface.co/docs/hub/en/spaces-config-reference
"""
import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
COLORS = {"red", "yellow", "green", "blue", "indigo", "purple", "pink", "gray"}


def scalar(name: str) -> str:
    content = README.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "Space frontmatter must start the README"
    header, separator, _body = content[4:].partition("\n---\n")
    assert separator, "Space frontmatter must be closed"
    values = re.findall(rf"^{re.escape(name)}: ([^\n]+)$", header, re.MULTILINE)
    assert len(values) == 1, f"Expected one unquoted scalar for {name}"
    return values[0].strip()


def test_lyte_card_uses_a_real_emoji_not_a_geometric_text_symbol() -> None:
    # U+1F537 is an emoji; the previous U+2726 text star was rejected by Hub.
    assert scalar("emoji") == "🔷"


def test_lyte_card_uses_only_provider_supported_gradient_colors() -> None:
    assert scalar("colorFrom") in COLORS
    assert scalar("colorTo") in COLORS


def test_lyte_card_description_respects_observed_provider_limit() -> None:
    assert 1 <= len(scalar("short_description")) <= 60


def test_card_retains_the_existing_docker_runtime_contract() -> None:
    assert scalar("sdk") == "docker"
    assert scalar("app_port") == "7860"
    assert scalar("license") == "apache-2.0"
    assert scalar("pinned") == "false"
