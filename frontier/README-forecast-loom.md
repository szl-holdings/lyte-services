# Forecast Loom frontier lane

- Source authority: `szl-holdings/lyte-services`
- Runtime route: `POST /api/lyte/v2/forecast`
- Default engine: `szl.robust-drift/v1`
- Challenger: `ibm-granite/granite-timeseries-patchtst-fm-r2`
- Granite runtime: disabled by default and fail-closed
- Evidence: exact HF job IDs and measured admission reports
- Execution authority: none

No upstream model weights, branding, or architecture implementation are copied into SZL source. The upstream checkpoint is consumed through an adapter and must satisfy SZL-owned governance and benchmark contracts.
