from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from lyte.domain import (
    AnatomyOrgan,
    AnatomyTrace,
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TenantSpec,
    TraceState,
    TruthLabel,
    TruthValue,
    WorkspaceSpec,
    allowed_bad_rate,
    apdex,
    availability_sli,
    error_budget_burn_rate,
    error_rate,
    lambda_advisory,
    mean_time_to_recovery,
    requests_per_second,
    revenue_at_risk,
)
from lyte.governance import (
    AuthenticationError,
    AuthenticationService,
    AuthenticationUnavailable,
    HatunDecision,
    HatunRequest,
    MemoryDraft,
    MemoryKind,
    digest_scope_token,
    evaluate_hatun,
)
from lyte.persistence import (
    STREAM_ANATOMY,
    STREAM_IDEMPOTENCY,
    STREAM_MEMORY,
    STREAM_OPERATIONAL,
    STREAM_RECEIPTS,
    Database,
    IdempotencyConflict,
    ImmutableRecordError,
    LyteStore,
    OperationalConflict,
    ReceiptRecord,
)
from lyte.settings import ConfigurationError, Environment, Settings


def test_truth_and_formulas_preserve_unavailable() -> None:
    unavailable = TruthValue.unavailable("no production sample")
    assert unavailable.to_dict() == {
        "value": None,
        "truth_label": "UNAVAILABLE",
        "reason": "no production sample",
        "evidence_refs": [],
        "observed_at": None,
        "metadata": {},
    }
    with pytest.raises(ValueError, match="must be null"):
        TruthValue(0, TruthLabel.UNAVAILABLE, reason="missing")

    result = availability_sli(None, 1_000)
    assert result.value is None
    assert result.truth_label is TruthLabel.UNAVAILABLE
    assert result.reason == "missing required input(s): good_events"

    zero_denominator = error_budget_burn_rate(0, 0, 0.999)
    assert zero_denominator.truth_label is TruthLabel.UNAVAILABLE
    assert zero_denominator.value is None

    modeled = revenue_at_risk(10_000, 0.91, 0.96, 50.0)
    assert modeled.value == pytest.approx(25_000.0)
    assert modeled.truth_label is TruthLabel.MODELED
    assert modeled.can_authorize is False

    missing_axis = lambda_advisory({"service": 0.9, "journey": None})
    assert missing_axis.value is None
    assert missing_axis.truth_label is TruthLabel.UNAVAILABLE
    assert missing_axis.proof_status == "CONJECTURE_1_ADVISORY"

    with pytest.raises(ValueError, match="not boolean"):
        availability_sli(True, 10)  # type: ignore[arg-type]


def test_minimum_formula_set_has_units_and_explicit_zero_denominators() -> None:
    formulas = (
        availability_sli(990, 1_000),
        error_rate(10, 1_000),
        allowed_bad_rate(0.999),
        error_budget_burn_rate(990, 1_000, 0.999),
        requests_per_second(6_000, 60.0),
        mean_time_to_recovery([60.0, 120.0, 180.0]),
        apdex(800, 100, 1_000),
    )
    assert [result.value for result in formulas] == pytest.approx(
        [0.99, 0.01, 0.001, 10.0, 100.0, 120.0, 0.85]
    )
    assert all(result.unit for result in formulas)
    assert all(result.can_authorize is False for result in formulas)
    assert requests_per_second(10, 0.0).truth_label is TruthLabel.UNAVAILABLE
    assert mean_time_to_recovery([]).value is None
    assert apdex(0, 0, 0).truth_label is TruthLabel.UNAVAILABLE


def test_settings_and_auth_are_explicit_and_production_fails_closed() -> None:
    tenant_id = uuid4()
    workspace_id = uuid4()
    token = "local-explicit-token-" + "x" * 32
    settings = Settings.from_env(
        {
            "LYTE_ENV": "development",
            "LYTE_DEV_AUTH_ENABLED": "true",
            "LYTE_DEV_AUTH_TOKEN": token,
            "LYTE_WEBHOOK_HMAC_SECRET": "webhook-secret-" + "y" * 32,
            "LYTE_DEV_TENANT_ID": str(tenant_id),
            "LYTE_DEV_WORKSPACE_ID": str(workspace_id),
        }
    )
    service = AuthenticationService.from_settings(settings)
    principal = service.authenticate_header(f"Bearer {token}")
    assert principal.tenant_id == tenant_id
    assert workspace_id in principal.workspace_ids
    assert principal.roles == {"admin", "operator"}
    assert token not in repr(settings)
    assert settings.webhook_hmac_secret not in repr(settings)
    assert settings.database_url not in repr(settings)
    assert token not in repr(principal)
    with pytest.raises(AuthenticationError):
        service.authenticate_header("Bearer wrong-token")

    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        Settings.from_env(
            {
                "LYTE_ENV": "production",
                "DATABASE_URL": "sqlite:///prod.db",
                "OIDC_ISSUER": "https://issuer.example",
                "OIDC_AUDIENCE": "lyte",
                "OIDC_JWKS_URL": "https://issuer.example/.well-known/jwks.json",
            }
        )
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        Settings(environment="production", database_url="sqlite:///prod.db")  # type: ignore[arg-type]
    with pytest.raises(AuthenticationUnavailable, match="no authentication verifier"):
        AuthenticationService.from_settings(Settings(environment=Environment.DEVELOPMENT))
    postgres = Settings.from_env(
        {"LYTE_ENV": "test", "DATABASE_URL": "postgres://user@db.example/lyte"}
    )
    assert postgres.database_url == "postgresql+psycopg://user@db.example/lyte"
    with pytest.raises(ConfigurationError, match="WEBHOOK_HMAC_SECRET"):
        Settings(webhook_hmac_secret="x" * 8)


def test_second_brain_digest_and_hatun_never_grant_execution() -> None:
    scope = Scope(uuid4(), uuid4())
    raw_token = "s" * 48
    digest = digest_scope_token(scope, raw_token)
    assert len(digest) == 64
    assert raw_token not in digest
    assert digest_scope_token(scope, raw_token) == digest
    assert digest_scope_token(Scope(scope.tenant_id, uuid4()), raw_token) != digest

    observation = MemoryDraft(
        scope_digest=digest,
        kind=MemoryKind.OBSERVATION,
        summary="Checkout latency rose after the declared deployment window.",
        truth_label=TruthLabel.REPORTED,
        evidence_refs=("receipt:abc",),
        subjects=("checkout",),
    )
    assert raw_token not in repr(observation)
    with pytest.raises(ValueError, match="approved knowledge"):
        MemoryDraft(
            scope_digest=digest,
            kind=MemoryKind.APPROVED_KNOWLEDGE,
            summary="Demonstration-only claim.",
            truth_label=TruthLabel.SAMPLE,
            evidence_refs=("fixture:demo",),
        )

    review = evaluate_hatun(
        HatunRequest(
            action_type="open-investigation",
            evidence_labels=(TruthLabel.MEASURED, TruthLabel.REPORTED),
        )
    )
    assert review.decision is HatunDecision.REVIEW
    assert review.can_execute is False

    sample_review = evaluate_hatun(
        HatunRequest(
            action_type="simulate-rollback",
            evidence_labels=(TruthLabel.SAMPLE, TruthLabel.MODELED),
        )
    )
    assert sample_review.decision is HatunDecision.REVIEW
    assert sample_review.truth_label is TruthLabel.SAMPLE
    assert sample_review.can_execute is False
    assert any("demonstration-only" in reason for reason in sample_review.reasons)

    abstain = evaluate_hatun(
        HatunRequest(
            action_type="open-investigation",
            evidence_labels=(TruthLabel.UNAVAILABLE,),
        )
    )
    assert abstain.decision is HatunDecision.ABSTAIN

    denied = evaluate_hatun(
        HatunRequest(
            action_type="rollback",
            evidence_labels=(TruthLabel.MEASURED,),
            requests_execution=True,
        )
    )
    assert denied.decision is HatunDecision.DENY
    assert denied.can_execute is False


@pytest.fixture
def scoped_store() -> tuple[Database, LyteStore, Scope]:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    store = LyteStore(database)
    tenant = TenantSpec("acme-corp", "Acme Corporation")
    store.create_tenant(tenant)
    workspace = WorkspaceSpec(tenant.id, "checkout-ops", "Checkout Operations")
    store.create_workspace(workspace)
    yield database, store, Scope(tenant.id, workspace.id)
    database.dispose()


def test_receipt_and_idempotency_streams_are_scoped_and_hash_chained(
    scoped_store: tuple[Database, LyteStore, Scope],
) -> None:
    _, store, scope = scoped_store
    draft = ReceiptDraft(
        kind="analysis.completed",
        subject_type="service",
        subject_id="checkout-api",
        payload={"burn_rate": 2.4, "causality_claimed": False},
        truth_label=TruthLabel.MEASURED,
        evidence_refs=("github:run:123",),
    )
    first = store.append_receipt(scope, draft, idempotency_key="request-000000000001")
    replay = store.append_receipt(scope, draft, idempotency_key="request-000000000001")
    assert replay.id == first.id
    assert replay.record_hash == first.record_hash

    second = store.append_receipt(
        scope,
        ReceiptDraft(
            kind="review.requested",
            subject_type="service",
            subject_id="checkout-api",
            payload={"decision": "REVIEW", "can_execute": False},
            truth_label=TruthLabel.MODELED,
        ),
    )
    assert second.sequence == 2
    assert second.previous_hash == first.record_hash
    assert store.get_receipt(scope, first.record_hash).id == first.id  # type: ignore[union-attr]
    assert [row.id for row in store.list_receipts(scope, limit=1, offset=0)] == [first.id]
    assert [row.id for row in store.list_receipts(scope, limit=1, offset=1)] == [second.id]
    assert store.verify_chain(scope, STREAM_RECEIPTS).valid
    idempotency = store.verify_chain(scope, STREAM_IDEMPOTENCY)
    assert idempotency.valid and idempotency.record_count == 1

    conflict = ReceiptDraft(
        kind="analysis.completed",
        subject_type="service",
        subject_id="different-service",
        payload={"burn_rate": 1.0},
        truth_label=TruthLabel.MEASURED,
    )
    with pytest.raises(IdempotencyConflict):
        store.append_receipt(scope, conflict, idempotency_key="request-000000000001")

    other_tenant = TenantSpec("receipt-other", "Receipt Other")
    store.create_tenant(other_tenant)
    other_workspace = WorkspaceSpec(other_tenant.id, "receipt-space", "Receipt Space")
    store.create_workspace(other_workspace)
    other_scope = Scope(other_tenant.id, other_workspace.id)
    store.append_receipt(other_scope, draft)
    assert len(store.list_receipts(other_scope)) == 1
    assert store.get_receipt(other_scope, first.record_hash) is None
    with pytest.raises(ValueError, match="offset"):
        store.list_receipts(scope, offset=-1)


def test_memory_and_anatomy_are_digest_scoped_and_immutable(
    scoped_store: tuple[Database, LyteStore, Scope],
) -> None:
    database, store, scope = scoped_store
    token = "m" * 48
    digest = digest_scope_token(scope, token)
    receipt = store.append_receipt(
        scope,
        ReceiptDraft(
            kind="observation.recorded",
            subject_type="journey",
            subject_id="checkout",
            payload={"status": "WATCH"},
            truth_label=TruthLabel.REPORTED,
            evidence_refs=("source:public-status",),
        ),
    )
    memory = store.append_memory(
        scope,
        MemoryDraft(
            scope_digest=digest,
            kind=MemoryKind.OBSERVATION,
            summary="Checkout entered WATCH during the observation window.",
            truth_label=TruthLabel.REPORTED,
            evidence_refs=(receipt.record_hash,),
            subjects=("checkout",),
        ),
        receipt_hash=receipt.record_hash,
    )
    assert token not in str(memory.basis_json)
    assert [row.id for row in store.list_memory(scope, scope_digest=digest)] == [memory.id]
    assert store.list_memory(scope, scope_digest="0" * 64) == []
    assert store.verify_chain(scope, STREAM_MEMORY).valid

    started = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    trace = AnatomyTrace.start(
        scope,
        organ=AnatomyOrgan.TRUST_GATE,
        operation="hatun.evaluate",
        input_sha256="1" * 64,
        attributes={"decision_boundary": "review-only"},
        now=started,
    ).finish(
        state=TraceState.COMPLETED,
        output_sha256="2" * 64,
        now=started + timedelta(milliseconds=4),
    )
    stored_trace = store.append_anatomy_trace(trace, created_at=started)
    assert stored_trace.organ == "trust_gate"
    assert store.verify_chain(scope, STREAM_ANATOMY).valid

    with database.session() as session:
        row = session.scalar(select(ReceiptRecord).where(ReceiptRecord.id == receipt.id))
        assert row is not None
        row.kind = "tampered"
        with pytest.raises(ImmutableRecordError):
            session.flush()


def test_operational_records_are_allowlisted_versioned_and_tenant_isolated(
    scoped_store: tuple[Database, LyteStore, Scope],
) -> None:
    _, store, first_scope = scoped_store
    required_kinds = {
        "Source",
        "SourceCursor",
        "Entity",
        "UserSubject",
        "RoleBinding",
        "Service",
        "DependencyEdge",
        "CustomerJourney",
        "JourneyStep",
        "BusinessOutcome",
        "OutcomeMeasurement",
        "TelemetryWindow",
        "AgentTraceSummary",
        "DeploymentEvent",
        "Incident",
        "IncidentEvent",
        "Signal",
        "Recommendation",
        "Decision",
        "ActionRequest",
        "OutcomeVerification",
        "EvidenceReference",
        "ReplaySnapshot",
    }
    assert {kind.value for kind in OperationalEntityKind} == required_kinds

    other_tenant = TenantSpec("other-corp", "Other Corporation")
    store.create_tenant(other_tenant)
    other_workspace = WorkspaceSpec(other_tenant.id, "checkout-ops", "Checkout Operations")
    store.create_workspace(other_workspace)
    other_scope = Scope(other_tenant.id, other_workspace.id)

    def service_draft(owner: str, state: str) -> OperationalRecordDraft:
        return OperationalRecordDraft(
            entity_kind=OperationalEntityKind.SERVICE,
            entity_id="checkout-api",
            name="Checkout API",
            body={"owner": owner, "state": state},
            truth_label=TruthLabel.REPORTED,
            evidence_refs=(f"catalog:{owner}",),
            source_revision="a" * 40,
        )

    first = store.insert_operational(first_scope, service_draft("tenant-one", "WATCH"))
    other = store.insert_operational(other_scope, service_draft("tenant-two", "HEALTHY"))
    assert first.entity_id == other.entity_id
    assert first.name == other.name
    assert first.tenant_id != other.tenant_id

    first_read = store.get_operational(first_scope, OperationalEntityKind.SERVICE, "checkout-api")
    other_read = store.get_operational(other_scope, OperationalEntityKind.SERVICE, "checkout-api")
    assert first_read is not None and first_read.body_json["owner"] == "tenant-one"
    assert other_read is not None and other_read.body_json["owner"] == "tenant-two"

    updated = store.upsert_operational(
        first_scope,
        service_draft("tenant-one", "CRITICAL"),
        expected_version=1,
    )
    assert updated.version == 2
    assert (
        store.get_operational(
            first_scope, OperationalEntityKind.SERVICE, "checkout-api", version=1
        ).body_json["state"]
        == "WATCH"
    )  # type: ignore[union-attr]
    assert (
        store.get_operational(first_scope, OperationalEntityKind.SERVICE, "checkout-api").body_json[
            "state"
        ]
        == "CRITICAL"
    )  # type: ignore[union-attr]
    assert (
        store.get_operational(other_scope, OperationalEntityKind.SERVICE, "checkout-api").body_json[
            "state"
        ]
        == "HEALTHY"
    )  # type: ignore[union-attr]

    assert len(store.list_operational(first_scope)) == 1
    assert len(store.list_operational(other_scope)) == 1

    for kind, entity_id in (
        (OperationalEntityKind.SOURCE, "github-actions"),
        (OperationalEntityKind.INCIDENT, "incident-001"),
    ):
        store.insert_operational(
            first_scope,
            OperationalRecordDraft(
                entity_kind=kind,
                entity_id=entity_id,
                name=entity_id,
                body={"state": "OBSERVED"},
                truth_label=TruthLabel.REPORTED,
                evidence_refs=(f"source:{entity_id}",),
            ),
        )
    all_rows = store.list_operational(first_scope, limit=10)
    first_page = store.list_operational(first_scope, limit=2, offset=0)
    second_page = store.list_operational(first_scope, limit=2, offset=2)
    assert [row.id for row in first_page + second_page] == [row.id for row in all_rows]
    assert all(row.tenant_id == str(first_scope.tenant_id) for row in all_rows)
    assert len(store.list_operational(other_scope, limit=10, offset=0)) == 1
    with pytest.raises(ValueError, match="offset"):
        store.list_operational(first_scope, offset=-1)
    with pytest.raises(ValueError, match="offset"):
        store.list_operational(first_scope, offset=100_001)

    with pytest.raises(OperationalConflict):
        store.insert_operational(first_scope, service_draft("tenant-one", "HEALTHY"))
    with pytest.raises(OperationalConflict, match="expected version"):
        store.upsert_operational(
            first_scope,
            service_draft("tenant-one", "HEALTHY"),
            expected_version=1,
        )
    chain = store.verify_chain(first_scope, STREAM_OPERATIONAL)
    assert chain.valid and chain.record_count == 4


def test_operational_batch_is_atomic_chained_and_skips_unchanged(
    scoped_store: tuple[Database, LyteStore, Scope],
) -> None:
    _, store, scope = scoped_store
    drafts = tuple(
        OperationalRecordDraft(
            entity_kind=OperationalEntityKind.SERVICE,
            entity_id=f"service-{index}",
            name=f"Service {index}",
            body={"index": index},
            truth_label=TruthLabel.SAMPLE,
            evidence_refs=(f"sample:service:{index}",),
        )
        for index in range(3)
    )
    written = store.upsert_operational_batch(scope, drafts)
    assert len(written) == 3
    assert [row.sequence for row in written] == list(
        range(written[0].sequence, written[0].sequence + 3)
    )
    assert store.upsert_operational_batch(scope, drafts) == ()
    verification = store.verify_chain(scope, STREAM_OPERATIONAL)
    assert verification.valid is True
    assert verification.record_count == 3
    with pytest.raises(ValueError, match="duplicate"):
        store.upsert_operational_batch(scope, (drafts[0], drafts[0]))


def test_initial_migration_installs_database_immutability_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "lyte-migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
            triggers = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        assert "lyte_operational_records" in tables
        assert "lyte_receipts" in tables
        assert len(triggers) >= 10
        assert "trg_lyte_operational_records_immutable_update" in triggers
        assert "trg_lyte_operational_records_immutable_delete" in triggers
    finally:
        engine.dispose()
