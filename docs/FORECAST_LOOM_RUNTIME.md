# Forecast Loom runtime contract

Lyte exposes Forecast Loom at `POST /api/lyte/v2/forecast` through the existing analysis router.

The default provider is the deterministic `szl.robust-drift/v1` baseline. The IBM Granite adapter is an optional challenger and remains disabled unless `LYTE_GRANITE_ENABLED=true` is explicitly set after evidence-backed admission. A model's benchmark reputation alone cannot enable it.

Every successful forecast emits SHA-256 input/output receipts, bounded context/horizon metadata, explicit limitations, and `execution_authority: NONE`.

Canonical flow: GitHub source of truth -> Hugging Face evaluated/runtime projection -> a-11-oy.com product projection -> a11oy.net proof projection.
