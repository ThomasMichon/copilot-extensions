# Testing copilot-extensions

How to run the plugin test suites, the fast gates to run before a push, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`docs/pipelines.md`](docs/pipelines.md).

## The turn-key runner

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

> **A cached venv is not editable.** A vendored path dependency (e.g.
> `agent-ssh-manager` under `plugins/<p>/libs/<lib>/`) installs into
> `.test-venvs/<platform>/<p>` as a normal copy, not an editable link —
> editing `libs/<lib>/src/...` does nothing to a suite you run against an
> already-built venv until you pass `--reinstall`. This also means a shared
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
processes, 4096 MiB of process-tree memory, and 2048 MiB of temporary storage:

On Windows, contained runs also set the installer test-mode contract
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`. Conforming installers virtualize
persistent User/Machine environment reads and writes to Process scope. The
runner separately snapshots both registry environment keys and fails the
sub-suite if it detects drift, so a missed adapter cannot silently alter host
PATH or other persistent environment state. It deliberately does not roll the
key back because another process may have made a legitimate concurrent edit.
Installer subprocesses launched by a direct pytest invocation receive the same
prevention through pytest's inherited `PYTEST_CURRENT_TEST` marker.

```bash
python tools/run-plugin-tests.py agent-dispatch \
  --test-timeout 20 --subsuite-timeout 180 --plugin-timeout 600 \
  --max-files-per-sub-suite 15 \
  --max-processes 32 --max-memory-mb 2048 --max-temp-mb 512
```

Filtered (`-k`) and guard-only selections run as one contained sub-suite rather
than repeatedly importing file groups that contain no selected tests.

Every run except `--list` also takes one host-wide admission lease shared by
every checkout and worktree -- including `--guards`, `--collect-only`, and a
bare `--prepare-only` pass, since each still rebuilds/updates the on-disk venv
(and so can rebuild or delete it mid-run via `--reinstall` or a drifted
dependency fingerprint) a concurrent admitted run may depend on. Only
`--list` is exempt, since it returns before that venv-management path is ever
reached. A second run fails fast and names the live holder instead of
competing for CPU, memory, and process slots. Use a bounded wait when joining
an existing queue is preferable:

```bash
python tools/run-plugin-tests.py agent-worktrees --admission-wait 900
```

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
rejected outright (see below). `--admission-wait` is also consulted by
the wrapper itself: it acquires the SAME host-wide lease
`run-plugin-tests.py` would, on the HOST, before any container work
begins (closing the Phase 2 admission-lease gap below), then still
passes the flag through unchanged. The wrapper:

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
for that case instead. `--admission-wait`'s host-wide heavy-test-slot
lease had the same structural problem (its lease lives under
`$HOME`/`XDG_CACHE_HOME`, a fresh tmpfs per container invocation, so
concurrent wrapped runs would otherwise acquire unrelated per-container
leases instead of coordinating against one shared host-wide slot) --
closed in Phase 2: the wrapper itself acquires that same host-wide lease
on the HOST, before any container work begins, and holds it for the
run's entire lifetime, so wrapped and bare invocations correctly
serialize against each other.

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

Required pull-request CI is a fast regression gate, not the complete test
inventory. Its design target is a wall time below five minutes under normal
runner variance. This is not the runner's timeout: containment budgets such as
the 15-minute per-plugin ceiling remain safety limits for unusually slow or
explicit runs. A suite that cannot fit the required pull-request CI target must
provide a contract-focused smoke lane and move its exhaustive cross-products to
a scheduled or manually dispatched workflow.

- Gate specialized suites by changed paths so unrelated pull requests do not
  pay their cost.
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
  version-mux, host-index persistence).
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
- **agent-codespaces:** config, lifecycle, resolver, and the credential relay.
  The focused `-k worktrees_peer` lane covers every same-cell worktrees adapter,
  refusal propagation (including claim and auth fallbacks), and cross-context
  cache isolation. The dispatch `-k procutil` lane owns the shared real-process
  proof for both plugins, including the source-only CodeSpaces hook outside the
  repository with a dependency-free disposable bootstrap interpreter.
- **agent-containers:** config, lifecycle, the lease broker, and the resolver.
- **agent-mcp:** config loading, auth injectors, transports, bridge framing, the
  decorator pipeline; the code-mode Node tests skip automatically when `node` is
  absent.
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
