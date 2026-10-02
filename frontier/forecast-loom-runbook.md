# Forecast Loom admission runbook

1. Verify the exact upstream checkpoint loads and exposes ordered quantiles.
2. Run both providers on identical bounded training context and held-out truth. Require complete unique signal pairs, identical request hashes, and a positive finite normalization scale from training differences only.
3. Retain the repository commit/tree and dirty status, hashes of all four executed local implementations, observed dependencies, checkpoint revision/bytes, measured worker resources, metrics and limitations. Record an HF job ID only when a real HF job executed. Refuse a source change during the measurement.
4. Apply `forecast-loom-admission-policy.json` v2: permit `ADMITTED_FOR_EVALUATION` only when every signal improves in its training-normalized scale, both providers repeat output hashes, and the quantile contract passes. A raw average across units cannot grant admission. The identity control reports no improvement. Historical v1 receipts remain historical; they do not establish v2 admission.
5. Keep `LYTE_GRANITE_ENABLED=false` in production until representative Lyte telemetry and deployment-SLO evidence pass.
6. Publish only through the canonical A11oy single writer; never create an ad-hoc second Hugging Face writer.
7. Project proof receipts to a11oy.net after the runtime projection is live-verified.

Local synthetic comparisons do not establish statistical independence or significance. Independent experimental units, a preregistered plan, representative external data and a meaningful minimum effect require a separate researcher-reviewed qualification. The portable checkpoint probe does not enforce resource budgets; use an actual-worker supervisor.
