# Testing copilot-extensions

How to run the plugin test suites, the fast gates to run before a push, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`docs/pipelines.md`](docs/pipelines.md).

## The turn-key runner

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
model. Its only explicit local-configuration classifications are the immutable
`RemoteAuth` and `RemoteAcpTarget` settings: unknown auth/transport configuration
fails closed instead of silently ignoring a typo. Subclasses do not inherit
that classification. Remote replay tests also include unfinished historical
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

### Fleet replay-retention boundaries

Native index installer snapshot tests separate dependency-content identity
invalidation from publication ordering and snapshot-local consumer closure.
Every case retains its real PowerShell boundary and existing 25-second child
deadline; engine and library changes are independently covered, not omitted
from the unchanged publication/older-pointer refusal case.

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
reads, reports progress and completes its own task through authenticated HTTP.
It must reject cross-task, fleet-route and signature-tampering attempts, and
observe protected task-file rotation by the trusted supervisor.
Acceptance also waits for the witness's final proof after its post-completion
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

The automatically selected `tools/test_installer_dependency_policy.py` suite
also exercises the deny-local CLI mode without product discovery. Its native
bootstrap fixture runs the real materializer,
removes the original library tree and deliberately corrupts embedded product
identity, then bootstraps a pinned registry wheel into a fresh venv through
real UV. Stricter cutoff, unseasoned artifacts and wrong hashes still refuse.
The ordinary attributed-source path remains covered separately.

Focused policy tests are:

```bash
python -m pytest -q tools/test_dependency_age.py tools/test_installer_dependency_policy.py tools/test_seasoned_npm_install.py
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

The review-request workflow's fast contract uses
`node --test tools/test_copilot_review_gate.cjs`. To exercise its exact script
against the bundled `actions/github-script` runtime as well, download
`dist/index.js` from the commit pinned in `workflow-lockdown-guard.yml` into a
task-specific scratch folder and pass its absolute path as
`COPILOT_REVIEW_ACTION_BUNDLE` to that same command. The opt-in test uses
synthetic read/write tokens and an in-memory HTTP adapter; it makes no live API
requests or review writes. This catches bundled dependency/import differences
that an injected unit-test client cannot reproduce.

The pre-push agent-bridge contract guard (`tools/check-agent-bridge-contracts.py`)
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
and declarative checks are unchanged.

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

Explicit out-of-plugin targets use the same bounded runner and admission lease:
`worktree-manager` selects that product's tests, and `payload-invocation` selects
`libs/payload-invocation/tests`. `--all` remains scoped to plugin suites.
The payload suite's PowerShell contracts require a real `pwsh`; when it is absent
from the isolation container, run that target through the host's contained runner
rather than counting skipped PowerShell contracts as validation.

> **A cached venv is not editable.** A vendored path dependency (e.g.
> `agent-ssh-manager` under `plugins/<p>/libs/<lib>/`) installs into
> `.test-venvs/<platform>/<p>` as a normal copy, not an editable link —
> editing `libs/<lib>/src/...` does nothing to a suite you run against an
> already-built venv until you pass `--reinstall`. That explicit option also
> forwards reinstall to uv, so recreating the venv cannot silently reuse an old
> same-version local-path wheel. Ordinary cached runs keep their existing policy.
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

The agent-worktrees namespace-package health-gate regression uses a minimal
payload containing the real installer and slot/resolver helpers, with fake uv
and a real isolated Python namespace import. It retains staging and activation
rejection without copying or fingerprinting unrelated runtime sources as the
plugin grows. The installer's 10-second process-tree watchdog runs inside a
15-second subprocess bound and the unchanged 30-second per-test budget.
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

The existing Linux/Windows runner-contract CI jobs are path-gated to runner
sources, supporting libraries, tests, and their workflow/documentation. Required
PR/push runs exclude the `runner_exhaustive` marker, retaining representative
real-process contention, cleanup, missing-executable, and uv rebuild checks.
The same jobs run every marked scenario on `workflow_dispatch`, with no
additional matrix. `tools/test_environment_admission.py` is present in both
platforms' selectors.

Those native jobs also host the existing focused payload-CWD smoke for relevant
dev PRs. `tools/test_payload_cwd_ci_scope.py` proves their checked-in selectors
and executes the actual changed-path Bash step against real Git fixtures,
including invalid refs and unrelated base advances. The standalone
`payload-cwd-contract.yml` retains non-dev PR coverage and its weekly/manual
full matrix; this consolidation adds no job or check and leaves both native
job budgets at five minutes.

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

Requires the [devcontainers CLI](https://github.com/devcontainers/cli)
(`npm i -g @devcontainers/cli`) and a working Docker daemon. Linux only --
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

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins\agent-bridge\libs\ssh-manager\src'
$env:SSH_MANAGER_WINDOWS_PROXY_TEST = '1'
& .\.test-venvs\win32\agent-bridge\Scripts\python.exe -m pytest -q libs\ssh-manager\tests\test_proxy_windows.py
```

The portable proxy contracts cover binary forwarding, routing identity, and
timeout/cancellation cleanup and run in the Agent Bridge CI lane.

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
- Release-tool compatibility tests retain complete legacy-controller branches
  and execute the prepared source-owned graph when present. Before activation,
  the full tool portfolio must also pass with the actual controlled workflow
  overlays, not only the default Dev YAML. Native source-owned tests use the
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

Pull-request CI runs the `installation_context_smoke` contract lane only when
the installation-context foundation, its synchronization tool, or an adopter's
vendored copy changes. The complete exhaustive portfolio runs nightly and on
demand through the `Installation context full` workflow. This keeps unrelated
pull requests incremental and keeps the required fast lane independent from the
size of the long-form regression portfolio.

Native Windows diagnostic evidence runs in the separate five-minute
`Installation-context native diagnostics (Windows)` job. It runs actual `status` and
`probe-legacy` CLI calls, existing/absent pointer checks, and mutation-entry
guards from `test_installation_mode_governance.py`; it is not inferred from
the job merely finishing successfully. The step has a two-minute budget,
uses Windows PowerShell 5.1 via
`INSTALLATION_CONTEXT_POWERSHELL=powershell`, and shares the
`tools/installation_context_ci_scope.py` path detector with Linux. Canonical
and derived implementation changes trigger it; unrelated docs do not.
An explicitly requested unavailable shell fails collection rather than
silently skipping the evidence. No exhaustive adapter matrix is added to PR CI.
Individual diagnostic/wrapper selections retain their two-minute limits.
The separate Windows runner, payload-CWD and Fleet job keeps its ordinary
seven-minute budget; the Linux runner keeps its ordinary five-minute budget.

Native promotion readiness/retry cases have their own five-minute
`Native promotion candidate contracts (Windows)` job. It consumes the governed
artifact builder's changed-path output instead of duplicating detection, and
both jobs gate PR and promotion snapshots. The same native cases and selectors
run without exhausting the builder's cumulative five-minute budget.

Source-qualified catalog no-write cases invoke the unchanged PowerShell script
twice in one real, initialized host. Host-only initialization stops CLR startup
profile recording before the full-profile baseline; no product script is warmed.
Each invocation and host shutdown must preserve every file byte, directory, and
modification time. This measures diagnostic behavior, not the separate startup
cache writes made by fresh Windows PowerShell processes.

Run the relevant suite yourself before pushing a runtime change — there is
intentionally **no** automatic push/PR gate. Fast structural/contract checks are
marked `@pytest.mark.guard` (marketplace + picker integrity, shipped-manifest
contract, binding invariants) so `--guards` runs them in sub-second-per-plugin.

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
