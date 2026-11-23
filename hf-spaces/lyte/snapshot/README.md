# lyte HF Space snapshot (scoped, pinned)

HF Space: SZLHOLDINGS/lyte at pinned revision 96e6cb7d402207d51ca827b3470b14211aec2eba (baseline r1.1 pin, recorded 2026-11-23). Source-of-record: szl-holdings/lyte-services (Kimi q9 ruling, MAPPED by measured byte dominance).

Measured mapping (2026-11-23 device rail, byte-compare at same paths):
- lyte-services: 85/99 Space files byte-identical.
- lyte-lattice: 0/99.
- The 14-file delta table: delta.md. Exceptions include HF-standard .gitattributes, HF-only deploy files (app.py, index.html, panels.html, config.json, Dockerfile.dockerignore), org-wide szl-card SVGs, and small ahead-of-pin drift on README.md + a11oy_factory/* + lyte/__init__.py + source_revision.txt.
- lyte-services HEAD (2026-11-23T01:41:18Z) is AHEAD of the 2026-11-20 Space pin.

Scope honesty: files/ carries only the acquired top-level deploy files + assets/szl-card/*.svg, byte-exact/sha256-asserted at the pin; the remaining nested source files are manifest-only entries in manifest.json. This is a PARTIAL PINNED SNAPSHOT, NOT full synchronization.

Propose-only, no-merge; merge decisions owner-gated behind the estate signature wall. Registry stays untouched (szl-asset-registry BACKLOG entry remains as-is; realignment is a separate governed change).
