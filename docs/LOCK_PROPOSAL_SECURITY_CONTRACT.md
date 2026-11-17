# Review-only dependency lock proposals

## Purpose

Replace lockfile-header shell evaluation and automatic branch writes with a bounded proposal artifact. This is a security-sensitive change requiring exact-diff human review. It does not by itself close a scanner alert or authorize merging a dependency update.

The previous workflow extracted `uv pip compile ...` commands from candidate lockfile comments, evaluated them as shell code, retained checkout credentials, and pushed to a branch interpolated from the pull-request context. The new workflow declares only `contents: read`, disables checkout credential persistence, and has no commit or push step. No credential was rotated, revoked, copied or changed by this implementation.

## Trust separation

The proposal job checks out the immutable base and candidate revisions into separate directories. It executes the base revision's `tools/propose_ci_locks.py` with isolated Python (`-I`), treating candidate requirements as data. A separate credential-free job checks the proposed helper's contract tests. The proposal job is restricted to same-repository Dependabot-authored events; these checks are additional scope controls, not substitutes for removing write authority.

The trusted helper ignores candidate command headers. It accepts only the five explicit requirements inputs and a closed include graph. Every dependency must be an exact package/index pin; direct URLs, editable installs, arbitrary options, escaping includes, cycles, conflicting pins and symlink inputs are rejected. It renders sanitized inputs into a separate temporary directory and invokes a fixed closure order with an argument vector, `shell=False`, `--no-config` and `--no-build`. Compiler child environments exclude credential variables; compiler output is not retained in memory or echoed.

Generated lock output is size-bounded and must contain pinned requirements with SHA-256 hashes. The candidate's inputs and existing lockfiles are reread before publishing the proposal artifact to detect concurrent changes. The patch and receipt bind source head, trusted-policy head, input hashes, generated hashes and patch hash. Candidate repository writes and repository pushes remain zero; `approval_granted` stays false.

## Admission and deployment boundary

This deliberately changes behavior: lock regeneration becomes a proposal, not an automated commit. An owner must review and apply an exact-head patch through the existing protected-source process, then run dependency consistency, installation, vulnerability, build and runtime checks at the resulting head. A changed head invalidates the proposal's applicability until requalified.

The trusted base must already include the reviewed helper before Dependabot proposal jobs can use it. The helper has only been tested with an offline synthetic compiler in this execution pass. No real dependency resolution, hosted workflow run, Linux installation or deployment is claimed. The nine existing lock contracts verify current checked-in closures; they do not qualify newly resolved dependency artifacts.

No method here proves a package safe merely because it is pinned, hashed, wheel-only, or successfully resolved. Independent security review and actual scanner/build results remain separate evidence.

## Local verification

```sh
python -I -B -m unittest discover -s tests -p test_lock_proposal.py -v
python -B -m pytest -o addopts= -p no:cacheprovider -q tests/test_ci_requirements_lock.py
python -m ruff check --select E9,F tools/propose_ci_locks.py tests/test_lock_proposal.py
git diff --check
```

The proposal suite uses disposable fixtures, a mocked compiler and denied socket connections. It checks header injection, path traversal, malformed and unhashed output, credential-variable isolation, no overwrite, immutable revision requirements and unchanged candidate bytes. The full application suite and a live hosted proposal remain release gates.
