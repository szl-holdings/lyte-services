"""Fixed, bounded first-party public source connectors."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from .core import (
    GITHUB_REPOSITORIES,
    REPOSITORY,
    USER_AGENT,
    SourceUnavailable,
    percentile,
    sha256_json,
)
from .memory import observation_receipt


def _bounded_get_json(
    url: str,
    *,
    query: Mapping[str, str],
    max_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, Any, str]:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "api.github.com"
        or parts.username
        or parts.password
    ):
        raise SourceUnavailable("source destination failed the fixed allowlist")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_READ_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        ) as client:
            with client.stream("GET", url, params=query, headers=headers) as response:
                if 300 <= response.status_code < 400:
                    raise SourceUnavailable("upstream redirect rejected")
                if response.status_code < 200 or response.status_code >= 300:
                    raise SourceUnavailable(
                        f"GitHub returned HTTP {response.status_code}"
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise SourceUnavailable("upstream response exceeds byte budget")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise SourceUnavailable("upstream response exceeds byte budget")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SourceUnavailable("GitHub returned invalid JSON") from exc
    except SourceUnavailable:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SourceUnavailable(
            f"source transport failed: {type(exc).__name__}"
        ) from exc
    source_url = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(dict(query)),
            "",
        )
    )
    return raw, payload, source_url


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def github_workflow_observation(
    repository: str,
    *,
    limit: int = 20,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe first-party GitHub Actions telemetry for an allowlisted repo."""
    repo = repository.strip().lower()
    if REPOSITORY.fullmatch(repo) is None or repo not in GITHUB_REPOSITORIES:
        raise ValueError("repository is not in the Lyte observability allowlist")
    bounded_limit = max(1, min(50, int(limit)))
    url = f"https://api.github.com/repos/szl-holdings/{repo}/actions/runs"
    query = {"per_page": str(bounded_limit)}
    raw, payload, source_url = _bounded_get_json(
        url,
        query=query,
        max_bytes=4_000_000,
        transport=transport,
    )
    if not isinstance(payload, dict) or not isinstance(
        payload.get("workflow_runs"), list
    ):
        raise SourceUnavailable("GitHub workflow payload schema is not recognized")
    rows: list[dict[str, Any]] = []
    completed = 0
    successful = 0
    failed = 0
    running = 0
    durations: list[float] = []
    for item in payload["workflow_runs"][:bounded_limit]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "UNAVAILABLE")
        conclusion = item.get("conclusion")
        if status == "completed":
            completed += 1
            successful += int(conclusion == "success")
            failed += int(conclusion not in {None, "success", "skipped", "neutral"})
        else:
            running += 1
        start = _parse_iso(item.get("run_started_at") or item.get("created_at"))
        end = _parse_iso(item.get("updated_at"))
        duration = (
            max(0.0, end - start)
            if start is not None and end is not None and end >= start
            else None
        )
        if duration is not None:
            durations.append(duration)
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "display_title": item.get("display_title"),
                "head_branch": item.get("head_branch"),
                "head_sha": item.get("head_sha"),
                "event": item.get("event"),
                "status": status,
                "conclusion": conclusion,
                "run_number": item.get("run_number"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "duration_seconds": (
                    round(duration, 3) if duration is not None else None
                ),
                "html_url": item.get("html_url"),
            }
        )
    success_rate = successful / completed if completed else None
    observation = {
        "repository": f"szl-holdings/{repo}",
        "runs": rows,
        "returned": len(rows),
        "completed": completed,
        "successful": successful,
        "failed": failed,
        "running": running,
        "success_rate": round(success_rate, 8) if success_rate is not None else None,
        "p50_duration_seconds": (
            round(percentile(durations, 0.50) or 0.0, 3) if durations else None
        ),
        "p95_duration_seconds": (
            round(percentile(durations, 0.95) or 0.0, 3) if durations else None
        ),
        "source": "GitHub Actions public REST API",
        "truth_label": "REPORTED",
    }
    scope_placeholder = "0" * 64
    receipt = observation_receipt(
        scope=scope_placeholder,
        kind="github-actions",
        payload=observation,
        truth_label="REPORTED",
        source_url=source_url,
    )
    # Session scope is replaced by the API boundary before persistence.
    return observation, receipt


def rebind_receipt_scope(receipt: Mapping[str, Any], scope: str) -> dict[str, Any]:
    """Re-mint a source receipt against the caller's hashed session scope."""
    body = {
        key: value
        for key, value in receipt.items()
        if key
        not in {
            "receipt_id",
            "receipt_algorithm",
            "raw_session_token_recorded",
            "session_scope_sha256",
        }
    }
    body["session_scope_sha256"] = scope
    return {
        **body,
        "receipt_id": sha256_json(body),
        "receipt_algorithm": "SHA-256",
        "raw_session_token_recorded": False,
    }

