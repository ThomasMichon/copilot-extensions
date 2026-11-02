# Python type checking

The repository is migrating to strict MyPy through the
[Python Static Type Safety effort](../efforts/active/python-static-type-safety/README.md)
and #9807. Project policy lives in `tools/typecheck-projects.json`.

The local checker foundation qualifies its own code/tests and the source of
Remote Login Shell, Session Liveness Probe and Worktree Identity. Their remaining
tests/helpers and the other projects are explicitly pending. This foundation
is enforced by source CI for executable-impact changes. Candidate-release
qualification remains a separate slice. A passing admitted cohort is not
repository-wide type safety.

## Local commands

From the source repository root, with Python and UV available:

```text
python tools/typecheck.py --prepare
python tools/typecheck.py --enforced
python tools/typecheck.py --changed --base origin/dev
python tools/typecheck.py lib-worktree-identity
python tools/typecheck.py --inventory
python tools/typecheck.py --inventory --json
```

`--prepare` explicitly installs the checked-in hash-locked checker dependencies
using the existing seven-day seasoning policy and environment-scoped admission.
Environments are private repository test-cache entries keyed by requirements,
host platform and interpreter version. A check never silently installs packages
or falls back to another interpreter; missing preparation prints the exact
command to run.

Preparation captures one immutable flat requirements export, then uses those
same bytes for both the environment key and installation. Nested requirement
includes are refused until a complete graph snapshot is qualified. The dependency
install subprocess has a five-minute bound. Cache-path validation stays independent of Python
3.11-only installer imports, preserving the checker's Python 3.10 bootstrap.

`--enforced` is the default when no project name or selector is supplied. It
validates the complete physical-Python inventory, then checks admitted projects
at their declared levels and interpreter/platform targets. The summary keeps
pending/basic/strict project counts visible.

`--changed` verifies its comparison base (default `origin/dev`) and compares
staged/unstaged files, or a pinned `--head` when supplied. It always validates
the full physical inventory, including non-ignored untracked Python. Until the
dependency/source-view graph is qualified, it deliberately rechecks **every
admitted project**, even when the diff is empty or documentation-only. This
conservative fallback includes affected consumers without claiming precise
dependency selection. An invalid comparison fails visibly.

Use `--inventory --json` for the per-file ownership ledger. Its
`inventory-only` result describes policy ownership, **not** a fresh type-check
result. Exact files/directory prefixes are matched once; unknown or overlapping
ownership and missing/out-of-repository source fail visibly. New non-ignored
untracked Python files are included for local pre-validation.

## Migration diagnostics

```text
python tools/typecheck.py <project-name> --prepare
python tools/typecheck.py <project-name> --strict
python tools/typecheck.py --all
python tools/typecheck.py --enforced --require-complete
```

- `pending` scopes are diagnostic probes, never passing admissions.
- `basic` uses MyPy defaults plus `--check-untyped-defs`.
- `strict` adds `--strict`. `--strict` on a basic/pending project measures its
  delta without changing the manifest.
- `--all` probes pending scopes too and returns failure while selected pending
  work remains, even if a probe emits no diagnostics. Known package-identity,
  composition and dependency-context prerequisites can still block these
  probes; the migration effort owns their qualification.
- `--require-complete` refuses pending/basic projects before claiming a complete
  physical-Python portfolio. Complete generated/composed source and live CI
  qualification remain additional campaign gates.

## Required source CI

The existing `checks` job explicitly runs `--prepare`, then `--enforced`, for
changes requiring executable checks. Both steps have six-minute bounds, cannot
ignore failures and feed the existing required PR aggregate. The trusted-CI
contract guard rejects missing, reordered or weakened steps. Existing unit
selection/reuse and release-controller admission are unchanged.

CI checks every admitted project, not only changed Python files. Checker,
manifest, dependency or workflow changes therefore cannot quietly skip an
activated consumer. Local `--enforced` uses the same pinned checker and target
policy; `--changed` is an intentionally conservative local parity command.
Additional project dependency contexts remain subsequent slices.

## Fixed fragment source views

The canonical Installation Context project uses a checker-only composition at
the actual `_load_fragments()` call. Its complete loader structure must match
the qualified adapter. The literal fragment tuple determines order; every
physical fragment must be present exactly once. The separate PowerShell worker
remains an independent input.

The view preserves physical source lines, normalizing line endings to LF and
replacing only standalone `from __future__ import annotations` declarations with
blank mapped lines. It refuses other future imports, changed loader semantics,
missing/extra fragments and file-wide checker directives. Product code is
never imported or executed. Each original source first passes non-executing
compilation, so AST-only acceptance cannot hide runtime syntax errors. Directive
scanning uses Python's universal-newline interpretation, including CR-only
sources. Checker diagnostics point back to original fragment
paths and line numbers. Input and emitted-view bytes are revalidated before a
result is credited; temporary views are removed while holding the checker lease.
Views live beside, not inside, the prepared venv because MyPy refuses its own
environment as an import root.

```text
python tools/typecheck.py lib-installation-context --strict
```

This is still a **pending diagnostic probe**: neither the library's tests nor its
remaining annotation delta is admitted by composing its namespace. Physical
file counts remain distinct from checker-target counts. Vendored copies also
remain independent pending inputs; the existing installation-context vendoring
owner must qualify correspondence before any copy can inherit canonical checking.
Other fragment families and independent package/module environments remain
separate campaign work.

## Exact-candidate release enforcement

The source controller prepares and checks MyPy from the imported frozen
candidate before emitting its unit evidence. The same candidate-local front
door, pinned lock and project policies apply; checking Dev or recomposing a
second candidate is not a substitute. Both steps are required and bounded inside
the existing read-only source-qualification job, which gates signing and
promotion. Each MyPy step has a six-minute bound; the combined job has a
20-minute deadline covering setup, both checks, the unchanged 300-second unit
packet and evidence upload. Effective shell overrides cannot mask check failures.
Unit selection, evidence and Main's independent lifecycle stay intact.
Release-helper discovery includes the candidate-local checker and its
repository import closure. Their bytes and actor-free decision coverage join
the existing promotion manifest; the unit runner retains its separate
qualification-framework role.

`python tools/check-release-controller-compatibility.py --main-ref origin/main`
validates this prepared sequence and reports `candidate_types_prepared`
separately from `candidate_types_active`. A prepared source proposal does not
activate Main's independently owned controller. Reviewed Main adoption landed in
#9914. [Run 41370906756](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41370906756)
then passed both candidate MyPy steps and completed the normal generated
promotion #9918. A separate unpublished, owner-frozen candidate passed immutable
identity validation but its actual `--enforced` command rejected a deliberate
type regression after fresh checker preparation; identities still validated
afterward. The provider success, non-skippable failure contract and real
candidate-local rejection jointly qualify #9862. No required source gate was
bypassed to publish a deliberately invalid Dev commit or release dispatch.
Remaining project admission and final strict-portfolio qualification stay open.

For local checking of an already imported, owner-validated candidate, run the
ordinary `--prepare` and `--enforced` commands **from that candidate checkout**,
not the source checkout. Candidate creation, import and immutable identity
validation belong to the existing frozen-candidate tools; this checker neither
publishes nor releases one.

## Implementation checks

The checker and its focused controls are strictly checked in the admitted
cohort. Run the latter with the prepared checker interpreter:

```text
<prepared-checker-python> -m pytest tools/test_typecheck.py -q
```

Controls cover ownership, malformed manifests, missing setup, honest pending
results, and actual MyPy failures for incompatible arguments, errors inside
untyped bodies, missing annotations and loose generics. They do not execute the
synthetic input programs. MyPy runs isolated from ambient Python import paths,
using fixed options and declared source roots; Windows argument lists use
response files.
