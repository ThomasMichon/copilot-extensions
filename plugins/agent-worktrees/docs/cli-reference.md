# Agent Worktrees -- CLI Reference

```bash
agent-worktrees <subcommand> [options]
```

## CLI mode (no project binstub)

The generic `agent-worktrees` command works without a project binstub. If
no project context is set it prints the command catalog and a recommended
next step rather than erroring. Target a project explicitly with
`--project <name>` (or `-p <name>`):

```bash
agent-worktrees --project my-control-harness worktree list
agent-worktrees -p copilot-extensions worktree create --json
```

Running a project binstub bare (e.g. `my-control-harness`) still launches the
interactive picker.

## Knowledge plugin composition

A stateless harness paired with a private knowledge worktree can compose the
operator's plugin settings into the harness worktree before Copilot discovery:

```bash
agent-worktrees knowledge compose-plugins [--json] [--cwd PATH]
agent-worktrees knowledge compose-plugins --harness-path PATH \
  --knowledge-path PATH [--json]
```

The command works from a neutral directory. `--cwd` activates the adopted
project containing that path before resolving its tracked pair; explicit
checkout paths bypass pair lookup. It writes the harness's gitignored
`.github/copilot/settings.local.json` and points
`directory`/`local` marketplaces at the paired **knowledge worktree** rather
than its anchor. It also carries operator-specific remote marketplace
declarations and enabled plugins. Committed harness settings remain the generic
base; already-provided base entries are not duplicated, and unmanaged local
settings are preserved. Exact managed values are recorded in the local file so
re-pointing can retire stale entries without deleting operator edits.

The command is also the launcher's idempotent safety preflight. A stale,
missing, unbound, or binding-mismatched **tracked harness pair** retires only
marker-owned values that are still exact, preserves modified/operator values,
removes the marker, and returns success with `action: "retired"` plus the
original `pair_error`. An ordinary untracked or unpaired repo, or a legitimate
marker-managed anchor overlay without a pair identity, returns success with
`action: "no-op"` and is not changed. Malformed/unreadable settings or an unsafe
retirement return exit `3`; launchers surface that detail and stop before
Copilot starts.

The Worktree Picker launcher runs this command after resolving the selected
worktree and before starting Copilot. `--cwd` supplies the real worktree during
the deprecated advanced **Bare resume** recovery path, where the launch process
itself starts from the user's home. Prefer normal Resume; Bare resume remains
only as a temporary diagnostic fallback.

## Local marketplace source overrides

Portable repository settings may keep a Git-backed marketplace source while a
developer has a same-named checkout registered with agent-worktrees:

```bash
agent-worktrees reconcile-marketplaces [--cwd PATH] [--json]
```

The reconciler reads user-global and repository marketplace declarations. For
each `github` or `git` source, it uses only an exact same-named `repos.yaml`
entry, requires a contained `.ai` marketplace whose manifest name matches, and
writes a source-only `directory` override to the checkout's gitignored
`.github/copilot/settings.local.json`. Missing, stale, mismatched, or removed
checkouts fall back to the committed remote source. Unrelated local settings
and all `enabledPlugins` values are preserved.

Worktree creation, adoption, and the launcher preflight seed the override before
Copilot plugin discovery. A session-start repair pass reconciles later drift and
asks for a restart only when it changes the file.

## Headless projects (CLI-only)

Adopt an external repo as a **headless** project to drive its worktree
lifecycle from another session without ever launching Copilot inside it:

```bash
agent-worktrees register copilot-extensions \
    --repo-dir ~/src/copilot-extensions --headless
```

A headless project records `headless: true` in its `config.yaml`. Running
its binstub bare lists worktrees and the available commands instead of
launching an interactive session:

```bash
copilot-extensions                      # lists worktrees + usage (no launch)
copilot-extensions worktree create      # create; print id + dir
copilot-extensions worktree push <id>   # squash + rebase + push
copilot-extensions worktree finalize <id>
```

This collapses the manual `git worktree add -> edit -> squash -> rebase ->
push -> remove` ritual into the same lifecycle commands, driven from your
existing (e.g. `my-control-harness`) session.

## Worktree namespace

`worktree` groups the non-launching lifecycle verbs as a discoverable
alias over the top-level commands -- none of these launch Copilot. Use it
to create and manage worktrees from the CLI (e.g. to drive an external
repo's worktrees without opening a session inside it):

```bash
<project> worktree create [--json]      # create; print id + dir, no launch
<project> worktree list [--json]        # this project's worktrees
<project> worktree status <id>          # git status of a worktree
<project> worktree push <id> [--title]  # squash + rebase + push to default branch
<project> worktree finalize [id]        # validate on upstream, then clean up
<project> worktree cleanup              # remove orphaned/finalized worktrees
```

`worktree create` returns the new worktree's id and directory without
launching into it; it appears as `unused` in the project's picker/list
until it has commits or a live session. The equivalent top-level verbs
(`create`, `list`, `status`, `push-changes`, `finalize`, `cleanup`)
continue to work unchanged.

## Session Lifecycle

> The end-to-end narrative — states, the two landing paths, held/follow-up and
> serial-vs-parallel PRs — is in
> [Worktree Lifecycle & Change Management](worktree-lifecycle.md). This table is
> the verb catalog.

| Subcommand | Description |
|------------|-------------|
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan. `--new` creates + launches a **muxed interactive** session (refused without a TTY) |
| `create` | Create a worktree **programmatically** -- no launch, no mux; prints id + path (add `--json`). The path for agents and daemons |
| `push-changes` | Push worktree changes to remote default branch (squash, rebase, push). Aborts if the pre-squash fails (`--allow-unsquashed` to opt into individual commits) |
| `finalize` | Validate the branch's content is on upstream; prune the worktree/branch only when idle (deferred while a session is live). The creating agent owns child cleanup; `--abandon` is refused without an operator-directed `--handoff-to <recipient-or-flow>`, recorded on each re-homed obligation |
| `mark-complete` | Manual recovery -- set tracking status flag only (hidden from help) |
| `claims` | The worktree's **resource-obligation ledger** (accountability for what it allocated; effort `resource-obligation-settlement`). `claims [id]` shows the ledger; `claims add <kind> <ref> [--owner-ref <m/p/w>]` journals an outbound claim; `claims settle <ref> [--released]` marks it at-rest/released; `claims release <ref> [--remove]` retires one; `claims sweep [--apply]` runs the never-wedge reclaim (flip a provably-gone+safe `active` claim → `abandoned`; dry-run default); `claims orphans` lists the durable orphanage (obligations re-homed by a `finalize --abandon`); `claims cleanup [<ref-or-source-worktree> ...] [--apply]` is the acting consumer that reclaims matching orphaned resources (delete the CodeSpace, finalize the cross-repo worktree) and drops settled entries. No selector means the entire orphanage. Same-machine, best-effort, dry-run by default |
| `cleanup` | List and remove orphaned or finalized worktrees |
| `gc` | Garbage-collect this project's worktrees on this machine: tracked reap (cleanup verdict) + **managed system/bridge leak sweep** (`--no-managed` to skip) + orphan-directory sweep + **orphaned launcher-shell reap** (`--no-reap-shells` to skip) + `git worktree prune`. `--dry-run` lists without removing; `--json` reports the managed + orphan + shell sweeps. Also runs automatically on the no-daemon cadence (picker launch + session end) |
| `reap-sessions` | Reap leaked `wt-<id>` tmux/psmux sessions whose worktree is finalized/gone/untracked **and** idle past the grace window (spares attached/active/busy) |
| `reap-shells` | Reap **orphaned launcher shells** -- pwsh/python `-m agent_worktrees` scaffolding stranded by a force-closed terminal (parent exited, nothing running under them). Engineered to fail safe: **positive launcher-signature** matching only (a blank-command-line service can never match), session-0/service/ACP vetoes, a live-descendant spare, self-preservation, and an idle gate. **Reports candidates by default; `--yes` terminates.** Reclaims the scaffolding only -- Copilot itself is handled by `reclaim`, mux sessions by `reap-sessions` |
| `reclaim` | Free the **exact** Copilot process(es) bound to a session/worktree, resolved from Copilot's own `inuse.<pid>.lock` claim -- precise, never splashing onto a sibling session or a worktree that merely shares a cwd. The primitive for **bare** orphans (a Copilot launched straight in a terminal, invisible to the `wt-<id>` mux fleet view; e.g. its terminal was closed/wedged). Target with `--session-id` / `--worktree-id` (infers from cwd) / `--all`, restrict with `--bare-only`. **Dry-run by default; `--yes` terminates.** Never reaps the process tree of the running command. Freeing an idle orphan loses nothing -- the session stays resumable |
| `remux` | Restore a running **bare** (un-muxed) Copilot to the worktree's mux fleet. On Linux/WSL, reparent it into the `wt-<id>` tmux pane via `reptyr`, preserving the live process. On Windows, where ConPTY cannot adopt an arbitrary running process, preview the precise reclaim-before-resume plan and pass `--yes` to retire only the confirmed Stop-unreachable owner; the structured result says to resume next through the normal PSMux launcher. Target with `--session-id` / `--worktree-id` (infers from cwd). The Pickers expose the combined preparation + launch path as **Restore**. |
| `backfill-sessions` | Explicitly scan legacy session state to populate empty registries and titles, derive legacy controller relations from authoritative creation fields, and inspect a bounded number of exact session projections (`--projection-budget N`). Local missing/stale projections are repaired; restored, foreign, ambiguous, colliding, and newer state remains report-only |
| `doctor` | Diagnose machine-wide Picker pivot hygiene plus this project's `config.d` and worktree/session **record + session-state** health: corrupt tracking records, empty session registries + missing titles, legacy controller metadata, bounded exact-ID projection drift (`--projection-budget N`), stale `active`+`completed_at` status, orphaned 0-user-message session shells (`--gc-sessions`, destructive), and cwd/path misalignment. `--fix` repairs local authoritative/controller/projection state but keeps restored, foreign, ambiguous, colliding, and newer state report-only. Runs outside a project for machine-wide pivot diagnostics; project health/config is then explicitly skipped/absent. `--json` emits the exhaustive report |
| `status` | Show worktree git status; **write mode** (`--summary "<one-liner>"` / `--title "<headline>"` / `--follow-up` / `--resolved`) annotates THIS worktree's Picker disposition; **history mode** (`--history` `[--limit N]` `[--json]`) prints this worktree's durable disposition trajectory (summary/title over time). A `postToolUse` hook nudges you to refresh it as work drifts (`AGENT_WORKTREES_NUDGE=off` to silence) |
| `recent-messages` | Show a worktree's latest session's last N conversation messages (`--worktree <id>` `--limit N`, JSON) -- the read-side companion to the disposition summary; reads `events.jsonl` directly. Backs the picker's **Messages** viewer |
| `list-sessions` | List Copilot sessions with interface/origin metadata, append-only activation intervals, resolved head revision, numbered handoffs, and any bound profile-assignment metadata (JSON); `--worktree <id>` scopes to one worktree and `--all-projects` enumerates every adopted project |
| `session-recovery` | Read one exact session's reciprocal projection and emit validated, machine-readable recovery state without changing bindings. Version-aware output includes schema version, history completeness, relation overflow (`omitted_relations` is `null` for v2 overflow), and tombstone-overflow diagnostics. `--stdin` accepts a sessionStart payload; `--emit-context` renders the bounded pointer used when startup cannot establish an authoritative worktree binding |
| `session-lineage` | Read one exact session projection and validate every retained worktree relation against authoritative records. Reports restored evidence, missing records, unsupported or invalid projections, schema/history completeness, relation overflow, full-key v1 or opaque-digest v2 tombstones, and tombstone overflow without enumerating the live session-state root |
| `head-session` | Project-agnostic replay of a worktree's monotonic head-transition ledger, including pending handoffs (JSON; fail-open when untracked) |
| `worktree-lineage` | Project-agnostic, bounded graph of one authoritative worktree's sessions, transitions, handoffs, controllers, normalized reciprocal presentation, projection health, and explicit terminal/fork/cycle/missing-session findings (JSON) |
| `conclude-session` / `link-succession` | Project-agnostic write primitives for explicit session conclusion and exact-token handoff succession links (JSON) |
| `conclude-disposable` | Project-agnostic, exact-id terminal conclusion for an explicitly disposable CLI worker. Requires `--policy disposable-cli` and `--owner`; preserves live sessions, all dirty work (including generated local overlays), local commits, follow-ups, claims, pairs, and open PRs. A clean branch with zero commits ahead of upstream may remain behind without being rewritten, then the command marks the record managed/final. `--remove` immediately runs the conservative managed-GC verdict for only that exact id, with fresh lifecycle/liveness checks; an already-removed id is idempotent success. |
| `session-transcript` | Emit a Copilot session's renderable transcript events by session id (JSON) |
| `session-lock` | Write/remove a session-state lattice lock beside Copilot's session state (bridge/mux liveness marker) |
| `session-backend` | Inspect or operate the configured worktree session host: `ensure` creates/verifies the exact AHP binding, `status` reads the persisted binding, and `dispose` retires the hosted session so finalization may proceed. With the default `direct` backend it reports disabled and changes nothing |
| `reconcile-sessions` | Run one bounded record/session/projection reconciliation pass and emit machine-readable repair and conflict counts; suitable for an optional low-duty scheduled backstop |
| `status-segment` | Print a styled status-bar segment for the worktree at the cwd (for a tmux/psmux status line) |
| `status-context` | Print a styled left status-bar segment: machine, environment, and repo:id4 for the worktree at the cwd |
| `status-updater` | Background loop that keeps a session's `@aw_ctx`/`@aw_seg` status vars fresh **off the paint path** (no per-render binstub spawn) |
| `list` | List worktrees from tracking records |
| `handoff-cutover` | Internal live-handoff primitive: spawn a seeded successor window in the existing mux or retire an old pane |
| `embody` | Agent-facing primitive to create/resume a detached mux+Copilot session in a worktree |

## Pull-request workflow

The `pr-*` family drives PR-gated landing (config `pr.enabled` / `pr.required` —
see [config-reference.md § PR workflow](config-reference.md)). `push-changes`
then targets the *feature* branch, never the default branch. The verbs are
self-describing: `pr-status` prints the active `flow:` profile, and `pr-merge`
refuses (naming the reason) on a repo where no consent label is bound. Full
narrative in [worktree-lifecycle.md § Landing the change](worktree-lifecycle.md).

| Subcommand | Description |
|------------|-------------|
| `create-pr` (alias `pr-create`) | Squash the worktree's commits, publish the PR head branch, and open the PR. Flags: `--title`, `--body`/`--body-file`, `--draft` (open not-ready-for-review), `--new` (force a fresh head branch for a parallel PR), `--no-open` (push only), `--hold` (deprecated alias for `--draft`) |
| `pr-ready` | Move a draft PR **out of draft** — request review |
| `set-pr` | Record PR metadata (`--url`, `--number`) when the PR was opened out of band by a provider sub-agent |
| `pr-status` | Show tracked PR metadata + live verdict / conflict / merge state; prints the `flow:` profile and flags pull-forward once merged |
| `pr-watch` | Block until the PR moves (`wait <repo> <pr> [--until …]`) and wake the caller with a race-proof cursor; `cursor <repo> <pr>` prints the current baseline |
| `pr-merge` | Signal **merge consent** on an approved PR (applies the bound `automerge_label`); the review gate merges when satisfied. `--all` / `--loop` for sweeps |
| `pr-complete` | Reconcile the worktree after its PR merged — fast-forward past the squash-merge (or rebase), dropping the local commits the squash already absorbed |
| `pr` | Namespace grouping the `pr-*` verbs |

`get pr-profile` / `get pr-required` / `get pr-provider` report the repo's PR
disposition (`direct` | `pr-human-merge` | `pr-agent-merge` |
`pr-self-merge`) so you know which verbs apply before signing off.



## Status bar segment (tmux / psmux)

`status-segment` prints a **single styled line** classifying the worktree at
the current directory (or `--path`) relative to its upstream default branch.
The launcher wires it into each session's bar **per session** (it does **not**
own your global `~/.tmux.conf` / `~/.psmux.conf`) -- but the bar does **not**
poll this command on its render path. Instead the `status-updater` watcher
calls it *off* the render path and pushes the result into the `@aw_seg` session
option, which the bar reads with zero per-render spawn (see *Off the paint
path* below):

```tmux
set status-interval 15
set status-right '#{@aw_seg} %H:%M '
```

Output (what the watcher stores in `@aw_seg`) is the resolved session title
followed by a colored state block:

| State | Color | Meaning |
|-------|-------|---------|
| `DIRTY` | red | Working tree has uncommitted changes (modified, staged, or untracked) |
| `FINAL` | green | Clean; work landed / fast-forwardable to upstream |
| `UNUSED` | grey | Clean; no commits **and no conversation** since the fork point |
| `CONVO` | teal | Clean; no commits, but the session held conversation turns (annotated with the turn count, e.g. `CONVO 12💬`) |
| `WIP` | amber | Clean; ahead with content not yet on upstream |
| `ORPHAN` | magenta | No merge base with upstream |

A trailing `↑ahead`/`↓behind` tag mirrors the picker's inline sync status. The
`CONVO` state refines `UNUSED` using session turn-count detection: a worktree
with no committed work is only truly *unused* when its session also held zero
turns; once it has held conversation, it renders as `CONVO` with the turn
count (mirroring the picker's `💬` annotation and `cleanup`'s
"conversation-only" preservation). The upstream default branch (`main`/`master`)
is auto-detected per repo, so the segment works regardless of which project the
binstub belongs to.

Flags: `--path PATH` (classify another worktree), `--fetch` (refresh
behind-counts from the remote -- off by default so the poll stays cheap),
`--plain` (no `#[style]` directives), `--no-title` (state block only).

### Machine-readable state: `list --json --classify`

`list --json --classify` enriches each worktree record with its git-derived
classification (`state`, `ahead`, `behind`, `dirty`) so a consumer -- notably
the multi-machine picker -- gets canonical state per machine (a remote's own
state travels in its `list` output over SSH; the local picker cannot
git-classify a remote worktree). Classification is **opt-in** because it costs
~5 git calls per worktree; a bare `list --json` stays fast.

The emitted `state` draws from the **same `WorktreeState` vocabulary the status
bar uses**, including the session-derived `convo` (a clean, commit-less
worktree whose session held conversation turns -- the lowercase data-contract
form of the bar's teal `CONVO` block). Centralized in
`git_ops.refine_state_with_session` so the bar and the picker can never drift
apart. Without `--classify`, records carry no `state` key.

Every row carries an additive `reciprocal_relation` object for presentation.
Its `binding` and `control` axes preserve the difference between an
authoritative bound session and a separate controller, while `state` provides a
compact summary: `bound-here`, `controlled-elsewhere`, `handed-off`, `terminal`,
`ambiguous`, or the no-relation baseline `unbound`. `actions` contains a
`navigate-worktree` target only when one active controller identity is
unambiguous. Unknown, restored-stale, unsupported, incomplete, or conflicting
evidence reports `ambiguous` with no action. These fields never alter the
record's head, liveness, occupancy, or resume target.

When a worktree record carries profile-assignment history, the ordinary
`list --json` row includes `current_profile_assignment`: the bound assignment
for the worktree's asserted head session, or `null`. A never-assigned row omits
the key. Pass
`--profile-assignment-history` on an explicit diagnostic/detail read to also
include the bounded `profile_assignments` history and
`latest_profile_assignment` (which may be pending, bound, or abandoned).
Cache-polled Picker rows therefore stay constant-size as history accumulates.
Fields are neutral and machine-readable: policy, opaque assignment label,
selected profile, bag generation/position, timestamps, lane, disposition,
session binding, and optional handoff predecessor session. The one-shot launch
token is never included. `list-sessions --json` attaches the bound assignment
to its actual session row.

### Left segment: worktree identity

`status-context` prints the **left** side of the bar -- the worktree's
identity rather than its git state. The launcher applies it **per session**
alongside the right segment:

```tmux
set status-left-length 100
set status-left '#{@aw_ctx} '
```

It renders three fields:

| Field | Style | Source | Example |
|-------|-------|--------|---------|
| Machine | Black, bold | Tracking record `machine` (else live host detection) | `anomalous-potato` |
| Environment | Badge: white on an OS-keyed background (win=blue, wsl=purple, linux=orange) | Platform short code, matching the worktree id | `win` |
| Repo : id4 | Black | Record `repo` + the worktree id's 4-char suffix | `copilot-extensions:8e45` |

Like the right segment, the watcher classifies the worktree by `--path` and
stores the result in `@aw_ctx` (once -- identity is static for a session).
Outside a tracked worktree it falls back to live machine/platform detection and
omits the `repo:id4` field. Flags: `--path PATH`, `--plain` (no `#[style]`
directives).

### Off the paint path: `status-updater` (psmux + tmux)

Polling `#(agent-worktrees status-segment)` directly from the bar is tolerable
on **tmux**, which runs `#()` jobs asynchronously and caches the result between
`status-interval` ticks. **psmux** (Windows) does not: it runs `#()`
synchronously **in the render path**, so a ~600 ms binstub spawn (two fresh
PowerShell processes for the two segments) fired on every repaint. Under
Copilot's high-framerate TUI that made muxed sessions sluggish on anomalous-potato
and unusable on slower hosts.

`status-updater` is the **single, cross-platform watcher** that fixes this on
both muxes. The bar reads **precomputed session options** instead of spawning:

```tmux
set status-left  '#{@aw_ctx} '          # identity  (machine | env | repo:id4)
set status-right '#{@aw_seg} %H:%M '     # disposition block + live clock
```

Two seams spawn one detached updater per session (psmux via
`launch-session.ps1`, tmux via `launch-session.sh`):

- the **launcher**, on psmux/tmux create and on every attach/join; and
- the **`sessionStart` hook** (`register-session` → `_spawn_status_updater`),
  which re-asserts the updater at the start of every Copilot session.

```text
agent-worktrees status-updater --session wt-<id> --mux <psmux|tmux> --path <worktree>
```

It renders **in-process** (paying Python import once, never re-spawning the
binstub), pushes the static identity into `@aw_ctx` once, and refreshes the
dynamic disposition into `@aw_seg` every `--interval` seconds (default 15) via
the cheap native `set-option` verb. Between updates the bar does **zero**
process work; the mux only re-runs the strftime `%H:%M` clock. Non-worktree
sessions leave the vars unset and render a blank bar.

**Single-instance election + self-healing.** Because both the launcher (on
every attach/join) and the `sessionStart` hook may (re)spawn an updater, each
one claims an `@aw_updater` token; a newer updater wins and older ones
self-retire on their next tick (the cross-platform equivalent of the old
`flock`). An updater also retires when a **version deploy supersedes its
runtime** — the active version slot no longer matches its `sys.prefix` — so updaters
don't pile up one-per-version across deploys (dotfiles #911). The two spawn
seams are what make that safe: whichever fires next (a launcher attach or the
next session's `sessionStart`) re-seeds a current-version updater, so a bar left
dark by a supersede recovers instead of staying blank until a manual attach
(dotfiles #915).

**Liveness is transient-tolerant.** The loop distinguishes a *definitive*
"session gone" (`has-session` ran and reported non-zero) from a *transient* mux
hiccup (a timed-out/errored `has-session` under a busy high-framerate TUI). It
retires only on a definitive gone, a lost token, a superseding runtime, or a
sustained run of transient failures (a genuinely wedged mux) — a single timeout
no longer silently kills the bar for the rest of the session (dotfiles #915).

Flags: `--session` (required), `--mux {psmux,tmux}` (default: auto-detect),
`--path PATH` (worktree to classify), `--interval N` (seconds, min 2).



agent-worktrees does **not** deploy, overwrite, or delete your global
`~/.tmux.conf`. The launcher applies the bar and session behaviors with
`tmux set -t <session>` (session-scoped, no `-g`) when it creates or rejoins a
worktree session, so your personal tmux config and any ad-hoc tmux sessions
sharing the same server are left untouched. The single source of truth is the
deployed `~/.agent-worktrees/bin/session-options.sh`.

Settings that **cannot** be session-scoped -- server-global `escape-time` and
the keystroke-passthrough root key table -- are **not** applied automatically
(they would leak onto every session on the server). They live in the opt-in
`~/.agent-worktrees/bin/apply-mux-keybinds.sh`. Run it once per machine, or wire
it into a machine-restore flow, if you want that behavior: it persists a
clearly-marked managed block in `~/.tmux.conf` (so it survives server restarts)
**and** applies to any running server. The installer never touches
`~/.tmux.conf` -- only this script does, and only when you elect to run it
(`--no-persist` tunes the running server without writing the file; deleting the
marked block removes the settings).

> Both tmux (Linux/WSL) and psmux (Windows) are configured **per session** by
> the launcher: `session-options.{sh,ps1}` stamps the bar + behaviors with
> `set -t` (no `-g`), and the server-global keystroke passthrough lives in the
> opt-in `apply-mux-keybinds.{sh,ps1}`. agent-worktrees no longer owns
> `~/.tmux.conf` or `~/.psmux.conf`.


## Keeping worktrees current

The picker keeps idle worktrees aligned with the default branch, fast-forward
only -- it never rebases, merges, or discards local commits.

- **Inline sync status.** Each worktree row shows its relationship to the
  default branch: `↓N` (behind by N, i.e. stale), `↑N` (ahead by N local
  commits), or `↑A↓B` (diverged). Aligned worktrees show nothing.
- **Auto-fast-forward on resume.** Resuming a *clean* worktree that is
  strictly behind upstream fast-forwards it before the session and setup
  script run, so they see an up-to-date tree. A worktree with uncommitted
  changes or local commits (ahead/diverged) is left untouched. Disable
  per-invocation with `--no-fast-forward`, or globally with
  `auto_fast_forward: false` in `config.yaml`.
- **System menu -> Update stale worktrees.** Fetches once, then fast-forwards
  a single selected eligible worktree or all eligible worktrees in a batch.
  Only clean, strictly-behind worktrees with no local commits are eligible.

## Garbage collection

agent-worktrees runs **no persistent monitor process**. Stale state is reclaimed
on a cadence at two natural lifecycle boundaries -- **picker launch** and
**session end** -- so nothing accumulates without a scheduled task:

- **Orphan mux sessions.** Leaked `wt-<id>` tmux/psmux sessions of finalized /
  gone / untracked worktrees are reaped once idle past the grace window
  (`reap-sessions`); an attached, active, or recently-busy session is spared.
- **Leaked system/bridge worktrees.** The daemon-owned kinds routine `cleanup`
  skips can leak (a crashed daemon, or a caller that finalized without tearing
  its bridge worktree down). The managed sweep reaps only the **provably dead**
  ones -- FINAL or UNUSED, no active process (mux/session/attach), no follow-up
  flag, idle past the grace window -- and rides the same cadence. Run it
  explicitly (with the tracked + orphan-directory sweeps) via `gc`; a caller
  worktree's session ending is when its bridge worktree becomes reapable.
- **Finished session worktrees.** Ordinary (non-managed) worktrees whose work is
  already landed -- `finalized` / merged / git-COMPLETED -- are auto-collected on
  the same cadence once **idle past the grace window** (default 48h;
  `AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS` overrides), so finished user worktrees
  don't pile up until a manual `cleanup`. The collection reuses the *exact*
  conservative safety of the manual cleanup: only the strictly-SAFE buckets are
  removed; an in-flight claimed resource, a `follow-up`-flagged, paired-pending,
  `empty`/conversation-only, dirty, wip, or unmerged worktree, and any live
  session/mux are all spared. No network fetch is done, so a merge not yet
  locally provable simply waits for a later pass. Disable entirely with
  `AGENT_WORKTREES_NO_AUTO_CLEAN=1`.

## Installation & Config

| Subcommand | Description |
|------------|-------------|
| `install` | Full deploy: runtime + project config + binstubs + terminal profiles |
| `register` | Register a new project (create config + binstub without full reinstall) |
| `uninstall` | Remove worktree manager |
| `update` | Re-deploy runtime from repo source + refresh every active registered plugin payload/runtime and opportunistically refresh installed-but-inactive payload inventory, then update sibling modules and fast-forward the managed repo anchor(s). An inactive inventory refresh failure is advisory; an active plugin refresh failure fails the update. Version-gated: skips a runtime whose deployed version already matches its payload (`--force` re-deploys all active runtimes; `--no-anchor-sync` skips the anchor sync) |
| `install-status` | Show installation and deployment status |
| `deploy-instructions` | Retire migrated managed instruction files (machine identity now via the `session-machine` sessionStart hook) |
| `machine-context` | sessionStart hook entrypoint: emit machine identity as `additionalContext` (cwd-gated) |
| `get` | Query config values (e.g., `agent-worktrees get repo-dir`) |

## Effort Focus

| Command | Description |
|---------|-------------|
| `effort-focus bind <README> --participant <name> --slice <slice>` | Bind this worktree to one open, repository-relative effort slice after validating containment, effort shape/status, and the declared participant/slice |
| `effort-focus show [--json]` | Inspect the raw binding and its current `open` / `closed` / `stale` state |
| `effort-focus bind ... --replace` | Explicitly replace the current effort/slice; silent replacement is refused |
| `effort-focus release --completed` | Release only when the effort is verified `Done`, every Plan and Validation Plan checkbox is resolved, and the active or standard dated archive path is verifiable |
| `effort-focus release --transfer "<tracked objective>"` | Release by recording the named objective that receives responsibility |

An open focus derives the existing `follow_up` and summary surfaces; it does not
create another cleanup flag. `status --resolved` is rejected while any effort
remains bound. Bind and completed release verify the authoritative Git
root; the existing session-conduct/history-digest read path re-inspects the
contained pointer under the recorded worktree path and stays silent on failure.
Bind also persists `follow_up=true` and replaces the durable summary; release
clears `follow_up` and replaces the summary. Re-assert any follow-up unrelated
to the effort after release.

## Services, Repos & Validation

| Subcommand | Description |
|------------|-------------|
| `services` | Service discovery, staleness checks, passthrough to installers |
| `repos` | Repos registry -- list, find, add, clone, srcroot, `account`/`account-for` (owner→gh-login map), `allow-edits` (break-glass edit grants) |
| `accounts` | gh account identity catalog (`accounts.yaml`) -- list/show/set/remove logins, scopes, login flows |
| `validate` | Validate core infrastructure files |
| `pre-launch` | Check bootstrap staleness (JSON output, for launch wrappers) |
| `reconcile-plugins` | Reconcile repo-adopted plugin payloads + gated runtimes (JSON output, for launch wrappers) |

### Repo-adopted plugin reconciliation (`reconcile-plugins`)

On an interactive launch, the launcher reconciles the anchor repo's
`.github/copilot/settings.json` `enabledPlugins`: for each
`<name>@copilot-extensions` it ensures the **payload** is installed (throttled
refresh) and the **runtime** matches the installed payload version, per the
plugin's `runtimeScope` (`none` | `universal` | `machine-gated`) and a multi-machine system
machine gate (`external-repos.yaml` `deploy_machines`). It is local and
version-keyed, so an unchanged re-launch does ~no work. Runs only after the
direct-dispatch boundary (plain subcommands never trigger it); opt out with
`WORKTREE_NO_RECONCILE=1`. See
[`docs/install-contract.md`](../../../docs/install-contract.md#automatic-reconciliation-at-launch-runtimescope) § "Automatic
reconciliation at launch" for the full policy. Headless `copilot -p` launches do
**not** reconcile (repo settings aren't merged there).

For an installation-cell-aware runtime, agent-worktrees delegates actual mode,
desired mode, and mutation authorization to the plugin's vendored
installation-context helper. An authoritative legacy runtime reconciles at its
legacy root without an `install.json` receipt. Namespaced active or
deactivation-required state reconciles only after the helper validates the exact
plugin receipt and root; activation-required, maintenance, invalid, foreign,
orphaned, revalidation, and provenance-uncertain states remain read-only.

### Deployment ownership (`extensions.agent-worktrees.auto_update`)

A `service.yaml` may set `extensions.agent-worktrees.auto_update: false` to
declare that another deployer (e.g. VAV) owns the service. agent-worktrees
then **skips it in automatic update/install sweeps** (`services --all update`
/ `--all install`). It still appears in `services list`/`status`, and an
**explicit** `services <name> update` (or `--all update --force`) runs it
regardless. Absent the flag, the service defaults to agent-worktrees
management.

## Development

| Subcommand | Description |
|------------|-------------|
| `dev` | Dev venv and test runner |
| `--version` | Print installed version |

## Diagnostics

| Subcommand | Description |
|------------|-------------|
| `activity` | View the persistent worktree/session lifecycle log |

The launcher and lifecycle code record high-level events -- worktree
created/resumed, session started/ended, Copilot exited, mux
attached/detached, changes pushed, worktree finalized/reaped, and
`finalize_skipped_removal` -- to a machine-global JSONL log at
`~/.agent-worktrees/logs/activity.jsonl`. Unlike the per-PID launcher
setup logs under `$TMPDIR/worktree-setup-logs` (capped at the 10 newest
and wiped on reboot), this log persists across reboots and keeps a
rolling 7-day window, so session-lifecycle anomalies can be reconstructed
after the fact. Every event carries the worktree id and, where known, the
session id.

```bash
agent-worktrees activity                       # full retained log (table)
agent-worktrees activity --since 2d            # last 2 days (2d/12h/30m/ISO)
agent-worktrees activity --worktree-id <id>    # one worktree's lifecycle
agent-worktrees activity --event mux_attached  # one event type
agent-worktrees activity --lines 50 --json     # last 50 events as JSONL
```

`activity-log` (append one event) is an internal hook used by the
launcher and is not intended for direct use.

---

## Installer Actions

The `install.ps1` and `install.sh` scripts support these lifecycle
actions:

| Action | Description |
|--------|-------------|
| `install` | Full deploy: runtime, binstub, config, terminal profiles, manifest |
| `uninstall` | Remove runtime and binstub (`--remove-config` for config too) |
| `status` | Check deployed runtime, config, PATH, worktrees, provenance |
| `update` | Re-deploy runtime + binstub, refresh marketplace plugin |
| `update-config` | Regenerate config.yaml (`--force` to overwrite) |
| `refresh-profiles` | Regenerate only Windows Terminal profiles/state |
| `provision` | Lean first-use runtime provision (venv + package + version marker) |
| `stamp` | Stamp the payload pointer/build metadata used by self-provisioning |

### Installer Flags

| Flag | Platform | Description |
|------|----------|-------------|
| `-ProjectName` / `--project-name` | Both | Project name (auto-detected from repo) |
| `-Force` / `--force` | Both | Overwrite config without confirmation |
| `-RemoveConfig` / `--remove-config` | Both | On uninstall: also delete config and metadata |
| `-Machine` / `--machine` | Windows | Machine name (auto-detected) |

### Programmatic Install (Outside Copilot)

```powershell
# Windows -- from the copilot-extensions checkout
cd <copilot-extensions-checkout>\plugins\agent-worktrees
.\scripts\install.ps1 install -ProjectName my-project
```

```bash
# Linux/WSL
cd <copilot-extensions-checkout>/plugins/agent-worktrees
bash scripts/install.sh install --project-name my-project
```

### Remote Deployment

```bash
ssh my-machine "cd <copilot-extensions-checkout>/plugins/agent-worktrees && bash scripts/install.sh update"
```

---

## Config Reference

> **Full reference:** [config-reference.md](config-reference.md) documents
> **every** option — top-level keys, all per-repo keys, the `pr:` workflow
> block, the in-repo `.agent-worktrees.yaml` overlay, backend profiles, and
> the platform-keyed hook maps. The example below is just the common subset.

`~/.{project}/config.yaml`:

```yaml
srcroot: C:\Data\Src              # or ~/src on Linux
machine: my-machine
platform: windows                 # windows | wsl | linux
repo_name: my-project
auto_fast_forward: true           # auto-FF a stale clean worktree on resume (default true)

repos:
  my-project:
    anchor: C:\Data\Src\my-project
    # worktree_root is optional; it defaults to a sibling
    # <anchor>.worktrees folder (here C:\Data\Src\my-project.worktrees),
    # matching the Copilot CLI's /worktree layout. Set it only to override.
    default_branch: main
    remote: origin
```
