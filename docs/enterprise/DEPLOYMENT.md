# Deployment

## Local sample

Build and run the non-root container with a writable database directory:

```bash
revision="$(git rev-parse HEAD)"
docker build --build-arg "LYTE_SOURCE_REVISION=${revision}" -t "szl-lyte:${revision}" .
docker run --rm -p 7860:7860 -v lyte-data:/data "szl-lyte:${revision}"
```

The default container mode is an explicitly labeled public sample. Its SQLite
volume is real file persistence but is not represented as a production HA data
service.

## Production

1. Provision PostgreSQL and an OIDC issuer/audience.
2. Set `DATABASE_URL`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL`.
   Keep sources within the code-defined allowlist. Supply secrets through the deployment secret
   store, never source or command-line arguments.
3. Run `alembic upgrade head` from the exact candidate image.
4. Start Uvicorn with `LYTE_ENV=production`, `LYTE_DEMO_MODE=false`, and
   `LYTE_DEV_AUTH_ENABLED=false`.
5. Verify build/source bindings, readiness, RBAC denial, tenant isolation,
   ingest limits, and a complete receipt chain before admitting traffic.

The canonical Hugging Face Space is a product-led sample surface. It is
published only by the protected A11oy single writer from an exact merged Lyte
revision. Customer data residency, backups, retention, private networking, and
availability depend on the selected customer deployment and are not implied by
the public Space.

Rollback means republishing the last passing, immutable source closure and
verifying its independent HF revision. Database migrations in this wave are
additive; do not roll back by deleting customer records.
