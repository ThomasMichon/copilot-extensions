# Pattern: vendor pointers

**Serves:** `docs/install-contract.md`'s self-contained shipped-payload guarantee.
**Exemplars:** file-pointer doc mirrors (`plugins/*/docs/entity-relationship-model.md`),
`agent-worktrees`'s `lazy-cli-dispatch` lib reference, `agent-pull-requests`'s
installer-engine reference.

## Problem

Several surfaces in this repo want the same thing at once:

- **one canonical source on `dev`** so a contributor edits one place and cannot
  drift a hand-copied duplicate; and
- a **fully self-contained shipped payload** on `main`, because a marketplace
  plugin installs independently and cannot depend on a sibling checkout at
  install or runtime.

The pattern is to keep `dev` DRY, then let promotion rebuild the shipped copy.

## Standard approach

Use one of **two** dev-time forms, chosen by whether the consumer must stay
*runnable on `dev`* or can be a *human-readable stub* there:

| Kind | Use when | `dev` form | Promotion-time action | Shipped `main` form |
|---|---|---|---|---|
| **File pointer** | A mirrored file is read by humans/agents, not executed as code on `dev` | A short physical stub file whose first line is `<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->` | Copy canonical file bytes over the stub in place | A normal full file |
| **Canonical reference** | The consumer must still run, test, and install from `dev` | A live relative-path reference back to canonical, with **no local vendored copy at all** | Copy canonical content into the consumer and rewrite the reference to the local shipped form | A normal full local copy |

Both kinds share one lifecycle:

1. **Author on `dev` against canonical.**
2. **Promotion/preview materializes** a self-contained tree by copying canonical
   content into the consumer and rewriting any live references that would not
   resolve outside the monorepo.
3. **`main` ships only materialized content.** No external path escape, no
   `editable = true`, no dependency on a sibling plugin or checkout.

## Kind 1: file pointers

File pointers are for mirrored payload files whose `dev`-branch copy does not
need to execute. The pointer is the file itself, not a sidecar:

```md
<!-- VENDOR_POINTER: source=docs/patterns/entity-relationship-model.md kind=file -->
```

The stub exists physically on `dev`, which makes the duplication obvious to a
reader opening the mirror. Promotion then overwrites that stub with the
canonical file's bytes.

### When to use it

- mirrored docs under `plugins/*/docs/`
- other payload files where a short stub is acceptable on `dev`

### When not to use it

- Python libs or installer/runtime scripts that must still execute from the
  `dev` checkout

## Kind 2: canonical references

Canonical references keep `dev` runnable while still removing the local copy.
This repo currently uses two concrete forms.

### Shared Python libs: `uv`-editable references

The consumer's `pyproject.toml` points straight at canonical `libs/<lib>` with
`editable = true`:

```toml
[tool.uv.sources]
agent-lazy-cli-dispatch = { path = "../../libs/lazy-cli-dispatch", editable = true }
```

On `dev`, `uv` resolves imports live from canonical. Promotion then:

1. copies canonical `libs/<lib>/` into the consumer's own `libs/<lib>/`, and
2. rewrites the source entry back to the shipped local form:

```toml
agent-lazy-cli-dispatch = { path = "libs/lazy-cli-dispatch" }
```

### Shared installer engine: direct source reference

For installer wrappers, the live reference is the source line itself. On `dev`,
the wrapper sources canonical `libs/installer-engine/installer-engine.{sh,ps1}`
directly and carries **no plugin-local `scripts/installer-engine.*` copy**.

Promotion restores the local shipped form by copying canonical back into
`scripts/installer-engine.{sh,ps1}` and rewriting the wrapper's source line to
the local path.

## Tool ownership: who enforces which invariant

| Concern | Owner | Invariant it owns |
|---|---|---|
| Whole-repo promotion | `tools/materialize_main.py` | Expands file pointers, `uv`-editable lib references, and installer-engine references into a self-contained snapshot; refuses missing/escaping/symlinked canonical inputs |
| Single-plugin local preview | `tools/preview_release.py` | Performs the same copy-and-rewrite into a preview copy, never the real checkout |
| Shared-lib conversion and dev-time checks | `tools/sync-vendored-libs.py` | Converts copies to the `uv`-editable form (`--uv-editable`), restores/promotes canonicals, and validates canonical-vs-copy/reference state in `--check` |
| Shared-lib parsing / rewrite helpers | `tools/uv_editable_ref.py` | Shared definition of "this `[tool.uv.sources]` entry escapes the consumer root and is therefore a canonical reference", plus table-span and tree-comparison helpers reused by sync + promotion |
| Remaining real-copy shared-lib drift | `tools/check-vendored-libs-sync.py` | Keeps the still-vendored multi-copy `src/` + version surfaces in sync with each other; once a consumer has **no local copy**, that consumer naturally falls out of this guard |
| Installer-engine adoption and drift | `tools/sync-installer-engine.py` | Verifies each adopter is in exactly one valid dev-time form: local vendored copy or canonical reference |
| Installer-engine parsing / rewrite helpers | `tools/installer_engine_ref.py` | Shared definition of the canonical-vs-local installer-engine source-line forms reused by sync + promotion |
| Install-contract enforcement | `tools/check-install-contract.py` | Fails if the install contract is broken and delegates installer-engine conformance to `sync-installer-engine.py`'s `verify()` |

## Adopting the pattern

### Add a new file pointer

1. Keep one authoritative source file in its canonical location.
2. Replace each vendored mirror with a stub file whose first line is the
   `VENDOR_POINTER` HTML comment marker pointing at the canonical path.
3. Validate the materialized output (`tools/materialize_main.py` and, for a
   plugin payload, `tools/preview_release.py`) before publishing.

Reference example: the `entity-relationship-model.md` mirrors converted by the
`vendored-doc-pointers` effort (`efforts/2026/09/26 vendored-doc-pointers/README.md`).

### Add a new shared-lib canonical reference

1. Make sure `libs/<lib>/` is the canonical copy and that you are not about to
   discard a consumer-local delta.
2. Convert each consumer with:

   ```bash
   python3 tools/sync-vendored-libs.py --uv-editable <consumer> <lib>
   ```

3. If the lib itself depends on another vendored lib, ensure that nested
   dependency is also expressed canonically and carries `editable = true`.
4. Audit any consumer-local fallback/install/test paths that hardcode
   `plugins/<plugin>/libs/<lib>` and update them if they must still work from a
   `dev` checkout with no local copy.
5. Re-run the repo guards.

Reference example: `agent-worktrees`'s `agent-lazy-cli-dispatch` entry in
`plugins/agent-worktrees/pyproject.toml`.

### Add a new installer-engine canonical reference

1. Delete the adopter's local `scripts/installer-engine.{sh,ps1}` copies.
2. Rewrite `scripts/install.sh` and `scripts/install.ps1` to source canonical
   `libs/installer-engine/installer-engine.{sh,ps1}` directly.
3. Register the plugin in `tools/installer_engine_ref.py`'s `ADOPTERS` list so
   the guard and the promotion materializer both know this plugin is expected to
   use the canonical-reference form.
4. Verify both language variants with `python3 tools/sync-installer-engine.py --check`
   and `python3 tools/check-install-contract.py`.
5. Confirm promotion/preview rewrite the adopter back to the local shipped form.

Reference example: `plugins/agent-pull-requests/scripts/install.{sh,ps1}`.

## What is *not* the current adoption path

`src-passthrough` `VENDOR_POINTER.json` directory pointers are historical, not
the preferred mechanism for new work. They survive only as legacy holdouts
where the current two forms do not yet fit cleanly. New conversions should use
the file-pointer form or the canonical-reference form above, not introduce a
new `src-passthrough` adopter.

## See Also

- Deploy contract: [`../install-contract.md`](../install-contract.md)
- Promotion pipeline: [`dev-main-promotion-pipeline.md`](dev-main-promotion-pipeline.md)
- Doc-pointer origin effort:
  [`../../efforts/2026/09/26 vendored-doc-pointers/README.md`](../../efforts/2026/09/26%20vendored-doc-pointers/README.md)
- Current generalization effort:
  [`../../efforts/active/vendor-pointer-generalization/README.md`](../../efforts/active/vendor-pointer-generalization/README.md)
