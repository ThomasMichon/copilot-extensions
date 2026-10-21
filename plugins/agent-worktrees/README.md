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

For optional encrypted PR attribution with an already configured agent-vault
KeePass database, use `identity-vault setup/status/verify` or the
[setting-up-pr-identity skill](skills/setting-up-pr-identity/SKILL.md).
The [local setup flow](docs/identity-vault.md) reuses one shared owner key and
persists only installation-scoped source selection; routine reads remain
cache-only and require no vault unlock.

For centralized PR status/waits and related-repository ADO review, discussion
and policy requirements, see [repository-configured PR observation](docs/pr-observation.md).
Persistent approvals, latest-push approval and human-only completion gates are
reported separately; no consumer-specific watcher is required for those reads.
The same surface provides [built-in publication checks](docs/pr-publication-checks.md)
and [bounded ADO remediation](docs/pr-remediation.md), without casting human
approvals or creating pipeline runs.

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

The existing session command catalog also reports a source-qualified
installation binding. Its legacy owner is published in the existing deploy
manifest; its namespaced owner is the validated active installation receipt.
Old or unattributed installations retain their command but report unresolved
ownership, not invented readiness. See the
[shared binding contract](../../libs/payload-invocation/README.md#source-qualified-command-binding)
for selection/refusal semantics and consumer compatibility.

The installation-owned runtime deployment policy also gates native bootstrap
stamping, legacy lib-copy reconciliation and payload-local first-use
provisioning. These paths use the canonical deployment probe before publication;
first use repeats admission under its existing provision lock. A refusal leaves
the hook's other components and healthy runtime dispatch usable and reports
explicit installation-management recovery. Absent policy retains standalone
behavior through the existing canonical legacy-admission fallback, including an
unattributed payload when that fallback permits it. Preliminary resolution
suppresses durable traces on a miss; healthy dispatch retains its resolver
trace phases. This protects these native entrypoints, not every installer or launcher
publication path. The [canonical deployment policy contract](../../libs/installation-context/README.md)
owns the policy schema and decision semantics.

Project launchers are attributable global entry points with ownership receipts
under `~/.agent-worktrees/binstub-receipts/`. Legacy launchers delegate to the
installed user-side `agent-worktrees` command in their resolved local-bin
directory, not a checkout or plugin payload. Cell-scoped launchers retain their
selected payload-local command. Routine
install, update, repair, and reconciliation preserve foreign owners and edited
bytes. Known historical generated launchers migrate automatically when receipt,
installation lineage and exact generated-content checks establish ownership,
including checkout-to-marketplace adoption of the same legacy runtime.
Attributable obsolete or knowledge-only launchers retire through the same
recoverable boundary; unknown unreceipted files and ambiguous relocations still
require deliberate attribution. Windows command names are compared
case-insensitively and edited siblings protect the entire command pair. See
[historical command migration](docs/historical-project-command-migration.md)
for admission and interruption recovery. An operator can explicitly transfer a
registered project command with:

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

Configuration/home discovery, front-door OS routing and Git path comparison
use interpreter platform identity rather than `platform.system()`/`uname`.
These OS-only checks do not require Windows WMI; home overrides, installation
context selection and WSL detection keep their existing precedence.
Front-door Git discovery also uses the shared headless process helper for
windowless callers.

Unified update performs local command/profile reconciliation once after
runtime reconciliation and anchor synchronization. It binds command repair to
the freshly selected payload rather than an inherited historical snapshot.
An unresolved required command or failed profile repair makes the update
incomplete, not an advisory success. Preserved unrelated foreign inventory
does not authorize overwriting it or make it a target of this installation.
On Windows, profile refresh uses the Manager's narrow `refresh-profiles`
JSON contract rather than entering the plugin installer. It repairs local
integration even when the runtime version is already current. A failed or invalid
response reports the Manager exit code and bounded, credential-redacted startup
stderr instead of hiding the cause behind a JSON parser error.

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

## Authoritative Repository Roots

Consumers requiring authoritative repository roots use
`agent-worktrees repos find <name> --registered-only --json`. This read-only
contract returns `registration: ready`, the registered name/class/remote and
current platform's existing local root. Unlike ordinary `repos find`, it never
uses a source-root discovery fallback. Invalid or duplicate registry mappings,
missing registrations and unavailable checkouts are explicit errors. Acquisition
and adoption remain separate owner operations; this read never clones a repo.

Worktree Manager's [Repositories Configuration section](docs/repositories-configuration.md)
uses the native read/preview/apply CLI to inspect registration, routes, state
binding and tracked pairs. Its only editable setting is the machine-local
future-launch Copilot login preference; it does not log in or launch agents.

## Independent Execution Spaces

Native Windows and WSL can register independent execution-space keys without
sharing project registrations, settings or ownership ledgers. Physical-host
grouping never makes distinct registered spaces local to one another. See
[Execution-space identity](docs/config-reference.md#execution-space-identity)
for explicit selection, compatibility and lifecycle fences.
For deliberate historical label normalization, see
[native machine-record repair](docs/machine-record-repair.md). The canonical
report-first doctor scope preserves same-machine alias relationships and requires
independent registration/namespace proof for removed checkouts. Ordinary doctor
fixes never rebind ownership.

## Captured child ownership

Bounded Git publication and static-guidance Git preflights use a headless
captured child on Windows, assigned to a kill-on-close Job before execution.
Job admission failure is explicit; there is no detached fallback. Timeout or
caller exit reaps descendants while preserving captured diagnostics.
Explicit descendant escape is prevented by this bounded Job; durable-service
launchers retain the shared helper's permissive default outside this seam.
POSIX retains its process-group timeout cleanup. This is not a durable-daemon
launch or an interactive terminal.

## Native resume admission

Bridge's exact-head ACP resume uses `resolve --no-resume
--native-resume-session <id>` for the selected existing worktree. The returned
command retains the worktree owner's process-exclusion guard through backend
execution and rechecks native identity, process liveness and pending intent at
the actual invocation boundary. A `finalizing` worktree refuses even before its
cleanup lock is acquired. A live or unknown incumbent refuses; a dry-run
plan is not launch authority. Mux embodiment takes that same per-worktree guard
before creating a new execution leg. Older owners reject the new flag rather
than silently returning an unfenced launch. The caller-facing mode contract is
owned by the [Bridge resume reference](../agent-bridge/skills/agent-bridge/references/cli-commands.md#session-management).

### Explicit restart Resume

`embody --worktree-id <exact-id> --restart-request <json> --json` is a separate,
explicit Linux/WSL restart intent, not the ordinary Resume or Bridge acquisition
mode. The bounded typed request binds the original UUID, frozen owner/head/source
revisions, old native PID/birth and old/current environment epochs. The old
process identity must hash to the recorded activation; same-boot, foreign,
changed, ended, live or unknown targets refuse. The only admitted existing
resource is one exact, active, producer-free, nonopaque self-session claim for
the original UUID and this machine/project/worktree, as normal native Start
creates. Launch and explicit acknowledgement share that predicate; neither
releases, reacquires or rewrites the claim. Other, mixed, task or historical
released claims, inbound ownership, dispatch provenance and profile assignment
remain blocked: a read-only task-policy snapshot is not action-time permission.
Custom launch templates and legacy setup launchers are unqualified.

This mode reuses the lifecycle and per-worktree launch-admission fences, with a
strict record-writer lock spanning final validation and mux invocation. The
owner persists a private, bounded `restart-recovery/launch.json` receipt under
that worktree's external tracking state, syncing the file and directories before
launch. It supplies the exact original `--resume=<uuid>` and full `--interactive`
STATUS FIRST restart seed through the existing native launch builder, never a
fresh conversation or post-launch `send-keys`. Other launch intent/argument
overrides, mux-installation opt-ins and staged typed/legacy prompts refuse.

`launch-uncertain` survives a timeout or owner crash and is never automatically
retried; `mux-created` records only a successful mux invocation. Repeating the
same request reads that outcome without another launch, even after native
startup advances the owner revision. A different request cannot overwrite an
unreconciled receipt. There is currently no receipt-clear/retry operation:
uncertainty requires owner reconciliation, not deleting state or guessing that
nothing launched. Neither outcome proves a ready original native process or
seed acknowledgement.

The seed directs the resumed agent to use its attributable session-catalog
command with `embody --worktree-id <exact-id> --restart-ack <request-uuid>
--status-reconciled --json` **after** STATUS FIRST reconciliation. This is an
explicit agent assertion, never a registration hook or Manager-generated ACK.
The owner requires the original UUID's live open hold and exact mux/process
birth, a fresh invoking-process ancestry containing that native source, the
current target boot and unchanged worktree/head identity. The frozen original
activation must remain unambiguous in history; only its ordinary
`inferred:next-start` closure is accepted, not a reasoned intentional exit.
Current holds, pending seeds/handoffs, task/dispatch/profile or opaque claims
refuse. The exact producer-free active self-session claim created by normal
native registration is allowed; it grants no permission for other claims.

A durable version-2 `seed-acknowledged` receipt retains the exact newly observed
source, mux incarnation and owner/activation revisions. It can reconcile
`launch-uncertain` without retrying the launch, and an identical ACK is
idempotent only while the same execution and current policy are still proven.
After acknowledgement, a separately frozen later-boot request may append a new
receipt only for that exact acknowledged execution: the same original UUID,
worktree incarnation/head, source PID/birth/identity and activation, with an
unchanged-or-later owner revision and the preceding target epoch as its source.
Current launch admission is still required. Every prior receipt is retained
unchanged; a reused request UUID with different contents or a repeated boot
refuses. An unacknowledged latest launch always blocks a different request.
History is bounded to 16 receipts and 32 KiB in aggregate, without eviction.
Admission reserves space for the largest valid future ACK and requires room
for owner/activation counters to advance before any native invocation.
Exhaustion refuses, rather than deleting outcomes or launching without ACK room.
There is no clear, destructive supersede or retry operation. `restored` and
`automatic_eligible` remain false: this assertion does not qualify real native
seed delivery, all recovery admission, or platform startup.

Add `--restart-receipt` to read the exact request's receipt without launch
configuration, admission, provisioning or writes, including for a retained
older target boot in the same platform/environment. Historical acknowledgement
is not current liveness or permission to launch again. `not-reserved` means no owner
reservation is present, not permission to launch. `--dry-run` performs a
provisional current-owner check without reservation. Automatic recovery,
startup enrollment and actual native seed-delivery qualification remain
disabled/unresolved; Windows, macOS and container restart intents refuse.

## Static instruction preparation

Startup copies every declared instruction unchanged in process, after applicable
worktree allocation and complete paired-plugin composition. The outer launchers
defer resolve-time copies and run
`knowledge compose-plugins --prepare-projections` after maintenance, before
starting Copilot. No packaged projection engine or authoring plugin is required.
Resolve deferral uses a call-scoped environment hint, not a new mandatory
argument, so an older runtime can still reach the launcher's update barrier.
Direct CLI resolve calls retain preparation by default.
The Manager's initial resolve also defers when it will hand off to those
launchers. Direct consumers and explicit or persisted AHP sessions still
prepare immediately. Final preparation targets the selected launch checkout,
including knowledge-side launches, unless an explicit harness path was supplied.
Generated instructions live under the ignored
`.github/instructions/agent-worktrees-generated/` namespace; tracked and staged
paths are protected before any batch copy or cleanup.
Inputs must be regular files; a FIFO or other nonregular input fails before it
can block the in-process publisher. On POSIX, foreground Git preflights retain
the Manager's owned group. An operation timeout exits that preparation so the
outer owner reaps remaining descendants before the launcher can continue.
Bounded backup workers keep independent process-group ownership.

Failed paired allocation or composition retains a small pending marker in this
worktree's existing Git-administrative guidance metadata. Both the final preflight and hook
backup refuse partial publication until successful paired composition clears
that marker. Explicit no-pair creation does not set it. Existing worktree
identities and checked-in guidance remain usable; pair repair is separate from
static copying.

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

- **Machine** -- the registered execution-space name (black). Historical
  hostnames are resolved using the worktree's recorded platform, so native
  and guest labels remain distinct without renaming worktrees or changing
  their ownership.
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
| [worktree](skills/worktree/SKILL.md) | Worktree lifecycle -- creation, finalization, cleanup, safety rules |
| [git-collaboration](skills/git-collaboration/SKILL.md) | Pull-forward and shared feature-branch git primitives |
| [service-lifecycle](skills/service-lifecycle/SKILL.md) | Service installer patterns -- deploy, update, status |
| [copilot-extensions-setup](skills/copilot-extensions-setup/SKILL.md) | Bootstrap agent-worktrees and optional sibling plugins |
| [agent-worktrees-wsl-provision](skills/agent-worktrees-wsl-provision/SKILL.md) | Provision the current project in WSL |
| [agent-worktrees-repos](skills/agent-worktrees-repos/SKILL.md) | Repos registry -- known repos and source roots |
| [agent-worktrees-related](skills/agent-worktrees-related/SKILL.md) | Directional related-repo index and locus/delegation plan |
| [resolving-state-home](skills/resolving-state-home/SKILL.md) | Native stateless-harness state routing and paired knowledge-worktree resolution |
| [repairing-worktrees](skills/repairing-worktrees/SKILL.md) | Diagnose/repair worktree+session health via `doctor` |
| [create-setup-script](skills/create-setup-script/SKILL.md) | Generate repo-specific session setup scripts |
| [working-cross-repo](skills/working-cross-repo/SKILL.md) | Good-citizen workflow for work in another registered repo |
| [tracing-claimant-graphs](skills/tracing-claimant-graphs/SKILL.md) | Trace resource claims back to their owning worktree |
| [setting-up-pr-identity](skills/setting-up-pr-identity/SKILL.md) | Set up and verify a shared vault-backed PR identity key |

## Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `preToolUse` | Tool calls | Runs statelessness, cross-repo, and anchor-write guards from `~/.agent-worktrees/bin/` when deployed |
| `sessionStart` | Every session | Emits worktree/account/machine context, reload guidance, runtime bootstrap hints, repo-plugin provisioning, project hooks, session registration, anchor hygiene, and provision checks. When cwd/env identity is absent, registration recovers the exact binding from the session lock PID -> process ancestry -> owning `wt-<id>` mux pane/session |
| `sessionEnd` | Session end | Uses original source evidence to close the matching activation, not a replacement; legacy unqualified records retain their prior behavior. Ending an interval does not conclude the resumable session |

Native Linux/WSL lifecycle hooks capture bounded original ancestor PID/birth and
boot/environment metadata before contacting the resident monitor. Registration
qualifies an opaque activation source only when that original process actually
holds the exact conversation's `inuse.<pid>.hold` inode. It does not infer source
identity from the current head, a PID-shaped pathname, or the resident daemon's
environment. Unsupported or inaccessible source evidence remains unconfirmed.

Reasoned End observations match that source in the existing canonical activation
history. A delayed historical End cannot end a replacement interval, release its
self-session claim, stop its fsmonitor, or capture its title. Missing or ambiguous
source cannot retire a qualified interval. Repeated logical activations in one
physical CLI with the same native UUID are deliberately unconfirmed; the ambiguity
survives history pruning. Each session separately retains at most 256 discarded
physical-source identities. A discarded superseded source cannot register again,
and discarded sources never regain End or lifecycle-qualification authority.
Exceeding that safety budget sets a persistent saturation fence: further
registration and source-bound closure are refused rather than forgetting history.
Stale saves cannot remove these fences. Duplicate old Start delivery cannot reactivate an ended
claim. A hook cannot reactivate its ended physical source without a usable event
timestamp; uncertain replay is refused before claim acquisition. Duplicate End
observations do not capture a workspace title. Both lifecycle writes require the version-3 owner verb, including legacy
requests, so an older resident cannot silently discard the new fields.
Current source-bound End returns its exact activation source and ordinal. Title
persistence revalidates that receipt against the latest ended activation under
the record lock, so a same-UUID replacement cannot receive a stale End's title.
Source-aware hook transport still delegates source-less End to the version-3
writer: legacy cleanup remains supported, while any source-bound history is
protected under the same record lock. No source proof or qualified exit reason
is invented for legacy records. A source-changing bind also advances lifecycle
revision when its handoff token was already linked, preserving replacement
history against stale saves.
PID-only catalog reconciliation can fill process metadata but cannot replace or
reopen a source-bound activation. Its matching End still closes the current
interval and releases that interval's claim and filesystem monitor.

Warm source-aware hooks require `session-start-source-v1` or
`session-end-source-v1` in the selected resident's rendezvous **before** sending
any mutating packet, as well as acknowledgement in its response. An older Linux
resident
uses the current source-aware CLI fallback; its explicit flag is rejected by
older runtimes, and source-aware Start never downgrades to old startup scripts.
Original Start bindings come only from the transported hook environment, not the
resident's inherited environment. No End path runs the terminal attachment
probe. Windows native End remains visibly unqualified; a missing new warm-hook
capability does not introduce a new console helper or fallback spawn.
End transports only the original scoped project/worktree/session binding
variables alongside native source metadata, not an environment dump. A warm
bare Resume therefore targets its bound record even when cwd is outside it and
the same UUID appears in several records.

The shell, PowerShell and Python installers deploy both lifecycle transport and
the canonical stdlib source-capture helper beside the installed hook client.
That client uses its sibling helper outside the payload layout, without
requiring the runtime package on its import path.

`session-role --hosting-evidence --worktree-id <exact-id> --json` is read-only.
An optional explicit `--session-id <native-uuid>` asks for that conversation's
bounded activation lifecycle in the same owner read, even when it is no longer
the hosted head. This mode never substitutes the ambient session ID. Hosting and
lifecycle observations do not authorize automatic restart recovery.

`session-role --restart-check <expected-json> --json` compares one exact frozen
identity with current worktree/head/activation and cold-session evidence.
For a single active task claim, it requests opt-in current policy from the
registered claim provider, using only `dispatch-local-task:` or
`dispatch-central-task:` references. The returned task must match the exact
machine/worktree, project lane and original native UUID, with a positive current
generation and no terminal/suspended/held/steered/waiting state. Matching task
snapshots appear in optional `task_policies`; they are not launch capabilities.
Legacy/default task refs, opaque or unmatched dispatch provenance, other/mixed/
released/producer claims, inbound ownership and bound/profile-assigned worktrees
remain unqualified. Missing/old providers refuse without default-writer guesses,
self-provisioning or legacy retries. The check never changes task/head state,
reserves a launch or grants automatic restoration.
Unknown persisted claim states still count as active for existing conservative
finality checks, but carry `metadata_opaque: true` through full/public projections
and later saves. Malformed claim collections/items retain conservative opaque
placeholders rather than becoming an empty ledger. Neither can qualify for
restart inspection.

`session-role --restart-survivor <request-json> --json` is a separate, explicit
read-only observation of the original conversation's current hosting. It uses
the full frozen restart request, not an ambient session ID. A positive snapshot
requires the same current head, worktree incarnation and path; retained,
unambiguous original activation history without an intentional End; a newer
activation and owner revision; and a live native process that owns the exact
hold and mux incarnation in the current target epoch. Changed owner/epoch,
missing holds, stale source identity, pending seeds, task/mixed/inbound claims
and recorded profile assignments refuse. The canonical original self-session
claim may remain unchanged. Attachment count is not eligibility.

The version-1 `native-restart-survivor` response is current evidence only,
separate from retained launch receipts. `survivor_confirmed` never implies
adoption, STATUS FIRST reconciliation or seed pickup: `reuse_authorized`,
`launch_authorized`, `seed_acknowledged`, `restored` and `automatic_eligible`
remain false. It neither writes state nor attaches, types a seed, launches,
reserves or retries. A negative or unavailable observation is likewise not
permission to launch.

The bundled live-pulse extension writes `substatus.json` beside Copilot session
state so the picker can show live intent/rest state. It is passive; durable
worktree disposition still comes only from `agent-worktrees status`.

## Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows | `install.ps1` | Windows Terminal fragments, psmux |
| Linux/WSL | `install.sh` | tmux, Tabby profiles |
| macOS | `install.sh` (POSIX path; reported as `linux` internally) | tmux where available |

## Explicit runtime repair and development

Ordinary `install`, `provision`, and `update` construct a numbered runtime once,
reuse exact healthy content, or refuse drift. `-Force` / `--force` reconciles
configuration; it never authorizes rewriting a completed numbered slot.

Both native installers also expose `repair`, `dev`, and `dev-release`:

```powershell
.\scripts\install.ps1 repair -InstallDir <runtime-root>
.\scripts\install.ps1 dev -InstallDir <runtime-root> -Owner <worktree-root>
.\scripts\install.ps1 dev-release -InstallDir <runtime-root> -Owner <worktree-root>
```

```bash
bash scripts/install.sh repair --install-dir <runtime-root>
bash scripts/install.sh dev --install-dir <runtime-root> --owner <worktree-root>
bash scripts/install.sh dev-release --install-dir <runtime-root> --owner <worktree-root>
```

Repair leaves the old slot untouched and constructs a distinct health-gated
generation of the package version. A single Python owner acquires the existing
cutover guard before choosing that generation and keeps it through construction,
promotion, and actual monitor retirement or recovery. A private, frozen control
environment supplies installed lifecycle code even when the old runtime cannot
start; it is not a selected deployment or a second lock protocol.

An ordinary update discovers completed generations of the requested package,
preferring the selected or last-known-good generation with matching frozen
content. It retains and health-checks that build without rebuilding or
restamping it. Repair suffixes distinguish runtime identities, not package
releases: an older repaired slot never outranks a later package release during
first-run discovery.

Dev is explicit and claim-gated. Its inputs must belong to the claiming
worktree; an omitted owner is resolved from the source checkout's Git root.
In a namespaced installation, that worktree is distinct from the stable
authorized payload named by `COPILOT_PLUGIN_STAGED_FROM` and its installation
receipt. Owner containment and build provenance describe the actual inputs;
the authorized payload still supplies installation governance. The installed
control build carries the frozen source evidence into candidate construction,
without rereading or attributing the build to that authorization payload.
Each invocation refreshes the mutable `versions/dev` environment from a frozen,
non-editable snapshot of the whole local dependency graph. This is the pattern's
ordinary-rebuild form, not the pilot plugin's editable-source form: edits become
visible on the next `dev` invocation, and installed preparation never writes
generated provenance into the worktree. Another owner's claim refuses with exit
code 2; configuration force is not a takeover.

Release probes and restores the original numbered target before relinquishing
the claim. Failed construction or restoration leaves the claim available for
diagnosis and an explicit retry; do not delete a held lock or hot-patch a numbered
runtime. Structured installation contexts use the same commands with their
validated root and receipt, never a fallback legacy root.

The deployed CLI also exposes `agent-worktrees dev-status --json` and
`agent-worktrees dev-release --owner <worktree-root> --json`. The explicit
owner may name a worktree that no longer exists; with no `--owner`, release
uses the caller's Git root. These commands use lifecycle code and validators
frozen into the installed slot, not the owner's checkout or today's installer
scripts. Namespaced release still requires its authorized installed payload and
receipt. There is no force-takeover option. Release checks the original target's
own frozen-origin receipt when present, without comparing its dependencies to
the newer dev build; a failed probe keeps both the claim and the selected dev
runtime intact. Older targets without that receipt retain legacy health behavior.

Deployment manifests are published by the retained Python owner under the same
cutover/governance authority. `source.version` is the package version;
`runtime_slot` and `runtime_generation` identify the actual installed build.
Source path, commit, branch, dirty state, and payload fingerprint come from that
build's frozen provenance, including after `dev-release`, not from the checkout
that happens to run the release command. Legacy builds without a saved footprint
report unknown fingerprint evidence rather than attributing today's payload to
an older deployment. Monitor lock/health evidence and self-supersession checks
also compare build generations, so reusing the `dev` path cannot hide an old
process behind an equal interpreter prefix.

Read-only runtime diagnostics use the live monitor's PID/start-time-verified
lock and startup-cached package/build identity, including the selected
installation-cell root. An equal package version with a different build UUID
is reported as serving-build drift, not as an up-to-date repaired/dev process
and not as a reason to reinstall that package. Restart from the selected runtime;
missing legacy generation evidence remains unknown.

Installation-cell governance waits use the caller's bounded installer wait
budget, not the directory-lock helper's short initialization grace period.
On Windows, temporary frozen package inputs use the OS temporary directory:
`CreateProcess` cannot start a build backend with an overlong working directory,
even when long file paths are enabled. The installed slot stays in its authorized
root. Its immutable `.runtime-input-origins.json` records each local package's
exact frozen origin, allowing installed-health reuse after the temporary input
workspace has been removed. Missing legacy receipts retain the older
installation-local frozen-origin check; malformed or conflicting evidence
requires explicit repair. Long interpreter paths are launched with an explicit
application path, not inferred from the command-line token.
