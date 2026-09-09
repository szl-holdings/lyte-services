# Forecast Loom admission runbook

1. Verify the exact upstream checkpoint loads and exposes ordered quantiles.
2. Run the candidate against the deterministic SZL baseline on bounded qualification workloads.
3. Record the exact source SHA, HF job ID, environment, model ID, metrics, and limitations.
4. Permit `ADMITTED_FOR_EVALUATION` only when aggregate MAE improves and the quantile contract passes.
5. Keep `LYTE_GRANITE_ENABLED=false` in production until representative Lyte telemetry and deployment-SLO evidence pass.
6. Publish only through the canonical A11oy single writer; never create an ad-hoc second Hugging Face writer.
7. Project proof receipts to a11oy.net after the runtime projection is live-verified.
