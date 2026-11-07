# Developer guide

Install Python 3.12 dependencies from `requirements-test.txt`, run
`alembic upgrade head`, then start `uvicorn lyte.app:app`. Use a temporary
SQLite URL and explicit dev auth in tests. The OpenAPI document is at
`/openapi.json` and the deterministic sample is visible only when demo mode is
explicitly enabled.

Before a PR, run:

```bash
python -m compileall -q lyte lyte_engine lyte_api
ruff check a11oy_factory lyte lyte_engine lyte_api space migrations tests tools lyte_runtime.py
python -m pytest
python -m tools.szl_product_frontier test --product lyte --json
```

New connectors need a code-defined destination, bounded transport, a strict
fixture, negative tests, source identity, truth label, receipt, and a documented
credential boundary. New formula output cannot authorize an action.
