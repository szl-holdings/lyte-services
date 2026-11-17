# Receipt inspection: an evidence-bound path through the existing Lyte surface

Status: review candidate, not deployed. Source basis:
`lyte-services@93343f67071a08a2f5c211b95306d59ce6e4103d`.
No formula identity, Lambda status, authority policy, dependency, migration,
production publisher, or model configuration changes are introduced.

## The implemented slice

The existing receipt drawer already shows persisted identifiers, truth labels and
metadata. This change adds an on-demand check of the record behind the selected
identifier through the existing scoped API and append-only store:

`receipt tape -> existing drawer -> GET /api/lyte/v2/receipts/{hash}/inspection -> scoped persistence -> canonical projection checks -> existing drawer`

The new read verifies both the stored canonical-basis digest and the projected
fields. This matters because Python mapping equality equates booleans and integers:
object equality alone is not the identity of serialized evidence. It additionally
recomputes payload identity and reuses the existing native projection checker.
It reads at most one target and 32 direct referenced receipts. The query does not
walk the full chain, fetch arbitrary URLs, mutate a stream head, or append an
inspection receipt. It requires the same tenant/workspace authority as existing
read routes. Auth-unconfigured is 503; a principal outside the selected scope is
403. Missing records are 404, not an empty successful inspection.

The drawer makes a fresh inspection request even when descriptive receipt metadata
is cached. Generation counters bind the response to the current selection and
workspace. Closing or changing scope prevents late responses from repainting a
new view. A missing or malformed API response displays UNAVAILABLE; the frontend
never invents a green inspection. All displayed diagnostic strings use text nodes,
not inserted HTML. Directly verified local references can be opened in the same
drawer using their exact recorded identifiers.

No new graph database, rendering framework, signing implementation, authentication
provider, or side-effecting agent is added. The existing Signal Lattice frontend,
FastAPI router, persistence schema, public SAMPLE boundary and protected publisher
remain the owners of this work.

## Exact response meaning

The schema is `szl.lyte.receipt-inspection/v1`.

| Field | Meaning |
|---|---|
| record_integrity | VERIFIED_LOCAL or FAILED for this stored record's canonical consistency |
| status | PARTIAL or FAILED; never global VERIFIED or an execution approval |
| expected_references | Count in the checked target's recorded reference list; null if target cannot be trusted |
| inspected_references | Unique direct referenced rows actually read and checked in this scope |
| verified_references | Direct references that match local canonical content |
| coverage_state | COMPLETE_LOCAL, INCOMPLETE, EMPTY_POPULATION, LIMIT_EXCEEDED, or UNAVAILABLE |
| reference_limit | 32; exceeding it returns no partial-prefix success |
| references | Bounded per-reference identity/state/reason, not arbitrary external content |
| can_authorize / can_execute / effectors_enabled | Always false |

For a nonempty, admitted direct-reference population, displayed local coverage is
`verified_references / expected_references`. Empty populations have no percentage.
Duplicate identities fail rather than inflating the number read. The API labels
non-receipt references unresolved and does not echo their raw potentially sensitive
text into the new projection. A missing same-scope receipt is indistinguishable
from an unavailable reference; no cross-tenant existence oracle is introduced.

Even COMPLETE_LOCAL means only the immediate referenced content was found and its
local representation matched. It does not transitively qualify those records'
own dependencies, verify a signature or independent witness, establish factual
truth, prove external freshness, or authorize a consequential action. The response
names those non-claims. Native store checks are reused, not advertised as an
independently implemented verifier.

## Research basis and adaptations

These are architecture inputs from primary documentation, source repositories,
and papers inspected during the November 16, 2026 execution pass. Vendor statements
are not an independent evaluation of the vendors or an exhaustive market census.
No competitor source, visual assets or trade dress is copied into this change.

### Palantir: shared operational concepts

The Ontology documentation couples data, logic, actions and security rather than
making a visualization the integration layer. Its OSDK repository and SDK docs
show a typed application interface to shared objects/actions. The 2023
*Concept-centric Software Development* experience report describes organizing
software around shared concepts and explicit ownership across applications.

SZL adaptation: use existing tenant, workspace, receipt, trace, decision and source
identities across UI/API/store. Extend one existing surface. Do not create a new
repository or introduce a second authority model for an impressive-looking demo.

Primary sources:
- https://www.palantir.com/docs/foundry/architecture-center/ontology-system
- https://www.palantir.com/docs/foundry/ontology-sdk
- https://github.com/palantir/osdk-ts
- https://arxiv.org/abs/2304.14975

### Palantir: validation is not all of execution

Submission criteria can bind user and object conditions at action submission.
The separate Validate Action API explicitly does not check all existing-object
state, such as primary-key uniqueness. A successful preview must not be described
as a complete transactional execution guarantee.

SZL adaptation: the inspector is an observation, never an action gate. Keep future
proposal, independent approval, execution and postcondition records separate;
recheck bound state at action time through the established owner.

Primary sources:
- https://www.palantir.com/docs/foundry/action-types/submission-criteria
- https://www.palantir.com/docs/foundry/api/ontology-resources/actions/validate-action
- https://www.palantir.com/docs/foundry/aip-observability

### LangGraph: resumability and repeated side effects

The current interrupt documentation describes persisted pause/resume state and
warns that resuming reruns the node. Non-idempotent effects can repeat. A durable
checkpointer is required for production use of that pattern.

SZL adaptation: retain the existing analysis bundle's atomicity and idempotency;
make inspection GETs nonmutating. A UI refresh or delayed request must not create
a memory, receipt, permission or second external action.

Primary source:
https://docs.langchain.com/oss/python/langgraph/interrupts

### Zep/Graphiti: time and provenance are distinct dimensions

Graphiti describes temporal facts, validity windows, episodes and provenance. The
2025 Zep paper reports its own benchmark comparisons; those scores are not imported
as SZL model quality or independent evidence of production performance.

SZL adaptation: distinguish observed-at, validity/expiry, source revision,
supersession and local byte integrity. This slice reports an inspection timestamp
but deliberately leaves external freshness unqualified. It does not infer that
recently read content describes a current event.

Primary sources:
- https://github.com/getzep/graphiti
- https://arxiv.org/abs/2501.13956

### NVIDIA: choose the current integration boundary

The current official Agent Toolkit documentation marks it deprecated from October
2026, with end-of-year EOL, and directs users toward Relay (observability/middleware/
governance), Helix (evaluation/optimization/hardening), and Fabric (harness execution).
This is a reason to inspect migration contracts before adding dependencies, not a
reason to rewrite already-working SZL services around a vendor API.

SZL adaptation: keep the present source and serving paths; qualify a later adapter
against exact compatible versions, recorded evaluations, resource limits and
receipts. No NVIDIA package or model is installed by this feature.

Primary sources:
- https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html
- https://docs.nvidia.com/nemo/agent-toolkit/1.9/resources/migration-guide.html
- https://github.com/NVIDIA/NeMo-Fabric

### Video scope

Palantir Developers' *Building with Palantir AIP: the Ontology Software Development
Kit* (2024-02-02) was located, and its official description/chapter list was
inspected. The description covers SDK generation and a React app. A full transcript
was not retrieved and the video was not fully watched. No additional implementation
claim is sourced to unwatched footage.

https://www.youtube.com/watch?v=vSJ5H00V7Es

## The next frontier: proposed, not implemented by this slice

A useful extension is an **evidence-dependency impact preview**: ask which dependent
claims would become unqualified if a receipt is missing, revoked or superseded.
For a frozen dependency graph G, the structural impact of vertex v is the set of
vertices reachable from v along dependency-to-dependent edges. That is graph
reachability, not a causal prediction about business or physical outcomes.

Proposed acceptance: exact node/edge manifests and scope bindings; deterministic
results; cycle handling; bounded expansion; no lost or duplicate population; and
monotonic refusal when required evidence is removed. Any later restoration of
eligibility must be tied to new evidence, not a favorable score or model narrative.

A second proposal is **qualification-aware resource scheduling**: prioritize
already-admitted test jobs that resolve an explicit unknown under a fixed compute
budget. Never schedule from download popularity alone, evict a serving model, or
launch unpinned training to make a dashboard appear active. Measure actual queue
ownership, GPU lease, resource usage, retained checkpoints and held-out evaluation.
No scheduler, paid job or training launch is included here.

A third proposal is **replayable operational decisions**: bind the exact inputs,
policy/source, plan, approval, execution and postconditions so an outside verifier
can reproduce the eligible decision state. A snapshot or self-signed digest alone
cannot establish independent witnessing or what happened between observations.

These are engineering directions, not newly proved Lutar formulas or claims of
being first in the market. Lambda remains CONJECTURE_1_ADVISORY. Existing locked
formula identities and all effectors remain unchanged.

## Qualification and release

The tests exercise the actual store and scoped HTTP route with synthetic SQLite,
not just dictionaries; direct SQL corruption models damaged materialized fields.
Node tests execute the shipped frontend functions and exercise response races,
binding mismatches, unsupported authority, unsafe text and unavailable APIs.
The separate Edge harness connects to an actual temporary loopback Lyte API, not a
mock server, and records SAMPLE-only browser acceptance at multiple viewports.
An in-memory fixture deliberately corrupts one record after its initial UI read to
check that a cached descriptive card cannot conceal a failed fresh inspection.

Exact run results belong in the dated execution ledger; these descriptions are
acceptance intent, not a hard-coded claim that a test ran. Hash-locked dependencies
are installed in a new run-local environment. The machine's old uv failed the
repository's required-version check; the documented standard pip/venv path was
used instead, without changing uv configuration or dependency pins.

Require independent review for this evidence/security-sensitive projection,
current exact-head checks, signed commits, thread resolution and a protected
squash admission. This does not approve a deployment or bypass the canonical
publisher. Production PostgreSQL, load limits, endpoint rate limiting, independent
verification, release container and deployed-byte readback remain separate checks.
Rollback is a protected forward revert of this source change, never deletion or
rewriting of prior receipts. No schema migration or stored-data rewrite is needed.
