# Initial measurement

Canonical effort and completion gate: [README](README.md).
This is a diagnostic baseline, not release qualification or a claim that every
reported error is a runtime defect.

## Source and method

- Source: Dev commit `1ad6e7efa633618b8bb83461314080671f971b7c`.
- Census: every Git-tracked `.py` file, parsed with Python's AST without
  importing product code or collecting tests.
- Checker: MyPy 1.18.2 under Python 3.12.10, with a separate prepared checker
  environment. YAML stubs were added after an observed missing-stub diagnostic.
- Progressive checks: defaults; defaults plus `--check-untyped-defs`; `--strict`.
  Targets used explicit canonical import roots, Python 3.12 and Windows
  analysis. A small independent Linux-targeted strict pilot distinguished
  platform-only diagnostics.
- Diagnostic isolation used `--follow-imports=silent` so each target's imported
  dependencies did not repeatedly report their own diagnostics. Missing imports
  were **not** globally ignored. Production dependency environments were not
  fully prepared, so setup-related errors remain visible limitations.
- Source/helper groups were checked independently; response files handled
  Windows' command-line limit. No source or runtime behavior was changed.

The full census contains **7,180 paths / 6,142 distinct file contents**. The
progressive sample selected 55 source/library/service/root-tooling scopes,
requesting 2,820 paths per tier. Root tooling's 198-file invocation stopped on
a duplicate module identity (`plugin_test_containment` versus
`tools.plugin_test_containment`), so that scope is explicitly blocked, not a
completed all-files check. Tests, most script copies and other Python locations
received a census, not this progressive checker pass.

## Syntactic annotation coverage

A complete signature has annotations for every parameter except conventional
`self`/`cls`, and a return annotation. The census includes nested functions and
test doubles. It does not measure annotation quality, inferability or semantic
type safety; some constructors may satisfy MyPy without the explicit return
annotation required by this metric.

| Category | Python files | Functions | Complete signatures |
|----------|-------------:|----------:|--------------------:|
| Production | 2,652 | 27,423 | 83.2% |
| Repository tooling | 283 | 2,489 | 81.4% |
| Scripts/bootstrap | 868 | 12,825 | 76.7% |
| Tests/helpers/doubles | 3,332 | 73,409 | 17.5% |
| Separately classified vendored library copies | 45 | 236 | 87.3% |

These categories are path-based and include duplicated source. Runtime plugin
signature coverage ranged roughly from 75% to 100%; Worktree Manager was 61.1%
and Fleet Manager 58.4%. High signature coverage is encouraging but not strict
compliance.

## Progressive results

These totals include setup and platform errors, cascades and intentionally
composed fragments. Deduplication identifies identical source bytes, line,
diagnostic text and error code across physical copies; it does not turn a
diagnostic into a proved bug.

| Tier | Raw diagnostics | Content-deduplicated diagnostics | Passing whole scopes |
|------|----------------:|--------------------------------:|---------------------:|
| Default | 45,405 | 10,575 | 9 |
| Check untyped bodies | 46,794 | 11,961 | 9 |
| Strict | 64,827 | 29,359 | 4 |

The root-tooling scope aborted, and the minimal environment reported 487
import/stub diagnostics per tier. Therefore these are comparable migration
probes, not an authoritative fully prepared repository baseline.

The four strict-clean scopes were `libs/remote-login-shell/src`,
`libs/session-liveness-probe/src`, `libs/worktree-identity/src` and
`services/dispatch/src`. Whole-scope cleanliness says nothing about imported
dependencies' separately suppressed diagnostics or unselected tests/scripts.

The deduplicated strict run included 5,229 untyped-call diagnostics, 4,551
missing/incomplete-definition diagnostics, 4,597 loose-generic diagnostics and
1,317 `Any` returns. These are cascades as well as independent sites; annotating
one shared helper can resolve many callers.

## Composition and near-clean examples

Installation-context executes 19 fixed fragments into shared module globals.
Independent checking of its source group emitted **3,123 undefined-name
diagnostics / 3,214 strict diagnostics**. A checker-only concatenation of the
same ordered fragments reduced that to **1 undefined-name / 75 strict
diagnostics**, without executing or modifying the runtime. Remaining errors
still need honest correction. Identical fragment copies account for much of
the raw total across runtime plugins.

- `config-migrate`: after YAML stubs, default/body checks were clean; strict
  reported two sites, a bare `dict` migrator contract and an `Any` return after
  runtime integer validation.
- `lazy-cli-dispatch`: default/body checks were clean; strict reported four
  missing argument/return annotation diagnostics on two dynamic callable
  helpers.
- Budget Guidance's Linux-targeted strict pilot reported three sites: local
  `int`/optional-`int` reuse, an overly narrow stream parameter and an `Any`
  return. Windows targeting added irrelevant POSIX-only helper diagnostics.

These examples suggest useful early annotation/narrowing and checker-adapter
PRs without a wholesale architectural rewrite. They do not establish a bound
on total migration effort. Typed dynamic delegation, native interfaces,
JSON/config boundaries, larger management layers and test fixtures remain
substantial work requiring per-component investigation and validation.
