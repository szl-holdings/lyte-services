# Lyte open observability benchmark

This lane measures Lyte against predeclared, vendor-neutral acceptance gates. It
does not call, scrape, emulate, or reverse-engineer a proprietary service, and
it does not convert a feature count or visual opinion into a superiority claim.

The fixture in `fixtures/checkout-observability-v1.json` is synthetic and
licensed `CC0-1.0`. `targets.json` is committed beside the runner so the gates
are visible before a run. Passing a gate is labeled `MEASURED_PARITY`; missing a
gate is `MEASURED_GAP`. `MEASURED_ADVANTAGE` is deliberately reserved for a
future like-for-like external measurement and is never assigned by this runner.

Run from the repository root with Python 3.12:

```powershell
python -m benchmarks.observability.run_benchmark `
  --source-revision <exact-40-character-git-sha> `
  --working-tree-state clean
```

For a quicker contract check, reduce the repetitions explicitly:

```powershell
python -m benchmarks.observability.run_benchmark `
  --source-revision 0123456789abcdef0123456789abcdef01234567 `
  --working-tree-state modified `
  --workflow-iterations 1 `
  --query-iterations 5 `
  --ingest-batches 2 `
  --spans-per-batch 2
```

The canonical JSON and its derived Markdown and CSV are written to
`artifacts/benchmarks/`. Every run records the fixture and target hashes, exact
command, environment, raw timing samples, source revision, database mode, and
limitations. The runner never records the local benchmark authentication
credential.

`UI keyboard completion` and `mobile overflow` require real browser evidence.
First run the application, then capture Edge/Chromium evidence and bind it into
the benchmark with the exact same source revision:

```powershell
python tools/capture_ui_evidence.py `
  --base-url http://127.0.0.1:7860 `
  --output-dir artifacts/final/screenshots `
  --json-output artifacts/final/lyte-ui-responsive.json
python -m benchmarks.observability.run_benchmark `
  --source-revision <exact-40-character-git-sha> `
  --working-tree-state clean `
  --browser-evidence artifacts/final/lyte-ui-responsive.json
```

The runner rejects browser evidence from a different source revision. Without
this input, those dimensions remain `NOT_TESTED` with null measurements rather
than being inferred from static HTML or CSS.
