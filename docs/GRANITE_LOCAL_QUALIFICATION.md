# Local Granite checkpoint comparison

Run the supplied benchmark from the repository root with Python 3.12:

```text
python -m benchmarks.forecast_loom_admission --candidate baseline --output baseline-control.json
python -m benchmarks.granite_checkpoint_probe --model-dir /existing/verified/checkpoint --output checkpoint-comparison.json
```

The checkpoint probe never downloads a model. It checks exact SHA-256 values of the official IBM Granite PatchTST FM r2 checkpoint's config, safetensors weights, license, and model card before loading it locally. The immutable model revision is `b125275f9204d37cbb81fe47b9cf5e08a521e829`. IBM supplies the checkpoint; SZL supplies the provider integration, measurement code, and governance. The checkpoint offers an Apache-2.0 license option.

The baseline and challenger receive exactly the same 512 training points and the same held-out truth at horizons 12 and 48. Each signal's loss is normalized by naive one-step differences from its training context only. Qualification requires positive improvement on every declared signal, complete pair coverage, and deterministic output hashes for both providers. A reduction in a large-unit loss cannot hide a regression in a smaller-unit signal. The version 2 report deliberately provides no raw average across different signal units.

The baseline-against-itself control reports `NOT_ADMITTED`. Its command exits successfully because it is a control measurement, not because it establishes an improvement. Zero training scales, missing pairs, changed contexts, ambiguous JSON, and nonfinite values remain held. Supplied series have bounded byte and signal counts, carry an exact input byte hash, and cannot silently replace the built-in synthetic signals.

The local qualification performed on September 29, 2026 used the real pinned checkpoint with deterministic synthetic inputs. Twelve of twelve paired forecasts improved and all repeated output hashes matched. The corrected Windows measurement observed the actual worker process: peak working set 2,052,358,144 bytes, peak pagefile usage 4,520,554,496 bytes, and about 161 seconds including supervision. The original supervisor observed the small Python launcher rather than the forecasting worker; its approximately 4 MB reading is invalid. The original files remain retained as an erratum and are not qualified resource evidence.

These are local measurements in an observed CPU environment. They do not establish significance, independent experimental replications, quality on external data, a complete production dependency lock, GPU performance, a production release, or estate completion. The two horizons from the same synthetic signal are correlated comparisons. Use the SZL paired science protocol to design a preregistered experiment with independent units and inspect the actual data, split construction, plan timestamp, and identity controls before making a statistical claim.

Actual worker memory and execution deadlines must be observed and bounded separately. The portable checkpoint probe does not pretend to enforce a memory budget. Keep the probe, benchmark, input, model, dependency, resource, and source identities together in the experiment receipt. Do not relabel an earlier observation as a run of a later source commit.
