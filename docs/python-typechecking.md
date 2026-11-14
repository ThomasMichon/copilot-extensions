# Python type checking

The repository is migrating to strict MyPy through the
[Python Static Type Safety effort](../efforts/active/python-static-type-safety/README.md)
and #9807. Project policy lives in `tools/typecheck-projects.json`.

The admitted cohort qualifies the checker code/tests, the complete Bootstrap
Killswitch, Config Migrate, Configuration Layers, Core Delegation, Docker Resource
Producer, Dropin Registry,
Endpoint Rendezvous, Fleet Contracts, Fleet Index/Vault Adapters, Installation
Context, Installer Readiness, Lazy CLI Dispatch, Machine Transport,
Peer Launch, Plugin Activation, Plugin Resolve, Process Telemetry, Remote Login Shell, Repository Policy, Session Liveness Probe, Single
Instance Lease, Update Telemetry, Work
Coalescing Singleton and Worktree Identity
source/test projects.
Other projects are explicitly pending. Source CI and the
exact-candidate release gate enforce this cohort. A passing admitted cohort is
not repository-wide type safety.

Materialized library copies under the strict services' `libs/` directories are
inventoried separately as pending consumers. They do not inherit service
admission merely because promotion places them below that service's path.
Service-owned source and tests remain strict, and each canonical library keeps
its own declared admission; this does not qualify every shipped copy.
An `--all` probe reports absent pending scopes without trying to check them;
explicit scope checks and admitted empty scopes still refuse qualification.

## Local commands

From the source repository root, with Python and UV available:

```text
python tools/typecheck.py --prepare
python tools/typecheck.py --enforced
python tools/typecheck.py --changed --base origin/dev
python tools/typecheck.py lib-worktree-identity
python tools/typecheck.py lib-bootstrap-killswitch
python tools/typecheck.py lib-config-migrate --prepare
python tools/typecheck.py lib-config-migrate
python tools/typecheck.py lib-configuration-layers
python tools/typecheck.py lib-core-delegation
python tools/typecheck.py lib-docker-resource-producer
python tools/typecheck.py lib-dropin-registry
python tools/typecheck.py lib-endpoint-rendezvous
python tools/typecheck.py lib-fleet-contracts
python tools/typecheck.py lib-fleet-index-adapter lib-fleet-vault-adapter
python tools/typecheck.py lib-installation-context
python tools/typecheck.py lib-installer-readiness
python tools/typecheck.py lib-lazy-cli-dispatch
python tools/typecheck.py lib-machine-transport --prepare
python tools/typecheck.py lib-machine-transport
python tools/typecheck.py lib-peer-launch
python tools/typecheck.py lib-plugin-activation --prepare
python tools/typecheck.py lib-plugin-activation
python tools/typecheck.py lib-plugin-resolve
python tools/typecheck.py lib-process-telemetry --prepare
python tools/typecheck.py lib-process-telemetry
python tools/typecheck.py lib-repository-policy
python tools/typecheck.py lib-remote-login-shell-remaining
python tools/typecheck.py lib-session-liveness-probe-remaining
python tools/typecheck.py lib-single-instance-lease
python tools/typecheck.py lib-update-telemetry
python tools/typecheck.py lib-work-coalescing-singleton
python tools/typecheck.py lib-worktree-identity-remaining
python tools/typecheck.py agent-roles
python tools/typecheck.py --inventory
python tools/typecheck.py --inventory --json
```

`--prepare` explicitly installs the checked-in hash-locked checker dependencies
using the existing seven-day seasoning policy and environment-scoped admission.
Environments are private repository test-cache entries keyed by requirements,
host platform and interpreter version. A check never silently installs packages
or falls back to another interpreter; missing preparation prints the exact
command to run.
Each check uses an invocation-private source-view directory for emitted files,
the target list and MyPy configuration, under the existing environment lease.
Successful cleanup removes only that invocation's verified outputs and empty
directory. Previous cache files or source-view directories are preserved and
are not reused as inputs to a new check.

Preparation captures one immutable flat requirements export, then uses those
same bytes for both the environment key and installation. Nested requirement
includes are refused until a complete graph snapshot is qualified. The dependency
install subprocess has a five-minute bound. Cache-path validation stays independent of Python
3.11-only installer imports, preserving the checker's Python 3.10 bootstrap.

Agent Roles checks all three Python skill-contract test files strictly on both
analysis platforms with the existing Python 3.10 target and base checker lock.
The synthetic JSON example retains explicit dynamic values; its assertions and
negative cases are unchanged. This admits the static Python test scope, not
live review, triage, fleet steering or provider behavior. It does not admit
another project's copies or enlarge the required unit packet.

Plugin Activation checks all twenty-one canonical source/test files strictly
on both targets using the existing YAML checker lock. Dynamic JSON and YAML
documents retain explicit `Any` boundaries after the existing runtime checks;
cache callbacks preserve their result types, and native Windows bindings retain
their pointer-correct signatures and platform gates. Resolution, preservation,
precedence and filesystem behavior are unchanged. The synchronized Customizing
Copilot copy and all nine release consumers remain independently pending
checker projects.

Plugin Resolve checks all twenty-three canonical source, test and installer-fixture
files strictly on both targets in the base environment. JSON manifests,
registration documents and preparation plans retain explicit dynamic values;
paths, subprocess results, cancellation callbacks and native locking operations
have concrete contracts. Runtime validation, source authority, preparation
precedence and locking behavior are unchanged. Its synchronized Customizing
Copilot copy and ten release consumers remain independently pending projects;
canonical admission does not qualify embedded Python transports.

Config Migrate selects `tools/typecheck-yaml-requirements.txt`, a separate flat
hash lock containing the same checker/pytest pins plus seasoned PyYAML and its
third-party stubs. Its complete source and tests pass strict checking for both
declared analysis targets. Serialization documents remain explicitly dynamic
`dict[str, Any]` boundaries; the version marker is narrowed from `object` by the
existing runtime validation. There are no new missing-import ignores or
unchecked first-party stubs. Other projects do not implicitly gain YAML
dependencies from this environment.

Configuration Layers checks its canonical source/test modules strictly
on both declared targets in the base environment. Raw contract inputs remain
`object` until existing validation succeeds. Source, layer, document and
composition results use typed records; recursive JSON merging has a concrete
value union, while arbitrary configuration keys and serialized owner reports
retain explicit `dict[str, Any]` boundaries. Optional reads retain their
`None` result and callers narrow it before use. Runtime validation, precedence,
source ownership and Python/Node parity are unchanged. The synchronized standalone payload copies remain independently pending with
their consumers.

Core Delegation checks all five canonical source/test files on both declared
targets. Its protocol-agnostic JSON request/response dictionaries retain
explicit dynamic values; the existing decoded-object check and nullable
fall-through remain intact. Socket addresses and the pointer-correct Windows
API signatures are typed without adding a runtime wrapper or changing native
calls. Real loopback/rendezvous tests remain separate from static analysis;
the existing Unix-socket skip still applies on Windows.

Docker Resource Producer checks all eleven canonical source/test files on both
targets in the base environment. Validated identity fields retain their existing
constructor checks; JSON responses remain dynamically shaped until validation.
A minimal binary-read protocol describes HTTP framing, and synthetic Docker
state/invocation records remain separate from malformed response vectors.
Standard-library socket capabilities retain their native transport admission.
Explicit contained fixture execution on minimum Python uses a fake Docker
process, not a live daemon. Required PR CI does not execute this independent
suite; canonical admission grants no resource or cleanup authority.

Bootstrap Killswitch checks all three canonical Python source/test files in
the base environment on both declared targets. State documents expose
`dict[str, object]` after the existing runtime validation; CLI dispatch uses a
narrow callable cast for the handler installed by its parser. Test fixtures
narrow the optional PowerShell executable after the existing availability
skip. Default-cell scope, fail-open reads, atomic writes and shell behavior are
unchanged. The three synchronized physical Python copies remain independently
pending until their source correspondence and consumer projects are qualified.

Installer Readiness checks all ten canonical source/test files strictly on both
declared targets in the base environment. Readiness reports and fixture records
have concrete typed shapes; intentionally malformed inputs retain explicit
negative-test casts. Local variable renames avoid conflicting inferred types
without changing receipt validation, blocking decisions or repair behavior.
Agent Machines remains an independently pending consumer; canonical admission
does not qualify its complete project or grant installation authority.

Single Instance Lease checks all fourteen canonical source/test files strictly
on both declared targets in the base environment. File handles, liveness probes
and reaper collaborators have explicit types; existing malformed-input and
descriptor-close fixtures retain narrow deliberate casts. Kernel-lock ownership,
process identity, fork handling and release behavior are unchanged. The shipped
Customizing Copilot copy is synchronized, but copies and consumer projects remain
independently pending until their complete scopes are qualified.

Process Telemetry checks all seventeen canonical source/test files strictly on
both declared targets. Its separate `tools/typecheck-otlp-requirements.txt` flat
hash lock retains the base checker pins and adds the library's declared optional
OTLP SDK/protobuf dependencies under the same seven-day seasoning policy.
The base package remains dependency-free; SDK imports remain optional at runtime.
Callable, custody and exporter protocols expose concrete collaborator contracts,
while validated JSON events retain explicit dynamic values. Native append/sync,
fail-open callbacks, no-SDK refusal and diagnostic-only delivery semantics remain
unchanged. Bridge, Dispatch and Worktree Manager remain independently pending
consumer projects.

Machine Transport checks all nineteen canonical source/test files strictly on
both declared targets using the existing YAML checker lock. Branch-local names
distinguish legacy repository declarations and execution-space selections;
registry tuples and optional observed hostnames retain explicit types. Positive
test helpers require a selected entry before attribute access, while absent and
refusal cases still exercise the original APIs. Parsing, alias precedence,
transport defaults and platform detection are unchanged. Its five release
consumers remain independently pending checker projects.

Dropin Registry uses the base checker environment and checks all six canonical
source/test files strictly on both declared targets. Its native FIFO test skips
only where that operation is unavailable; the actor-free release adapter retains
its existing synthetic FIFO refusal control. Synchronized vendored copies remain
independently pending until copy-checking correspondence is qualified.

Endpoint Rendezvous also uses the base environment for its complete five-file
canonical source/test project, including native ownership/concurrency fixtures.
JSON record values remain an explicit dynamic serialization boundary. This
admission does not claim that existing malformed-record or publication-race
tracking is resolved, nor that independent in-package variants inherit checking.

Fleet Contracts checks all six canonical source/test files in the base
environment on both targets. Serialization dictionaries and dataclass
introspection retain explicit dynamic boundaries; runtime validation and
frozen-record semantics are unchanged. Positive test factories preserve real
keyword contracts, while intentionally invalid constructor vectors use
narrow invocation boundaries confined to negative tests. The SSH consumer
remains independently pending; canonical admission does not qualify its
whole runtime.

Fleet Index and Vault Adapters each check all four canonical source/test
files in the base environment. Backend JSON objects retain an explicit dynamic
serialization boundary; operation-specific parameter casts rely on the
validated request contract and leave runtime validation unchanged. Positive
request fixtures use direct typed constructors, and response-presence assertions
narrow optional results without removing existing checks. These are canonical
prototype-library admissions, not activation of a service or runtime.

Repository Policy checks all six canonical source/test files with the declared
YAML/stub environment. Its guest reader is a separately checked module whose
source is sent to fresh `-I -S` receivers; building the probe reads that file
without import-time I/O. YAML and receiver JSON remain validated dynamic
boundaries. The archive builder retains its existing filesystem-distribution
requirement at the metadata path boundary and preserves required licenses.
The five consuming runtimes remain independently pending.

Update Telemetry checks its eight canonical source modules on both targets
in the base environment. Its contract tests remain owned by the pending
Worktrees project. JSON events, status records and error details retain
explicit dynamic boundaries; human sinks use a minimal write/flush protocol,
and active-context clocks and enabled-run paths retain narrow lifecycle
invariants. Native platform branches are visible to both analysis targets.
The generated lifecycle human helpers stay synchronized through the
payload-invocation generator, but their copies and consuming projects do not
inherit canonical admission.

Work Coalescing Singleton checks all eight canonical source/test files in the
base environment on both targets. Protocol-agnostic payloads and computation
results retain explicit dynamic JSON boundaries; rendezvous fields use a typed
record. TCP handler ownership, socket overrides and the shared in-flight record
are statically visible without changing wire validation, deadlines, coalescing
or lifecycle behavior. Fault-injection tests retain their original assertions
through typed callbacks and scoped monkeypatches. The three editable consumers
remain independently pending.

Lazy CLI Dispatch checks all three canonical files, including its tests and
helper, on both declared targets. Its reflective override helpers preserve the
local callable's parameter and return types through `ParamSpec` and a return
type variable. Their narrow casts document the existing dynamic boundary:
an override must honor the callable contract it replaces. Runtime lookup still
checks callability and keeps the existing selection order; typing does not
add runtime signature validation or qualify pending callers.
Exact callable-type assertions in the library tests run as part of the required
MyPy cohort, detecting parameter or return-type erasure. The checker test suite
also retains real positive/negative clients for both helpers. Its type-only
`typing_extensions` import uses the existing pinned checker dependency, not a
new library runtime dependency.

Peer Launch checks its complete three-file canonical source/test project on
both declared targets. A typed optional `creationflags` dictionary preserves
the native subprocess keyword contract without widening unrelated arguments.
The two dynamically loaded, validated installation/governance records retain
explicit `dict[str, Any]` boundaries. No validation, headless behavior,
environment filtering or dependency-free bootstrap rule changes. Its 12
synchronized packaged copies and pending consumers remain independently
unqualified until their own projects and source correspondence are admitted.

The three initially admitted leaf libraries retain separate source and
`-remaining` project names so existing local selectors remain valid. Both
halves now use strict checking, including every canonical test/helper file.
Invalid wire-value fixtures use `object` boundaries rather than unchecked
`Any`; existing optional probe reasons are narrowed before substring assertions.

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

## Interpreter qualification versions

Each project's `python_version` remains its authoritative minimum analysis
target. A project may additionally declare `"additional_python_versions":
["3.11"]`. Omission or an empty list retains the single-version policy.
Additional values must be unique canonical `3.<minor>` strings strictly above
the minimum; they never replace it or change runtime requirements.

The checker runs the minimum and every additional version against every
declared platform. An additional-version matrix finishes all cells even after
diagnostics and returns the first failing status; single-version projects retain
their existing fail-fast behavior. Every result names its Python version and
platform, and MyPy caches are separated by prepared dependency environment,
platform and analysis version. Ordinary projects share incremental dependency
analysis instead of rebuilding common dependencies in a project-private cache;
each project still runs its own complete checker invocation. MyPy retains its
normal source and option invalidation.
Projects with explicit import roots and the guard alias still disable the cache.
Source views are verified after every invocation. The prepared
dependency environment remains keyed by its actual host interpreter/platform
and captured lock bytes; analysis targets do not select a different interpreter
or install another runtime.

This is one project and one physical-file inventory, not additional ownership
per version. Local, source-PR and frozen-candidate `--enforced` commands all read
the same project declaration. A version guard that excludes code at the minimum
cannot qualify that code by itself: an additional declared version must strictly
check the supported branch. Native minimum-interpreter and consumer/copy
qualification remain separate evidence.

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
The YAML project exercises independent dependency preparation; additional
project dependency contexts remain subsequent slices.

## Project-local real import roots

A project may append an optional ordered `import_roots` list of repository-relative
directories. Omission or `[]` preserves the existing seven-field project defaults.
The optional eighth constructor argument is `additional_python_versions`;
`import_roots` is optional ninth and is normally passed by keyword. Both empty
defaults preserve existing seven-argument callers.
For example, `["tools", "plugins/agent-worktrees/scripts"]` exposes those ordinary
first-party helper directories after the existing checker import roots. These
are real source directories, not module aliases, stubs or arbitrary MyPy options.
Malformed, duplicate, missing, non-directory and redirected roots are refused.
An explicit root's absolute path must not contain the native path-list separator,
including in a repository ancestor. For explicit-root projects, the checker also
refuses that separator in any final search component, including inherited source
directories and the existing Installation Context view prefix. This uses the
SDK host's delimiter, not the analysis target's platform: otherwise `MYPYPATH`
could search different directories from those captured. Empty-root projects
retain their existing search-path serialization and order. A checking root must
contain Python source in the existing ownership inventory.
Ambient `MYPYPATH`, `PYTHONPATH` and `PYTHONHOME` do not supply roots.

For explicit roots, the checker captures the ordinary directory/file identities,
bytes and membership of reachable Python source without executing it. Identifier
directory components include namespace packages without requiring `__init__.py`;
non-module fixture names and data paths do not become import interfaces.
Importable `.py` members must belong to the existing inventory, including owned,
nonignored developer WIP. Extra ignored/unowned Python, first-party `.pyi`
shadows or substitutes, symbolic links, reparse points and nonordinary inputs
are refused. Third-party SDK stubs outside these roots are unchanged.
Windows extension aliases such as `.PYI` count when the native lowercase lookup
resolves to that same file. Ordinary `.PY` aliases retain their inventoried
`.py` ownership spelling; POSIX case-distinct data is not reclassified.
The ephemeral capture is verified before and after each version/platform analysis and
again after checker-file cleanup, before releasing the environment lease.
Changed or replaced roots/members and added/missing sources cannot credit a
result, including when MyPy itself fails or times out.

Import delivery does not add helper files to the project's owned targets or
certify their bodies. MyPy retains ordinary module identities, duplicate-module
diagnostics and `--follow-imports silent`: consumed helper contracts supply
types, while the helper owner qualifies its implementation independently.
The Payload Invocation row uses these roots and remains pending. Both a named
pending check and `--all` remain nonzero even after a clean diagnostic probe.

Projects with explicit roots use the existing `--cache-dir` option with the null
device rather than reusing incremental diagnostics for mutable helper inputs.
Projects without roots retain their existing cache behavior and environment key.
No new qualification receipt or committed-only source restriction is introduced.
Local developer files retain the normal ownership inventory rules; frozen checks
resolve roots relative to their candidate checkout. This does not replace the
existing frozen-candidate owner's Git content-tree identity. Additional imported
source must satisfy the same inventory and ephemeral verification; ignored or
stub inputs are not qualified by the candidate identity.

## Fixed fragment source views

The canonical Installation Context project uses a checker-only composition at
the actual `_load_fragments()` call. Its complete loader structure must match
the qualified adapter. The literal fragment tuple determines order; every
physical fragment must be present exactly once. The separate PowerShell worker
remains an independent input. The adapter includes the Windows native-read
helper while keeping compiled filenames in their original lexical spelling;
the helper itself remains in the checked source.

When that canonical loader is in the physical inventory, every project also
receives the faithful view as an import root. A consumer can therefore import
the public flat `installation_context` module without selecting its loader or
fragments as owned targets. Import delivery does not transfer their ownership,
add them to the consumer's reported targets, or certify their implementation:
the canonical project retains its separate strict check. Imported views use
the same byte/membership verification, source mapping and lease-bound cleanup;
no stub, private-fragment import or missing-import suppression is substituted.

The view preserves physical source lines, normalizing line endings to LF and
replacing the loader call and standalone `from __future__ import annotations`
declarations with blank mapped lines. It refuses other future imports, changed
loader semantics, missing/extra fragments and file-wide checker directives.
Product code is never imported or executed. Each original source first passes
non-executing compilation, so AST-only acceptance cannot hide runtime syntax
errors. Directive scanning uses Python's universal-newline interpretation,
including CR-only sources. Checker diagnostics point back to original loader and
fragment paths and line numbers. Input and emitted-view bytes are revalidated
before a result is credited; temporary views are removed while holding the
checker lease. Views live beside, not inside, the prepared venv because MyPy
refuses its own environment as an import root.

```text
python tools/typecheck.py lib-installation-context
python tools/typecheck.py lib-installation-context --strict
```

The complete canonical project is **strictly admitted** on both declared targets:
51 physical source/test files qualify as 32 checker targets, including the
composed implementation, standalone worker and every test/helper module. Physical
file counts remain distinct from checker-target counts. Reflective module and
native callback boundaries retain precise callable/protocol contracts; JSON
serialization remains explicitly dynamic where required by the existing API.
Fixture vectors and layouts retain their actual nullable and heterogeneous
values. No runtime validation, assertions, skip policy or cleanup is weakened.

Vendored copies remain independent pending inputs; the existing
installation-context vendoring owner must qualify correspondence before any copy
can inherit canonical checking. Other fragment families and independent
package/module environments remain separate campaign work.

### Fixed guard module alias

Only the `copilot-extensions-harness` project substitutes its owned
`plugins/copilot-extensions-harness/scripts/copilot-mention-guard.py` target
with a derived `copilot_mention_guard.py` view. The alias contains exactly the
original bytes: no header, newline normalization, annotations, stub or runtime
loader change. Every diagnostic line and column maps to the original source.
The physical inventory is unchanged, and all other current owned targets remain
selected; this substitution preserves N physical files as N targets.
Another project's inventory-only appearance of the guard never activates it.
Missing, unowned or incomplete bindings are refused.

The alias shares the already-owned source-view directory with Installation
Context, without changing its fragment composition or adding a duplicate import
root. The project's declared ordinary import roots remain separate captured
source providers. Competing canonical module, stub, package and namespace
providers in the search roots are refused using native filename semantics.
The alias uses the null cache rather than crediting mutable-source cache results.

Ordinary source, ancestor-directory and generated-output identities, source and
view bytes, and exact stage membership are checked before every analysis, after
every result or failure, and during final cleanup. Cleanup removes only
still-owned outputs; unknown or replaced files/directories are retained and
reported. Each view retains its own source correspondence. These safeguards do
not execute the guard, admit its pending consumer, qualify emitted programs or
extend the adapter to other hyphenated modules.

### Invocation catalogs, binding bootstrap and shim selectors

The pending `lib-payload-invocation` project selects its four complete physical
source files plus four in-process catalog witnesses, one catalog-binding
bootstrap view and two shim cache-selector views. The catalog declaration binds the real producer, validators
and templates; its closed data witnesses cover single/multi-command and
PowerShell/cmd variants. The bootstrap view preserves the actual PowerShell
single-quote transport, both interpreter launch sites and real helper forwarding.
Diagnostics map back to the canonical producer and its authored expression range.

Normal Dev checks derive the bootstrap from canonical source without reading
or refreshing derived consumer mirrors. Explicit materialized-consumer controls
separately verify the actual emitted block and helper correspondence. Frozen
checks use their candidate's inputs, with the same byte/membership custody and
lease-bound cleanup as other source views. Neither path executes the bootstrap.

The selector views preserve the actual PowerShell here-string and POSIX
single-quoted `-c` argument, including their distinct newline transports.
Their fixed launch arguments and real `cache_arguments` import/call must match.
Embedded PowerShell here-string terminators are refused even inside otherwise
valid Python string literals.
The winning candidate-local `agent_procutil` provider and its ordinary source
tree are captured and rechecked; first-party stubs cannot replace that helper.
Selector diagnostics map to the original shim-template lines. The optional
PowerShell prefix annotation preserves the existing `None` fallback and JSON
output rather than coercing it to an empty string.
The nineteen in-process correspondence controls are positively selected in
the required unit packet. Real interpreter, Git and source/frozen frontdoor
checks remain separate integration controls, not additional required-unit work.

The production project keeps Python 3.10 plus persistent Python 3.11 analysis
on both platforms. Python 3.10 checks the bootstrap's early exit guard only;
Python 3.11 checks its full body. Strict admission refuses a declaration that
omits that full-body target. These eleven analysis targets do not qualify native
execution, interpreter availability, remaining embedded programs or consumer
copies. Production remains pending; the independently strict test-source
project does not activate these views.

## Exact-candidate release enforcement

The pending Harness project additionally declares its fixed emitted Python
inputs in `plugins/copilot-extensions-harness/python-emitted-inputs.json`.
Within each verification pass, emitted views with identical member, asset and
input-byte bindings share one binding verification. Every generated output's
identity and bytes remain independently checked; nothing is cached across passes.
Its normal checker counts physical inputs separately from supplemental programs.
The pure AST adapter recognizes only declared literals, concatenations and
validated fixture conversions; it never imports or runs a producer.
Named inputs require one prior assignment in the same statement suite or a
dominating ancestor suite. Parameters, ambiguous rebindings and assignments
from another branch are refused; fixture origins obey the same construction-time
rule. Nested programs are limited to one declared decoded child, with deeper
Python producers refused rather than silently left inside an unchecked string.
Portable parameter strings are typing witnesses, not captured native paths.
Diagnostics identify the original expression range rather than inventing a
column mapping through concatenated strings. Generated-file epochs, nested
parent constants and the quoted shell body retain separate roles.
Source, structural, member and executable-asset drift refuses qualification.
These checks do not qualify native execution, shell behavior, physical
relocation, shipped copies or the full composed-consumer/promotion gate.

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
