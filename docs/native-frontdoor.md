# Lyte native front door

## Source and scope

Lyte serves `lyte/ui/index.html`, `lyte/ui/styles.css`, and `lyte/ui/app.js`
through the existing routes in `lyte/app.py`. The files below qualify that
actual front door. `space/index.html` remains the existing redirect.

The presentation uses the accepted SZL palette from
[`szl-brand@27d12e77fc0f9f1a0e5fae8b34adf2380d51d070`](https://github.com/szl-holdings/szl-brand/tree/27d12e77fc0f9f1a0e5fae8b34adf2380d51d070).
It projects those native colors into Lyte's existing CSS roles; it does not
claim to install the complete KANCHAY bundle. The LY product glyph, semantic
signal colors, operating lenses, and evidence hierarchy remain local.

The Orbit artwork is the exact native
[`kit/logos/orbit-v2/suite/szl-icon-32.svg`](https://github.com/szl-holdings/szl-brand/blob/27d12e77fc0f9f1a0e5fae8b34adf2380d51d070/kit/logos/orbit-v2/suite/szl-icon-32.svg),
displayed at 40 pixels within a 44-pixel link. Its source SHA-256 is
`6e2b7d72a3af3d9602b79de41df0194ede6496a9661f057108ec224c0efc92e6`.
The visible footer attributes the artwork under CC BY 4.0; software remains
Apache-2.0.

## Presentation changes

Opaque reading panels, graphite surfaces, silver borders, and restrained teal
accents improve the separation of navigation, operating views, and evidence.
The native source and Ask dialogs retain their existing actions. The Ask input
has native autofocus, the lattice viewport accepts keyboard scrolling, and
the text-alternative disclosure has a 44-pixel target. Forced colors use an
actual focus outline rather than a shadow that the browser removes.

Receipt counts stay unknown when the receipt API is unavailable. A successful
empty response remains a known zero. The proof panel no longer paints fixed
48% and 68% segments: no completeness denominator was supplied. Its neutral
ring and unavailable initial count make no proportional claim. When data is
loaded, the caption names only the count of loaded records with persisted
hashes; the existing hash validation and truth labels are unchanged.

## Local verification

The 2026-10-11 source qualification used the accepted README-only main
`7e4c1742e9b682b5274fe4e8ba81fd8b79748a02` as its base. All 213 previously
tracked files outside the three frontend files matched that base byte for
byte. Backend code, tenant and workspace rules, request expressions, receipts,
formula and signal calculations, publisher authority, source marker, Docker
closure, and the existing tests were not changed.

- **36 tests passed** from `test_frontend_contract.py`,
  `test_frontend_script.py`, and `test_build_receipt.py`, using Python 3.12 and
  the pinned `requirements-test.txt` closure. The local test process used an
  in-memory test database and denied unmocked socket/urllib transport before
  imports; it recorded zero transport attempts. One existing Starlette
  deprecation warning remained.
- `python -m tools.verify_frontend --mode all`, Ruff for the new browser
  helper, and `git diff --check` passed.
- **92 browser checks passed** with Chromium on the actual three static
  files. The helper visits all five operating views at 320, 390, 768, 1280,
  and 1920 pixels. It checks layout, target sizes, heading and dialog focus,
  mobile navigation, native disclosure, keyboard scrolling, reduced motion,
  forced colors, exact native artwork, unknown proof, and empty-versus-missing
  receipts. It recorded zero page errors, external requests, or attempted
  writes. The 320- and 1280-pixel captures were visually inspected.

The browser fixture starts no application backend and has no credentials. It
serves a fixed three-asset map and returns explicitly labeled unavailable or
empty SAMPLE responses from localhost. Every non-GET or external browser
request is denied. The strict existing script/style CSP is retained. This
qualifies presentation behavior; it is not a live runtime or tenant test.

To reproduce the browser qualification in an environment with Playwright and
an available Chromium executable:

```sh
CHROME_PATH=/path/to/chromium python tools/verify_native_frontdoor.py \
  --output /path/to/new-evidence-directory
```

The existing closed payload enumerator includes all three frontend files
within its 82-file application closure. The rendered bytes matched these
SHA-256 values:

| Actual served source | SHA-256 |
| --- | --- |
| `lyte/ui/index.html` | `bc502c820271ee42324d286ea35f36171e70ecf16f99fbe3a281978c962b2068` |
| `lyte/ui/styles.css` | `9ba3fb23e664c19f464c643082c137d6690ced6d55a37a72cc074bb3954d5c3e` |
| `lyte/ui/app.js` | `e46203fc2c1670069c70bb3ca72e3d30566f5f51c21f060dc7a6a999fcd3b7ba` |

## Publication

Protected A11oy remains the sole publisher for `SZLHOLDINGS/lyte`, through its
existing `hf-publish-vertical-flagships.yml` contract. After normal source
review and merge, that writer must consume the exact accepted Lyte revision,
publish its complete artifact closure, and verify the returned source and
build receipt through its existing gates. This presentation change does not
alter publisher admission, enable another writer, open a maintenance window,
or establish that the new frontend is already live.
