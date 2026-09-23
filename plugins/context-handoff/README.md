# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin is intentionally **process-manager agnostic**. It tracks context
pressure, teaches the rules of engagement for continuation, stores durable
handoff batons, and can **signal that a handoff pickup is requested**. It does
**not** spawn successor sessions, inspect mux state, retire panes, or perform
any other cutover choreography itself. Those actions belong to external control
planes such as a worktree manager, agent-bridge, or a human operator.

This plugin ships four cooperating payload pieces:

| Piece | Type | Role |
|-------|------|------|
| **continuity guidance hook** | Declarative `sessionStart` hook | Writes the full owner-marked continuity contract to the exact session folder and emits only `{}` |
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; applies percentage-based soft/hard/**force** thresholds (55% / 70% / 79% by default) with optional repository overrides, delivered on the next idle -- the automatic nudges, the force tier's auto-draft/store/trigger + mutating-tool-call denial, and `trigger_handoff`'s live-cutover signaling are all opt-in (`mode: auto` in `.context-handoff/config.yaml`; the default, `manual-only`, always still stores/seeds a handoff on request, see § Thresholds); provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, and `trigger_handoff` tools plus **`/handoff-continue`**, **`/consume-handoff`**, and the compatibility **`/resume-handoff`** alias |
| **context-handoff skill** | Skill | Owns the `/handoff` workflow: compose the continuation prompt from the extension's structured facts and the agent's live context, decide when to store it, and decide whether to ask or trigger |
| **payload-local fallback CLI** | Node script (`handoff-cli.mjs`) | Extension-free facts, save, trigger, task/file consume, `check-heads` auditing, a safe `retry-cutover` remediation for a superseded session, and a lock/rebase-safe `sync-worktree` (shared with the force-tier path). Invoked by exact verified plugin-root-relative path; it has no PATH binstub or install/runtime step and shares `handoff-core.mjs` with the extension |

## The boundary

`context-handoff` owns **continuity policy and baton storage**:

1. detect context pressure (soft/hard warnings fire under the default
   `manual-only` mode too; always available on request),
2. help the agent compose the right brief,
3. store that brief durably,
4. expose a short recovery seed,
5. signal that pickup is requested (opt-in: `mode: auto` only -- see
   § Thresholds),
6. report whether anything seems to have picked the request up (same
   opt-in scope as 5).

It does **not** own:

- mux / tmux / psmux / Herdr orchestration,
- successor process creation,
- pane retirement,
- PID verification,
- in-process cutover choreography.

If a control plane is present, it can watch the pending handoff state this
plugin leaves behind and perform the actual cutover. If not, the plugin still
returns a short handoff seed a human can use manually.

## Why the monitor is an extension

The live monitor is **only** possible as a session extension. The Copilot CLI
hook surface a plugin normally uses cannot replicate it:

- **No hook input carries token counts.** `session.usage_info` (current /
  limit tokens) is delivered only to the extension SDK via
  `session.on("session.usage_info", ...)`. No `sessionStart` / `postToolUse`
  hook input exposes it.
- **Command hooks cannot inject a turn.** The extension's nudge works by
  queueing a `session.send()` message from the `session.usage_info` handler and
  delivering it on the next `session.idle` boundary. Command-hook output is
  discarded (only `preToolUse` can *deny* a tool call, not inject a message).

So token monitoring and idle-boundary nudges require the extension payload.
The ambient continuity contract does not: it is delivered independently through
the plugin's static instruction pointer plus a declarative `sessionStart` file
writer.

## How the extension is delivered

This is a **plugin-contributed extension**. The Copilot CLI discovers
extensions contributed by **enabled** installed plugins directly from the
plugin's `extensions/` directory. This plugin ships exactly one:

```text
plugins/context-handoff/extensions/context-handoff/extension.mjs
```

There is **no** installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, deploy manifest, or `scripts/install.*`. Enabling the
plugin is the whole setup; the extension activates on the **next** Copilot CLI
session.

## Verify

A session where the plugin hooks loaded receives the full owner-marked
continuity contract in
`instructions/context-handoff/session-guidance.instructions.md` beneath its
exact session folder. The checked-in static pointer instructs the agent to read
that file if present. The hook itself emits only `{}`.

A loaded extension exposes `generate_handoff_prompt`, `save_handoff_prompt`,
`consume_handoff`, and `trigger_handoff`, plus `/handoff-continue` and
`/resume-handoff`; `/extensions` lists it with source **plugin**. It
intentionally does **not** emit a user-visible "Session started" breadcrumb.

## The intended workflow

### 1. Compose and store early

When the session reaches a natural stopping point and you are not yet ready to
trigger (no pickup needed right now):

1. call `generate_handoff_prompt`,
2. compose the full markdown brief,
3. call `save_handoff_prompt`.

That is the routine, safe, non-committal step. It preserves the baton before
context gets tighter, but it does **not** arm pickup or request that any
external system create a successor. **This sequence is not the
context-pressure-driven trigger path** -- when context pressure is rising and
you intend to trigger a handoff now, see section 2 below instead, which syncs
*before* composing so the stored baton reflects the synced state.

### 2. Context-pressure-driven handoff: trigger directly

If the reason for the handoff is **context pressure** and the objective still
has more work left to do, the agent should sync the worktree onto the latest
default branch first (see "Sync before triggering" in the `context-handoff`
skill -- never blanket-commits, and skips cleanly rather than blocking if
anything looks unsafe), *then* compose/save the brief so it reflects the
synced (or un-synced/conflicted) state, then call `trigger_handoff`. This
path does **not** ask for confirmation first.

### 3. Turn-end follow-ups ask before triggering

If the requested work is done and the agent would otherwise end the turn by
listing follow-up ideas or questions, the flow is different:

- **compose + save** the baton,
- **ask the user** whether to continue via handoff,
- only after a brief yes (for example, "sure"), **sync the worktree** (same
  rule as above), then **always recompose and re-save** the baton -- even if
  the sync looked like a no-op, since a WIP commit or a failed sync still
  changes what the successor needs to know -- so it reflects the post-sync
  state, then call `trigger_handoff`.

Only this turn-end follow-up path is skipped by autopilot mode or prior user
pre-authorization.

### 3. Let one session own one slice

When a repository uses both **efforts** and **handoffs**, the combination is
meant to keep sessions well scoped. A single session should not "bite off" an
entire long-running effort just because the overall objective is still active.
Instead, one session advances one natural slice confidently, reaches a clean
boundary, writes the relay delta, and hands the next slice to the successor.
The effort remains the durable source of truth; the handoff carries only the
immediate baton.

## `trigger_handoff`: the signal-only contract

`trigger_handoff` is the plugin's one "arm the continuation" tool. It never
performs process management.

- For **context-pressure-driven** handoffs with remaining work, call it
  immediately after `save_handoff_prompt`.
- For **turn-end / follow-up** handoffs, call it only after the user says yes,
  unless autopilot or prior pre-authorization applies.

It always:

1. drops the composed handoff markdown in the current session's session-state
   folder,
2. durably stores it (`agent-dispatch` task-backed storage when available,
   otherwise a worktree-state file).

**Only when `.context-handoff/config.yaml`'s `mode` is `auto`** (the default
is `manual-only`) does it additionally:

3. note it in the worktree's own record via `agent-worktrees note-handoff`
   (this creates a `pending_handoffs` entry agent-worktrees' resident
   monitor can discover and claim on its own -- a live-cutover trigger
   point, not merely advisory, so it is gated identically to the two
   below), refresh worktree-visible PENDING-HANDOFF state -- the signal
   agent-worktrees' resident status-monitor watches for -- when
   `agent-worktrees` is available,
4. best-effort ping `agent-bridge` if present,
5. wait up to 30 seconds for the CUTOVER to start -- not for the successor to
   fully finish cold-starting and consume the handoff, which legitimately
   takes longer (40-90+ seconds) and isn't worth blocking on.

Regardless of mode, it always:

6. check once whether the session-state marker was consumed, the worktree
   recorded a successor, the dispatch task moved out of `proposed` /
   `queued`, or (the earlier, cheaper signal) the resident status-monitor
   has already logged a `handoff_cutover_spawn` for this token -- under
   `manual-only` this is a single check with no polling wait (steps 3-5 are
   the only ones actually skipped),
7. print manual continuation instructions -- distinctly worded when
   automatic cutover is simply disabled by `mode` versus when it was
   attempted and nothing happened, or a distinct "already under way" note if
   a spawn is merely in flight,
8. always end by printing the final short handoff prompt/seed.

That final seed is the "if your download doesn't start, click here" fallback:
it gives a human or control system enough to continue even if none of the
signaling paths responded during the grace window, or automatic cutover was
never wired up at all under the default mode.

A real successor's Copilot cold-start (loading MCP servers/skills before it
can even run its own `sessionStart` hook) routinely takes 40-90+ seconds --
much longer than the 30-second wait, and full pickup was the only signal
`trigger_handoff` used to check. Under `mode: auto`, `trigger_handoff` treats
the resident status-monitor's `handoff_cutover_spawn` activity marker --
which fires much sooner, as soon as the cutover itself starts -- as an
earlier, distinct "spawn acknowledged" signal, so the predecessor reports
real progress within the original 30-second window instead of a false
"nothing happened".

## Storage

`save_handoff_prompt` and `trigger_handoff` use the same durable store
selection: 

| Coordinator availability | Storage |
|---|---|
| `agent-dispatch` reachable | proposed, handoff-labeled task pinned to the current worktree |
| no `agent-dispatch` | one-time JSON file under the machine-local worktree state directory |

In both cases the plugin also returns a bounded one-line seed. The seed is a
locator, not the handoff itself: it contains a task lead, a recommendation to
use `/consume-handoff`, and one opaque `task:<id>` or `file:<id>` recovery
locator.

## Resuming

A handoff is **never** auto-loaded.

- `/consume-handoff` is the canonical slash command. It prefers a pending
  worktree-pinned agent-dispatch handoff task and otherwise falls back to the
  newest matching unconsumed worktree-state handoff file.
- `/resume-handoff` is a compatibility alias for `/consume-handoff`.
- If the extension is unavailable, use the payload-local CLI and pass the
  recovery locator to `consume --locator`.

For a natural-language "resume from handoff" request, sweep the current
worktree's own state first rather than doing a global search.

### Already-claimed handoffs

A consume attempt that fails because the handoff was already consumed (or is
currently being consumed elsewhere) always reports the claimant's session id,
via `result.claimedBySession` and inline in the message text -- for both the
file-backed and agent-dispatch task-backed stores. When this happens, state
the claimant session id to the user and offer to file a bug (do not file one
automatically): repeated or racing consumption of the same handoff is
typically a sign of a real defect upstream, not routine behavior.

### Extension-host disconnected mid-call

A handoff tool call -- `consume_handoff` most commonly -- can fail with
something like "Extension disconnected before responding to tool call" when
the Copilot CLI's extension host restarts mid-call, for example while a
background plugin update or reconciliation pass is being applied. A following
notification that the available tool set changed (tools disappearing and
reappearing) is a strong corroborating signal that this is what happened.

This is a **transport-level failure, not a semantic answer**. Unlike an
already-claimed response (which always names a claimant session), a
disconnect carries no information about whether the underlying store was
read, mutated, or left untouched -- it is not evidence that no handoff is
pending.

1. Do not conclude "nothing is pending" or reconstruct a different objective
   from session history solely because the call errored this way.
2. Once the tool set stabilizes (previously-lost tools become available
   again), retry the identical call once.
3. If the tool remains unavailable, or the retry fails the same way, fall
   back to the payload-local CLI (`consume --locator`, `facts`,
   `check-heads`) -- it talks to the durable stores directly and does not
   depend on the extension host being up.
4. Only report "nothing pending" once that CLI-backed retry also finds none.

## Handoff-lifecycle observability

`trigger_handoff` is stage 6 of a wider 13-stage cutover lifecycle spanning
this plugin, the resident `agent-worktrees` status monitor, and the mux
layer -- worktree creation through the predecessor's confirmed retirement.
The full stage vocabulary, the two durable stores that record it
(`activity.jsonl`'s rolling log and `handoff_trace.py`'s unrotated
per-worktree store), and the diagnostic tools available today
(`agent-worktrees handoffs-check`, `agent-bridge handoff-check`) are
documented in
[`agent-worktrees`'s architecture doc](../agent-worktrees/docs/architecture.md#handoff-cutover-lifecycle-the-13-stage-trace)
-- read that first when a handoff appears to have gone sideways rather than
re-deriving the sequence from scratch. `handoffs-check` diagnoses (and can
repair) an **unretired predecessor after a spawn is recorded** -- a
successor associated as a candidate *or* already linked, plus a recorded
spawn event, with no confirmed retirement since. Its read-only report does
**not** itself confirm the pane is still alive (no `_mux_pane_alive()` call
in that path) -- it lists every such unretired case as a candidate, even one
whose pane already exited without ever being logged as retired; `--execute`
is the step that performs the live check and actually resolves it. It does
**not** diagnose "ack but no pane at all" (a host acknowledgement with no
successor ever recorded) -- that case has no dedicated diagnostic yet; a
dedicated
`handoff-trace` render command is
still open follow-on work.

## Plugin-load reliability

Whether this extension's tools (`generate_handoff_prompt`,
`save_handoff_prompt`, `consume_handoff`, `trigger_handoff`) are even
*available* in a given session is a separate question from the
handoff-lifecycle observability above -- if the extension never loads, none
of that machinery is reachable at all. Copilot CLI already writes a
per-launch diagnostic log for every extension fork under
`~/.copilot/logs/extensions/plugin-<name>_<name>-<launch-epoch-ms>-<pid>.log`,
ending in a terminal marker (`ready`, `ready-timeout`,
`peer-closed-before-ready`, or an `exit code=N disposition=<disposition>`
line) -- real, already-being-written data, no new instrumentation required
to read it.

`scripts/extension_load_reliability.py` summarizes that data into a
reliability report for one plugin over a time window:

```
python scripts/extension_load_reliability.py --plugin context-handoff --days 7
python scripts/extension_load_reliability.py --plugin context-handoff \
  --split-at 2026-09-21T05:00:00+00:00 --json   # before/after a fix
```

`--split-at` partitions the window into `before`/`after` summaries around a
timestamp, e.g. when a fix's new version actually lands on a machine (a
merge only primes deployment; check the installed plugin's own version/mtime
under `~/.copilot/installed-plugins/` to find when it actually took effect,
not the merge time). Empirically, a plugin's own `ready-timeout` rate has
tracked closely with *every* concurrently-launching plugin's rate on the
same machine, not just the ones with heavier load-time work -- consistent
with shared host/CPU contention across simultaneously-forking extensions
being a real contributor, not only a given plugin's own synchronous work.
Compare against a sibling plugin's rate over the same window before
attributing a change in rate to a specific fix.

### Post-readiness crash diagnostics

The launch log above records only a terminal disposition and exit code --
if the extension reaches `ready` and then stops with no further harness
output (observed and diagnosed on at least one host, with no root cause
identified yet), the launch log alone cannot say *why*, or even *whether it
was actually a crash at all* -- see "interpreting an entry" below.
`extension.mjs` registers `process.on('uncaughtException'
/'unhandledRejection'/'exit'/'SIGTERM'/'SIGINT'/'SIGHUP')` handlers (added
directly above the `--- State ---` section) that write a synchronous,
durable diagnostic line the instant anything goes wrong to
`<os.tmpdir()>/context-handoff-extension-crash.log` (e.g.
`/tmp/context-handoff-extension-crash.log` on Linux/macOS,
`%TEMP%\context-handoff-extension-crash.log` on Windows). Read this file
after a suspected crash.

Each log entry begins with a line stamped `<ISO timestamp> pid=<pid>
<label>: <detail>`. For an `uncaughtException`/`unhandledRejection` entry,
`<detail>` is produced by `describeFailure()`: a multi-line `Error.stack`
when the thrown/rejected value has one (only that first, stamped line
carries the timestamp/pid/label prefix -- the stack's own trailing `at ...`
lines that follow are not individually stamped, so correlate by that first
line); a single-line `String(value)` when it does not (a thrown value is
not required to be an `Error`); or the fixed fallback string
`<failure detail unavailable: describing it threw>` if even that extraction
itself throws (a hostile/buggy `.stack` getter or `Symbol.toPrimitive`/
`toString`). `<detail>` for the exit/exception/rejection/signal handlers
always includes `ready=true`/`ready=false` -- an in-memory-only flag (never
itself written on its own) set once `joinSession()` actually resolves, so a
captured failure's line says whether it happened before or after this
instance reached readiness.

This file is:

- **Created privately, symlink-safe.** `os.tmpdir()` is commonly a shared,
  world-writable directory on POSIX (`/tmp`); a predictable filename there
  is both world-readable by default and a symlink-attack target. The first
  write opens it with `O_CREAT|O_WRONLY|O_APPEND|O_NOFOLLOW|O_NONBLOCK` and
  mode `0o600` -- private to this user, and the kernel itself refuses to
  open through a pre-existing symlink rather than following it (and refuses
  to block indefinitely against a pre-created FIFO with no reader). Neither
  flag exists on Windows (no equivalent local-multi-user attack surface
  there in the same shape); the file descriptor, once opened, is reused for
  every subsequent write in the same process. **On POSIX**, a pre-existing
  file that this user does not own, or that grants group/other any access,
  is refused outright rather than written to or "fixed in place" -- **this
  check does not run on Windows** (no POSIX uid/mode model there, and no
  `process.getuid`), so a pre-existing file at this path on Windows is
  accepted regardless of its ownership or permissions.
- **Only records the interesting cases, by design.** A routine `code=0`
  exit is never logged: this extension's own module is dynamically
  re-imported many times over a machine's lifetime (once per discovery
  pass, plus once per reconnect/resume), and logging every uneventful fork
  would make this file rotate away its own history far more often than it
  otherwise would (see the size-rotation bullet below) -- the existing
  lifecycle-logging pattern
  (`docs/patterns/lifecycle-activity-logging.md`) deliberately bounds or
  reboot-volatilizes every tier it defines, and this file has neither
  property beyond its coarse size trigger, so it must not record routine
  events at all. Only an
  `uncaughtException`/`unhandledRejection` (+ non-zero `exit`), a caught
  `SIGTERM`/`SIGINT`/`SIGHUP`, or a genuinely non-zero exit for some other
  reason produces an entry.
- **Size-rotated at a coarse 1 MiB threshold, no other retention.** No
  scheduled or calendar-based cleanup exists beyond that size trigger;
  treat it as a manually-cleared scratch file for anything the size-based
  rotation described below does not already handle. `SIGTERM`
  specifically is the Copilot CLI's own **routine** mechanism for `/clear`
  and foreground-session replacement (see "Interpreting an entry" below),
  so -- unlike the suppressed `code=0` exit path above -- ordinary,
  expected signal churn alone still appends a line every time, with no
  natural ceiling on a long-lived, handoff-heavy host. Rather than stop
  recording a legitimate signal (which would defeat the entire "distinguish
  a routine stop from a crash" purpose of this file), each process checks
  the file's size once, at its own first write: if it has grown to 1 MiB or
  more, it is **rotated** -- renamed aside to a per-process, uniquely-named
  `.stale-<pid>-<timestamp>-<uuid>` sidecar **in its own dedicated
  `context-handoff-crash-sidecars/` subdirectory** (created on demand next
  to the log file itself, and re-validated on every rotation and purge as
  a real, non-symlink, privately-owned-and-permissioned (0700 on POSIX)
  directory before anything is ever renamed into or purged from it -- a
  plain "create if missing" call alone would otherwise treat an
  attacker-planted symlink, or a pre-existing directory with permissive
  group/other bits, as fine to use, silently redirecting rotations
  elsewhere or exposing sidecars to another local user; a directory that
  fails this check simply means rotation is skipped for that call, not
  that diagnostic logging itself fails), with a fresh file opened at the original path --
  rather than truncated in place. This matters because the file is shared
  by every extension instance on the machine (see the next bullet): an
  in-place reset is not serialized across processes, so a second process's
  own reset could otherwise erase the crash entry a first process just
  finished appending moments earlier. The rename is guarded by an identity
  check first (the file currently at this path must still have the same
  device+inode this process actually opened and measured as oversized --
  empirically confirmed reliable on both POSIX and Windows) -- a *blind*
  rename of whatever currently sits at the path, with no such check, could
  otherwise rename a different process's already-rotated, already-live
  fresh file into this process's own sidecar, silently detaching that
  process's diagnostics from the well-known path.
  Immediately after a rotation, the same process also opportunistically
  purges old sidecars from that same dedicated subdirectory (never the
  shared parent directory the log file itself lives in -- see below for
  why), by age -- **read from the rotation timestamp embedded directly in
  the sidecar's own filename** (`.stale-<pid>-<timestamp>-<uuid>`), never
  the filesystem's mtime -- older than 24 hours. mtime was tried first, but
  is observable (and mutable) by every other process on the host from the
  instant a rename completes: a concurrent process's own purge could run
  against a brand-new sidecar before this process got a chance to
  separately re-stamp its mtime, see the oversized source file's old,
  pre-rotation mtime, and delete it immediately -- a genuine cross-process
  race. Baking the timestamp into the name atomically, in the very same
  `renameSync()` call that creates the sidecar, removes that window
  entirely: there is no longer a separate step (and therefore no race)
  between "this sidecar exists" and "its age is correctly and immutably
  knowable" by any process that reads its name. The dedicated subdirectory
  matters for a second, independent reason: this purge is bounded to a
  fixed number of directory entries read per call (see the module's own
  comments for why), since it can run synchronously inside a
  signal-handling path with a hard termination deadline -- but a bounded
  scan of the log's own *shared parent* directory (commonly `os.tmpdir()`,
  populated by every other application on the host too) could keep landing
  on the same leading, unrelated entries every single call and never reach
  this log's own sidecars at all, no matter how many rotations ever ran. A
  directory that holds nothing but this log's own sidecars has no such
  adversarial population ahead of them, so the same bounded scan is
  actually guaranteed to make progress. This age gate bounds the
  *cumulative* disk usage many rotations over a long-lived host would
  otherwise leave unbounded, without reintroducing the destructive race a
  blind/unconditional delete would risk. This bounds growth across
  the many separate short-lived processes that are the actual growth vector
  (though not a single pathological process logging in a tight loop -- not
  a real shape here, since at most a handful of entries are ever logged per
  process lifetime). A rotation discards nothing at the moment it happens
  -- the oversized content survives under its sidecar name until that
  sidecar eventually ages out -- but the live path itself becomes a rolling
  window, not a permanent record; any sidecar an operator needs to inspect
  sooner than its own 24-hour purge is still a manually-cleared scratch
  file like the log itself.
- **A failed file write falls back to a best-effort line on stderr, not
  silence.** Since these listeners suppress Node's own default
  uncaught-exception report, a log write that fails for any reason
  (missing directory, full disk, an untrusted pre-existing path, ...) would
  otherwise leave neither a file entry nor any stderr output -- exactly the
  original silent-exit symptom this module exists to diagnose, just moved
  one layer down. Look for a
  `context-handoff emergency diagnostics (log write failed): <label>: ...`
  line on stderr in that case; it covers every failure path here,
  including a plain non-zero `process.exit()` with no preceding
  exception/rejection/signal, and is deliberately printed at most once per
  process (a later handler -- e.g. the `exit` event that always follows an
  `uncaughtException` handler's own `process.exit(1)` -- does not repeat
  the same underlying failure as a second, redundant line).
- **Shared by every extension instance on the machine -- correlate by `pid=`
  and timestamp before drawing a conclusion.** This is one fixed,
  machine-global path, and multiple `context-handoff` processes (across
  different worktrees, sessions, or simply overlapping discovery/reconnect
  forks) can each append to it independently, interleaved in whatever order
  they actually wrote. A `signal: ...` line followed somewhere later in the
  file by an `exit: code=...` line does **not** by itself mean that exit
  belongs to the same process that received the signal -- it can belong to
  an unrelated instance, or (rarely) a later instance that reused the same
  PID after the first exited. Always check that the `pid=` value matches
  between the lines you are comparing, and that their timestamps are close
  enough in sequence to plausibly be the same launch, before concluding
  whether a given signal was followed by an exit or not.

**Interpreting an entry -- a `SIGTERM`/`SIGINT`/`SIGHUP` line does not by
itself mean a crash.** The Copilot CLI's own documented extension lifecycle
stops and reloads every extension process "on `/clear` (or if the
foreground session is replaced)" via SIGTERM (then SIGKILL after 5s if it
doesn't exit) -- a routine, expected event, not a bug. A signal handler here
logs, then **removes its own listener and re-sends the identical signal to
itself**, so the OS's default disposition genuinely terminates the process
by that signal -- Node then reports the same `(code=null,
signal="SIGTERM"/"SIGINT"/"SIGHUP")` shape a parent observes with no
diagnostics installed at all (deliberately *not* converted into
`process.exit(128 + signum)`, which would silently change that contract for
any host code that distinguishes a signal-terminated child from a normal
exit). Because the re-raised signal kills the process before Node's own
`exit` event gets a chance to run, **a `signal: <name>` line with no
following `exit: code=...` line is the *expected* shape for this legitimate,
host-initiated stop** -- not evidence of a crash.

**On Windows, this same routine host-initiated stop leaves no entry in this
file at all.** `child_process`/`process.kill()`-delivered `SIGTERM` on
Windows terminates the target process unconditionally at the OS level,
bypassing any registered `process.on('SIGTERM', ...)` JS listener entirely
(empirically confirmed against this repo's actual Node/Windows runtime --
see the tests) -- so the signal handler above never runs, and a routine
`/clear`/foreground-session-replacement stop on this platform is
indistinguishable, from this file alone, from the "no entry at all" cases
below. This is a genuine platform gap, not a bug in this module: Windows
gives a JS process no hook into a routine external `SIGTERM` in the first
place.

So, reading a launch's entries in this file (if any) -- **always match `pid=`
between the lines being compared first** (see the correlation bullet
above):

- `signal: SIGTERM`/`SIGINT`/`SIGHUP`, no `exit` line **for that same
  `pid=`** -- an expected, host-initiated stop (`/clear`, foreground session
  replaced, or a real Ctrl+C/HUP). Given how handoff/session-switch-heavy
  some workflows are, this may explain a large share of historical `exit
  code=1 disposition=stopped-normally` launch-log entries that were never
  actually crashes. **POSIX only** -- see the Windows note above.
- `uncaughtException`/`unhandledRejection` (with a stack), followed by
  `exit: code=1` **for that same `pid=`** -- a genuine bug, but not
  necessarily in this extension's own code: these handlers observe the
  entire process, so the stack can equally point into the bundled SDK, the
  CLI's own harness/runtime, or another dependency loaded into the same
  process. Read the stack itself to identify the actual source rather than
  assuming this extension's code is always at fault; the trailing
  `ready=true`/`ready=false` on both lines says whether the failure hit
  before or after this instance reached readiness. If the crash log's own
  write also failed (missing directory, full disk, an untrusted
  pre-existing path, ...), look for a
  `context-handoff emergency diagnostics (log write failed): ...` line on
  stderr instead -- a best-effort fallback specifically so a logging
  failure never reproduces the original silent-exit symptom this module
  exists to diagnose.
- A **standalone** `exit: code=1 ready=...` line, **with no preceding**
  `uncaughtException`/`unhandledRejection`/`signal` line for that same
  `pid=` -- the process reached the exit handler with a non-zero code
  through some other path entirely (a bare `process.exit(1)` elsewhere in
  the code, for instance), without ever raising a captured exception,
  rejection, or signal here. There is no stack to inspect for this shape;
  correlate the timestamp/`pid=` against whatever other logs this host
  keeps (the harness's own launch log, application logs, etc.) to find
  what actually called `process.exit(1)`.
- **No entry at all for a launch whose own harness log shows it reached
  `ready` and then stopped** -- four distinct possibilities, not just one:
  the ordinary case is simply a routine `code=0` exit, which is never logged
  by design (see above); on Windows specifically, a routine `SIGTERM`
  host-initiated stop is *also* never logged, since the signal never reaches
  the JS handler at all (see the Windows note above) -- check the stderr
  fallback described just above before assuming a missing entry means
  something worse. Beyond those two routine cases, either something
  bypassed Node's own signal/exit handling entirely (`SIGKILL`, an external
  whole-process-tree kill -- registering a handler for a given event cannot
  help when the process never gets to run any more JS at all), **or**
  `createEmergencyLog()`'s own open/write attempt failed *and* its stderr
  fallback was unavailable, itself failed, or was simply uncaptured by
  whatever is holding this process's stderr (its own defensive contract:
  diagnostic logging must never itself become a second crash cause) -- for
  example the crash log's directory is missing, the disk is full, or the
  pre-existing-file ownership/mode check rejected an untrusted file at that
  path (see the private-file-creation note above), *and* stderr 2 was
  itself closed/broken or not being captured anywhere. A missing entry does
  not, by itself, distinguish these; check that the log's parent directory
  exists and is writable by this user, and check for a captured stderr
  fallback line, before concluding it must have been a force-kill.

## Payload-local CLI fallback

When the extension does not resolve or fails to load, the plugin's payload
files are still on disk. Resolve the verified `handoff-cli.mjs` relative to the
installed plugin root and invoke it with `node`.

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-}"
if [ ! -f "$CH_ROOT/plugin.json" ]; then
  CH_ROOT="$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff"
  provenance="$(node -e 'const fs=require("fs"),m=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); console.log(`${m.name||""}|${m.repository||""}`)' "$CH_ROOT/plugin.json" 2>/dev/null)"
  [ "$provenance" = "context-handoff|https://github.com/ThomasMichon/copilot-extensions" ] ||
    { echo "canonical context-handoff@copilot-extensions payload not found" >&2; exit 1; }
fi
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
[ -f "$CH" ] || { echo "context-handoff payload-local CLI not found" >&2; exit 1; }

node "$CH" facts --json --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" retry-cutover --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<topic>" --prompt-file "<handoff.md>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --title "<topic>" --prompt-file "<handoff.md>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --handoff-token "<HANDOFF_TOKEN>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" consume --locator "task:<task-id>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" consume --locator "file:<handoff-id>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```

PowerShell uses the same verified, plugin-folder-relative invocation:

```powershell
$chRoot = $env:COPILOT_PLUGIN_ROOT
if (-not (Test-Path -LiteralPath "$chRoot\plugin.json")) {
  $chRoot = Join-Path $HOME '.copilot\installed-plugins\copilot-extensions\context-handoff'
  try { $manifest = Get-Content -Raw "$chRoot\plugin.json" | ConvertFrom-Json } catch { $manifest = $null }
  if ($manifest.name -ne 'context-handoff' -or
      $manifest.repository -ne 'https://github.com/ThomasMichon/copilot-extensions') {
    throw 'canonical context-handoff@copilot-extensions payload not found'
  }
}
$ch = Join-Path $chRoot 'extensions\context-handoff\handoff-cli.mjs'
if (-not (Test-Path -LiteralPath $ch -PathType Leaf)) { throw 'context-handoff payload-local CLI not found' }
node $ch facts --json --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch check-heads --json --cwd $PWD
node $ch retry-cutover --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch sync-worktree --json --cwd $PWD
node $ch save --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --handoff-token '<HANDOFF_TOKEN>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'task:<task-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'file:<handoff-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
```

## Last-resort fallback: write the file yourself

The tool-backed path (`save_handoff_prompt` / `trigger_handoff` /
`consume_handoff`) and the payload-local CLI fallback above both assume
*something* still works -- the extension host, `node`, or a reachable store.
When even that assumption is unsafe (the extension is disconnected, the CLI
fails the same way, or storage reports "no safe task or file store was
available"), the **most durable** continuation path needs none of it: an
ordinary file write, plus a short prompt a human pastes into a fresh session
after `/clear`. This always works because it depends on nothing but the
agent's normal ability to write a file and print text.

1. Compose the same markdown brief the Template section describes.
2. Write it with an ordinary file write -- no MCP tool call, no extension, no
   `node` -- to a stable path under the current session's own state folder:
   the same `~/.copilot/session-state/<session-id>/` directory the extension
   itself already uses (for example
   `~/.copilot/session-state/<session-id>/files/handoff-<slug>.md`). Create the
   `files/` directory first if it does not already exist -- it is not
   guaranteed to be pre-created. This directory persists independent of the
   extension, the payload-local CLI, and any store selection.
3. State the exact absolute path to the user.
4. Give the user a short prompt to paste into a new session after `/clear`,
   naming that exact path and instructing the next session to read it and
   resume the objective it describes -- not merely recap it. For example:

   ```text
   /clear
   Read <absolute-path-to-file> and resume the objective it describes.
   ```

5. This path has no automatic pickup, no claim tracking, and no
   supersession -- it is a manual handoff between two humans/agents. Prefer
   the tool-backed and CLI-backed paths above whenever either is reachable;
   reserve this one for when both have failed.

## Auditing handoff head alignment

`check-heads` compares the authoritative `agent-worktrees head-session --json`
ledger view for each known worktree against the resident status-monitor's
current registry of `wt-<id>` sessions. Its main failure mode is a pending
handoff that still exists in the ledger but is not currently reachable through
the monitor's served-session roster, which means a proactive cutover spawn will
not visit that worktree.

- `pending-handoff-unregistered` — the ledger still has one or more pending
  handoffs, but the monitor currently has no registered `wt-<id>` target for
  that worktree.
- `pending-handoff-registry-path-mismatch` — the monitor has a registered
  `wt-<id>` entry, but it points at a different checkout path than the worktree
  inventory row being audited.

Use the command directly:

```bash
node "$CH" check-heads --json --cwd "$PWD"
```

If you are looking at a superseded predecessor session and the worktree already
has a live successor pane, use:

```bash
node "$CH" retry-cutover --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```

That shells to `agent-worktrees handoff-cutover --retry --session-id <sid> --json`: it
first checks whether the latest handoff already has a live successor pane and,
if so, refocuses that existing pane instead of spawning a duplicate successor.
Only when no live successor exists does it fall back to a fresh spawn attempt.

If the ledger itself is stale, repair the underlying worktree state with
`agent-worktrees doctor --fix`, or explicitly repoint the head with
`agent-worktrees conclude-session` / `link-succession` once you know the exact
predecessor and successor ids.

## Thresholds

| Threshold | Behavior |
|-----------|----------|
| 55% of window | Soft reminder: compose/store a baton at the next clean boundary and trigger directly if work still remains |
| 70% of window | Urgent reminder: preserve the baton now and trigger directly; compaction remains at ~80% |
| 79% of window | **Force tier** (only when `mode: auto`; a no-op under the default `manual-only`): the extension does it *for* the agent -- auto-drafts a handoff from whatever session facts are available, stores and triggers it via the same path `save_handoff_prompt`/`trigger_handoff` use, and denies further mutating tool calls (read-only inspection still allowed) for the rest of the session. This is the last chance to capture state before the runtime's own auto-compaction (~80%) destroys it -- it does not wait for the agent to act. Lifted only by a successful compaction (the operator may then keep working in the same session instead of switching to the handed-off one); at most one auto-handoff per session |

An owning repository may override these defaults in `.context-handoff/config.yaml`,
and a user may set lower-priority personal defaults in
`~/.context-handoff/config.yaml`. The repo layer merges per key over the user
layer, so a repo that only sets `mode` still inherits user-level thresholds.

```yaml
mode: auto         # auto | manual-only | off
thresholds:
  soft_percent: 65
  hard_tokens: 75000
  force_percent: 78
```

**The default mode is `manual-only`, not `auto`** -- the force-tier
auto-trigger and `trigger_handoff`'s live-cutover wiring (the
`handoff_requested` activity event agent-worktrees' resident status-monitor
watches for, and the agent-bridge ping) are opt-in: a repo (or a user,
via the home-directory layer) must explicitly set `mode: auto` in
`.context-handoff/config.yaml` to enable them. The soft/hard context-pressure
warnings above fire under `manual-only` too -- only the force tier's own
automatic handoff and any live pickup signaling wait for `mode: auto`.
`save_handoff_prompt`,
`trigger_handoff`, and `consume_handoff` all keep working under
`manual-only` -- `trigger_handoff` still stores/seeds the handoff and prints
the manual pickup instructions, it just never wires up automatic pickup.
`mode: off` disables both automatic behavior and the extension/CLI handoff
entry points for that repo entirely (only the last-resort manual file write
remains reachable, since it depends on nothing this plugin owns).

Each threshold tier may use either `<tier>_percent` or `<tier>_tokens`. Mixed
configs are allowed per tier; percent tiers resolve against the live session
token limit, token tiers stay fixed. If a mixed config's effective ordering is
only knowable once the live token limit arrives, that final ordering check is
deferred until then. Invalid config produces a visible warning and uses the
55% / 70% / 79% defaults. If the runtime does not report a window size,
percent-based tiers remain unknown, while absolute-token tiers still work.
