# Agent Worktrees

Copilot CLI plugin for worktree-isolated sessions. Every Copilot CLI
session gets its own git worktree -- no branch conflicts, no stale state,
no stepping on parallel sessions.

agent-worktrees is **standalone-first**: enable this plugin, bootstrap the
runtime, and register any git repo you want to manage. You do **not** need a
separate "control harness", agent-bridge, CodeSpaces, containers, or any other
sibling plugin to use local worktree isolation. Those sibling plugins compose
gracefully when installed (extra picker pivots, cross-machine delegation,
CodeSpace/container resolvers), but their absence only disables those features.

Resolved machine topology treats native Windows and WSL as independent logical
machines, conventionally `host` and `host-wsl`. The shared machine owner
projects legacy combined entries without editing topology or historical
records. Local configuration resolution uses the actual platform for those
projected entries, so `get machine`, new ownership references, and bridge
registration name the correct logical machine. Explicit execution-space
configuration retains its existing canonical-key/platform mutation gates;
physical association never authorizes access to a sibling's ledger.
Identity probes refuse an invalid explicit registered source rather than
advertising the physical host or another platform's key.

## How It Works

On Windows, ordinary and passive status-monitor starts bypass the venv Python
redirector and use the real base **console** interpreter, preserving the venv
prefix and imports through the shared `agent-procutil` environment helper.
The returned process PID therefore identifies the runtime used by health and
route-generation ownership. Existing hidden-console, lifetime, cwd, and
credential-scrubbing controls remain unchanged. A venv whose required base
interpreter is unavailable is refused explicitly rather than launched with
ambiguous PID ownership. This does not change endpoint-driven promotion,
installer failure semantics, singleton ownership, or cleanup of existing pairs.

Completed numbered runtime slots are immutable. An unchanged completed slot
is reused; a same-version payload mismatch is refused before venv or package
mutation, including forced updates. Publish a new version for ordinary changes.
Failed activation quarantines readiness as `.install-complete.failed.json`
without erasing completion evidence. The resolver no longer sees a ready
marker, and subsequent installer admission still refuses rebuilding that slot.
Repair of a broken completed slot requires a distinct, health-checked generation,
not an in-place dependency reinstall into a running CLI/monitor's files.
Both installer entrypoints require successful completion publication before
activation. Missing bootstrap Python, an unavailable input fingerprint, or a
publication refusal stops before selector, last-known-good, monitor, and GC
changes. A successful health probe alone does not authorize activation.

Agent Worktrees has three shipped pieces:

- **Plugin payload** (skills, hooks, live-pulse extension) -- loaded by Copilot
  CLI when the plugin is enabled
- **Runtime** (versioned Python CLI) -- manages worktrees, launches sessions,
  handles finalization, and self-provisions on first CLI use when possible
- **Project binstubs** -- one launcher per registered project, plus the global
  `agent-worktrees` CLI

Project launchers are attributable global entry points. Each launcher invokes
the absolute payload-local agent-worktrees command that created it and carries
an ownership receipt under `~/.agent-worktrees/binstub-receipts/`. Routine
install, update, repair, and reconciliation refuse to replace a command owned
by another payload, replace any unreceipted command, or remove a receipt-owned
command whose bytes changed. Legacy launchers require the same explicit
transfer as every other unreceipted file. Windows command names are compared
case-insensitively. An operator can explicitly transfer a registered project
command with:

```bash
agent-worktrees reconcile-binstubs --transfer <project>
```

For isolated Python registry and project-launcher operations, `AGENT_HOME`
relocates the legacy registries, `.local/bin` launchers, and
`.agent-worktrees/binstub-receipts` ledger together without changing `HOME` or
`USERPROFILE` for Git/SSH credentials. A validated installation context still
selects its own registry root; cells sharing one harness home still arbitrate
the same launchers and ownership ledger. This does not isolate the native
installers or Windows Terminal profile refresh performed by `register`; use
`register-project-entry` for registry and project-launcher publication only.

The plugin installs via the Copilot CLI marketplace. The runtime installs
via init/install scripts (or first-use provisioning from the global binstub)
and provides the `agent-worktrees` CLI and per-project binstubs.

Structured installs capture the payload and canonical development libraries
in one staging tree, preserving their relative dependency paths. Packaged
releases retain their local library copies. Dependency builds use the captured
stage rather than rereading the original checkout; canonical symbolic links and
reparse points are refused rather than retained as external dependencies.

The runtime does not install Textual or the UI rendering stack. Picker and Mux
Companion presentation belongs to the separately installed Worktree Manager,
reached over a process boundary. The small `packaging` dependency is explicit
so reconciliation preserves version ordering and downgrade protection without
relying on UI dependencies.

## Independent Execution Spaces

Native Windows and WSL can register independent execution-space keys without
sharing project registrations, settings or ownership ledgers. Physical-host
grouping never makes distinct registered spaces local to one another. See
[Execution-space identity](docs/config-reference.md#execution-space-identity)
for explicit selection, compatibility and lifecycle fences.

## Captured child ownership

Bounded Git publication and local-guidance discovery/rendering use a headless
captured child on Windows, assigned to a kill-on-close Job before execution.
Job admission failure is explicit; there is no detached fallback. Timeout or
caller exit reaps descendants while preserving captured diagnostics.
Explicit descendant escape is prevented by this bounded Job; durable-service
launchers retain the shared helper's permissive default outside this seam.
POSIX retains its process-group timeout cleanup. This is not a durable-daemon
launch or an interactive terminal.

## PowerShell post-tool hook

The legacy-context PowerShell `postToolUse` hook contacts an already-running
resident monitor inside its existing shell, without launching Python or the
CLI on success. It uses the monitor's authenticated loopback JSON-line
transport, not HTTP. An unavailable resident falls back to the deployed
Python advisory evaluator without resending the request. Explicit installation
cells retain the existing validated Python client; Bash, pre-tool safety,
session-start reconciliation and its single-flight owner are unchanged.

This removes a child process, not the shell the hook host itself creates.

## Same-Machine AHP Sessions

Same-machine Agent Host Protocol (AHP) session ownership now lives in the
standalone **Worktree Manager**, not in agent-worktrees. Agent-worktrees keeps
only the provider-neutral `execution-leg` record and the finalize/cleanup
barriers that refuse a live or unknown externally owned session.

The Worktree Manager Picker is the only path that can establish a **new** AHP
session. A bare/direct `agent-worktrees` launch can still resume an **existing**
persisted AHP execution leg and hard-bind Copilot to that session, but if no
active AHP leg already exists it falls back to an ordinary direct/mux launch
and warns that new AHP sessions must be created through Worktree Manager.
See the [Configuration Reference](docs/config-reference.md#same-machine-ahp-sessions)
and [CLI Reference](docs/cli-reference.md#session-lifecycle).

## Balanced Profile Assignment

Agent-worktrees can optionally assign eligible new interactive session
generations from a user-owned pool of existing `copilot_profiles` using a
balanced shuffled bag. The feature is off by default. Once the policy is valid,
explicit `--profile` selection remains authoritative; recovery, ACP/bridge,
system/delegated, and base-repo launches remain outside assignment. Invalid
armed user configuration fails before any worktree mutation even on those
excluded launch paths. An excluded launch, unassigned older session, replay
whose saved profile is unavailable, or optional assignment-state failure keeps
the concrete ordinary default/manual profile the launch path already selected.

Assignments are persisted before launch, reused by launch retries, bound to the
actual Copilot session id at registration, and replayed on ordinary resume.
Handoff cutover successors draw a new generation only while the policy is
armed. See the
[Configuration Reference](docs/config-reference.md#balanced-profile-assignment--profile_assignment)
for the trust boundary, generic example, supported lanes, one-shot private
token handling, and graceful fallback behavior.

## Status Bar at a Glance

Every worktree session runs inside a multiplexer (psmux on Windows, tmux on
Linux/WSL) with a status bar that reads the worktree's identity and git
disposition **live, per pane** -- the `#()` jobs run in each pane's own
directory, so a split or second window reports its own worktree, not the
session's.

**Left segment** (`status-context`) -- who and where you are:

```
 anomalous-potato  [ win ]  test-chamber:8e45
```

- **Machine** -- the host designation (black)
- **Environment** -- platform as a color-coded badge keyed on OS type
  (win = blue, wsl = purple, linux = orange), so a Windows pane and a WSL
  pane are distinguishable at a glance
- **Repo : id4** -- repo name plus the worktree id's last 4 hex (bold), so
  you always know which of several parallel worktrees a pane belongs to

**Right segment** (`status-segment`) -- what state the work is in:

| Block | Meaning |
|-------|---------|
| `DIRTY` (red) | Uncommitted changes in the working tree |
| `WIP` (amber) | Clean; committed work not yet on the default branch |
| `FINAL` (green) | Clean; work landed / fast-forwardable upstream |
| `CONVO N💬` (teal) | No commits, but the session held *N* conversation turns -- real work that an `UNUSED` label would hide |
| `UNUSED` (grey) | No commits **and** no conversation since the fork point |
| `ORPHAN` (magenta) | No merge base with upstream |

The state is classified content-aware (squash-merged work reads `FINAL`, not
`WIP`) and is annotated with a `↑ahead`/`↓behind` sync tag. The `CONVO` state
draws on the same turn-count detection that keeps `cleanup` from reaping a
worktree whose session held conversation but no commits -- so an
idle-*looking* tree that actually holds work is never mistaken for unused.

See the [CLI Reference](docs/cli-reference.md#status-bar-segment-tmux--psmux)
for the full state table and flags.

On Linux/WSL the bar is applied **per tmux session** by the launcher --
neither agent-worktrees nor Worktree Manager deploys, overwrites, or deletes
your global `~/.tmux.conf`. Server-global tuning that can't be session-scoped
(keystroke passthrough, `escape-time`) is an **opt-in** `apply-mux-keybinds.sh`
you run yourself; it persists a clearly-marked managed block in `~/.tmux.conf`
(so it survives restarts) and applies to any running server. (Windows/psmux
works the same way: per-session `session-options.ps1` + opt-in
`apply-mux-keybinds.ps1`; neither system owns `~/.psmux.conf`.) See the CLI
Reference's *Per-session, not global* note for details.

## PR Attribution & Codenames

Every worktree is assigned a random, public-safe **codename** at creation,
and `create-pr` embeds it in a hidden PR marker by default
(codename-attribution-by-default) -- a public repo can't publish raw
machine/worktree/session identifiers, but a codename gives the PR's author,
or any maintainer who didn't write it, a way to trace a stalled or
unfamiliar PR back to its source worktree: `resolve --codename <name>` or
`embody --codename <name>`, resolved locally first and then via an automated
cross-machine SSH scan that fails closed on a remote match (reports the
machine, never launches remotely). See the
[Architecture doc's PR Attribution & Codenames section](docs/architecture.md#pr-attribution--codenames)
for the mechanism and threat-model tradeoff, and the `worktree` skill's
`references/pr-attribution.md` for the consumer-facing how-to.

## Getting Started

See [Getting Started](docs/getting-started.md) for the minimal standalone path:
install, runtime bootstrap, project registration, and first launch. Then use
the [CLI Reference](docs/cli-reference.md) for headless/scripted commands and
the skills below for in-session guidance.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [The Worktree Picker](docs/picker.md) | The interactive launcher — screen anatomy, navigation, resume/create/clean/sync, launch-time freshness |
| [Multiplexed Sessions](docs/mux.md) | Why sessions run in tmux/psmux — persistence, detach/rejoin, and muxed-vs-programmatic launch |
| [Worktree Lifecycle & Change Management](docs/worktree-lifecycle.md) | The full landing flow — states, direct-push and PR mode, held/follow-up and serial-vs-parallel PRs |
| [Cross-Repo Setup](docs/cross-repo-project-setup.md) | Which of repo class, agent exposure, and project registration to set for a given cross-repo need — decision table + traps (e.g. `expose_agent` without `--repo-dir` is a no-op; registering a `reference` repo silently reclassifies it to `worktree`) |
| [Architecture](docs/architecture.md) | Plugin/runtime layers, installed layout, session lifecycle, PR attribution & codenames |
| [CLI Reference](docs/cli-reference.md) | Commands, installer actions, config format |

## Validation

Fresh-machine install/bootstrap behavior is covered by the repo's clean-room
rig. The
[`agent-worktrees-solo`](../../tools/clean-room/scenarios/agent-worktrees-solo)
scenario installs **only** agent-worktrees, verifies first-use runtime
provisioning and the versioned runtime slot, then round-trips register → create
→ finalize. Run or extend that scenario for installer, bootstrap, or
standalone-behavior changes.

## Skills

| Skill | Description |
|-------|-------------|
| `worktree` | Worktree lifecycle -- creation, finalization, cleanup, safety rules |
| `git-collaboration` | Pull-forward and shared feature-branch git primitives |
| `service-lifecycle` | Service installer patterns -- deploy, update, status |
| `copilot-extensions-setup` | Bootstrap agent-worktrees and optional sibling plugins |
| `agent-worktrees-wsl-provision` | Provision the current project in WSL |
| `agent-worktrees-repos` | Repos registry -- known repos and source roots |
| `agent-worktrees-related` | Directional related-repo index and locus/delegation plan |
| `resolving-state-home` | Native stateless-harness state routing and paired knowledge-worktree resolution |
| `repairing-worktrees` | Diagnose/repair worktree+session health via `doctor` |
| `create-setup-script` | Generate repo-specific session setup scripts |
| `working-cross-repo` | Good-citizen workflow for work in another registered repo |

## Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `preToolUse` | Tool calls | Runs statelessness, cross-repo, and anchor-write guards from `~/.agent-worktrees/bin/` when deployed |
| `sessionStart` | Every session | Emits worktree/account/machine context, reload guidance, runtime bootstrap hints, repo-plugin provisioning, project hooks, session registration, anchor hygiene, and provision checks. When cwd/env identity is absent, registration recovers the exact binding from the session lock PID -> process ancestry -> owning `wt-<id>` mux pane/session |
| `sessionEnd` | Session end | Reads the authoritative hook payload, resolves the exact prior association, and closes the latest activation interval without concluding the resumable session |

The bundled live-pulse extension writes `substatus.json` beside Copilot session
state so the picker can show live intent/rest state. It is passive; durable
worktree disposition still comes only from `agent-worktrees status`.

## Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows | `install.ps1` | Windows Terminal fragments, psmux |
| Linux/WSL | `install.sh` | tmux, Tabby profiles |
| macOS | `install.sh` (POSIX path; reported as `linux` internally) | tmux where available |
