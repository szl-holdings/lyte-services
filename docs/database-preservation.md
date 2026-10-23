# Operator database preservation

`python -m lyte.persistence.preservation` is an operator CLI for a SQLite online
backup and a checked, transactional import into an empty PostgreSQL schema.
It adds no HTTP endpoint, provider operation, restart, migration, or cutover.
The runtime's existing PostgreSQL adapter and Alembic migration remain canonical.

## Scope and evidence

The tool retains every supported table, checks SQLite integrity and foreign keys,
and verifies the five stream chains, heads, and hashed record projections. Unknown
tables, columns, views, or migration revisions fail closed. Logical fingerprints
cover every column, including IDs and fields outside the hash basis. It compares
those fingerprints before commit and from a separate connection after commit.

These checks establish preservation of the observed snapshot. They do not certify
the truth of its rows, establish signer trust, or authorize application activation.
Receipts are explicitly unsigned (`signature_status: UNAVAILABLE`) and
`cutover_authorized: false`. No credential, row payload, or connection string is
included in a receipt. The recovery bundle itself contains all database contents;
store it in an operator-controlled directory with private OS permissions and
retain it outside the provider's ephemeral filesystem.

## Preserve the running SQLite database

Access the existing database through an already authorized filesystem session
that does not restart the running service. Deploying this tool to obtain that
access would restart an ephemeral service before its data was preserved.

```sh
python -m lyte.persistence.preservation backup \
  --source /path/to/current/lyte.db --output /private/recovery/snapshot-001
```

The output directory must not exist. SQLite's online backup API includes committed
WAL data. The receipt describes the snapshot instant; later source writes are not
included. A final cutover requires an operator-controlled write freeze, a fresh
snapshot, and independent verification. Failed bundles are retained for diagnosis.
Files are flushed with `fsync`; directory entries are also flushed on POSIX.
Windows directory `fsync` is `UNAVAILABLE`, so crash durability of directory entries
is not claimed there. Power-loss/storage guarantees remain the operator's concern.

## Identify the destination

Provision and independently approve an empty PostgreSQL destination first. Run
the existing Alembic migration in the selected schema. Keep application writers
and schema migrations stopped during inspection/import/readback. The tool does
not provision a database or infer a tenant from a hostname.

Use a secret injection mechanism to set `LYTE_MIGRATION_TARGET_URL`; the CLI has no
URL/password argument. Supply an explicit username, password, single TCP host,
database, and optionally port (default 5432). Remote hosts require
`sslmode=verify-full` and an explicit readable `sslrootcert` file. Optional
`sslcert`/`sslkey` must be paired. Plaintext is accepted only when explicitly
configured with `sslmode=disable` for a literal loopback IP used in local testing.
DNS names, including `localhost`, never receive that exception.

The optional `LYTE_MIGRATION_SCHEMA` defaults to `public` and must be a single
lowercase SQL identifier. URL query connection overrides (`host`, `port`,
`service`, `options`, and all other unrecognized keys), duplicate values, and
ambient `PG*` variables are rejected. This prevents libpq defaults from silently
redirecting an approved destination or changing transport settings.

```sh
python -m lyte.persistence.preservation inspect-target
```

This operation is read-only and mints no receipt. Record its
`target_identity_sha256`, which binds the requested host/port and measured server
address/port, database, schema, username, TLS mode, and CA file hash. Inspect target fingerprints
and confirm zero rows before import. A changed server address (including a
pool/failover change) requires a new operator inspection.

## Import and verify

Use the SHA-256 in the successful backup receipt and the inspected target digest:

```sh
python -m lyte.persistence.preservation restore \
  --snapshot /private/recovery/snapshot-001/snapshot.sqlite \
  --snapshot-sha256 EXACT_BACKUP_SHA256 \
  --target-identity-sha256 EXACT_TARGET_SHA256 \
  --output /private/recovery/import-001
```

Restore makes its own flushed copy and verifies the supplied digest before
contacting PostgreSQL. It requires a standalone snapshot with no WAL/journal
sidecar. The import locks the exact Alembic and model tables before inspecting
their schema, requires the migrated target to be empty, and writes all rows in
one transaction. A busy target, incompatible schema, failed insert, or mismatched
transaction readback fails without committing. An unknown commit outcome is
reported as `COMMIT_OUTCOME_UNKNOWN`; do not blindly retry. Inspect the target
and reconcile the retained bundle first. A failure after a confirmed commit is
reported with `effect: COMMITTED` and `truth_label: BLOCKED`.

Complete independent restore/readback and the separate OIDC, provider durability,
source publication, and runtime gates before any release or restart. This CLI's
success is not those operational checks.

## Tests

`python -m pytest -q tests/test_database_preservation.py` exercises real SQLite
WAL backup, projection tampering, secret-safe failures, and rejected target
configurations. PostgreSQL cases require `LYTE_TEST_POSTGRES_URL` pointing to a
disposable test database; they create and remove only uniquely named test schemas.
The repository's `database-migrations` CI job runs these cases on its isolated
PostgreSQL service. Skipped PostgreSQL cases are not PostgreSQL evidence.
