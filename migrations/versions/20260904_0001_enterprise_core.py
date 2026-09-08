"""Create tenant-scoped append-only enterprise core.

Revision ID: 20260904_0001
Revises: None
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = (
    "lyte_receipts",
    "lyte_idempotency_records",
    "lyte_memory_records",
    "lyte_anatomy_traces",
    "lyte_operational_records",
)


def _append_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.Column("basis_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("sequence >= 1", name="ck_sequence_positive"),
        sa.CheckConstraint("length(record_hash) = 64", name="ck_record_hash_length"),
        sa.CheckConstraint(
            "(sequence = 1 AND previous_hash IS NULL) OR "
            "(sequence > 1 AND previous_hash IS NOT NULL)",
            name="ck_previous_hash_shape",
        ),
    ]


def _scope_constraints(prefix: str) -> list[object]:
    return [
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            name=f"fk_{prefix}_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "sequence",
            name=f"uq_{prefix}_scope_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "record_hash",
            name=f"uq_{prefix}_scope_hash",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "lyte_tenants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "lyte_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["lyte_tenants.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workspace_tenant_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
    )
    op.create_table(
        "lyte_stream_heads",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("stream_kind", sa.String(length=32), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("last_sequence >= 0", name="ck_stream_head_sequence"),
        sa.CheckConstraint(
            "(last_sequence = 0 AND last_hash IS NULL) OR "
            "(last_sequence > 0 AND last_hash IS NOT NULL)",
            name="ck_stream_head_hash_shape",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            name="fk_stream_head_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "stream_kind"),
    )

    op.create_table(
        "lyte_receipts",
        *_append_columns(),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=256), nullable=False),
        sa.Column("truth_label", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        *_scope_constraints("receipt"),
    )
    op.create_index(
        "ix_receipt_subject",
        "lyte_receipts",
        ["tenant_id", "workspace_id", "subject_type", "subject_id"],
    )

    op.create_table(
        "lyte_idempotency_records",
        *_append_columns(),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("response_receipt_hash", sa.String(length=64), nullable=False),
        *_scope_constraints("idempotency"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "key_digest",
            name="uq_idempotency_scope_key",
        ),
    )

    op.create_table(
        "lyte_memory_records",
        *_append_columns(),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("memory_kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("truth_label", sa.String(length=32), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("subjects", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        *_scope_constraints("memory"),
    )
    op.create_index(
        "ix_memory_scope_digest",
        "lyte_memory_records",
        ["tenant_id", "workspace_id", "scope_digest", "sequence"],
    )

    op.create_table(
        "lyte_anatomy_traces",
        *_append_columns(),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("span_id", sa.String(length=36), nullable=False),
        sa.Column("parent_span_id", sa.String(length=36), nullable=True),
        sa.Column("organ", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("truth_label", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        *_scope_constraints("anatomy"),
    )
    op.create_index(
        "ix_anatomy_trace",
        "lyte_anatomy_traces",
        ["tenant_id", "workspace_id", "trace_id", "sequence"],
    )

    op.create_table(
        "lyte_operational_records",
        *_append_columns(),
        sa.Column("entity_kind", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("truth_label", sa.String(length=32), nullable=False),
        sa.Column("body_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_operational_version_positive"),
        *_scope_constraints("operational"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "entity_kind",
            "entity_id",
            "version",
            name="uq_operational_entity_version",
        ),
    )
    op.create_index(
        "ix_operational_entity_latest",
        "lyte_operational_records",
        ["tenant_id", "workspace_id", "entity_kind", "entity_id", "version"],
    )

    _create_immutability_guards()


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION lyte_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'Lyte append-only records cannot be updated or deleted';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION lyte_reject_append_only_mutation()
                """
            )
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER trg_{table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN
                  SELECT RAISE(ABORT, 'Lyte append-only records cannot be updated');
                END
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER trg_{table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN
                  SELECT RAISE(ABORT, 'Lyte append-only records cannot be deleted');
                END
                """
            )
    else:
        raise RuntimeError(f"unsupported database dialect: {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in reversed(IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute("DROP FUNCTION IF EXISTS lyte_reject_append_only_mutation()")
    elif dialect == "sqlite":
        for table in reversed(IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_delete")

    op.drop_index(
        "ix_operational_entity_latest", table_name="lyte_operational_records"
    )
    op.drop_table("lyte_operational_records")
    op.drop_index("ix_anatomy_trace", table_name="lyte_anatomy_traces")
    op.drop_table("lyte_anatomy_traces")
    op.drop_index("ix_memory_scope_digest", table_name="lyte_memory_records")
    op.drop_table("lyte_memory_records")
    op.drop_table("lyte_idempotency_records")
    op.drop_index("ix_receipt_subject", table_name="lyte_receipts")
    op.drop_table("lyte_receipts")
    op.drop_table("lyte_stream_heads")
    op.drop_table("lyte_workspaces")
    op.drop_table("lyte_tenants")
