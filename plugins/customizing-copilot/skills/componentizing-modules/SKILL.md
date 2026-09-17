---
name: componentizing-modules
description: >
  Runbook for splitting an oversized or growing source module (or test file)
  into smaller, single-responsibility pieces -- proactively, not only when
  tools/check-module-size.py fails. Covers finding natural seams, the CLI/route
  registration-table pattern, extracting a module safely with tests intact,
  refreshing the shrink-only module-size baseline, splitting large test
  modules by behavioral contract with @pytest.mark.contract attribution, and
  using tools/rank-module-size.py to prioritize which already-grandfathered
  files to tackle first.
  Trigger phrases include:
  - 'split this module'
  - 'break this file up'
  - 'this file is too big'
  - 'componentize this'
  - 'refresh the module size baseline'
  - 'what should we split next'
  - 'module size pecking order'
---

# Componentizing Modules

A runbook for breaking a large or growing source/test file into smaller,
single-responsibility pieces. See `CONTRIBUTING.md`'s Componentization bullet
for the policy (1,000-line hard cap, shrink-only baseline, "a couple of
related classes/functions per module" as the working ceiling); this skill is
the *how*.

## When to use this

- A module you're already editing is approaching, at, or past its cap/ceiling
  (`python tools/check-module-size.py`).
- You're picking the next item off a componentization backlog and want the
  current worst offenders (`python tools/rank-module-size.py`).
- A test module has grown into a runner covering several unrelated behavioral
  contracts.
- **Proactively**: you're adding a new responsibility to a module that already
  has one or two unrelated ones, even if it's nowhere near the cap yet. Don't
  wait for the guard to fail — by the time it fails, the responsibilities are
  usually already entangled.

## Step 1 — find the seams, not just a line to cut at

A module rarely needs a syntactic mid-point split; it needs its distinct
responsibilities pulled apart. Look for:

- **A CLI/route/handler registration table.** If the module is a `__main__.py`
  (or any dispatcher wiring subcommands, routes, or event handlers to
  implementations), each subcommand/route family is almost always independent
  of the others and only shares a thin registration surface. Extract each
  family into its own module (`<verb>_cli.py`, `<area>_routes.py`, …) and leave
  behind a thin registrar that imports and wires them — this is exactly what
  `agent-dispatch` did extracting `producers_cli.py`, `recipes_cli.py`, and
  `supervise_cli.py` out of its `__main__.py`; read that extraction as a
  worked example before inventing a new shape.
- **A policy table vs. its evaluator.** Static data (allowed transitions,
  effect matrices, default configs) belongs in its own module separate from
  the code that interprets it.
- **An adapter per external system/shell.** Code branching heavily on
  "which backend/shell/platform" is a sign each branch wants its own adapter
  module behind a shared interface.
- **A single large class with many methods, all sharing instance state
  (a connection, a lock, a cache).** Free-function extraction doesn't fit
  here — the methods need `self`. Split via **mixin classes** instead: group
  methods by responsibility into `_XMixin` classes in separate files, then
  compose them back with `class Foo(_AMixin, _BMixin, ...): ...` in the
  original module. This is safe without call-graph tracing because
  `self.<method>()` resolves through the instance's MRO at runtime
  regardless of which mixin file defines it — unlike splitting a CLI's
  free functions, you don't need to prove which module each caller should
  import from. Keep `__init__` (and any other state-establishing method) in
  whichever mixin comes first in the base-class list. `agent-bridge`'s
  `db.py` (one `Database` class, ~75 methods) split into `db_core.py`,
  `db_schema.py`, `db_sessions.py`, `db_live_sessions.py`, `db_events.py`,
  `db_prompts.py`, and `db_maintenance.py` this way — read that split as the
  worked example.
- **A vendored/synced copy.** Before splitting a file, check whether it's one
  of several *identical* copies kept in sync by a tool like
  `tools/sync-installation-context.py`, `tools/sync-versioned-runtime.py`, or
  `tools/check-vendored-libs-sync.py`. If so, split the **canonical** source
  (see that tool's `CANONICAL_DIR`/`_lib_copies()`), not a downstream copy —
  the sync step propagates the split to every adopter automatically.
- **A framework app-factory with routes/handlers closing over local state**
  (e.g. a FastAPI `create_app()` building routes inline that close over
  `queue`, `bus`, or similar locals). This is the hardest shape — neither
  free-function extraction nor mixins apply cleanly, since each route needs
  that closed-over state. Don't rush it: design an explicit way to carry the
  state across the split first (e.g. an `APIRouter` factory function taking
  the state as constructor arguments), and treat it as its own dedicated
  slice rather than reusing another shape's mechanics by rote.

This same seam-finding logic applies to `.sh`, `.ps1`, and `.ts` sources, even
though the automated cap currently only scans `*.py` (see CONTRIBUTING.md) —
don't let that tooling gap be an excuse to let a large shell/PowerShell/
TypeScript file keep growing unsplit.

## Step 2 — extract

1. Create the new module(s) with only the moved code plus the imports it
   actually needs; do not drag along unrelated helpers "just in case".
2. Update the original module to import from the new location. Keep a
   re-export only if external callers depend on the old import path and
   updating every caller isn't part of this change; otherwise update callers
   directly and skip the indirection.
3. Keep the diff mechanical wherever possible (move + import fixups) —
   behavior changes belong in a separate commit/PR from a pure split, so a
   reviewer (or a future bisect) can tell "moved" from "changed" at a glance.
4. **Check every method/function you moved for decorators that don't show up
   in a plain `^def ` grep** — `@staticmethod`, `@classmethod`, `@property`,
   `@cached_property`, and similar sit on the line(s) *above* the
   `def`/`async def` and are trivially dropped when moving code by hand or
   copy-paste. A dropped `@staticmethod` in particular fails loudly (a
   `TypeError: takes N positional arguments but N+1 were given`) but only
   when something actually calls it through `self.` — which a partial test
   run can miss (see Step 3). Grep the *original* file for
   `@staticmethod|@classmethod|@property|@cached_property` before you start
   and confirm every one of those decorated definitions still carries its
   decorator in its new home.

## Step 3 — re-validate

```bash
python tools/run-plugin-tests.py <plugin>        # the plugin(s) you touched
ruff check --select F,E9 <every file you touched or created>
python tools/check-module-size.py                # must still pass
```

**`run-plugin-tests.py` groups a plugin's tests into sub-suites and stops at
the first sub-suite that fails** — including a pre-existing, unrelated
failure already in sub-suite 1 that has nothing to do with your change. A
green run only proves sub-suite 1 passed; it does **not** prove your change
is safe if an earlier, unrelated failure means later sub-suites (2, 3, ...)
never ran at all. This masked a real regression during this effort's own
`db.py` mixin split (a dropped `@staticmethod`, see Step 2) until CI's
differently-shaped run surfaced it. Do not trust a local run that stops at
sub-suite 1 as full coverage of your change:
- If the plugin has **any** pre-existing failure, target the actual
  files/behavior you changed directly, e.g.
  `python tools/run-plugin-tests.py <plugin> -k "<area you touched>"`,
  and confirm that filtered run is fully green (it bypasses sub-suite
  grouping and reaches every matching test regardless of unrelated
  failures elsewhere).
- Compare the *total counts* (`N passed, M failed, K skipped`) your run
  reports against what you expect for the whole suite, not just "did it
  print PASS" — a truncated early-exit run reports a smaller, misleadingly
  clean-looking number.
- CI is the authoritative, full-matrix confirmation — treat a passing CI
  run (with only already-known, unrelated pre-existing failures) as the
  real gate before merging, not a substitute for your own targeted check.

If a **baselined** file shrunk below its prior grandfathered ceiling:

```bash
python tools/check-module-size.py --refresh-baseline
```

This only ever *lowers or removes* entries — never raises one — so it's safe
to run any time after a shrink. Commit the updated
`tools/module-size-baseline.json` alongside the split.

If you extracted from a vendored-sync canonical source, also run that source's
sync check (e.g. `python tools/sync-installation-context.py --check`) and
`python tools/check-vendored-libs-sync.py` before pushing.

## Splitting a large test module

Follow `TESTING.md`'s directive: split by **behavioral contract**, not
arbitrary line count. Prefer one parameterized/scenario-style test per
contract over many near-duplicate process-launching micro-tests. As you split:

- Tag each resulting test (or class) with
  `@pytest.mark.contract("<component>.<behavior>")` naming the contract it
  covers. This is attribution, not enforced policy — its job is to make a
  test's contract a filterable, greppable fact (`pytest -m 'contract("...")'`)
  instead of something only inferable from the file it happens to live in, so
  a *future* re-split can prove no contract's coverage silently moved or
  disappeared.
- Keep the existing `portfolio_tier`/`effect` markers as-is; `contract` is
  additive attribution alongside them, not a replacement.

## Prioritizing what to split next

```bash
python tools/rank-module-size.py                 # biggest offenders overall
python tools/rank-module-size.py --near-cap 25   # closest to failing next
```

The ranking folds identical vendored copies into their canonical source (see
Step 1) so it reflects distinct real work, not duplicated line counts. Treat
the top of that list — and anything with a small `--near-cap` margin — as the
standing backlog; see the `module-componentization-discipline` effort for the
current prioritized phasing.
