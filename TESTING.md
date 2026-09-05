# Testing copilot-extensions

How to run the plugin test suites, the fast gates to run before a push, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`CONTRIBUTING.md`](CONTRIBUTING.md).

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

Potentially heavy runs (everything except `--guards` and `--collect-only`) also
take one host-wide admission lease shared by every checkout and worktree. A
second run fails fast and names the live holder instead of competing for CPU,
memory, and process slots. Use a bounded wait when joining an existing queue is
preferable:

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
- **agent-index:** fail-closed effective repository/required-state-root
  activation, gated command/scope contribution, non-mutating CLI admission,
  base-only explicit first-use provisioning, attributed dispatch-managed host
  declaration and interpreter-only launch, unsupported/missing-supervisor
  inertness, and ordered direct-SSH routing parity. Agent-dispatch also consumes
  the shipped declaration/provider in its materialization and launch tests.
- **agent-codespaces:** config, lifecycle, resolver, and the credential relay.
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
