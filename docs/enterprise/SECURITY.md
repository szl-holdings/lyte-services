# Security boundary

Lyte assumes connector input, webhook input, browser input, and identity claims
are hostile until validated.

- Production mutations require a verified OIDC JWT and an adequate RBAC role.
  Explicit development identities work only when `LYTE_DEV_AUTH_ENABLED=true`
  outside production.
- Tenant and workspace are derived from the verified principal and checked at
  every database query. Caller headers cannot widen a verified scope.
- GitHub fetches use HTTPS, a fixed host and organization/repository allowlist,
  no redirects, bounded pages, a byte ceiling, and short timeouts.
- OTLP JSON and event bodies have hard byte/record limits and reject malformed,
  unsupported, non-finite, or over-limit input instead of truncating it.
- Governed webhook signatures use raw bytes, HMAC SHA-256, constant-time
  comparison, bounded clock skew, nonce replay defense, and durable idempotency.
- Logs, traces, receipts, and support output exclude credentials, raw bearer or
  session tokens, prompts, responses, and known sensitive telemetry keys.
- CSP disallows external script/font/tracker origins. The container runs as UID
  10001 and has no privileged or effector path.

Hatun never authorizes or executes. Action requests are immutable simulations
for human review. Security-relevant `UNAVAILABLE` state fails closed.

Known deployment requirement: a production tenant must configure PostgreSQL and
OIDC. A public demo can be ready without either only because it permits no
anonymous state-changing operation.
