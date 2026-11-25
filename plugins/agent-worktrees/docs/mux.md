# Multiplexed Sessions — Why and When

agent-worktrees runs your interactive Copilot sessions inside a **terminal
multiplexer** — `tmux` on Linux/WSL, `psmux` on Windows. This page explains
*why* that matters and *when* you want a muxed session versus a plain,
non-muxed worktree. For the mechanics (status bar, scoped defaults and optional
host-wide tuning) see [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux);
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

New installations and provider selection require PSMux 3.4.0 or newer; 3.4.0 is
the preferred installer version. This does not revoke observation or attachment
of existing 3.3.5 sessions. The native observation path below retains legacy
registration support, and installation refuses provider replacement while any
provider process is present or its absence is unconfirmed. In that state the
installer instead puts the required version side by side under
`%USERPROFILE%\.agent-worktrees\psmux\<version>\`, from the WinGet
portable archive after it verifies the manifest SHA-256 and the binary file
version. If that version directory already exists but is empty, damaged or
holds another version, the installer leaves it unchanged and uses a new
`<version>-r<id>` directory instead. It then moves the selected directory
before older PSMux entries on the user PATH. The older provider keeps its live sessions, and a newer client can still
list, capture and attach them. Legacy binaries without version metadata are not
executed merely to discover their version while provider activity may be live.
The in-place package upgrade still runs only after the live-session gate is
satisfied; changing this source policy neither updates a running provider nor
migrates its sessions.

The canonical Windows creation owner checks the selected executable's native
file-version resource before preparing a pane. An old or unknown version
refuses new creation from that executable. When the PATH copy is old, the
owner selects the newest side-by-side or WinGet package copy that passes the
same 3.4.0 floor and puts its directory first on the new pane's PATH,
including a PATH value that the launch plan sets for the pane; no
fallback admits an older binary. If no copy passes, the refusal names the
side-by-side update as the remedy. This read starts no provider process. An existing-session join returns before that check,
so legacy reattachment does not acquire a new creation-version requirement.
Manager checks the same passive metadata after the join decision and before
selecting a creation owner, preserving that floor even with an older Core or
the legacy creation path. Existing joins bypass both checks.
Native qualification pins and its exact-version validator must agree; the
workflow/validator consistency control checks both prerequisite release URLs.

For an existing worktree, the interactive resolver carries a fresh positive mux
observation as the boolean `mux_join_existing` launch-plan hint. Manager consumes
it only to join that exact worktree's session, without starting observation or
creation helpers again. An attachment failure exits without falling through to
creation. Missing, false or non-boolean hints retain normal native observation;
unknown presence never authorizes creation. This hint is not an absence proof,
a persisted liveness cache or permission to replay a cold-start prompt.

A missing listing is not proof of process death or a safe replacement target.
PSMux can run registration cleanup on startup even for nominal reads such as
`has-session`, `list-sessions` and version queries. Public reports
[psmux/psmux#622](https://github.com/psmux/psmux/issues/622),
[psmux/psmux#546](https://github.com/psmux/psmux/issues/546) and
[psmux/psmux#650](https://github.com/psmux/psmux/issues/650) describe live registration loss
or subsequent orphan reaping. A successful but incomplete listing is another
failure class ([psmux/psmux#809](https://github.com/psmux/psmux/issues/809)).

Worktrees activity and batched status cross-reference exact session names from
the selected client's bounded `list-sessions` response. On Windows, the reader
first uses the Driver's passive file-version check to select a PSMux 3.4.0 or
newer client. It never executes a legacy or unknown client to qualify it.
Blank rows (including a trailing empty row), malformed counts, duplicate
sessions, diagnostics and failed queries remain unknown. Only the qualified
client's explicit no-server response confirms an empty Windows catalog.
This name-based observation does not require coverage of unrelated server
processes and does not prove that a checkout is safe to remove.

Creation/check-or-join, resident mux enumeration and Manager discovery/client
refresh retain the Driver owner's
[finite native observation](../../../libs/copilot-driver-core/README.md#finite-psmux-observation).
Their missing-rendezvous and process-custody guards remain independent.
Last-known mux presence retains its original timestamp when fresh observation
fails; it is not refreshed into current proof. Independent worktree, Git and
process facts can still publish.

Open can reattach to an observed live session without creating or reseeding
it, and cold Open uses the same Driver check/create lease. Unknown presence
refuses cold creation/seed rather than guessing that the old session died.
Manager qualifies observation support from its selected Worktrees interpreter;
an older provider without that declaration stays unsupported, with no passive
PSMux CLI fallback. Explicit foreground attach/create and provider writes still
run PSMux and retain its startup-cleanup risk. This mitigation does not repair
lost registrations or authorize provider restart, recovery or a host update.
Fresh observations do not reactivate automatic pane discovery/status-bar
repainting through the PSMux CLI. The elected Worktrees monitor publishes its
computed session options directly through Driver Core's native status transport,
with native-incarnation fences and readback; Manager is optional. Fresh matching
Manager mappings remain usable. Other connected native sessions require a unique
canonical tracking record and a live pane at that record's exact worktree path,
with at most two attribution probes per round-robin pass. Attributed paths feed
the existing scheduled content refresher, not a second updater or the render
path. Unknown or ambiguous targets receive no write. Cached in-pane bar text is
not fresh listing evidence.
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

This mitigation retains the automatic PSMux CLI restrictions while
restoring native status publication, fresh listings and client-count refresh. It does **not** make explicit
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
an orphan, attaches a client, paints status or sends pane input. A process
that verifiably exits during the census (its PID no longer opens, or its exit
code is observed) is absent. A process this token may not open, such as an
elevated server observed by a medium-integrity monitor, cannot be classified
as server or client, so it is kept as a possible server under its
system-snapshot start time: authenticated registration coverage must still
prove it, and an uncovered inaccessible process (including an elevated
client) keeps the result unknown. Any other unreadable process, unsupported
command-line mode, missing live-server registration, PID reuse, rename,
duplicate names across namespaces, authentication failure, incomplete reply
or changing inventory remains unknown; the error names the PID, the
unreadable field and the Win32 error where one applies.
`PSMUX_DATA_DIR` must be absolute when set; live
servers whose registrations are outside that selected directory also prevent
a complete result. This conservative action-time support does not re-enable
periodic PSMux CLI discovery or change cleanup authority. A silent failed PSMux CLI
presence query still cannot authorize a cold replacement, and failed
reachability does not make a mux-homed process reclaimable.

### In-place PSMux replacement: preserve the running image name

Prefer the normal side-by-side update above. The installer does **not** move
live images: its native live/unknown-provider guard selects side-by-side
installation, and in-place WinGet package replacement requires confirmed
provider absence. Do not bypass that guard with `--uninstall-previous`,
`--force`, a process stop, or an ad-hoc backup rename.

An explicitly approved **in-place binary replacement** is a different
operation from uninstalling/reinstalling the portable package. It must preserve
the old running image as well as install the incoming binary. PSMux 3.4.0's
`cleanup_stale_port_files` checks server image stems against
`psmux`/`tmux`/`pmux`. Renaming a running `psmux.exe` to `psmux.old.exe`,
`psmux-backup.exe`, or another stem can cause a subsequent client startup to
delete that still-live server's port registration. Even a nominal CLI read can
run that cleanup. Keeping a copy of the bytes elsewhere does not repair the
running process's image identity.

Use this flow only with the installation owner's approval and an incoming
publication method that does not terminate existing providers:

1. Exclude competing package updates through the existing owner. Inventory
   the affected servers using native process/registration observation, not
   `psmux -V` or `list-sessions`. Record each PID, native creation identity and
   actual executable path, the existing registrations, and the incoming
   release/version. Verify the incoming bytes through the normal source and
   file-version checks before moving anything. Unknown ownership, unreadable
   image paths or an unsupported updater stop this operation.
2. Resolve a **non-disposable, installer-owned retention directory on the
   same volume as the running image**. The standard layout is
   `%USERPROFILE%\.agent-worktrees\psmux\retained\<version>-<pid>\psmux.exe`.
   Create a fresh destination; never overwrite another retained generation.
   If that root is on another volume, use an owner-approved durable location
   on the image's volume or stay side by side. Do not substitute Temp, a test
   fixture, an expiring scratch directory, or a copy/delete fallback.
3. Revalidate the recorded native identities and paths immediately before
   the move. Move each distinct running image **without changing its basename
   from `psmux.exe`** into its retained directory. For example, with both paths
   already resolved and qualified, use
   `Move-Item -LiteralPath $runningImage -Destination $retainedImage -ErrorAction Stop`,
   where `$retainedImage` ends in `\psmux.exe`, not `.old.exe` or `.bak`.
   Several servers may share one image: retain it once and account for every
   dependent PID. Preserve required companion files; if their coexistence
   cannot be established, use the normal side-by-side flow instead.
4. Re-read native image identity for those same PIDs and creation identities.
   Every affected process must still be running from its intended retained
   `psmux.exe` path. A successful file move alone is not proof. On a partial
   or uncertain result, preserve the journal and inspect both paths rather
   than repeating the move or starting a PSMux client.
5. Publish the verified incoming binary at the original `psmux.exe` path
   through the approved installation owner, without an uninstall/kill phase.
   Read back its file version and the owner-selected client path. Keeping
   the old process alive does not by itself prove the incoming selection.
6. Verify preserved server registrations, native session/pane observations
   and existing joins without creating or reseeding sessions. Record the
   actual outcome and retained paths in the owner's upgrade journal. Keep
   each retained image until all dependent processes have exited naturally
   and its other owner references are released; age, an incoming version
   check, or a completed installer is not deletion permission.

**Recovery after an unsafe rename:** do not signal or kill the server, delete
port files, or run another PSMux client to diagnose it. Pin the affected native
PID/creation identity and the actual renamed image first. Under the same
owner's exclusion, move that exact image on the same volume into a fresh,
non-disposable retained directory **as `psmux.exe`**, then re-read the same
process's image path and existing registrations. Do not overwrite an incoming
`psmux.exe` to restore the name. An image already kept in Temp also needs a
durable retained home, even when its basename is correct. If cleanup already
removed registrations, fixing the filename does not recreate them or prove
the sessions recoverable. Preserve the processes and evidence and ask the
existing native owner whether supported registration recovery is available;
report it as unavailable when it is not. Do not invent rows, restart the
provider, or claim success.

### Resident state-only status projection

The elected monitor projects already-computed Worktrees state, not Git work.
One completed native inventory batch supplies session membership, incarnation
and attached-client counts for the pass; it does not probe each worktree with
`has-session`. Unknown inventory is not an empty catalog, and a cached live
mapping absent from the current batch does not authorize a status write.

Each presentation batch reads each project's passive current-roster snapshot
once. Current content-disposition closure descriptors take precedence over completed
segment-cache values. Otherwise the monitor retains a completed segment or
marks advisory/pending state explicitly; projection never invokes the
compute-on-miss segment getter, Git classification or a refresh request.
The normal TMux monitor path also uses this read-only segment view. Explicit
status commands and the existing Git sweep, lifecycle and watcher owners retain
their own refresh behavior.
Managed live mux projects remain enrolled in that scheduled roster computation
even without a Picker, list request or runtime demand. Passive roster reads do
not enroll runtime demand or request refreshes. Path lookup uses one per-project index
and refuses ambiguous paths; current CONVO descriptors retain their computed
conversation turn count, while advisory roster counts stay suppressed.
Path identity follows the segment cache's normalized real-path rule, including
trailing separators and symlink aliases.

Roster `ACTIVE` is a liveness disposition, not a content status. For observed
mux worktrees in that state, the scheduled computation owner also refreshes the
separate content-segment cache after releasing the roster observation ticket.
The cache retains its existing TTL, invalidation and cleanup ownership.
Every content observation rechecks lifecycle admission and governance; pending
priority ends the batch rather than waiting under the shared lock. A stored
empty project remains a known POSIX cache owner, avoiding repeated path scans
without weakening cleanup fences.
Presentation prefers completed fresh content bytes and never substitutes
`ACTIVE` for DIRTY, WIP or CONVO. When an identified active roster row outlives
the fresh content entry, the same cache owner can retain its last completed
segment with an explicit `(stale)` marker. The marker applies to the whole
retained string, including any historical counts. This presentation-only copy
is bound to the project, normalized checkout path, worktree ID and creation
nonce. Recreating the same ID at the same path with a different nonce refuses
the predecessor's copy. The roster path must match the managed checkout path.
Identity is captured from the actual rendered record together with its returned
segment, not from an earlier alias lookup. Missing, mismatched or multiple
renderer observations cannot populate this copy; ordinary fresh output is
unchanged.
An otherwise usable renderer record without a worktree ID cannot retain it.
The copy survives ordinary expiry, invalidation and an in-flight observation,
but never supplies
fresh-cache, hook or action evidence. Missing or unsupported roster subjects,
retained cleanup and a retiring owner cannot use it.
No completed matching content still means pending; projection never triggers
Git work to fill that gap. The content TTL, invalidation and refresh cadence
remain unchanged.
Registered standalone TMux paths also enter a separate content pass after the
scheduled served-session projection, even when no adopted project or tracking
record exists. This pass uses the same content cache and restores each
captured project context under the existing lock, skipping work when lifecycle
priority is pending. A cold read can remain pending for one pass; subsequent
projection reads completed content without invoking Git.

Native presentation runs before bulk roster collection. During that same
owner's roster pass, moderated cooperative progress between record observations
and serialization can project another batch without waiting for the whole
project's Git roster. It creates no thread, daemon or second publisher, preserves
the active project and observation/cleanup ownership, and skips publication
when lifecycle priority is pending rather than waiting inside the roster lock.
Publication duration does not consume an individual Git observation's deadline.

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
applies ordinary options to the named session. PSMux create/join also
automatically sources passthrough and Companion bindings into that worktree's
private server; server-global directives in that fragment do not target the
other PSMux servers. Shared-server tmux key tuning is deliberately opt-in.
Ordinary launch does not rewrite personal `~/.tmux.conf` / `~/.psmux.conf`.
The separate `apply-mux-keybinds.{sh,ps1}` helpers default to persisting a
managed block when explicitly invoked; the PowerShell helper additionally
applies the fragment to every listed live server, including personal sessions.
Legacy owned-file retirement retains its own lifecycle/approval boundary,
not permission to remove unknown or edited personal files.
Full detail: [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux).

## PSMux wheel input

The PowerShell helper and private-session passthrough fragment enable
`mouse on` and `scroll-enter-copy-mode off`. PSMux handles wheel-up and
wheel-down natively, outside the root key table; clearing that table does
not require re-adding tmux's `WheelUpPane` / `WheelDownPane send-keys -M`
bindings. PSMux does not implement those mouse-key bindings.

The target and receiving application still determine delivery:

- The reported [PSMux3.3.4 receiver](https://github.com/psmux/psmux/blob/v3.3.4/src/window_ops.rs#L923-L977)
  forwards both directions to alternate-screen applications. A main-screen
  application does not acquire mouse forwarding from a tmux-style binding.
- [PSMux3.4.0's actual predicate](https://github.com/psmux/psmux/blob/v3.4.0/src/window_ops.rs#L396-L398)
  also permits an application-attributed mouse protocol. Applications that
  do not satisfy its gate retain native scrollback/copy-mode behavior; no
  unconditional arrow-key or Enter translation is installed.

These are source-supported capability boundaries, not a census of installed
versions or a guarantee for every frontend. Successful `source-file` execution
alone does not prove that an application received a wheel event. Ordinary
tmux retains its supported mouse bindings. New-session version selection,
older-session compatibility and runtime upgrades keep their existing owners.

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
