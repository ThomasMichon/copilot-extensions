---
name: agent-bridge-troubleshooting
description: >
  Diagnose agent-bridge CodeSpace dispatch failures without destroying evidence
  or changing shared state by default. Covers expected disconnect-and-resume,
  ACP resume hangs, 409 starting/not-idle errors, protocol disconnects,
  mid-turn aborts, credential-relay auth flaps, persisted traces, split-brain
  checks, agent-bridge peek, and operator-authorized remediation. Use for
  "agent-bridge session stuck/wedged", "resume hang", "dispatch stuck in
  starting", "send returns 409", "agent-bridge protocol error", "session
  aborted mid-turn", "connection closed", "restart agent-bridge", "codespace
  can't push", "relay unreachable", "unable to get password", or
  "diagnose an agent-bridge dispatch".
---

# Troubleshooting agent-bridge CodeSpace dispatch

Use the exact `argv` prefix from the agent-bridge session command catalog for every
interactive bridge operation below. Replace
`<agent-bridge catalog argv prefix>` with its shell-ready rendering: quote each
prefix element separately and prepend `&` in PowerShell. Never search `PATH` for a
same-named command. In PowerShell, invoke it as
`& <agent-bridge catalog argv prefix> <args>`. Commands explicitly labeled as
service or provider management boundaries remain literal global-wrapper
invocations. If the catalog is missing, follow the single-installed-payload
fallback in the `agent-bridge` skill and fail on ambiguity.

Diagnose a wedged `agent-bridge` CodeSpace dispatch without discarding the
session, disturbing unrelated work, or erasing the evidence. Two distinct
failure modes -- jump to the matching section:

- **ACP resume-hang** --
  `<agent-bridge catalog argv prefix> send codespace:<name>` (or any `resume`)
  against a **stopped multi-turn** session stalls in `[starting]` and never
  becomes usable. -> *Is this the resume-hang?* below.
- **Credential-relay flap** -- the dispatched agent authors + commits work fine,
  then **cannot fetch/push/PR**: git fails with `unable to get password` /
  `relay unreachable`. -> *Is this the credential-relay flap?* below.

Identify the mode and capture the trace. The resume-hang root cause is the
Copilot CLI startup race
github/copilot-agent-runtime **#13492** (fix **#13494**), originally scoped to
*headed* sessions but it reproduces on **ACP** sessions too. The relay-flap fix
(sticky port + buffered token-fetch + single-owner republish) is tracked in
**#580**.

> The examples use Windows PowerShell (the primary control-plane host). On a
> POSIX daemon host substitute the obvious equivalents: `ss -tlnp` / `lsof -i`
> for `Get-NetTCPConnection`, `ps -ef | grep agent_bridge` for the
> `Get-CimInstance` process query, and `$HOME` for `$env:USERPROFILE`.

## Authority boundary -- read-only first

### Expected disconnect -- peek, then resume the same session

A one-off disconnect caused by a network disruption or by the daemon restarting
during a plugin update is normal transport recovery, not a wedged-session
signature. Preserve the session and continue in place:

```bash
<agent-bridge catalog argv prefix> peek <session>                  # when its current state is unclear
<agent-bridge catalog argv prefix> send <session> "<next prompt>"  # resumes the same persisted session
```

Do not `end` + `create`, start a replacement session, or restart the daemon.
`peek` reads the target's persisted state without launching Copilot, so it is the
safe way for a host agent to distinguish an interrupted client stream from a
still-running, idle, stopped-but-resumable, or genuinely unhealthy session.

### Unexpected resume/protocol behavior -- preserve and file

A resume that fails, remains contradictory, or repeatedly disconnects -- and
any unexpected timeout, status transition, or slow launch -- is **evidence**,
not permission to diagnose or repair the system. If the operator did not ask for
diagnosis, preserve the session, report/file the already-visible evidence, and
stop. When the operator does request diagnosis, stay read-only unless a mutating
step is separately authorized:

- inspect only (`status`, bounded `read --tail`, `peek`, persisted events/logs,
  routing/health, and exact process listings);
- preserve the existing bridge session and remote child;
- do **not** `stop`, `end`, recreate, or start a replacement session;
- do **not** restart/update/reinstall the bridge daemon, kill processes, stop/start a
  CodeSpace or provider, or edit `active.json`, `relay-port`, session databases,
  or other runtime state;
- report the evidence and file a bug in the owning tracker.

In particular, **never restart the shared bridge daemon just to clear a
session/protocol error**. Daemon-touching commands already self-heal a genuinely
down service, while a restart can disrupt unrelated sessions and hide the
original failure. The remediation sections below are reference procedures to
use only after explicit authorization; they are not the default next step.

## Is this the resume-hang? -- the signature

All of these together mean it's the race, **not** a broken CodeSpace or bad plugins:

- `send`/`resume` of a **stopped, multi-turn** session goes `[starting]` and
  **does not reach `[idle]` within the normal client/startup window**. Any
  multi-minute resume/create delay is a failure signal to trace, not normal or
  "known" expected latency.
- The `send` fails immediately with **`HTTP 409: Session ... is starting, not idle`**.
- The **CodeSpace is healthy** -- a direct `agent-codespaces ssh <name>` works <!-- marketplace-isolation: allow provider-management -->
  throughout (the diagnostic SSH path skips plugin injection, which is *why* SSH
  succeeds while the `copilot --acp` launch hangs).
- A **fresh `create`** on the same target reaches `[idle]` fine; only *resume*
  hangs. It is intermittent (a generation race).

If SSH itself fails, or a *fresh create* also hangs, it is **not** this -- treat
it as a CodeSpace/transport problem (see the `agent-codespaces:codespaces-lifecycle` skill).

## Operator-authorized remediation -- end + create

Only when the operator explicitly authorizes dropping prior-turn context, a
wedged/stopped ACP session can be ended and recreated:

```bash
<agent-bridge catalog argv prefix> end <session>            # tears down the wedged session (bridge-side)
<agent-bridge catalog argv prefix> create <target> ...      # fresh session-host -> reliably reaches [idle]
```

This trades prior-turn context for a new start; it is not a harmless diagnostic.
`end` deletes the bridge-side session + its `events`, but the
target's own `~/.copilot/session-state/<acp_session_id>/events.jsonl` persists
independently on the CodeSpace (that's what `peek` reads -- see below).

## Decide BEFORE resuming -- payload-local `peek`

`<agent-bridge catalog argv prefix> peek <session|agent>` gives a **Copilot-free** reuse verdict +
context snapshot **without launching `copilot --acp`** (the thing that stalls).
It reads the target session's `events.jsonl` directly over SSH and distills
lifecycle/health, the recent message tail, a tool-call summary, and usage.

```bash
<agent-bridge catalog argv prefix> peek <session>            # human-readable snapshot + verdict
<agent-bridge catalog argv prefix> peek <session> --json     # machine-ingestible
```

A **`risky -- resumed without clean shutdown`** verdict is the resume-stall
signature (a `session.resume` with no clean `session.shutdown`). Use `peek` to
classify and report the session. If the operator authorizes remediation, that
verdict informs the resume-vs-fresh decision.

## The automatic recovery ladder (what the daemon does on its own)

`resume_session` self-heals the race -- do **not** intervene manually while its
recovery ladder is active:

1. **stop -> resume x3** (`_MAX_RESUME_ROUNDS`) with per-round timeouts. Each
   round re-rolls the Copilot launch race against the **same** ACP session, so
   prior-turn **context is preserved**. Recorded as `acp_resume_retry` events.
2. **end + create (last resort)** -- `resume_session(allow_recreate=True)`: after
   the ladder is exhausted, recreate a **fresh** ACP session **in place** (same
   bridge id, context dropped + context/handoff state reset). Recorded as
   `acp_resume_recreated` + `session_state_changed(recreated)`.

`submit_prompt` / `send` **opt in** to the last-resort recreate; an **explicit
`resume`** and handoff paths **do not** (so an explicit resume never silently
drops context). If even the ladder cannot recover, preserve the trace and file a
bug. Use the manual `end` + `create` procedure above only with operator
authorization.

## Pull the persisted trace (diagnosing a fresh recurrence)

The `copilot --acp` child **stderr** (where "Resuming..." / extension messages
print) is **always captured**: INFO during the startup window, DEBUG after.
Look at:

- **`sessions.db` `events`** for the structured markers, newest first:
  - `acp_launch_timeout` -- a launch that stalled (`stderr_tail` included).
  - `acp_child_log` -- persisted child stderr lines.
  - `acp_resume_retry` -- a stop->resume ladder round fired.
  - `acp_resume_recreated` -- the end+create last resort fired.
- **The daemon log** -- the always-logged child stderr lives in
  **`~/.agent-bridge/agent-bridge-err.log`**.

> **Known gap:** for **CodeSpace** targets the child runs in the Session Host on
> the far side, so its stderr does not yet relay into the local `sessions.db`
> `acp_child_log`. For a CodeSpace, read the child stderr on the box, or use
> `peek` (which reads `events.jsonl` directly).

## Operator-authorized mitigations (attack the race at the source)

- **Bump the CodeSpace Copilot CLI past the #13494 fix.** This changes the
  target environment and therefore requires explicit authorization. The race is a CLI
  startup bug; a CLI carrying the fix stops reproducing it.
- **ACP `--no-experimental`** -- an operator-authorized diagnostic that disables
  extensions and sidesteps the extension-load leg of the startup generation
  race.

---

## Is this the credential-relay flap? -- the signature

The dispatched CodeSpace agent **does its work fine** (edits, builds, commits --
all local, auth-free), then **fails only when it needs the remote (ADO/GitHub)**:
`git fetch` / `pull` / `push` / PR creation. Tell-tales:

- Git errors: **`unable to get password`**, **`fatal: Authentication failed`**,
  or a credential-helper line like **`relay unreachable; served git credential
  from short-TTL cache`**.
- It fails **at push/PR time after a long turn** -- the auth-free work is done,
  so it looks like "everything's built but I can't ship it".
- **`agent-bridge service restart` fixes a *standalone* `agent-codespaces ssh` <!-- marketplace-isolation: allow service-provider-management -->
  connection but NOT the in-flight bridge session** -- the classic "contradiction".
  The standalone path is one stable SSH; the in-flight session's reverse-forward
  is the thing that's flapping. (So it is *not* merely the Windows
  no-ControlMaster / ephemeral-port issue.)

Root cause: the CodeSpace credential-relay **reverse-forward** (`-R
<listen>:127.0.0.1:<host_relay_port>`) keeps re-establishing because the **host
relay port is unstable** -- it oscillates (competing publishers / a cutover /
a stale published port), so every git credential fetch that lands in a
re-establish window hits a dead port. Fix tracked in **#580**.

## Operator-authorized remediation -- end + recreate

**There is no non-destructive way to repair an *in-flight* session's relay
reverse-forward.** Don't burn time restarting the daemon or opening warm-up
connections to rescue the running session -- the wedged session's `-R` cannot be
re-established under it, and while it holds the session the CodeSpace is
**claimed** so you can't open a clean path either.

By default, preserve the session, capture the trace below, and file a bug. Only
when the operator confirms the work is durably committed and authorizes context
loss should you end and recreate:

```bash
<agent-bridge catalog argv prefix> end <session>            # frees the CodeSpace claim + stops the flapping monitor
<agent-bridge catalog argv prefix> send codespace:<name> "<low-context task>"   # fresh session binds a stable reverse-forward
```

A **fresh** session resolves the host relay port from the current serving daemon
**in-process** and binds a stable `-R` -- the flap does not recur unless the
churn condition (a cutover mid-session, a stale published port) is still active.
Good low-context validation prompts (they exercise the exact remote-auth path):
**"rebase on latest main"** or **"try pushing your work again"**. Tell the agent
to **STOP and report the exact error if git auth fails** rather than retry-loop.

> If a fresh session **also** flaps immediately, the churn condition is still
> live -- go to *Diagnose the flap* and *Deeper checks* below before re-dispatching.

## Diagnose the flap (persisted, non-destructive)

The reverse-forward activity is captured as `acp_child_log` events + the daemon
log. Count the flaps and read the port churn -- from the daemon log (simplest,
PowerShell):

```powershell
Select-String -Path "$env:USERPROFILE\.agent-bridge\agent-bridge-err.log" `
  -Pattern "reverse-forward|host relay port changed|relay unreachable"
```

...or from the persisted events, as a short Python script (avoids per-shell
quoting of the SQL; run with `python flaps.py`):

```python
import sqlite3, os
d = os.path.expanduser("~/.agent-bridge/sessions.db")
rows = sqlite3.connect(d).execute(
    "SELECT data_json FROM events WHERE event_type='acp_child_log'"
).fetchall()
flaps = [r for (r,) in rows if "port changed" in r]
print("flaps:", len(flaps))
for x in flaps[-8:]:
    print(x[:160])
```

A steady cadence of `host relay port changed (A -> B)` where the port never
settles -- **and the detected port differing from the one it just forwarded to** --
is the signature. A healthy session logs **one** `Credential relay
reverse-forward up ... (-R P:127.0.0.1:P)` and then stays quiet.

## Deeper checks -- split-brain / stale published port

The host relay port is published to **`~/.agent-bridge/relay-port`** and resolved
by `get_live_relay_port()`. Two ways it goes bad:

- **Stale/dead published port.** Compare the file against what's actually
  listening: if `relay-port` names a port **nothing listens on** while the live
  daemon's relay is on a *different* port, the publication is stale (a cutover
  that didn't republish). Sub-daemons resolve via this file, so they forward to
  a dead port.
  ```powershell
  Get-Content "$env:USERPROFILE\.agent-bridge\relay-port"          # published port
  Get-NetTCPConnection -State Listen | ? { $_.LocalPort -eq <that port> }   # is anything there?
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'agent_bridge' | select ProcessId,ParentProcessId,CommandLine
  ```
- **Two relay publishers.** A zero-downtime deploy runs a **supervisor -> worker**
  pair (parent + child both `-m agent_bridge start`) -- that pair is **normal**.
  What's *not* normal is two daemons each **hosting + publishing** a relay (e.g.
  an un-retired generation), which flip-flops the file. `active.json` names the
  canonical serving pid/port; a relay-hosting python that is **not** that pid and
  is **not** its supervisor/worker is the stray. Record a genuinely stray daemon
  in the bug report. Stop an exact PID only as an operator-authorized
  diagnostic/remediation step -- never hand-hack `relay-port` /
  `current-version` / `active.json`.

> **Valid state, not a bug: on Windows the two `python.exe` show DIFFERENT
> interpreter paths.** The supervisor (parent) runs from the versioned-slot path
> `~/.agent-bridge/versions/<v>/Scripts/python.exe`; its worker **child** runs
> from the **base interpreter** `C:\Program Files\Python3XX\python.exe` -- and the
> *base-path child is the one bound to port 9280*. This interpreter-path
> difference is the **normal Windows stdlib-venv launcher redirect**, NOT version
> skew and NOT a stray/global install: a stdlib venv's `Scripts\python.exe` is a
> `venvlauncher.exe` that re-execs `home\python.exe` (from `pyvenv.cfg`) with the
> slot's `site-packages`. The child is still running the **versioned slot's**
> code (`sys.executable` = the slot path, `sys._base_executable` = the base). Do
> **not** kill the `Program Files\Python3XX\python.exe` worker thinking it's a
> rogue global daemon. Confirm it's one daemon: the base-path child's **PPID is
> the slot-path supervisor**, both share the **same start time**, and the slot's
> `pyvenv.cfg` shows `home = C:\Program Files\Python3XX`. (A bare
> `& "C:\Program Files\Python3XX\python.exe" -c "import agent_bridge"` fails on
> purpose -- run *directly* it bypasses the venv; the daemon child reaches the
> slot's packages only *through* the launcher.)

Until the durable fix (**#580**) lands, an operator may choose **end + recreate**
after reviewing the evidence and accepting context loss.

## Operator-authorized mitigations (relay flap)

- Prefer **end + recreate** over daemon restarts -- a fresh session gets a clean
  port; restarting the daemon under a live wedged session does not fix its `-R`.
- If auth is only briefly needed and the session is otherwise fine, a single
  targeted remote op over a fresh **standalone** `agent-codespaces ssh <name>` <!-- marketplace-isolation: allow provider-management -->
  connection re-warms/verifies auth on that one connection (it does **not** fix
  the in-flight bridge session's git -- that still needs end + recreate).

## Windows: daemon won't stay up / "auto-start not configured"

**Signature.** On Windows the daemon keeps ending up down (after a reboot, a
sleep, or a self-update), and/or the installer prints `auto-start ... is not
configured` or a scheduled-task `Access is denied.` warning. Often the daemon is
actually **up on a dynamic port** while `service status` looked wrong on the
legacy 9280 (fixed separately) -- always confirm via the routing table.

**What's fine / by design.**

- The scheduled task is **write-once bootstrap** and is *decoupled from the
  runtime version*: routine `update`s never rewrite it, and the daemon
  **self-heals on demand** -- any daemon-touching command (`send`/`wait`/`read`/
  `agents`/`sessions`) boots a down daemon (persistence-correct detached spawn).
  So a missing/broken auto-start task is a convenience gap, not an outage: the
  next command brings the bridge back. Confirm live state, don't assume "down":

  ```pwsh
  Get-Content ~/.agent-bridge/active.json | ConvertFrom-Json | Select -Expand active
  # then GET http://127.0.0.1:<that port>/health
  ```

**Repairing a broken/never-ran auto-start task (one self-elevating command,
operator-directed only).** A
stale **S4U/boot** task that never launches (`LastTaskResult = 267011` /
`SCHED_S_TASK_HAS_NOT_RUN`) can't be rewritten by a routine non-elevated update
(that's *why* updates leave it untouched). Fix it once with the self-elevating
repair script -- it removes the stale task and registers the clean
**interactive AtLogOn** task, **without** starting an elevated daemon. It
prompts for UAC **only when actually needed** (i.e. an existing **S4U/boot**
task must be removed -- that requires admin); if the task is already a healthy
non-elevated interactive one (or absent), it does its work with **no UAC** and,
when nothing needs fixing, no-ops:

```pwsh
# from a normal (non-elevated) shell -- it raises its own UAC prompt
pwsh -File <plugin>/scripts/repair-scheduled-task.ps1
```

It reuses the existing task's action verbatim (the version-stable
`start-agent-bridge.ps1` supervisor pointer), so only the logon mode changes.
The repaired task starts the daemon at your next logon; meanwhile the daemon
self-heals on demand (any payload-local bridge command boots it), so there's no rush.
Prefer the default interactive AtLogOn task; only opt into headless S4U
(`install.ps1 provision -NonInteractive`, elevated) for an always-on box reached
over SSH/RDP with no persistent interactive session -- and know S4U can silently
fail to acquire the logon token (the 267011 case). (`agent-bridge-elevated` is a
*separate* task for the elevated sub-daemon and is recreated on demand -- leave
it alone.)

## Related skills

- the `agent-bridge` skill -- the CLI/service reference (same plugin).
- `agent-codespaces:codespaces-lifecycle` -- CodeSpace ssh/list/stop/delete +
  rescue when the CodeSpace itself is broken (not the resume-race/relay-flap).
