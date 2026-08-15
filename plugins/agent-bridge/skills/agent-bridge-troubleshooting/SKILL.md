---
name: agent-bridge-troubleshooting
description: >
  Diagnose and recover a wedged agent-bridge CodeSpace dispatch. Two failure
  modes: (1) the ACP resume-hang where `send`/`resume` of a stopped multi-turn
  session stalls `[starting]`->`[stopped]` and never reaches `[idle]` (the
  copilot-agent-runtime #13492/#13494 race, extended to ACP); (2) the
  credential-relay flap where a dispatched agent works fine but then cannot
  fetch/push/PR (`unable to get password` / `relay unreachable`) because the
  CodeSpace relay reverse-forward re-establishes on a flapping host relay port.
  Covers the end+recreate workaround (never repair an in-flight session's relay),
  the signatures, `agent-bridge peek`, the persisted sessions.db trace, and the
  split-brain/stale-relay-port check. Use when asked to "agent-bridge session
  stuck/wedged", "resume hang", "dispatch stuck in starting", "send returns 409
  not idle", "codespace can't push / git auth fails / relay unreachable / unable
  to get password", "credential relay died", or "diagnose an agent-bridge
  dispatch".
---

# Troubleshooting agent-bridge CodeSpace dispatch

Recover a wedged `agent-bridge` CodeSpace dispatch fast. Two distinct failure
modes -- jump to the matching section:

- **ACP resume-hang** -- `agent-bridge send codespace:<name>` (or any `resume`)
  against a **stopped multi-turn** session stalls in `[starting]` and never
  becomes usable. -> *Is this the resume-hang?* below.
- **Credential-relay flap** -- the dispatched agent authors + commits work fine,
  then **cannot fetch/push/PR**: git fails with `unable to get password` /
  `relay unreachable`. -> *Is this the credential-relay flap?* below.

Identify the mode, apply the **immediate workaround**, then (optionally) capture
the trace. The resume-hang root cause is the Copilot CLI startup race
github/copilot-agent-runtime **#13492** (fix **#13494**), originally scoped to
*headed* sessions but it reproduces on **ACP** sessions too. The relay-flap fix
(sticky port + buffered token-fetch + single-owner republish) is tracked in
**#580**.

> The examples use Windows PowerShell (the primary control-plane host). On a
> POSIX daemon host substitute the obvious equivalents: `ss -tlnp` / `lsof -i`
> for `Get-NetTCPConnection`, `ps -ef | grep agent_bridge` for the
> `Get-CimInstance` process query, and `$HOME` for `$env:USERPROFILE`.

## Is this the resume-hang? -- the signature

All of these together mean it's the race, **not** a broken CodeSpace or bad plugins:

- `send`/`resume` of a **stopped, multi-turn** session goes `[starting]` and
  **never reaches `[idle]`**, transitioning to `[stopped]` after ~5 min.
- The `send` fails immediately with **`HTTP 409: Session ... is starting, not idle`**.
- The **CodeSpace is healthy** -- a direct `agent-codespaces ssh <name>` works
  throughout (the diagnostic SSH path skips plugin injection, which is *why* SSH
  succeeds while the `copilot --acp` launch hangs).
- A **fresh `create`** on the same target reaches `[idle]` fine; only *resume*
  hangs. It is intermittent (a generation race).

If SSH itself fails, or a *fresh create* also hangs, it is **not** this -- treat
it as a CodeSpace/transport problem (see the `agent-codespaces:codespaces-lifecycle` skill).

## Immediate workaround -- end + create

On a wedged/stopped ACP session, **stop trying to resume it** -- drop the
prior-turn context and start fresh:

```bash
agent-bridge end <session>            # tears down the wedged session (bridge-side)
agent-bridge create <target> ...      # fresh session-host -> reliably reaches [idle]
```

Fresh creates succeed where resume hangs. This trades prior-turn context for a
reliable start. `end` deletes the bridge-side session + its `events`, but the
target's own `~/.copilot/session-state/<acp_session_id>/events.jsonl` persists
independently on the CodeSpace (that's what `peek` reads -- see below).

## Decide BEFORE resuming -- `agent-bridge peek`

`agent-bridge peek <session|agent>` gives a **Copilot-free** reuse verdict +
context snapshot **without launching `copilot --acp`** (the thing that stalls).
It reads the target session's `events.jsonl` directly over SSH and distills
lifecycle/health, the recent message tail, a tool-call summary, and usage.

```bash
agent-bridge peek <session>            # human-readable snapshot + verdict
agent-bridge peek <session> --json     # machine-ingestible
```

A **`risky -- resumed without clean shutdown`** verdict is the resume-stall
signature (a `session.resume` with no clean `session.shutdown`). Use `peek` to
choose **resume vs. fresh** before committing to a dispatch that might hang.

## The automatic recovery ladder (what the daemon does on its own)

`resume_session` self-heals the race -- you usually do **not** need to intervene
manually:

1. **stop -> resume x3** (`_MAX_RESUME_ROUNDS`) with per-round timeouts. Each
   round re-rolls the Copilot launch race against the **same** ACP session, so
   prior-turn **context is preserved**. Recorded as `acp_resume_retry` events.
2. **end + create (last resort)** -- `resume_session(allow_recreate=True)`: after
   the ladder is exhausted, recreate a **fresh** ACP session **in place** (same
   bridge id, context dropped + context/handoff state reset). Recorded as
   `acp_resume_recreated` + `session_state_changed(recreated)`.

`submit_prompt` / `send` **opt in** to the last-resort recreate; an **explicit
`resume`** and handoff paths **do not** (so an explicit resume never silently
drops context). If even the ladder can't recover, fall back to the manual
`end` + `create` above.

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

## Cheap mitigations (attack the race at the source)

- **Bump the CodeSpace Copilot CLI past the #13494 fix.** The race is a CLI
  startup bug; a CLI carrying the fix stops reproducing it.
- **ACP `--no-experimental`** -- disables extensions, which sidesteps the
  extension-load leg of the startup generation race.

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
- **`agent-bridge service restart` fixes a *standalone* `agent-codespaces ssh`
  connection but NOT the in-flight bridge session** -- the classic "contradiction".
  The standalone path is one stable SSH; the in-flight session's reverse-forward
  is the thing that's flapping. (So it is *not* merely the Windows
  no-ControlMaster / ephemeral-port issue.)

Root cause: the CodeSpace credential-relay **reverse-forward** (`-R
<listen>:127.0.0.1:<host_relay_port>`) keeps re-establishing because the **host
relay port is unstable** -- it oscillates (competing publishers / a cutover /
a stale published port), so every git credential fetch that lands in a
re-establish window hits a dead port. Fix tracked in **#580**.

## Immediate workaround -- end + recreate, do NOT try to repair in-flight

**There is no non-destructive way to repair an *in-flight* session's relay
reverse-forward.** Don't burn time restarting the daemon or opening warm-up
connections to rescue the running session -- the wedged session's `-R` cannot be
re-established under it, and while it holds the session the CodeSpace is
**claimed** so you can't open a clean path either.

Instead -- the work is almost always already **committed on the CodeSpace**, so
context loss is safe:

```bash
agent-bridge end <session>            # frees the CodeSpace claim + stops the flapping monitor
agent-bridge send codespace:<name> "<low-context task>"   # fresh session binds a stable reverse-forward
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
  is **not** its supervisor/worker is the stray. Only retire a genuinely stray
  daemon with `Stop-Process -Id <pid>` -- never hand-hack `relay-port` /
  `current-version` / `active.json`.

Until the durable fix (**#580**) lands, **end + recreate** is the
reliable recovery.

## Cheap mitigations (relay flap)

- Prefer **end + recreate** over daemon restarts -- a fresh session gets a clean
  port; restarting the daemon under a live wedged session does not fix its `-R`.
- If auth is only briefly needed and the session is otherwise fine, a single
  targeted remote op over a fresh **standalone** `agent-codespaces ssh <name>`
  connection re-warms/verifies auth on that one connection (it does **not** fix
  the in-flight bridge session's git -- that still needs end + recreate).

## Related skills

- `agent-bridge` -- the CLI/service reference (same plugin).
- `agent-codespaces:codespaces-lifecycle` -- CodeSpace ssh/list/stop/delete +
  rescue when the CodeSpace itself is broken (not the resume-race/relay-flap).
