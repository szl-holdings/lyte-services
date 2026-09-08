"""Deterministic SAMPLE dataset persisted through the same repositories as real data."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lyte.domain import (
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TruthLabel,
    sha256_text,
)
from lyte.governance import MemoryDraft, MemoryKind
from lyte.intelligence import CheckoutScenario, build_checkout_scenario
from lyte.persistence import LyteStore, OperationalConflict

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class _Seed:
    kind: OperationalEntityKind
    entity_id: str
    name: str
    body: dict[str, Any]
    truth_label: TruthLabel
    evidence_refs: tuple[str, ...]


def sample_memory_digest(scope: Scope) -> str:
    """Return a stable public-fixture scope digest; it is not an auth credential."""

    return sha256_text(
        f"szl.lyte.public-sample-memory/v1\x00{scope.tenant_id}\x00{scope.workspace_id}"
    )


def _source_revision() -> str | None:
    for name in ("LYTE_SOURCE_REVISION", "SOURCE_REVISION", "GITHUB_SHA"):
        value = os.getenv(name, "").strip().lower()
        if _SHA.fullmatch(value):
            return value
    return None


def _seed_rows(scenario: CheckoutScenario) -> tuple[_Seed, ...]:
    timestamp = scenario.generated_at.isoformat().replace("+00:00", "Z")
    rows: list[_Seed] = []

    def add(
        kind: OperationalEntityKind,
        entity_id: str,
        name: str,
        body: dict[str, Any],
        truth_label: TruthLabel,
        *refs: str,
    ) -> None:
        rows.append(_Seed(kind, entity_id, name, body, truth_label, tuple(refs)))

    for citation in scenario.citations:
        add(
            OperationalEntityKind.EVIDENCE_REFERENCE,
            citation.citation_id,
            citation.title,
            citation.to_dict(),
            citation.truth_label,
            citation.citation_id,
        )
    for service in scenario.services:
        add(
            OperationalEntityKind.SERVICE,
            service.service_id,
            service.name,
            service.to_dict(),
            service.truth_label,
            *service.evidence_refs,
        )
        add(
            OperationalEntityKind.ENTITY,
            f"service:{service.service_id}",
            service.name,
            {"entity_type": "service", "target_id": service.service_id},
            service.truth_label,
            *service.evidence_refs,
        )
    for source_id, source_type, state in (
        ("github-actions-fixture", "github_actions", "SAMPLE_CONFIGURED"),
        ("otlp-fixture", "otlp_json_subset", "SAMPLE_CONFIGURED"),
        ("governed-event-fixture", "governed_event", "SAMPLE_CONFIGURED"),
    ):
        add(
            OperationalEntityKind.SOURCE,
            source_id,
            source_id.replace("-", " ").title(),
            {
                "source_type": source_type,
                "state": state,
                "sample_only": True,
                "arbitrary_url_fetch": False,
            },
            TruthLabel.SAMPLE,
            "sample:source-catalog:v1",
        )
    add(
        OperationalEntityKind.SOURCE_CURSOR,
        "sample-fixture-cursor",
        "Sample fixture cursor",
        {"position": timestamp, "durable": True, "sample_only": True},
        TruthLabel.SAMPLE,
        "sample:source-catalog:v1",
    )
    add(
        OperationalEntityKind.DEPENDENCY_EDGE,
        "checkout-api~payment-api",
        "Checkout depends on Payment",
        {
            "from": "checkout-api",
            "to": "payment-api",
            "relation": "DECLARED_SERVICE_DEPENDENCY",
            "causality_claimed": False,
        },
        TruthLabel.SAMPLE,
        "sample:service-window:payment:20260904t1200z",
    )
    for journey in scenario.journeys:
        add(
            OperationalEntityKind.CUSTOMER_JOURNEY,
            journey.journey_id,
            journey.name,
            journey.to_dict(),
            journey.truth_label,
            *journey.evidence_refs,
        )
        for step in journey.steps:
            add(
                OperationalEntityKind.JOURNEY_STEP,
                f"{journey.journey_id}:{step.step_id}",
                step.name,
                step.to_dict(),
                step.truth_label,
                *step.evidence_refs,
            )
    for outcome in scenario.outcomes:
        add(
            OperationalEntityKind.BUSINESS_OUTCOME,
            outcome.outcome_id,
            outcome.name,
            outcome.to_dict(),
            outcome.truth_label,
            *outcome.evidence_refs,
        )
        add(
            OperationalEntityKind.OUTCOME_MEASUREMENT,
            f"{outcome.outcome_id}:{timestamp}",
            f"{outcome.name} measurement",
            {"outcome_id": outcome.outcome_id, "indicators": outcome.to_dict()["indicators"]},
            outcome.truth_label,
            *outcome.evidence_refs,
        )
    for agent in scenario.agents:
        add(
            OperationalEntityKind.AGENT_TRACE_SUMMARY,
            agent.agent_id,
            agent.name,
            agent.to_dict(),
            agent.truth_label,
            *agent.evidence_refs,
        )
    for incident in scenario.incidents:
        add(
            OperationalEntityKind.INCIDENT,
            incident.incident_id,
            incident.title,
            incident.to_dict(),
            incident.truth_label,
            *incident.evidence_refs,
        )
        add(
            OperationalEntityKind.INCIDENT_EVENT,
            f"{incident.incident_id}:opened",
            "Sample incident opened",
            {"incident_id": incident.incident_id, "state": incident.state, "at": timestamp},
            incident.truth_label,
            *incident.evidence_refs,
        )
    add(
        OperationalEntityKind.TELEMETRY_WINDOW,
        "checkout:20260904t1200z",
        "Checkout sample telemetry window",
        {
            "window_start": timestamp,
            "request_count": 100_000,
            "good_event_count": 98_600,
            "bounded": True,
        },
        TruthLabel.SAMPLE,
        "sample:service-window:checkout:20260904t1200z",
    )
    add(
        OperationalEntityKind.DEPLOYMENT_EVENT,
        "github-run-1842",
        "Sample checkout deployment",
        {"workflow_run": 1842, "service_id": "checkout-api", "at": timestamp},
        TruthLabel.SAMPLE,
        "sample:github:run:1842",
    )
    add(
        OperationalEntityKind.SIGNAL,
        "checkout-burn-14x",
        "Checkout error-budget burn",
        {"service_id": "checkout-api", "burn_rate": 14.0, "state": "BREACHED"},
        TruthLabel.SAMPLE,
        "sample:service-window:checkout:20260904t1200z",
    )
    add(
        OperationalEntityKind.RECOMMENDATION,
        "review-checkout-evidence",
        "Review checkout and payment evidence",
        {
            "recommended_next_review": "Validate payment latency and deployment evidence",
            "can_authorize": False,
            "causality_claimed": False,
        },
        TruthLabel.MODELED,
        "sample:service-window:checkout:20260904t1200z",
        "sample:service-window:payment:20260904t1200z",
    )
    add(
        OperationalEntityKind.DECISION,
        "hatun-checkout-review-1",
        "Hatun checkout review",
        {
            "decision": "REVIEW",
            "authority_mode": "HUMAN_SOVEREIGN",
            "can_authorize": False,
            "can_execute": False,
        },
        TruthLabel.SAMPLE,
        "sample:hatun-review:checkout:1",
    )
    for request in scenario.action_requests:
        add(
            OperationalEntityKind.ACTION_REQUEST,
            request.request_id,
            "Simulated rollback request",
            request.to_dict(),
            request.truth_label,
            *request.evidence_refs,
        )
    for index, frame in enumerate(scenario.playback):
        offset_token = (
            f"m{abs(frame.offset_minutes):03d}"
            if frame.offset_minutes < 0
            else f"p{frame.offset_minutes:03d}"
        )
        add(
            OperationalEntityKind.REPLAY_SNAPSHOT,
            f"checkout:{index:02d}:{offset_token}",
            f"Checkout playback T{frame.offset_minutes:+d}",
            frame.to_dict(),
            frame.truth_label,
            *frame.evidence_refs,
        )
    add(
        OperationalEntityKind.OUTCOME_VERIFICATION,
        "sample-rollback-not-verified",
        "Outcome verification unavailable",
        {
            "action_request_id": "sample-rollback-request-1",
            "state": "UNAVAILABLE",
            "reason": "no action was approved or executed",
            "production_outcome_claimed": False,
        },
        TruthLabel.UNAVAILABLE,
        "sample:hatun-review:checkout:1",
    )
    add(
        OperationalEntityKind.USER_SUBJECT,
        "public-sample-explorer",
        "Public sample explorer",
        {"authentication_method": "PUBLIC_SAMPLE", "production_authority": False},
        TruthLabel.SAMPLE,
        "sample:identity-boundary:v1",
    )
    add(
        OperationalEntityKind.ROLE_BINDING,
        "public-sample-explorer:viewer",
        "Public sample viewer binding",
        {
            "subject_id": "public-sample-explorer",
            "role": "viewer",
            "sample_scope_only": True,
        },
        TruthLabel.SAMPLE,
        "sample:identity-boundary:v1",
    )
    if len({(row.kind, row.entity_id) for row in rows}) != len(rows):
        raise RuntimeError("demo seed contains duplicate operational identities")
    return tuple(rows)


def _upsert_rows(
    store: LyteStore,
    scope: Scope,
    seeds: Iterable[_Seed],
    *,
    observed_at: datetime,
) -> None:
    revision = _source_revision()
    drafts: list[OperationalRecordDraft] = []
    for seed in seeds:
        drafts.append(
            OperationalRecordDraft(
                entity_kind=seed.kind,
                entity_id=seed.entity_id,
                name=seed.name,
                body=seed.body,
                truth_label=seed.truth_label,
                evidence_refs=seed.evidence_refs,
                source_revision=revision,
                observed_at=observed_at,
                metadata={"fixture": "sample.checkout-degradation.v1"},
            )
        )
    try:
        store.upsert_operational_batch(scope, drafts)
    except OperationalConflict:
        # A concurrent process may have seeded the same persistent volume.
        # Re-read and converge item-by-item without accepting different data.
        for draft in drafts:
            current = store.get_operational(scope, draft.entity_kind, draft.entity_id)
            if current is not None and (
                current.body_json == draft.body
                and current.truth_label == draft.truth_label.value
                and current.source_revision == revision
            ):
                continue
            store.upsert_operational(
                scope,
                draft,
                expected_version=current.version if current else 0,
            )


def seed_demo(store: LyteStore, scope: Scope) -> dict[str, Any]:
    """Persist the deterministic fixture and return its durable proof summary."""

    scenario = build_checkout_scenario()
    receipt = store.append_receipt(
        scope,
        ReceiptDraft(
            kind="demo.scenario.seeded",
            subject_type="scenario",
            subject_id=scenario.scenario_id,
            payload={
                "scenario_sha256": scenario.scenario_sha256,
                "data_mode": "SAMPLE",
                "persistent_projection": True,
                "effectors_enabled": False,
            },
            truth_label=TruthLabel.SAMPLE,
            evidence_refs=tuple(citation.citation_id for citation in scenario.citations),
        ),
        idempotency_key="sample-checkout-scenario-v1",
    )
    rows = _seed_rows(scenario)
    _upsert_rows(store, scope, rows, observed_at=scenario.generated_at)
    digest = sample_memory_digest(scope)
    if not store.list_memory(scope, scope_digest=digest, limit=1):
        store.append_memory(
            scope,
            MemoryDraft(
                scope_digest=digest,
                kind=MemoryKind.OBSERVATION,
                summary=(
                    "SAMPLE checkout degradation connects a deployment window, payment "
                    "latency, journey completion, modeled revenue risk, and human review."
                ),
                truth_label=TruthLabel.SAMPLE,
                evidence_refs=(receipt.record_hash,),
                subjects=("checkout-api", "payment-api", "checkout", "checkout-revenue"),
                metadata={"data_mode": "SAMPLE", "approved_knowledge": False},
            ),
            receipt_hash=receipt.record_hash,
        )
    return {
        "scenario_id": scenario.scenario_id,
        "scenario_sha256": scenario.scenario_sha256,
        "receipt_hash": receipt.record_hash,
        "operational_records": len(rows),
        "data_mode": "SAMPLE",
    }


__all__ = ["sample_memory_digest", "seed_demo"]
