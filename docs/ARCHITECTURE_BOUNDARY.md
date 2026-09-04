# Architecture boundary

Lyte is the dedicated business-observability product surface powered by the A11oy governance substrate. It can ingest or normalize specialist telemetry, but it does not silently become the source of truth for data it did not observe. The product may recommend investigation or abstain; it cannot authorize or execute remediation. Existing APM, OpenTelemetry, cloud, log, cost, and business systems remain valid source planes during adoption.
