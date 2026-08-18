# Elevated session-host launch repro (singleton + worktree class)

Reproduction / validation harness for the elevated agent-bridge session-host
daemon across **both** repo classes:

* **singleton** (`base_repo: true`) -- works the anchor enlistment directly, no
  worktree; launches through a (typically heavy) launch cmd.
* **worktree** -- resolves a fresh worktree, then launches copilot there.

## The bug this guards against

Starting a heavy **elevated** singleton (real-world: `SPO.Core`) returned an ACP
`RequestError.internal_error` -- a "500" to the caller. Root cause: the **local
Session Host readiness budget** was a hard-coded `30s` in
`launch_session_host` and was never wired to config. A cold host-process start
(python interpreter + `agent_bridge` import + child spawn), especially under
elevation with AV real-time scanning of a freshly written venv, can exceed 30s,
so the `LAUNCH_ACP` stage timed out and `new_session` reported `FAILED`.

Secondary noise: inside the elevated sub-daemon the `codespace:` namespace
resolver raised a `RuntimeError` on every agent-list because the
`agent-codespaces` binstub is not on the elevated PATH -- a scary traceback for
a benign "this provider contributes nothing here" state.

## The fix (see the commit)

* `PhasedTimeouts.session_host_ready` (default **90s**), threaded
  `LocalSpawner -> launch_session_host` and constructed from
  `timeouts.session_host_ready` in `session_manager`. Tune it in `config.yaml`
  for a very slow box.
* `CliNamespaceResolver.list()` degrades to `[]` when the provider binstub is
  unavailable **and** there is no in-process fallback. `resolve()` /
  `ensure_ready()` stay strict.

## Running the harness

Hermetic Python driver (no elevation, real copilot, network, or credentials --
uses a fake ACP copilot child), run from the plugin's dev venv:

```powershell
.venv\Scripts\python.exe spikes\elevated-launch\repro.py
```

`repro.py`:

1. Creates two throwaway git repos under a temp dir: `elev-singleton-test`
   (`base_repo`) and `elev-worktree-test` (worktree class), each with a trivial
   `AGENTS.md`.
2. Builds each class's launch shape -- the singleton as a
   `cmd /c launch.cmd` / `sh launch.sh` wrapper (the shape that makes a real
   base_repo launch heavier), the worktree as a direct child argv -- both
   pointing at the fake ACP copilot (`fake_copilot.py`).
3. Drives each through the **real** `SessionManager -> LocalSpawner ->
   launch_session_host` local Session Host path and asserts `session/new`
   succeeds with **no** `internal_error` -- the post-fix expectation for both
   classes.
4. **Regression gate:** re-runs the singleton shape with
   `timeouts.session_host_ready = 0.001` and asserts the pre-fix failure
   signature reappears (`LAUNCH_ACP` readiness timeout -> FAILED session ->
   what `new_session` turns into `RequestError.internal_error`), proving the
   readiness **budget** is the gate.

Exit code 0 = all checks passed. Everything is cleaned up on exit.

### Operator-facing elevated end-to-end (manual)

To validate against the *real* elevated sub-daemon (post-deploy), register the
two repos as elevated agents the way `SPO.Core` is declared -- in
`~/.agent-worktrees/repos.yaml` + `projects.yaml` with `elevated: true`,
`expose_agent: true` (and `base_repo: true` for the singleton) -- ensure the
sub-daemon is up (`agent-bridge` elevated status; headless after first UAC
consent), then drive `agent-bridge send admin:<name> "hello"` for each and
confirm no `internal_error`. Point each agent's launch at `fake_copilot.py` to
keep it hermetic.

