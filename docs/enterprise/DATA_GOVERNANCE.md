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
stores only the digest of a caller-held session scope, and returns evidence
references and truth labels with every retrieval.
