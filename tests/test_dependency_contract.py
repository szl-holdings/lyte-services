"""Dependency contract for lyte-services (standard library and pytest only; executes no
repository code).

Every third-party import in the repository's Python files is classified below, and the
classification must agree with pyproject.toml. "core" is [project].dependencies; "test",
"browser" and "granite" are [project.optional-dependencies]. requirements.txt and
requirements-test.txt mirror core and test, which tools/verify_release_scaffolding.py checks.

Areas follow where code runs. Runtime code (the wheel packages, the space/ and migrations/
directories that the image copies, and the lyte_runtime.py shim) may import only core
requirements at module scope; an import inside a function, or one guarded by
``except ImportError``, may come from an opt-in extra. Tests may import core and test
requirements unguarded, and an extra only behind a guard or pytest.importorskip. Tooling
(tools/, benchmarks/, a11oy_factory/, .github/scripts/) may import any declared group.
opentelemetry is a namespace shared by several distributions, so its imports are matched by
module path. Unclassified imports and files outside a known area fail closed. The negative
controls at the end show that each check can fail.
"""

from __future__ import annotations

import ast
import functools
import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE, TEST, BROWSER, GRANITE = "core", "test", "browser", "granite"

# import name -> (distribution, group that must list it)
DECLARED = {
    "alembic": ("alembic", CORE),
    "fastapi": ("fastapi", CORE),
    "httpx": ("httpx", CORE),
    "jwt": ("PyJWT", CORE),
    "prometheus_client": ("prometheus-client", CORE),
    "pydantic": ("pydantic", CORE),
    "sqlalchemy": ("SQLAlchemy", CORE),
    "uvicorn": ("uvicorn", CORE),
    "packaging": ("packaging", TEST),
    "pytest": ("pytest", TEST),
    "yaml": ("PyYAML", TEST),
    "playwright": ("playwright", BROWSER),
    "websockets": ("websockets", BROWSER),
    "pandas": ("pandas", GRANITE),
    "torch": ("torch", GRANITE),
    "tsfm_public": ("granite-tsfm", GRANITE),
}
# opentelemetry module path -> core distribution; any other opentelemetry module fails.
OPENTELEMETRY = {
    "opentelemetry.trace": "opentelemetry-api",
    "opentelemetry.sdk": "opentelemetry-sdk",
    "opentelemetry.instrumentation.fastapi": "opentelemetry-instrumentation-fastapi",
}
RUNTIME = ("lyte", "lyte_engine", "lyte_api", "space", "migrations", "lyte_runtime.py")
TOOLING = ("tools", "benchmarks", "a11oy_factory")
_STDLIB = frozenset(sys.stdlib_module_names)
_GUARDS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
_IMPORT_CALLS = {"__import__", "import_module", "importorskip"}
_SKIP_DIRS = {"__pycache__", "build", "dist", "node_modules"}
_REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[\w.,\s-]*\])?\s*"
    r"((?:[<>=!~]=?\s*[\w.*+!-]+\s*,?\s*)*)"
)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution(requirement: str) -> str:
    """Normalized distribution name; environment markers and URLs fail closed."""
    match = _REQUIREMENT.fullmatch(requirement.strip())
    if match is None:
        raise ValueError(f"unsupported requirement: {requirement!r}")
    return _norm(match.group(1))


def area_of(path: str) -> str | None:
    top = path.split("/")[0]
    if top in RUNTIME:
        return "runtime"
    if top == "tests":
        return "test"
    if top in TOOLING or path.startswith(".github/scripts/"):
        return "tooling"
    return None


def repo_sources(root: Path = REPO_ROOT) -> dict[str, str]:
    """{posix path: text} for every .py file outside dot-directories (except .github),
    caches, build output and virtual environments."""
    sources = {}
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIRS
            and not d.endswith(".egg-info")
            and not (d.startswith(".") and not (here == root and d == ".github"))
            and not (here / d / "pyvenv.cfg").exists()
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                path = here / name
                sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def local_names(sources: dict[str, str]) -> set[str]:
    """Top-level names a checkout provides: its directories and root-level modules."""
    return {path.split("/")[0].removesuffix(".py") for path in sources}


def _call_name(node: ast.AST) -> str | None:
    func = getattr(node, "func", None)
    return getattr(func, "id", getattr(func, "attr", None))


def _modules(node: ast.AST) -> list[str]:
    """Dotted modules that an absolute import, or a literal __import__, import_module or
    pytest.importorskip call, loads. Relative imports are always local."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level or node.module is None:
            return []
        return [node.module if a.name == "*" else f"{node.module}.{a.name}" for a in node.names]
    if (
        isinstance(node, ast.Call)
        and _call_name(node) in _IMPORT_CALLS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return [node.args[0].value]
    return []


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(getattr(t, "id", getattr(t, "attr", None)) in _GUARDS for t in types)


def _is_main_block(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return (
        isinstance(node, ast.If)
        and isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


Site = tuple[str, str, str, int, str, bool]


def import_sites(sources: dict[str, str]) -> list[Site]:
    """(top-level name, module, path, line, scope, guarded) for every import.

    scope is "module" (runs on import) or "deferred" (a function body or an
    ``if __name__ == "__main__":`` block). guarded means a ``try`` in the same scope
    catches ImportError around the import, or the import is pytest.importorskip.
    """
    sites: list[Site] = []

    def visit(node: ast.AST, path: str, scope: str, guarded: bool) -> None:
        for field, value in ast.iter_fields(node):
            for child in value if isinstance(value, list) else [value]:
                if not isinstance(child, ast.AST):
                    continue
                inner_scope, inner_guarded = scope, guarded
                if field == "body" and isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                ):
                    inner_scope, inner_guarded = "deferred", False
                elif field == "body" and isinstance(node, (ast.Try, ast.TryStar)):
                    inner_guarded = guarded or any(map(_catches_import_error, node.handlers))
                elif field == "body" and _is_main_block(node):
                    inner_scope = "deferred"
                skipped = _call_name(child) == "importorskip"
                for module in _modules(child):
                    top = module.split(".")[0]
                    site = (top, module, path, child.lineno, inner_scope, inner_guarded or skipped)
                    sites.append(site)
                visit(child, path, inner_scope, inner_guarded)

    for path, text in sorted(sources.items()):
        visit(ast.parse(text, filename=path), path, "module", False)
    return sites


def _otel_prefix(module: str) -> str | None:
    return next((p for p in OPENTELEMETRY if module == p or module.startswith(p + ".")), None)


def _group(top: str, module: str) -> str | None:
    if top == "opentelemetry":
        return CORE if _otel_prefix(module) else None
    return DECLARED[top][1] if top in DECLARED else None


def classification_problems(sites: list[Site] | tuple[Site, ...], local: set[str]) -> list[str]:
    problems = []
    for top, module, path, line, scope, guarded in sites:
        if top in local or top in _STDLIB:
            continue
        where, area, group = f"{path}:{line}", area_of(path), _group(top, module)
        if group is None:
            problems.append(f"{where} imports unclassified {module!r}")
        elif area == "runtime" and group == TEST:
            problems.append(f"{where} imports test requirement {top!r} in runtime code")
        elif area == "runtime" and scope == "module" and not guarded and group != CORE:
            problems.append(
                f"{where} imports {top!r} ({group}) at module scope; runtime code "
                "may load only core requirements on import"
            )
        elif area == "test" and not guarded and group not in (CORE, TEST):
            problems.append(
                f"{where} imports {top!r} ({group}) without a guard or pytest.importorskip"
            )
    return problems


def read_groups(text: str) -> dict[str, list[str]]:
    project = tomllib.loads(text)["project"]
    return {CORE: list(project.get("dependencies", [])), **project.get("optional-dependencies", {})}


def declaration_problems(groups: dict[str, list[str]]) -> list[str]:
    listed = {group: {distribution(r) for r in reqs} for group, reqs in groups.items()}
    wanted = [(dist, group, name) for name, (dist, group) in DECLARED.items()]
    wanted += [(dist, CORE, module) for module, dist in OPENTELEMETRY.items()]
    return sorted(
        f"{dist} missing from [{group}] (imported as {name!r})"
        for dist, group, name in wanted
        if _norm(dist) not in listed.get(group, set())
    )


@functools.cache
def _scan() -> tuple[dict[str, str], tuple[Site, ...]]:
    sources = repo_sources()
    return sources, tuple(import_sites(sources))


def _pyproject() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_every_python_file_has_an_area() -> None:
    sources, _ = _scan()
    assert [path for path in sources if area_of(path) is None] == []
    assert {area_of(path) for path in sources} == {"runtime", "test", "tooling"}
    wheel = tomllib.loads(_pyproject())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert set(wheel["packages"]) <= set(RUNTIME)


def test_every_third_party_import_is_classified() -> None:
    sources, sites = _scan()
    assert classification_problems(sites, local_names(sources)) == []


def test_classification_matches_what_the_repository_imports() -> None:
    # Stale entries and an empty scan both fail.
    sources, sites = _scan()
    local = local_names(sources)
    external = [site for site in sites if site[0] not in local and site[0] not in _STDLIB]
    assert {site[0] for site in external} == set(DECLARED) | {"opentelemetry"}
    assert {_otel_prefix(site[1]) for site in external if site[0] == "opentelemetry"} == set(
        OPENTELEMETRY
    )


def test_pyproject_declares_every_classified_import() -> None:
    assert declaration_problems(read_groups(_pyproject())) == []


def _problems(path: str, source: str) -> list[str]:
    return classification_problems(import_sites({path: source}), {"lyte", "tests", "tools"})


def test_negative_control_pre_contract_manifest_is_rejected() -> None:
    # The manifest before this contract: no browser or granite extra, no packaging in test.
    groups = read_groups(_pyproject())
    groups.pop(BROWSER, None)
    groups.pop(GRANITE, None)
    groups[TEST] = [r for r in groups[TEST] if distribution(r) != "packaging"]
    assert declaration_problems(groups) == [
        "granite-tsfm missing from [granite] (imported as 'tsfm_public')",
        "packaging missing from [test] (imported as 'packaging')",
        "pandas missing from [granite] (imported as 'pandas')",
        "playwright missing from [browser] (imported as 'playwright')",
        "torch missing from [granite] (imported as 'torch')",
        "websockets missing from [browser] (imported as 'websockets')",
    ]


def test_negative_control_module_name_is_not_the_distribution_name() -> None:
    # tsfm_public ships in granite-tsfm; a requirement named after the module does not count.
    groups = read_groups(_pyproject())
    groups[GRANITE] = [
        "tsfm-public==0.3.12" if distribution(r) == "granite-tsfm" else r
        for r in groups.get(GRANITE, [])
    ]
    missing = "granite-tsfm missing from [granite] (imported as 'tsfm_public')"
    assert missing in declaration_problems(groups)


def test_negative_control_unclassified_import_fails_closed() -> None:
    # Every name of a multi-name import is checked, not only the first.
    assert _problems("lyte/x.py", "import json, requests\n") == [
        "lyte/x.py:1 imports unclassified 'requests'"
    ]


def test_negative_control_area_rules_are_enforced() -> None:
    assert _problems("lyte/x.py", "import pandas\n") == [
        "lyte/x.py:1 imports 'pandas' (granite) at module scope; runtime code "
        "may load only core requirements on import"
    ]
    assert _problems("lyte/x.py", "def f():\n    import pandas\n") == []
    guarded = "try:\n    import pandas\nexcept ImportError:\n    pandas = None\n"
    assert _problems("lyte/x.py", guarded) == []
    assert _problems("lyte/x.py", "def f():\n    import pytest\n") == [
        "lyte/x.py:2 imports test requirement 'pytest' in runtime code"
    ]
    assert _problems("tests/test_x.py", "import playwright\n") == [
        "tests/test_x.py:1 imports 'playwright' (browser) without a guard or "
        "pytest.importorskip"
    ]
    skip = 'import pytest\npd = pytest.importorskip("pandas")\n'
    assert _problems("tests/test_x.py", skip) == []
    assert _problems("tools/x.py", "import torch\n") == []
    assert area_of("newdir/x.py") is None


def test_negative_control_opentelemetry_is_matched_by_module_path() -> None:
    assert _problems("lyte/x.py", "from opentelemetry import trace\n") == []
    exporter = "from opentelemetry.exporter.otlp.proto.http import trace_exporter\n"
    assert _problems("lyte/x.py", exporter) == [
        "lyte/x.py:1 imports unclassified "
        "'opentelemetry.exporter.otlp.proto.http.trace_exporter'"
    ]
    assert len(_problems("lyte/x.py", "import opentelemetry\n")) == 1


@pytest.mark.parametrize(
    "requirement",
    [
        "granite-tsfm==0.3.12; python_version < '3.14'",
        "torch @ https://example.invalid/torch.whl",
        "",
    ],
)
def test_negative_control_unsupported_requirements_fail_closed(requirement: str) -> None:
    assert distribution("PyJWT[crypto]==2.15.1") == "pyjwt"
    with pytest.raises(ValueError, match="unsupported requirement"):
        distribution(requirement)
