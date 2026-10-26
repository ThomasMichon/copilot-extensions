# Testing copilot-extensions

How to run the plugin test suites, explicit development checks, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

The opt-in `installation-context-acceptance.yml` dependency-compile selection
also runs independent registered-marketplace provider and inventory partitions
on Linux and Windows, each retaining a six-minute job ceiling.
`tools/test_runtime_marketplace_source.py` uses real registered catalogs,
installed ledgers, independent Git clone/fetch and canonical preparation.
A test-owned Git transport maps synthetic declared remotes to independent local
repositories; no production origin or feed is contacted. Controls reject a
cache-created commit absent from upstream, preserve an older installed commit
when Main advances, refuse source/version/registration/ledger changes and
ambiguous caches, and separate two marketplace owners.
`libs/plugin-resolve/tests/test_native_git_inventory.py` preserves existing
ordinary inventory behavior and qualifies the reusable registered-cache proof.
These fresh isolated fixtures qualify the explicit provider, not normal
bootstrap, the production dependency closure, installation speed or rollout.

The opt-in `installation-context-acceptance.yml` admitted-source jobs also run
`tools/test_manager_source_install.py` and `tools/test_manager_source_native.py`.
Profiles, refusal controls and Manager reuse run in disjoint partitions on
each host, retaining the existing six-minute job ceiling rather than combining
every native source scenario into one growing serial job.
The latter exercises the real standalone Manager adapter and immutable
constructor with a complete synthetic Manager/process-owner wheel closure:
offline construction, retained reuse, changed binding, missing first-party
input and failed-candidate selector preservation. A configured synthetic Git
source additionally traverses real native feed admission, source builds and
Manager `self_install`; a fresh Python process reuses its completed generation
with the feed unavailable and construction forbidden. The completion marker
and receipt remain unchanged, and the feed observes no additional requests.
Both invocations test an absolute selected UV with no UV on PATH, and a UV
command selected from PATH. Changing the authenticated feed account or native resolver mode subsequently
requires ordinary source admission instead of reuse; corrupt completion
evidence still refuses before cold admission. The same hosted Manager jobs
also alter a receipt URL, an installed archive digest and installed origin
JSON; each requires an integrity refusal, not cold repair or another feed
request. A real installed-module health failure still requests source repair. They
run package-version parsing and actual update-check regressions for source,
source-profile and repaired generations.
This does not qualify the production Manager dependency closure, default
marketplace bootstrap or performance. Fast adapter and self-install
contracts remain in the ordinary `worktree-manager` suite.

Native feed-capture controls exercise account changes and password rotation
through named environment, embedded URL and netrc credentials. They compare
the real keyed opaque identities, retain stable unchanged captures, and make
no feed request or machine credential-key write.

The same file's `test_native_capture_first_touch_is_stdlib_and_payload_local`
copies only canonical installer resources into an isolated payload and runs
the actual native capture in a fresh `-I -S` process. Repository and site
packages are absent; poisoned ambient imports cannot supply Packaging or
profile/build tools. Real retained UV projections preserve named authentication
bytes and capture identity, and retain offline, unsafe-index and insecure-TLS
refusals without an HTTP request. This qualifies the shared capture mechanism,
not admission-tool installation, normal-consumer enablement or a live feed.

`tools/test_runtime_profile_bootstrap.py` also runs runtime source-policy and
actual wheel inspection in a fresh isolated Python process with the Manager
installer, promotion and publisher modules explicitly refused. Pure owner
controls preserve the single semantic inspector, byte/metadata refusals and
promotion's additional first-party source check. These are narrow import and
validation contracts, not complete toolkit packaging or admitted-tooling
consumer qualification.
Manager inventory/update fixtures select the shared configuration root through
`WORKTREE_MANAGER_ROOT`, not an attribute patch on the installer re-export.
The unsupported-source case reads that actual policy before forbidding engine
submission, retaining the refusal contract across configuration-owner changes.

`tools/test_runtime_source_reuse.py` covers bounded persisted evidence admission,
changed current selections, missing completed receipts, source/receipt corruption, current digest denials
and consume-time invalidation. Nonordinary selected slots, including dangling
links, refuse rather than becoming cold-source availability. The same constructor must refuse to construct
from a reuse-only receipt. Pure caller controls distinguish typed installed
health repair (which releases custody and reacquires real source inputs) from
integrity failures (which never trigger fallback). Prepared-source publication
also tests bounded Windows transient-lock retries without retrying permanent
errors or extending the original deadline.
Expired source-lock consumption retains an active typed subprocess timeout
and its diagnostics; successful consumption still checks the outer deadline.

## Materialized source-owner SDK readiness

`plugins/agent-worktrees/tests/test_source_portfolio_owners.py` materializes the
current required-context Python runtime roster and invokes each installed owner's
actual source validator against a separate prepared artifact. It checks canonical
lifecycle SDK bytes and unchanged fixture selector, config and SQLite file
identity. The fixture snapshot uses the canonical materializer's ignore policy
and omits bytecode-only directories, retaining working-tree additions without
requiring a platform-native Git worktree. Run this integration lane through the
bounded test runner with short Windows and Linux-native temporary paths.

This is SDK-readiness evidence with synthetic source receipts, not Git admission,
native construction, full collected-portfolio execution, production data-format
compatibility or serving qualification. The legacy version-1 command manifest is
not converted. Generator controls retain its required-context/vendored-primitive
guard and unchanged command/catalog rendering. No required unit-only CI packet
is expanded.

## Optional non-serving collected-source updater witnesses

Set `COPILOT_TEST_NATIVE_SOURCE_SIMPLE=1` and select
`plugins/agent-worktrees/tests/test_source_simple_native.py` through the bounded
test runner. SSH, Machines and MCP exercise real materialized native provisioning,
the collected source updater and canonical installed-owner lifecycle admission.
The witnesses check installed module/interpreter/distribution identity, original command
binding, default fixture config/SQLite identity, exact selected-source
currentness, completion-preserving reuse and global help after prepared inputs
become unavailable. Machines and MCP retain their CMD-only Windows global launchers.
MCP also checks its source-qualified operational module version separately from
the installed distribution's package version. Its fixture explicitly sets
`AGENT_MCP_NO_CUTOVER=1` and `AGENT_MCP_NO_VERSION_REAP=1`, checking that the
collected step retains both opt-outs. This qualifies construction/publication,
not live daemon replacement, stale-process cleanup or serving readiness.
The original owner is built with a higher package label than the selected source
to distinguish publisher delivery, actual installed version and exact source
selection without imposing version ordering. The helper code remains current;
this is not a claim that arbitrary older helper ABIs are supported.

Use short Windows and private Linux-native temporary paths. The Windows witness
also compares persistent User PATH fingerprints; contained installers may update
only their child process environment. Source receipts and account homes remain
synthetic fixtures, not Git admission, production data-format/serving proof,
service-role adoption or a full eligible-portfolio rollout. No required unit-only
CI packet is expanded, and none of these witnesses starts a resident service.
Companion controls in `tools/test_native_source_manifest_publication.py` exercise
the actual native publication blocks with a refusing SDK child, retaining prior
manifest bytes and the original-owner argv without a success-shaped result.

## Optional native source-profile updater witness

Set `COPILOT_TEST_NATIVE_SOURCE_PROFILE=1` and select
`plugins/agent-worktrees/tests/test_source_profile_native.py` through the bounded
test runner. The two native-shell cases exercise base and provider-exec source
construction through the actual collected updater and canonical lifecycle
helper, selected-profile currentness, installed code/global CLI help, retained
config and SQLite file identity, and unchanged completion reuse in disposable
homes. Native source calls do not pass a profile override. The fixture reuses
the existing copied-account adapter on POSIX; production OS-profile resolution
is unchanged. Its synthetic source receipt is not merged-source provenance.

The 270-second item and finite child/install bounds remain inside the existing
bounded runner. Short fixture names avoid Windows native-extension DLL path
limits; no production root or system setting changes. This is opt-in, not a
new required CI matrix, fleet/role enablement, full portfolio rollout, production
data-format compatibility or performance qualification. Companion unit controls
are Containers' `test_runtime_source_target.py`, Worktrees'
`test_update_runtime_target.py` and Manager's `test_runtime_source_update.py`.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`docs/pipelines.md`](docs/pipelines.md).

## Optional promotion-control retirement witness

The manual `installation-context-full.yml` portfolio `promotion-controls` runs
six actual-Git cases and their workflow guard on an ephemeral Ubuntu runner, with a 100-second packet
deadline and a five-minute setup/job envelope. It records the exact checked-out
commit and parents, uses read-only permissions and no persisted credentials,
and does not expand required PR CI or the promotion unit portfolio.
The cases prove repeated deletion/rename preservation through real composition,
ordinary declassification, pinned-Main selection and artifact recomposition.
Recorded ownership cannot be removed to reactivate a deleted Dev workflow.
It is not a live controller, artifact-reuse or late-merge race qualification.

```bash
gh workflow run installation-context-full.yml --ref <published-branch> \
  -f portfolio=promotion-controls
```

## Optional repository PR sweep qualification

The manual `installation-context-full.yml` portfolio `pr-sweep` runs the
focused provider, selection, original-root and native-process packet on Ubuntu
with Python3.12. It adds no schedule or required PR matrix. Its ten-minute job
and existing60-second item deadline contain only synthetic state and processes;
no live forge, remote host, credentials, installation or rollout are needed.
The recorded checkout SHA identifies the candidate actually exercised.

```bash
gh workflow run installation-context-full.yml --ref <published-candidate-branch> -f portfolio=pr-sweep
```

The same packet runs locally through `tools/run-plugin-tests.py agent-worktrees`
with the six explicit test files named in that job. Native Windows and POSIX
shell cases skip on the other OS; inspect actual outcomes instead of crediting
skips as qualification. It exercises the real CLI in a synthetic registered
context, known read-only claims adapter, exact quoted catalog paths,
unsupported-old-client refusal and owned descendant timeout.
The same manual job separately runs the publication blob/identifier guards
through bounded pytest tooling, including native POSIX quoted-command
and finite-reader timeout proof. Windows additionally qualifies pipe-holding
descendant retirement and command-binstub quoting. These remain optional
native checks, not an expansion of required CI.
Live read/coverage evidence remains separate and never requires lifting a
held route or installing the candidate into another owner's runtime.

## Optional Dispatch scoped-stamp binding

The manual `installation-context-full.yml` portfolio `dispatch-binding` runs
`test_dispatch_scoped_publication.py` through the isolated `payload-invocation`
runner on Ubuntu and Windows, with the existing 60-second test deadline.
It does not expand required PR CI.

The packet runs actual custom-root and active-cell stamps with and without
pre-existing legacy primary/board launchers. Stamps must preserve every global
launcher byte-for-byte or leave them absent while retaining selected-root helpers
and healthy payload invocation. Ordinary-shell calls through existing globals
must still reach the healthy legacy root or the active cell selected by the
attributable publisher, never acquire an unselected custom root. Whole-home
snapshots constrain healthy retained-command writes to their existing boot trace.
Equivalent trailing-separator and dot-segment legacy roots must still publish
the global commands. Distinct case-sensitive POSIX roots must preserve them;
both Bash and PowerShell exercise that distinction.
These controls do not qualify native scoped staging, service registration,
runtime construction, host rollout or performance; cold scoped provisioning
retains its explicit unsupported-readiness outcome.

## Optional native pane argument delivery

The manual `installation-context-full.yml` portfolio `pane-argv` runs the real
PSMux direct-child control and both file-based pane producers twice on a fresh
`windows-latest` runner. It downloads PSMux 3.3.6 into disposable job storage
with a pinned archive digest, records the actual checked-out source and Python,
and retains kernel containment and the existing client/query/teardown bounds.
The opt-in two-cycle cases have a 180-second item budget and 45-second
PowerShell setup/payload-readiness limits. Measured native readiness exceeded
the earlier eight-second fixture wait; these functional-integration limits do
not change ordinary unit budgets, production deadlines or performance gates.

Dispatch against the published candidate branch with `-f portfolio=pane-argv`.
This lane qualifies only native argument delivery and owned teardown. It does
not prove interactive Copilot, selected-host desktop visibility, real
uninstall/reinstall survival, handler adoption or rollout. It does not expand
required PR CI or the default scheduled portfolio.
The pinned test tool does not adopt a new production compatibility floor or
upgrade the operator's installed PSMux. The older 3.3.5 baseline also exhibited
intermittent immediate exits and is not qualified by this lane.

## Optional Bridge scoped-stamp binding

The manual `installation-context-full.yml` portfolio `bridge-binding` runs
`test_bridge_cell_stamp_preserves_global_ownership` through the isolated
`payload-invocation` runner on Ubuntu and Windows. It retains the 60-second
test deadline and does not expand required PR CI.

The packet stamps an actual selected cell with and without existing legacy
global commands. Scoped stamping must preserve their exact bytes or leave them
absent. A healthy cell runtime proves payload dispatch before ordinary-shell
controls remove the incoming context. Existing globals may reach the active
cell through its attributable publisher; publisher loss, pointer loss and
activation-CAS deactivation must never turn that cell into a legacy
resolver-only fallback. Synthetic distinct cell and legacy modules identify
the runtime actually executed; no native runtime construction, service
installation, live host adoption or performance-budget claim follows.

## Optional POSIX selected-runtime bootstrap controls

Run `python tools/run-plugin-tests.py runtime-source --test-file
tests/test_bootstrap_selected_readiness.py` with the normal containment and
deadlines. The actual three adopting hooks and vendored shell resolver execute
against fixture-owned runtimes and harmless installer witnesses. Controls cover
completed marker-only/source/repair selections, legacy links, invalid selections
and completion, fallback, changed identities/bytes, first-touch and publication
refusal, exact structured output and read-only trace behavior. A before/after
control records actual installer child counts and hook elapsed time; it does not
assert an invented speed threshold or substitute for installed startup evidence.
Windows skips native POSIX controls explicitly without invoking WSL. Deterministic
host controls still execute the hook's real identity probe and the family sync
contract; they are not shell qualification. Use the isolated Linux integration
fixture for native evidence and full-plugin first-turn validation separately.
No required unit-only CI packet is expanded by this on-demand integration file.

## Optional historical Bridge source proof

The manual `installation-context-full.yml` portfolio `bridge-contracts`
runs only the canonical Bridge evidence checker, HTTP-48 archive byte controls,
HTTP-48/49 publication guards and HTTP-47 history-preservation cases on Ubuntu.
Shared immutable-input preparation has its own bounded qualification control;
each publication case still creates a fresh consumer fixture.
It retains the ordinary runner's containment and test deadlines. This is
source/publication evidence, not SDK, native Windows or installed qualification.
Use a fresh canonical-history-only clone to check proof retention across a
squash; a long-lived clone may hide missing objects. Windows archive identity
controls are independent of the heavier Git-backed publication packet.

## Optional native Mux Companion navigation

The manual `installation-context-full.yml` portfolio `mux-companion` runs
`tests/native/test_companion_pane_tmux.py` on Ubuntu 24.04 with tmux 3.4 and
`tests/native/test_companion_pane_native.py` on Windows with digest-pinned
disposable PSMux 3.3.6 against the dispatch's recorded immutable source.
It verifies actual native option targeting, a fresh
synthetic constructed-prefix invocation, same-window reuse and the unchanged
CLI process in an isolated socket. It is not a required PR lane or a production
runtime rollout. Both use the explicit plugin runner with
`--allow-explicit-tiers --test-file <path>`. For local Windows qualification,
`PSMUX_TEST_BIN` selects a disposable binary without changing the installation;
without it the fixture uses the existing PSMux on PATH.
The same optional portfolio also runs the bounded root-attention provider and
actual fresh-home CLI subprocess contract on Linux. This does not qualify a
live pending question, human input, native status chrome or a deployed runtime.
The optional attention client and real Textual packet additionally exercise
provider rollback, head changes, hidden/unknown visibility, failure backoff,
single-flight, stale-result rejection and actual process exit while an
attention child stays blocked. Native pane fixtures verify the observer's
own pane environment and that detached sessions are not considered visible;
these are not proof of rendered or real-client input/click acceptance.
The older PSMux 3.3.5 baseline has exhibited immediate session exits and later
CLI/directory loss in this fixture. Those observations remain an unqualified
stability gap under #8811, not a reason to skip a failed test or claim that
3.3.6 qualification proves 3.3.5. The fixture does not retry past failures,
weaken native identity checks or modify any installed mux.

## Optional native PSMux launch-directory control

On Windows with PowerShell and PSMux already installed:

```text
python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_psmux_home_directory.py
```

The control uses fresh private profiles/registries and unique namespaces. It
compares legacy and home-scoped launches, verifies the actual pane directory
and caller restoration, and removes the retired checkout while its standby
still runs. On affected PSMux 3.3.5, the legacy case must reproduce the lock.
Both cycles observe owned windows/foreground activity and preserve pre-existing
mux process identities. Cleanup targets only the test's own namespace and
birth-checked processes; no default-namespace shutdown or installed update is
performed. This is an explicit native lane, not additional required PR CI.

## Optional immutable Worktrees runtime qualification

The manual `installation-context-acceptance.yml` selector
`immutable_runtime=true` runs only `tests/test_python_runtime_dev_live.py` through
the canonical isolated `agent-worktrees` plugin runner. Its four native matrix
cells use two explicit jobs with literal Linux (`ubuntu-latest`) and macOS
(`macos-latest`) runner routes, each with a `legacy`/`namespaced` mode matrix
and fail-fast disabled. Python and `UV_PYTHON` are 3.11,
matching the supported first-adopter floor. No host-state opt-in, live
installation or service deployment is used.

```bash
gh workflow run installation-context-acceptance.yml --ref <published-workflow-branch> \
  -f immutable_runtime=true -f immutable_runtime_ref=<full-40-hex-candidate-commit>
```

Publish the candidate normally first; the dispatch branch must contain this
workflow definition. `immutable_runtime_ref` accepts only a full immutable
40-hex commit, or empty to select `github.sha`. Branch names, PR refs and short
SHAs are refused before checkout. Checkout does not persist credentials; each
job verifies and records the actual SHA, parents, interpreter and platform.
The workflow definition's dispatch ref is distinct from the source SHA tested.

Each matrix cell runs:

```bash
python tools/run-plugin-tests.py agent-worktrees \
  --test-file tests/test_python_runtime_dev_live.py -k <legacy-or-namespaced> \
  --timeout 2500 --plugin-timeout 2700
```

The 50-minute job envelope covers the existing 2700-second plugin suite plus
setup. It does not raise the 2400-second test-item cap or the 480-second
production deadline. The plugin runner prepares its own isolated, seasoned
tooling. Selecting this lane suppresses the default diagnostics, while all
other explicit selectors keep their routes. It adds no schedule, required CI
gate or Windows rotation coverage.

Workflow contract tests and CI enablement are not native qualification. Inspect
all four cells' actual selected test outcomes and skips against the recorded
candidate SHA; only real Linux/macOS execution can establish native PASS.

## Optional native update diagnostic contracts

The manual `installation-context-acceptance.yml` selector
`update_diagnostics=true` runs the complete native auth, durable queue and fixed
launch-request contracts on isolated Linux and Windows runners. Each platform
also exercises its actual selected-phase shell front doors, including missing
engine refusal without repair downloads or implicit provisioning.
The packet also routes the real outer runtime planner/executor through Bridge's
native completed-slot installer functions and shared slot evidence, with
healthy-current, older-serving, refused-probe and failed-transition controls.
It verifies one invocation, no build/install fallback and unchanged completed
content. Windows exercises available PowerShell 5.1 and PowerShell 7 hosts;
POSIX exercises Bash. Side-effecting service seams are synthetic: this does not
qualify a live installed daemon or fleet preservation.

```bash
gh workflow run installation-context-acceptance.yml \
  --ref <published-candidate-branch> -f update_diagnostics=true
```

Both jobs check out and record the immutable dispatch SHA without persisting
credentials. The canonical plugin runner resolves its disposable temp parent
before creating the private sandbox, including Windows short-path aliases;
daemon-registry indirection checks remain enabled. Independent test families
still run after an earlier assertion failure when setup succeeded; any failure
keeps the job red. The existing five-second auth budget, twenty-second worker bound,
thirty-second test-item limit and runner aggregate limits remain unchanged.
The fifteen-minute job envelope includes governed tooling preparation, not a
production deadline exception. Selection suppresses default diagnostic-copy
jobs and leaves other explicit selectors unchanged. This opt-in packet adds no
schedule, required PR gate, host-state opt-in or deployment.

Inspect actual selected test results and platform skips against the recorded
SHA. Ordinary unit CI, a collected test or a skipped platform case is not native
qualification.

## Optional macOS guardian follow-through

The manual `installation-context-acceptance.yml` selector
`guardian_followthrough=true` runs a bounded packet on a fresh `macos-latest`
runner, outside required CI. It uses Python 3.11 (supported by the shared
library's Python >=3.10 floor and matching an originally affected interpreter)
and preserves existing test/runner timeouts. `guardian_ref` accepts only a full
40-hex commit; empty means the immutable dispatch SHA, never a moving branch.
Validation precedes checkout, credentials are not persisted, and the job records
the actual checked-out SHA, its parents, Python version and OS.

```bash
gh workflow run installation-context-acceptance.yml --ref dev \
  -f guardian_followthrough=true -f guardian_ref=<full-40-hex-commit>
```

For a pending change, first prepare an immutable merge-preview commit containing
the candidate and current development baseline, then pass that commit's SHA.
Do not pass a mutable PR merge ref or modify the contributor's branch merely to
run qualification. The workflow definition comes from the dispatch ref; the
recorded checkout SHA identifies the source actually tested.

The packet runs all shared-library `test_caller_owned.py` cases (including
Darwin census positive/negative controls when present in the selected source),
all Worktrees `test_installer_guardian.py` cases, the exact companion-lifecycle
guidance/concurrent-embody node, the explicit inline-allowlist guard, and the
Windows-command-shim simulation family. Library tests use the existing
process-containment harness; plugin tests use the existing isolated runner and
seasoned dependency preparation. No host-state opt-in, deployment, live profile
adoption or elevation is used. Selecting this packet suppresses the default
diagnostics; other explicit selectors retain their existing behavior.

Each family has its own logged step. After tooling succeeds, later families
still run following an earlier assertion failure, but that failure keeps the
job red. Setup failure or cancellation is not a pass. Inspect native skips and
individual outcomes, not just collection: Linux/Windows-only controls may
legitimately skip on macOS. Local workflow-schema tests, Windows/Linux results,
or adding this lane do not constitute native macOS PASS. A successful packet
qualifies only its recorded source/interpreter/OS and selected contracts, not a
full plugin corpus, performance benchmark, installation rollout or fleet.

Worktrees' `test_terminal_conclusion.py -k "lifecycle_lock or stale_pid_claim"`
also covers retained live-owner refusal and PID-reuse reclamation with both
integer and macOS `seconds:microseconds` birth identities. The native
companion-lifecycle node above exercises that same lock through concurrent
creation recovery and launch, rather than replacing it with a test double.

## Optional Bridge selected-source snapshots

The manual `installation-context-full.yml` portfolio `bridge-snapshots` qualifies
Bridge's source-qualified selected legacy snapshot for the fixed `provision`
recipe on native Windows and Ubuntu. It uses the actual clean-source
materializer and Bridge stamp, removes the installed publisher/source checkout,
then exercises Python-free admission, caller loss, ordinary queue results and
installed CLI/module/distribution consumers. Same-package source generations
remain distinct; failed candidates preserve the previous completed selection.
Separate bounded runner roots cover admission/freeze refusal matrices, native
construction, installed-source recipe regressions, shared Logger behavior and
canonical-installed-only bootstrap publication. A native launch acknowledgement
is not durable request acceptance.
The portfolio also runs canonical source-preparation regressions and native
daemon wait-status controls, preserving the same job and per-run resource bounds.

Linux fixtures explicitly construct the disposable-daemon native support with
its canonical build helper before cold admission. The synthetic source commit
includes that support before source materialization and identity publication;
no launcher compiles code or mutates an accepted source assembly.
The Python-free Bridge construction PATH retains the platform C compiler,
assembler and linker for the source package's explicit native build; it still
exposes no host Python or UV. This is not compiler-free host qualification.
Acknowledged owners and foundation/queue/kernel custody settle before fixture
cleanup, including failure paths. Registration/start effects alone use isolated
synthetic adapters; no host task/unit or live bridge is launched. This portfolio
does not adopt ordinary command publishers, unreceipted copies, explicit
installers, cells/custom roots or macOS. Exhaustive native qualification stays
manual, outside required PR CI.

## Optional Bridge command adoption

The manual `installation-context-full.yml` portfolio `bridge-commands` qualifies
the actual materialized primary/payload commands on native Windows and Ubuntu.
Independent bounded packets cover Python-free original caller Job/group loss,
concurrent ordinary callers, failed candidates and selected-source execution
with the installed publisher unavailable. Windows also exercises the `.cmd`
front. A launch acknowledgement is not a committed ordinary receipt; the
original request must finish before any replacement submission.

The separate cold-wait transport case installs an explicitly synthetic
application protocol through the real queued Bridge recipe, without rewriting
a completed slot. It exercises held-open binary frames, quoted multiline raw
prompt input, nonzero exit/stderr and Windows PowerShell object input while
callers observe the same cold owner. Product CLI/module/distribution health is
qualified separately using unmodified Bridge sources. Service registration/start
effects alone use the existing isolated adapters, never host tasks or a live
bridge.

Four refusal packets retain complete materialization and source/runtime/queue
no-write comparisons. Publication controls are partitioned by scope/entry into
separate runner roots; warm native transport and canonical snapshot admission
remain separate controls. Existing 35-minute job, 900-second native test,
1080-second runner and 2 GiB temporary-storage bounds are unchanged; publication
cases retain their own 60-second bound. A skipped platform or explicit native
case is not acceptance. This portfolio does not qualify cold cells/custom roots,
unreceipted snapshot migration, explicit installers, macOS, serving or rollout.

## Optional Bridge bootstrap adoption

The manual `installation-context-full.yml` portfolio `bridge-bootstrap` qualifies
`test_first_use_bridge_bootstrap.py` on native Windows and Ubuntu. Separate
bounded runner roots cover materialized foundation/refusals, actual cold hooks,
and retained fixed-recipe regressions. The hook proof measures the real
15-second deadline and exact `{}` stdout with no caller Python/UV/Manager,
caller Job/group loss, concurrent sessions and ordinary durable results.
Drift selects `install`, not prepared Windows `provision`; native task effects
assert S4U/AtStartup while ordinary recipe tests retain Interactive/AtLogOn.
Linux effects inspect the actual unit and intercepted systemctl operations.
No fixture launches a real bridge or creates a host task/unit.

The portfolio also exercises source-aware selected/completed readiness through
the actual POSIX hook, read-only healthy controls, malformed/incomplete selection,
failed-candidate preservation, installed CLI/module/distribution consumers after
payload removal, historical reconcile-file preservation, and the independent
45-second guidance/catalog hook. Join acknowledged owners and settle foundation,
queue and kernel custody before fixture cleanup. Synthetic native effects are
not live serving, remote-session, deployment or rollout evidence. Exhaustive
native integration remains explicitly manual, not required PR CI.

## Optional Logger bootstrap adoption

Logger's corresponding manual `installation-context-full.yml` portfolio
`logger-bootstrap` runs `test_first_use_logger_bootstrap.py` and the retained
`test_first_use_logger.py` recipes on native Windows and Ubuntu. It measures the
actual 15-second hook and exact `{}` stdout, Python-free cold and drift callers,
caller loss, concurrent submissions, ordinary durable receipts/results, complete
packaged foundation and native raw-checkout snapshot mappings. Drift must run
`install` (including isolated task/timer reconciliation), not service-less
`provision`. Healthy no-op, failed-candidate preservation and six installed help
consumers remain covered. The 10-second cold-store provider and 45-second
guidance hooks run independently after bootstrap refusal, preserving exact
payload binstub identity without configuring a host bridge or processing
personal sessions. This is an explicit manual integration lane, not required PR
CI, live timer/serving-worker proof, selected-snapshot recipe admission or rollout.

## Optional CodeSpaces bootstrap adoption

Independent Windows evaluator admission is a separate native packet:
`plugins/agent-codespaces/tests/test_bootstrap_python.py` covers the selector,
while `libs/payload-invocation/tests/test_evaluator_bootstrap_consumers.py`
covers both adopters' dispatcher routing, cleanup, complete SDK parity and
terminal refusals.
Use the existing bounded process-containment harness. Set
`EVALUATOR_BOOTSTRAP_TEST_PYTHON` to an explicit existing signed console CPython
base for actual native admission; a supplied but unfit fixture fails rather
than skipping. The consumer's real-admission case skips without this explicit
fixture. ABI stubs and platform/fixture skips are not native trust qualification.
These subprocess/registry/signature cases are not required pure-unit CI.

The manual `installation-context-full.yml` portfolio `codespaces-bootstrap`
qualifies the actual materialized legacy hook on fresh Windows and Linux
runners. It enforces the existing 15-second deadline and `{}` response,
Python-free cold stamp, overlapping cold supervisors, caller Job/group loss,
ordinary receipt/result, own-installed-payload drift, failed-candidate selection
preservation, healthy no-op and source-independent installed CLI/module/
distribution identity. The foundation must come from canonical payload
materialization, not a fixture's separately supplied source tree.
Windows additionally stamps the actual unmaterialized checkout shape, removes
that source, validates the retained complete local dependency graph and provisions
the real installed CLI from its snapshot. This protects the added driver
dependency; it does not replace #6909's separate copy/runner/native matrix.
POSIX legacy stamping retains its original payload pointer, so that Windows-only
snapshot regression is not relabelled as a POSIX contract.

No-write controls cover publication policy, no-self/pinned/nested invocation,
unsupported source/context and missing/skewed support. Provider/guidance hooks
are exercised independently after bootstrap refusal. Isolated adopted
disabled-Owner configuration and native enabled/disabled/unknown registration
adapters do not qualify live host registration, serving behavior or a CodeSpace
connection. Selected snapshots, command publishers, explicit installers, cells,
custom roots and #6909 acceptance remain separate.

The non-download subset uses `tools/run-plugin-tests.py update-driver
--test-file tests/test_first_use_codespaces_bootstrap.py`; the actual cold
construction test requires the existing platform-specific genesis opt-in.
The plugin smoke packet selects `test_bootstrap_driver.py`,
`test_bootstrap_check_reconcile_always_on.py`, `test_bridge_provider_manifest.py`
and `test_write_session_guidance.py` with the same runner. Heavy native matrices
remain outside required PR CI.

## Optional SSH installation recipes

The manual `installation-context-full.yml` portfolio `ssh-recipes` runs only
the fixed SSH stamp/provision prerequisite on fresh Windows and Linux runners.
It uses the existing isolated runner, canonical materializer, native foundation
and ordinary queue. The packet covers no-write admission refusals, actual
service-less installation from its exact selected legacy snapshot with the
installed payload unavailable, failed-snapshot candidate preservation, source-independent
installed CLI/module origin and caller Job/group loss during Python-free cold
acquisition. Bootstrap controls additionally refuse snapshot/copy publication.
It does not adopt ordinary command publishers, exercise remote SSH, qualify
network-backed retained-source #5379 or cells, or enroll the operator host.
Run the non-download subset locally with `tools/run-plugin-tests.py update-driver
--test-file tests/test_first_use_ssh.py`; the native cold cases require their
existing explicit Windows/Linux genesis opt-ins.

## Optional SSH bootstrap adoption

The separate manual `installation-context-full.yml` portfolio `ssh-bootstrap`
qualifies the actual materialized legacy hook on native Windows and Linux:
its unchanged 15-second deadline, exact `{}` response, Python-free first stamp,
caller Job/group loss, ordinary receipt/result, manifest-selected drift and
healthy reentry. No-write publication/source/nested/skew refusals and independent
hook/deadline controls run alongside it. Initial runtime preparation uses the
already-qualified fixed recipe, not an unadopted SSH command. This lane does
also exercise checkout stamping and dependency-policy validation after removing
the checkout, to retain the new dependency closure without a network install.
That packaging check does not qualify the separate retained-source timeout.
The lane does
not qualify retained-source #5379, command publishers, cells, explicit installers
or host rollout. The non-download subset is
`tools/run-plugin-tests.py update-driver --test-file tests/test_first_use_ssh_bootstrap.py`.

## Optional SSH command adoption

The manual `installation-context-full.yml` portfolio `ssh-commands` separately
qualifies actual materialized legacy primary/payload commands on fresh Windows
and Linux runners. It exercises Python-free first use, caller Job/group loss
across the whole stamp/snapshot/provision request, concurrent callers, retained
source with the installed payload unavailable, healthy argument/exit forwarding
and no-write cold refusals. Healthy cell dispatch remains covered independently.
This does not exercise remote SSH, explicit installers, custom-root/cell cold
publication or the network-backed #5379 acceptance. The non-download subset is
`tools/run-plugin-tests.py update-driver --test-file tests/test_first_use_ssh_command.py`.

## Optional SSH session snapshot

The manual `installation-context-acceptance.yml` selector `ssh_snapshot=true`
runs snapshot, session writer, mesh and fragment-registry tests on Linux and
Windows. The native writer control uses a ready test interpreter and the actual
CLI on fresh/resumed session files; it does not qualify installed-runtime
readiness. It verifies replacement, the 4 KiB bound, preserved catalog, and no
profile/runtime creation. Dispatch's existing real-venv peer fixture also
exercises SSH-to-Worktrees same-cell selection and foreign receipt refusal.
These process boundaries stay outside required unit-only CI.
The Windows job explicitly enables `AGENT_SSH_WINDOWS_VISIBILITY=1`: a native
`pythonw.exe` parent drives the actual writer/contributor/CLI chain through two
cycles and forced timeout while the shared Win32 observer checks visible
windows and foreground transitions. The flag is otherwise off. Resolver
timeout coverage also checks that its nested runtime exits independently of
the writer's outer timeout.

For a focused local run, select `tests/test_session_snapshot.py` and
`tests/test_write_session_guidance.py` with `tools/run-plugin-tests.py agent-ssh`.
The separate real peer control uses `tools/run-plugin-tests.py agent-dispatch
--test-file tests/test_procutil.py -k "ssh_snapshot or (namespaced_sibling_resolution
and agent-ssh)"`. No production credentials, remote hosts or capacity are needed.

## Optional foreground completion custody

The manual `installation-context-acceptance.yml` selector
`interactive_completion=true` runs `libs/agent-procutil/tests/test_interactive.py`
and `test_interactive_session_native.py` on Linux, macOS and Windows. It
exercises real finite commands, normal-exit
descendant cleanup, abrupt caller death, POSIX interruption and controlling-PTY
input/resize/foreground restoration. Windows also generates a real SIGINT in
the owned caller while its command remains blocked, then verifies descendant
exit; it does not broadcast signals to an external console. Job refusal and
cleanup-failure confirmation remain deterministic unit controls. This is
completion custody evidence, not a resident Picker startup benchmark or an
installed-runtime rollout, and does not expand required unit-only PR CI.
The separate-session native cases use a real interactive Bash with background
job groups, preserve redirected JSON stdout and check that an explicitly
managed detached workload remains live. Managed-detachment cases skip inside
the contained local runner rather than bypassing its suppression; they run in
the explicit disposable native workflow.

The same Linux job also runs Manager's
`tests/native/test_warm_picker_lifetime.py` through its isolated plugin runner.
That packet uses an unmodified warm worker on a private tmux TTY, hard-kills
its birth-pinned owner before and after project binding, and verifies UI,
engine-child, mux-session and slot retirement. Its audited engine contract
double records every child state root; the fixture allowlists the environment
and isolates home, app-data, provider, Manager, temp and mux paths. This is
not full engine/daemon or performance qualification. Run that selector locally
on Windows with psmux and PowerShell available; platforms without supported
warm-worker birth identity retain the cold path and skip this packet.

## Optional daemon-instance lifetime acceptance

`tools/clean-room/scenarios/leased-daemon-instances` is a credential-free Tier-P
source/native consumer proof. Use a fresh disposable Linux container, a
read-only candidate and either a prebuilt helper bundle or an explicitly
selected source-build toolchain. Its README defines the inputs and actual
process/filesystem assertions; no normal startup invokes a compiler.

Run `python -m pytest tools/test_native_daemon_artifacts.py` for the artifact
contract controls. On a supported, prepared Linux build host, explicitly select
`PROCUTIL_NATIVE_ARTIFACT_ACCEPTANCE=1` and
`test_real_built_wheel_and_source_fanout_find_native_with_no_python_or_cc`
for real construction, installed lifecycle and compiler/Python-free execution.
That native lane remains opt-in rather than enlarging required unit-only CI.

`python -m pytest tools/test_cleanroom_daemon_fixtures.py` checks finite
Gateway-fixture custody and environment restoration on success/failure, plus
host refusal for the synthetic production-control probe. The existing
process-hygiene scenario retains its full six-scenario flood thresholds; its
daemon roles inherit shared test custody while transient clients remain light.
Supply the installed shared package or an explicit candidate source through
`CR_PPC_PROCUTIL_SOURCE`/`CR_HARNESS_MOUNT`.

Run both `tools/check-daemon-coverage.py` and `tools/check-headless-launch.py`;
visibility flags and lifetime attribution are separate contracts.

`tools/on_demand_tests/test_scratch_copy_boundaries.py` is an explicit filesystem
packet for the bounded, isolated development-test lane. It checks scratch
exclusion before source/test copying and strict link refusal in unfiltered
build contexts. It is not enrolled in the eligible unit packet.

## Optional candidate dependency profiles

The installation-context Python `runtime-profile-policy` reader has no
site-package dependency. `libs/installation-context/tests/test_runtime_profile_policy.py`
exercises a fresh configured adopter with no installation, profile or approval
file, real owner-bound overrides and an actual `-I -S` CLI process.
`test_runtime_deployment_adapters.py` preserves matching native policy parsing
and publication holds, including malformed profile fields. Pure selection,
owner-binding and CLI dispatch controls live in
`tools/promotion_unit_tests/test_runtime_profile_intent.py`; only these pure
controls join required unit CI. These checks prove configuration-policy input,
not a permitted CPU wheel closure, authenticated profile release or native
engine construction. The manual `runtime_profiles=true` acceptance selector
runs the actual readers on Linux and Windows.
The Windows lane enables test-only PowerShell phase markers on stderr.
On a subprocess timeout, the failure retains the parent start timestamp and
child entry, native compilation, path, installation and deployment-reader
markers to distinguish cold startup from reader work. The deadline remains
30 seconds; tracing does not retry or admit a timed-out decision.

`python tools/run-plugin-tests.py agent-worktrees --test-file
tests/test_python_installer_engine.py` covers the canonical candidate engine,
including explicit declared extras. Its offline synthetic packages exercise real
UV construction, installed-origin health, immutable profile reuse and refusal of
an unavailable extra dependency before completion. They also retain the original
base fingerprint and preserve an existing active selector on failure. These are
on-demand construction controls, not additions to the eligible-unit packet.
The same file exercises explicit `local_packages` role closures against real
UV and fresh interpreters: client-only omission, a service's transitive local
closure, complete source retention, immutable reuse without construction tools,
receipt drift and excluded installed-distribution refusal. Incomplete direct or
transitive selections must fail even when prereleases are enabled; they must
not complete a candidate or change the active selector. The caller supplies
role policy; these controls do not add a second dependency resolver.
The manual `installation-context-acceptance.yml` selector
`candidate_profiles=true` runs those focused real-UV profile controls on fresh
Linux and Windows runners without enlarging required PR CI.
They do not prove a production extra graph, hosted feed access, a native support
matrix or required release enforcement. The
[feed-construction effort](efforts/active/feed-construction-release-gate/README.md)
retains those open acceptance gates.

The independent `chat_construction=true` selector runs both the 180-second
acceptance and 600-second complete contained Chat lanes on the same candidate
on Linux and Windows with Python 3.10 and 3.11. Preparation explicitly binds
`UV_PYTHON` to the setup step's interpreter rather than allowing an unrelated
managed Python to change the advertised floor. Python 3.11
constructs the actual client role from independent Chat/library source
distributions using the wheel's unchanged canonical engine, verifies the exact
installed closure and immutable reuse, then invokes the slot-local canonical
retention launcher after deleting source inputs. Python 3.10 retains core/client
qualification and explicitly refuses construction before creating a runtime.
Foreign import scopes and attempts to prepare a completed slot must refuse.
This optional lane does not publish a selected installation, create a host,
prove a governed feed or qualify native subscription/adoption.
`tools/test_standalone_qualification.py` checks its explicit opt-in, literal
hosted runner routes, retained default-lane exclusions, interpreter binding and
exact acceptance/full commands. Deterministic workspace clock-boundary controls
also require forwarded owner timeouts to stay within the original 60-second
budget; no floating-point tolerance is added to that assertion.

## Persistent reproducer boundary controls

`tools/test_reproducer_lab_boundary.py` exercises the pure
[`tools/reproducer-lab/`](tools/reproducer-lab/README.md) declaration and static
Docker-observation controls. Run that selector through the caller's bounded
test supervisor and selected Python/pytest environment. It invokes no Docker,
service, network, dependency installation or resource lifecycle operation.
Exact integer budget boundaries, forbidden/missing exposures, foreign or
duplicate mounts, minimal environment and physical-identity fields are tested.
Even a clean synthetic observation retains explicit unavailable measurement,
egress, exclusive-custody and guest/runtime qualification requirements; this
is not an admission result or persistence proof.
`tools/test_reproducer_lab_monitoring.py` adds storage-only threshold, reserve,
cadence, lifetime, loss/resume and failed-refresh controls, mocked finite
Docker-reader seams and local synthetic guest-traversal files. Run both selectors
through the same bounded supervisor. The actual reader reuses `agent-procutil`
for bounded caller-owned Docker clients; its guest probe has a separate deadline.
The controls invoke no real Docker or service action and do not qualify
persistence, egress, exclusive custody, native capture or strict pre-effect
support. Ordinary-volume thresholds are monitored, never hard quotas. Those
separately authorized native experiments remain open. No unconditional promotion
or platform matrix is added.

`tools/test_reproducer_lab_network.py` adds bounded synthetic observations of
the exact internal bridge network, explicit subject/peer IDs, complete endpoint
membership, external attachment refusal, physical replacement, retained resume
selection and the final command's deadline. Run all three reproducer selectors
together through the bounded supervisor. Static boundary controls also require
the exact private `/tmp` and `/run` flags and declared byte sizes.
These are mocked network inspections and static tmpfs configuration assertions,
not actual egress, guest mount-table, peer-runtime or custody qualification.

`tools/test_reproducer_lab_source.py` adds in-process immutable-source metadata
and mocked materializer controls. The separately selected
`tools/reproducer-lab/source_native_tests.py` uses actual local Git against a
fresh synthetic repository to prove one-commit source closure, dirty/untracked
exclusion and no copied source config/hooks or older history, using actual
SHA-1 and SHA-256 materialization. Pure command controls require system/user
attribute-policy exclusion separately from Git configuration exclusion.
Run either through the bounded supervisor; the native filename is outside
default discovery and does not join required PR/promotion CI.
Neither lane builds an image, creates Docker resources, installs dependencies
or qualifies an executing guest's source or service runtime.

`tools/test_reproducer_lab_mounts.py` covers the opt-in effective volatile-mount
reader using synthetic tables, mocked Docker and local directory-descriptor
fixtures. It checks bounded table bytes/records, read-only root, exact private
tmpfs capacity/flags/owner/mode, distinct devices, nested/shadowed mount refusal,
retained physical selections and failed/expired observations. Run it with the
other pure selectors through the bounded supervisor. It queries no real guest
or host mount table and does not qualify the complete guest mount policy,
executing source/runtime, egress, custody or persistence. No native lane or
unconditional CI matrix is added.

## Wheel-input inspection

`tools/test_installer_dependency_compile.py` covers both ordinary metadata-only
exports and the explicit retained source-graph mode. Native dated loopback-feed
controls freeze source/configuration inputs, export hashed seasoned transitive
dependencies without local distributions, and refuse missing upload-age
evidence without replacing an existing output. Graph and effective-omission
refusals cover inactive markers/extras and recursive requirements, while native
controls preserve root-metadata registry pins and recursively mapped local
dependencies. Context-only argument/environment/ambient project mappings refuse
for direct, recursive and project-metadata inputs. Ordinary compile keeps its directory-dependency boundary. These are
candidate-export controls, not source-origin approval or real-feed admission.

`tools/test_runtime_source_lock.py` composes the real installation-owned planner,
existing source receipt validator and retained compiler. Fresh offline sources
prove the empty third-party case; a dated synthetic loopback feed proves a
nonempty exact hashed candidate. Independent selection mismatch, source mutation,
wrong interpreter target, malformed/inapplicable output, byte-limit exhaustion,
child failure, timeout and cancellation refuse and clean up. Retained inventory
coverage and original digest comparison reject a transient source edit even if
the original is restored before resolution returns. Native candidate mode
rejects universal/platform overrides, including environment configuration.
The caller's deadline covers retention entry, both UV settings probes and
per-chunk source copying as well as compilation and consumption. The same optional
`dependency_compile=true` Linux/Windows lane runs these controls, not required
unit CI. They do not qualify private-feed policy, wheel admission or deployment.

The explicit `retain_source_wheel_lock` path additionally consumes native
`pylock.toml` output, retaining actual filenames/hashes and the compiler's held
configuration. A native export can contain multiple compatible files; this
initial path refuses that ambiguity rather than implementing another wheel
ranking algorithm. Ordinary requirements exports remain unchanged. Real
multi-wheel and source-only feed controls refuse before acquisition or a
third-party backend request. Compressed wheel-tag alternatives pass through the
existing bounded canonicalizer before filename parsing; a regression repeats
each alternative 128 times and asserts the parser receives only one combination.

`tools/test_runtime_source_authentication.py` composes actual installation-owned
fallback intent with the existing configured-runtime-source acquisition and
canonical preparation paths. Real local Git repositories exercise exact
origin/ownership, fetched merged commit/tree, source-policy changes, prepared
byte changes and cleanup. Manager self-update preferences do not authorize
runtime source; its default marketplace choice remains unsupported by this
explicit provider. No artifact index, manifest or caller-provided source receipt
authenticates these inputs.

`tools/test_runtime_source_feed.py` continues that real source selection through
native exact-file resolution, the same held native feed configuration and the
existing bounded TLS acquisition worker. Feed capture must not run another
resolver or fabricate a release selection. A public test-only server leaf,
signed by the existing fixture CA, lets both UV and the worker verify the chain;
the older CA-as-leaf fixture alone is not acceptable to UV.

`tools/test_runtime_source_build.py` extends that path through a single native
runtime/build union lock, byte-inspected build dependencies, an independent
immutable build-tools environment and the existing offline first-party build
frontend. A real PEP 517 backend builds authenticated sources, including the
canonical process-ownership implementation, and imports a declared build-only
registry requirement that must be locked, acquired and installed only in the
tools environment. Actual wheel metadata must match
static runtime declarations and selected index Python hints. The final runtime
contains only its reachable runtime closure, not build tools; installed health
and held-input offline reuse exercise the existing constructor. Negative
controls refuse corrupt RECORDs, direct build URLs, metadata drift and source
policy revocation; a native timeout must settle an actual backend descendant.
The composition exercises the existing `retain_source_profile` owner with
owned offline build custody and its exact local-package selection. Foreign
build roles, changed tool versions/files and an online build request refuse
before the backend runs; ordinary promotion-toolchain controls remain intact.
Fresh and reused tools slots validate their installed interpreter against the
selected marker environment before any backend runs. Native regressions query
real tools interpreters, inject a mismatched target result and require refusal;
the success control still proves unchanged immutable reuse. Offline source
builds use the existing source-freezing helper to keep backend working paths
compact on Windows while checking the original and compact source hashes.
Native settings-only capture additionally proves stable configuration identity,
changed seasoning identity, no lock creation and zero feed requests. A current
build-wheel denial must refuse before tools installation or backend execution.
Source-mode configuration refusals additionally forbid effectful compiler
execution and require zero package requests for implicit public indexes and
unsupported index, credential, TLS and proxy settings.
These tests do not qualify persistent offline discovery, activation, ordinary
consumer enablement or live private-feed operations. The build checks have
their own six-minute Linux/Windows jobs under `dependency_compile=true`, rather
than extending the existing compile job's budget or required unit CI.

The same optional `dependency_compile=true` selector also runs independent
six-minute Linux/Windows installer-latency contract jobs. They check the
shared signed-venv helper's preserved seeded default and explicit UV-owned
`-WithoutPip` mode. Windows additionally verifies real signed copied interpreters,
absence of pip/setuptools and actual fixture-wheel installation by UV.
The canonical Codespaces runner selects only the batched closure/retry,
signed recovery and native cold/warm source-edit cases. Every explicit local
root and reinstall/editable selection is preserved in one retained transaction;
the real native case checks canonical editable source mappings overridden by
normal non-editable installation, all nine installed distributions and
refreshed same-version source. These are disposable correctness checks, not a
whole-update speedup claim, and do not expand required unit-only PR CI.

The selector also runs independent six-minute Logger Linux/Windows batch jobs.
`tests/test_install_batch.py` keeps its native cases in explicit T3: canonical
and materialized payloads must make two governed calls, retain every local
root and named reinstall, stop before activation on either transaction's
failure, and skip package work for a reused construction. Real UV installs
fixture build prerequisites and eight local distributions into a fresh
unseeded slot, then refreshes a same-version source edit. Build isolation and
seasoning remain enabled. The existing offline full-provision fixture checks
the same batching against the real Logger package, health and publication flow.
Logger's existing Windows retry fixtures allow 180 seconds for their native
subprocess and 210 seconds per item: three/four-attempt cases exceeded the old
20-second subprocess cap on a slow host. Exact attempt counts, mocked 3/6/10
backoff, failure propagation and production deadlines are unchanged. Run these
separately from the full-provision case to retain the aggregate runner budget.

`tools/test_installer_wheel_acquisition.py` uses a public test-only localhost TLS
certificate/key and real owned workers to check exact file/redirect trust,
credential isolation, file policy, inclusive byte/storage limits, blocked DNS,
trickling headers/body and cleanup. `tools/test_runtime_feed_configuration.py`
composes the actual owner resolver and native UV preflight with TLS download,
complete-profile inspection and a real offline numbered runtime. It verifies
actual installed distributions/health and acquisition-policy receipt binding,
with poisoned ambient CA/proxy/resolver/cache settings. Source selection,
supported native configuration, stable policy identity and unsupported-provider/
constraint refusals are distinct controls. Release-verifier backend responses
remain synthetic; no live-feed or production approval is claimed.
The existing opt-in `wheel_inputs=true` Linux/Windows jobs include both suites;
their six-minute budget and required unit-only CI roster are unchanged.
Native deployment-reader cases also cover `downloadOrigins` parity. A real
constructor timeout control proves the shared deadline settles its child and
lease, removes partial inputs and preserves the selected healthy prior.
Windows controls retain a wheel whose path exceeds 260 characters and observe
two real console-worker cycles from a `pythonw.exe` parent, with zero surfaced
windows or foreground transitions. Storage thresholds measure file-content
bytes, including control/helper copies; filesystem allocation overhead and
enabled budgets still require the parent effort's separate measurements.

`tools/test_runtime_source_wheels.py` continues the retained selection through
real first-party/helper wheel builds, complete metadata/extras validation and the
existing offline immutable constructor. The source profile is frozen before the
constructor creates its first slot. Its receipt binds the selected source,
fallback trigger, policy, target, exact complete profile and locked build tools.
The constructor uses the selected owner's runtime root and source generation,
not an alternate caller-supplied destination. Changed source, altered/missing
third-party lock inputs and dropped helper extras refuse. Unit-only graph and
build-deadline controls live in
`tools/promotion_unit_tests/test_source_profile_derivation.py`.
Built wheels must preserve the retained source's dependencies, extras and
Python requirement. Native regressions alter actual wheel metadata, refuse
nonordinary inputs before opening, bound compressed filename tags before
expansion and reject oversized WHEEL metadata before an unbounded read.
Deadline-bound builds use the existing installer process-tree supervisor;
a real backend's delayed writer must exit before staging custody is released.
Both absolute and PATH-only UV selection reach real construction using the
same selected absolute executable throughout building and assembly.
An installed-target mismatch must refuse before the constructor writes its
completion marker. Third-party RECORD entries accept the runtime verifier's
SHA-256/384/512 policy; built first-party wheels retain SHA-256-only records.
Marker probes bypass site initialization: a real venv control proves that
an installed `.pth` hook executes normally, but not during target inspection.
The existing `wheel_inputs=true` opt-in also runs a separate six-minute native
source-profile packet on Linux, Windows and macOS. This does not extend the existing
wheel-inspection job's deadline. These are synthetic source/byte and fresh-root
construction controls, not private-feed acquisition, production CPU/accelerator
profiles, persistent reuse admission or deployment. Callers still own permitted
third-party acquisition and retain the source-lock and wheel contexts through
health; no new package approver or feed exception is introduced.
CI-scope inventory checks execute all four actual pytest selectors in one
bounded child, sharing interpreter imports but not collection results. Every
partition still has its original exact-inventory assertions; the aggregate
child retains the original twenty-second cap. The macOS source-consumer lane
does not qualify standalone-service supervision or new engine deployment parity.
A clean `-I -S -B` caller also denies native-loader operations during consumer
import. Feed-governance native adapters load only when their operations run;
the no-actor packet neither preloads them nor permits their effects.

`python -m pytest -q tools/test_installer_wheel_inputs.py` exercises real
synthetic wheel archives, streamed digest/RECORD checks, independent selected
metadata mutations, ambiguous/linked/encrypted members, compressed metadata
limits, deadlines and source mutation/replacement. A real isolated `-I -S`
interpreter proves the canonical inspector needs no site packages. The
five byte-only cases in `tools/promotion_unit_tests/test_wheel_inputs.py`
join the required no-actor packet alongside complete-profile checks in
`tools/promotion_unit_tests/test_runtime_profile_wheels.py`. These cover
SHA-256/384/512 RECORD policies, SHA-256 source facts, complete-set budgets,
additional inspector limits and the shared deadline. Native filesystem/bootstrap
checks remain in the development suite. Existing promotion and
standalone artifact suites exercise the shared verifier's trusted-source
import closure and source/toolchain checks. The optional
`installation-context-acceptance.yml` selector `wheel_inputs=true` runs the
native inspector, complete-profile verification, existing exact-profile controls, promotion wheel refusals
and trusted CLI import-closure check on Linux and Windows. It does not expand
the required unit-only CI packet to include native processes.

These are inspector-component tests, not proof of authenticated profile/feed
admission, retained offline assembly or installed runtime activation.

`tools/test_installer_wheel_snapshot.py` covers exact-set byte custody through
`installer_wheel_snapshot.retain_wheel_inputs`. The helper reuses the installer's
private temporary-input owner, copies only ordinary unlinked files, bounds
wheel count, per-wheel/aggregate bytes and temporary archive storage, and shares
the caller's deadline through streamed copying and custody rechecks. It yields
read-only filename/path and filename/stream maps for the existing complete-profile
verifier. Call `snapshot.verify()` before a dependent publication boundary;
normal context exit also rechecks custody, then closes streams and removes only
owned copies. Caller files need not remain unchanged after the snapshot yields.
The expected SHA-256 map is byte evidence, not authenticated profile/feed policy.
Windows controls additionally prove read-sharing permits independent readers
but denies writes/replacement, handles access-time-only metadata updates without
a false byte-mutation refusal, and preserves extended-length input paths.

Fresh/cache parity, exact inclusive budgets, pre-open replacement, mutation of an
earlier source, growth during copying, read/write errors, final-EOF deadline,
retained mutation, cancellation cleanup and an actual `-I -S` bootstrap child
are exercised in the same optional `wheel_inputs=true` native lane. No resolver,
backend, venv constructor or production profile consumer is enabled by this API.

`tools/test_installer_closed_wheels.py` extends that same optional native lane
through the actual existing candidate constructor. Fresh independent venvs
consume an inspected transitive wheel closure without ambient configuration,
network, cache, dependency resolution or builds. The tests check installed
health and exact distribution/origin receipts, PEP 440 version aliases,
unchanged cached-byte reuse without reconstruction, changed binding refusal,
installed receipt/closure corruption, and preservation of the selected prior
after health failure or cancellation. These are real local first-touch and
consumer-contract controls, not an authenticated approval/feed adapter or
production consumer activation.
The large-closure control constructs and reuses 97 actual wheels: its complete
receipt exceeds 32 KiB, while each real health-child command stays below
16,000 characters using digest-bound evidence read from the owned receipt.

`tools/test_runtime_profile_release.py` exercises the release authority adapter
and pre-signing validation through the existing promotion index and attestation
verifier. It checks exact producer/workflow pins, full manifest/first-party
bindings, wrong selection facts, forged approval origins, byte limits,
replacement races and private-input cleanup after failure or cancellation.
It joins the same optional native lane, not the required no-actor packet.
Synthetic attestation responses test the actual verifier's command boundary;
there is not yet a published runtime-profile subject or a production consumer.
These controls do not qualify adopting-policy defaults, governed acquisition,
source fallback or live release approval.

`tools/test_runtime_profile_policy_plan.py` composes the real installation-owned
policy resolver with that release verifier in the same optional native lane.
It covers a fresh no-file baseline, explicit/retained backend intent, disabled
fallback, invalid/foreign/pending owners, and failure-class separation. A fresh
isolated Python caller can request source fallback without artifact infrastructure.
Supplied subjects still authenticate when their artifact is absent; malformed,
ambiguous or unauthenticated subjects never become availability outcomes.
The result is a frozen policy plan, not source verification, an exact source
lock, feed admission or permission to bypass a publication hold.

## Optional Dispatch service replay

`tools/run-plugin-tests.py agent-dispatch --test-file tests/test_delegated_replay_consumer.py
--test-file tests/test_delegated_replay_binding.py
--test-file tests/test_delegated_replay_transport.py` exercises the finite replay
consumer and actual TLS/PoP Gateway/connector with isolated SQLite and replay
stores. Dispatch's dev preparation selects the same checkout's PR and Fleet
packages; default Dispatch installs remain independent. The existing manual
`installation-context-acceptance.yml` `dispatch_delegation=true` selector runs
these alongside the local callback contracts on Linux and Windows. No new job,
production credential or fleet placement is added. This does not qualify the
unimplemented installed consumer lifecycle or production fleet budgets.
Scoped controls retain coexisting v1/v2 rows without conversion, reject changed
bindings and preserve older v1 client construction. Both versions exercise
the actual watcher-to-Dispatch nonevent/restart/lost-ACK path and lost committed
register/ACK/cancel responses through real TLS/PoP and both disk owners.
The programmatic `server.build_app` control exercises the selected scoped
factory through the real front door's queue, active-route checkpoint and
lifespan/drain. Default-import protection covers that front door with neither
optional PR nor Fleet package admitted.

The same manual packet includes `test_delegated_replay_credentials.py`, a T2
integration over the actual watcher, scoped Dispatch consumer, native GitHub/ADO readers
and typed TLS credential exchange. Per-request renewal must override poisoned
ambient credentials; provider refusal across a drained watcher restart must
retain original waits without a wake even when the synthetic PR has merged.
Restored authorization settles one wake per wait without persisted credentials.
The fixture substitutes GitHub forge CLI responses or ADO HTTP responses after
the actual native request/header construction, not credential acquisition or
native snapshot construction. The ADO control uses bearer authentication and
the configured observation reader, not the legacy `az` snapshot path; it does
not certify basic-auth or broader ADO conformance. It is not a live provider,
forge TLS-transport or deployment test.
Its two service lifetimes and nested native commands have a 180-second Windows
and 120-second non-Windows item watchdog; production deadlines and the existing
ten-minute manual jobs remain unchanged. This does not add native work to the
required unit-only portfolio.

The real watcher nonevent/restart/lost-ACK case has a 120-second item watchdog
on Windows. Its two service lifetimes, native subprocess observations and
durable recovery measured 20.90/23.46 seconds locally for v1/v2, while hosted
Windows exhausted the ordinary 30-second item budget. This functional
integration allowance leaves its readiness, request and drain deadlines,
assertions, non-Windows item limit and runner/workflow budgets unchanged.

The shared-maintenance cardinality case in `test_waiter_maintenance.py` retains
real cold SQLite schema creation, 40 durable waiter preparations and a bounded
32-row replay cycle. It has a 120-second item watchdog on Windows: hosted
acceptance exhausted the default 30 seconds during the initial schema commit,
before waiter preparation began. Other items, non-Windows item limits and the
runner/workflow budgets are unchanged; this is not a production database change.

## Optional PR service lifecycle

The existing `pr_service=true` manual Linux/Windows lane also runs
`services/pr/tests/test_image_inputs.py` through the standalone runner. It
checks the repository-only offline-context consumer with real retained
synthetic wheels, a fresh offline hash-enforced installation and tampered-wheel
refusal. It neither contacts a feed nor invokes Docker. Profile/feed/base
approval, real image construction and container lifecycle remain separate
acceptance tiers; this lane does not substitute for them.

The independent `dependency_compile=true` selector exercises the canonical
retained lock-export API on Linux and Windows. `tools/test_installer_dependency_compile.py`
uses a synthetic loopback package index: ordinary UV selects a recent transitive
wheel, while the retained export selects the seasoned version with exact hashes,
ignores stale output pins, survives mutations of original project/requirement/
configuration files through canonical and absolute-dot input/include paths, and refuses
missing upload timestamps. Directory requirement edges and local source mappings
refuse before export; metadata-only project inputs still produce registry-only
hashed output after the private snapshot is removed. The same bounded
lane retains the existing transitive/isolated-build installer age cases.
Root-working-directory coverage prevents a canonical retained include from
being remapped a second time when its original path remains a retained suffix.
Pure option, effective-output and resolver-drift controls run alongside those
native cases. This does not resolve a governed feed or approve a target profile;
the subprocess/network cases remain outside required unit-only CI.

The separate manual selector `pr_artifacts=true` exercises real first-party
wheel construction and verification on Linux and Windows through
`tools/smoke_public_toolchain.py --standalone`. It shares one seasoned-public
build-toolchain lock with the existing plugin builds, validates PR/Fleet
standalone bundles against their source/resource closure, and installs only
those built wheels offline into a fresh environment outside the checkout.
It also exports both standalone projects through a real nested promotion
scratch worktree with a simulated reuse-catalog miss, validates the canonical
source bundles and admission identity, and checks cleanup.
This is packaging evidence, not runtime dependency admission or image proof.
`tools/test_standalone_python_artifacts.py` retains the fuller synthetic
builder/bundle refusal matrix; pure identity, inventory, exact-hook and export
controls in `tools/promotion_unit_tests/test_standalone_artifacts.py` join the
required no-actor unit packet without adding native builds to required CI.

The manual `installation-context-acceptance.yml` selector `pr_service=true`
runs the focused PR serving/observation lifecycle on Linux and Windows:
`test_watch_replay_store.py`, `test_watch_service.py`, `test_watch_gateway.py`,
`test_watch_gateway_transport.py`, `test_watch_observer.py`,
`test_watch_replay_client.py`, `test_watch_replay_binding.py` and
`test_watch_replay_client_transport.py`, through the
existing contained plugin runner. It exercises real locks/disk/native children,
TLS/PoP reconnect, restart and drain, with synthetic issuer/provider inputs.
V1/v2 client controls cover expected scope, post-discovery remapping,
legacy-provider rollback without downgrade, original-ID reconciliation and
credential/channel loss before lookup or during an already-started commit.
It adds no required-PR process matrix and does not qualify a production
credential provider, installed artifact or fleet placement.

The same manual selector also runs `tools/run-standalone-tests.py services/pr
--prepare --timeout 180`. Its independent environment installs this checkout's
PR/Fleet dependencies and the standalone wheel without Copilot. Tests build a
normal source-independent wheel, validate the existing reference-only
`container.service` declaration, and drive an installed foreground service in
a separate process over Fleet's real TLS/PoP Gateway fixture. Provision,
duplicate writer refusal, register/observe, token-file reread on reconnect,
durable restart and exact ACK use synthetic identities/provider snapshots.
Windows uses the public embedding stop event; Linux exercises CLI SIGTERM.
Windows passive-reader rename sharing is tested at the actual file boundary.
These controls do not qualify a live provider, built container or fleet
placement, and do not close the remaining production-performance/update gates.
The focused observer/service tests additionally exercise a four-read admission
bound, rolling progress past a blocked target, distinct subscriber identities,
real native-child completion order and retained writer custody until every
parallel read settles. These source controls are not a fleet latency benchmark.

The same store/service and installed-host selections exercise durable
fleet/Gateway/slot binding on every disk transaction, cross-mode and wrong-volume
refusal without mutation, original-ID recovery after uncertain commits, and
same-bound-volume restart/replay/ACK. Installed host processes try each changed
identity after clean drain and must refuse without native reads or replay writes.
This is a disk-schema/update safeguard, not an image build, automatic migration,
cross-host writer fence or completed update/rollback lifecycle.

## Optional SSH service and native relay

The independent `ssh_service=true` selector runs the contained standalone
`services/ssh` suite on Linux and Windows. It covers installed foreground
Gateway registration, later-machine enrollment/notifications, original-request
recovery and writer drain. Its disposable environment installs the complete
dependency graph non-editably through the existing guarded preparer. The
source-free consumer witness uses isolated Python without startup `.pth` files,
checks installed distribution custody, refuses checkout reads and external
source imports, and reuses the real connected/restart convergence scenario. An
intentional checkout import must fail under the same guard. This qualifies an
installed runtime, not release-artifact provenance, a selected image or managed
installation/adoption. The Linux lane additionally selects native
acceptance in its disposable runner account: real OpenSSH listener custody,
two distinct relay keys, a loopback reverse forward, a peer's permitted local
forward and an unauthorized reverse-bind refusal. It never invokes a remote
shell, exec, PTY or subsystem. Native target-login credentials remain distinct.
The fixture owns one finite temporary directory in that disposable account's
actual home so production `StrictModes` remains enabled; it does not read or
modify existing SSH configuration. Do not select
`SSH_SERVICE_NATIVE_ACCEPTANCE=1` in a production account.
The Linux job also runs both target-login native fixtures in that disposable
account. Their finite home-directory fixtures require
`AGENT_SSH_NATIVE_DISPOSABLE=1` and the plugin runner's explicit
`--allow-host-state --allow-explicit-tiers`; default local runs skip them rather
than writing to an ordinary account's home or relaxing `StrictModes`.
The Windows lane remains control-plane-only and explicitly skips Linux-native
cases. These tests do not qualify a built native container image, normal Fleet
placement, provider/Vault admission or a preserving production migration.

The same suite covers optional explicit peer approval: fresh/restarted pending
state, exact public fingerprints, authenticated active approvers, self/stale/
unauthorized refusal, distinct confirmed recovery and original-decision retries.
Its real TLS/JWT/DPoP test starts the installed CLI with three synthetic caller
identities, proves that pending enrollment cannot notify/admit, and restarts
that owner against retained state. The Gateway fixture follows real time for
that native restart instead of weakening proof freshness around a frozen unit
clock. SDK tests additionally prove old/missing support and policy rollback
refuse before a required-policy request can admit anything. These remain
contained standalone/explicit-lane tests, not additions to required unit CI.
The maximum-binding regressions assert the actual 32 KiB response boundary:
compact original decisions remain retrievable as the directory grows,
oversized response construction refuses, capacity failure rolls back the whole
decision transaction, and every long-identifier candidate remains reachable
through byte-bounded pending pages.
The exact-limit short-principal control exercises a 32,769-byte terminal
response through the actual client/service route, then proves both cursor
representations are budgeted and every candidate remains reachable.
Native restart consumers share the Gateway fixture's realtime clock. Invalid
local writer scopes also refuse before credential-runtime imports, with the
installed CLI's existing fifteen-second refusal deadline unchanged.
Policy-round-trip controls also prove that a retained observation is durable,
stable across later directory growth, and remains readable as superseded after
a generation change. New identical request aliases preserve the original
candidate decision; a withdrawn status caller cannot commit receipt repair.

## Optional diagnostic telemetry

Manager's `tests/test_launch_update_requests.py` native lane checks the user-local
automatic-maintenance veto in both Windows shells and POSIX Bash, including
malformed/unreadable policy and preserved launch continuation. Its real-driver
control separates pending-request coalescing from a distinct project and keeps
each native handoff's 20-second bound. The enclosing integration item has a
120-second aggregate budget for up to three handoffs, a cold-owner handshake,
and canonical drain; this is not a production timeout or required-unit CI
expansion. Run it with `tools/run-plugin-tests.py worktree-manager --test-file
tests/test_launch_update_requests.py`.

Manager's source-native consumer lane is documented in
[`worktree-manager/docs/source-observation.md`](worktree-manager/docs/source-observation.md).
Select its `test_performance_observation.py`, `test_performance_native.py` and
production Picker `test_launch_trace.py` with the existing bounded runner.
The native process/window controls are explicit integration work, not additions
to the required pure-unit packet. Its report distinguishes real source refresh/
input callbacks from unavailable headed paint and production row authority.

The shared `libs/process-telemetry` contract is exercised by its real Bridge
consumer. Run the bounded runner for `agent-bridge` with
`--test-file tests/test_process_telemetry.py --test-file tests/test_telemetry.py`
and for `agent-dispatch` with `--test-file tests/test_telemetry.py`.
Add `--test-file tests/test_spool_append.py` to Bridge for the shared synchronous
append contract: native thread/process contention, Unicode paths/bytes,
one append-only Windows handle per record, and failed/short-write cleanup
without suffix retries. All inputs are synthetic and use runner-owned files.
These tests prove bounded producer behavior and compatibility, not native
startup latency or OpenTelemetry export. Threaded blocked/throwing sink controls
belong to the on-demand integration portfolio, not required unit-only CI.
The [reviewed effort](efforts/active/performance-telemetry/README.md) retains
native boundary, actual SDK/local receiver and overhead qualification.

For the optional export lane, explicitly prepare the isolated test environment
with `agent-process-telemetry[otlp]` using the source library and the same
seven-day dependency policy, then select `tests/test_otlp_telemetry.py` in the
Bridge runner. It uses an owned port-zero loopback receiver and synthetic
protobuf file readback, never a shared collector. A missing extra skips the
module and is not passing wire proof. Normal producer installs remain SDK-free.

For the closed source Logs/Metrics adapter, add
`--test-file tests/test_otlp_signals.py` to the Bridge selection. Its pure models
run without the SDK; genuine protobuf cases explicitly skip when the optional
extra is absent. The floor and currently seasoned SDK/proto/proto-common
resolution are 1.45.1. Required unit CI is not widened into the native portfolio.
The owned port-zero fixtures cover actual public SDK objects, official protobuf,
partial/unsupported/malformed ACKs, total-batch slow-drip deadlines, per-signal
loss accounting and trace-only compatibility. No SDK-private encoders, global
providers, environment mutation or extra producer workers are used.
Manager's source-observation selection includes the same-writer SDK/readback
control; its receiver is a finite test fixture, not the shared collector.
Actual native Logs/Metrics receiver qualification and installed enrollment stay
separately authorized in the [reviewed plan](efforts/active/performance-telemetry/structured-signals.md).

## Static SSH plugin preparation

Bridge's `tests/test_ssh_plugin_preparation.py` executes the streamed stdlib
receiver against isolated synthetic checkouts, without installing Bridge or
starting Copilot. It covers target-native marketplace authority, disablement and
missing-payload refusal; a payload larger than Windows argv limits; UTF-8/spaced
paths; hidden Windows Python with concurrent stdout/stderr drains; and bounded
installer-descendant cleanup, including early worker success/error and loss of
the supervisor while the worker remains active beyond its configured deadline.
Nested execution directories retain their productive cwd while registration
and compiled-context validation use the verified Git root. The
session-qualified controller-owner controls verify both a matching session and
refusal of a mismatched one.

Two live SSH controls additionally require
`AGENT_BRIDGE_TEST_SAME_FILESYSTEM_SSH=<configured-profile>`. Select an existing
profile that reaches the test runner's own filesystem and OS; the fixture checks
access to its isolated temporary checkout before proceeding. It never changes
SSH profiles, installs a remote runtime, starts an agent or changes global
plugin preferences. The controls prove actual SSH streaming and cancellation
quiescence, not cross-machine native plugin discovery. Leave this opt-in unset
for ordinary contained test runs.

## Targeted launch-maintenance acceptance

Manually dispatch `windows-coverage-rotation.yml` with the candidate `ref` and
`launch_maintenance: true` to run Worktrees, Manager and shared-driver launch-maintenance
selections through the existing hardened, network-isolated Linux container
wrapper. The resolved source SHA, exact selector, wrapper invocation and actual
exit status are recorded in each execution artifact. The ordinary daily
nine-plugin portfolio is unchanged. A targeted run never files the shared
portfolio failure issue, even when it targets `dev`.

This exercises native Bash handoff, canonical driver coordination and explicit
failure behavior, not Windows broker activation, installed rollout or every
integration family. The disabled Windows rotation stays disabled; local
contained Windows evidence is recorded separately. Required PR CI and promotion
retain their existing unit-only policy.

## Construction-source inventory

With Python 3.11 or newer, `python tools/construction_inventory.py` reads the
tracked Python projects,
canonical marketplace and `standalone_consumers.py` roster without constructing
or launching any runtime. It records declared package/Python requirements,
optional dependency group names and script bindings. Products and optional
extras are not assumed to be separate environments; canonical and identified
vendored libraries remain construction inputs.

`--root` binds every ownership declaration to that requested checkout. Its
standalone roster is read as an immutable literal tuple, never imported or
executed; mutable, missing, computed or ambiguous declarations are refused.

Plugin-owned Python projects below a plugin root are declared with
`additionalPythonProjects` in that plugin's manifest, using plugin-relative
`pyproject.toml` paths. Missing, duplicate or escaping declarations are refused.
This inert source-owner declaration does not select or install an environment.
Resolved project locations must stay within their owning plugin/product root
and cannot acquire multiple owners through path aliases.

Exit `1` reports unclassified Python projects, including nested projects and
fixtures that have no established source-owner classification. Exit `2` reports
invalid, missing, duplicate or escaping source declarations. Exit `0` means only
that source-owner classification is complete: `runtime_qualification` remains
`not-performed`. Actual environment composition, selected production profiles,
native interpreter matrix, canonical construction/launch actions and bootstrap/
build closure still require classification and execution. This diagnostic is
not a configured-feed CI or promotion gate.

Run `python -m unittest discover -s tools -p test_construction_inventory.py -v`
for its local contracts. The real CLI control creates a private Git fixture and
checks the actual tracked-tree invocation and nonzero unknown-owner result.
That native subprocess control is separate from eligible-unit promotion
evidence; this change does not add it to the admitted unit packet or alter CI.

## Construction-result contract

`tools/construction_qualification.py` is a pure result-admission library, not a
runner or an active release gate. Its `assess_qualification(expected_plan,
report, target_policy=...)` API requires independently derived expected inputs.
The version-two
`copilot-extensions.construction-plan` binds the source commit, exact composed
candidate digest, effective package-policy and native-target-policy digests and nonempty environment
matrix. Each row names its source project, profile, normalized sorted extras,
exact native OS/interpreter patch/architecture/ABI/hardware class, and digests of its canonical
construction and installed-launch specifications. Those opaque specification
digests bind the existing owners' actions; they are not a new execution format.
`plan_digest` canonically orders rows without making different profiles,
constructors, launch actions or native targets interchangeable.

A version-two `copilot-extensions.construction-results` report names that plan digest and
one result per row. Every result records the observed native identity, elapsed
seconds, terminal status and five check records: `construction`,
`installed_origin`, `installed_launch`, `feed_policy` and `cold_environment`.
Each check needs `passed` and its evidence digest. Missing/extra rows, stale
bindings, another native interpreter, failed/skipped/timed-out/unavailable rows
or incomplete evidence yield `qualified: false` with explicit reasons.
Malformed schemas, duplicate rows, nonfinite/negative durations and invalid
digests raise `QualificationError`; callers must surface these as failures.
There is no successful skip or empty-matrix qualification.

The authoritative native-target policy is `tools/construction-targets.json`.
It requires x64 and ARM64 Windows, Linux and macOS, plus actual Raspberry Pi 5
hardware running 64-bit Linux/ARM64. Generic Linux ARM64 evidence is not a
Pi 5 hardware observation. Each environment/profile/extra selection must cover
every target on every enumerated Python minor; a report with exact coverage of
a reduced plan still refuses the missing target rows. Native policy digest
changes, duplicate target/minor coverage and nonstandard interpreter/ABI
substitutions also refuse qualification.

The qualification floor is Python 3.11; existing package compatibility metadata
is unchanged. The policy currently enumerates standard GIL-enabled CPython
3.11 through 3.15, the stable minors listed by the
[Python Developer's Guide](https://devguide.python.org/versions/) as checked on
2026-10-23. Future stable minors require a reviewed policy update. The trusted
plan producer must independently resolve the latest stable patch per minor and
record each exact version/ABI; that resolver is not implemented here. The
hardware target follows the
[Raspberry Pi 5 product specification](https://pip.raspberrypi.com/documents/RP-008348-DS-3-raspberry-pi-5-product-brief.pdf).
Capacity or compatible-wheel unavailability is failed qualification, not
authority to trim the policy. WSL observations do not prove native Pi hardware.

**Trust boundary:** matching self-reported hashes do not authenticate a producer
or prove execution. The future trusted consumer must derive the complete plan
from authoritative source/profile/platform declarations, bind actual policy and
candidate inputs, acquire authenticated evidence from the expected producer and
verify referenced artifacts before calling this library. It must not accept an
expected plan or native-target policy supplied by the result producer. No complete environment catalog,
feed resolver, native executor, artifact verifier, CI check, promotion consumer
or publication authority is introduced here. Existing source-owner inventory
and installer-readiness declarations alone are not that complete plan.
Version-one plans/results are not accepted as version-two hardware/policy
evidence; there is no inferred generic-hardware or target-policy default.

Run `python -m unittest discover -s tools -p test_construction_qualification.py -v`
for the in-process contract controls. These tests perform no installation,
network or native-process work and are not yet added to the admitted unit
packet. Hosted feed, native construction/launch and required negative
CI/promotion enforcement remain open in the
[feed-construction effort](efforts/active/feed-construction-release-gate/README.md).
The full 35-target synthetic matrix and its reduction/Pi-hardware/ABI controls
are in-process data tests, not 35 native executions.

## Native file-link prerequisites

`tools/test_dependency_uv_executable_native.py` qualifies both absolute and
named Windows UV commands through real guarded SDK/Python-owned installs.
Named controls use an owned child's fixture-only PATH; external `*-system`
qualification retains the target's actual PATH and verifies it is unchanged.

Repository-tool installer-sync and vendor-guard link controls use the shared
`native_file_symlink` fixture. Only Windows error 1314, raised by actual file-link
creation, produces an explicit missing-privilege skip; other failures propagate.
Supported POSIX and provisioned Windows hosts still execute the real linked-file
refusal assertions. No test enables Developer Mode or elevates the process.
`tools/test_symlink_capability.py` checks that classification independently.

## Client frontdoor native acceptance

The Fleet test helper gives **wheel construction only** a 60-second budget,
separate from its 30-second native-command and source-distribution
budgets. This covers cold setuptools import, canonical resource checks and file
copying, not a product/runtime or publication deadline. The local-Gateway
distribution proof installs the real wheel offline and runs independent
60-second SDK probes for both supported identity profiles and normal/UNKNOWN
recovery. This probe-only allowance includes cold cryptographic imports; the
shared native-command default remains 30 seconds and product/effect deadlines
are unchanged. Each probe uses a minimal pytest-free consumer with real TLS,
the SDK caller and native owner, not another test collector. On slower hosts,
select each matrix case separately under the 120-second qualification
envelope. A prepared qualifier with `pytest-timeout` may instead share the
session's immutable wheel across the module, enforcing 120 seconds per case
(including setup/teardown) inside the existing 600-second component envelope.
Every installed consumer remains a fresh process and target; this is artifact
reuse, not cached execution evidence. No fixture or source callback is readiness proof.

The preparation Gateway's real-Git functional fixture selects the existing
supported 30-second acquisition configuration for repeated source pulls.
Production defaults to 15 seconds and caps selection at 30. Separate
controls assert the default and a real authenticated deadline refusal;
this does not relax the source owner or Gateway's execution limits.

`fleet-manager/tests/test_client_frontdoor_native.py` separates the real
construction prerequisite, concurrent independent registration owners, and
platform command/exact-rerun behavior. The constructed client remains the real
offline installed distribution; no construction, health, publication or
registration primitive is mocked. Per-case 90-second, wheel-build 60-second,
registration-child 60-second and command 20-second bounds remain unchanged.
Each registration case uses actual owned command withdrawal at teardown so a
different test's directory cannot become its registration authority.

On slower native Windows hosts, run each native case as a separate
`PYTEST_ADDOPTS=-k` selection through
`tools/run-standalone-tests.py`, retaining its 120-second caller bound. Use
`construction_prerequisite`, `owners_serialize_and_reuse` and
`commands_and_exact_rerun` in separate invocations. Each registration/command
case still constructs its real prerequisite through the shared fixture; the
separate construction case asserts the prerequisite's own contract. An owner
selection may instead include `construction_prerequisite or owners_serialize_and_reuse`
so the first test owns construction's existing 90-second clock before the
independent-owner case's own 90-second clock begins. That pair still has the
same 120-second caller bound; it is not a function-only timeout exemption. A missed
stage deadline is still a failure, not a reason to omit health, skip an
assertion or extend the timeout. Ordinary ownership/fault cases remain in
`test_client_frontdoor.py`; the periodic standalone suite discovers both files.

`fleet-manager/tests/test_client_recovery_native.py` separately exercises the
real construction prerequisite, installed `recover-retained` CLI, actual
platform commands/exact repeat, and concurrent independent installed owners.
The CLI uses independently signed synthetic evidence valid at the real wall
clock; no health, constructor, publication, clock or registration primitive is
mocked. Existing build/constructor/owner/command/test/caller deadlines remain
unchanged. On slower native hosts, select `construction_prerequisite` together
with one of `cli_transition`, `commands_and_exact_rerun` or
`independent_installed_retained_recovery_owners` per bounded invocation.
`test_client_recovery.py` contains separate real-trust/physical-proof behavioral
and fault controls, using explicitly labelled unit health stubs rather than
claiming native proof. Split large behavioral selections into disjoint bounded
groups when needed; an earlier timeout remains a failure, not a waived gate.

`libs/versioned-runtime/tests/test_receipt_header_publication.py` exercises
unpublished empty/partial/synced header attempts, abrupt producer death, a
concurrent foreign final-name winner, identity-checked append reopening and the
pre-effect callback boundary. `fleet-manager/tests/test_client_first_publication.py`
checks that initial failures leave no pending owner and that an interrupted
later header cannot poison a previously pending selector retry. Existing strict
receipt/parser and pending-withdrawal suites still cover malformed legacy proof;
the new constructor must not repair it.

## Unit-only promotion packet

`tools/promotion_unit_tests/test_gh_account_prerequisite.py` exercises the
canonical Machines account-prerequisite evaluator with synthetic CLI responses
and a substituted owned-process boundary. It checks explicit stored/environment
sources, healthy inactive accounts, malformed/ambiguous/unhealthy evidence,
safe error projection, `--no-refresh`, execution/output bounds and preservation
of unconfirmed native custody. The exact nodes, source and production coverage
target are admitted by `tools/promotion-unit-manifest.json`.
The separate Machines `test_resource_gh_account.py` covers actual manifest,
authority, resource, restore and JSON integration. Its native companion runs
the real CLI against an empty isolated configuration and contained synthetic
CLIs for argument fidelity, deadline and output limits. These development
witnesses require no live account or profile and do not expand required CI.
They are not sign-in, policy application, two-machine or installed-updater
qualification.

`tools/promotion_unit_tests/test_resume_cli_contract.py` exercises the real
Bridge resume/version declarations, target dispatch and HTTP client against
retained prior parser declarations and synthetic current/prior daemon responses.
Only external transport, discovery,
server/logging plumbing and unrelated entrypoint glue are substituted.
Exact-target cases cover parser refusal before discovery, session/singleton
collisions, ACP/CLI identity and full response preservation, hosting/protocol
fences, unconfirmed responses and no effectful replay. Positional routing and
plain version output retain their compatibility controls; executing-CLI
disclosure is independent of daemon availability. The manifest positively
admits these exact nodes, source closure and production coverage targets.
This is in-process contract evidence, not installed CLI, native conversation,
process-exit, transport availability or deployment qualification.

`tools/promotion_unit_tests/test_namespace_contract.py` joins the actual
Containers `namespace-list` CLI and `ContainerResolver.list_specs` output to
Bridge's `CliNamespaceResolver._enumerate_once` and `NamespaceAgentInfo`.
Synthetic inventory/configuration/lease/hold inputs and substituted thread and
process transport keep both production endpoints in the same no-actor unit
execution; unrelated command integrations are not exercised. The cases protect
field fidelity, ordered multi-agent and empty rosters, repository fallbacks,
ready/stopped/unknown/draining states, and visible refusal of malformed,
partially invalid or schema-drifted JSON and failed provider enumeration.
The actual CLI must emit no success JSON on failure; Bridge must neither cache
nor accept that result as an empty roster. These exact nodes and their source
closure are positively admitted by `tools/promotion-unit-manifest.json`.
This does not qualify native process transport, task/join ownership, Docker,
installed runtime compatibility or either provider's other operations.

Complete-source units in `test_complete_source.py`,
`test_complete_source_publication.py` and `test_source_objects.py` under
`tools/promotion_unit_tests/` use synthetic blobs, Git protocol responses and
release metadata. They prove full-tree reconstruction, immutable generation
bindings, fresh exact-subject admission and publication refusal; no Git
storage, network or child actor is used. Separate controller contract tests
inspect the proposal's prepare/attest/publish/PR ordering. They are not packet
inputs: independently owned Main workflows need not match a Dev proposal.

The separate `tools/test_complete_source_native.py` witness is opt-in:
set `COPILOT_EXTENSIONS_SOURCE_ARCHIVE_INTEGRATION=1` and run that file through
bounded development validation. It creates a fresh temporary Git repository
with export attributes, executable and symlink blobs, then verifies the
actual object's complete reconstruction. It is not enrolled in required CI
or the unit-only promotion packet, and does not prove live release publication
or Main activation.

The three retained-input cardinality/remapping controls live in
`tools/promotion_unit_tests/test_installer_retention.py` and are shared with
the on-demand dependency-policy suite. They exercise the real retention
transaction with synthetic files inside the packet's owned temporary root;
only UV settings/executable selection and native privacy/path adapters are
substituted. They prove one comparison per overlapping file pair, remapping
work independent of metadata scalar count, and preservation of nested native,
URI and escaped paths. Native privacy, installer subprocesses and real package
construction remain separate development-suite controls.

The exact-profile wheel cases in
`tools/promotion_unit_tests/test_runtime_profile_wheels.py` inspect complete
synthetic ZIP/RECORD bytes in memory, including executable-looking payloads
that must never execute. They cover actual metadata/digest disagreement,
closed-set refusal, inclusive closure-wide byte budgets, malformed archives,
mutation during inspection and preservation of first-party generator checks.
These units qualify the repository-only byte verifier, not an approved feed,
real CPU/accelerator closure, installed constructor or production reuse receipt.

Portable preservation units substitute synthetic file reads/replaces for the
Windows native shared-handle adapter. The registry packet runs its original
FIFO refusal assertions with a synthetic FIFO stat result instead of creating
a platform-specific FIFO. Original library native tests remain unchanged and
outside this packet; neither native rename sharing nor FIFO creation is
qualified by these unit adapters. The effect observer and zero-skip rule remain
unchanged on Windows and POSIX.

`tools/test_runtime_profile_bootstrap.py` separately runs a real fresh caller
with `-I -S` and an import guard denying `packaging`. The builder's public
filename parser and first-party wheel inspector must both work without that
caller dependency. Runtime PEP 440 validation still uses the runtime tooling's
declared `packaging`; no package is installed by the parser. Run this native
child check under the bounded development supervisor, not the no-actor packet.

The reviewed [promotion burndown](efforts/active/dev-branch-release-pipeline/promotion-burndown.md)
supersedes the historical full-suite promotion requirement below. Source CI
and the independently governed Main control consume the unit-profile contract.
A local packet result alone does not qualify Main or prove candidate reuse.

Source CI resolves impact and, when selected, executes this packet within one
hosted job, parallel with source guards. Narrative-only PRs do not install test
tooling or run units. Pushes leave release-unit execution to promotion and
omit the unit job before runner allocation or checkout; the independent source
guards still run. Manual CI and unknown event types allocate the unit job and
select units conservatively. The required
aggregate checks both job results and the real unit step outcome, so a skipped
selected step or an absent/invalid selection fails rather than becoming a
success-shaped result.

The report-only marketplace-isolation inventory remains in push/manual source
CI. Omitting its duplicate PR execution removes no test or strict policy gate.

The checks job uses a separate conservative pinned-diff selector for the
tracking-write, headless-launch and asyncio-timeout AST scans. Known narrative
PRs and changes limited to direct Python files in `tools/promotion_unit_tests/`
plus `tools/promotion-unit-manifest.json` omit these scans: none of the three
scanners consumes those inputs. This does not omit the unit packet. Other
paths, nested/lookalike packet paths, production, scanner/selector/workflow
source, allowlists and mixed changes retain the complete scans. Empty evidence,
pushes, manual and unknown events also run them. Diff/encoding/admission errors
fail the checks job instead of emitting a skipped-success selection. All other
guards, including the ungated workflow-policy guard, remain unchanged.

Selected AST work runs through `tools/run-source-ast-guards.py`: three fixed
workers invoke the original full tracking-write, headless-launch and
asyncio-timeout scanners, each with a 300-second deadline. The runner joins
every scanner and emits its complete labelled output in fixed order; a failed
exit, missing interpreter or timeout fails the step. The six-minute step and
ten-minute job budgets remain bounded, with no additional runner job or cache.
Native subprocess regressions live in `tools/test_source_ast_guards.py`; they
prove real three-process overlap and joined failure diagnostics rather than
admitting process effects into the unit-only release packet.

The asyncio scanner still reads and parses every selected source file. Each
mode enumerates matching calls once in breadth-first order and builds a parent
map only for files containing those calls. This changes no file selection,
handler classification, diagnostic ordering or read/path-error behavior.
`tools/test_check_asyncio_timeout_handlers.py` covers both verification and
listing, including no-call files and invalid inputs.

`tools/run-promotion-unit-tests.py` executes only the exact nodes declared by
`tools/promotion-unit-manifest.json`, in one Python process with coverage.
The packet lives in `tools/promotion_unit_tests/`, independent of mixed
integration test modules and their conftests. The manifest binds source and
test bytes, the expected parameter nodes, production coverage targets and a
whole-packet budget. Any missing, duplicate, unexpected, failed or skipped node
or phase rejects the result. Each admitted file is collected once; an exact
node whitelist deselects other cases before execution and restores manifest
order. Missing or duplicate admitted nodes reject collection before execution.
The runner rejects process, network, native-loader,
Git-storage, thread and sleep effects, including attempted effects caught by a
test. Test writes are confined to synthetic temporary storage.

Release-helper qualification additionally resolves the canonical repository
import closure from controller-discovered commands and registered executable
build inputs. Candidate verification includes both the preserved Main control
and its development recipe. Every release-tool decision module must be enrolled
and have positive test-call hits inside executable decision-function bodies;
hash binding, module initialization or import-only coverage cannot substitute.
The native C source
is an explicitly admitted data input, including source-bound materialized
copies, never a selected unit or Python coverage target.
`test_release_helper_contract.py`, `test_release_helper_decisions.py` and
`test_release_build_admission.py` under `tools/promotion_unit_tests/` exercise
the admission, reuse, materialization, resource and native verification rules.
Compiler, Git transport and setuptools superclass actors are substituted only
at their boundaries. These tests do not qualify real compiler/native execution.

`tools/test_release_controller_contract.py` uses the recorded development
proposal for source-contract tests, even inside generated Main with an
independently preserved controller. Its explicit-path tests never substitute
that source recipe for the supplied Main control. It checks
immutable Main/bootstrap and admitted-source/helper selection, the existing
signer qualification dependency, required CLI inputs and complete-source
producer/attestor/publisher ordering. The PR-time compatibility guard reports
live Main separately from prepared compatibility; a source-only pass does not
activate independently owned Main controls. Warning-only migration applies
only to the explicit legacy pinned-helper controller. An already source-coherent
Main that lacks a required input fails before a source merge.
Invocation regressions isolate repeated calls and exclude inputs mentioned only
by comments or unrelated commands; the current indirect promoter is tested with
its own base argument array, not the composition step's arguments.
The command-line compatibility regression also supplies a helper used only by
Main, proving that active Main is checked against its own implementation
inventory. File-loader regressions retain imported-alias provenance through
lexical scopes and shadowing; a renamed dynamic import is not qualification.
File-loaded decision bodies outside `tools/` also require positive call
coverage, including bodies visited before their dynamic edge is discovered.
The native build `exec` exception admits only its exact source-bound load site
and declared input bindings; an additional executable load is not exempt.
Captured helper calls are refused rather than silently borrowing a later
invocation's arguments. Literal helper-path assignments and the supported
runpy heredoc trampoline remain distinct supported forms.

Use the bounded test supervisor around:

```text
python tools/run-promotion-unit-tests.py --source-sha <full-source-commit> --output-dir <new-evidence-directory>
```

The command requires the existing `pytest`, `coverage>=7.7`, `packaging` and
`PyYAML` test dependencies, matching the source CI/promotion jobs.
On Windows, pytest's existing Colorama console adapter is initialized before
unit observation starts. Unit-side native-loader attempts remain rejected;
`tools/test_promotion_unit_windows_io.py` exercises that distinction in a fresh
native process outside the admitted unit packet.

`tools/test_promotion_version_allocation.py` exercises actual Git composition
from landed Main: unlanded aliases do not change versions, dependency/checkout
changes retain distinct source content, exact retries preserve the candidate,
and a newly landed Main version advances the next changed plugin normally.
Its Chat cases also compose first-release and Main-seeded standalone versions
through the real promoter, retain every enrolled Chat Python input byte, and
prove that an actual candidate code mutation still fails the unchanged
equivalence verifier. Source-removed Chat wheel tests separately exercise both
the source version and a different promoted metadata version on native hosts.
It also proves a malformed unrelated plugin-version alias cannot block source
composition; current-source tag and artifact validation remain independent.
Run this existing development cohort under the bounded supervisor, not
unit-only promotion. The retired reservation-selector units are not admitted
after removal of that selector; all other unit identities and bounds remain.
Open-flag cases use semantic node IDs rather than platform-dependent constant
values; the descriptor mock preserves an opaque extra bit without requiring a
POSIX-only flag on Windows.
Their interpreter environment must live outside the checkout: dependency
sources inside a repository-local venv are correctly treated as unadmitted
local imports. The runner scopes pytest collection to the declared source root
as well as selecting exact nodes; unrelated ancestor directories are not
collection inputs, including on mounted filesystems.
It emits a profile-bound `unit-proof.json` and contextual `coverage.json` from
that same execution. It neither starts general plugin suites nor widens
selection on unknown inputs. Enrollment changes require review of the exact
test/import/fixture closure and refreshed declared hashes; tier labels and
test names alone do not establish eligibility. Real-Git, installer, native,
multi-process and end-to-end cases remain source-preserved for periodic
containerized validation, not fallback promotion tests.

Coverage preserves file/line/context attribution directly from coverage.py.
Call contexts start at the actual Python test-function frame and end when it
returns; observer/reporting bookkeeping cannot keep a completed call active.
Redundant per-function/class expansions are intentionally omitted, not unit
outcomes or line evidence. Packet verification retains the sixteen-MiB per-file
bound and requires every admitted unit to contribute production call coverage.
The runner applies `promotion_unit_packet.py` to its actual three-file output
before returning success. An in-process adversarial collector fixture proves
that a passing assertion without production calls is refused by both paths;
synthetic packet fixtures alone are not collector acceptance.

The explicit `source-units-v2` candidate receipt reuses this execution only
after immutable Dev/candidate byte equivalence and independent provider
run/attempt/job/upload/archive checks. Main's metadata-only CI supplies no test
proof. Historical `legacy-ci-v1` verification remains separate and is never an
implicit fallback for missing unit evidence.

`tools/promotion_unit_tests/test_legacy_version_evidence.py` independently
protects that historical raw-source boundary: stamped version-only changes,
code or newline drift, relabeled hashes and post-test source rewrites remain
refusals. Identical source bytes remain reusable. These unit controls do not
claim that raw-Dev execution qualifies a transformed candidate; candidate-first
execution and attributable coverage retain their separately owned implementation.

For the composed-candidate controller proposal, invoke the runner from the
imported frozen candidate, adding `--development-root <immutable-Dev-checkout>`
and `--candidate-tree <full-frozen-tree>`. These arguments are a pair. The
runner validates the original Dev inventory, derives the candidate-specific
manifest without adding test nodes, and observes the composed import closure.
Declared uv vendor copies are admitted only for already-admitted origins and
must match their composed canonical bytes.

Candidate packets retain the same three filenames but use
`copilot-extensions.promotion-candidate-units/v1` and require explicit
`candidate-units-v3` qualification. `coverage.json` preserves raw candidate
`files` and adds `development_mapping` plus `development_projection`. The
packet verifier recomputes both from authoritative Dev and candidate bytes;
self-declared hashes or path relabeling do not qualify them. Version-stamped
statements and unsupported/generated regions remain explicitly unattributed.
The projection retains per-copy hits before combining mapped Dev coordinates
and never claims branch/function coverage or raw-Dev execution.

Focused regression files are `test_candidate_units.py`,
`test_frozen_candidate.py`, `test_development_map.py` and the v3 cases in
`test_qualification.py` under `tools/promotion_unit_tests`. They use synthetic
bytes and mocked Git/provider boundaries. `test_candidate_workflow.py` separately
checks the real controller proposal's dependencies and explicit profiles; it is
not a second runtime test gate. Actual composition/transport exercises belong
to bounded development validation, not the required in-process unit packet.

Prepared-controller rehearsals in `tools/test_promotion_merge_wait.py` and
`tools/test_release_publication_boundary.py` select the declared v2 or v3 unit
profile and dependency graph explicitly. Stage the controller together with its
matching `ci.yml` and admission workflow when testing a historical snapshot.
The existing bounded process-tree supervisor contains those local rehearsals;
they do not become additional promotion or required-PR test dependencies.

The coupled source-checkout, artifact-validator and readiness-clock regressions
also follow the frozen path. Git-based Windows development fixtures need a short
private scratch root and explicit short pytest base directory; deeply nesting
their synthetic Git stores below a worktree can exceed Git's path limit.

Source PR CI has a separate exact-input reuse boundary in
`tools/ci_pr_unit_reuse.py`. It binds the current source/merge/base, workflow,
manifest/source closure, hosted image, Python identity, actual installed
distribution file bytes and sealed child environment before looking up prior
proof. A fresh PR child gets only the declared noncredential environment plus
fixed Python isolation settings; GitHub event IDs, credentials and unrelated
ambient variables are not unit inputs. Toolchain binding is bounded, and the
read-only provider search has an eight-second total budget.

An eligible prior run must have a successful exact-attempt unit job and fresh
upload, the matching context-derived artifact identity, immutable archive
bytes and the original canonical three-file packet. Every phase, source hash,
effect refusal and actual production call-coverage requirement still applies.
The aggregate records `executed` versus `reused` with the original run/attempt
and packet digest; a reused packet is not uploaded again as a fresh producer.
Producing-run/attempt artifact names avoid retry collisions. The aggregate
preserves GitHub's successful-unit-job retention on failed-job-only retries
of the same immutable run, without accepting future-attempt evidence.
Unknown or incompatible evidence retains fresh execution. The provider
inventory includes failures, pending runs and retry revisions in a complete
bounded qualification epoch. A later retry of a lower-ID run changes that
epoch rather than hiding behind ID ordering; incomplete history cannot reuse.
Reruns that invoke the helper always execute fresh units, and PR checkout
validation remains mandatory when toolchain binding is unavailable. The pure synthetic
tests in `tools/promotion_unit_tests/test_ci_pr_unit_reuse.py` exercise provider,
attempt, archive, coverage, dependency-byte and environment refusals without
network, Git or child actors. Real CLI/provider execution is complementary
integration evidence, not an admitted unit effect. This does not alter the
release packet's format or independent execution for every Main release.

The first runtime-regression cohort in
`tools/promotion_unit_tests/test_runtime_regressions.py` exercises actual
project-routing, mux-result, anchor-guard and parent/sub-agent turn decisions.
Its closed production imports and relevant production calls are admitted
explicitly in the same manifest; it is not a generic plugin-suite invocation.
Synthetic repository-state evidence does not create or access Git storage.
Real front-door, missing-socket, installed-hook, SDK-forwarding and JavaScript
delivery counterparts retain their existing periodic ownership. No Python
classifier assertion is treated as proof of a native or shell boundary.

`tools/promotion_unit_tests/test_peer_environment.py` re-exports all six exact
functions from `libs/peer-launch/tests/test_peer_environment.py`. The original
module already loads the canonical flat `libs/peer-launch/peer_launch.py`;
the adapter does not preload production, rewrite imports or copy assertions.
Replacing only the original file prefix maps each unit to its packet identity.
All three files are hash-bound, and each unit must contribute actual
`peer_environment` call coverage under the unchanged observer and budget.
The eleven separate packaging tests remain unqualified; this cohort does not
prove native peer launch, vendor synchronization or the complete library census.

The shared lazy-dispatch cohort re-exports the three original classes from
`libs/lazy-cli-dispatch/tests/test_dispatch.py` through
`tools/promotion_unit_tests/test_lazy_dispatch.py`. Its fourteen class/method
suffixes map one-to-one to the original identities; no assertion body is copied
or moved. The adapter loads the canonical library source and original test
module directly, without its path-only conftest or an installed library.
All three files are hash-bound in the same manifest and every aliased case must
contribute actual library call coverage under the unchanged effect observer.
This bounded mapping does not establish the unfinished full-library census.

`tools/promotion_unit_tests/test_fleet_contracts.py` re-exports the exact
functions and parameterization from both original `libs/fleet-contracts/tests/`
modules. Replacing either original module's file prefix with the adapter path
maps each case to its release identity; their disjoint function names make the
mapping injective, and a duplicate name fails collection. The original tests,
canonical package initializer and both production modules remain hash-bound.
The path-only conftest is not executed and no assertion body, import, effect
rule or collection-root restriction is rewritten. Every admitted case must
contribute actual library call coverage under the same observer and budget.
This qualifies that bounded library cohort, not the whole historical census.

`tools/promotion_unit_tests/test_worktree_identity.py` re-exports the eight
original codec test functions and their unchanged parameterization from
`libs/worktree-identity/tests/test_worktree_identity.py`. Replacing only the
original module prefix maps its 72 cases injectively to release identities.
The original tests, canonical initializer and adapter are hash-bound; every
case must contribute actual codec call coverage under the unchanged observer,
root and budget. No original assertion body or production behavior is copied
or modified. This bounded mapping does not complete the historical unit census.

`tools/promotion_unit_tests/test_config_migrate.py` re-exports the original
config-migration functions and unchanged parameterization from
`libs/config-migrate/tests/test_config_migrate.py`. Replacing only the file
prefix maps all 29 original cases to their release identities. Its synthetic
temporary-file cases retain the actual atomic rewrite, backup, interruption
and idempotency assertions. The original tests and all four canonical
production modules are hash-bound, without executing their path-only conftest
or changing the observer, collection root or packet budget. Each case must
contribute real production call coverage. This is one bounded library mapping,
not complete historical-census or native-platform qualification.

`tools/promotion_unit_tests/test_dropin_registry.py` re-exports all nineteen
original functions from `libs/dropin-registry/tests/test_dropin_registry.py`.
Replacing only the original file prefix maps their identities to release nodes.
The original test and all four canonical package modules remain hash-bound;
every case must contribute actual production call coverage under the unchanged
observer, root and budget. Temporary-file, link and FIFO assertions execute on
the Linux release host without skips. The original platform availability rules
remain intact: a missing capability cannot become positive packet evidence.
This bounded mapping does not qualify the complete historical or Windows census.

`tools/promotion_unit_tests/test_plugin_resolve.py` re-exports the 29 original
local-resolution functions and their unchanged parameterization from
`libs/plugin-resolve/tests/test_plugin_resolve.py`, mapping 33 cases by replacing
only the file prefix. The original namespace-import regression starts a real
subprocess and remains outside this cohort, alongside the separate preparation
and execution suites. The original module and all five imported package modules
are hash-bound; every selected case must contribute actual production call
coverage under the unchanged observer, collection root and budget. No original
body or production behavior is copied or modified. Linux qualification includes
the original symlink-escape assertion. This is a bounded mapping, not a completed
historical or native-platform census.

`tools/promotion_unit_tests/test_docs_consistency.py` re-exports all 29 original
functions from `tools/test_check_docs_consistency.py`, replacing only the file
prefix for release identities. The original test and production guard remain
hash-bound; every case must reach an actual production call context under the
unchanged observer, root and budget. Paths, exact casing, headings, reference/
HTML links, file-versus-directory and real symlink-escape assertions execute
against synthetic temporary trees. No Markdown parser, count regex, original
test body or platform rule changes. This Linux mapping does not qualify the
whole historical census, native Windows or the separate metadata/Git portfolio.

`tools/promotion_unit_tests/test_activation_preservation.py` re-exports all
original functions and unchanged parameterization from
`libs/plugin-activation/tests/test_preservation.py`, mapping its 29 cases by
replacing only the file prefix. The adapter loads the canonical activation,
single-instance lease, drop-in registry and resolution packages directly,
without running path-only conftests. The exact imported source closure and
original test remain hash-bound; each case must contribute actual production
call coverage under the unchanged observer, root and budget. Synthetic original
snapshots, refused transactions, restoration, injected contention and real
symlink assertions execute on Linux without changing production or test bodies.
This qualifies no command/descendant process settlement, native Windows,
other activation test module or complete historical census.

The activation package's remote-identity compatibility export imports the
canonical pure-stdlib parser from `plugin_resolve.remote_identity`; both source
files are hash-bound in that existing import closure. This adds no unit nodes,
coverage targets, effects or budget, and does not enroll SSH dispatch tests.

`tools/promotion_unit_tests/test_remote_login_shell.py` re-exports five original
functions and their unchanged parameterization from
`libs/remote-login-shell/tests/test_remote_login_shell.py`. Replacing only the
file prefix maps thirteen callable cases to release identities. The original
test, canonical production module and adapter are hash-bound; each admitted
case must contribute actual production call coverage under the unchanged
observer, root and budget. No shell process is launched. The fourteenth original
case, `test_posix_login_shells_vocabulary`, asserts only a constant and has no
production call context. It remains an explicit unmapped policy residual, not
deleted coverage, a relaxed admission rule or a completed historical census.

## Tracking-write source guard

The source tracking-write guard still parses every sibling-plugin Python file,
including unchanged files, and fails closed on read, encoding and syntax errors.
It reuses one per-file breadth-first AST enumeration and assignment list across
its existing alias fixed-point passes. This is traversal reuse, not a changed-path
filter or a persistent cache; the denylist, reflection checks and diagnostic
ordering remain unchanged. The focused regression entrypoint is
`python -m pytest -q tools/test_check_no_sibling_tracking_writes.py`, run through
the bounded test supervisor.

## Copilot Code Review setup staging

The dedicated `copilot-code-review.yml` setup stays on literal `ubuntu-latest`
until an explicit isolated-capacity activation. Its only job is
`copilot-setup-steps`, with no setup token permissions, repository checkout or
dependency install. Copilot performs its checkout after setup. Manual dispatch
can exercise the setup once the definition is on the default branch; it is not
proof that an official agentic review completed or that an ARC worker is ready.
The five-minute timeout belongs only to the setup command step, not the job.
No job-level timeout overrides the official review's execution budget.

`tools/check-trusted-ci.py` and `tools/test_check_trusted_ci.py` retain the
existing validator authorization and global custom-route restrictions. The
dedicated review contract accepts only the exact minimal setup with literal
`ubuntu-latest` or `copilot-extensions-review`; a different file, job, label,
permission, trigger, extra step or job-timeout override is rejected. The setup
and prepared-control fixture modules keep the cases independent of production
expectation constants, and the existing required guard test entry point runs
them without adding a CI job.

Prepared-control fixtures exercise the real inline admission body and unchanged
preservation helper with old and four-workflow allowlists, including rejected
candidate self-authorization, foreign sources, missing/unsafe blobs, noncanonical
metadata and unrelated paths. They do not activate a Main policy. Before any
Main bootstrap, separately run the exact old Main guard/tests with only the
proposed workflow overlay; passing Dev tests is not that compatibility proof.
A future ARC activation additionally needs the compatible guard to reach the
default branch through qualified delivery, plus operator-owned network and
ephemeral-worker qualification.
Do not use a readiness expression or a hosted-label alias to select private
capacity. Local pytest commands remain wrapped by the bounded test supervisor.

## Trusted metadata event partition

The focused metadata entrypoint covers actual source routing for
Dev synchronize, closed/retarget-away, body/label edits and unrelated bases:

```text
python -m pytest -q tools/test_required_source_attribution_workflow.py
node --test .github/actions/required-pr-attribution.test.cjs tools/test_copilot_review_gate.cjs
```

The metadata wiring test checks the current unit-only CI profile through the
canonical trusted-CI validator, including its ungated policy check, admitted
unit execution and required aggregate. It does not require the retired
dispatched-snapshot or exhaustive metadata-test steps to return to source CI.
The Python/Git and JavaScript metadata suites above remain outside the admitted
release-unit packet; their periodic enrollment is still an open portfolio
obligation, not evidence supplied by a passing required PR check.

The 72 event/base cases, job-boundary checks and real-Git admission shapes reuse
the existing bounded fixtures, with no extra CI job. Source declarations come
from the checkout under test; the separate admission body comes from immutable
`HEAD` or an explicitly selected `METADATA_PREFLIGHT_SHA`. A selected available
full Main commit qualifies that control's admission shape, not live routing or
promotion. See [required source metadata](docs/pipelines.md#required-dev-source-metadata)
for trusted-default activation and measurement boundaries.

## Bridge protocol validation and startup performance

Protocol correctness and startup speed have separate on-demand flows. Run the
native transfer and import-boundary contracts normally:

```powershell
python tools\run-plugin-tests.py agent-bridge --test-file tests\test_runtime_transfer.py --test-file tests\test_startup_imports.py
```

The native fixture permits 15 seconds for cold bootstrap, before its existing
shared post-connect protocol deadlines begin. That module has a 60-second item
watchdog; the 300-second group limit is unchanged. The child still shares its
original ten-second protocol deadline, and the deliberate 100 ms refusal test
still requires that deadline to expire. Import/export correctness has a separate
15-second child watchdog, not a five-second speed assertion. A bootstrap or
cleanup timeout fails; the fixture closes its listener and reaps its owned
process rather than leaving cleanup to a later test.
The runner gives the native runtime/transfer family and both startup files
independent sequential groups. This retains every selected test and each
unchanged group watchdog, avoids cross-module fixture/import state in the
ordinary native flow, and preserves complete failure diagnostics instead of
losing them when a mixed-process batch exhausts its aggregate deadline.
Explicit filtered collection keeps the runner's existing selection semantics.

The performance flow starts five fresh interpreters through the real
`python -m agent_bridge start` CLI, with fresh empty databases, isolated config,
no peers, a disabled credential relay and dynamically reserved loopback ports.
It verifies the published process identity and observes real `/health`
responses through full HTTP readiness. It does not import pytest fixtures in
the measured child or replace readiness with an import-only proxy. Windows
uses the actual executing interpreter rather than measuring a venv redirector;
interpreter spawn is included, launcher overhead is not. OS filesystem caches
are not purged.

```powershell
$env:AGENT_BRIDGE_STARTUP_PERF = 'report'
try {
    python tools\run-plugin-tests.py agent-bridge --test-file tests\test_startup_performance.py
} finally {
    Remove-Item Env:AGENT_BRIDGE_STARTUP_PERF
}
```

`report` is explicitly measurement-only: it records `baseline: not-established`,
`budget_gate_enabled: false` and whether the provisional target was met. A
successful measurement is **not** performance qualification. Every sample is
retained; no failed or slow sample is retried or dropped. `BRIDGE_STARTUP_PERF`
JSON lines contain the complete timings, Python/platform identity, source
fingerprint, observation-conflict count and final median/maximum. Recognized
Windows publication sharing conflicts remain inside the same wall-clock sample
and are counted; corrupt routes and other errors fail immediately. Incomplete
samples, startup errors and unsuccessful cleanup fail even in report mode.

For a separate explicit budget check, use `AGENT_BRIDGE_STARTUP_PERF=qualify`
with the same command. Its provisional thresholds are a five-second median and
15-second maximum across all five samples. Neither mode changes these
thresholds. Both retain a 30-second startup watchdog per sample, a 210-second
benchmark-item watchdog and the runner's unchanged group/process containment.
Startup measurement stops at readiness; shutdown is outside that measurement
and must succeed separately within its own bounded request and exit waits.

Establish a reviewed blocking baseline on controlled capacity before making
this a publication or promotion gate. A local `qualify` result alone does not
prove controlled capacity or establish that baseline. The performance case is
skipped unless explicitly selected by its environment variable; its budget and
observer unit tests run normally. No required-PR job, matrix or release gate is
added for this periodic/manual benchmark.

## Retained installer guardian: native Windows opt-in

The retained-child desktop regression requires a quiescent interactive Windows
desktop and an installed real `pythonw.exe`. It is outside the fast required lane:

```powershell
$env:COPILOT_EXTENSIONS_NATIVE_WINDOWS = '1'
try {
    python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests\test_installer_guardian_windowless.py
} finally {
    Remove-Item Env:COPILOT_EXTENSIONS_NATIVE_WINDOWS
}
```

It observes the actual windowless parent image, two console-child/descendant
cycles, visible and Default Terminal windows, and foreground transitions. Its
timeout and owner-cancellation cases require pinned process handles to signal
exit and canonical cleanup custody to become available. Breakaway remains
refused; no resident service is installed or escaped from test containment.

The existing `test_python_installer_engine.py` teardown-fault regression injects
its first accounting failure inside the guardian, not through parent-only
monkeypatches. It checks frozen inputs, construction/retention exclusion,
descendant settlement and refusal to publish after the recovered teardown.
`test_installer_guardian.py` also launches from a real owning interpreter beyond
Windows' executable-token `MAX_PATH` limit.

Normal Fleet preparation retains its canonical runtime and both process-build
companions. `tools/test_run_standalone_tests.py` exercises that actual preparation
entry point while changing the original resources, then verifies the retained
wheel's exact bytes. The full development extra remains broader than the
dependency-free client; selected source distributions can still require a
platform's native build prerequisites.

## The turn-key runner

Agent MCP's focused home-binding lane is:

```text
python tools/run-plugin-tests.py agent-mcp --test-file tests/test_home.py --test-file tests/test_home_binding.py --test-file tests/test_serve_cwd.py
```

Run it through the bounded supervisor. Fresh child processes use only temporary
profiles and real local daemon listeners, leases and routing publication.
They verify missing, empty, relative and absolute homes across the daemon's
cwd change, named configs/overlays, storage, digest keys and token caches,
with no loose artifacts in the launch directory. POSIX cases additionally
exercise symlink-plus-`..` home overrides and explicit socket paths through real
daemon publication and artifact writes. Existing `test_serve_cwd.py`
retains explicit relative-socket and passive-refusal controls. These native
cases stay outside required unit CI; the packet admits only the dependency-free
home selector's synthetic no-actor controls.

The read-only repository hosting-policy resolver has the explicit
`repository-policy` target, backed by `libs/repository-policy/tests`. Run
`tools/run_tests_in_devcontainer.py repository-policy` through the bounded
supervisor for canonical/legacy/marketplace parsing, negative-policy precedence,
fresh checkout selection and actionable refusal controls. This target neither
launches agents nor changes adoption state. Consumer entry-point and receiving
boundary tests remain with their owning plugins; passing the resolver target
alone is not proof that every managed launch is covered.

The dependency-free worktree identity codec has the explicit
`worktree-identity` target, backed by `libs/worktree-identity/tests`. Run it
through `tools/run_tests_in_devcontainer.py worktree-identity` with the same
bounded supervisor used for plugin suites. Worktrees' compatibility and
consumer tests remain in its own target; repository packaging contracts live
in `tools/test_worktree_identity_packaging.py`. These periodic/source checks
do not enroll new tests in the unit-only promotion packet.

The local runner measures each plugin from dependency preparation through final
sandbox removal. Its aggregate timeout cannot turn a slow cleanup into a pass.
After containment verifies that a group's processes are gone, one cleanup worker
removes that group's private pytest directory while the next group runs. Shared
environment roots and immutable fixture seeds outside those directories remain
available until the plugin finishes. Every queued removal is joined, including
failure and cancellation paths; cleanup failures are explicit. Unverified
process teardown retains the current sandbox, even if an earlier group's
cleanup also failed. The process, storage and per-item/group limits are unchanged.
These focused runner contracts do not qualify a complete suite's timing budget.

Bridge's generated Windows management and payload launchers have a focused
binary/prompt smoke in `tests/test_payload_invocation.py`. The full native
matrix is explicit T3, preserving the default fast portfolio:

```text
python tools/run-plugin-tests.py agent-bridge --allow-explicit-tiers --test-file tests/test_payload_invocation.py --test-file tests/test_carrier_stdio.py
```

It compares actual generated direct-PS1 and retained CMD bodies on PowerShell 7
and Windows PowerShell 5.1, with real selected-runtime resolution, arbitrary
binary frame boundaries, held-open stdin, clean EOF, separated streams and exit
codes. The staged runtime also executes the real carrier protocol. A strict
caller-owned Windows Job proves cancellation retires a pinned live descendant.
Fixtures never install into the operator's runtime or contact a live peer.
The Windows PowerShell 5.1 native prompt case uses its encoded command boundary:
that host rejects the standalone `-` argument before executing a `-File` script.
Only the fixture-owned command/arguments are encoded; the quoted multiline
prompt remains raw stdin. Framed carrier cases still exercise `-File` directly.

Bridge's `tests/test_worktree_owner_launch.py` separately checks the owner
trampoline's exact Windows argv, sanitized environment, inherited stream objects,
no-window flags and exit status. Its native pipe cases deploy the real resolver
into a temporary profile, construct a private versioned peer and run the actual
isolated trampoline with an empty PATH. They require Unicode/spaced stdin and
arguments, separate stdout/stderr, scrubbed caller routing and exact exits 0/7.
Select that file with the same contained runner; existing native deadlines and
platform skips remain unchanged, with no new required-unit CI enrollment.

The source and candidate Linux full-suite declarations use exactly two
work-stealing pytest workers for Worktrees only. Both workers remain inside the
same runner-owned containment boundary and unchanged 30-second item,
300-second group, 900-second plugin, process, memory and temporary-storage
limits. Guard, collection, baseline and unrelated steps do not inherit those
worker options. The local runner remains serial unless explicitly configured;
activation of preserved Main declarations is separately reviewed. These options
do not count as complete budget evidence until the exact full run succeeds.
CI's worker options also require an owning promotion run, so ordinary manual
validation of an older released Main stays serial and does not require a test
dependency that has not shipped there yet.
The initial paired disconnected Linux run executes all 17 groups with the same
11,941 passes and 336 skips: serial 560.951 seconds versus two workers 348.064
seconds, including preparation and cleanup. This is local execution evidence,
not a substitute for the exact hosted release gates or Windows margin proof.

Candidate Bridge validation also opts into exactly two work-stealing workers
inside its existing ordinary Linux job. The owning `promotion_run` is required;
older released Main validation without that input stays serial until the
test-only dependency has shipped. This adds no matrix entry or parallel
pipeline, retains every test and native subprocess boundary, and shares the
same item/group/plugin, process, memory and temporary-storage limits. It does
not increase the preserved Main job's eight-minute budget. Local complete
execution and actual hosted candidate qualification remain separate gates.
Its real relay-probe fixture reserves an ephemeral listener before starting
the probe, and completed-slot harness cleanup looks up its optional function
directly rather than searching unrelated commands or autoloading modules.

The existing artifact-readiness suite also executes the immutable release
materializer against current direct and nested retained-flat source references.
On Dev's legacy controller it reads the actual `origin/main` controller; an
exact generated candidate retains its preserved controller but replays the
unmaterialized manifests and libraries from its recorded exact Dev commit,
not its already-rewritten payload. A real generated Git checkout exercises
that boundary, with altered generated library bytes and refused missing,
invalid or unavailable source identities. Full Git history is
required, as in both existing artifact-contract CI lanes. The fixture loads
only pinned Git helper files in an isolated child, verifies materialized bytes
and rewritten paths, and retains the obsolete-helper refusal as a negative.
It does not build, install, publish or qualify an artifact.

Source-installer smoke contracts cover receipt changes, canonical snapshot
publication and generation selection with small assembled payloads. Logger and
Vault additionally share an explicit full-install lane in Logger's
`tests/test_source_runtime_install.py`: it stamps the complete assembly, makes
the publisher unavailable, provisions and reuses the immutable runtime, then
checks installed commands and failed-candidate preservation in separate bounded
tests sharing one healthy installation. Run each plugin parameter separately
through the bounded supervisor with
`COPILOT_EXTENSIONS_SOURCE_INSTALL_FULL=1` in the runner's environment:

```text
python tools/run-plugin-tests.py agent-logger --allow-explicit-tiers --test-file tests/test_source_runtime_install.py -k "(stamp_provision_reuse or failed_source_candidate) and agent-logger"
python tools/run-plugin-tests.py agent-logger --allow-explicit-tiers --test-file tests/test_source_runtime_install.py -k "(stamp_provision_reuse or failed_source_candidate) and agent-vault"
```

These expensive full-install cases are not unconditional required smoke tests.
They do not start a deployed service or request credentials.

Native tmux pane targeting and seeded New/Resume handoff have a focused lane:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_mux_active_pane.py --test-file tests/test_launch_seed_state.py
```

The native cases use a test-owned foreground tmux server and private socket,
never the user's default server. They exercise both active-pane lookup helpers
and the final cold-handoff guard with real staged-prompt state and a recording
backend, including rejection of a different pane without consuming the prompt.
The companion argv cases preserve psmux target syntax. This module belongs to
the existing fast contract-guard lane, so ordinary PR CI exercises its native
cases on Linux rather than only collecting them. POSIX GitHub Actions refuses
missing tmux instead of silently skipping that proof. Local native cases skip
when POSIX tmux is unavailable; this is not a live Copilot or Windows psmux test.

The Windows status-monitor console regression keeps the at-most-one hidden
terminal host and zero visible-window/focus assertions across eight psmux
probes. Its process census rejects a parent edge only when readable Windows
creation times prove the child predates that numeric parent; unavailable
identities remain conservatively counted. Focused controls cover reused root
and intermediate PIDs, unavailable identities and multiple real terminal
hosts. A native failure records parent/birth/baseline observations and actual
probe-console membership. These diagnostics do not by themselves establish the
cause of the tracked hosted seven-host occurrence (#6266).

Source-bound native lifecycle and restart-evidence contracts use the existing
bounded runner:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_lifecycle_source or test_tracking_session_registration_write or test_tracking_session_deregistration_write or test_native_hosting_evidence or test_restart_observer or test_restart_evidence or test_hook_ipc or test_register_session'
```

The fixtures cover original-source transport, real held-file ownership, source
PID/birth mismatch, delayed historical End versus a replacement self-claim,
source-bound Start through PID-only catalog refresh and matching End cleanup,
timestamp-less Start replay with released claims and duplicate historical End
without title mutation,
same-UUID replacement before or after title input is read,
source-aware legacy End cleanup without fabricated proof and a source-changing
already-linked handoff surviving a stale save,
warm End over real resident IPC with two same-UUID records and an explicit
binding outside cwd,
history-pruning ambiguity including complete source removal, bounded sticky
pruning fences across stale saves and saturation, pre-dispatch old-daemon refusal with a real legacy
peer whose mutation handler stays uncalled, installed-bin helper loading,
the 256-entry retained-history boundary, tombstone-visible closure and
shutdown/cancellation ordering across inferred exits. Closed or definitively
consumed unknown-End unmapped history does not dilute live polling, but a new
live mapping re-admits its subject. Journal compatibility, marker validation,
same-generation persistence and execution-generation reset are covered separately
from inferred-End corroboration.
They do not reboot a machine, kill a
production session, qualify Windows native source capture, or prove acknowledged
restart-seed delivery. Those remain separate native integration obligations.

Default-off recovery configuration and native notification transport use:

```text
python tools/run_tests_in_devcontainer.py worktree-manager -k 'test_manager_config or test_restart_capabilities or test_restart_notification_runtime or test_restart_notification_thread or test_restart_notifications'
```

The disposable transport tests exercise actual pipe-FD ownership, inhibitor-before-
subscription ordering, checkpoint/cancellation/rearm, owner and bus loss, spoofed
signals, immediate SQLite lock refusal and FD cleanup before coroutine pickup.
`test_restart_notifications_dbus.py` additionally uses a private `dbus-daemon`
and negotiated SCM_RIGHTS; it never connects to the host system bus. That native
fixture skips explicitly if the binary is unavailable (including the standard
isolated image), so such a run is not actual D-Bus qualification. A disposable
derivative of the same pinned, hardened image with Debian's `dbus-daemon`
installed can run this fixture without changing the wrapper's filesystem,
credential or network isolation. At production daemon cadence, the private-bus
fixture also delivers a real shutdown signal while ordinary work stays blocked
for 1.25 seconds: checkpoint and inhibitor release must occur within 0.75 seconds,
before the one-second native delay expires. It asserts the notification thread
and its event loop are closed before the actual daemon lease is released.
Thread fixtures prove exclusive adapter ownership, stale-admission refusal,
drain waiting through cleanup, safe undrain and visible startup/I/O/cleanup
failures without busy retry. Default-off/passive paths create no native owner.
Actual adapter pipe-descriptor failures cover both the active inhibitor and
unclaimed reply descriptors, during pump-time and shutdown cleanup. Failure
must survive later adapter state changes and keep runtime drain/close unclean,
even when adapter cleanup records the error without raising.
These contracts do not prove hard real-time OS/filesystem bounds, real host
shutdown/startup coverage, automatic enrollment or acknowledged Resume.

Explicit exact-original restart Resume and read-only outcome composition use:

```bash
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_restart_launch or test_embody or test_bridge_acquisition_launch or test_native_restart_check or test_restart_owner_check or test_restart_launch_inspection or test_restart_capabilities'
```

These contracts cover real concurrent owner processes, owner death after the
native invocation boundary, private/fsynced uncertainty before invocation,
failed outcome publication, corrupt/indirect receipts, changed owner policy,
unknown mux absence, typed staged seeds, exact UUID/full interactive STATUS
FIRST argv and read-only frozen-attempt/outcome binding. The process tests keep
the real locks and durable state; native epoch, process liveness and mux creation
are synthetic. The explicit acknowledgement fixtures exercise actual owner
policy/hosting reduction with synthetic process observations, retained original
activation history and fresh invocation ancestry. Wrong native/ancestry/boot,
head/incarnation changes, missing holds, intentional End, pruned sources,
opaque/other claims and pending prompts must not publish an ACK. A real child
exits after durable ACK publication; an exact repeat must retain the same
receipt without another native invocation. Manager fixtures accept only the
exact typed version-2 acknowledged binding, retaining false restored/automatic
flags and a qualification blocker. They do not launch Copilot, prove prompt
readiness or actual seed consumption, or qualify automatic startup/platform
recovery.
Canonical self-session claim regressions cover the actual native Start
transaction's persisted claim, unchanged owner bytes through exact-original
Resume, wrong machine/project/worktree/UUID, released/producer/opaque/duplicate
or mixed claims, inbound/dispatch/released-ledger fences, and replacement during
preflight. Persisted launch-admission cases clear the writer cache before the
owner reread; opaque dispatch metadata must survive actual YAML serialization.
Concurrent owner processes and post-invocation owner death exercise
both an empty ledger and the original self claim with real locks and durable
uncertainty. The provisional check makes no self-claim provider callback;
task-policy snapshots still cannot authorize a launch.

Exact-original survivor inspection uses:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_restart_survivor or test_restart_survivor_inspection or test_native_restart_launch or test_native_restart_ack_platform'
```

These fixtures distinguish fresh exact-original native hosting from historical
launch/ACK receipts. Stale or missing holds, unknown/dead hosting, wrong
head/path/incarnation/epoch, intentional End, pruned/ambiguous original history,
non-increasing activation/revision, pending seeds and task/inbound/profile
policy cannot confirm a survivor. Current owner/epoch changes during the read
invalidate the snapshot. Real child processes exercise persisted owner reads
and the Manager's engine JSON boundary without changing YAML or frozen SQLite
bytes; process/epoch/mux facts remain synthetic. Ordinary default-off inspection
makes no owner call. No case grants adoption or seed-ACK authority, launches a
native CLI, or qualifies automatic recovery.

Receipt-history fixtures retain the original ACK while launching and
acknowledging a separately frozen next-boot request. They refuse different
requests after either unacknowledged state, changed head/source/activation,
reused UUIDs, boot cycles, malformed history and exhausted counter capacity.
The exact 16-entry limit and aggregate 32-KiB boundary are checked independently,
including a pending receipt that fits but whose maximum valid future ACK would
not. Publication failures before/after the native boundary retain either the
prior acknowledged history or durable uncertainty without retry authority.
Read-only owner/Manager checks retrieve older frozen targets only within the
same platform/environment; current-owner inspection remains boot-strict.
Real owner processes cover concurrent reservation and crashes both before and
after synthetic mux invocation. No fixture qualifies a real native recovery.

Manager-owned durable receipt observation uses:

```text
python tools/run_tests_in_devcontainer.py worktree-manager -k 'test_restart_outcomes or test_restart_attempts or test_restart_launch_inspection or test_restart_owner_check or test_restart_notification_runtime'
```

The lane covers additive version-1 migration, unchanged frozen payload bytes,
typed exact-request outcomes, monotonic state and immutable ACK binding,
orphaned/malformed/oversized storage, write failure and capacity refusal.
At the actual 4096-attempt boundary, failed freezing of a new candidate must
still permit an existing uncertain receipt to advance, without eviction.
Unconfirmed startup evidence still prevents that historical owner call.
Real child processes crash before COMMIT or after publication, both during
migration and later updates; concurrent owner observations retain the newer
receipt without replacement or retry. Active opt-in polling is bounded and
fair, skips historical ACKs and preserves their original target boot.
Unavailable or stale owners cannot erase uncertainty. Default-off, passive and
draining generations make no owner call. A real existing-daemon loop fixture
observes under its lease/mutation fence, and a blocked owner read keeps drain
busy until it returns without persisting after admission closes. Ordinary
inspection reports retained outcomes without owner I/O or schema migration.
These are durable receipt and daemon-lifecycle contracts with synthetic owner
responses, not live native Copilot readiness, seed consumption or automatic
startup qualification.

Positive owner/Manager ACK fixtures use the mux producer's actual `$1:100`
incarnation shape, including the leading dollar sign, rather than a
synthetic token alphabet. Newline and delimiter-bearing tokens remain refused.
`test_native_restart_ack_platform.py` is not POSIX-skipped: unsupported platform,
container or unavailable-epoch capture must return a structured refusal before
configuration, lifecycle/admission locks or Unix-only receipt I/O is reached.

Frozen restart-attempt identities and historical owner bindings use:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_hosting_evidence or test_restart_observer or test_restart_evidence or test_restart_attempts or test_restart_capabilities or test_restart_notification_runtime'
```

Provisional current-worktree checks additionally use
`-k 'test_native_restart_check or test_restart_owner_check'` with those same
two release units. The tests cover exact incarnation/head/lifecycle/activation
binding, pause/seed/task/claim/profile refusals, changed owner observations,
transcript/mux/liveness blockers, target-epoch skew, malformed input and typed
response mismatch. Failed/denied/timed-out mux probes stay unknown, confirmed
empty listings qualify absence, and existing dotted worktree names use their
canonical mux encoding. One actual child reads persisted owner YAML and a separate
Manager/engine process fixture verifies the no-update/no-reconcile environment
and unchanged real attempt storage. Process observations and native epochs are
synthetic; these fixtures do not launch Copilot, qualify action-time task policy,
reserve a recovery launch or prove restart-seed acknowledgement.

Current task-policy snapshots additionally use:

```text
python tools/run_tests_in_devcontainer.py agent-dispatch agent-worktrees -k 'test_native_restart_policy or test_native_restart_check or test_claim_providers or claim_status'
```

This lane covers the opt-in owner API's reduced SQLite snapshot, explicit
writer/native owner/generation/hold/steer/waiter checks, old-coordinator and
missing-field refusal, and unchanged default callback output.
Submitting two steering instructions while `awaiting_steer` and
`resume_requested` are false must block qualification; partial consumption
keeps the blocker, and consuming the final entry restores only a provisional
match. Missing or non-Boolean inbox evidence also refuses.
An actual CLI
child makes only the task-policy HTTP GET; another child reads persisted
worktree YAML and invokes its registered callback process with the exact writer,
opt-in flag and no-self-provision environment. These are disposable protocol
fixtures, not live task acquisition, automatic launch permission or native
restart qualification. Mixed/legacy/released/producer claims remain blocked;
generation snapshots are not reservations.
Persisted-record regressions cover unknown/null/structured claim states and
malformed claim collections through actual owner load/save. `test_claim_codec`
checks the retained `metadata_opaque` marker, conservative placeholders and
unchanged valid/legacy wire shapes. Normalized active state cannot become
restart admission proof; ordinary finality handling stays conservative.

The frozen-identity lane covers no-write default/passive/inspection paths, legacy
binding refusal, original-snapshot retention, cancelled-shutdown fences, private
storage, corruption/schema refusal and atomic capacity rollback. Two real
processes prove concurrent freeze deduplication; a disposable child exiting
after insert but before commit proves crash rollback without discarding prior
attempts. These are metadata/transaction contracts, not owner/task admission,
native launch, seed pickup or platform restart qualification.
Malformed SQLite column types, truncated databases and orphaned sidecars must
block without losing state or preventing ordinary mux startup. Initial and
promoted daemon activation share the drain-visible mutation fence; admitted
freezing prevents idle-drain acknowledgement, and draining blocks a new freeze.
Extreme numeric timestamps and deeply nested JSON exercise the same structured
blocker across both the source-evidence and attempt journals.

Canonical native machine-record repair has focused existing-linked-Git and
removed-record lanes:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_machine_record_repair.py --test-file tests/test_machine_record_history.py
```

The removed-record fixture also drives the real CLI from a fresh knowledge-only
registration without a projects entry or local project config. It verifies
read-only preview, exact scalar replacement and preserved alias relationships.
Existing linked-Git proof and native/guest refusal remain separate contracts.
Legacy handoff fixtures explicitly model their non-Git anchors as having no
in-repo settings, asserting those files remain absent. Receiving machine-local
YAML and machine-registry readers remain real, with a fresh memoization scope for
each offer/accept transaction. No snapshot crosses a topology edit or retry;
record/registry locks and native Git leases remain real. The ordinary item and
suite deadlines are unchanged.

Bridge's owned-reader cancellation tests cover both the sample join and the
native-command join. Non-cancelling task waits retain detached ownership and
existing deadlines while late failures are consumed without abandoned-shield
exception reports, including on Python 3.14.
The separate native HTTP/2 peer consumes the redirect request body before its
307 response; its evidence probe verifies body consumption and no forwarding.

Standalone fleet, Manager and Dispatch PR jobs are admitted before runner
allocation by `tools/ci_job_admission.py` in the existing discovery job.
`tools/test_ci_job_admission.py` exercises pinned real-Git inputs, dependency/
unknown-path widening, actual job expressions and required aggregate refusal;
`tools/test_fleet_ci_scope.py` retains all six Linux/Windows partition contracts.
The contract selection runs once per platform's planner partition, not twelve
times. Non-PR full/manual/promotion coverage remains enabled even when discovery
is skipped or fails; the existing aggregate still decides overall success.

Guest-environment coverage runs the actual Python, Bash and PowerShell CLIs
with only operating-system observations substituted (account home, guest kind
and machine-id). Each writer's private registration is read repeatedly by all
three runners with and without direct-entry variables. Exact profile snapshots,
registration mode, foreign/conflicting identity and malformed-version controls
retain real filesystem, parser, lock and atomic-write behavior. All three
writer parameters participate in `installation_context_smoke` on native POSIX;
native Windows tests separately cross both installed PowerShell hosts and
prove inherited WSL/OS labels do not confer guest identity. Targeted Worktrees
doctor tests cover explicit enrollment, repeated SSH-style no-write calls and
refusal before unrelated repair machinery.
Separate enrollment-admission fixtures inject another completed registration at
real lock entry, using the native atomic writer. Every runner must revalidate
the newly visible identity, refuse a conflicting name and retain canonical
case for an idempotent enrollment; lock ownership and cleanup are not mocked.

Remote ACP's focused contract/ledger tests and independent synthetic-process
consumer lane run together:

```text
python tools/run-plugin-tests.py agent-bridge --test-file tests/test_remote_acp_transport.py --test-file tests/test_remote_acp_sessions.py --test-file tests/test_remote_acp_peer.py
```

The peer uses a disposable locally trusted synthetic certificate and Hypercorn:
actual negotiated TLS HTTP/2 with connection/session SSE, and actual WSS
upgrade. It checks explicit structured permission/form roundtrips, cancellation,
auth refusal/refresh, cookie affinity, redirects-off, disconnect/load without
prompt replay, capability mismatch without a write and independent server
survival after client close. It does not contact a deployed remote agent or
prove deployment readiness. Existing ACP/client/API tests retain local/provider
regression coverage; the new lane is bounded rather than an exhaustive fleet
matrix.

Bridge's CI smoke job retains the full contained suite and fresh independent
peer processes. Its bounded job budget is 12 minutes (the same native-consumer
exception as dispatch/index): an observed complete run exceeded the former
eight-minute job limit during cleanup despite passing test steps. Other plugin
budgets and the required aggregate gate are unchanged.

Bridge's tolerant-wire-reader guard continues to cover every discovered peer
model. Its explicit local-configuration classifications are the immutable remote
target, contribution, binding and auth-approval settings: unknown configuration
fails closed instead of silently ignoring a typo. Subclasses do not inherit
that classification. Catalog/source tests cover enabled payload ownership,
non-runnable missing bindings and exact URL/auth policy. The native TLS lane
also crosses ordinary roster/detail/create and remote resume for configured,
URL and contributed targets with a real custom-scheme header.
Remote replay tests also include unfinished historical
tools, checking live tracking and telemetry after load, database restore and
event-log rebuild.

The shared `agent-procutil` daemon-broker unit suite covers authenticated frame
bounds, WMI's JSON-stdin environment boundary, containment opt-out, partial-start
cleanup and adoption failures. Its Windows-only native regression launches from
a windowless parent in both breakaway-permitted and breakaway-forbidden
kill-on-close Jobs. Each mode runs two full launch/stop cycles and observes
actual daemon survival, exact argv/environment, split logs and zero attributable
visible windows or foreground changes. A separate broker-handle case checks
wait timeout, termination and actual exit status. The forbidden-breakaway cases
fail rather than skip when lifetime escape fails; contained-mode execution
instead proves that the daemon dies with its caller's Job.
The redirector-fallback case also runs two native cycles; standalone import
tests cover both an alias-loaded single file and a minimal package. Consumer
checks exercise the real Session Host zipapp and the actual standalone
Worktrees lifecycle loader. Fault-injected cleanup waits/closes verify
independent cleanup, normalized errors and refusal to retry with uncertain
process ownership.

Pull Requests' installer wiring contract covers both canonical development
references and self-contained materialized payloads. Its fixture invokes the
real installer-engine materializer without changing the source wrappers or
canonical assets. Both language variants must use the same layout; materialized
engines and their required support files must exist locally and match the
canonical bytes. Missing or modified assets and mixed source/local references
remain failures, not accepted release-layout variations.
Its separate synthetic legacy-source transaction fixture carries the complete,
byte-checked canonical `versioned_runtime*` family rather than unrelated cell
bootstrap inputs. Native construction/reuse and installed-closure retention
remain real; full cell payloads stay in their own acceptance lane. Each native
harness owns its child tree and reaps pipe-inheriting descendants on timeout
within the unchanged query/item deadlines.

Bridge's native PowerShell/Bash completed-slot admission and serving-reconcile refusal
matrices use a minimal plugin payload. Both setup and admission still compute
the real fingerprint and use real versioned-runtime evidence; dedicated
shared-library fingerprint cases retain the full transitive fixture. This
keeps unrelated MSYS hashing subprocesses from consuming the admission cases'
unchanged 30-second item budget. Install and update partitions retain every
case without extending the 300-second suite budget.
The current-daemon native fixture starts independent cold daemon and CLI
processes concurrently. A stdin barrier holds the actual CLI entrypoint until
the daemon is verified ready; neither process is pooled or replaced by a mock.
Their imports and consumer execution share the same 25-second budget, with
cleanup inside the unchanged 30-second item cap. IPv4 exercises reconcile and
IPv6 exercises the read-only installer probe. Fresh-root cases retain ordinary
`python -m agent_bridge` invocation.
Native fixture processes are contained before execution: required no-breakaway
Jobs on Windows, owned sessions/process groups on POSIX. Bounded teardown covers
broker descendants even before a serving-worker receipt exists; an injected
early-failure case verifies both actual processes exit. Real worker CLI cases
also prove missing/corrupt serving credentials stay unchanged, while ordinary
startup retains first-run credential creation.
The same native lifecycle fixture also exercises a supported non-default
loopback bind and graceful start/stop/reconcile. The stopped case compares
runtime files after confirmed native exit, excluding only its own log/output
files, and requires inactive success without activation or runtime writes.
Conditional cutover's native contention checks retain their one-second limit,
measured from actual holder acquisition. Earlier healthy publication/fixture I/O
is not part of that contention interval; the native lock and whole-item deadline
remain real and unchanged.
Real stalled-probe regressions establish descendant readiness and birth-bound
Job/group custody before the forced 0.8-second capture; that fixture input is
not a bootstrap allowance or the production 30-second authentication timeout.
Communication and retirement stay real. On Windows, known pending termination
may receive one same-pinned-handle exit confirmation, capped at 1,000ms inside
the existing five-second actor window with one second reserved for heartbeat
and diagnostics. Failed/unknown observations remain fatal; identity, custody,
absent stop, signaled exit and stable heartbeat must hold before watchdog
expiry and fixture Job close. No production timeout or containment is changed.
The finite Windows probe actors do not import the unrelated process runtime
before publishing readiness. A poisoned-runtime-module regression proves this
dependency boundary while retaining the ten-second readiness and five-second
active windows.

Windows census transport writes compact JSON directly to explicit UTF-8 stdout,
without host formatting. Its native synthetic-data regression round-trips a
command line longer than 400,000 characters with Unicode and control characters;
real durable-launch and cutover tests still exercise the actual CIM inventory.

Budget Guidance's installer fixture supports both development and materialized
payloads through the real materializer. It preserves an embedded dependency
byte-for-byte and fails visibly
for an incomplete embedded manifest/package rather than repairing it from the
development checkout. The cross-platform fixture guards cover both layouts,
source immutability, and malformed embedded input; installer behavior is unchanged.
The billing-response rejection matrix uses short explicit parameter IDs while
retaining its 65,537-byte oversized input, so Windows can set pytest's current-test
environment value without preventing the actual 65,536-byte boundary check.
External-reader fixture snippets use isolated, no-site Python because they need
only the standard library. The real reader, process cleanup and existing
half-second deadline still execute; unrelated editable-package startup is not
part of the fixture's parse/exit/byte-bound classification workload.
Native failed-install and retry cases execute the real PowerShell installer from
a standalone fixture completed by the real dependency materializer. The payload
has no file-pointer or installer-engine references to expand. Fixture copies
omit only their own top-level tests and runtime caches, retain embedded
dependency test/data inputs, and use at most 16 copy workers with propagated
errors. The fake UV's stdlib-only driver uses isolated no-site Python, and the
native child receives explicit executable/module paths and disabled PowerShell
telemetry. Real installer execution, failure injection, slot/selection assertions
and existing item/child deadlines remain unchanged.
The verified-session quota guard checks one absolute acquisition deadline before
and after RPC phases and passes only remaining budget to each RPC. Deterministic
clock cases prove completed late values are withheld; separate real async cases
retain cancellation checks at first status, quota and final status.

The registered-repository Git-probe guard follows the real `repos._git` to
`git_ops.git` call chain. It checks headless options at that subprocess owner,
the existing 60-second bound and UTF-8 capture policy, and removal of inherited
repository/config selectors while retaining unrelated environment state.
The companion real-Git identity test and native registered-only first-touch
CLI test remain process-boundary coverage rather than mocked-runner evidence.

The install-contract machine-gate probe exercises actual policy, gate, planner
and execution-barrier source with synthetic config/inventory/installer
boundaries. Registered and module plans cover forced and ordinary Windows/Linux
updates, explicit enabling repositories and anchor fallback, missing manifests,
universal runtimes and detection failures. Source-mutation tests independently
break each planner and the shared policy/barrier; the production guard-wiring
test prevents these probes from becoming an unwired helper. This source-level
contract does not substitute for native installer or installed-update proof.

Runtime purge contracts retain the original install-lock identity and active
staged sources while removing runtime/config contents. The existing tool guard
suite collects native POSIX flock, live PID-fallback, link-target and failure
cases. Linked roots with terminal slash/dot components must be refused, and a
symlink followed by `..` must purge only the physical directory holding the lock.
Root/home resolution preserves trailing newlines and ignores `CDPATH`.
An unprivileged mode-0300 case holds the real transaction lock and requires
unreadable roots to fail; its source-mutation control reproduces the old false
success without changing item or job deadlines.
Vault's transaction suite runs the real shared helper under both Windows
PowerShell 5.1 and PowerShell 7, with a held lock, open staged source and outside
junction target. Native PowerShell purge cases compare home directory identities
through linked ancestors while retaining legitimate aliased runtime paths; the
existing POSIX guard also exercises that helper under native PowerShell.
Native consumer cases exercise supported uninstall entrypoints
and read-only previews in fresh roots; service APIs are isolated, never live.
Pull Requests retains its existing 300-second production wait: its immediate
native lock probe and source-wiring test cover contention without stretching a
test deadline to wait out that default.
Its fresh-payload fixture invokes the real engine materializer, then runs the
actual marketplace-path self-stage/uninstall twice under native PowerShell and
POSIX Bash. The next invocation reaps the prior dead stage while an independently
live stage survives. Immediate self-stage cleanup is not the v4 protocol.

The Worktrees standalone instruction-preflight fixture copies runtime files
with at most 16 workers, waits for every copy and propagates copy errors.
Byte-preservation and cache-exclusion guards cover that fixture separately.
Native PowerShell and Bash preflight checks assert the framed launch-progress
events while retaining the existing test and subprocess deadlines.

The Worktrees bootstrap fixture also copies its complete plugin payload with
at most 16 workers before the real release materializer closes the dependency
graph. Copy failures propagate, and byte, file-mode and modification-time
guards cover the staged input. Installer source assertions follow the isolated
UTF-8 runtime arguments and direct installation-context helper invocation.
The shared PR repository seed pins `core.autocrlf=false` in its own local Git
config, so copies retain identical blobs even when system Git configuration
enables line-ending normalization. Its regression creates the matching squash
commit, pushes it and runs the real pull-forward operation; production Git
configuration and rebase conflict handling remain unchanged.

Landing explanations evaluate each base's complete evidence once and reuse
those checks only while forming that read-only report. Its creation-history
queries share a context-local snapshot that is discarded even when the report
raises. Native Git regressions add an empty commit or expire actual reflog
evidence after a report, requiring later reports and pruning checks to read
fresh state. Report fields, refusal messages and provider validation remain
unchanged. Historical cross-base cases copy complete prepared Git topologies
for both publication bases, retaining independent objects, refs and reflogs;
each case still performs its own later commit/revert, rewrite, expiry or
shadow mutation and original safety assertions.

Config-overlay regressions require self-hosted lookups to retain base and
machine-local sources without resolving an unused state root. Stateless and
external-state configurations still resolve the knowledge overlay on every
call, including changed or removed bindings. `config_sources` owns this read
path; `state_root` re-exports the same types and function for existing callers.
The diverged-checkout Git fixtures
hide only the unrelated CodeSpace CLI from discovery, leaving real Git,
finalization, status polling and checkout-invalidation assertions intact.
The claim-warning suite separately exercises CodeSpace CLI invocation.
The native consoleless-parent observation in `test_pr_rebase.py` also executes
both state-root Git probes over two cycles, observing windows and foreground
transitions without pytest's subprocess-suppression hook.
Post-publication refspec finalization cases use the existing private-copy
publication fixture. They still perform their own real squash merge, fetch,
reconciliation and unchanged branch/content assertions; publication itself is
executed by the shared seed rather than repeated as setup in each case.

The optional profile-assignment-state failure guard follows both real command
layers returned by worktree creation: exact record-bound execution admission,
then the installed setup launcher. It checks the underlying model arguments,
fallback profile environment and absence of assignment metadata without
mocking either launcher or treating a setup host as the Copilot executable.

The create-plan project-scoping guard uses a real `WorktreeRecord` derived from
the shared fixture, with the selected creation identity and owning project.
Only external side effects are stubbed: the real creation path assembles its
launch plan, including the deferred admission command. The guard still requires
that plan to name the selected repository rather than the stale ambient project.

Agent Logger's PowerShell access-denied classifier guard executes the extracted
installer functions under both installed PowerShell hosts. Each child uses fresh
contained home/config/temp roots, noninteractive startup and disabled telemetry
and update checks; its existing 20-second process deadline and all downgrade/
rethrow assertions remain unchanged. An immediate stderr marker distinguishes
entry into the harness from a pre-script startup timeout. The original hosted
timeout's cause remains tracked under #6747; a passing native run alone is not
flake acceptance.

The POSIX lean-provision launcher test uses fake uv and excludes runtime `src`
and `libs` trees from its temporary payload. It still exercises real lifecycle
admission, self-staging, activation, resolver deployment and both generated
launchers under the existing 90-second item budget. Package construction and
whole-source fingerprint coverage belong to their own contracts; this fixture
must not grow with unrelated runtime source files.
The healthy and namespace/watchdog cases share this source-free payload builder.
Healthy provision has a 20-second installer-owned deadline inside the unchanged
90-second pytest ceiling; failures retain both stdout and stderr diagnostics.

### Shared PR identity against a disposable KeePass database

The opt-in combined contract is
`plugins/agent-worktrees/tests/test_identity_vault_keepass_live.py`.
It requires a real `keepassxc-cli`, both plugins' source/dependencies available
to the selected test interpreter, and
`AGENT_WORKTREES_IDENTITY_REAL_KEEPASS=1`. Run that exact file with pytest
and an explicit 180-second item ceiling for native launcher integration;
normal required suites skip it without the explicit opt-in.

The fixture creates its own database, master password, owner key, cache,
configuration and foreground daemon with a private local endpoint (dynamic
loopback TCP or a Unix socket). It verifies the daemon PID before private IPC
and retires only that child. It never targets an installed daemon or the
operator's database.

The individually bounded cases prove existing-key reuse, mutation-free
status, persisted source selection, a fresh normal consumer while the private
daemon is locked, ordinary missing-entry refusal, explicit first-owner creation,
retry without database rewrites, and stopped-service refusal without spawning
a resident inside the contained adapter. Setup and source configuration must not
expose fixture key material. This is a source integration proof, not installed
release or whole-machine clean-room evidence. Keep source/runtime selection
explicit and record the Docker lane separately when it is unavailable.

The vault's matching `test_identity_payload_gate.py` actual-launcher contracts
are also opt-in (`AGENT_VAULT_IDENTITY_PAYLOAD_GATE=1`, and
`AGENT_VAULT_IDENTITY_REAL_KEEPASS=1` for real databases). Select that exact file
with a 120-second item ceiling. Its pure pin-validation contract remains in
the ordinary suite. This keeps repeated native-shell startup and real databases
out of the required fast portfolio rather than expanding its default budget.

### Fleet replay-retention boundaries

Native index installer snapshot tests separate dependency-content identity
invalidation from publication ordering and snapshot-local consumer closure.
Every case retains its real PowerShell boundary and existing 25-second child
deadline; engine and library changes are independently covered, not omitted
from the unchanged publication/older-pointer refusal case.

Select Index's two-cell shutdown contracts with `tools/run-plugin-tests.py
agent-index --test-file tests/test_installation_cells.py -k
"two_cell_local_services or two_cell_shutdown"`. The real isolated services
retain their observed instance tokens, require explicit shutdown acknowledgements
and zero exit codes, and report control failures after waiting for both children.
Four boundary-fault controls inject a false acknowledgement, a response failure,
a nonzero wait result and a timeout result after real graceful shutdown; they
verify that neither actual child remains running. Native deadlines, item limits
and required unit-only CI selection are unchanged.

The passive index-service promotion contract supplies an explicit synthetic
search engine, just as the adjacent drain cases do. Its real HTTP admission
must reject passive queries without invoking that engine, then admit exactly
the tested query after owned promotion; it never contacts an embedding service.

The required native fleet lane runs the replay-expiry/idempotency contract with
a four-event window and real, fully synchronous SQLite transactions. The same
parameterized test also crosses the production 1,024-event window under
`observation_retention_full`; unfiltered local and manual workflow runs retain
that full-size proof. Required PR/push lanes exclude only that parameter and
the existing `client_distribution_full` artifact matrix, keeping the
120-second containment budget unchanged. This avoids making one boundary
test issue over 1,000 disk flushes on every hosted Windows run; neither the
production retention limit nor its durability policy changes. Explicit manual
runs select both windows and the full artifact matrix with a 600-second fleet
suite budget inside a 15-minute native runner job. PR/push budgets remain
120 seconds for fleet, five minutes for the Linux job and seven for Windows.

The native fleet matrix runs disjoint planner, durable storage, artifact,
client-distribution/diagnostic, Gateway TLS and Gateway process partitions on
both operating systems, each with its own unchanged containment and job deadline.
The exact `-k` selectors are
declared together in `ci.yml` and checked by `tools/test_fleet_ci_scope.py`.
The required PR aggregate depends on both static GitHub-hosted
`fleet-native-linux` and `fleet-native-windows` partition matrices;
a failed or cancelled partition fails that gate.
Every otherwise selected test belongs
to exactly one partition; neither partition is advisory or fail-fast.
Manual runs use the same complete partition and retain both full markers.
Native artifact/diagnostic subprocesses and durable planner/ledger tests do not
consume each other's wall-clock budgets. Gateway TLS/credential tests and
separate-process acceptance are independently bounded too; none shares the
unrelated test-runner job's budget.
The full Worktrees runner gives `test_python_runtime_dev_live.py` and
`test_python_runtime_install.py` separate file groups. Exact-head native CI
showed the former shared group consuming 196 seconds through both dev cycles
before monitor drain began; that left 104 seconds for a drain test independently
measured at 129 seconds. Splitting those files preserves every collected test
and all item, 300-second group and whole-plugin deadlines. Filtered invocations
retain their existing single-group behavior.

The focused `test_python_runtime_install.py` selector
`-k prepared_bootstrap_loads_actual_installer_without_source` prepares the real
retained Worktrees lifecycle, removes its frozen source and runs the copied
installer's `--help` with an isolated interpreter. The complete closure must
load, while deleting either wheel-snapshot helper must fail at its import.
These controls require no package build, UV invocation or host runtime change.

### Native PR-owner installation acceptance

The manual `installation-context-acceptance.yml` workflow retains its derived
diagnostic matrix by default. Dispatch it at the candidate ref with
`watch_owner=true` to run the PR owner's normal full-payload refresh/live upgrade
and two-shipped-cell isolation, normal PR-owner peer roundtrip, plus
reachable/unreachable lease-only refusal,
on separate fresh Linux and Windows hosted
accounts. Each job explicitly uses `--allow-host-state --allow-explicit-tiers`;
the test fixture refuses non-hosted runners, existing installation/config state,
or a home/profile differing from the OS account database (POSIX) or native
profile-folder API (Windows). It never redirects HOME or USERPROFILE in this
lane. The hosted job discards the whole account afterward, while each test
gracefully stops only its own original, natively captured owners.

The Windows normal-upgrade scenario also runs the opt-in bootstrap admission
visibility/cleanup probe for Windows PowerShell and PowerShell 7. It launches
the actual wrapper from a real `pythonw.exe` parent, exercises an unflagged
local console descendant across two invocations, and uses the shared live
Win32 window observer plus stricter foreground tracking. A separate forced
timeout proves all four native participants belong to the caller's Job and
exit when that Job is terminated, with zero active processes confirmed.
The probe is restricted to the disposable hosted-account lane, not the
operator's desktop or fast required CI. It creates no daemon or remote route
and does not test or authorize deployment.

These are manual acceptance jobs, not additions to required fast CI. Each
composite case runs separately under a 1,200-second group and 1,800-second
aggregate bound; actual service cutover and native-exit deadlines remain
120 and 30 seconds. Ordinary contained Windows invocations still use disposable
fixture profiles and are not evidence for the native-account lane. The lease-only jobs first perform normal full-payload installation, then
prove refusal with its already-selected metadata and archived terminal custody
preserved; they never control or replace the unprovable original. Local Windows
private-profile coverage for that first-touch path is explicitly selected with
`--allow-explicit-tiers -k normal_payload_lease_only_owner_preserves_selected_metadata`.
The `normal_payload_peer_roundtrip` case uses the normally installed PR payload
as both caller and target peer, with a test-owned Worktrees echo backend and
canonical adoption/resolver primitives. It proves inherited streams and exit
status, credential/override scrubbing, explicit same-cell PR health, unchanged
selection and no bytecode mutation of the immutable bootstrap snapshot.

The same opt-in also runs separate contained owner-boundary jobs on Linux and
Windows, without host-state admission: actual completed-slot controller death
at 12 cutover boundaries and a real installer activation amid admitted
register/unregister requests, a poll result, a slow acknowledged callback and
queued legacy callbacks. These use disposable fixture profiles and test-owned
fetch/gate/spawn adapters, not native-account or full-shipped-payload evidence.
The passive-bind crash regression remains an ordinary smoke case; the other
crash items and the composite concurrent upgrade are explicit T3 cases. Run the
full process matrix with `--allow-explicit-tiers` and the selectors
`actual_controller_death_recovers or bound_passive_endpoint_recovery` and
`actual_installer_upgrade_drains_handlers_poll_ack` in separate runner calls.
Production callback, cutover and original-exit deadlines remain unchanged.

The separate native macOS job runs
`native_macos_governed_watch_refuses_unprovable_identity` in a real completed
interpreter. Until shared native birth/exit evidence supports macOS, governed
activation and instance creation refuse; an absent-owner stop stays bounded,
and cleanup/original-exit recovery refuse without health control, signaling,
spawning or selected-state mutation. This proves the fail-closed boundary, not
working macOS governed cutover. The ordinary unsupported-evidence unit case
injects unavailable primitives and is not native macOS evidence.

### Derived native installation diagnostics

The Windows source-provenance/read-only lane separates command-catalog
contracts, command-lifecycle contracts and native first-touch cases. Every
previously selected case still runs; each step retains its two-minute deadline
and the enclosing job retains five minutes. The former combined selection could
exhaust a step's budget through cumulative successful native calls, without any
single subprocess exceeding its own deadline. Do not re-combine those groups or
drop first-touch assertions to make the aggregate appear faster.

`libs/payload-invocation/tests/test_derived_diagnostic_cli.py` executes the
actual vendored shell entry points selected by the installation-context
inventory. Each copy has independent legacy/active and absent/retained-pointer
items; each item invokes `status` and `probe-legacy` twice and compares the
complete fixture profile after every call and after PowerShell-host shutdown.
The existing initialized-host fixture excludes only host initialization from
the baseline, never product execution or profile contents.

Required change-scoped CI runs the Agent Worktrees copy's four cases with
`-m "not derived_diagnostic_full"`; the existing derived-Python API matrix and
mutation tripwires remain required. The explicit
`installation-context-acceptance.yml` manual workflow executes every native
copy on Windows PowerShell 5.1 and POSIX, partitioned by mode and pointer state
into eight independently bounded four-minute jobs. Run it against the exact
candidate ref before claiming all-copy native acceptance. A smoke pass or
byte-identical vendoring check alone does not satisfy that broader gate.

### Assignment reconnect contracts

The assignment TCP reconnect guard first holds the initial ready frame until
startup reconciliation completes, then waits for the independent ready-triggered
read before disconnecting. It holds the replacement SSE subscription at
an explicit server-side barrier while creating unannounced work, and releases
that barrier only after checking the disconnected queue, rather than assuming
an HTTP request and a fixed sleep fit inside the real reconnect backoff.
The blocked-spawn presence guard also waits for actual reader readiness;
heartbeat progress alone does not prove the independent reader connected.

### Native category view contracts

The worker-instance first-touch CLI contract has independent catalog, save,
activation, retry, published-catalog and degraded-catalog items. Each makes one
real cold subprocess call under
the existing 20-second subprocess and 30-second item bounds; prerequisite
states use the same CLI parser/handler in-process against the fixture's real
files. This preserves every cold-entry boundary without composing multiple
permitted 20-second calls into one 30-second item.

`plugins/agent-dispatch/tests/test_task_categories.py` gives each native view
contract its own function-scoped coordinator/database and pytest item:
`test_native_cli_category_view_contract[default-work|all-categories|maintenance-board|evidence]`.
Each item makes one real cold CLI invocation with the existing 20-second
subprocess bound under the unchanged 30-second item budget. Producer creation
still crosses real loopback HTTP; evidence inspection retains result, progress,
statistics and session/worktree attachment assertions.

Do not combine these into one serial multi-CLI item: four individually permitted
20-second calls allow 80 seconds before setup/teardown, which cannot fit a
30-second item. This granularity fix addresses test-contract budget composition,
not a diagnosis of an observed process hang. The original unknown-cause timeout
tracking remains open; an isolated or instrumented pass is not flake acceptance.

```powershell
python tools\run-plugin-tests.py agent-dispatch -k native_cli_category_view_contract
python tools\run-plugin-tests.py agent-dispatch --guards -k one_cold_cli_boundary
```

### Restricted supervisor Docker acceptance

`plugins/agent-dispatch/tests/test_restricted_supervisor_docker.py` is opt-in.
It uses a disposable, test-owned host Bridge/supervisor, a real central service
container and a separate restricted worker on an internal network. No installed
service, live account, shared Docker socket mount or cloud token is used.
The offline ACP witness creates its actual native identity, then starts,
reads, reports progress and either completes or abandons its own task through
authenticated HTTP, in independent fresh-container cases.
It must reject cross-task, fleet-route and signature-tampering attempts, and
observe protected task-file rotation by the trusted supervisor.
Acceptance also waits for the witness's final proof after its terminal
revocation check, not merely for the central task row to become completed.
An older matching-label task with unmet capabilities must be deferred without
an agent, allowing the subsequent eligible task to run in the same lane.

Build the central image from the candidate source with
`services/dispatch/Dockerfile`, using the environment's approved package-index
configuration. Prepare the `agent-dispatch`, `agent-bridge` and
`agent-containers` test runtimes through `tools/run-plugin-tests.py --prepare-only`.
Set `AGENT_DISPATCH_RESTRICTED_E2E=1`,
`AGENT_DISPATCH_E2E_CENTRAL_IMAGE` to that image, and
`AGENT_DISPATCH_E2E_BRIDGE_PYTHON` / `AGENT_DISPATCH_E2E_PROVIDER_PYTHON` to the
prepared Bridge/provider interpreters. Then run:

```powershell
python tools\run-plugin-tests.py agent-dispatch --test-file tests\test_restricted_supervisor_docker.py
```

For bounded execution, select `-k complete` and `-k abandon` in separate runner
invocations, each with the original 300-second item / 360-second aggregate
limits. The [host-driven clean-room lane](tools/clean-room/restricted-dispatch/README.md)
does this automatically and retains a structured report plus both cases'
proofs outside the checkout.

The fixture owns its temporary Bridge state, provider runtime copy, containers
and networks, and tears them down. Provider discovery uses an isolated legacy
manifest to exercise its supported compatibility path without changing real
plugin activation. The test is programmatic transport/authority acceptance,
not a model evaluation or a claim of automatic pool placement.
For timeout diagnosis, `AGENT_DISPATCH_E2E_EVIDENCE_DIR` may name a new absolute
directory under an ignored local test-output root. The fixture refuses reuse
and retains timestamped Bridge diagnostics and operation-label-only stage
timing, without writing provider stdin or task credentials into the stage log.
All diagnostics remain inside the unchanged test and subprocess deadlines.

### Consented Windows elevation (Agent Conduct Guidance)

`tools/run-plugin-tests.py agent-conduct-guidance --test-file tests/test_windows_elevation.py`
covers exact terminal IDs, incomplete/stale/success-shaped failure refusal,
actual stdout/stderr, argument quoting, native exit codes, script drift and
owned-Job deadlines. Broker failure controls replace `Start-Process`, so this
selection never requests UAC or registers a task.

On a quiescent, active same-user Windows desktop, set
`AGENT_CONDUCT_TEST_INTERACTIVE_ELEVATION=1` and select
`tests/test_windows_elevation_scheduler.py` through that same runner. Its two
bounded cycles use private directories, real limited interactive tasks and the
harmless probe. They observe visible console windows and foreground transitions,
require matching `probe` receipts and verify exact task cleanup. They never
request UAC and do not qualify administrator execution.
Window qualification first requires the baseline and watcher thread to match
the worker's recorded session, window station and desktop. An unbound observer
explicitly reports both window and focus observation unavailable; a nonempty
`EnumWindows` result alone is not qualification for the selected desktop.
The watcher takes its first snapshot before signaling readiness and propagates
startup or later observation failures to the test. Its three synthetic observer
controls run without desktop opt-in; empty or truncated observations cannot pass.
Foreground qualification additionally requires a non-null observable foreground window.
A headless caller can enumerate visible windows yet receive a null foreground
even when the operator confirms an unlocked desktop. Such a run emits an
explicit unavailable-observation warning: it does not prove zero focus changes.
Use an observer with access to the interactive foreground or obtain a literal
operator witness of the two harmless cycles before claiming that dimension.
The native fixture allows thirty seconds for queued consent admission, gives
its real child PowerShell ten seconds and bounds the complete command at
twenty-five seconds. A five-second fixture child bound expired in the
combined native run; the separate two-second owned-Job deadline
control and production defaults remain unchanged. Failure diagnostics retain
the log tail rather than hiding the last completed stage behind a reader timeout.
Discovery controls exclude access-denied/unmapped foreign entries while still
requiring positive SID ownership, and preserve unexpected errors. Identity
preflight tests use token elevation type (including linked limited tokens),
not the filtered group list, and reject unsupported alternate-account elevation
before staging or consent.
The worker contract selection also verifies an actual write/replacement denial
between hashing and execution, separate admission expiry while execution time
remains, and retained process-handle proof that long-lived descendants die on
leftover detection and on forced worker exit. These controls are non-elevating
and do not request a UAC dialog.

Actual administrator qualification remains an explicitly approved, on-demand
invocation of the skill's harmless `assets/probe.ps1` with `-ConsentConfirmed`,
a fresh private log and the selected interactive desktop. Require its actual
elevated-token output and matching successful terminal marker; do not count a
limited `probe` result, scheduler result or canceled dialog as that proof.
No ordinary test/CI path prompts a human, changes UAC policy, stores a password,
registers a highest-privilege task or reboots.

### Native Agent Machines lifecycle boundaries

`tools/run-plugin-tests.py agent-machines --test-file tests/test_registry_native.py`
checks DWORD/QWORD convergence against real `reg.exe` hexadecimal output.
It reads the existing Windows `InstallDate`/`InstallTime` values independently
with `winreg`, then exercises dry-run and apply through a query-only runner.
No registry value is created, changed or deleted. Missing Windows prerequisites
skip explicitly; a skip is not native convergence proof. Pure injected-runner
controls remain in `tests/test_resources.py`; required unit CI is unchanged.

`plugins/agent-machines/tests/test_payload_installer_lifecycle_powershell.py`
likewise gives each cleanup outcome, staging/watchdog outcome and output
encoding its own function-scoped sandbox and pytest item. The default remains
an 18-second subprocess bound and the runner's 30-second item budget; combining
those native scenarios into serial loops exceeds the composed budget. The two
Windows PowerShell 5 staging items alone use a 45-second subprocess bound and
a 60-second item budget: measured cold nested hosts/probes consumed roughly
five CPU seconds apiece before reaching the smoke seam. Their fixture watchdog
uses 20 seconds and a 30-second simulated sleeper so it can observe the started
child before proving exit 124. PowerShell 7 staging uses a 30-second subprocess
bound and 45-second item budget, with an eight-second fixture watchdog: its
three-second watchdog demonstrably killed a cold staged child before its smoke
record existed. Other native cases, production deadlines and every
readiness/output/cleanup assertion are unchanged. Run the
native lifecycle file without `--guards`, which excludes these integration
items. An individual subprocess timeout still fails and requires diagnosis;
parameterization does not excuse it.

This lifecycle fixture preserves the upstream in-process governance stub and
its phase diagnostics; it is not an end-to-end governance invocation. Actual
owner admission and native installed first-touch repair are exercised separately
by `test_installation_repair.py`.

The native plan/apply/retry, multi-apply restart-confirmation and independent
restart-batch items in that file have a 60-second native Windows item budget,
retaining 30 seconds elsewhere.
Their several real PowerShell calls exceeded the former 30-second item cap;
an isolated unchanged-bound run took 24 seconds. Production readiness/installer
deadlines and all restart, dependency and confirmation assertions are unchanged.
The plan/apply/retry case also exceeded 30 seconds after adding mandatory
executable admission and re-admission of initially-ready outcomes; its real
process calls and repeated-apply assertions remain intact.
Native CMD path/argument preservation and POSIX conductor-termination tests live
in `test_installation_repair_boundaries.py`; the latter proves descendant cleanup
before lock release for SIGINT and SIGTERM during action execution, spawn
ownership handoff and normal-exit cleanup, plus preservation of caller handlers
and ignored child dispositions. The same file covers executable
admission failure without execution and binding drift during an initially-ready
probe and required-dependent blocker propagation after optional prerequisite
binding refusal; source-content admission is shared by planning and actual invocation.
The admitted executable is passed as an absolute argv path. A native POSIX
relative-PATH case separates the conductor and payload working directories and
places a different executable at the payload-relative path; real plan/apply
must still invoke the admitted interpreter.
POSIX payload commands bind supported `sh`/`bash` shebang interpreters, bypassing
`env` lookup after the working-directory change and refusing unsupported forms.
Both command and script relative-PATH cases exercise real plan/apply. Native
Windows Job teardown confirms zero active members, including an exited
installer's redirected descendant, before releasing the repair lock.
POSIX cleanup likewise waits for transient group members to exit (ignoring
resource-free Linux zombies); an exited-parent regression verifies a redirected
descendant's exclusive file lock is released before repair unlocks. Interpreter
admission preserves the declared absolute path's basename semantics while
binding its resolved executable content, including `sh` symlinked to Bash.
Declared action content is bound independently of payload cache exclusions.
Post-install probes also require fresh owner admission before readiness is
accepted, including the final selected module. Binding drift or cancellation
retains the completed installer result but rejects stale readiness and blocks
dependents. The eight cancellation-boundary and three post-install drift cases
use a 60-second native Windows item budget after a real PowerShell probe exceeded
30 seconds; POSIX item limits and production deadlines remain unchanged.

The first-use items in
`plugins/agent-machines/tests/test_payload_provisioning_powershell.py` separate
stamp refusal from the real failed-provision-then-retry-then-warm sequences.
PS5's retry flows use at most 60 seconds per invocation within a shared
120-second workflow deadline and a 150-second item budget. Measured native
failure/retry/warm chains took 50-80 seconds; a 45-second invocation bound still
expired before the first installer entered in the namespaced cold-host chain.
The single stamp refusal instead uses a 45-second workflow and 60-second item
budget. PowerShell 7's retry/warm cases use a 30-second invocation within a
shared 60-second workflow and a 90-second item budget: a measured cold
legacy invocation exceeded 20 seconds after stamp but before provision.
Its single stamp-refusal case retains the 20-second invocation and
30-second item limits, with a 25-second workflow bound leaving cleanup time.
These are aggregate deadlines, not independent retries that each reset the
workflow budget. Both hosts still verify live progress, complete split
stdout/stderr capture, exact exit codes, actual retry, warm dispatch and cleanup.
Transient processes use the canonical caller-owned Windows Job: a deadline must
close the whole tree before joining readers or closing their streams.

### Central versus local-only authority

`tests/test_client_authority.py` in `agent-dispatch` covers the opt-in
`AGENT_DISPATCH_TASK_AUTHORITY` selector, separate credentials, no automatic
cross-authority fallback, local installer lifecycle, producer/worker routing,
restricted task pins, explicit browse/create authority and presence-only role
admission. The existing CLI discovery fixture has its own distinct name so it
cannot shadow the suite's authentication-command isolation fixture.
Authority-qualified claim providers retain fixed callback target prefixes and
disable read-time provisioning. The existing native consumer-prefix tests also
run under the managed Windows runner: their CMD fixture uses an ordinary path
spelling of the same interpreter file, while preserving every registered
argument, child environment and process boundary.
Native fallback retirement retains the selected CIM creation identity and a
process handle: creation-time validation and termination use that same handle,
never a later PID lookup. Missing, replaced or unqueryable identities refuse
with a warning. The native regression covers replacement of both roots and
descendants, handle disposal and a real test-owned child's stale-identity
refusal followed by matching-identity termination. Runtime existence probes use
literal paths so extended Windows interpreter paths reach these real adapters.

The opt-in native multi-container proof is documented in the
[central/local authority clean-room lane](tools/clean-room/central-local-authority/README.md).
It uses the actual central service, an independent peer database and native
CLI processes inside fresh containers, including a central network partition
without ownership migration. It is not required PR CI or a model evaluation.
The lane's host driver durably preassigns the network and all three container
names (including the UID probe), binds the fixture to that registry and verifies
absence after contained-worker death within the original 360-second budget.
`tests/test_central_authority_clean_room.py` covers report/owner refusals and a
real native containment timeout against a file-backed daemon protocol stub.
That fatal-worker regression is not a real Docker fault-injection proof.

The standalone runner accepts the legacy `agent-index-service` and
`agent-dispatch-service` component names as aliases for `services/index` and
`services/dispatch`, respectively. It normalizes aliases before environment
admission, dependency preparation, and smoke/full selection, so a released
promotion workflow can validate a development snapshot after the service move.
New invocations should use the canonical service paths. The native runner
contract executes both aliases in canonical contained fixture environments
from an isolated parent whose pytest dependencies reside outside its interpreter
scheme, exercising the layered dependency layout without installing packages.
Each alias has its own native fixture item and unchanged subprocess budgets.
That pure pytest fixture disables unrelated third-party plugin autoload without
changing the real contained runner or its ownership/environment assertions.
Preparation's disposable override files stay under the repository so retaining
their topology does not expand copied source paths back to a drive-root layout
and exceed Windows' relative-path limit.

`agent-chat-service` resolves to `services/chat` through that same canonical
preparation and environment-admission path. Its dependency-free conversation
core and optional client role have an **on-demand contained standalone
portfolio**, not required native Windows/Linux CI lanes. Current source CI
executes the admitted [unit-only packet](#unit-only-promotion-packet); its green
required aggregate does not qualify this standalone portfolio or either native
client platform. The existing periodic integration portfolio does not currently
enroll `services/chat`.

The separate `tools/test_chat_container_template.py` packet consumes Chat's
portable container contribution through the canonical Fleet design/role-binding
readers and source CLI. Its synthetic support graph checks exact private
actualization, provenance, resources and refusal without installing a runtime
or changing Fleet implementation. Run it with an existing pytest interpreter;
it is not enrolled in the native Chat construction portfolio and does not
qualify an image, private allocation, Gateway connector or Komodo effect.

Portable SQLite, real CLI/provider/loopback HTTP contracts,
process-crash reader-lock recovery and source-independent core/client wheel
installation run within the existing contained standalone harness. A fresh
default-core environment proves the helper is absent; the separate offline
client-role environment installs its declared canonical helper and uses the
actual console after all fixture source trees are removed. Temporary listeners
and credentials are synthetic fixtures, not production authentication, model
wake, plugin activation or managed service deployment.
Auxiliary parser/offline dispatch checks run in-process, without replacing the
real provider/HTTP operations. Actual CLI round trips, installed-console death,
source-independent artifacts and worker-shadow cases retain their real process
boundaries. Capacity setup seeds existing valid records; the actual final
admission, overflow refusal and uncertain-request recovery still use the normal
client and its durable writer at the unchanged 128-record boundary.

The shared role fixture builds four independent wheels and installs three
separate fresh environments with bounded parallel builders. Its source copies
exclude test trees unrelated to wheel construction; package inputs, offline
dependency resolution and source-removal assertions remain real. The healthy
installed console/catalog exchange uses a fixed-route stdio provider forwarding
to the fixture HTTP service. Installed credential/isolated-HTTP, caller death,
worker shadow, uncertain replies, deadlines and raw capture bounds retain their
own actual process cases. Exact CLI argument forwarding is checked cheaply
against the client API rather than repeating those same full-stack operations
for every parser variant. Preserve the real installed join before the
console-death fault; cold startup is not part of its five-second read barrier.

Finite artifact/CLI fixture commands use the published caller-owned process
API with their unchanged 20/30-second bounds and separate raw capture limits.
The artifact fixture passes the supported CWD/environment inputs directly,
removes `PYTHONPATH` and preserves stdin plus separate stdout/stderr without
another Python child adapter. It does not change the test process's global
CWD/environment or implement another cleanup authority. The canonical helper
owns the entire bounded tree; the explicit console-death
test still performs its fault and pinned observations before external cleanup.
The shared first-party library wheels and independent base Chat sdist are built
once in parallel. Metadata-only version variants each receive a fresh wheel
and core/client/service environment set; returning to a previously qualified
version reuses only that exact version's artifacts. The promoted-version
control checks the requested version explicitly. Independent library sdists
are built only when the full construction control requests them. No collected
case or real installed boundary is removed by this fixture reuse.

Chat qualification requires **both lanes on the same immutable candidate**:

```text
python tools/run-standalone-tests.py services/chat --prepare --chat-suite acceptance --timeout 180
python tools/run-standalone-tests.py services/chat --chat-suite full --timeout 600
```

Chat defaults to the acceptance lane and its 180-second allowance; `full`
defaults to 600 seconds. Other consumers keep their existing 600-second
defaults. The expanded artifact/version portfolio exceeded the previous
120-second acceptance allowance; the measured increase retains the entire
acceptance inventory rather than moving cases out of the lane. Per-operation
deadlines, capture bounds and the full allowance are unchanged.
Neither lane substitutes for the other. The acceptance packet runs
the real source-removed installed client, native caller/provider/descendant
death, protocol, authentication, durable data/progress, deadlines and raw
capture contracts, plus explicit local rendezvous refusal, TCP rebinding and
the native POSIX Unix-socket client. The complete lane additionally runs all host and
source-removed service lifecycle cases, with no test removed or marked skipped
by this partition. Existing platform-specific skips still require positive
qualification on their owning platform.

`services/chat/tests/qualification.py` lists every test family and its lane.
Every collected parameter node must map to exactly one family with its retained
parameter count; new, missing or duplicate families/nodes and lost parameter
cases fail qualification instead of silently disappearing.
Each lane reports the same complete membership digest, collected/selected
counts and a parameter-level JSON map, atomically persisted when collection
finishes rather than waiting for the terminal summary. Supply
`--chat-membership-report <new-file>` to retain that map, including the actual
runner and test-process exit codes, after sandbox cleanup. A killed test
process retains its collected map. A failure before completed collection
exports explicit unavailable membership rather than fabricated empty nodes;
reporting never replaces an original nonzero containment result. Missing
membership after a zero exit refuses qualification. Its parent directory must already
exist and an existing report is never overwritten. The
acceptance collection explicitly deselects full-only families, while `full`
executes their union. Direct pytest/partial diagnostics without the runner's
explicit qualification environment remain unqualified and unpartitioned.
Filters and collection/setup-only modes are rejected for either lane.
The runner validates membership even without external report retention:
an early zero exit such as pytest help/version is not qualification.
Fixture-listing modes are also refused. At session completion, actual
passed-call reports and the explicitly allowed Windows skips for POSIX
private-file permissions and the native Unix-socket host must
exactly cover the selected nodes, without duplicates, unexpected skips,
xfails or failing phases. The map records pending/complete/incomplete
execution separately from collection. A zero exit with collection alone
refuses qualification, even if a map exists.

The native caller-death case reuses the process utility's test-only birth-pinned
Windows handle/Linux pidfd observer. It observes the actual installed provider,
its sleeping descendant and the console ancestry alive, then requires them all
to exit before outer observer cleanup and before the existing held-read
watchdog. The test dependency does not enter the core/client runtime wheels.

Typed collaboration adds pure normalization and captured-view-control contracts
to `tools/promotion_unit_tests/test_chat_collaboration.py`, with the exact
five-file import/test closure and production coverage targets enrolled in the
existing unit manifest. These units create no store, provider, process or
listener. Database atomicity, non-consuming pending/thread pages, unchanged
reader progress, strict receipts, lost-response recovery and actual source-removed
installed commands stay in the existing Chat acceptance/full portfolio.
Its family and parameter inventory expands additively. Every original family
remains; the current whole-lane allowances are the 180/600-second bounds above.
The empty-inbox and sparse-thread regressions compare actual SQLite instruction
counts with100 versus5000 unrelated questions/messages, including the viewer's
own ambient traffic and correspondence in another channel. They allow at most1000 extra instructions
while retaining correct pending/page results and unchanged semantic progress.
Pending-backlog cases additionally require the same bound with100 versus5000
unanswered questions and mixed answer/question attention. Settled-history cases
retain the bound for answered, resolved and superseded conversations, proving
candidate examination is limited before retirement filtering. The client follows
empty scan-progress pages within the captured boundary without skipping pending
entries or consuming semantic progress, and rejects stalled/out-of-bound
continuations and mismatched returned counts.

The sharing-race control injects its transient error only after the first
successful real readiness-file read. A stale negative existence probe cannot
bypass injection. Both normal and fault cases retain actual child birth/prefix
checks, crash-before-outer-Job-cleanup proof and the original five-second bound.

The [per-test assessment](efforts/active/agent-chat-graduation/test-portfolio.csv)
and [measured comparison](efforts/active/agent-chat-graduation/README.md#journal)
separate the full222-case snapshot from explicit42/221-case candidates measured
under120s. Later230/236-case measurements and a65-pass/one-skip changed-path
packet are recorded separately in that journal; none is a current full120s
acceptance claim. Those historical results do not retroactively pass either
new lane. The split does not change required CI, per-operation bounds or
containment. Use pytest's built-in JUnit call-duration reporting plus
`--durations=0 --durations-min=0` to retain every case and fixture cost; quote
Windows output paths inside `PYTEST_ADDOPTS` so its argument parser preserves
backslashes. Shared artifact setup must not be charged solely to its first user.
Record the exercised platform and artifact separately. The retained client
candidate consumes the published caller-death API with separate raw capture
budgets. Source release alone does not update a resolved installation.
Qualify fresh installed consumers on both Windows and native Linux;
portable-core or unsupported-mode
control results are not positive native Linux client qualification.

The index-service smoke lane includes offline container-context and explicit
fleet-image profile contracts; neither needs Docker, a model or enrollment.
The real-wheel release test builds the sixth first-party `fleet-manager` wheel
and verifies the actual declared fleet extras, without publishing or installing
those fixture versions. Native schema-1 release behavior remains covered.

Dependency preparation uses the canonical seven-day configured-resolver policy.
Do not replace a failed admission with plain pip or an alternate index. After
changing declarations, run `python tools/check-dependency-age.py`; its explicit
`--ecosystem` lanes are diagnostics, not a replacement for the all-ecosystem CI
gate.

The dependency-age audit retains every source-contract read and reuses an AST
only when its decoded source text is identical within that audit. Changed
evidence is reparsed; containment, read, encoding and syntax failures are not
cached away. Each invocation owns fresh tree storage, with no persistent cache
or change to declaration inventory, metadata requests or age policy.
`tools/test_dependency_age.py` covers parse reuse, mid-audit evidence changes
and failures, and revocation observed by a later audit.

The automatically selected `tools/test_installer_dependency_policy.py` suite
also exercises the deny-local CLI mode without product discovery. Its native
bootstrap fixture runs the real materializer,
removes the original library tree and deliberately corrupts embedded product
identity, then bootstraps a pinned registry wheel into a fresh venv through
real UV. Stricter cutoff, unseasoned artifacts and wrong hashes still refuse.
The ordinary attributed-source path remains covered separately.

Focused policy tests are:

```bash
python -m pytest -q tools/test_dependency_age.py tools/test_installer_dependency_policy.py tools/test_dependency_uv_executable.py tools/test_seasoned_npm_install.py
```

The editable-retention regression runs the real setuptools backend; the test
environment must include `setuptools>=83.0.0` and `wheel`, installed through
`tools/seasoned_uv.py` like CI's guard tooling.
The native UV lane uses a controlled index to exercise a transitive dependency
and an isolated build backend, including newer/missing upload timestamps,
transitive URL refusal and UV's conservative exclusive boundary. The native npm
lane runs configured-registry queries and `npm ci` against a controlled registry;
newer or unknown releases must refuse before artifact download. These helpers
perform no production service mutations.

The standalone runner's retained-resource regressions build real Fleet and
Index wheels with their declared setuptools backend; use a prepared test
environment, not an older host setuptools installation. Installer fixtures
must run from their own attributable payload or isolated working directory so
UV does not discover unrelated checkout metadata. Retained-path assertions
validate source identity, private ownership and content at the physical UV
boundary before normalizing paths for ordering or retry comparisons.
Vault's offline UV fixture builds its native PE directly with the Windows
.NET Framework compiler, preferring Framework64 and falling back to Framework.
Its existing preflight contract forces fresh compilation and executes the real
provider, then proves byte-identical reuse without another compiler invocation.
Headless execution and the 20-second native child deadline remain unchanged.
This fixture does not alter snapshot copying or the separate NoService contract.

Vault's NoService snapshot fixture invokes the actual installer and isolated
runtime root through a PowerShell wrapper with throwing scheduled-task mutation
controls. The existing stamp case proves all five controls refuse mutation,
preserves arguments containing spaces and verifies exact nonzero exit forwarding.
Real snapshot provisioning and compatibility launch retain their original
assertions and deadlines; installer/runtime code and host services are unchanged.

Vault's two snapshot-provisioning integration cases explicitly allow 60 seconds
per item: fixture setup performs one complete four-package installation,
health gate and publication transaction. The remaining items keep the default
30-second limit; suite, child-process and CI budgets are unchanged.
A focused, change-scoped Windows CI job executes both native cases under the
same two-minute suite limit and participates in the required PR aggregate;
the existing POSIX plugin suite remains required independently.
The off-PATH Windows bootstrap fixture creates a real stdlib virtualenv in a
spaced directory and probes its exact executable and prefix with a restricted
PATH before invoking the hook. Copying a base `python.exe` and writing
`pyvenv.cfg` is not a runnable cold interpreter fixture: its DLL dependencies
may be absent, independently of argument forwarding or installation policy.
The full native Fleet check allows 300 seconds on Windows under its unchanged
seven-minute CI job limit; Linux retains 120 seconds. This is a full-suite
budget, not a reduced test selection or a relaxed dependency-admission gate.
Multiline PowerShell steps propagate each native admission or prerequisite-test
failure explicitly before starting later commands.
`tools/test_installer_finalizers.py` exercises production cleanup and exclusive
lock reacquisition in the same still-live Windows PowerShell host.

The pre-commit hook runs staged guards in batches of 32 paths to stay within
Windows child-command limits during large integrations. Every path is checked,
later batches still run after a failure, and any failed batch blocks the commit.
`python -m pytest -q tools/test_pre_commit_batches.py tools/test_hook_interpreter.py tools/test_check_large_files.py`
exercises the real hook, spaced/flag-like paths, batch failures and existing guards.

The review-request workflow's fast contract uses
`node --test tools/test_copilot_review_gate.cjs`. To exercise its exact script
against the bundled `actions/github-script` runtime as well, download
`dist/index.js` from the commit pinned in `workflow-lockdown-guard.yml` into a
task-specific scratch folder and pass its absolute path as
`COPILOT_REVIEW_ACTION_BUNDLE` to that same command. The opt-in test uses
synthetic read/write tokens and an in-memory HTTP adapter; it makes no live API
requests or review writes. This catches bundled dependency/import differences
that an injected unit-test client cannot reproduce.

CI first runs `tools/prepare_bridge_contract_evidence.py` against its explicitly
selected repository URL. If the exact HTTP-47 capture checkpoint is absent, this
bounded preparation retains it without moving a branch or changing capture
evidence. Full ordinary history does not include every squash-orphaned commit.
Missing or malformed checkpoints fail rather than waiving closure validation.
The targeted tests cover existing and missing objects, exact remote/revision
selection, failed preparation and a real unreferenced Git commit.

The CI agent-bridge contract guard (`tools/check-agent-bridge-contracts.py`)
requires Git 2.45+ for enforced `GIT_NO_LAZY_FETCH` support; older versions
fail before object reads, including in partial clones. This contributor
validation requirement is stricter than the general installation minimum.
It
uses only local Git evidence. It does not fetch history or access credential
helpers: unavailable historical commits remain opportunistic, while available
commit and content-addressed blob cross-checks remain enforced. Local Git
probes have a 10-second bound and a probe timeout fails the guard. Repeated
object reads are memoized within one validation invocation, with bounded
caches, so fixture fan-out does not multiply identical Git subprocesses. Fetch any
additional history explicitly, using the repository's scoped identity, before
running validation rather than asking the guard to recover it during a push.
The headless-launch guard shares a single file read and AST parse among its
Python rules within each invocation; its rule coverage, syntax-error refusal,
and declarative checks are unchanged. Daemon-only AST analysis runs only in
the adopting plugins' source trees where that rule applies; all other Python
rules still scan every production root. Adoption and file eligibility are
recomputed on every invocation. Each AST visitor resolves its bound dispatch
method once per node type and file, retaining the same traversal and scope
handling without repeated dynamic method lookup.

`tools/run-plugin-tests.py` builds/reuses a cached dev venv per plugin under
`.test-venvs/<platform>/` (git-ignored; uses `uv`, so vendored
`[tool.uv.sources]` path deps resolve) and runs `pytest`:

```bash
python tools/run-plugin-tests.py agent-bridge           # one plugin, full suite
python tools/run-plugin-tests.py --changed              # plugins changed vs origin/main
python tools/run-plugin-tests.py --all                  # every plugin with a suite
python tools/run-plugin-tests.py agent-bridge --guards  # just the fast @pytest.mark.guard checks
python tools/run-plugin-tests.py agent-bridge -k picker # pass-through pytest -k filter
```

For a focused lane, repeat `--test-file tests/test_name.py` with exactly one
named plugin to avoid importing unrelated test modules. The selector accepts
only existing, unlinked Python test files inside that plugin's `tests` tree;
it retains the usual containment and time budgets and fails if no selected
tests run. Combine it with `-k` to preserve a diagnostic lane's exact test
roster without collecting the full plugin suite.

The resident repository-map native lane uses
`agent-worktrees --allow-explicit-tiers --test-file tests/test_repository_map.py
--test-file tests/native/test_repository_map_native.py` with this runner.
Set `REPOSITORY_MAP_DISPATCH_PYTHON` to the separately prepared Dispatch
interpreter for the real cross-distribution catalog case. It uses a private
local server, fresh registry and actual CLI/JSON boundary, with one whole-map
call and no per-repository lookup. Missing that interpreter explicitly skips
only the cross-distribution case. This on-demand native lane does not add a
process matrix to required unit CI or qualify an installed rollout.

Explicit out-of-plugin targets use the same bounded runner and admission lease:
`worktree-manager` selects that product's tests, `payload-invocation` selects
`libs/payload-invocation/tests`, and `runtime-source` selects the source
preparation/identity/native-consumer contracts in `libs/versioned-runtime/tests`.
`agent-procutil` selects the shared process primitives in
`libs/agent-procutil/tests`, including explicit native daemon wait-status controls.
`--all` remains scoped to plugin suites.

Manager's cold canonical-adopter acceptance has an explicit native lane:

```text
python tools/run-manager-runtime-acceptance.py --root .validation
```

It uses the existing process-tree containment supervisor and one 300-second
group budget for fresh/legacy-repair fixture construction plus the assertions.
Fixtures are constructed before pytest's unchanged 30-second item checks;
no constructor, dependency policy, package health or failed-selection assertion
is mocked. The assertions run through `run-plugin-tests.py` with explicit
`tests/native/test_manager_runtime_install.py` selection. The ordinary Manager
suite does not recurse into that prepared native lane. Report both ordinary
orchestration/assembly units and this cold native acceptance before publishing
a Manager installation change; neither is a substitute for the other.
The runner's default file discovery honors the existing
`[tool.pytest.ini_options].norecursedirs` declaration before passing explicit file
groups to pytest. `--test-file` deliberately retains access to the prepared
native lane; those tests still require their native driver's fixture data.
The migration fixture requires Git history containing published pre-engine
Manager `0.6.35-dev1` at `9c7a61518540787498c77a284f640c36bbe7d8e4`.
It executes that release's controller against the new payload, witnesses the
old launcher fail with an interpreter-only selection, then enters the new
bootstrap and reuses the same registered command without `uv` on `PATH`.
This is an isolated handoff proof, not a live-host or live registry-age probe;
missing pinned history refuses the lane rather than substituting new code.
The lane also constructs two same-package, distinct generations, imports both
through their isolated installed interpreters, and exercises a real contained
fixture mux cutover to the source-qualified target. Injected failures before and
after the second selector replacement prove both selector states and runtime
receipts survive, with no failed-update cutover. Fixture mux processes suppress
production breakaway and are terminated/waited within the containing test.

Prepared-source Manager selection/recovery has its own bounded native contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract source
```

It retains the 300-second group and 30-second item budgets without adding cold
builds to the original runtime lane or required PR CI. The source lane uses
an automatically cleaned OS-temporary sandbox, avoiding Windows path-length
failures from source-plus-repair identities below a deep checkout. It constructs
real non-editable same-package source generations. Manager's explicit Dev
self-update acquires a clean standalone fixture's merged `dev` tip using its
own trusted preparer, independently of a different service-source policy.
The first apply runs the actual standalone Python bootstrap adapter under an
isolated base interpreter without site packages. Its cold construction uses the
same four-build group rather than an additional fixture or timeout allowance.
The lane refuses a subsequently dirtied acquisition checkout without changing
the installed selection through both self-update and bootstrap, while an already
qualified exact payload remains reusable without resolving that dirty checkout.
It repairs an intentionally
damaged installed module into a distinct slot, then reuses that exact healthy
repair. Dry-run reports the actual source selection; the damaged slot and its
completion/origin receipts remain unchanged.
Both the repaired interpreter and the restored previous interpreter must resolve
the same fixture-default data root without a root override, report their actual
installed prefix/module/generation, and read the original SQLite witness and
schema version. Config, database bytes/file identity and global command contents
are preserved. This is synthetic compatible-state continuity, not qualification
of production data migrations, the shell bootstrap's download/prerequisite
transport, service-source engine repair or host rollout.

Independent service-source engine repair has a separate native contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract engine-source
```

It uses the same 300-second group and 30-second assertion budgets in an
automatically cleaned OS-temporary sandbox.
The existing account-isolated repair fixture supplies the registered incumbent;
only native Copilot payload acquisition is substituted with its local published
fixture. Manager acquires merged Dev, prepares it through trusted tools, runs
the actual native runtime-only repair and queries the constructed engine's real
inventory. It first constructs the exact source engine, damages an installed
module, then invokes the same pinned native command twice: canonical distinct
repair must preserve the damaged slot, and the next call must reuse the healthy
repair without changing selection. This includes the canonical repair owner's
control/candidate constructions, not an in-place edit or a fake successful
native result. Upstream Dev advances between repair and inventory: the complete
subsequent suite packet must still name the original commit/tree and artifact.
The final suite process is intercepted to check that packet and exact selected
argv; this does not exercise marketplace refresh or the full update portfolio.
The installed interpreter and generated global command execute, the installed
module belongs to the selected non-editable slot, and the original default-root
SQLite witness/schema, config/database bytes and file identity are retained.
Command ownership remains the incumbent's. This is not production-data migration,
consumer cutover, namespaced repair or host rollout evidence.
The coupled mode/recovery units additionally check both enabled and disabled
adopted-Manager recovery through the source-health fallback, CLI parser,
control-process argv and settlement, alongside the existing adopted-Manager
recovery controls. The native runtime-only lane keeps that recovery disabled.

The archived normal-updater consumer has its own explicit Windows contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract updater
```

It prepares the actual Worktrees `1.24.91-dev1` release and broken Manager
`0.6.35-dev1`, then invokes the old payload's normal project `update` command
without force or Manager-bypass flags. A staged-index snapshot is materialized
through the release helpers into a private published-like Git artifact.
Copilot delivery and credential transport are controlled fixture processes;
Git checkout, canonical package construction, the fresh-interpreter handoff,
registered Manager launch and the private Windows Terminal profile writer are
real. The resulting profile must report `verified_current`.
Archived-fixture preparation and normal-update consumption each retain a
300-second containment boundary. A separate 300-second admission/concurrent-caller
phase reuses those constructed fixtures: it checks healthy reuse without source
parsing, unadopted no-op, failed source pin and denied credentials with prior
selection preserved, repair of unexecutable legacy and completed canonical
interpreters, and an in-flight old caller skipping installation followed by
recovery on the next normal invocation.
Pytest items retain their 30-second limit.
This proves the updater migration contract, not release qualification or a
live-host rollout.

Supported Manager removal has its own explicit native contract:

The admitted pure packet also covers batch-custody input refusal in
`tools/promotion_unit_tests/test_manager_uninstall.py`, before filesystem
ownership can be acquired. Real lease contention and removal remain in the
separate functional/native portfolios, not the unit-only CI packet.

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract uninstall
```

It prepares a real standalone payload and canonical installed Manager in a
redirected disposable home, then checks an actual installed daemon start,
health request and graceful shutdown before non-mutating preview, installed-self
refusal, actual external removal, repeated absence and actual reinstall.
The daemon and Installation & Updates list command execute their production
child argv without an inherited bytecode suppression setting; both must leave
the sealed runtime unchanged.
Installed package/payload hashes and origin evidence must match; runtime slots,
selectors and owned frontdoors/registration must disappear while durable-state
bytes remain. No synthetic inventory, constructor mock or manual fixture
deletion substitutes for removal. Initial installed preparation and the
removal/reinstall exercise use separate existing 300-second native groups:
each requires a real cold construction, with a 240-second command bound inside
its group. Ordinary pytest items retain 30 seconds. Other native contracts,
production deadlines and required PR CI are unchanged.
The disposable daemon is caller-contained and fully settled before removal.
This operation does not launch a mux or Copilot, qualify interactive session
survival, or authorize a host uninstall/rollout.
The ordinary Manager suite excludes this prepared lane.
The manual `installation-context-full.yml` portfolio `manager-uninstall`
executes the same owner on a fresh Windows runner with Python 3.12, read-only
repository permissions, exact checked-out source verification and a 15-minute
setup/job envelope. Its native groups retain their 300-second boundaries.
It is not part of required PR CI or the default scheduled portfolio.

Retained mux shell callbacks have a separate, independently contained contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract callbacks
```

This does not replace or split the original ten runtime checks. Each contract
keeps the existing 300-second aggregate budget and 30-second pytest item limit.
The callback contract cold-constructs a real package while recovering an
unchanged selector whose old slot is missing, then executes the actual retained
PowerShell/Bash registration and removal bodies with `uv` absent. It observes
real registry updates/tombstones against a private live rendezvous and an owned
fake engine, not command-string assertions. A distinct contract avoids adding
another cold construction plus consumer subprocesses to the original group,
whose measured native Windows execution is already close to its unchanged bound.
That contract also removes only the selected marker from an otherwise healthy
constructed installation: dry-run must plan repair, then apply must restore the
selection using real installed health and unchanged origin/completion receipts.
An unexpected constructor call fails the test rather than rebuilding silently.
The same prepared installation is reused for empty and whitespace-only selector
repair; it is not rebuilt for each state. `tests/test_manager_selector_states.py`
uses real canonical file publication/recovery for both selector fields, including
faults before/after the second replacement, exact blank-byte/absence rollback,
held runtime/publication custody, and unchanged invalid-name/target refusal.
`tests/test_companion_popup_runtime.py` separately executes the generated Ctrl+K
popup command through the Windows command shell against a synthetic installed
fixture CLI and real retention bootstrap (not a package-construction proof).
It checks paths with spaces, exact Companion argv, the held lease, target prefix,
root binding, stdin/stdout/stderr and exit status without opening a popup/TUI.
Source and legacy entries execute through an owned `uv` stand-in. Since the
PowerShell runtime resolver is shared, also run the real callback contract after
changing it; the original ten-case cold contract need not repeat for popup text
and equivalent resolver extraction alone.
The payload suite's PowerShell contracts require a real `pwsh`; when it is absent
from the isolation container, run that target through the host's contained runner
rather than counting skipped PowerShell contracts as validation.
On native Windows, the generator file's complete portfolio can run in two
complementary bounded selections: `-k "agent_machines"` and
`-k "not agent_machines"`. Account for both test/skip totals; neither alone
is complete coverage. These keep the repeated native governance contracts
within the existing per-run budget without extending any item deadline.
Disposable Worktrees copies retain the complete runtime/dependency graph
where consumed, but do not write bytecode that no later fixture reuses.

For an explicit clean/reprepare without running tests, use the existing runner:

```bash
python tools/run-plugin-tests.py <target> --reinstall --prepare-only
```

This cleans only that target's current-platform test environment in this
checkout, after acquiring its normal lease. A busy environment refuses or waits
under `--admission-wait`; linked cache roots refuse before cleanup. Other
targets, platforms, checkouts, installed runtimes and the global UV cache are
untouched. There is no need to delete `.test-venvs` or purge UV's cache.

On Windows, cache inspection/removal and disposable sandbox cleanup use one
native extended-path spelling so deep private UV wheel paths do not become
false missing-file/nonempty-directory failures. Drive, UNC and already-native
paths retain their targets; POSIX paths are unchanged. This changes neither
machine long-path settings nor ownership: rebuild keeps the leased root,
linked roots/ancestors refuse, child junctions are unlinked without traversal,
and failed cleanup cannot publish a ready dependency fingerprint. Disposable
group cleanup still follows confirmed process reaping; an unreaped scope
retains its sandbox, including overlong files.

> **Cached local dependencies are source-bound, not editable.** Path dependencies
> install as retained copies in `.test-venvs/<platform>/<target>`. The runner
> fingerprints their complete canonical/transitive local source graph, explicit
> companion projects and retained standalone build resources, not just package
> versions or manifests. Changed, added or removed source files trigger a rebuild;
> generated build/bytecode/cache files and unrelated projects do not.
> Both automatic source drift and explicit `--reinstall` forward reinstall to UV,
> so rebuilding cannot silently reuse an old same-version local-path wheel.
> Rebuild cleanup preserves the leased environment directory and unlinks child
> junctions without traversing their targets, including under Python 3.11.
> Native GUI-parent probes capture child diagnostics even when `pythonw -I`
> cannot import the installed dependency API; an opaque exit code is not
> windowless-launch evidence. This also means a shared
> lib's **own** test suite (its top-level `libs/<lib>/tests/`, as opposed to
> any plugin's `tests/`) is never run by this runner at all — it belongs to
> no single plugin. Validate it directly against one specific vendored copy by
> shadowing the venv's stale install on `PYTHONPATH` (the `SSH_MANAGER_WINDOWS_PROXY_TEST`
> example just below does exactly this). See CONTRIBUTING.md's *Hot-patching a
> deployed venv for fast pre-merge iteration* gotcha for the fuller loop,
> including validating a fix against the real deployed CLI (not just this test
> venv) before a PR merges.

Every invocation is contained by default. The runner redirects user, Copilot,
plugin, XDG, and temporary state beneath a per-run sandbox; owns the pytest
process job/group and its ordinary descendants; and enforces budgets at three
time scales: 30 seconds per test, 300 seconds per sequential 25-file sub-suite,
and 900 seconds across the plugin. Each sub-suite also defaults to 128
processes, 4096 MiB of process-tree memory, and 2048 MiB of temporary storage.
The pytest group parent is created with mode `0700` on POSIX, so an inherited
shared-write umask cannot expose the fixture namespace.
The runner resolves its temporary parent before allocating that owned sandbox,
so Windows short TEMP aliases do not look like redirected daemon registries.
Daemon-registry redirection and ownership refusals remain unchanged.
PowerShell module discovery uses a sandbox-owned `PSModulePath` by default,
so inherited host modules cannot autoload into a test. Core PowerShell commands
and native application lookup remain available. Only the existing explicit
host-state tier preserves the caller's module path; ordinary contained runs
do not modify the parent environment.
Changed-plugin selection always includes both `copilot-extensions-harness` and
`customizing-copilot`, whose suites inspect other plugins' session-context
contracts. An admission-wrapper change must therefore pass the real whole-suite
hook-composition consumer before merge, not first encounter it in post-merge
CI. This scheduling adds no new policy exception or scanner allowlist.

Temporary-storage accounting uses a fresh `scandir` walk per check and retains
each entry's native metadata instead of issuing repeated path-stat calls.
On Windows, each check has at most two readers and at most 32 directories
per submitted batch; POSIX traversal remains serial. Each invocation measures
growth and removal afresh, without caching totals between checks. Reader
failures propagate, and cancellation or failure waits for active readers to
close before returning so the caller can safely clean up its sandbox.
Symlinks remain excluded; counting and process-tree termination at the storage
ceiling are covered by native containment tests.

Sandbox cleanup requires verified process-tree termination, not just the
command's exit. The POSIX controller reports status through a private pipe
and keeps its PID allocated until teardown; the owner verifies its group and
session identity before each signal and confirms no live members remain.
The controller stays live through TERM grace and is excluded only from the
grace-period member count. An identity-checked final SIGKILL is mandatory even
when an earlier census appears empty; post-kill membership must reach zero.
An independent owner pipe stays open only in the runner. EOF makes an abandoned
controller terminate and then kill its own still-pinned group, both during
command execution and after reporting status; abrupt runner death cannot leave
the controller waiting forever.
Failed membership observation also releases that pipe before waiting for the
controller, allowing its independent reaper to escalate while the original
unreaped error still requires sandbox retention.
Zombies still count toward process admission but cannot hold sandbox files.
Windows teardown explicitly terminates the Job Object and waits for zero
active members before closing its handle. Uncertain termination raises
`UnreapedProcessError`; plugin and standalone runners and the parallel coverage
collector retain their sandbox, detach automatic cleanup and report its path
rather than deleting potentially owned files. Normal exit, deadline and
cancellation retain the same reaping requirement and existing resource limits.
If the environment audit also fails, the unreaped error retains precedence
and reports both failures; an audit exception cannot authorize file deletion.

Full dispatch execution in the CI smoke matrix (push, manual and promotion)
distributes its complete file inventory across three independent jobs.
The PR owner uses two complete/disjoint jobs. Its default sequential process
group is ten files rather than twenty-five: the hosted second assignment
exceeded the unchanged 300-second group budget before completing its last
cases. Each assigned file still runs exactly once, with no marker/filter
change. The runner's explicit file-group limit still wins; other plugins keep
their existing grouping, and all item, group, plugin and job deadlines remain
unchanged.
`tools/ci_plugin_partitions.py` assigns
whole canonical 25-file cohorts round-robin and supplies explicit `--test-file`
arguments to the same contained runner, which may subdivide an assigned cohort
into tighter sequential process groups. Every file belongs to exactly one
partition, and every full-suite partition remains required by the applicable aggregate;
a failed or cancelled partition fails the gate. Local unfiltered runs remain
sequential. Item, sub-suite, plugin and 12-minute job deadlines are unchanged.
This addresses #6052's recurrence where all 5,933 tests passed but aggregate
reporting/cleanup reached the job deadline. Partition-contract tests exercise
the actual runner's file-selection boundary, native helper CLI, complete
inventory and workflow/gate wiring; empty or invalid partitions refuse rather
than silently falling back to a different suite.
These contracts execute in both required native test-runner lanes, independently
of the selected plugin smoke matrix. Their changed-path selectors cover the
partition helper/tests, contained runner/tests and CI workflow, so scheduling-only
changes cannot bypass the complete/disjoint coverage and runner-compatibility
checks.
Promotion qualification derives every required partition name from the helper's
literal `_SMOKE_PARTITIONS` mapping without executing candidate Python. Its
receipt binds the normalized workflow and helper bytes. Missing, unsuccessful,
duplicate or unsupported rows still refuse qualification; both native promotion
contract lanes also select changes to this scheduling helper.
The PR-only coverage-selected dispatch lane below instead requests one matrix
row through `--single-job-plugin agent-dispatch`. Selection and its evidence
artifact run once, not once per complete-suite partition. The explicit matrix
override leaves every other plugin and the default full partition policy intact.
Qualification requires the complete canonical execution step, including its
partition environment and file selection. Appended collection/filter flags,
shell failure masking and changed partition selection refuse qualification.

Agent-index's fleet-ingress tests select the same-checkout `fleet-manager[auth]`
distribution explicitly during development preparation. Its runtime metadata
keeps an ordinary versioned optional dependency, not an escaping standalone
project path or a vendored Gateway copy. The runner fingerprints that project's
dependency metadata and uses an editable test install so source changes remain
visible. The canonical dependency-seasoning adapter retains the shared
`libs/versioned-runtime` build resources needed by that standalone distribution,
just as standalone test preparation does; it does not bypass policy or build
from a mutable source fallback.

The independent `libs/docker-resource-producer/tests` suite uses only a fake
Docker process and temporary JSON state, never a live daemon. Required PR guards
select it when that library, its selector, promotion tool/tests, or the CI workflow changes, and run it
on the declared minimum Python 3.10 with bounded per-test and step timeouts.
The same selection runs the canonical package's focused promotion-compatibility
regression on guard Python, before minimum-runtime setup.
Minimum-runtime dependency provisioning uses the canonical seasoned UV adapter.
The library also declares the native seven-day UV cutoff for direct consumers.
Minimum-runtime setup uses its explicit interpreter output without replacing
the guard job's Python 3.12 environment. This catches fractional-timestamp and
optional TLS client-identity compatibility; it is not live Docker lifecycle
acceptance.

On Windows, contained runs also set the installer test-mode contract
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`. Conforming installers virtualize
persistent User/Machine environment reads and writes to Process scope. The
runner separately snapshots both registry environment keys and fails the
sub-suite if it detects drift, so a missed adapter cannot silently alter host
PATH or other persistent environment state. It deliberately does not roll the
key back because another process may have made a legitimate concurrent edit.
Installer subprocesses launched by a direct pytest invocation receive the same
prevention through pytest's inherited `PYTEST_CURRENT_TEST` marker.

Codespaces PowerShell installer regressions additionally use a fixture-only
executable search path and explicit batch exit-code forwarding. Temporary
payloads carry test guards through self-staging and first-use provisioning:
they require the fake uv resolver, reject uv network bootstrap and service
mutations, and expose attempted forbidden effects as assertion failures.
User-root redirection alone does not isolate executable discovery or scheduled
tasks. The signed-venv health/exit-code matrix uses simulated signatures on both
platforms; native signature-policy behavior still requires Windows validation.
Isolated runtime-helper fixtures must copy `versioned_runtime.py` with its
adjacent `versioned_runtime_leases.py` and `versioned_runtime_construction.py`.
Standalone wheel/sdist tests bind every member to canonical bytes and wheel
RECORD; a missing companion must fail rather than yield a partial artifact.

`plugins/agent-index/tests/test_payload_hash_stability.py` copies a cold helper
closure without bytecode caches. Its portable CLI, native Bash role probe and
PowerShell 5/7 construction checks compare every payload file before and after
actual execution, and require real completion evidence. Windows uses Git Bash,
not WSL, for the Bash case. Run it directly with pytest; it needs neither an
Index service nor package downloads.

Dependency fixtures must also preserve source attribution. Stage matching
publisher/project metadata and the declared local library graph at the real
helper location; use the existing release materializers before stamping a
payload rather than retaining development paths that escape its root.
Direct policy API callers attest the fixture creator's fixed repository root,
not a root derived from the requested package argument. Keep admission active:
do not mock ownership discovery or widen trust to make a fixture pass.

The Codespaces module-launch matrix is an opt-in T3 contract:
`python tools/run-plugin-tests.py agent-codespaces --allow-explicit-tiers -k activation_requires_installed_module_launch`.
A focused completed-namespace pre-construction refusal remains in the ordinary
installer suite. For exact-candidate Linux provisioning and forced-update
refusal on a disposable fresh machine, run
`tools/clean-room/run.ps1 -Scenario agent-codespaces-launch-health -HarnessMount <candidate> -NameSuffix <unique> -Then down`.
This agent-free scenario requires no credentials or live CodeSpace and tests
the actual installed `python -I -m agent_codespaces --help` entrypoint.

Agent SSH's cold first-use snapshot provisioning restores real network
dependencies and is an explicit T3 check, not a default fast unit case.
Its pytest item budget remains 30 seconds; an unavailable budget is a failed
or unexercised native lane, never an installation pass. The agent-free
`tools/clean-room/scenarios/agent-ssh-snapshot` lane exercises the exact local
candidate with separate bounded stamp/provision phases, removes its original
source copy, and reads machine-transport from the actual fresh runtime:
`tools/clean-room/run.ps1 -Scenario agent-ssh-snapshot -HarnessMount <candidate>
-NameSuffix <unique> -Then down`. Supply the approved feed explicitly on a
governed host. This Linux-container proof does not claim Windows signature or
live SSH/WSL deployment coverage.

```bash
python tools/run-plugin-tests.py agent-dispatch \
  --test-timeout 20 --subsuite-timeout 180 --plugin-timeout 600 \
  --max-files-per-sub-suite 15 \
  --max-processes 32 --max-memory-mb 2048 --max-temp-mb 512
```

Filtered (`-k`) and guard-only selections run as one contained sub-suite rather
than repeatedly importing file groups that contain no selected tests.
Use repeatable `--test-file <path-relative-to-tests>` with one plugin to avoid
collecting unrelated modules before applying `-k`. Unknown, escaping and
non-file selections fail before environment preparation. The Windows
reconciliation smoke names `test_reconcile.py` and
`test_installer_command_binding.py`, retaining all 18 selected checks and the
120-second budget without collecting the other 10,000-plus tests.

Managed-worktree removal fixtures accept the canonical project loader's
keyword-only overlay selection while retaining synthetic receiving-side
configuration; they never discover installed project or plugin registries.
Both explicit overlay values also run in the required PR guard lane.

The default headless-pool CLI case retains its original unparameterized node ID;
explicit central/local authority cases have separate parametrized IDs. This
keeps a current candidate compatible with an existing coverage selection while
retaining all three real command contracts. Missing requested nodes still fail
collection; they are not silently removed from the selection.

Execution-space registry tests retain the supplied receiving configuration
through the real YAML overlay reader and reject malformed registries. A fresh
Python subprocess also exercises the machine getter's missing-registry path
and verifies that configuration is loaded exactly once. Packaged-wrapper
previews use the actual wrapper manifest and canonical assets, without copying
unrelated runtime sources or reading the checkout's pending changefiles.
Native landing tests exercise explain and finalize separately against the same
repository scenario, retaining both real Git contracts within each item budget.
The landing fixture isolates the unrelated CodeSpace-claim warning; that
provider adapter has its own dedicated tests.

Agent-worktrees installer command resolution, pipefail execution and POSIX
background-prune tests are explicit T3 native contracts. Collection never probes
Bash or PowerShell. A lazy session fixture admits native shells only after
`--allow-explicit-tiers`; direct pytest also requires
`COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS=1`. Windows admission prefers Git
Bash and rejects WindowsApps/System32 WSL launchers before executing anything.
Fallback lookup removes those launcher directories before searching PATH, so
an earlier WSL shadow cannot hide a later portable Git Bash.
The exit-code, pipefail and Windows cygpath probes run once per admitted session,
headlessly with five-second bounds. Native semantic tests and path conversion
remain real; ordinary tests mock the admission policy and forbid process
creation during fresh imports. The pipefail text drift guard remains default.

```powershell
python tools\run-plugin-tests.py agent-worktrees -k native_shell_collection
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers -k 'installer_copilot_resolution or installer_pipefail or invoke_payload_runtime_posix'
```

### Shell-first update: real local Git acceptance

Run this explicit native lane under a process-tree supervisor (a Windows Job
supervisor on Windows), never raw pytest:

```text
python tools/run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests/native/test_update_entry_local_git.py --test-timeout 45 --subsuite-timeout 180 --plugin-timeout 240
```

The lane creates private temporary roots and a real bare Git repository holding
only harmless `worktree-manager/bootstrap.sh` and `.ps1` fixtures. Its ordinary
Manager `config.toml` selects that source with a `file://` URI and exact ref;
there is no source-selection bypass. Fixture Git objects are deterministic:
no `git commit`, identity lookup, publication, SSH or network source is used.
The generated project launchers and installed shared launchers are unmodified.
POSIX executes the actual shell entry; Windows executes both actual project
`.cmd` and `.ps1` entries with missing and failing old runtimes.
When an eligible Git Bash is installed, Windows also exercises the existing
global sh binstub with its real Windows `provider_root` registration. That
entry must select the native PowerShell early path before POSIX root parsing.
Unavailable/unsupported native shells are reported as skipped, not positive
platform acceptance.

Success means the freshly fetched fixture records exact argv and exits **73**,
with no old resolver/interpreter/Manager marker, no installer staging, and no
remaining per-attempt scratch. It is deliberately **not** whole-suite update
success. Native Windows execution and console/foreground observation remain
separate acceptance evidence. The `tests/native` directory is excluded from
default discovery and required unit promotion; collect it explicitly as above.

The keyed workspace-creation regressions have an opt-in native companion.
It runs the real create parser/handler, Git allocation, tracking writer, and
creation journal in a fresh synthetic owner, then replays from a second Python
process. No Copilot session or host registration is created. Run it with:

```powershell
$env:AGENT_WORKTREES_CREATION_NATIVE = '1'
python tools\run-plugin-tests.py agent-worktrees -k creation_requests --test-timeout 120
python tools\run-plugin-tests.py agent-bridge -k workspace_creation --test-timeout 120
```

The bridge companion uses the same fresh-owner fixture across real create,
exact-record/head reads and explicit `bind-session` subprocess calls. It also
exercises a real dispatch queue's lost allocation response, restart and changed
routing, proving one execution checkout despite a different queue lane. Bridge
SessionManager/SQLite and durable first-turn admission are real; Copilot ACP/
provider I/O is simulated. This is connected first-touch evidence, not a
deployed binstub, full host installation or live model transcript check.

Native conversation-continuity probes should use the normal worktree launcher
and Bridge CLI control when exercising the complete ownership path. A deliberately
manual mux/TTY probe must establish folder trust before waiting for model output:
use the owner's `permissions.add_trusted_folder()` against the fixture's actual
Copilot configuration, or explicitly handle the one-session trust prompt with a
known key sequence. An isolated `COPILOT_HOME` must not accidentally leave that
helper writing production configuration. Assert the fixture trust entry before
launch; an unanswered trust prompt is not evidence that native resume failed.

For a cold-resume claim, observe the predecessor process exit, the exact native
conversation ID, and a new response proving remembered context. Use UTF-8 for
captured Windows mux output. Keep backend-only evidence distinct from managed
owner/HTTP acceptance, and close registered fixtures through Bridge control
rather than inventing a second controller. A failed terminal-exit sequence must
preserve the fixture's exact PID/identity for authorized cleanup; it is not a
passing complete lifecycle test.

`plugins/agent-bridge/tests/test_native_worktree_continuity.py` is an opt-in
real-backend acceptance lane (`AGENT_BRIDGE_NATIVE_CONTINUITY=1`). It requires
explicit lifecycle approval (`AGENT_BRIDGE_NATIVE_LIFECYCLE_APPROVED=1`), a private
`AGENT_BRIDGE_NATIVE_ROOT`, the prepared candidate Worktrees interpreter in
`AGENT_BRIDGE_NATIVE_OWNER_PYTHON`, and a nonsecret preferences JSON file named
by `AGENT_BRIDGE_NATIVE_PREFERENCES_FILE`. The preferences contain the operator's
model, effort and context selections, never credentials. Authenticate the
fixture profile through the vendor's normal login before running; never copy a
credential store or export a token. The shared private owner home is reused
across mode cases while each importing Bridge starts with an empty database.
The fixture materializes unchanged source launch assets and explicitly forwards
its owner PATH and Bridge endpoint through the mux environment. Both ACP and
CLI cases use real owner commands, HTTP routes and Bridge turn control, then
assert remembered context, actual idle, exact native identity and no replacement
history. Run through the normal bounded test runner, not against a shared daemon.

Resident list-warm snapshot policy tests keep process calls mocked and verify
exact output shapes, filter/project separation, next-pass freshness, unavailable
classification, and unchanged standalone reads. Their bounded native companion
executes the real warm loop, classify client/server transport and Git classifier
against a fresh divergent checkout; transcript, mux and reclaim enrichment are
modeled separately. The fixture refuses a bypass of the classify transport.
Its cache clock is isolated from the shared standard-library clock, so the
native transport's request deadlines remain real.
Four same-selection shapes retain eight direct Git starts in each of two
unchanged passes, then observe a subsequent dirty edit. Run that lane explicitly:

```powershell
$env:AGENT_WORKTREES_LIST_WARM_NATIVE = '1'
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers -k test_native_resident_warm_two_cycle_git_budget
```

The agent-worktrees native lean-provision and namespace-package health-gate
regressions materialize the real release payload and install it through UV and
the canonical seasoning adapter. Preparation retains registry metadata, wheels,
and isolated-build dependencies in the runner venv's `.bootstrap-uv-cache`. Each fixture copies
that seed into its own temporary root and sets `UV_CACHE_DIR` and `UV_OFFLINE`,
so redirecting HOME/XDG state cannot trigger dependency downloads after the
container disconnects. Missing prerequisite state fails explicitly; prepare
with `tools/run-plugin-tests.py agent-worktrees --prepare-only`.
The lean-provision case uses the same isolated offline cache handoff and checks
the installed resolver and both real launcher entrypoints after construction.
The UV wrapper changes only the staged interpreter's health invocation:
it removes package entrypoints to exercise real namespace rejection, or stalls
that invocation to exercise the installer's process-tree watchdog. Both cases
must reach health before asserting rejection, and retain child stderr when
construction fails. The 60-second installer deadline stays inside the
90-second subprocess and 120-second test bounds.
The stalled descendant/captured-pipe case lives separately in
`test_first_install_bootstrap_watchdog.py` and runs only in the full suite,
not the required guard lane.

Worktree Manager's native viewport regression uses 1,100 live synthetic
worktrees, with 40 visible by default, both without workers and with 1,100
loaded worker rows. It checks Show Hidden and the actual pulse refresh through
the real Textual compositor, bounds formatting by the viewport, retains
one-line option geometry and exercises off-screen selection/content updates,
late worker changes, focus and scrolling. Worker scopes are indexed once per
build, keyed by each pivot's effective scope: account-wide worker rows are
indexed once even in the cross-machine All view; machine-scoped rows stay
separate. Native title, detail and worker rows
remain deferred during incremental repaint too. Native chrome does not build
the data body; deterministic capture resolves its selected window with Rich
styles intact.
The ordinary regression enforces access counts rather than host-speed timing.
For an explicit local 200 ms rebuild/pulse-to-painted check, collect only the
benchmark file and run each shape in its own contained process, matching one
production application. Wait for the default view to paint before measuring a
mode change. Process CPU and GC costs are reported, never subtracted from the
wall-time gate; no GC policy is changed:

```powershell
$target = (Resolve-Path worktree-manager\tests\production_picker\test_native_viewport.py).Path
$ignore = Get-ChildItem worktree-manager\tests -Filter 'test*.py' -Recurse -File |
    Where-Object FullName -ne $target |
    ForEach-Object { '--ignore="' + $_.FullName + '"' }
$env:WORKTREE_MANAGER_RENDER_BUDGET = '1'
foreach ($shape in 'False', 'True') {
    $env:PYTEST_ADDOPTS = '-s -k ' + $shape + ' ' + ($ignore -join ' ')
    python tools\run-standalone-tests.py worktree-manager --prepare
    if ($LASTEXITCODE -ne 0) { throw 'Native viewport budget failed' }
}
```

FINAL stamp regressions use one immutable real-Git template and isolated
per-test copies. The changed-plugin guard lane runs four smoke cases:
serialized stamp/poll persistence, a tracked edit invalidating the stamp,
the production cached-display/removal split, and a retained at-rest claim.
The remaining checkout/obligation matrix runs in the existing full
promotion/scheduled/manual suite, not as an unconditional new PR matrix.
The report-only audit recipe's pure parameter/receipt/path checks and
service-free CLI render smoke also run in the changed-dispatch guard lane.
A non-dry-run CLI kick additionally traverses the real parser/create/client
path against an isolated loopback coordinator without spawning a worker.
Native symlink escape coverage runs where the test environment permits
creating symlinks; a platform refusal is reported as a skip, not a pass.

There is no host-wide test singleton. Each target takes a lease keyed by its
**concrete environment directory's filesystem identity** (`st_dev`, `st_ino`),
held across venv preparation,
dependency-fingerprint rebuilds, `--reinstall`, and all test use. This includes
`--guards`, `--collect-only`, and `--prepare-only`; `--list` neither establishes
nor locks an environment. Different plugins or checkouts run independently
under the unchanged per-job resource and time bounds. Filesystem aliases of
the same environment, including case-insensitive and bind-mount aliases,
contend for the same lease. Managed runners establish the directory before
locking it and preserve its identity through rebuild: only contents are
cleared under the lease, and `uv venv --allow-existing` preserves the root.
The installer environment excludes `UV_VENV_CLEAR`, and a post-creation
identity check detects an unexpected replacement. An absent or unidentifiable
custom root is rejected; there is no lexical fallback lock key.
A multi-target run releases
each target before acquiring the next, so it never holds multiple environment
locks. A conflicting run fails fast with exit code 3 and names the environment
and live holder; `--admission-wait` allows a non-negative, finite, bounded wait
**per target**:

```bash
python tools/run-plugin-tests.py agent-worktrees --admission-wait 900
```

Environment setup failures are recorded per target without aborting later
targets; genuine busy contention retains exit code 3. Junction detection is
guarded for Python versions without `Path.is_junction`.

The former Linux/Windows runner-contract and payload-CWD jobs no longer run
in Source PR/push CI. Their real-process, actual-Git, uv rebuild and native
assertions remain integration source, not eligible units. The standalone
`payload-cwd-contract.yml` retains weekly/manual routing metadata, with its
execution jobs explicitly disabled until a hardened root-tools/native container
route is enrolled. A skipped job is not validation evidence.

`windows-coverage-rotation.yml` reuses its existing schedule/manual entrypoints
for all nine Linux plugin suites through `run_tests_in_devcontainer.py`, with
immutable source selection, execution logs and named failure ownership.
Each Linux job provisions the exact CLI in `tools/devcontainer-cli/package-lock.json`
through the existing configured-registry age/integrity admission helper, with
dependency scripts disabled and a matching `devcontainer --version` health
check. The CLI is job-local, not a global install; Docker and CLI prerequisite
checks remain before the hardened wrapper. CLI readiness alone is not a
container-suite or platform qualification.
Its former hosted Windows execution is explicitly unavailable pending Windows
container capacity. Linux success does not qualify Windows, explicit-tier
families or other unenrolled residuals; `tools/periodic-integration-inventory.json`
records those boundaries under portfolio issue #1303.

Tests may declare `@pytest.mark.portfolio_tier("T0" ... "T4")` and repeatable
`@pytest.mark.effect(...)` markers. The injected policy rejects effects that do
not belong in the declared tier. T3 clean-room and T4 end-to-end families are
skipped unless the caller passes `--allow-explicit-tiers`; target-specific
environment gates still apply. Credential-dependent explicit-tier checks may
also pass `--allow-host-state`; this preserves host credentials and config
roots while keeping temporary/runtime roots sandboxed and removing live Copilot
session and worktree-owner bindings.

The shared `agent-procutil` spawn helper detects contained test runs and
suppresses deliberate Windows Job breakaway and POSIX session detachment.
Production daemon semantics remain unchanged outside the runner. Runtime
survival adapters, including the Agent Bridge Session Host's in-process
`setsid()` / Job setup, consult the same policy. An adversarial test launches a
descendant through the detachment API and proves the containment owner still
reaps it on timeout.

Agent-worktrees' captured-child guard tests run in-process. Its opt-in
`AGENT_WORKTREES_BOUNDED_NATIVE=1` integration requires `--allow-explicit-tiers`
and a quiet Windows desktop: a real consoleless Python parent exercises two
captured venv-interpreter cycles, stdout/stderr and exit status, then a stalled
child with a grandchild. The probe checks descendant teardown, visible-window
creation, cleanup despite explicit descendant breakaway requests, and foreground transitions without
pytest's headless spawn adapter.
The ambient desktop observations intentionally fail rather than excuse an
unattributable window or focus change; run this lane separately from normal
interactive desktop activity.

Agent-worktrees' tree-comparison policy tests also run in-process. The opt-in
`AGENT_WORKTREES_TREE_DIFF_NATIVE=1` lane in `test_git_tree_diff.py` requires
`--allow-explicit-tiers` and constructs isolated fresh Git repositories. It
measures two unchanged divergent-WIP cycles with one and twenty changed
paths, requiring eight direct classifier Git starts per cycle, excluding
fixture setup, with 500 unrelated upstream paths excluded from the actual
lookup output. It also checks dirty/ref changes, Unicode names, replacement
objects, exact ancestor/descendant lookups and differently shaped landed content.
These are direct Git-launch
budgets, not a host-wide spawn/window census or a deployed-daemon measurement.

Large test modules should be split by behavioral contract, not by arbitrary
line count. Within each contract family, prefer one parameterized or
scenario-style test that validates several related observable features over
many process-launching micro-tests that repeat the same setup and failure
boundary.

Tag the behavioral contract a test (or test class) belongs to with
`@pytest.mark.contract("<component>.<behavior>")` — e.g.
`@pytest.mark.contract("agent_worktrees.pr_ops.merge")`. This is attribution,
not policy: unlike `portfolio_tier`/`effect`, nothing rejects a missing or
unrecognized value. Its purpose is to make the contract a test module was
split along an explicit, filterable, greppable fact instead of something only
inferable from a file's name or a docstring — `pytest -m 'contract("agent_worktrees.pr_ops")'`
selects every test attributed to that contract regardless of which file it
now lives in, which is exactly what a future re-split needs: proof that
moving tests between files didn't silently drop or duplicate a contract's
coverage. When splitting an existing oversized test module, assign one
contract per resulting file as you go; retrofitting the marker onto
already-small, single-contract files is not required.

## Optional devcontainer-based isolation (Linux)

`tools/run_tests_in_devcontainer.py` is an opt-in wrapper around the turn-key
runner above that additionally runs it inside a hardened, ephemeral
devcontainer (`.devcontainer/test-isolation/devcontainer.json`, a named alternate config -- never the canonical `.devcontainer/devcontainer.json` root path, which would let a direct "Reopen in Container" pick up an empty, wrapper-only workspace by accident) -- a real OS-level
filesystem/privilege boundary on top of (not instead of) the turn-key
runner's own process-level containment. **Networking is now part of that
boundary for the real test run**: the wrapper first runs a dependency-
preparation pass (`--prepare-only`, which drives the same venv-install path
a real run would WITHOUT ever importing a single test module or
`conftest.py` -- deliberately not `--collect-only`, which still runs
pytest's own collection and would execute that module-level code with
network access) while the container still
has its default outbound reach, then disconnects the container from every
attached network before running the real, now network-disconnected test
pass -- a test can no longer open an ordinary socket to an external or
host/LAN service during actual execution. The filesystem/privilege boundary
exists for the case the turn-key runner cannot cover on its own: a buggy or
adversarial test that escapes process-level containment via an absolute
path write or a privilege a job object doesn't restrict. See
`efforts/active/devcontainer-test-isolation/README.md` for the full design
rationale and the findings (Phase 0) that shaped it.

Requires a seasoned [devcontainers CLI](https://github.com/devcontainers/cli)
on `PATH` and a working Docker daemon. The scheduled lane provisions the locked
CLI described above; it does not assume the hosted image supplies one. Linux only --
this repo's CI already runs a dedicated `windows-latest` job covering the
Windows-specific containment paths described above, so a devcontainer adds
nothing there.

```bash
python tools/run_tests_in_devcontainer.py agent-worktrees
python tools/run_tests_in_devcontainer.py --changed
python tools/run_tests_in_devcontainer.py --all -- -k some_filter
python tools/run_tests_in_devcontainer.py --include-untracked agent-worktrees
```

Everything after the wrapper's own small flag set (`--keep`,
`--include-untracked`; or a literal `--` anywhere in the remaining
arguments) is passed through to `tools/run-plugin-tests.py` *inside* the
container -- with one normalization (a `--base` value that resolves on
the host is rewritten to its resolved commit SHA, or appended when
changed-selection is active and `--base` was omitted entirely, before
the in-container invocation is assembled; see below for why) and two
known exceptions to otherwise-transparent passthrough: `--allow-host-state`
is rejected outright (see below), and a `--max-memory-mb`/`--max-processes`/
`--max-temp-mb` value above the container's own fixed outer ceiling is
rejected outright (see below). The wrapper validates `--admission-wait` before
any container work (even for `--list`) and passes it through unchanged. It
takes no host-wide lease: each invocation has its own container workspace and
environments; the inner runner retains environment-scoped exclusion. The wrapper:

1. Writes a per-invocation copy of `.devcontainer/test-isolation/devcontainer.json` with
   its workspace volume name made unique to this run, creates that volume
   explicitly as a size-bounded (4 GiB), tmpfs-backed Docker volume (not
   the default unbounded local-disk volume), then brings the container up
   via `devcontainer up` -- **never** a bind mount of the host checkout.
2. Copies a point-in-time snapshot of the host checkout into that volume
   (a `tar` file streamed through `docker exec`'s stdin). The working-tree
   files come from `git ls-files --cached` by DEFAULT -- git-tracked files
   only, deliberately NOT every file physically present under the checkout
   and NOT untracked files either: this repository has no blanket
   `.gitignore` rule for `.env`-style config or arbitrary credential
   filenames, so an untracked-but-not-ignored secret file sitting in the
   working tree would otherwise still be copied into a container that has
   outbound network access during its dependency-preparation pass. Pass
   `--include-untracked` to additionally
   include untracked-but-not-gitignored files (e.g. to test a new,
   not-yet-committed file) -- a deliberate, explicit opt-in, never the
   default. `.git` is handled separately and deliberately minimally: rather
   than copying the real git database wholesale (which would carry every
   branch, stash, reflog, and unreachable object -- local-only content
   having nothing to do with the plugin suite being run, into a container
   that can still reach the network during its dependency-preparation
   pass), a `git bundle` containing only the
   object closure of `HEAD` and the `--changed` diff base (`--base`, or
   that runner's own
   `origin/main` default when `--base` isn't passed) is built and cloned
   into a fresh, minimal git directory instead -- this also transparently
   handles a linked worktree's `.git` (this repo's own required flow,
   where `.git` is a pointer FILE naming an absolute HOST path, meaningless
   inside the container) without needing to special-case it. The index is
   then rebuilt from `HEAD` (`git read-tree HEAD`) rather than copied from
   the host -- a copied index can reference a staged-but-uncommitted
   blob that is unreachable from both `HEAD` and the base ref, which the
   bundle would then be missing entirely. The tradeoff: staged state isn't
   preserved as "staged" inside the container, but every modification
   (staged or not) is still visible as an ordinary working-tree difference,
   since the modified file's actual current on-disk content is what gets
   copied in regardless. `config` is always replaced with a fresh,
   credential-free minimal one and `hooks` is always dropped (neither is
   needed for `git diff`/`status`/`rev-parse`, and either could carry
   credential-bearing or otherwise sensitive
   content). Every `git` subprocess call here scrubs ambient
   `GIT_DIR`/`GIT_WORK_TREE`/etc. from its environment first, so a
   contaminated calling environment can't silently redirect it to the wrong
   repository. The host checkout is only ever **read**, never mutated, by
   anything that happens afterward inside the container.
3. Resolves every targeted plugin's venv dependencies first (a
   `--prepare-only` pass -- never imports test code -- run with the
   container's default outbound network reach), then disconnects the
   container from every attached network, strips a passed-through
   `--reinstall` (the prep pass already rebuilt the venv; the real pass
   must reuse it, not rebuild it with no network left), then runs
   `tools/run-plugin-tests.py` for real inside the now
   network-disconnected container via `devcontainer exec` and
   propagates its exit code. A request for only `--list` (which never
   touches a venv) skips both the preparation pass and the network
   disconnect.
4. Tears the container AND its per-invocation volume down afterward (pass
   `--keep` to leave both running for debugging); a failed removal raises
   rather than silently reporting success, and a failed `devcontainer up`
   itself still triggers best-effort cleanup of anything it managed to
   create.

The container itself runs with every Linux capability dropped
(`--cap-drop=ALL`), `no-new-privileges`, a read-only root filesystem with
only `/tmp`, `/run`, `$HOME`, and the size-bounded workspace volume
writable, and hard resource ceilings (14 GiB memory with no extra swap, 4
CPUs, a 512-process PID limit) -- no Docker socket is ever mounted in.
Outbound networking is available only during the dependency-preparation
pass above; the real pytest run is fully network-disconnected (see step 3) --
everything above has been validated against a real container, not merely
asserted.

Because the workspace is a fresh copy rather than the live checkout, an
uncommitted MODIFICATION to a tracked file is included (the copy reads the
working tree's current on-disk content at invocation time, not the
committed blob) -- but a new, never-committed file is NOT included unless
`--include-untracked` is passed, per the tracked-files-by-default policy
above. **Known, accepted residual exposure**: the tracked-files boundary is
about which PATHS are copied, not which bytes -- a secret pasted directly
into an otherwise-tracked file (e.g. a config example) and never committed
is still copied in, since the content read is the live on-disk file, not
the last-committed blob. A clean CI checkout has no such dirty state; a
contributor's local checkout might. This is a deliberate tradeoff (the
wrapper's whole point is testing in-progress, uncommitted changes), not an
oversight -- "tracked" means "this path isn't the kind of thing that
normally carries secrets," never "every byte currently in it is safe." The
wrapper prints an explicit stderr warning naming every dirty tracked file
before building the snapshot, so this residual exposure is surfaced at the
moment it's actually relevant, not only here (and fails closed -- aborts
rather than proceeding -- if that check itself cannot run). The same
residual exposure applies to a tracked file carrying a Git
assume-unchanged or skip-worktree index flag: `git status` deliberately
will not report an on-disk modification for such a path, but the wrapper
still copies the file's real current content. The wrapper separately warns
about any such flagged path before building the snapshot (and likewise
fails closed if that check itself cannot run), so a locally "hidden"
modification doesn't go unnoticed just because `git status` stays quiet
about it. A fresh, per-invocation volume means no state (including prior
test artifacts) carries over between runs.

An unresolvable `--base` is a HARD failure, not a silent degradation, when
changed-selection is actually in play (an explicit `--changed`, or
`tools/run-plugin-tests.py`'s own default when neither `--all` nor an
explicit plugin name is given): that runner's own `changed_plugins()`
quietly reports an empty target set for a bad diff base rather than
erroring, so a typo'd or never-fetched `--base` could otherwise make a run
silently report "No plugin suites to run." instead of the real problem. An
explicit plugin name or `--all` run is unaffected, since neither ever
consults `--base` at all.

A `--base` value that DOES resolve on the host (bare flag, `=value` form,
or an unambiguous abbreviation like `--bas`) is rewritten to its resolved
commit SHA before the in-container invocation is assembled -- this matters
for a ref-relative expression (e.g. `origin/dev~1`): it resolves fine on
the host, but `git bundle create` does not preserve a remote-tracking ref
(`refs/remotes/origin/...`) as a named ref in its resulting clone (unlike
a plain local branch name, which it does preserve), so the unrewritten
expression would otherwise fail to resolve again inside the materialized
snapshot even though the underlying commit object is present. A bare SHA
has no such problem -- it resolves against any clone containing its
object, named ref or not.

One of `run-plugin-tests.py`'s own flags cannot retain its documented
semantics through this wrapper, for a structural reason (a fresh,
credential-free tmpfs `$HOME` per container): `--allow-host-state` is
rejected outright with a clear error (its whole contract is preserving
the caller's real HOME/config/credentials, which this isolation boundary
specifically does not expose) -- run `tools/run-plugin-tests.py` directly
for that case instead. Environment leases remain inside the container:
the wrapper's unique volume prevents a wrapped invocation from mutating
another wrapped or bare invocation's cached environment, so they do not
need to serialize on the host. Each retains its own process/resource
containment.

The standalone runner (`tools/run-standalone-tests.py`) uses the same
environment lease protocol for managed venvs and custom `--python`
interpreters. Standard venv roots are discovered from the canonical containing
`bin`/`Scripts` directory **with `pyvenv.cfg`** or a known managed target,
without executing candidate code. Directory names alone are not proof of a
venv: an opaque wrapper can also live in `bin` or `Scripts`. Known managed
roots remain identifiable while a competing rebuild removes their contents;
custom environments with temporarily missing configuration need an explicit
root. Availability is checked only after the lease is acquired, so
`--admission-wait` covers the missing-executable race.
Nonstandard custom launchers require `--environment-root <actual-env-root>`:
their target cannot be inferred safely without executing them. This explicit
address must name the environment they actually use, so different launchers
sharing it contend. The override requires `--python` and cannot be used with
`--prepare`; it is not an environment-exclusion bypass.
`--prepare` remains restricted to repository `.test-venvs` interpreters;
preparation and test use share one held lease. POSIX venv `bin/python`
symlinks are invoked as venv launchers, not resolved to the base interpreter.

Windows standalone cleanup retains extended-length paths and the unchanged
15-second transient sharing retry. A read-only regular file may have its write
bit restored only inside the runner-owned temporary sandbox before retrying
that same unlink. Shared Git objects require every hardlink to be accounted
for within that sandbox; the link inventory is captured lazily once.
Symlink/junction traversal, outside targets, writable-file denials and
non-unlink failures do not gain a permission-changing fallback. Permanent
cleanup failures remain runner failures, not silently successful test runs.

The complete Manager portfolio can be partitioned without raising its
per-run budget: run `tools/run-standalone-tests.py worktree-manager` once with
`PYTEST_ADDOPTS='-k "production_picker"'` and once with
`PYTEST_ADDOPTS='-k "not production_picker"'`. Account for both test/skip totals;
neither selector alone is a full-suite pass. Keep the dedicated viewport timing
lane isolated as described above.

A second exception, for a different reason: the container itself enforces
FIXED, lower outer resource ceilings (`--memory=14g`, `--pids-limit=512`,
and `/tmp`'s own `size=6144m` tmpfs) regardless of what the inner runner's
own `--max-memory-mb`/`--max-processes`/`--max-temp-mb` flags claim. A
value above the matching outer ceiling would otherwise pass through
unmodified, then be silently preempted by the container at the wrong
moment (an OOM-kill, a hit `ENOSPC` on `/tmp`, or a stalled fork) instead
of the clear, immediate rejection the other exception already gives
-- so the wrapper rejects an over-the-ceiling value outright, before any
container work begins, naming the exact flag/value/ceiling involved.

Every git subprocess the wrapper runs on the HOST (bundling, cloning,
reading the dirty/hidden-flag warnings, etc.) forces
`GIT_NO_LAZY_FETCH=1` and `GIT_NO_REPLACE_OBJECTS=1` in addition to the
repository-selection scrubbing and `GIT_OPTIONAL_LOCKS=0` described above
-- without them, resolving `HEAD`/the diff base in a partial clone could
lazily fetch missing objects INTO the host repository (a host mutation
this wrapper exists to prevent), and a locally configured replacement ref
could silently substitute different history into the snapshot than what
`HEAD`/`--base` actually name.

Inside the container, `uv` is bootstrapped via a pinned-version,
SHA-256-verified direct download of its release tarball (not a bare
`curl ... | sh` pipeline) -- the installed `uv` persists and runs again on
every later `devcontainer exec` with the checkout snapshot already
present and outbound networking reachable, so an unpinned/unverified
installer would have everything it needed to defeat the isolation
boundary. The devcontainer spec also grants the workspace path a `git`
`safe.directory` exemption (via `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/
`GIT_CONFIG_VALUE_0` `containerEnv` entries): the workspace volume's own
mountpoint is always root-owned (nothing inside the container can ever
`chown` it, since `--cap-drop=ALL` drops `CAP_CHOWN` too), and modern Git
refuses to operate inside a working tree it discovers is owned by a
different user -- without the exemption, every git invocation
`run-plugin-tests.py` makes (including its own changed-file diffing)
would fail, and since that script treats a failed `git diff` as an empty
target set rather than an error, it would silently report "no plugin
suites to run" instead of the real problem.


## Local Windows SSH proxy regression

After preparing the isolated `agent-bridge` test environment with the turn-key
runner, the opt-in native smoke test exercises two SSH/proxy cycles from a
consoleless parent with native Windows OpenSSH and, when installed, Git for
Windows. It checks displayed terminal windows and foreground changes using a
synthetic proxy, without remote hosts or credentials:

The windowless parent carries synthetic Win32-OpenSSH socket-descriptor metadata
while its children receive newly redirected handles. Both cycles must reach the
real SSH proxy, and the broker-owned proxy must not inherit that obsolete state.
Portable contracts separately cover ambient, explicit and empty environments,
case-insensitive key removal, retained unrelated entries, caller immutability,
and unchanged POSIX handling.

```powershell
$env:PYTHONPATH = "$(Join-Path $PWD 'libs\ssh-manager\src');$(Join-Path $PWD 'libs\agent-procutil\src')"
$env:SSH_MANAGER_WINDOWS_PROXY_TEST = '1'
& .\.test-venvs\win32\agent-bridge\Scripts\python.exe -m pytest -q libs\ssh-manager\tests\test_proxy_windows.py
```

The portable proxy contracts cover binary forwarding, routing identity, and
timeout/cancellation cleanup and run in the Agent Bridge CI lane.

## Coverage-selected dispatch PRs

Only `agent-dispatch`'s pull-request lane uses coverage selection. Its unchanged
twelve-minute job runs `tools/run-coverage-selected-tests.py`, which reuses
`coverage_guided_selection.decide()` and the trusted main-history pointer /
Release-asset baseline. Push, manual and promotion portfolios remain full;
`agent-worktrees`' separate selection consumer remains shadow-only.
Changes to the dispatch selection, containment, runner or workflow inputs also
nominate dispatch, so the mechanism's own PRs cannot silently skip its execution.

The dispatch lane always unions three sets: exact diff-attributed test nodes,
the existing weighted-set-cover smoke floor (120 seconds of measured baseline
test costs, T0-T2/untiered candidates), and structural `guard` tests. Changed or
new `tests/test_*.py` files run in full too. The smoke budget bounds only that
floor, never the implicated tests or complete invocation. Current debt limits
are 200 intervening commits and seven days. Curation uses compact line bitsets
and lazy marginal-gain ranking, with the same greedy score/cost/node-ID ties
and hard budget as the original algorithm.

Selected execution requires the baseline's actual measured commit. If a
selected test module changed between that commit and the candidate, the
producer adds the whole current module while retaining the originally requested
node IDs. Upstream changes to unselected modules do not expand the selection.
The collector emits repository-relative source keys with portable separators,
including when measured on Windows.

Diff attribution uses the merge-base's **old** changed line numbers, remapping
the earned baseline to that fork point rather than invalidating it against the
replacement being tested. Added lines, unrecognized/binary diff shapes, and
changes outside the attributed source scope remain explicit fallback triggers.
That fallback adds the measured smoke set without removing known implicated
tests, including when age or commit-volume debt exceeds its threshold:
remappable implicated tests remain additive to smoke. Legacy shadow consumers
retain their existing debt fallback. No usable baseline (including an unavailable
Release asset) visibly runs the full/default portfolio; it never means “guards
only” or zero tests.
An empty curated smoke floor is an error even when attributed tests remain.
The pilot CLI also refuses an absent plugin test suite, so full fallback cannot
become a successful no-suite skip.

`--selection-file PATH` on `run-plugin-tests.py` accepts a versioned JSON
manifest, for example:

```json
{
  "version": 1,
  "plugin": "agent-dispatch",
  "nodes": ["tests/test_example.py::test_contract"],
  "files": ["tests/test_changed.py"],
  "include_guards": true
}
```

Use one named plugin and no other selection mode. Paths must identify existing,
unlinked plugin-relative test files; node IDs must match collected items
exactly, including parameter IDs. Missing/renamed nodes, empty files, malformed
or empty evidence fail rather than silently deselecting everything. Selection
diagnostics report total counts and bounded samples; the manifest/decision
artifact retains the complete requested selection. The
portable validator rejects POSIX absolute paths, Windows drive/UNC paths and
drive-relative forms on every platform. Interactive `--test-file` still accepts
native relative Windows separators before normalization.
The contained invocation snapshots the validated manifest, neutralizes ambient and
project `addopts`, and retains the existing isolation and process-tree limits.
Ordinary unselected runs are unchanged. The hardened
`run_tests_in_devcontainer.py` wrapper accepts the same flag: it transfers only
validated manifest data into the disposable container, strips the selection
from dependency-only preparation, then executes it with networking removed.

The `dispatch-selection` artifact records exact nodes, changed test files,
baseline generation, debt, fallback reasons, predicted smoke cost and attributed
line breadth. Console and step summaries contain bounded metadata rather than
repeating the full node lists. Complete invocation timing includes planning, dependency
preparation, execution and cleanup, not just pytest's test timer. Guard and
additional implicated/changed-file costs are not claimed to fit the predicted
smoke-floor cost.

Focused contracts live in `tools/test_coverage_selected_execution.py`,
`tools/test_coverage_fallback_equivalence.py` and
`tools/test_plugin_test_selection.py`. The first file's opt-in
`test_runtime_covered_regression_is_selected_and_fails` (`CGS_RUN_INTEGRATION_TEST=1`)
earns real runtime coverage, proves smoke alone cannot afford the detecting
test, and checks the selected clean/broken/repaired executions. The manual
proof requires `coverage` and `pytest-json-report` in the test environment.
Its private fixture reuses those declared dependencies while executing the real
collector driver and contained runner, rather than resolving packages inside
each test's deadline.

## Test portfolio invariants

Owned-PR rebase publication keeps representative real-Git success, reviewer-race
and pre-push rejection contracts in the default/guard lane. The existing
agent-worktrees changed-plugin gate scopes those checks to affected PRs. The full
head-scheme/publication-flow and refusal matrix is opt-in and owned by the
manually dispatched `PR rebase full` workflow on Linux and Windows:

```powershell
$env:AGENT_WORKTREES_PR_REBASE_EXHAUSTIVE = '1'
python tools\run-plugin-tests.py agent-worktrees -k "owned_pr_rebase or pr_recovery or pr_break_glass or pr_rewrite or pr_publication_state or pr_publish_stale_tip or pr_partial_publish_guard" --allow-explicit-tiers --subsuite-timeout 1800 --plugin-timeout 1800
```

Use the exhaustive mode when changing replay authorization or its publication
adapters; unrelated PRs and ordinary promotion runs do not pay the matrix cost.
The same manual lane covers exact operator-recovery previews, advanced live
heads, backed-sync bookkeeping settlement, immutable source/lease races and
ordinary stale-tip reconciliation against disposable Git repositories. It
requires no live PR, credentials or effectful operator recovery.

Required pull-request CI is a fast regression gate, not the complete test
inventory. Its design target is a wall time below five minutes under normal
runner variance. This is not the runner's timeout: containment budgets such as
the 15-minute per-plugin ceiling remain safety limits for unusually slow or
explicit runs. A suite that cannot fit the required pull-request CI target must
provide a contract-focused smoke lane and move its exhaustive cross-products to
a scheduled or manually dispatched workflow.

- Gate specialized suites by changed paths so unrelated pull requests do not
  pay their cost.
- The existing artifact/toolchain, Python 3.10 compatibility, Windows composed
  artifact and Windows candidate-readiness lanes also cover their source inputs:
  root/nested `.gitattributes`, plugin and shared-library trees (including
  metadata and copied files), declared standalone consumers, changefiles,
  marketplace versions, materializers and their helpers, and canonical copied
  documentation. Both inline artifact selectors in `ci.yml` must agree;
  `tools/test_public_toolchain_source.py` checks positive and unrelated-path
  cases. `tools/test_promotion_python_artifacts.py` exercises the real producer
  hash/materializer/verifier roundtrip with offline wheel-encoding fixtures,
  including LF/CRLF policy and altered source, dependency, bundle and wheel
  bytes. These contracts do not normalize downloaded bytes or replace raw
  archive/wheel digest checks, and do not require an exhaustive portfolio for
  unrelated documentation changes.
- `tools/test_promotion_source_alias.py` uses real Git candidates, squash
  landings and real source-bound archives to prove that an unchanged version
  alias survives both a prior squash merge and a failed Main merge. Signed
  prior-index proof never waives current plugin-source or raw-byte checks.
- Before a workflow-only control activation, run the prepared file explicitly:
  `python tools/test_release_publication_boundary.py --workflow <prepared-workflow> --base-branch-workflow <prepared-admission-workflow>`.
  It executes the literal merge, pause and control-generation admission code
  with bounded provider/Git fixtures. A legacy default controller is explicitly
  skipped, not credited as implementing the new boundary; fixture proof does
  not replace exact-candidate live CI and artifact qualification.
- Rehearse the existing merge-wait portfolio against that same controller with
  `python tools/test_promotion_merge_wait.py --workflow <prepared-workflow>`,
  inside the normal bounded test supervisor. The fixture supplies the original
  qualification owner for successful cases; missing ownership still refuses
  before a provider call. Default legacy-profile results alone do not validate
  a newly preserved Main controller.
- Release-tool compatibility tests retain complete legacy-controller branches
  and execute the prepared source-owned graph when present. Before activation,
  the full tool portfolio must also pass with the actual controlled workflow
  overlays, not only the default Dev YAML. Include workflow-contract test modules
  invoked by native CI cells, such as Fleet Manager's admission/partition guard,
  as well as the release-tool portfolio. Source-policy assertions read the
  recorded Dev recipe; separate actual-controller assertions retain preserved
  admission, required coverage and deadlines. Native source-owned tests use the
  existing second Windows job without increasing either five-minute budget;
  unfiltered file partition assertions guard against lost cases.
- Keep one canonical implementation broad; sample external adapters at genuine
  divergence seams such as parsing, interoperability, security boundaries,
  atomicity, concurrency, and wrapper wiring.
- Preserve an explicit exhaustive mode for adapter implementation changes.
- Reuse bounded processes or in-process APIs when process startup is not the
  behavior under test. Concurrency and process-lifecycle tests must bypass
  pooling so they still exercise real process boundaries.
- When adding a large or subprocess-heavy family, state which lane owns it and
  measure required pull-request CI wall time. Do not make the default lane grow
  merely because the tests are individually valid.

The installation-context foundation follows a reference-versus-adapter model
for its most expensive matrices. The default suite exercises the complete
snapshot-provenance and installation-governance case corpora against the
canonical Python implementation, preferring its importable API when a Python
CLI process would duplicate the same contract. A smaller contract-focused set
keeps Python CLI, POSIX shell, and PowerShell parity, interoperability,
security-boundary, and atomicity coverage. Every exemplar's delegation wiring
is checked; repeated security behavior still spans both exemplar plugins and
both shell styles, while low-risk staged-CWD behavior defaults to the installer
with legacy-discovery support. Source-identity fixtures likewise run exhaustively
in-process against the reference implementation and use representative vectors
for external adapter parity instead of respawning every adapter for every
fixture. Set
`INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1` to restore the full CLI and
exemplar cross-products when changing an adapter implementation:

```bash
INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1 \
  python -m pytest -q libs/installation-context/tests
```

Required PR CI executes the positively admitted unit packet, not
`installation_context_smoke`, native command/diagnostic matrices or a full
foundation suite. Generated payload checks and source guards remain separate
from runtime qualification. The complete installation-context adapter portfolio
runs nightly and on demand through `Installation context full`.

`Installation context native acceptance` retains the explicitly dispatched
Linux/Windows derived-diagnostic mode/pointer matrix and separately selected
PR-owner and Dispatch-delegation portfolios. Native Windows diagnostic cases
use `INSTALLATION_CONTEXT_POWERSHELL=powershell`; an explicitly requested
unavailable shell fails rather than silently supplying evidence. The Windows
derived-diagnostic step propagates both dependency-construction and pytest
failures explicitly. None of these jobs gates ordinary PR or promotion snapshots.

`tools/test_installation_context_ci_scope.py` checks the current unit-only
boundary, explicit manual matrix/selector bounds and real PowerShell failure
propagation. The path detector remains a callable source contract; its presence
does not mean required CI runs the retired native jobs. Source-preserved
diagnostic, command-lifecycle and Vault first-use cases remain available for
isolated integration. The existing periodic inventory records unavailable
Windows container capacity and unenrolled portfolios; source presence, a
disabled job or a passing schema audit is not native qualification. See the
[portfolio boundaries](#test-portfolio-invariants) and
`tools/periodic-integration-inventory.json`.

Source-qualified catalog no-write cases invoke the unchanged PowerShell script
twice in one real, initialized host. Host-only initialization stops CLR startup
profile recording before the full-profile baseline; no product script is warmed.
Each invocation and host shutdown must preserve every file byte, directory, and
modification time. This measures diagnostic behavior, not the separate startup
cache writes made by fresh Windows PowerShell processes.
The owned case then changes the receipt and requires the next emission to
refuse it without writes. Command-binding launcher controls execute the real
isolated bootstrap/helper and separately retain unavailable-interpreter
fallback, helper failure, empty-response refusal, and opt-in stderr-only
tracing. An admitted launcher passes the helper in its first invocation;
helper failures must not rediscover or launch a fallback interpreter.

Run the relevant isolated integration suite when changing a runtime boundary;
required PR CI's unit gate is not that evidence. Fast structural/contract checks
may carry `@pytest.mark.guard` (marketplace + picker integrity, shipped-manifest
contract, binding invariants), but that marker alone does not admit a test into
the unit-only release packet.

## Lint + contract gates (before a push)

```bash
ruff check --select F,E9 <touched .py files>   # fast lint (pyflakes + syntax)
python tools/check-install-contract.py         # runtime-plugin install contract — zero violations
python tools/check-version-consistency.py      # plugin.json / pyproject / marketplace versions agree
python tools/check-marketplace-isolation.py    # report-only legacy installation inventory
python libs/payload-invocation/generate.py --all --check  # generated payload shims match manifests
python tools/sync-installation-context.py --check  # inert exemplar copies match the canonical primitive
python tools/sync-peer-launch.py --check  # shared native peer boundary matches packaged consumers
python -m pytest -q libs/peer-launch/tests  # canonical/vendor packaging contracts
python -m pytest -q libs/installer-readiness/tests  # schema/discovery/graph fixtures
```

## Per-plugin coverage (unit suites)

- **agent-bridge:** transport, sessions, config, CLI, and the **Session Host**
  (framing, reattach/ack/buffering, reap logic, protocol-aware turn boundaries,
  version-mux, host-index persistence). Preference-authority tests require a
  real release-label mutation, exercise the promotion fallback writer and
  materializer against a disposable payload, and reject old receipts after
  executable authority-code changes. Only standalone root-module release
  labels are normalized; the complete staged authority closure remains bound.
- **agent-dispatch:** queue/coordinator/supervisor behavior plus the opt-in
  worktree-focus `sessionStart` kernel (payload cwd authority, Git/config and
  agent-worktrees status-core gates, strict bounded input, exact config shape,
  symlink/reparse and contaminated-Git-environment rejection, exact output,
  process-cwd isolation, and live platform-aware Bash/PowerShell parity).
  The targeted `-k procutil` smoke lane also exercises same-cell sibling
  invocation with disposable real venvs, shipped peer governance/resolvers,
  native argv/stdio fidelity (including Windows PowerShell 5.1 resolution),
  execution-time receipt rejection, legacy parity, and a two-cycle windowless
  Windows parent. POSIX runs additionally require the normal symlinked venv
  interpreter to remain usable. These are bounded contract samples, not a
  duplicate of the exhaustive installation-context adapter matrix.
- **agent-index:** fail-closed effective repository/required-state-root
  activation, gated command/scope contribution, non-mutating CLI admission,
  base-only explicit first-use provisioning, attributed dispatch-managed host
  declaration and interpreter-only launch, unsupported/missing-supervisor
  inertness, and ordered direct-SSH routing parity. Agent-dispatch also consumes
  the shipped declaration/provider in its materialization and launch tests.
  Installer fixture contracts in `test_install_engine_adoption.py`,
  `test_installer_staging.py`, and the two
  `test_installer_uv_editable_lib_preinstall_{sh,ps1}.py` files build explicit
  canonical Dev and self-contained materialized layouts independently of the
  checkout running the tests. The real materializer supplies payload-local
  engine assets and regenerates native human-output declarations after wrapper
  changes. Cell coordinator cases retain the live payload-replacement/no-reentry
  proof in both layouts and shells. Procutil fallback cases remove only their
  copied local procutil directory, assert its absence, and retain canonical
  dependency-policy/private-copy checks; an existing local copy is not evidence
  that fallback ran. Run all four files in source and generated-consumer views.
- **agent-codespaces:** config, lifecycle, resolver, and the credential relay.
  The focused `-k worktrees_peer` lane covers every same-cell worktrees adapter,
  refusal propagation (including claim and auth fallbacks), and cross-context
  cache isolation. The dispatch `-k procutil` lane owns the shared real-process
  proof for both plugins, including the source-only CodeSpaces hook outside the
  repository with a dependency-free disposable bootstrap interpreter.
- **agent-containers:** config, lifecycle, the lease broker, and the resolver.
- **agent-mcp:** config loading, auth injectors, transports, bridge framing, the
  decorator pipeline; the code-mode Node tests skip automatically when `node` is
  absent. The native POSIX cutover integration waits for the exact spawned
  daemon's published control endpoint, not just `active.json` existence:
  conservative custody history may precede publication of the active route.
  The native PowerShell owned-construction cleanup case records fixture-only
  abort/reply/exit stages and Python stacks at eight-second intervals. An
  unchanged 20-second subprocess timeout prints bounded diagnostic readback
  before re-raising the original exception; no deployed helper is instrumented.
- **agent-ssh:** transport rendering, managed OpenSSH fragment source identity,
  tri-state reconciliation, stale/duplicate quarantine, bounded warnings,
  report-only doctor parity, and reachability-vs-hygiene separation.
- **agent-logger:** session segmentation and lookup, local/remote sync targets,
  verified provider-rescue ingestion, generic provenance transport, compaction,
  and background-chronicle source/sink orchestration.
- **agent-worktrees:** a large suite covering worktree lifecycle, the
  status/tracking model, PR flow, activation-preserving installed-inventory
  updates, and the Picker-facing engine contracts.
- **Worktree Manager:** its standalone suite includes the production Textual
  **Picker** UX, golden, cache, pivot, streaming, steering, profile, SSH-source,
  selection, capture, mock, and PNG-validation corpus under
  `worktree-manager/tests/production_picker/`.
  The real-child feed deadline regressions separate process/SSH startup from
  admitted snapshot/update transactions, retain stale rows across transport
  expiry, reconnect to a complete replacement generation, and verify child
  cleanup on cancellation, including partial JSON lines returned after child
  termination. Malformed output without watchdog expiry and explicit terminal
  peer refusals retain no-respawn coverage.
  The configuration-action contracts exercise native forms, Cancel-default
  confirmation, safe argv substitution and visible command failures. For the
  real Workers-to-override acceptance lane, prepare agent-dispatch's test venv
  with `python tools/run-plugin-tests.py agent-dispatch --prepare-only`, set
  `WORKERS_CONFIG_TEST_PYTHON` to that venv's interpreter, then run
  `python tools/run-standalone-tests.py worktree-manager` with
  `PYTEST_ADDOPTS='-k workers_picker_real_override_round_trip'` (add `--prepare`
  only when the Manager test environment needs provisioning). It uses the
  current source and shipped Workers form with an isolated registrar declaration
  and override store: Cancel writes nothing, Disable sets the logical override,
  Enable removes it, and declaration bytes stay unchanged. Ordinary Manager
  runs skip this cross-distribution lane when no interpreter is supplied.
  The original-request recovery lane uses the same opt-in pattern:
  set `COPILOT_TEST_FORM_REQUEST_OWNER_PYTHON` to the existing agent-dispatch
  test interpreter, then run the Manager standalone runner with
  `PYTEST_ADDOPTS='-k native_form_requests'`. Its real owner/CLI tests cover
  both category contributions and rendered Textual forms/global recovery at
  80x24 and 120x40: retain before a lost response, replace the card, reopen a
  client without the row, inspect the original receipt, cancel and separately
  confirm exact retry. Assertions preserve original fields/fingerprint and
  one answer/wake. The owner, storage and provider attribution are isolated
  fixtures; list/subscription refresh is disabled on every fixture runtime,
  including the fresh client, so replay cannot start an ambient board producer.
  No installed registry, live task or worker is used. Ordinary runs
  skip this lane without the explicitly selected dependency interpreter.
- **ai-attribution:** payload-only hook tests covering authoritative
  `sessionStart` payload cwd, malformed/missing payloads, git-repo gating, safe
  defaults, bounded operator/repository config discovery, symlink/reparse
  rejection, repository authority boundaries, normalized guide and
  host-qualified account/remote validation, same-owner cross-forge isolation,
  injection-shaped data, complete JSON control escaping, missing-script
  fallback, setup-skill structure, exact JSON and context size, and live
  Bash/PowerShell parity when `pwsh` is available.
- **delegation-guidance:** payload-only hook tests covering owner/version
  attribution, the 2 KB context budget, direct script fallback, missing-root
  failure-open behavior, skill trigger boundaries, README inventory, and live
  Bash/PowerShell parity when both shells are available.
- **customizing-copilot:** payload-only scanner and plugin-state helper tests
  covering installed inventory vs. user/repository activation, dry-run-safe
  user deactivation, loaded-plugin
  discovery, project/`.claude`/`.ai`/suite agent ownership, Task-disabled
  exemptions, MCP readiness and equivalent fallback checks, origin/version-aware
  external advisories, collision remediation, and counts-only context inventory.
- **context-handoff:** payload-only hook tests covering the owner/version-marked
  continuity kernel, its 2 KB budget, the bounded 3 KB adjacent agent-worktrees
  compatibility catalog, plugin-root compatibility aliases, incomplete-payload
  failure-open behavior, oversized-catalog fallback, and cross-platform kernel
  and catalog semantics; Node tests cover thresholds, configuration, successor
  seeds, storage, prompt-before-session candidate association, retry-safe
  acknowledgement/takeover, payload-local extension-free CLI parity, exact
  plugin-root resolution, and guidance. The identity-free
  `context-handoff-eval` clean-room fixture has a deterministic self-test and an
  opt-in Tier-E run that emits seed/token, turn/tool, timing, fidelity, and
  lifecycle metrics.
- **efforts:** payload-only policy-producer tests covering exact repository
  adoption, authoritative payload cwd, Git/config containment, malformed input,
  symlink/reparse rejection, contaminated Git environments, manifest-derived
  ownership, the read-only cross-repository adoption probe, the 1 KB context
  budget, setup-fallback structure, and live Bash/PowerShell parity when `pwsh`
  is available. Hook registration remains deferred while #1234 prevents
  deterministic multi-plugin context aggregation.

---

## Opt-in end-to-end smoke tests (real infrastructure)

Some flows can only be validated against **live** infrastructure. They are
therefore **opt-in** and take **no defaults**: a target's identity — CodeSpace
names, repos, checkout paths, launch commands — is account- and
environment-specific and must never be hardcoded into a test. Each such module
**skips itself** (naming exactly which variables are missing) unless the caller
supplies the target via the environment, so it **never runs in the default
suite**.

The calling agent/operator is responsible for providing the target and the
account/auth preconditions listed per module below.

### agent-bridge — CodeSpace Session-Host path

**Module:** `plugins/agent-bridge/tests/test_codespace_e2e_smoke.py`

Exercises the real remote stack the unit suite (fakes/monkeypatch) cannot: `gh`
CodeSpace SSH → far-side Session Host bootstrap → the `-L` forward → the
credential relay → ACP over the forwarded loopback — including the reattach
guarantee (adopt the surviving child, not respawn) this session-host work
hardened.

**Required environment (ALL must be set, or the module skips):**

| Variable | Must contain |
|----------|--------------|
| `AGENT_BRIDGE_E2E_CODESPACE` | raw or friendly CodeSpace name (Available/resumable for the active `gh` account) |
| `AGENT_BRIDGE_E2E_REPO` | `owner/repo` the CodeSpace hosts (e.g. `example-org/example-web-codespaces`) |
| `AGENT_BRIDGE_E2E_WORKSPACE` | absolute workspace checkout path **on** the CodeSpace, used as the ACP cwd (e.g. `/workspaces/example-web`) |
| `AGENT_BRIDGE_E2E_ACP_COMMAND` | the far-side shell command that launches copilot in ACP mode, passed **verbatim** (e.g. `cd /workspaces/example-web && copilot --acp --stdio`) |

**Optional (timeouts only — operational, not target identity):**
`AGENT_BRIDGE_E2E_BOOT_TIMEOUT` (default `420`s), `AGENT_BRIDGE_E2E_TURN_TIMEOUT`
(default `240`s).

**Preconditions the caller owns (not asserted by the tests):**
- `gh auth`'s active account **owns** the CodeSpace — the resolver is
  active-account sensitive (the account-flip gotcha), so a wrong active account
  surfaces as "Codespace not found".
- If a turn needs ADO/git, the daemon's credential relay is reachable. The smoke
  turns are intentionally trivial and need neither.

**Run** (with the four `AGENT_BRIDGE_E2E_*` variables exported):

```bash
python tools/run-plugin-tests.py agent-bridge -k e2e \
  --allow-explicit-tiers --allow-host-state
```

The explicit host-state escape hatch is required because these opt-in checks
intentionally use the caller's authenticated provider configuration.

**Flows:**
1. `test_e2e_dispatch_and_single_turn` — cold dispatch reaches `IDLE` with a live
   child pid and runs one ACP turn to a clean terminal stop reason.
2. `test_e2e_reattach_adopts_same_child` — a stop + resume adopts the **same**
   far-side child (pid) and ACP session id — reattach, not respawn — then runs
   another turn to prove the reattached session is live.

> `stop` here is a graceful detach (the analogue of a transport drop); a true
> mid-turn socket sever (tunnel flap) and a "silent long turn survives a drop"
> flow are natural follow-ups once these are validated against a live CodeSpace.

### Adding a new opt-in e2e module

Follow the same contract so it stays safe-by-default and reproducible:
- Gate the **whole module** on its required env with
  `pytest.skip(<reason listing what is missing>, allow_module_level=True)`.
- Take **no defaults** for target identity — read every target value from the
  environment; only *timeouts* may carry internal constants.
- Assert observable end-state, not exact model output (turns vary).
- Document the module and its variables in this file.

## Optional paired-worktree finalization

For paired-worktree finalization, select
`tools/run-plugin-tests.py agent-worktrees --test-file tests/test_paired_claims.py
--test-file tests/test_paired_finalize_integration.py`.
The first file covers claim mutation/refusal and concurrent lifecycle ownership.
The second uses isolated fresh Git repositories and actual CLI refusal paths,
then proves bottom-up sibling settlement, BOTH-finalized removal retention,
and preservation of untracked, modified and unpublished committed knowledge.
These are on-demand filesystem/process controls, not additions to required
unit-only CI or a claim of installed-runtime rollout.
