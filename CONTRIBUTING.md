# Contributing

**Control before action. Evidence after.** Three doctrine rules govern every
contribution to this repository:

1. **Control before action.** State the invariant first — a failing test, a
   policy check, or a written assertion — then change behavior. No silent
   refactors of governed surfaces.
2. **Evidence after.** Every change updates its receipts: CI output, test
   runs, screenshots, audit artifacts. If something was not measured, say
   UNKNOWN — UNKNOWN is never claimed as PASS.
3. **Canonical surfaces only.** Product lives at a-11-oy.com, proof lives at
   a11oy.net. The legacy lookalike domain is forbidden and must never appear
   in code, docs, config, or fixtures; the `forbidden-domain` job in
   `lyte-enterprise-gates` fails on any reference outside a guard context
   (run it locally with `python -m tools.forbidden_domain`).

## Branch model

- Branch from the default branch as `szl/<change>` —
  e.g. `szl/fix-receipt-ordering`, `szl/alignment-v14`.
- One concern per branch; keep diffs reviewable.
- Never push to the default branch — every change lands via pull request.

## Commits

- [Conventional Commits](https://www.conventionalcommits.org):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:` …
- Sign every commit cryptographically (GPG or SSH): the `main` ruleset
  (`lyte-main-protected-release`) rejects unsigned commits and requires linear
  history and squash merges. A DCO `Signed-off-by:` trailer (`git commit -s`)
  is welcome but not enforced.

## Pull requests

- State the control and the evidence in the PR description: the invariant
  (test, gate, or assertion) and the receipts that prove it (CI jobs, command
  output).
- CI must be green: every job in `lyte-enterprise-gates`
  (`.github/workflows/compiler.yml`) and CodeQL. The ruleset requires 17 of
  those jobs as status checks; `unit-py314`, `granite-adapter` and
  `forbidden-domain` run on every pull request as well.
- If the change alters a governed surface, attach the receipt that proves it.
- If you change `requirements.txt` or `requirements-test.txt`, regenerate the
  hash-locked closures with the commands in `requirements/ci/*.in`.
