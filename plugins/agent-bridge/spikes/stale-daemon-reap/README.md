# Stale / duplicate-daemon reap repro (#1612)

Reproduction / validation harness for the **duplicate/stale-daemon** half of
[dotfiles#1612](https://github.com/tmichon_microsoft/dotfiles/issues/1612):
after a crash or an abandoned cutover, agent-bridge left **orphaned / duplicate**
daemons holding `:9280`, and `service start` had *"no clean reap-and-rebind"* so
it never recovered.

## What it exercises

dev297 adopts the shared `single_instance_lease` primitive (#759). This spike
drives its **reconcile-set reaper** — the outside backstop that retires a
service's identified strays down to the single routing-table `active`, never
touching `active` or `self`, fail-soft — against **real** throwaway child
processes (no agent-bridge daemon, no port bind, no network):

* `superseded_pids_from_table` — the pure reader that turns a `zdd.routing`-shaped
  table into stray candidates.
* `reconcile_set_reap` — the policy: spare `active` + `self`, terminate the rest,
  record (never raise) a terminate failure.

## Running the harness

Run from the plugin's dev venv:

```powershell
.venv\Scripts\python.exe spikes\stale-daemon-reap\repro.py
```

`repro.py`:

1. **Discovery.** Spawns one live "new active" and two strays (a `previous` slot
   and a **stale `active`** still recorded on `:9280`), and asserts
   `superseded_pids_from_table` returns exactly the two strays — never the
   genuinely-live active.
2. **Reap.** Asserts `reconcile_set_reap` retires both strays, **spares** the live
   `active` and `self`, actually terminates the stray OS processes, and leaves the
   active alive.
3. **Fail-soft + identity guards.** Asserts a stray whose terminate *raises* is
   recorded in `failed` and **never re-raised** (a stray must never fail a
   cutover), an already-dead pid is skipped, and a live pid a `verify` check
   vetoes (pid-reuse defense) is skipped, not killed.

Exit code 0 = all checks passed. Everything is cleaned up on exit.

## Note — liveness oracle

The library's `pid_alive` is an **OS probe** (`OpenProcess` on Windows) whose
result depends on no handle being held to the target — true for the real,
independent daemons the reaper retires. This harness's stand-ins are its own
`subprocess.Popen` children, so it holds a handle that keeps a killed pid
queryable (a **harness artifact**, not a lib bug). The repro therefore injects the
reaper's documented `alive=` seam with a deterministic `Popen.poll()` oracle (and
confirms deaths via `poll()`), validating the reaper's **policy** directly.
`superseded_pids_from_table` is pure and used as-is.
