# Data governance

Lyte stores normalized operational summaries, entity relationships, evidence
references, decisions, and append-only receipts. It does not need raw prompts,
responses, bearer tokens, session tokens, or connector credentials.

Every consequential field has one of `MEASURED`, `REPORTED`, `MODELED`,
`SAMPLE`, `ROADMAP`, or `UNAVAILABLE`. Revenue at risk includes currency,
formula inputs, and whether its basis is reported or modeled. Correlation is not
promoted to causation.

Retention metadata is attached to scoped records. Deletion and export are
customer-deployment responsibilities and must preserve legal/audit holds. The
public sample ships no customer data and no external trackers.

Second Brain separates observations from approved organizational knowledge,
uses a stable partition derived from the authenticated tenant and workspace,
and returns evidence references and truth labels with every retrieval. The
legacy database column named `scope_digest` stores this partition for new
records; it contains no caller or session material. Optional caller-held session
headers are validated and discarded during a request. Neither session tokens
nor their digests are retained or returned by enterprise memory.

Observations require operator authority; approved knowledge requires administrator
authority. Writes require a compatible receipt from the same tenant and workspace
and return the append-only memory record hash. Retrieval supports exact service,
journey, outcome, incident, decision, receipt, kind, and timezone-aware time filters.
Each query reads at most 1,001 candidates and returns at most 100 items. Pagination
ends within the first 1,000 candidates; an explicit `bounded_scan.truncated` flag
means older matching records may exist. Narrow the receipt or time filters to
retrieve another window. A GET creates no receipt or memory record.

Enterprise Ask reads at most 50 current records of each required entity kind and
100 fallback receipts. Every input used in an answer must have a compatible scoped
receipt. Confidence never exceeds 0.97. Approval alone cannot establish execution
or improvement: post-action answers require measured execution and outcome receipts,
an explicit non-simulated execution state, and a subsequent verification window.
Stored receipts are evidence inputs, not independent certification or execution
authority.
