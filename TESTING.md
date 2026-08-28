# Testing copilot-extensions

How to run the plugin test suites, the fast gates to run before a push, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## The turn-key runner

`tools/run-plugin-tests.py` builds/reuses a cached dev venv per plugin under
`.test-venvs/` (git-ignored; uses `uv`, so vendored `[tool.uv.sources]` path deps
resolve) and runs `pytest`:

```bash
python tools/run-plugin-tests.py agent-bridge           # one plugin, full suite
python tools/run-plugin-tests.py --changed              # plugins changed vs origin/main
python tools/run-plugin-tests.py --all                  # every plugin with a suite
python tools/run-plugin-tests.py agent-bridge --guards  # just the fast @pytest.mark.guard checks
python tools/run-plugin-tests.py agent-bridge -k picker # pass-through pytest -k filter
```

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
- **agent-codespaces:** config, lifecycle, resolver, and the credential relay.
- **agent-containers:** config, lifecycle, the lease broker, and the resolver.
- **agent-mcp:** config loading, auth injectors, transports, bridge framing, the
  decorator pipeline; the code-mode Node tests skip automatically when `node` is
  absent.
- **agent-ssh:** transport rendering, managed OpenSSH fragment source identity,
  tri-state reconciliation, stale/duplicate quarantine, bounded warnings,
  report-only doctor parity, and reachability-vs-hygiene separation.
- **agent-worktrees:** a large suite covering worktree lifecycle, the
  status/tracking model, PR flow, and the Textual **Picker**.
- **ai-attribution:** payload-only hook tests covering authoritative
  `sessionStart` payload cwd, malformed/missing payloads, git-repo gating, safe
  defaults, bounded operator/repository config discovery, symlink/reparse
  rejection, repository authority boundaries, normalized guide and
  host-qualified account/remote validation, same-owner cross-forge isolation,
  injection-shaped data, complete JSON control escaping, missing-script
  fallback, setup-skill structure, exact JSON and context size, and live
  Bash/PowerShell parity when `pwsh` is available.

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
python tools/run-plugin-tests.py agent-bridge -k e2e
```

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
