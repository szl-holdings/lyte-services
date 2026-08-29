from __future__ import annotations

import argparse
import json

from .compiler import compile_cell
from .jobs import search_jobs


def main() -> int:
    p = argparse.ArgumentParser(prog="a11oy-factory")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="Compile a decision cell (Lyte admitted; N1–N8 ROADMAP)")
    c.add_argument("--cell", required=True)
    c.add_argument("--signal", default="")
    s = sub.add_parser("search", help="Search cited jobs versus SZL organs")
    s.add_argument("--q", default="", help="Catalog query (vllm, langgraph, guidewire, ...)")
    args = p.parse_args()
    if args.cmd == "search":
        print(json.dumps(search_jobs(args.q), indent=2))
        return 0
    rec = compile_cell(args.cell, signal=args.signal)
    print(json.dumps(rec.as_dict(), indent=2))
    return 0 if rec.decision == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
