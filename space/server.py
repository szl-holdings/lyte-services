"""Compatibility import path for the canonical Lyte ASGI application.

The production runtime lives in :mod:`lyte.app`. Keeping this module as a
pure re-export preserves existing ``space.server`` imports without creating a
second router, authentication scheme, state store, or execution boundary.
"""

from lyte.app import app, create_app, run

__all__ = ["app", "create_app", "run"]


if __name__ == "__main__":
    run()
