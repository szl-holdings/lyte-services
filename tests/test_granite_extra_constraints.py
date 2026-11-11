# Copyright 2026 SZL Holdings — SPDX-License-Identifier: Apache-2.0
"""The granite extra must be installable: its pins must satisfy the granite-tsfm release it pins.

granite-tsfm declares version ranges for torch and pandas. A pin outside them makes
``pip install szl-lyte-enterprise[granite]`` unresolvable, and no CI job sees it: the
granite-adapter job installs pandas only and uses test doubles for torch and tsfm_public.
Dependabot PR #62 merged exactly that (torch 2.13.0 against granite-tsfm 0.3.12's torch <2.12).

GRANITE_TSFM_REQUIRES records the Requires-Dist ranges of each granite-tsfm release the extra
may pin, copied from that release's package metadata. A release with no record fails closed, so
a granite-tsfm bump must add its ranges here in the same change.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]

GRANITE_TSFM_REQUIRES = {
    "0.3.12": {"pandas": ">=2.3.3,<4", "torch": ">=2.10,<2.12"},
}

_PINS = {"granite-tsfm": "0.3.12", "pandas": "3.1.0", "torch": "2.11.0"}


def granite_pins(pyproject_text: str) -> dict[str, str]:
    """Exact ``name==version`` pins of the granite extra; anything else fails closed."""
    extra = tomllib.loads(pyproject_text)["project"]["optional-dependencies"]["granite"]
    pins: dict[str, str] = {}
    for text in extra:
        requirement = Requirement(text)
        specifiers = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise ValueError(f"granite requirement is not an exact pin: {text}")
        pins[canonicalize_name(requirement.name)] = specifiers[0].version
    return pins


def granite_problems(pins: dict[str, str]) -> list[str]:
    """Each granite pin must lie inside the range its granite-tsfm release requires."""
    release = pins.get("granite-tsfm")
    if release is None:
        return ["the granite extra does not pin granite-tsfm"]
    if release not in GRANITE_TSFM_REQUIRES:
        return [f"granite-tsfm {release} has no entry in GRANITE_TSFM_REQUIRES"]
    problems = []
    for name, required in sorted(GRANITE_TSFM_REQUIRES[release].items()):
        if name not in pins:
            problems.append(f"the granite extra does not pin {name}")
        elif Version(pins[name]) not in SpecifierSet(required):
            problems.append(
                f"{name}=={pins[name]} is outside granite-tsfm {release}'s requirement "
                f"{name}{required}"
            )
    return problems


def dependabot_problems(dependabot_text: str, pins: dict[str, str]) -> list[str]:
    """Dependabot's root pip entry must not propose a torch above the recorded range."""
    release = pins.get("granite-tsfm", "")
    required = GRANITE_TSFM_REQUIRES.get(release, {}).get("torch")
    if required is None:
        return [f"granite-tsfm {release} has no torch entry in GRANITE_TSFM_REQUIRES"]
    upper = [spec.version for spec in SpecifierSet(required) if spec.operator == "<"]
    if len(upper) != 1:
        return [f"torch{required} has no single exclusive upper bound"]
    expected = [f">={upper[0]}"]
    entries = [
        entry
        for entry in yaml.safe_load(dependabot_text)["updates"]
        if entry.get("package-ecosystem") == "pip" and entry.get("directory") == "/"
    ]
    if len(entries) != 1:
        return ["dependabot.yml must have exactly one pip entry for directory '/'"]
    ignored = [
        rule.get("versions")
        for rule in entries[0].get("ignore") or []
        if rule.get("dependency-name") == "torch"
    ]
    if expected not in ignored:
        return [f"dependabot.yml must ignore torch versions {expected[0]} in the root pip entry"]
    return []


def _pyproject() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_granite_pins_satisfy_the_pinned_granite_tsfm_release() -> None:
    assert granite_problems(granite_pins(_pyproject())) == []


def test_dependabot_does_not_propose_torch_above_the_granite_range() -> None:
    text = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert dependabot_problems(text, granite_pins(_pyproject())) == []


def test_negative_control_the_merged_torch_bump_is_rejected() -> None:
    # The manifest after Dependabot PR #62: torch 2.13.0 beside granite-tsfm 0.3.12.
    assert granite_problems({**_PINS, "torch": "2.13.0"}) == [
        "torch==2.13.0 is outside granite-tsfm 0.3.12's requirement torch>=2.10,<2.12"
    ]


def test_negative_control_unrecorded_release_and_missing_pins_fail_closed() -> None:
    assert granite_problems({**_PINS, "granite-tsfm": "0.3.13"}) == [
        "granite-tsfm 0.3.13 has no entry in GRANITE_TSFM_REQUIRES"
    ]
    assert granite_problems({"pandas": "3.1.0", "torch": "2.11.0"}) == [
        "the granite extra does not pin granite-tsfm"
    ]
    assert granite_problems({"granite-tsfm": "0.3.12", "pandas": "2.3.2"}) == [
        "pandas==2.3.2 is outside granite-tsfm 0.3.12's requirement pandas>=2.3.3,<4",
        "the granite extra does not pin torch",
    ]


@pytest.mark.parametrize(
    "requirement",
    ["torch>=2.10", "torch==2.11.*", "torch==2.11.0; python_version < '3.14'"],
)
def test_negative_control_non_exact_granite_requirements_fail_closed(requirement: str) -> None:
    text = (
        "[project.optional-dependencies]\n"
        f'granite = ["granite-tsfm==0.3.12", "pandas==3.1.0", "{requirement}"]\n'
    )
    with pytest.raises(ValueError, match="not an exact pin"):
        granite_pins(text)


def test_negative_control_dependabot_without_the_torch_ignore_is_rejected() -> None:
    base = "version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n"
    rule = "    ignore:\n      - dependency-name: torch\n        versions: ['{}']\n"
    problem = ["dependabot.yml must ignore torch versions >=2.12 in the root pip entry"]
    assert dependabot_problems(base, _PINS) == problem
    assert dependabot_problems(base + rule.format(">=2.14"), _PINS) == problem
    assert dependabot_problems(base + rule.format(">=2.12"), _PINS) == []
