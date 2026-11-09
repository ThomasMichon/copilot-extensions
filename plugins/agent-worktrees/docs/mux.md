# Multiplexed Sessions — Why and When

agent-worktrees runs your interactive Copilot sessions inside a **terminal
multiplexer** — `tmux` on Linux/WSL, `psmux` on Windows. This page explains
*why* that matters and *when* you want a muxed session versus a plain,
non-muxed worktree. For the mechanics (status bar, per-session config, the
opt-in keybinds) see [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux);
for the launch surface see [The Worktree Picker](picker.md).

## Why a multiplexer

A worktree is meant to **outlive any one terminal**. The multiplexer is what
makes that true — the session survives things that would otherwise kill it:

- **The session persists past the terminal.** Each launched worktree gets a
  named mux session (`wt-<id>`). Close the terminal, drop the SSH connection, or
  reboot your local terminal app, and the session — and the running Copilot
  agent — keep going. Reconnect and **rejoin** exactly where you left off.
- **Copilot exiting ≠ the session ending.** When the Copilot process exits the
  mux session stays alive, so `/restart` and re-launch work without tearing down
  the worktree.
- **Detach and rejoin at will.** Detach to background a long-running agent and
  reattach later — from the same terminal or a different one (including over
  SSH).
- **Parallel panes.** Multiple worktree sessions (and multiple machines) run as
  independent mux sessions you can move between, without one blocking another.

This is the backbone of "a worktree can outlive any one terminal, shell, or
Copilot session."

## When you want a mux — and when you don't

Allocating a checkout and hosting an agent are separate operations. Choose by
**who's driving**, **whether to start a process**, and **whether to attach**:

| You are… | Use | Muxed? | Why |
|----------|-----|--------|-----|
| A human at a terminal, picking or resuming | `my-project` (the **Picker**) / `agent-worktrees resolve` | **Yes** — creates or **rejoins** the `wt-<id>` session | Persistent, detachable interactive work |
| A human who wants a brand-new session now | `agent-worktrees resolve --new` | **Yes** — launches a fresh muxed session | Same, skipping the picker (refused without a TTY) |
| An agent, daemon, or script | `agent-worktrees create [--json]` | **No** — prints the worktree path, launches nothing | Programmatic callers edit in their *current* process; a mux would just get in the way |
| Automation that needs a hosted agent process | `agent-worktrees embody --worktree-id <id> --json` | **Yes**, detached | Ensure a process exists without taking the caller's terminal |
| A human connecting this terminal | `agent-worktrees copilot --worktree-id <id>` | **Yes**, attached here | Ensure the same process, then connect the current TTY |

Rule of thumb: **interactive human work → muxed** (persistence + detach/rejoin);
**checkout-only automation → `create` (no mux)**; **hosted-agent automation →
`embody` (detached)**. `resolve --new` is refused without a TTY, whereas
`embody --new` deliberately allocates and hosts a detached agent.
Both `embody` and `copilot` resume existing history by default, including with
a startup prompt; `--fresh` explicitly opts into a new conversation.
An already-live target is reused without prompt injection or a duplicate process.

An interactive launch retries multiplexer session creation up to three times
before treating it as failed. If all attempts fail in an interactive terminal,
the launcher offers an explicit retry; declining or exhausting recovery reports
the preserved worktree path and the command that retries that same worktree. A
non-interactive caller never blocks on the prompt. The launcher never silently
starts a bare Copilot process instead. Use `--no-mux` (or
`WORKTREE_NO_MUX=1`) only when you intentionally want the direct,
non-persistent diagnostic path.

Ordinary unseeded direct CLI plans with a selected native conversation now
enter the same retained Driver core as headed setup. The original Worktrees
owner keeps its admission fence, native process receipt and exit result.
Canonical launch-command wrappers remain in the path; on Windows their copied
startup manifest carries literal argv, including drive-qualified `-C=...`
values, without a second PowerShell parameter-binding pass. Nonlocal,
ambiguous and unsupported commands retain their existing path.
Windows callers opt into the new path only when they own final startup
serialization. Current Manager declares that ability for the resolver call,
restores the process environment afterward, and keeps argv visible while
refreshing the inner runtime and applying the authoritative AHP binding.
It serializes only at the final launch boundary. Older Manager callers keep
the existing plan path; older Worktrees providers can ignore the declaration
without accepting new CLI syntax.
POSIX hoisting requires a known empty effective `BASH_ENV`; nonempty values
or an unknown mux-server environment retain the outer admission owner.
An explicitly empty planned value is pinned with `env BASH_ENV=` before Bash
starts. Authoritative AHP rebinding preserves literal option values rather
than treating prompt/model/agent text as connection selectors. Bare `--resume`
does not consume a following option. Worktrees and Driver share the native
value-option table and preserve split `-C` directory values when detecting
the selected conversation or validating a retained headed command.

This does not turn unknown mux state into absence or bypass cold-seed checks.
Admission-only execution cannot claim a pending prompt; a changed prompt
blocks the launch. Staged seed handoff and special native-head recovery retain
their cold-stop checks. Failed PSMux CLI queries remain unqualified; the
action-time native reader described below can establish stopped mux state.

> **Windows over SSH:** the interactive TUI picker auto-falls back to a simpler
> flow (a ConPTY limitation), but the muxed-session model is the same. See
> [picker.md](picker.md).

## Recovery after a maintenance window

### Preserve first when discovery is unavailable

A missing listing is not proof of process death or a safe replacement target.
PSMux can run registration cleanup on startup even for nominal reads such as
`has-session`, `list-sessions` and version queries. Public reports
[psmux/psmux#622](https://github.com/psmux/psmux/issues/622),
[psmux/psmux#546](https://github.com/psmux/psmux/issues/546) and
[psmux/psmux#650](https://github.com/psmux/psmux/issues/650) describe live registration loss
or subsequent orphan reaping. A successful but incomplete listing is another
failure class ([psmux/psmux#809](https://github.com/psmux/psmux/issues/809)).

The resident Worktrees mux enumeration and batched status reads therefore do
not execute PSMux. They use the existing Driver owner's
[finite native observation](../../../libs/copilot-driver-core/README.md#finite-psmux-observation),
as do creation/check-or-join and Manager discovery/client refresh. Fresh
authenticated session/pane replies establish presence and actual client rows
supply counts. Missing rendezvous needs complete native server coverage to establish absence;
partial, stale, refused or timed-out queries remain unknown, not an empty
catalog. Independent worktree, Git and process facts can still publish.
Authoritative batched status raises when any native row is unobserved, so
reclaim cannot interpret an unavailable row's default `exists=False` as death.
The separate display API preserves available rows alongside unknown ones.
Last-known mux presence retains its original timestamp when fresh observation
fails; it is not refreshed into current proof. TMux retains bounded CLI
observations, but malformed rows, duplicate sessions, diagnostics and failed
fallback probes cannot establish absence.

Open can reattach to an observed live session without creating or reseeding
it, and cold Open uses the same Driver check/create lease. Unknown presence
refuses cold creation/seed rather than guessing that the old session died.
Manager qualifies observation support from its selected Worktrees interpreter;
an older provider without that declaration stays unsupported, with no passive
PSMux CLI fallback. Explicit foreground attach/create and provider writes still
run PSMux and retain its startup-cleanup risk. This mitigation does not repair
lost registrations or authorize provider restart, recovery or a host update.
Fresh observations do not reactivate automatic pane discovery/status-bar
repainting through the PSMux CLI. Worktrees publishes current roster facts;
Manager's periodic status-option writes remain refused. Cached in-pane bar
text is not fresh listing evidence.
Legacy `status-updater` entrypoints also skip PSMux rather than restarting that
CLI polling loop when monitor registration is unavailable. Manager's warm
Picker observes presence and incarnation natively; an unknown observation
cannot retire its retained UI state. Its Windows creation client starts in the
user home without inheriting another pane's nesting variables. Native reads
use that creation's exact namespace, not another observer's ambient namespace;
scrubbed nesting flags are excluded from both sides of the worker scope hash.

Cleanup retains checkouts and tracking records whenever mux presence is
unknown, including its lock-held recheck and final managed-removal guard.
This is not a positive activity verdict: without independent live evidence,
classification still reads the checkout's actual Git disposition and counts.
Expired or negative cache hints do not waive this protection, nor does
`--force` on session cleanup. Fleet `status --mux-details`, nonresident list
reads and legacy Picker refreshes still return independent facts without
clearing last-known mux positives. A project publication failure is isolated
so unrelated resident rosters remain available.
Actions-menu liveness refinement also retains mux fields and uncertainty
unless it receives a complete current mux observation.
Complete refinement removes only superseded mux provenance and last-known
hints; unrelated provenance and stale source-envelope evidence remain intact.

Picker shows **`MUX(?)`** for known but unconfirmed mux hosting and
**`HOST(?)`** for a live process whose mux hosting is unknown. A client count
is not inferred from either. Proven process-only and ACP hosting remain
distinct. Old providers without this evidence retain their legacy rendering;
an old consumer may not render the new uncertainty, so use a compatible
Manager when relying on these badges.

When a current-roster feed becomes stale or disconnects, normalized attachment
and mux-live controls become unavailable along with the badge; preserved raw
client counts and Git facts do not remain current control evidence. A fresh
publication restores the normal controls.

This mitigation retains automatic PSMux status-painting restrictions while
restoring fresh listings and client-count refresh. It does **not** make explicit
creation, attachment, recovery or shutdown commands harmless, upgrade PSMux,
repair a running server, or qualify another executable with the same protocol.
Strict action-time Windows checks retain the bounded native reader, now
owned by Driver behind the existing Worktrees compatibility imports, without
starting PSMux. It enumerates native PSMux/PMux/TMux process identities and
discovers their port/key registrations without requiring `.pid` files, which
legacy PSMux 3.3.5 servers do not publish. Existing PID/birth sentinels still
have to match. It sends only a
fixed authenticated `display-message -p` request directly to each registered
loopback server, including warm servers, to confirm its current name, PID and
client count. Each reply's PID must belong to the native census, every live
server must be covered, and a second census and registration read must agree before
absence can admit a prompted Resume. The normal bound-Copilot check still
applies independently, including to a bare process.
An exited legacy server can leave a refused port/key endpoint behind. Failed
PID-less candidates are tolerated only when other authenticated replies cover
every live native server; the files are never removed or repaired.

The reader has a five-second total budget, bounded registration/reply sizes
and process counts. It never writes registrations, starts a server, repairs
an orphan, attaches a client, paints status or sends pane input. An unreadable
process, unsupported command-line mode, missing live-server registration,
PID reuse, rename, duplicate names across namespaces, authentication failure,
incomplete reply or changing inventory remains unknown.
`PSMUX_DATA_DIR` must be absolute when set; live
servers whose registrations are outside that selected directory also prevent
a complete result. This conservative action-time support does not re-enable
periodic discovery or change cleanup authority. A silent failed PSMux CLI
presence query still cannot authorize a cold replacement, and failed
reachability does not make a mux-homed process reclaimable.

For an affected host:

1. Keep working attachments open; checkpoint surviving agents and preserve
   source changes through their existing owners. Detachment is not death.
2. Separately authorize containment through the owning service if old observers
   are still polling. A source merge alone does not stop installed observers.
3. Inspect the on-disk executable through file/package metadata, not by running
   a version query. Identify already-running servers independently; replacing
   an executable does not upgrade them.
4. With bounded authorization, recover one checkpointed session at a time
   through its managed worktree and original durable conversation. Reconcile
   pending effects before resuming them. Preserve an unreachable survivor
   rather than starting a duplicate under the same name.

Do not fabricate port/PID registrations, perform global orphan cleanup, or
restart all mux servers to repair one failed observation. Version 3.4.0 was
verified against the specific psmux/psmux#622 sequence; that is not proof of every
discovery guarantee or of the build used by a running server.

The [planned-maintenance contract](../../../visions/agent-fabric/README.md#planned-maintenance-windows)
applies equally to disruptive mux replacement and machine restart. It calls for
advance notice, an opportunity for cooperative checkpoint/drain, a durable
inventory of original conversations, and status-first recovery afterward.
This is not an implemented machine-wide shutdown/reboot coordinator and is not
activated by package compatibility declarations.

After an independently authorized stop and environment repair, an automation
can resume a saved worktree through its normal owner:

```text
agent-worktrees embody --worktree-id <id> --resume-head --seed "Terminal session restarted. Check status and resume." --verify-timeout 30 --json
```

That is the default recovery wording; a scenario can replace the seed.
`--resume-head` is explicit here so the same conversation intent survives the
supported prior provider. If the target is already live the command is read-only,
not an event-message transport.

Recreated terminals do not restore process memory, shell variables, child jobs
or native schedules. The resumed agent must inspect its objective, worktree/head,
pauses, claims, background flows and external operations before continuing.
Unknown consequential operations are reconciled through their owners, not
blindly rerun. Mux creation, native startup, prompt consumption and completed
self-recovery are separate observations. A future coordinator must remain
outside affected sessions, fence competing launches and retain partial failures.

## Detach and rejoin

- **Detach** with the multiplexer's own detach key (tmux default `Ctrl-b d`;
  psmux equivalent) — the `wt-<id>` session keeps running in the background.
- **Rejoin** by relaunching the project binstub and picking the worktree (the
  Picker **resumes** the existing session rather than starting a second one), or
  by attaching to the mux session directly.

The status bar of a muxed worktree session shows its identity and live git
state; that's the same `status-segment` / `status-updater` machinery documented
in [cli-reference.md](cli-reference.md#status-bar-segment-tmux--psmux). A
`MERGED`/`FINAL` block there can carry markers of its own -- a compact
`C<N>`/`F<N>` for held claims / open follow-ups, and an independent `U*`/`OC*`
for an unconfirmed upstream-containment / claims fact (worktree-finality-and-
obligations Phase 9's per-fact freshness markers) -- see
[worktree-lifecycle.md § Decomposed sub-state facts](worktree-lifecycle.md#decomposed-sub-state-facts-phase-9)
for what each one means.

## Two backends, one model

Windows mux creation starts the PSMux client in the resolved user home, not
the selected checkout. The pane still receives its explicit worktree directory
through `-c`, so Copilot executes in the selected workspace.
Programmatic relative or omitted pane directories retain the original caller's
context before the PSMux process moves to home. This separates the
short-lived launch context from PSMux's persistent servers and idle standbys,
which otherwise can retain a checkout's directory handle after its session ends.
The Manager restores its original launch directory after success or failure.
This does not disable warm sessions, edit global PSMux configuration, change
POSIX launch behavior, or move an already-running standby.

Connected-session status recovery checks the inner panes' working directories,
not the server's `session_path`. A matching pane lets an already-attributable
worktree session recover its missing status registration even when the server
started in home. Exact tracked identity, current incarnation and Manager
ownership gates still apply; an unavailable probe or no matching pane does not
authorize recovery.

`tmux` (Linux/WSL) and `psmux` (Windows) are different implementations of the
**same** model — a named, detachable session with a status bar. The launcher
(relocated to Worktree Manager's `bin/` in Phase 3b Sub-slice 2a Step 2)
configures them **per session** (`set -t <session>`, never a global `-g`), so
**neither agent-worktrees nor Worktree Manager owns or overwrites** your
personal `~/.tmux.conf` / `~/.psmux.conf`, and ad-hoc mux sessions you start
yourself are untouched. The few server-global settings it can't scope
per-session (keystroke passthrough, `escape-time`) live in an **opt-in**
`apply-mux-keybinds.{sh,ps1}` you run only if you want them.
Full detail: [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux).

## Troubleshooting: every mux session died at once

If every `wt-<id>` session on a host disappeared together — not one worktree,
**all of them**, with no `finalize`/`cleanup` run and no scheduled task that
owns their lifecycle — the two backends point at **different** likely layers,
because they don't share a server the same way:

- **tmux (Linux/WSL) runs one shared server per machine.** A single tmux
  server exit (crash, OOM-kill, a host reboot) takes every session on that
  host down together — this *is* a plausible explanation for simultaneous
  loss on Linux/WSL.
- **psmux (Windows) runs a separate server per `wt-<id>` session** (each
  session's keybind config is sourced into its own server, never a shared
  one). A single psmux server exiting only takes down **its own** session —
  its own exit cannot by itself explain several unrelated Windows sessions dying at
  the same time. However, a PSMux command can inspect shared registration and
  reap other server trees during startup; separate servers do not isolate that
  cleanup path. On Windows, simultaneous multi-session loss can also point
  at something **common to all of those independent server processes**: the
  console-host layer each one depends on, or a host-wide event (e.g. a
  Windows Update reboot, a shared binary crash-looping under every server).

Check the host's crash/application event log for the shell binary (`pwsh.exe`
on Windows, the Application log, Event ID 1000) **and** for `psmux.exe`/
`conhost.exe` themselves. Correlate observation/cleanup activity as well as
shared console-host faults; simultaneous disappearance alone does not select
one of these causes.

**An observed compatibility signature:** PowerShell 7.6.6 (ARM64)'s
`Console Host` (`Microsoft.PowerShell.ConsoleHost.dll`) has been seen
fast-failing repeatedly (`Environment.FailFast`, exception code
`0x80131623`, same binary fault offset across occurrences) when hosted
inside a `psmux`-managed redirected console — i.e. exactly the pattern every
muxed Windows worktree session runs under. Each crash kills the `pwsh.exe`
process backing that pane; since every independent psmux server on the host
hosts the same crash-prone binary, a burst of unrelated-looking "session
gone" reports across different `wt-<id>` sessions can land within the same
few minutes without any single shared process tying them together. Treat
this as a reproducible compatibility signature to rule in or out, not a
confirmed root cause: the same fault code has also been reported upstream as
a downstream symptom of a `conhost.exe` failure. Inspect `psmux.exe`/
`conhost.exe` events alongside `pwsh.exe` before concluding which layer
actually failed first.

**Mitigation, if this signature matches:** pin the host's PowerShell install
to the 7.4 LTS line instead of latest-stable 7.6.x as a **time-boxed**
compatibility workaround, not a permanent fix — 7.4 reaches end of support on
**2026-11-10**. Before that date, reassess whether a fixed 7.6.x (or later)
build resolves the crash and migrate off the pin; do not let a declarative
pin silently carry a host onto an unsupported PowerShell release past its
support window. If you manage the host through `agent-machines`, express the
pin as a declarative `package` resource (`manager: winget`, `id:
Microsoft.PowerShell`, an explicit `version`, and `pin: true`, optionally a
`process_guard` on `pwsh.exe` so an in-progress restore defers rather than
races a live session) so the pin survives machine re-provisioning **until
you deliberately remove it**. See `docs/resources.md` in the `agent-machines`
plugin for the resource schema.

**Known limitation — this declares the pin, it does not downgrade a newer
install for you.** `agent-machines`' winget package resource converges a
version mismatch with `winget upgrade --version <pinned>` (see
`resources.py`'s `update` command), which winget treats as a no-op when the
installed version is already *newer* than the pin target ("No available
upgrade found") — winget has no downgrade verb. If the host already has
7.6.x installed, `agent-machines restore --apply` will **not** actually
replace it; you must uninstall the newer package yourself first (for
example `winget uninstall --id Microsoft.PowerShell --exact --version
7.6.6.0`) before a restore can install and pin the 7.4.x target. A fresh
restore on a host that has never installed PowerShell before converges
correctly with no manual step.

## See also

- [The Worktree Picker](picker.md) — the launcher that creates/rejoins muxed
  sessions.
- [Worktree Lifecycle & Change Management](worktree-lifecycle.md) — the
  `resolve` / `resolve --new` / `create` modes in the lifecycle.
- [CLI Reference](cli-reference.md) — `status-segment`, `status-updater`, and the
  per-session vs opt-in mux configuration.
