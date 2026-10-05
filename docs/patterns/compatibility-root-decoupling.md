# Pattern: compatibility-root-decoupling

**Serves:** CONTRIBUTING.md's Componentization policy (1,000-line hard cap,
shrink-only baseline); the `module-componentization-discipline` effort.
**Exemplars:** `agent-worktrees/__main__.py` (currently the violation this
pattern exists to retire).

## Problem

A module that started as a CLI's single entry point accumulates subcommands
until it must be split. The mechanical, locally-safe way to split it — extract
a subcommand family into its own file, leave a thin registrar behind — works
cleanly the first several times. But once a function in the original module is
called by *both* an already-extracted sibling *and* another function still
resident in the root, a shortcut takes hold: give the sibling a lazy accessor
back into the root (`def _core(): from . import __main__ as core; return
core`) instead of tracing out which direct import each call site actually
needs. Call sites convert from a bare name to `core().<name>(...)` and the
split "works" — tests still pass, because the test suite was written against
the monolithic module and monkeypatches functions **directly on the root
module object** (`monkeypatch.setattr(m, "<name>", fake)`), and `core()`
dutifully resolves through the same root every time.

This is a trap, not a shortcut. Every future split now has to preserve the
accessor-and-reexport dance to keep monkeypatches observed, so the pattern
self-reinforces: the more of the module you've extracted this way, the more
of the *next* extraction's effort goes into maintaining backward-compatible
indirection rather than actually decoupling anything. Measured in
`agent-worktrees` (2026-10-05): 47 per-sibling `_core()` accessors, 317
`core().<name>(...)` call sites, and 112 distinct names monkeypatched on the
root module across 704 patch-site occurrences — and the module remains the
single largest offender in the repo even after ten dedicated extraction
slices, because the easy, fully-decoupled seams are exhausted and what
remains is exactly this entangled core-routed tail.

## Standard approach

**New code never introduces a reverse-import accessor back into a module it
was just extracted from, and a test monkeypatches the module where a name is
actually defined — never a re-export or compatibility shim.**

- **A genuinely shared, stateless helper gets a real shared-module home.**
  If two or more sibling modules need the same utility (a JSON-output
  formatter, an ID resolver, a path normalizer), it belongs in a dedicated
  shared module both import directly — not in whichever file happened to
  define it first, re-exported for backward compatibility.
- **Tests patch at the definition site.** `monkeypatch.setattr(<module where
  the function actually lives>, "<name>", fake)`, never the historical root
  it used to live in. This is what makes the next bullet safe: once nothing
  patches the root for a name, nothing needs to *call through* the root for
  it either.
- **Retiring a `_core()` accessor is itself the unit of progress.** A sibling
  module's accessor is done when it has zero remaining call sites — track
  this per-accessor the same way the module-size guard tracks per-file line
  counts.
- **Migrate incrementally, by name, not as a mass rewrite.** Pick the
  highest-traffic names first (biggest reduction in `core()` call sites and
  monkeypatch-on-root occurrences per slice); land each name's move, its test
  repoints, and its call-site conversions as one atomic, reviewable change.
  **Any change that makes an oversized file even slightly smaller is a win**
  — this pattern is intentionally worked a handful of names at a time
  alongside the broader componentization backlog, never as a single large
  refactor.

## Gotchas this pattern encodes

- **A bare-name call inside the band you're moving silently stops observing
  a monkeypatch applied to the root.** `core()` resolves through the root at
  call time; a plain module-global reference resolves through whichever
  module's own globals the code now lives in. Moving code without converting
  every cross-call is the single most common way this regresses, and a
  partial test run will not catch it if the patched test happens to live in
  an unrelated sub-suite.
- **Decorators above a moved `def` are easy to drop by hand/copy-paste.**
  `@staticmethod`, `@classmethod`, `@property`, `@cached_property` fail loud
  but late — only when something calls through `self.`
- **A module-level constant a test overrides is part of the compatibility
  surface too**, not just functions — `monkeypatch.setattr(m, "_RETRY_COUNT",
  1)` needs the same definition-site repoint as a function would.
- **A source-string regression test needs its path updated, not just a
  re-export** — a test asserting a literal string lives in a specific file's
  text does not follow the code when it moves.

## See Also

- How-to for the mechanics of a split (including how to work safely with an
  *existing*, not-yet-decoupled compatibility root while it's being retired):
  the `customizing-copilot:componentizing-modules` skill, § "Splitting a
  compatibility root".
- Tracking: `efforts/active/module-componentization-discipline/` (the broader
  line-count campaign this pattern's violations are currently blocking) and
  the dedicated decoupling effort this pattern was authored alongside.
