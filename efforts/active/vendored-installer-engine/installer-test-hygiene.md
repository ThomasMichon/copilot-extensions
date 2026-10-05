# Installer test hygiene sweep

Date: 2026-10-05

## Scope

This sweep focused on installer/snapshot/provisioning-adjacent tests for the
10 plugins queued for shared installer-engine adoption:

- `agent-logger`
- `agent-bridge`
- `agent-vault`
- `agent-ssh`
- `agent-codespaces`
- `agent-index`
- `agent-dispatch`
- `agent-containers`
- `agent-mcp`
- `agent-machines`

The concrete starting point was PR #5228's `agent-logger` conversion, especially
the tests added or churned there (`test_install_binstub.py`,
`test_install_signed_python_probe.py`, `test_install_sre_retry.py`,
`test_install_venv_corruption_retry.py`, `test_scaffold.py`).

## Existing containment convention reused

The repo already has a containment model in `tools/run-plugin-tests.py` +
`tools/plugin_test_containment.py`: per-run sandbox roots for `HOME`,
`USERPROFILE`, XDG roots, Copilot/plugin state, and temp dirs, plus process-tree
containment and per-test/per-subsuite/per-plugin timeouts.

The fixes below reused that model rather than inventing a new one:

- direct installer tests now build their own temp-rooted envs that mirror the
  runner's HOME/XDG/temp isolation more closely, so a direct pytest invocation
  stays contained too;
- real shell/PowerShell/git subprocesses that were still unbounded now have an
  explicit local timeout.

## Findings fixed

### 1. `agent-logger` real git fixtures inherited ambient git-routing state

Files:

- `plugins/agent-logger/tests/conftest.py`
- `plugins/agent-logger/tests/test_scaffold.py`

Before:

- `init_git_repo()` and two follow-on `git` calls in `test_scaffold.py` ran real
  `git` commands against temp repos, but with no timeout.
- Those calls also inherited ambient `GIT_DIR` / `GIT_WORK_TREE` /
  `GIT_INDEX_FILE` / object-dir overrides, so a contaminated caller env could
  redirect a supposedly throwaway repo command at some other checkout.

After:

- Added `git_test_env()` to scrub the repo-redirection env vars while still
  allowing tests to inject deliberate config overrides.
- Added a 20-second timeout to every real `git` subprocess used by the fixture
  and its trust-gate regression tests.

### 2. `agent-logger` snapshot/provision tests only sandboxed part of the env

File:

- `plugins/agent-logger/tests/test_install_binstub.py`

Before:

- The real `stamp` / `provision` / first-use snapshot tests used temp `HOME`,
  `USERPROFILE`, and `LOCALAPPDATA`, but left other stateful roots inherited
  from the caller (`APPDATA`, `PROGRAMDATA`, XDG roots, temp dirs,
  `COPILOT_HOME`, `AGENT_HOME`, `AGENT_LOGGER_HOME`).
- That meant a direct pytest run could still let real installer subprocesses
  touch host caches/temp roots even though the test otherwise looked
  throwaway-local.
- One PowerShell harness subprocess in the same file had no explicit timeout.

After:

- Added `_isolated_install_env()` to mirror the runner's containment roots for
  direct installer subprocesses.
- Routed the real stamp/provision/first-use tests through that helper.
- Centralized `PYTHONHOME` / `PYTHONPATH` / `VIRTUAL_ENV` stripping in the same
  helper.
- Added a 20-second timeout to the PowerShell task-warning harness test.

### 3. `agent-logger` installer-engine harness tests had unbounded local shell runs

Files:

- `plugins/agent-logger/tests/test_install_signed_python_probe.py`
- `plugins/agent-logger/tests/test_install_sre_retry.py`
- `plugins/agent-logger/tests/test_install_venv_corruption_retry.py`
- `plugins/agent-logger/tests/test_install_sync_repo_config.py`

Before:

- These tests execute real local PowerShell/bash harnesses that extract helper
  functions from the installer source. They were stubbed/offline, but a broken
  shell invocation could still wait indefinitely because some `subprocess.run()`
  calls had no timeout.

After:

- Added a 20-second timeout to each unbounded harness subprocess.

### 4. The same missing-timeout pattern existed in `agent-bridge`'s analogous retry harnesses

Files:

- `plugins/agent-bridge/tests/test_install_sre_retry.py`
- `plugins/agent-bridge/tests/test_install_venv_corruption_retry.py`

Before:

- The PowerShell retry harnesses mirrored the old `agent-logger` pattern:
  real local harness process, no timeout.

After:

- Added a 20-second timeout to both harness runners.

### 5. `agent-codespaces` POSIX self-provisioning binstub tests lacked full temp roots and timeouts

File:

- `plugins/agent-codespaces/tests/test_self_provisioning_binstub.py`

Before:

- Real bash binstub invocations overrode only `HOME` and had no timeout.
- The tests disable self-provisioning, so they should exit quickly; if they
  don't, that needs to fail fast instead of hanging.

After:

- Added `_isolated_binstub_env()` to sandbox `HOME`, `USERPROFILE`, XDG roots,
  and temp dirs under `tmp_path`.
- Added a 20-second timeout to each real binstub subprocess run.

## Plugins covered vs. spot-checked

### Covered in detail and changed

- `agent-logger`
- `agent-bridge`
- `agent-codespaces`

### Spot-checked for the same hazard class; no code changes needed this round

- `agent-vault`
  - preinstall-loop harnesses already run in temp plugin dirs with explicit
    30-second subprocess timeouts
- `agent-ssh`
  - installer fallback harnesses already use explicit 30-second timeouts
- `agent-index`
  - preinstall/install-cell installer harnesses already carry explicit timeouts
    and temp-rooted homes where they execute real subprocesses
- `agent-dispatch`
  - install build-artifact scrub harnesses already use explicit 20/30-second
    timeouts
- `agent-containers`
  - installer-adjacent surfaces checked; no matching git/env leakage gap found
    in the bounded pass
- `agent-mcp`
  - preinstall-loop harnesses already use temp plugin dirs and explicit
    30-second timeouts
- `agent-machines`
  - provisioning/lifecycle/two-stage stamp tests already sandbox
    HOME/USERPROFILE/XDG/temp roots and use bounded `communicate()`/`run()`
    timeouts

## Explicitly out of scope for this sweep

- Non-installer behavioral suites that happen to shell out but are not part of
  snapshot/provisioning/install coverage
- A repo-wide conversion of every remaining unbounded subprocess in every test
  file
- Any redesign of the installer tests themselves beyond containment/hang-safety

## Result

The concrete `agent-logger` churn surface now has:

- repo-local git commands pinned to the throwaway repo they created,
- real installer/provisioning subprocesses pointed at temp-rooted HOME/XDG/temp
  state,
- explicit local timeouts on every changed shell/PowerShell/git harness path.

That should make the next adopter conversions less likely to stall on the same
class of test instability, while preserving the real subprocess coverage those
tests are supposed to provide.
