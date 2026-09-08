"""Bounded, read-only observation of allowlisted public GitHub Actions runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from lyte.domain import ReceiptDraft, TruthLabel, isoformat_z, sha256_text

from .base import ConnectorPolicyError, ConnectorState, PayloadValidationError, strict_json_loads

GITHUB_API_ORIGIN = "https://api.github.com"
_REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True, slots=True)
class GitHubActionsLimits:
    max_pages: int = 3
    per_page: int = 50
    max_body_bytes: int = 1_000_000
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if not 1_024 <= self.max_body_bytes <= 5_000_000:
            raise ValueError("max_body_bytes must be between 1024 and 5000000")
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("read_timeout_seconds", self.read_timeout_seconds),
        ):
            if not 0.1 <= value <= 30.0:
                raise ValueError(f"{name} must be between 0.1 and 30 seconds")


@dataclass(frozen=True, slots=True)
class GitHubWorkflowRun:
    run_id: int
    workflow_name: str
    status: str
    conclusion: str | None
    duration_ms: int | None
    branch: str
    event: str
    head_sha: str
    created_at: str
    run_started_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "status": self.status,
            "conclusion": self.conclusion,
            "duration_ms": self.duration_ms,
            "branch": self.branch,
            "event": self.event,
            "head_sha": self.head_sha,
            "created_at": self.created_at,
            "run_started_at": self.run_started_at,
            "updated_at": self.updated_at,
            "truth_label": TruthLabel.REPORTED.value,
        }


@dataclass(frozen=True, slots=True)
class GitHubActionsResult:
    repository: str
    state: ConnectorState
    runs: tuple[GitHubWorkflowRun, ...]
    receipt: ReceiptDraft
    etag: str | None = None
    pages_fetched: int = 0
    complete: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ConnectorState(self.state))
        if self.state is ConnectorState.UNAVAILABLE:
            if not self.reason:
                raise ValueError("UNAVAILABLE GitHub results require a reason")
            if self.runs:
                raise ValueError("UNAVAILABLE GitHub results cannot contain runs")
        elif self.reason is not None:
            raise ValueError("available GitHub results cannot have an unavailable reason")
        if self.state is ConnectorState.OBSERVED_PARTIAL and self.complete:
            raise ValueError("partial results must declare complete=False")

    @property
    def truth_label(self) -> TruthLabel:
        return (
            TruthLabel.UNAVAILABLE
            if self.state is ConnectorState.UNAVAILABLE
            else TruthLabel.REPORTED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": "github_actions",
            "repository": self.repository,
            "state": self.state.value,
            "truth_label": self.truth_label.value,
            "reason": self.reason,
            "complete": self.complete,
            "pages_fetched": self.pages_fetched,
            "etag": self.etag,
            "run_count": len(self.runs),
            "runs": [run.to_dict() for run in self.runs],
        }


class GitHubActionsConnector:
    """Fetch workflow runs only for repositories fixed in constructor policy.

    Pagination URLs are constructed from the allowlisted repository and bounded
    page numbers. Remote ``Link`` URLs and redirects are never followed.
    """

    def __init__(
        self,
        allowed_repositories: set[str] | frozenset[str] | tuple[str, ...],
        *,
        limits: GitHubActionsLimits | None = None,
        client: httpx.Client | None = None,
        user_agent: str = "szl-lyte-enterprise/4",
    ) -> None:
        normalized = frozenset(self._normalize_repository(item) for item in allowed_repositories)
        if not normalized:
            raise ValueError("at least one GitHub repository must be allowlisted")
        self.allowed_repositories = normalized
        self.limits = limits or GitHubActionsLimits()
        self._owns_client = client is None
        timeout = httpx.Timeout(
            connect=self.limits.connect_timeout_seconds,
            read=self.limits.read_timeout_seconds,
            write=self.limits.read_timeout_seconds,
            pool=self.limits.connect_timeout_seconds,
        )
        self._client = client or httpx.Client(
            base_url=GITHUB_API_ORIGIN,
            follow_redirects=False,
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": user_agent,
            },
        )

    @staticmethod
    def _normalize_repository(repository: str) -> str:
        normalized = str(repository).strip()
        if not _REPOSITORY.fullmatch(normalized) or ".." in normalized:
            raise ConnectorPolicyError("repository must be a valid owner/name identifier")
        return normalized.lower()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubActionsConnector:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def fetch_workflow_runs(
        self,
        repository: str,
        *,
        etag: str | None = None,
    ) -> GitHubActionsResult:
        normalized = self._normalize_repository(repository)
        if normalized not in self.allowed_repositories:
            raise ConnectorPolicyError("repository is not in the code-defined allowlist")
        if etag is not None and (not isinstance(etag, str) or not 1 <= len(etag) <= 256):
            raise ConnectorPolicyError("ETag must contain 1-256 characters")

        runs: list[GitHubWorkflowRun] = []
        total_count: int | None = None
        first_etag: str | None = None
        pages_fetched = 0
        try:
            for page in range(1, self.limits.max_pages + 1):
                headers = {"If-None-Match": etag} if page == 1 and etag else {}
                path = f"/repos/{normalized}/actions/runs"
                with self._client.stream(
                    "GET",
                    GITHUB_API_ORIGIN + path,
                    params={"per_page": self.limits.per_page, "page": page},
                    headers=headers,
                    follow_redirects=False,
                ) as response:
                    pages_fetched += 1
                    if response.status_code == 304 and page == 1:
                        receipt = self._receipt(
                            normalized,
                            state=ConnectorState.NOT_MODIFIED,
                            run_count=0,
                            pages=pages_fetched,
                            complete=True,
                            etag=response.headers.get("etag") or etag,
                        )
                        return GitHubActionsResult(
                            repository=normalized,
                            state=ConnectorState.NOT_MODIFIED,
                            runs=(),
                            etag=response.headers.get("etag") or etag,
                            pages_fetched=pages_fetched,
                            receipt=receipt,
                        )
                    if 300 <= response.status_code < 400:
                        return self._unavailable(normalized, "redirect_rejected", pages_fetched)
                    if response.status_code != 200:
                        reason = (
                            "rate_limited_or_forbidden"
                            if response.status_code in {403, 429}
                            else f"upstream_http_{response.status_code}"
                        )
                        return self._unavailable(normalized, reason, pages_fetched)
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > self.limits.max_body_bytes:
                                return self._unavailable(
                                    normalized, "response_body_overflow", pages_fetched
                                )
                        except ValueError:
                            return self._unavailable(
                                normalized, "invalid_content_length", pages_fetched
                            )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.limits.max_body_bytes:
                            return self._unavailable(
                                normalized, "response_body_overflow", pages_fetched
                            )
                    decoded = strict_json_loads(
                        bytes(body),
                        max_bytes=self.limits.max_body_bytes,
                        path="github_response",
                    )
                    if not isinstance(decoded, Mapping):
                        raise PayloadValidationError("github_response must be an object")
                    raw_runs = decoded.get("workflow_runs")
                    count = decoded.get("total_count")
                    if not isinstance(raw_runs, list) or not isinstance(count, int) or count < 0:
                        raise PayloadValidationError(
                            "github_response requires workflow_runs and non-negative total_count"
                        )
                    if len(raw_runs) > self.limits.per_page:
                        raise PayloadValidationError("GitHub returned more runs than requested")
                    if total_count is None:
                        total_count = count
                        first_etag = response.headers.get("etag")
                    elif count != total_count:
                        raise PayloadValidationError("GitHub total_count changed during pagination")
                    runs.extend(self._parse_run(item, normalized) for item in raw_runs)
                    if len(raw_runs) < self.limits.per_page or len(runs) >= count:
                        break
        except (httpx.HTTPError, PayloadValidationError, UnicodeError, ValueError):
            return self._unavailable(normalized, "transport_or_payload_failure", pages_fetched)

        complete = total_count is not None and len(runs) >= total_count
        state = ConnectorState.OBSERVED if complete else ConnectorState.OBSERVED_PARTIAL
        receipt = self._receipt(
            normalized,
            state=state,
            run_count=len(runs),
            pages=pages_fetched,
            complete=complete,
            etag=first_etag,
        )
        return GitHubActionsResult(
            repository=normalized,
            state=state,
            runs=tuple(runs),
            receipt=receipt,
            etag=first_etag,
            pages_fetched=pages_fetched,
            complete=complete,
        )

    @staticmethod
    def _parse_time(value: Any, *, field: str) -> datetime:
        if not isinstance(value, str) or len(value) > 64:
            raise PayloadValidationError(f"GitHub run {field} is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PayloadValidationError(f"GitHub run {field} is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PayloadValidationError(f"GitHub run {field} must include a timezone")
        return parsed

    @classmethod
    def _parse_run(cls, value: Any, repository: str) -> GitHubWorkflowRun:
        if not isinstance(value, Mapping):
            raise PayloadValidationError("GitHub workflow run must be an object")
        required = {
            "id",
            "name",
            "status",
            "conclusion",
            "head_branch",
            "event",
            "head_sha",
            "created_at",
            "updated_at",
        }
        if not required.issubset(value):
            raise PayloadValidationError("GitHub workflow run is missing required fields")
        run_id = value["id"]
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise PayloadValidationError("GitHub workflow run id must be positive")
        text_fields: dict[str, str] = {}
        for field in ("name", "status", "head_branch", "event", "head_sha"):
            raw = value[field]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 256:
                raise PayloadValidationError(f"GitHub workflow run {field} is invalid")
            text_fields[field] = raw.strip()
        if not _SHA.fullmatch(text_fields["head_sha"]):
            raise PayloadValidationError("GitHub workflow run head_sha must be a 40-digit SHA")
        conclusion = value["conclusion"]
        if conclusion is not None and (
            not isinstance(conclusion, str) or not conclusion.strip() or len(conclusion) > 64
        ):
            raise PayloadValidationError("GitHub workflow run conclusion is invalid")
        created = cls._parse_time(value["created_at"], field="created_at")
        started = (
            cls._parse_time(value["run_started_at"], field="run_started_at")
            if value.get("run_started_at") is not None
            else created
        )
        updated = cls._parse_time(value["updated_at"], field="updated_at")
        if started < created or updated < started:
            raise PayloadValidationError("GitHub workflow run timestamps are inconsistent")
        duration = (
            int((updated - started).total_seconds() * 1_000)
            if text_fields["status"] == "completed"
            else None
        )
        return GitHubWorkflowRun(
            run_id=run_id,
            workflow_name=text_fields["name"],
            status=text_fields["status"],
            conclusion=conclusion.strip() if isinstance(conclusion, str) else None,
            duration_ms=duration,
            branch=text_fields["head_branch"],
            event=text_fields["event"],
            head_sha=text_fields["head_sha"].lower(),
            created_at=isoformat_z(created),
            run_started_at=isoformat_z(started),
            updated_at=isoformat_z(updated),
        )

    @staticmethod
    def _receipt(
        repository: str,
        *,
        state: ConnectorState,
        run_count: int,
        pages: int,
        complete: bool,
        etag: str | None,
        reason: str | None = None,
    ) -> ReceiptDraft:
        truth = (
            TruthLabel.UNAVAILABLE if state is ConnectorState.UNAVAILABLE else TruthLabel.REPORTED
        )
        return ReceiptDraft(
            kind="source.github_actions.observed",
            subject_type="repository",
            subject_id=repository,
            payload={
                "source_id": "github_actions",
                "state": state.value,
                "run_count": run_count,
                "pages_fetched": pages,
                "complete": complete,
                "etag_sha256": sha256_text(etag) if etag else None,
                "reason": reason,
                "read_only": True,
            },
            truth_label=truth,
            evidence_refs=(f"github:{repository}",),
        )

    @classmethod
    def _unavailable(cls, repository: str, reason: str, pages_fetched: int) -> GitHubActionsResult:
        receipt = cls._receipt(
            repository,
            state=ConnectorState.UNAVAILABLE,
            run_count=0,
            pages=pages_fetched,
            complete=False,
            etag=None,
            reason=reason,
        )
        return GitHubActionsResult(
            repository=repository,
            state=ConnectorState.UNAVAILABLE,
            runs=(),
            receipt=receipt,
            pages_fetched=pages_fetched,
            complete=False,
            reason=reason,
        )


__all__ = [
    "GITHUB_API_ORIGIN",
    "GitHubActionsConnector",
    "GitHubActionsLimits",
    "GitHubActionsResult",
    "GitHubWorkflowRun",
]
