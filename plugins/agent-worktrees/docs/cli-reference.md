# Agent Worktrees -- CLI Reference

```bash
agent-worktrees <subcommand> [options]
```

## Native machine-record repair

`doctor --repair-machine-record <exact-id> --from-machine <old>
[--to-machine <configured-target>] [--fix] [--json]`, scoped with `-p <project>`,
reports one record first. Existing linked checkouts retain Git ownership proof;
removed checkouts require independent receiving registration/namespace proof.
Same-machine reference aliases remain unchanged. See
[admission, audit, relationship semantics and roster verification](machine-record-repair.md).

## Disposable producer ownership

`resource-producer <capabilities|reserve|begin|complete|bind|inspect|close> --json
[--owner-ref REF]` exposes version-1 provider-neutral disposable ownership.
Except for capabilities, operations read exactly one JSON object from stdin.
See [the exact ABI and callback contract](resource-producer.md). These claims
cannot be settled through generic release, at-rest, sweep or abandon commands.

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

Project-only `get` scalars (`machine`, `platform`, `project`, `repo-dir`, `git-remote`,
`worktrees-root`, `src-dir`, and `config-dir`) read the layered project
configuration without scanning current worktree/session identity. Worktree-
scoped keys retain their binding and checkout resolution.
An explicit `--session-id` still selects the session's project before any
scalar read; the shortcut does not change that targeting contract.

`get git-remote` returns the active repository's layered `RepoConfig.remote`
name, defaulting to `origin`. It does not substitute the registry's canonical
URL (`get repo-remote`) or assume that the checkout has an `origin` remote.
SSH CLI discovery uses this read-only owner query before looking up the
selected remote's URL in the actual execution checkout.

`get execution-identity` emits the versioned
[configured execution-identity contract](execution-identity.md), rather than a
machine-name scalar. It distinguishes explicit registered authority from
hostname detection and unavailable/invalid bindings without introducing a new
configuration setting.

## Session machine identity

For entry-path-independent WSL installation identity, use
`doctor --guest-distribution "Example Distro" --json`. This is a targeted
report: it never enters general session/record repair. On Windows it checks
the designated name against the current user's WSL default; inside the guest
it checks the guest registration and reports host-default evidence unavailable.
`--fix` can enroll only a verified named direct guest entry. It is idempotent
from SSH once enrolled, never changes the OS default, and cannot be combined
with other doctor repair scopes. The shared
[guest-identity contract](../../../libs/installation-context/README.md#wsl-guest-identity)
owns enrollment, refusal and cross-runner mechanics.

The existing `instructions/agent-worktrees/session-guidance.instructions.md`
file in the exact Copilot session contains a compact **Current machine
identity** block. `hostname` is the observed OS hostname; `machine` is the
canonical physical machine from the shared registry map. `environment` identifies
the current execution environment, while `execution_machine` retains the compact
registered execution name (the former `machine` value). `alias` is the friendly
display name; `platform` remains available for compatibility. Native and guest
environments may share one physical name without sharing execution authority.
Resolution status and source are separate contextual facts, never authorization.

The combined startup lifecycle resolves and writes this block before
worktree registration and other slower reconciliation. Fresh and resumed
launches replace the exact-session snapshot. A HOME-cwd resume that recovers
its project during registration refreshes the identity from that project's
registry before final guidance is read. Native context rollover reads
the same file through the existing session-guidance pointer. No machine
inventory or machine-specific value is checked into instruction projections.
Without usable registry context, the observed hostname remains visible while
machine, execution name and alias are explicitly unresolved. An oversized combined guidance
file retains the bounded machine block and reports the omitted guidance.
The initial hook claims the launch under a per-session OS file lock.
Lifecycle rewrites check that same launch ownership, read current snapshots,
compose guidance and replace the file under the lock. A delayed prior launch
cannot overwrite a newer resume's
identity or command catalog. The launch marker is ordering metadata, not
authorization or evidence that the model read the file.

The legacy `machine-context` command labels the physical machine, observed
hostname, execution identity, environment and friendly name separately.
`System environment` retains descriptive OS metadata; `SSH alias` identifies
transport rather than an execution environment. Without registry metadata it
labels the physical machine unresolved while retaining the configured execution
identity, observed hostname and actual platform. These presentation changes do
not rename records, IDs, paths or execution addresses; `get machine` still
returns the configured execution identifier.

## Exact durable record read

`agent-worktrees record --worktree-id <exact-id> --json` reads one durable
tracking YAML from the receiving project's configured tracking namespace.
Use the repository's binstub, its current directory, or explicit `--project`
to select that project. IDs are exact filename components, not suffixes or
codenames; the command never searches another project, enumerates records or
sessions, asks the resident, or computes live facts. A newly created record
is readable even before the resident publishes it. Ordinary `list` projection
authority and failure behavior are unchanged.
Membership is read from the selected project's machine-local repository
declarations and registered anchors, not from a global default project or
Git-backed repository policy. No plugin activation, policy overlays, or Git
probes are needed to identify these metadata records.
Validated operator-owned `config.d` YAML fragments merge in lexical order
beneath `config.yaml`, preserving ordinary repository-declaration precedence.
Managed plugin pointers are not activated by this metadata-only read.
Project selection for this command reads Git marker and common-directory files
without running Git or changing the caller's working directory.
Git-file layouts without a common-directory marker (including submodules and
separate Git directories) use the registered checkout root. Malformed or
unreadable selection metadata returns `project-unavailable` JSON with the
underlying diagnostic, not an unstructured traceback.
Registered bare anchors and their linked worktrees resolve the common Git
directory itself before considering a non-bare checkout's parent.

### Trusted initialization directory

The mutually exclusive `record --cwd <absolute-directory> --json` form resolves
an existing checkout in the selected project without enrolling it, contacting
the resident, starting a service or changing its head. It checks the actual Git
marker's registered project, finds an existing project-scoped tracking record,
then strictly re-reads that exact record and verifies its repository and root.
Recorded roots are matched canonically, so symlinked/junction worktree-root
spellings retain the same identity. Multiple records for the same canonical
checkout refuse as ambiguous rather than choosing by filename order.
An untracked linked checkout, a foreign project, a disappeared record or a
nested untracked checkout refuses; there is no cross-project or anchor fallback.

Success is `{"version":1,"ok":true,"context":{...}}`. The context has schema
`agent-worktrees.workspace-context`, version1, `project`, `anchor`, `root`,
`identity` and `worktree`. For a tracked checkout, `identity` is null and
`worktree` extends the ordinary record projection with `codename` and `head`.
The head's session, state, monotonic revision and pending-handoff ordinals derive
from the same parsed record, using existing asserted-head semantics, not a live
session scan or another stored pointer. Colliding IDs in another project are
never consulted. Consumers must still treat the observation as potentially
stale, not a lease or mutation authority.

At the configured anchor, `worktree` is null and `identity` comes from the
existing configured execution-identity reader. An unavailable configured
identity remains unavailable; it is not replaced with a detected hostname or
synthetic worktree. This read performs no Chat assignment, membership or native
admission. The exact `--worktree-id` response below remains unchanged.

Success exits zero with `{"version":1,"ok":true,"worktree":{...}}`.
The worktree contains `id`, `repo`, `path`, `branch`, `creation_nonce`, `status`,
`session_count`, `last_session_id`, `sessions_indexed`, `execution_leg`,
`execution_leg_opaque`, `execution_leg_live`, `session_backend`,
`session_backend_opaque`, `dispatch_attempt`, `dispatch_attempt_opaque`,
and raw historical `machine`, `platform`, `owner`,
`owner_ref`, `caller_worktree`, `parent_session`, `checkout_managed` fields
(absent raw fields are null). `session_count` is null when sessions have not
been indexed, not zero; an indexed empty list returns zero.
`last_session_id` uses the existing record-local asserted-head resolution,
including its transition ledger, without filesystem/session discovery.
Malformed present ledgers or session states return `record-malformed` rather
than silently deriving a legacy head. An absent or empty legacy ledger retains
the existing asserted-head fallback; an explicit null head transition clears it.
`execution_leg` uses the existing read-only legacy-backend compatibility view,
preserving an unrecognized explicit leg as opaque data.
`execution_leg_live` follows the roster row's rule (opaque bindings, or an
active/unknown derived leg, are live), so callers can judge a just-created
worktree before the resident republishes the roster.
An unrecognized legacy backend likewise remains visible as raw data with its
opaque flag; a null derived leg does not prove absence of a provider binding.
`dispatch_attempt` uses the canonical seven-field dispatch provenance parser.
An absent attribution returns null with `dispatch_attempt_opaque: false`;
an explicit null, incomplete or unrecognized attribution remains raw with
`dispatch_attempt_opaque: true`. Consumers must check that capability and
opacity before treating a record as unassigned.
YAML timestamp scalars, including those nested in opaque bindings or provider
blobs, are represented as ISO-8601 strings in JSON without rewriting the record.
Other values must be JSON-compatible: mapping keys must be strings, and
unsupported scalar types, non-finite numbers, and cyclic YAML are malformed.
JSON conversion is bounded to 64 nesting levels, 10,000 expanded nodes and
1 MiB of encoded data. Shared aliases count on every expansion; exceeding any
budget returns `record-malformed` rather than allocating unbounded output.
The same budgets are checked on raw record values before compatibility parsing
can stringify them, as well as on the returned metadata.
Keys are never silently coerced or collapsed during JSON conversion.
Recognized legacy backends use the existing parsed version-1 representation.
Explicit binding schema versions must be integers, not booleans, floats,
strings, or null; malformed versions return `record-malformed` before binding
interpretation. Unknown integer versions remain opaque, and an omitted legacy
version retains the existing version-1 compatibility behavior.
Durable `finalizing` and legacy `completed` statuses are returned unchanged,
like other recognized lifecycle statuses; reading them grants no execution
authority.

The stored ID must match the requested ID, the repository must belong to the
receiving project's configured repositories, and the stored path must be
absolute and non-traversing. Historical paths need not exist or be locally
executable, including paths recorded on another platform. No machine/platform
identity is rewritten, nonce backfilled, registration created, or process
started.

Failures exit one with `{"version":1,"ok":false,"worktree":null,
"error":{"code":"...","message":"..."}}`. Codes are `invalid-id`,
`project-unavailable`, `record-missing`, `record-unreadable`,
`record-malformed`, `id-mismatch`, `repository-mismatch`, and `path-invalid`.
Argparse usage failures exit two. Missing, unreadable, or malformed metadata
never means terminal/disposed, sessionless, or permission to allocate again.
Successful reading likewise grants **no local execution, takeover, mutation,
or launch authorization**. Actual launch APIs retain execution-space,
locality, head, duplicate and live-holder protections.

## State-root and paired-worktree resolution

`agent-worktrees state-root --json` resolves the repository that owns personal
state for the current project. `state-root --pair` is stricter: it resolves the
task-specific sibling of the current tracked worktree and exits `3` when no
pair is available.

From a registered repository anchor, `--pair` exits `3` because the anchor does
not identify one task-specific writable sibling. Its JSON result contains
`"paired": false`, `"error": "current directory is not a tracked worktree"`,
the independently resolved state root, checkout classification, and recovery
guidance. Plain output begins with the same error and reports the safe read-only
destination when available. The command never guesses among the anchor's
worktrees and never creates or selects a writable pair.

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

> **Choosing between headless, reference-only, or agent-exposed registration:**
> see [Cross-Repo Setup](cross-repo-project-setup.md) for a decision table
> keyed on what you're actually trying to do with the adopted repo (inspect
> its path only, launch interactive sessions in it, back another tool's
> binstub without a Terminal profile, or drive it entirely from the CLI).

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
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan. `--new` creates + launches a **muxed interactive** session (refused without a TTY). `--codename <name>` selects an existing worktree by its assigned public-safe codename, as an alternative to `--worktree-id` (effort `pr-attribution-codenames`); resolved locally first, then via a cross-machine SSH scan (Phase 3) -- a match on a different machine fails closed (reports the machine, does not attempt a remote launch) |
| `create` | Create a worktree **programmatically** -- no launch, no mux; prints id + path (add `--json`). The path for agents and daemons. Assigns the new worktree a random, public-safe **codename** (`codename.wordlist_path` configures a repo-declared vocabulary; falls back to a built-in neutral one) -- surfaced in the JSON output and usable later as an alternate lookup key via `list`/`resolve --codename` |
| `push-changes` | Push worktree changes to remote default branch (squash, rebase, push). Aborts if the pre-squash fails (`--allow-unsquashed` to opt into individual commits) |
| `finalize` | Validate the branch's content is on upstream; for a merged PR with a missing cached head SHA, use a fresh provider-observed head only after the provider confirms the merge, and keep refusing when its boundary remains unverifiable; prune the worktree/branch only when idle (deferred while a session is live). The creating agent owns child cleanup; `--abandon` is refused without an operator-directed `--handoff-to <recipient-or-flow>`, recorded on each re-homed obligation |
| `mark-complete` | Manual recovery -- set tracking status flag only (hidden from help) |
| `claims` | The worktree's **resource-obligation ledger** (accountability for what it allocated; effort `resource-obligation-settlement`). `claims [id]` shows the ledger; `claims owner <kind> <ref> [--json] [--all-states]` searches every registered project on this machine for the worktree(s) holding that outbound claim (held claims only by default); `claims add <kind> <ref> [--owner-ref <m/p/w>]` journals an outbound claim; `claims annotate <ref> --note <text>` attaches/updates a note on an already-existing claim (e.g. one an underlying tool auto-created) without release + re-add; `claims settle <ref> [--released]` marks it at-rest/released; `claims release <ref> [--remove]` retires one; `claims handoff offer ...` offers exact held claims to another worktree, and `claims handoff accept <bundle-id>` atomically transfers them -- same-machine in-process, cross-machine by SSHing to the source machine's own project binstub for the source-side settle while the accepting machine commits the consumer-side ledger locally, fenced by the shared claim-handoff lease; `claims sweep [--apply]` runs the never-wedge reclaim (flip a provably-gone+safe `active` claim → `abandoned`; dry-run default); `claims reconcile-at-rest [<worktree-id> ...] [--apply]` is the dedicated legacy/GC close-out reconciliation command (`worktree-finality-and-obligations` design.md): releases lingering **at-rest-only** claims on existing records that predate/bypassed finalize's own automatic release-under-freeze, never an `active` one; no selector means every tracked record; dry-run by default; `claims orphans` lists the durable orphanage (obligations re-homed by a `finalize --abandon`); `claims cleanup [<ref-or-source-worktree> ...] [--apply]` is the acting consumer that reclaims matching orphaned resources (delete the CodeSpace, finalize the cross-repo worktree, release a PR only when its reference matches the configured provider and authority and the live provider confirms it merged) and drops settled entries. No selector means the entire orphanage. Same-machine, best-effort, dry-run by default; `claims transitive [worktree_id]` lists every unsettled obligation anywhere in a worktree's whole subtree (itself + every worktree it created, transitively) -- a diagnostic convenience, never a substitute for the per-hop finalize gate; `claims history <ref>` prints the durable, ordered claim/release/settle/reassigned event history recorded for a claimed resource -- **today `pr`-kind only**, fed from the explicit claim-lifecycle write paths, `worktree run`'s own PR-claim persistence (both its live pending-ref settle site and its tested helper), an explicit claim-handoff bundle accept's transfer, plus a context-handoff cutover's implicit agent-bridge-style session reassignment (not yet an agent-dispatch task redrive -- an empty result is NOT proof nothing happened), locally plus, with `--remote`, merged with any mirrored remote events (`refs/agent-worktrees/claim-history/v1`, same shared store as the lease refs) -- an unreadable remote store warns on stderr rather than silently showing local-only history as complete |
| `follow-ups` | The worktree's **itemized follow-up ledger** (worktree-finality-and-obligations effort; replaces the boolean-only `follow_up` flag). `follow-ups [id]` lists items + the effective open count; `follow-ups add <summary> [--ref <kind>:<value>]...` journals a new open item (`--ref` is repeatable; kinds: `resource-claim`\|`dispatch-task`\|`issue`\|`pull-request`\|`file`\|`effort`\|`other`) and reopens a `finalized` owner; `follow-ups resolve <id> [--result-ref <ref>]` marks one done; `follow-ups dismiss <id> --reason <text>` marks one explicitly not requiring action. An open (or `pending-transfer`) item counts as an open obligation the same way `status --follow-up` did, and blocks `cleanup`/`gc` the same way a held claim does. Transfer (`offer`/`accept`/`decline`) is not yet implemented |
| `cleanup` | List and remove orphaned or finalized worktrees. Open/unconfirmed PRs and held outbound obligations block removal and archival, including `--force` and missing checkouts; see [cleanup safety details](../skills/worktree/references/cleanup-details.md). |
| `gc` | Garbage-collect this project's worktrees on this machine: **claim-history mirror sync** (opt-in, `--mirror-claim-history` -- pushes not-yet-mirrored `claim_history` events to the shared store; runs FIRST, before any step below can delete the tracking records a legacy event's eligibility depends on) + tracked reap (cleanup verdict) + **managed system/bridge leak sweep** (`--no-managed` to skip) + orphan-directory sweep + **orphaned launcher-shell reap** (`--no-reap-shells` to skip) + `git worktree prune` + optional **lease-ref squash** (`--lease-gc`). `--dry-run` lists without removing; `--json` reports the managed + orphan + shell + lease + claim-history-mirror sweeps. Also runs automatically on the no-daemon cadence (picker launch + session end) |
| `reap-sessions` | Reap leaked `wt-<id>` tmux/psmux sessions whose worktree is finalized/gone/untracked **and** idle past the grace window (spares attached/active/busy) |
| `reap-shells` | Reap **orphaned launcher shells** -- pwsh/python `-m agent_worktrees` scaffolding stranded by a force-closed terminal (parent exited, nothing running under them). Engineered to fail safe: **positive launcher-signature** matching only (a blank-command-line service can never match), session-0/service/ACP vetoes, a live-descendant spare (Copilot, Node, mux clients, and SSH/WSL transports, including through Manager intermediaries), self-preservation, and an idle gate. A remote session need not have a local Copilot or mux process to protect its transport's launcher ancestry. **Reports candidates by default; `--yes` terminates.** Reclaims the scaffolding only -- Copilot itself is handled by `reclaim`, mux sessions by `reap-sessions` |
| `reclaim` | Free the **exact** Copilot process(es) bound to a session/worktree, resolved from Copilot's own `inuse.<pid>.lock` claim -- precise, never splashing onto a sibling session or a worktree that merely shares a cwd. The primitive for **bare** orphans (a Copilot launched straight in a terminal, invisible to the `wt-<id>` mux fleet view; e.g. its terminal was closed/wedged). Target with `--session-id` / `--worktree-id` (infers from cwd) / `--all`, restrict with `--bare-only`. **Dry-run by default; `--yes` terminates.** Never reaps the process tree of the running command. Freeing an idle orphan loses nothing -- the session stays resumable |
| `remux` | Restore a running **bare** (un-muxed) Copilot to the worktree's mux fleet. On Linux/WSL, reparent it into the `wt-<id>` tmux pane via `reptyr`, preserving the live process. On Windows, where ConPTY cannot adopt an arbitrary running process, preview the precise reclaim-before-resume plan and pass `--yes` to retire only the confirmed Stop-unreachable owner; the structured result says to resume next through the normal PSMux launcher. Target with `--session-id` / `--worktree-id` (infers from cwd). The Pickers expose the combined preparation + launch path as **Restore**. |
| `backfill-sessions` | Explicitly scan legacy session state to populate empty registries and titles, derive legacy controller relations from authoritative creation fields, and inspect a bounded number of exact session projections (`--projection-budget N`). Local missing/stale projections are repaired; restored, foreign, ambiguous, colliding, and newer state remains report-only |
| `doctor` | Diagnose machine-wide Picker pivot hygiene plus this project's `config.d` and worktree/session **record + session-state** health: corrupt tracking records, empty session registries + missing titles, legacy controller metadata, bounded exact-ID projection drift (`--projection-budget N`), stale `active`+`completed_at` status, orphaned 0-user-message session shells (`--gc-sessions`, destructive), and cwd/path misalignment. `--fix` repairs local authoritative/controller/projection state but keeps restored, foreign, ambiguous, colliding, and newer state report-only. `--repair-machine-record <exact-id> --from-machine <old-label> [--to-machine <configured-target>]` is a separate report-first scope: `--fix` changes only a proven local native record's machine label, with exact backups/audit and no ID, path, session or ownership rewrite. Existing checkouts retain selected-repository linked-Git proof; removed ones require canonical receiving registration/namespace evidence. Refuses foreign/guest identities, wrong stores and other repair selectors. Runs outside a project for machine-wide pivot diagnostics; project health/config is then explicitly skipped/absent. `--json` emits the exhaustive report |
| `hygiene` | Detect (and with `--fix`, remove) stale **global-Python editable installs** left by a manual `pip install -e .` against a worktree checkout (a real, empirically-hit hazard: #2726) -- a `.pth` file permanently pinned to one worktree that silently shadows whichever worktree an `import`/console-script invocation actually intends, even after that worktree is deleted. Machine-wide; no project context needed. `--json` emits the report |
| `status` | Show worktree git status; **write mode** (`--summary "<one-liner>"` / `--title "<headline>"` / `--follow-up` / `--resolved` / `--paused` / `--unpaused`) annotates THIS worktree's Picker disposition -- `--paused`/`--unpaused` is purely informational (never affects `finalize`'s obligation gate or any other gate, unlike `--follow-up`); **history mode** (`--history` `[--limit N]` `[--json]`) prints this worktree's durable disposition trajectory (summary/title over time). A `postToolUse` hook nudges you to refresh it as work drifts (`AGENT_WORKTREES_NUDGE=off` to silence) |
| `recent-messages` | Show a worktree's latest session's last N message-bearing turns (`--worktree <id>` `--limit N`, JSON) -- the read-side companion to the disposition summary; includes per-turn `tool_names` plus an ending-state signal that distinguishes a cut-off assistant turn from a clean unanswered offer. Backs the picker's **Messages** viewer |
| `list-sessions` | List Copilot sessions with interface/origin metadata, append-only activation intervals, resolved head revision, numbered handoffs, and any bound profile-assignment metadata (JSON); `--worktree <id>` scopes to one worktree and `--all-projects` enumerates every adopted project |
| `session-recovery` | Read one exact session's reciprocal projection and emit validated, machine-readable recovery state without changing bindings. Version-aware output includes schema version, history completeness, relation overflow (`omitted_relations` is `null` for v2 overflow), and tombstone-overflow diagnostics. `--stdin` accepts a sessionStart payload; `--emit-context` renders the bounded pointer used when startup cannot establish an authoritative worktree binding |
| `session-lineage` | Read one exact session projection and validate every retained worktree relation against authoritative records. Reports restored evidence, missing records, unsupported or invalid projections, schema/history completeness, relation overflow, full-key v1 or opaque-digest v2 tombstones, and tombstone overflow without enumerating the live session-state root |
| `head-session` | Project-agnostic replay of a worktree's monotonic head-transition ledger, including pending handoffs (JSON; fail-open when untracked) |
| `record` | Read one exact durable record in the current project (`--worktree-id <exact-id> --json`), without resident roster computation, session discovery, or lifecycle/launch authority |
| `worktree-lineage` | Project-agnostic, bounded graph of one authoritative worktree's sessions, transitions, handoffs, controllers, normalized reciprocal presentation, projection health, and explicit terminal/fork/cycle/missing-session findings (JSON) |
| `fleet` | Aggregate `list --json` across every reachable machine × environment -- one SSH/local process per target, never per-worktree. `--json` returns host rows (`machine` / `env` / `reachable` / `worktrees`) and annotates delegate edges with `caller_state`, `delegate_finalizable`, and reverse `delegates` lists. |
| `reconcile` | Force-refresh PR state for every tracked worktree (including `finalized`) out of band, so `list` stays a pure file read |
| `delegates` | Focused cross-machine delegate/caller query built on the fleet snapshot. Dry-run by default: reports every `caller_worktree` delegate plus whether its host is `resolved`, `gone`, `unreachable`, or `ambiguous`, and whether the delegate became finalizable because the host is `finalized` / `complete(d)` / gone. `--execute` finalizes every currently eligible delegate through the owning host. |
| `conclude-session` / `link-succession` | Project-agnostic write primitives for explicit session conclusion and exact-token handoff succession links (JSON) |
| `resolve-handoff-successor` | Sanctioned repair for a terminal (`kind` managed, status finalized/complete/completed) worktree whose real handoff successor session ran to completion but was never `register-session`'d (a crash/race in that step), leaving `pending_handoffs`/`resolved_head_session` permanently wedged. `--token` names the still-`pending` handoff and `--successor` the NOT-yet-tracked session id; refuses unless the successor is a real, non-live, non-detached local session (`sessions.validate_session_id`) whose recorded cwd matches the worktree's `worktree_path` and whose transcript's first turn is itself a handoff-seed prompt with real turns beyond it (the same class-G evidence bar `repairing-worktrees` documents). On success it retroactively registers the successor, links the handoff, and immediately concludes it so the worktree lands back in the terminal shape gc already expects -- the sanctioned alternative to hand-editing tracking YAML |
| `conclude-disposable` | Project-agnostic, exact-id terminal conclusion for an explicitly disposable CLI worker. Requires `--policy disposable-cli` and `--owner`; preserves live sessions, all dirty work (including generated local overlays), local commits, follow-ups, claims, pairs, and open PRs. A clean branch with zero commits ahead of upstream may remain behind without being rewritten, then the command marks the record managed/final. `--remove` immediately runs the conservative managed-GC verdict for only that exact id, with fresh lifecycle/liveness checks; an already-removed id is idempotent success. |
| `session-transcript` | Emit a Copilot session's renderable transcript events by session id (JSON) |
| `session-tail` | Emit one Copilot session's last N message-bearing turns by session id (`--limit N`, JSON), including per-turn `tool_names` and an ending-state signal (`complete` / `assistant_turn_in_progress` / `assistant_offer_pending`) |
| `session-binding` | Report one session's live mux binding (`found`, `worktree_id`, `mux_session`, `pane_id`, `pane_pid`, `copilot_pid`, ...) plus its recorded `terminal` identity (`--session-id <sid> --json`). `found` still means "a live mux binding exists"; `terminal` is present for any session whose start was recorded, muxed or not -- see [Terminal identity](#terminal-identity) |
| `session-lock` | Write/remove a session-state lattice lock beside Copilot's session state (bridge/mux liveness marker) |
| `execution-leg` | Provider-neutral JSON process boundary for a worktree's externally owned execution identity. `get` reads the generic record (including the legacy `session_backend` compatibility view) and fails closed for opaque schemas. `reserve` atomically fences an `ensure` or `dispose` lifecycle operation as `unknown` under the record/finalize locks and records a bounded owner/token lease plus the prior binding; a live lease rejects competitors, while an expired unchanged lease is reconciled and safely taken over. Token-bound `set` commits it and `release` rolls it back without allowing revisions to move backward; stale tokens fail closed. Unreserved `set`/`clear` retain optimistic `--if-match-revision` fencing. The bare launcher consults `execution-leg get` only to resume an already-established AHP leg; creating a new AHP session now belongs exclusively to Worktree Manager |
| `reconcile-sessions` | Run one bounded record/session/projection reconciliation pass and emit machine-readable repair and conflict counts; suitable for an optional low-duty scheduled backstop |
| `status-segment` | Print a styled status-bar segment for the worktree at the cwd (for a tmux/psmux status line) |
| `status-context` | Print a styled left status-bar segment: machine, environment, and repo:id4 for the worktree at the cwd |
| `status-updater` | Background loop that keeps a session's `@aw_ctx`/`@aw_seg` status vars fresh **off the paint path** (no per-render binstub spawn) |
| `list` | List worktrees from tracking records. `--codename <name>` restricts to the worktree with that assigned codename, as an alternative to `--worktree-id` |
| `handoff-cutover` | Internal live-handoff primitive: spawn a seeded successor window, safely refocus an already-live successor with `--retry`, or retire an old pane |
| `embody` | Agent-facing primitive to create/resume a detached mux+Copilot session in a worktree. `--codename <name>` selects the target by its assigned codename (effort `pr-attribution-codenames`), same local-then-cross-machine resolution as `resolve`; a match on a different machine fails closed (reports the machine, does not attempt a remote launch). Optional `--copilot-process-launcher <absolute-executable>` binds a one-launch native wrapper without putting inference credentials in the manager/mux environment. It supports direct `copilot` templates or normalized setup; incompatible templates, missing executables and configured `copilot_path` overrides refuse. The attended `copilot` counterpart can also write a nonsecret create-or-resume receipt with `--launch-result-file <launcher-directory>/result.json` before attaching. Rejoining a running session does not use a new launcher or refresh its authentication. |

### Parent worktree PR visibility

Worktree claim summaries include active PR claims reached through active local
child-worktree edges, including children in another registered project. PR links
rank ahead of worktree links; the PR ledger and settlement responsibility stay
on the child. Direct `resources`, `claims_assets` counts, and finalize gates are
unchanged. No provider polling or claim writes occur while deriving these links.

Current rows carry at most eight ranked links. `claims_link_count` counts all
available ranked links before truncation, and `claims_links_complete` is false
when links were omitted or a descendant could not be resolved.
`claims_projection` reports `inherited_pr_count`, `unresolved_count`, and
`complete`; a remote, missing, unsafe, or cyclic child remains explicitly
incomplete rather than appearing to have no PR obligations. Use
`claims transitive <worktree-id> --json` to inspect the paths and unresolved
edges. Settled child edges and settled PR claims are not followed or inherited.
The producer reuses child records within one publication and refreshes them on
the next; resident and advisory reads consume the published row unchanged.

### Session command catalog

The session-start hook renders its command catalog in-process from the generated
payload declaration and the payload's read-only installation-binding helper.
It preserves the native catalog emitter's exact command paths, availability,
source provenance, and ownership decisions without starting a shell or a second
Python interpreter. Catalog construction errors are reported on stderr; they
do not produce a ready-looking replacement catalog. The hook uses its existing
stderr-write convention so diagnostics preserve the structural output-free
session-start contract without relaxing the scanner's stdout refusals.

On Windows, the cold hook checks the shared bootstrap killswitch before entering
the canonical ownership lifecycle with its existing Python interpreter and one
native installer child. It passes that interpreter to the installer, avoiding
redundant bootstrap-shell startup and interpreter discovery while keeping both
installer ownership checks. Explicit installation contexts never enter this
legacy path. The standalone bootstrap script remains the no-Python/deployed-copy
fallback; it forwards the supplied interpreter through both ownership admission
and the staged installer, including paths containing spaces. Stamp diagnostics
use UTF-8 and stay on stderr, not in session guidance. The lifecycle owner
enforces the cold stamp's ten-second child deadline, stopping the installer
tree before releasing its ownership lease rather than killing the owner from
the hook.
The stamp copies the same source tree
through native filesystem APIs; runtime provisioning remains deferred to first
tool use.

### Terminal identity

At every `sessionStart` (including resume), the hook client probes what only a
process inside the session's terminal can observe and the lifecycle persists a
small, best-effort record as `agent-worktrees-terminal.json` in the session's
state directory. It lets a later tool (e.g. a desktop companion) find and focus
the terminal showing a session -- including sessions started before that tool
was running. `session-binding --json` returns it as `terminal` (`null` when
nothing was recorded):

```json
{
  "version": 1, "platform": "windows", "recorded_at": "2026-01-01T00:00:00Z",
  "copilot_pid": 1234, "copilot_start_time": "<opaque start-time token>",
  "term_program": null, "wt_session": null,
  "console_hwnd": 721460, "console_class": "PseudoConsoleWindow",
  "host_hwnd": 66706, "host_pid": 5678, "host_exe": "WindowsTerminal.exe",
  "host_class": "CASCADIA_HOSTING_WINDOW_CLASS",
  "tty": null, "mux": null, "ssh": false, "live": true
}
```

- Unknown facts are `null`. No window titles, absolute user paths, or secrets
  are recorded; executable names are basenames. `mux` is `{kind, pane}` inside
  tmux/psmux; `tty` is the controlling pts/tty on Linux.
- **Platform scope:** process ancestry and Copilot process identity
  (`copilot_pid`, `copilot_start_time`, `live`) are recorded on Windows and
  Linux, matching the plugin's existing Win32 and `/proc` process-identity
  primitives. Other platforms (e.g. macOS) record only environment-derived
  facts (`term_program`, `mux`, `ssh`); those identity fields stay `null`.
- `live` is computed at read time: `true` while `copilot_pid` still has the
  recorded start time; `false` when the pid was reused (different start time)
  or definitively no longer exists; `null` when unknown (e.g. the start time
  of an existing process is unreadable).
- **Windows:** hooks run without a console window, so the hook client briefly
  `AttachConsole`s to its Copilot ancestor to read `GetConsoleWindow()` (from a
  short-lived console-less helper when the hook itself holds a windowless
  console, where `AttachConsole` is refused). Under
  ConPTY that is a `PseudoConsoleWindow` whose root owner is the hosting
  terminal's top-level window (`host_hwnd`/`host_pid`/`host_exe`); a classic
  console is its own host (`conhost.exe`). This works even when `WT_SESSION` is
  unset (default-terminal handoff). Windows Terminal does not expose tab
  identity to child processes, so `host_hwnd` names the window; a consumer
  needing the exact tab combines it with its own UI Automation tab matching.
- Recording is fail-soft and bounded (~1 s probe budget, typically well under
  0.2 s); it never fails session start.

## Pull-request workflow

The `pr-*` family drives PR-gated landing (config `pr.enabled` / `pr.required` —
see [config-reference.md § PR workflow](config-reference.md)). `push-changes`
then targets the *feature* branch, never the default branch. The verbs are
self-describing: `pr-status` prints the active actor-effective `flow:` profile,
and `pr-merge` refuses (naming the reason) on a repo where the effective flow
does not authorize that action. Full narrative in
[worktree-lifecycle.md § Landing the change](worktree-lifecycle.md).

### Routine publication reconciliation

Normal `push-changes` and `git sync` automatically reconcile a lost publication
acknowledgement when the source-owning checkout still has intact backed-sync
evidence. The unique live numbered private PR must retain ordinary publication
ownership and an exclusive destination. Fresh provider/ref observations must
agree on the checkpoint's already accepted result, which must be an ancestor of
the pinned, clean source commit. No confirmation token or ownership waiver is
used. Unknown evidence, changed ownership/destination, and unrelated history
remain refusals.

Acknowledgement loss outside that settlement case still uses the existing
verified publication paths: a non-forcing fast-forward retry, or an independently
verified source replay after a rebase. The preflight checks ownership,
destination and provider/ref agreement before deferring; it neither settles
tracking nor authorizes a rewrite through this fallback. Corrupt recovery
evidence still refuses rather than silently falling back.

Reconciliation retains source, original saved and observed commits plus a private
`agent-worktrees-pr-reconciliation-<id>.json` audit in the worktree's Git metadata.
It settles tracking through the normal publication transaction without a push or
PR edit. `push-changes` then publishes the pinned source with bounded, non-forcing
transport and normal hooks; a racing unrelated remote update is not overwritten.
`git sync` instead preserves the next backed transition before rebasing.
Dry runs do not reconcile. Review/check/merge gates and rollout policy are unchanged.

### Explicit operator recovery

Prefer invoking publication from the source-owning worktree's CWD. When that
historical context or its ownership/replay evidence is unavailable, select the
recovery worktree explicitly with its project configuration and request a preview:

```text
agent-worktrees push-changes <recovery-worktree-id> --config <project-config> --break-glass-reason "<reason>" --json
```

This first call refuses with exit 2 and an exact PR/provider/repository/head-ref,
destination fingerprint, full source commit and expected remote commit preview.
The saved head remains separate from the current head agreed by the provider
and remote ref; disagreement or an unreadable/deleted ref refuses even a preview.
The preview also binds the original publication authority, proposed action and
any validated recovery evidence. It changes no Git refs, tracking data, audit
files or remote head. The reason and preview
fingerprint are not operator authentication or permission. Obtain real operator
sign-off for this specific PR and operation before repeating the same call with
`--confirm-break-glass <preview-fingerprint>`. Never add confirmation automatically
after a guard refusal. Changed source, reason, destination, saved publication,
expected head, action or evidence requires a fresh preview and sign-off.

If an existing completed backed-sync checkpoint proves that its exact result
is already the agreed published head, the preview prefers `settle-publication`:
confirmation updates only local publication bookkeeping, never pushes or edits
the PR, and leaves later local work unpublished. The preview binds the checkpoint's
new base; settlement records that base and its corresponding publication patch ID.
Equal source/remote commits
or matching patches alone do not establish this proof. Otherwise `rewrite`
publishes the pinned committed source as-is with the **previewed live head** as
its exact CAS lease, not a silently refreshed saved lease. Both actions require
confirmation and revalidate the plan immediately before taking effect.
Malformed, unreadable or dangling historical checkpoints explicitly mark
settlement evidence `unusable` and disable settlement, not the rewrite preview.
Invalid ref arguments and failed optional Git probes receive the same treatment.
The imperfect evidence snapshot is confirmation-bound and preserved unchanged;
it is neither repaired nor accepted as authority.
Outside the routine backed reconciliation above, ordinary `push-changes` and
`--rewrite-pr` retain their saved-lease refusal.
Historical
ownership, suffix and replay bookkeeping may be waived; provider-confirmed
destination, exact remote lease, serialization, protected refs, clean source,
real hooks and fresh PR review/merge gates are not. Title/squash options and
new-PR use are inapplicable. Source and actual agreed published commits are retained at
the reported `refs/agent-worktrees/recovery/...` refs before transport.

Azure recovery recognizes the standard secure SSH and HTTPS addresses of the
same organization/project/repository. Other hosts, organizations, projects,
repositories, non-default ports and plaintext destinations remain distinct;
this source observation does not change the saved publication destination fence.

The selected worktree's Git metadata contains private
`agent-worktrees-pr-break-glass-<id>.json` audits with the reason, confirmed
operation, original saved publication evidence, retained refs and observed
invoker/session context. Unknown attribution
stays unknown; this is not authenticated identity. Private rationale and invoker
details are not posted to the provider. Audit `succeeded` records successful
transport or the explicitly named bookkeeping settlement, not completed
review/merge: a subsequent persistence failure explicitly
reports that the remote was published and needs reconciliation. Do not repeat a
push to repair a tracking or audit error. A failed transport is followed by a
bounded exact-ref observation: an approved commit on the remote is reconciled
as succeeded, an unchanged leased head is failed, and an unreadable or different
head is audited as `unknown` with an explicit reconciliation warning. Never
repeat a push while its outcome is unknown. Inspect the provider and remote first.
See the worktree skill's PR workflow reference for retention and restore guidance.

Networked actor-specific commands resolve a shared **effective actor profile**:
configured/base `PRConfig` + live provider permission + a matching GitHub
`pr.roles` override. Thus a conservative base can become `pr-self-merge` for a
configured maintainer role, while an explicitly non-self-merge Read/Write role
remains on its review path. A confident read-only permission also demotes an
otherwise unscoped self-merge profile. An unknown/unreadable permission falls
back to the configured base: conservative bases fail closed, while a legacy
base that is itself self-merge preserves the historical fail-open contract.
Repos without `pr.roles` and non-GitHub providers keep their prior behavior.

| Subcommand | Description |
|------------|-------------|
| `create-pr` (alias `pr-create`) | Squash the worktree's commits, publish the PR head branch, and open the PR. Flags: `--title`, `--body`/`--body-file`, `--draft` (open not-ready-for-review), `--new` (force a fresh head branch for a parallel PR), `--no-open` (push only; with no PR recorded yet, the result's `pr_open_skipped` and a `no PR opened` reminder say none exists; a re-run against an already-recorded PR still reports that PR), `--hold` (deprecated alias for `--draft`) |
| `pr-ready` | Move a draft PR **out of draft** — request review |
| `set-pr` | Record PR metadata (`--url`, `--number`) when the PR was opened out of band by a provider sub-agent |
| `pr-status` | Show tracked PR metadata + live verdict / conflict / merge state and supported native gate evidence. `--repo <slug> --pr <n> --json` reads an explicit target through the same one-shot observation as `pr-watch snapshot`. Tracked mode prints the effective actor `flow:` and flags pull-forward once merged; `--no-live` stays offline. |
| `pr-nudge` | Ask this repo's bound automated reviewer (`pr.reviewer`, e.g. GitHub Copilot) to (re-)review the active PR. Nothing to nudge (unconfigured/unsupported) is reported, not an error; a successful request is async -- poll `pr-status`/`pr-watch` for a fresh verdict |
| `pr bar` | Check a PR against its merge bar, clause by clause, on one head: `ci_green`, `review_on_head` (the reviewer's latest review is on the head; `--reviewer`, default Copilot's), `review_findings_zero` (that review reports no open or previously missed findings, and agrees with the threads), `threads_unresolved_zero` (outdated threads are closed), `human_reviews_answered` (people only; an app reviewer is not one), `mergeable`, `merge_policy` (the repo's own policy through the shared classifier: no hold label, not a draft or WIP title, no outstanding change request (any reviewer's latest verdict, an app's too, or the provider's own review decision; a provider-required review is pending unless the provider affirms this actor may bypass it), and an approval on the head where the policy requires one -- resolved for the acting identity's role; an actor demoted by live authority needs a person's approval on the head). Every list is read to its last page; an unreadable page or an empty check list is `unknown`, never met, and a head that moves mid-read makes every clause `unknown` (mergeability the provider is still computing is not a change: the latest answer stands). The read is the repo provider's `PRProvider.get_bar_snapshot` (GitHub reads it today; a provider without one reads every clause `unknown`). `pr bar [<owner/name> <n> \| <n> \| <worktree-id>] [--reviewer <login>] [--json]`; exit 0 met (or merged), 10 pending, 11 failed, 12 unknown |
| `pr rounds` | Is a PR's review/fix loop converging? One round per head the reviewer reviewed (its latest review in effect there), measured by the open plus previously missed findings it reported; a review stating no count is unmeasured, never zero. `done` when the latest round measured zero on the current head (a clean review of an older head is `continue`); `plateau` when none of the last `--plateau-passes` rounds (default 3) improved on the best before them; `round_cap` after `--max-rounds` (default 6); else `continue` ("round N of M"). Reads through the same `PRProvider.get_bar_snapshot` as `pr bar`, and fails closed the same way: no head, a head that moves or a PR that changes mid-read is `unknown`, never a stop or a go. `pr rounds [<owner/name> <n> \| <n> \| <worktree-id>] [--reviewer <login>] [--max-rounds N] [--plateau-passes N] [--json]`; exit 0 continue or done, 20 plateau, 21 round cap, 12 unknown |
| `pr-watch` | `snapshot/status <repo> <pr> --json` reads configured native gate evidence once; `threads` reads discussion bodies and reply fingerprints on demand. `wait <repo> <pr> [--until …]` blocks for transitions with an opaque cursor; `cursor <repo> <pr>` captures the baseline. `subscribe/stop/ownerstatus` use the existing shared subscription owner. See [repository-configured observation](pr-observation.md) for native requirements and ownership. |
| `pr-watch checks` | Built-in `lenses`, `record-self-review`, `verify-self-review` and `scope-report` for an explicit repository/workspace. `export-config` writes the resolved read/check policy for a worker; worker checks use the delivered file through `--config`. Configured checks also gate publication mandatorily. See [publication checks](pr-publication-checks.md). |
| `pr-watch reply/requeue` | Explicit, bounded ADO discussion replies or recovery of an existing build-policy evaluation; no new pipeline, approval or attestation. See [remediation](pr-remediation.md) for freshness, evidence, deduplication and reconciliation requirements. |
| `pr-merge` | Signal **merge consent** on an approved PR (applies the bound `automerge_label`); the review gate merges when satisfied. `--all` / `--loop` for sweeps |
| `pr-complete` | Reconcile the worktree after its PR merged — fast-forward past the squash-merge (or rebase), dropping the local commits the squash already absorbed |
| `pr-diff` | Read the active tracked PR's current unified diff (reviewer-side; provider support varies -- Azure DevOps does not expose one) |
| `pr-comment` | Post a general (non-verdict) comment on the active tracked PR: `pr-comment <body>` |
| `pr-review` | Publish a review verdict on the active tracked PR: `--approve` / `--request-changes` / `--comment`, with an optional `--body` |
| `attribution-audit` | Config-only check: flags this repo's `pr.head_pattern` for the branch-name leak class (embeds `{machine}` while `pr.source_attribution` isn't `true`; `{worktree_id}` is never flagged -- it isn't part of `pr_head_name`'s rendering contract, so it can't actually leak). Plain mode prints findings and exits 1 if any are found (0 if none); `--json` exits 0 whenever it can report findings (empty or not). A configuration-load failure exits 1 in both modes |
| `attribution-prepare` | Emit a source marker, or prepare a UTF-8 `--body-file`, for an external GitHub/Azure DevOps PR tool without provider access. Optional `--tag` supplies an adopter-owned public label; `--omit-source` honors anonymous publication. `--json` returns `version`, `marker`, `body`, `attributed`. Runs from a managed source worktree; leaves files and remote PRs unchanged. See the worktree skill's `pr-attribution.md` reference. |
| `identity-vault status/setup/verify` | Local optional shared-key setup for an already configured agent-vault database and independently running service; no project is required. `--vault-argv` supplies exact vault catalog argv as JSON; optional `--vault-context` supplies its owning validated receipt, not worktrees' context. Setup requires a running service and persists non-secret installation-scoped source selection after population and a fresh cache-loader/encryption probe. Status is read-only; verify exercises the persisted normal reader. `--prompt` is explicit; first-owner creation requires both `--initialize-owner --confirm-first-owner`. See [identity-vault](identity-vault.md). |
| `pr` | Namespace grouping the `pr-*` verbs |

`get pr-profile` / `get pr-required` / `get pr-provider` are deliberately
network-free configuration queries. `get pr-profile` reports the configured
base disposition (`direct` | `pr-human-merge` | `pr-agent-merge` |
`pr-self-merge`); use live `pr-status` for the acting identity's effective
profile when `pr.roles` or live merge authority can change which verbs apply.



### Observed check failure diagnostics

GitHub `pr-watch` results (including timeouts and `checks_failed` events) and
the `pr-status --json` live block include `check_failures`: the failing check's
`name`, `state` (provider conclusion), `source` (`check-run` or `commit-status`),
`url` and available `description`. Human output prints the same names and links.
The data comes from the existing REST status/check-run reads, so it can include
an optional automated-review check omitted by `gh pr checks`.

`checks_scope: "observed"` is intentional: this rollup includes optional checks,
not just required CI. A reviewer runner failure does not mean the required CI
aggregate failed. Required-check completeness is separately verified at the
admin merge boundary; these diagnostics neither weaken nor replace that gate.
Direct GitHub merge tries the normal server-protected operation first, pinned
to the expected head (or a freshly observed head). GitHub enforces its effective
checks and reviews, including legitimate check reruns. If that operation refuses,
only an already configured admin fallback can proceed, and its existing strict
required-check verification still applies. A failed head lookup or changed head
cannot authorize that fallback.
An accepted normal merge can enter a provider queue. `pr-merge --now` reads back
the lifecycle state before reporting a completed merge; accepted but unfinished
requests report `merge-pending` and retain the obligation to watch. An unreadable
completion is explicit and never reported as landed.
An untracked direct merge discovers the head in the CLI before mutation and
retains that exact pin for completion readback; unavailable discovery refuses.
Completion lookup failures expose the exception type, not raw exception values.
Merge-attempt receipts retain `merged` and `completion_verified` across providers.
The latter is null when independent completion verification is unsupported.
`checks_complete` reports whether both observed sources were readable/complete,
or is null for providers that do not supply diagnostic coverage. An empty
failure list from an unsupported provider is not proof that no failure exists.
Known decoded failures survive missing or malformed later pages and a failed
paginated command, with `checks_complete: false`; incomplete evidence cannot
produce a success/none rollup.

Diagnostics retain at most 64 failures per snapshot and 2,048 characters per
text field. `check_failures_omitted` reports any additional failures; shortened
text is marked `[truncated]`. Only HTTP(S) diagnostic links without embedded
credentials are emitted. Raw annotations and job logs are not fetched on each
watch poll; the diagnostic link is the drill-down path when a summary is absent.

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
| `FINAL` | green | Landed on upstream AND *proven* currently safe to clean: a refreshed (fetched) classification with zero held claims, zero open follow-ups, and no other blocker |
| `MERGED` | orange | Landed on upstream but **not (yet) proven** safe to clean -- see below |
| `UNUSED` | grey | Clean; no commits **and no conversation** since the fork point |
| `CONVO` | teal | Clean; no commits, but the session held conversation turns (annotated with the turn count, e.g. `CONVO 12💬`) |
| `WIP` | amber | Clean; ahead with content not yet on upstream |
| `ORPHAN` | magenta | No merge base with upstream |

A trailing `↑ahead`/`↓behind` tag mirrors the picker's inline sync status.
`MERGED` may carry a compact `C<N>`/`F<N>` marker suffix for held claims / open
follow-ups, plus an independent `U*`/`OC*` suffix (any state, not just
`MERGED`) for an unconfirmed `upstream_containment`/`open_claims` fact
respectively (worktree-finality-and-obligations Phase 9's per-fact freshness
markers); see
[worktree-lifecycle.md § FINAL vs MERGED](worktree-lifecycle.md#final-vs-merged----the-closure-descriptor-split)
for the full closure-descriptor rules that decide `FINAL` vs `MERGED`. The
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

### Machine-readable current state: `list --json --current`

`list --json --current` reads the resident's maintained, bounded Worktrees
publication, including `state`, `ahead`, `behind`, and integer `dirty` counts.
Ordinary production JSON/stream listings use this authority by default when
the resident is enabled; `--classify`, `--cache-only`, and `--fresh` do not
authorize caller-side git/session/mux reconstruction. The Manager explicitly
requires `--current`, and incompatible engines report an upgrade error rather
than dropping that requirement.

An installed tmux binary with no running server is a healthy empty session
inventory, not a failed probe. Its recognized no-server responses do not block
current-roster publication or make session liveness unknown by themselves.
Permission errors, timeouts, missing executables and other probe failures remain
unavailable evidence; they are never treated as proof that sessions are gone.

Responses include complete generation/revision/count provenance. A failed
producer preserves last-good rows with explicit stale availability. An
unreachable or incompatible resident permits only independently indexed,
labeled advisory data or an unavailable error, never direct recomputation.
`--stream --follow` holds the engine-owned snapshot/update feed. See the
[engine/Picker contract](engine-picker-contract.md) for barriers, order,
selection, recovery and cutover rules.

The emitted `state` draws from the **same `WorktreeState` vocabulary the status
bar uses**, including the session-derived `convo` (a clean, commit-less
worktree whose session held conversation turns -- the lowercase data-contract
form of the bar's teal `CONVO` block). Centralized in
`git_ops.refine_state_with_session` so the bar and the picker can never drift
apart. State is part of the current publication even without `--classify`.

Every row carries an additive `reciprocal_relation` object for presentation.
Its `binding` and `control` axes preserve the difference between an
authoritative bound session and a separate controller, while `state` provides a
compact summary: `bound-here`, `controlled-elsewhere`, `handed-off`, `terminal`,
`ambiguous`, or the no-relation baseline `unbound`. `actions` contains a
`navigate-worktree` target only when one active controller identity is
unambiguous. Unknown, restored-stale, unsupported, incomplete, or conflicting
evidence reports `ambiguous` with no action. These fields never alter the
record's head, liveness, occupancy, or resume target.

When a row carries `caller_worktree`, `list --json` also adds a normalized
`caller_state` object so consumers do not re-derive the host relationship: the
host worktree id plus the best-known `state` (`resolved`, `gone`,
`unreachable`, or `ambiguous`) and, when resolved, the host's machine/platform,
path, and tracking status. Every such delegate row also carries
`delegate_finalizable` + `delegate_finalizable_reason`, encoding the manual
inheritance rule now made programmatic: a delegate becomes finalizable once its
host is `finalized`, `complete(d)`, or gone. The reverse edge is surfaced on
explicit legacy/detail host rows as `delegates` (the delegate worktree ids plus
their own finalizable/not-finalizable verdicts); current rows omit that graph.

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

Two seams spawn one detached updater per session (psmux via the relocated
`launch-session.ps1`, tmux via `launch-session.sh` -- both now owned by
Worktree Manager, see below):

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
Worktree Manager-deployed `~/.worktree-manager/versions/<current>/bin/session-options.sh`
(the interactive mux launch scripts relocated there in Phase 3b Sub-slice 2a
Step 2; agent-worktrees' normal mux path no longer ships or deploys its own
copy, aside from the packaged Python-only non-editable fallback installer's
materialized self-contained copy set).

Settings that **cannot** be session-scoped -- server-global `escape-time` and
the keystroke-passthrough root key table -- are **not** applied automatically
(they would leak onto every session on the server). They live in the opt-in
`apply-mux-keybinds.sh`, deployed alongside `session-options.sh`. Run it once
per machine, or wire it into a machine-restore flow, if you want that
behavior: it persists a clearly-marked managed block in `~/.tmux.conf` (so it
survives server restarts) **and** applies to any running server. Neither
agent-worktrees nor Worktree Manager's installer ever touches `~/.tmux.conf`
outside this script, and only when you elect to run it (`--no-persist` tunes
the running server without writing the file; deleting the marked block
removes the settings).

> Both tmux (Linux/WSL) and psmux (Windows) are configured **per session** by
> the launcher: `session-options.{sh,ps1}` stamps the bar + behaviors with
> `set -t` (no `-g`), and the server-global keystroke passthrough lives in the
> opt-in `apply-mux-keybinds.{sh,ps1}`. Neither agent-worktrees nor Worktree
> Manager owns `~/.tmux.conf` or `~/.psmux.conf`.


## Claim handoff locality

Every handoff actor must match its receiving project's exact tracking record,
actual execution platform and absolute checkout namespace, including same-name
native records with an unscoped machine registry. Mixed historical/current
producer references use full-record locality at admission and acceptance;
stored references and producer metadata are not relabeled.

An accepted bundle can finish or retry from its consumer and child records
without requiring a retired source ledger or acquiring a new cross-machine
fence. Receiving-side identity, platform, namespace and artifact checks still
apply; acceptance does not authorize a foreign execution space.
Before releasing source claims, the transaction persists child-specific
`source_owner_refs` for validated source aliases. This optional version-1
metadata survives local/remote serialization and is used only for accepted
completion of those exact children. Older bundles still support exact source
references; an alias-only retry without either the source ledger or durable
proof fails explicitly rather than guessing equivalence.

## Resolve previews

`resolve --dry-run --worktree-id <id>` is read-only, including with `--json`
or when forwarded to another machine. It does not record a resume, assign a
codename, reserve a random profile, stage or consume a launch prompt, refresh
instructions, restore/reclaim a process, or append launch activity/trace logs.
Existing record/session/seed state is inspected without creating writer lock
sidecars. Execution-space authority, configuration preflight and seed
revision/cold-start checks still apply; refusal does not change durable state.
A current typed launch seed takes precedence over leftover legacy prompt text;
a newer legacy revision still supersedes the typed seed.

JSON existing-worktree previews retain the normal launch-plan shape and set
`launch.preview` to `true`; the returned worktree retains its current resume
count and timestamp. Base-repo JSON plans remain flat and set `preview: true`.
Base-repo previews skip the informational dirty-file scan so Git cannot refresh
the checkout's index as a side effect.
Explicit profiles and existing-session profile replay remain visible. When a
new random assignment would be needed, selection is deferred until a real
launch: `launch.preview_warning` explains that no profile was reserved (the
interactive preview prints the same warning). A preview's command is advisory,
not an admitted launch; resolve again without `--dry-run` to execute.

Restoring an existing worktree with `--restore --dry-run` is refused without
restoring a session.
`--json --new --dry-run` remains unsupported and creates nothing.
Interactive dry-run requires an explicit worktree, `--new`, or `--base`;
it does not open the state-maintaining picker.

## Remote launch options

Machine-targeted JSON and interactive resolve use the same option policy.
Ordinary remote plans preserve resume opt-outs (`--no-resume`,
`--no-fast-forward`), bare/restore mode, dry-run, recovery, projection/seed
deferral, bridge kind, profile and parent/caller lineage. Valued arguments are
quoted for the target's declared shell. JSON's internal direct-launch setting
does not disable remote multiplexing: `--target-no-mux` is the explicit override.

Cross-space `--owner-ref` creation and raw Copilot arguments are unsupported and
refused before a remote plan is emitted; invoke those operations on the target.
Structured remote prompt admission supports its New/Resume selector and mux
choice only (queued seed staging is intrinsic). Additional behavior options
are rejected before SSH rather than omitted from the request. Staged seed IDs
and launch-request admission/status options remain target-local, not selectors
to forward through `--machine`.

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
| `unregister` | Deregister a project's own adoption lifecycle (projects.yaml + repos.yaml entry + its binstub) -- never the shared runtime or another project's registration; see below |
| `uninstall` | Remove worktree manager (`--remove-config` ALSO deletes the SHARED `~/.agent-worktrees` registry root -- shared by every adopted project, not just `--project`'s; requires `--yes`) |
| `update` | Re-deploy runtime from repo source + refresh every active registered plugin payload/runtime, purge inactive installed identities absent from an authoritatively refreshed marketplace catalog, and opportunistically refresh the remaining installed-but-inactive payload inventory; then update sibling modules, fast-forward the managed repo anchor(s), and auto-prune legitimately stale Picker pivot manifests. Active or activation-unknown identities are never purged. An inactive inventory refresh/uninstall failure is advisory; an active plugin refresh failure fails the update. Version-gated: skips a runtime whose deployed version already matches its payload (`--force` re-deploys all active runtimes; `--force-runtime NAME`, repeatable, re-deploys only the named runtime(s); `--no-anchor-sync` skips the anchor sync; `--no-prune-pivots` skips the pivot prune -- see `doctor`'s `--prune-pivots` for the same safe subset run on demand). Exit codes and the run's status file: see below |
| `install-status` | Show installation and deployment status |
| `deploy-instructions` | Retire migrated managed instruction files (machine identity now via the `session-machine` sessionStart hook) |
| `machine-context` | sessionStart producer for the exact-session guidance writer (cwd-gated) |
| `get` | Query config values (e.g., `agent-worktrees get repo-dir`) |

### `update` results: exit codes, events, and status

The public command submits a durable request before Manager or standalone
execution. `--wait` observes it by default; `--detach --json` returns an
acceptance receipt, and `--request-status <id> --json` is a project-independent
read-only query. `--no-manager` still uses the same execution authority.
`--supervised-debug` uses foreground supervision under that authority.
`--recovery-status` reports restart-confirmed settlement availability;
`--recover-after-restart` settles only old-boot interrupted work without
replaying it. Both support `--json` and need no project.
The [Manager reference](../../../worktree-manager/README.md#durable-update-execution)
defines the shared lifetime, output, interruption and migration boundary.

Required payload acquisition is a barrier before the engine's runtime work:
if its own payload refresh or an active/activation-unknown target refresh
fails, the engine returns that failure without running runtime installers,
anchor synchronization, pruning or local command/profile finishing. Successfully
refreshed payloads remain available for a retry; the error names the cause and
asks the caller to resolve it and retry `update`. Inactive inventory failures
remain advisory.

Payload failure summaries retain a recognized native sharing/lock violation or
I/O/permission cause before shortening the child output to a redacted tail.
Both failed-attempt history and the final result keep that classification;
a successful bounded retry remains advisory history rather than a failed run.
The classification identifies an error class, not the process holding a file.

The engine collects the logical target selection before acquisition, refreshes
its own payload once, and attempts required registered payloads before inactive
inventory. A required failure stops remaining payload attempts immediately;
retired inactive payloads are not removed until acquisition succeeds. The summary
distinguishes attempted failures from remaining targets that were not attempted.
This does not persist a resumable runtime plan or add automatic retry. The
barrier does not make the existing combined installers prepare-only or undo
Manager's earlier self-update.

`update` (and `worktree-manager update`, which drives it) records each run
through the shared [`update-telemetry`](../../../libs/update-telemetry/README.md)
library: phase and per-plugin events go to
`~/.copilot-extensions/update/runs/<run_id>/events.jsonl` and the folded result
to `~/.copilot-extensions/update/update-status.json` (override the directory
with `COPILOT_EXTENSIONS_UPDATE_HOME`). Each step is its own timed phase
(`payload_refresh`, `engine_runtime`, `binstubs`, `module_update`,
`runtime_reconcile`, `anchor_sync`, `prune`). The exit code is 0 on success and
non-zero for every failure:

| Exit | Meaning |
|---|---|
| 0 | Everything required is current (a restart may still be flagged in the status file) |
| 1 | A step failed (`command_failed`), including a failed Manager cutover |
| 2 | Usage error. Standalone `agent-worktrees update` rejects bad arguments before a run starts, so nothing is recorded. `worktree-manager update` creates the run (and self-updates) before the engine parses the forwarded arguments, so an engine usage error is recorded as the run's failure (`command_failed`, error code `usage`) and still exits 2 |
| 3 | A configuration problem must be fixed first (for example a missing PowerShell, installer or engine, an invalid installer deadline, or an invalid installation context) |
| 4 | Authentication is required |
| 5 | Blocked by contention: a lock that timed out, a file another process holds open (Windows sharing/lock violation), or an installer exit of 75 (the cutover did not complete and the previous generation is still serving). Plain permission or I/O errors are exit 1 (`permission_denied` / `io_error`) |
| 6 | An update step timed out: the agent-worktrees or a registered plugin's payload refresh, or a runtime or module installer. A timeout is always reported as a timeout, never as success |
| 130 | Interrupted |

`--force-runtime NAME` re-deploys just that runtime (repeatable); it is
forwarded through `worktree-manager update`, and a name this update would not
deploy (unknown, inactive, skipped, payload-only or not allowed on this machine)
is a configuration error (exit 3). Inactive inventory failures are
advisories and never fail the run. Telemetry
is best-effort: if events or status cannot be written, `update` warns once,
stops writing telemetry for the rest of that run, and its result is unchanged.

### Deregistering a project (`unregister`) vs. tearing down the runtime (`uninstall --remove-config`)

`~/.agent-worktrees` mixes two things with very different disposability: the
deployed **runtime** (`venv/`, `lib/`, `bin/` -- disposable, `install`
regenerates it with zero data loss) and the **shared registry**
(`config.yaml`, `repos.yaml`, `projects.yaml`, `accounts.yaml`, `snapshots/`,
`pivots/` -- the cross-project registry every adopted project shares on this
machine). `uninstall --remove-config` deletes that whole shared root, so it
is never scoped to the project named via `--project` -- it is a machine-wide,
hard-to-undo action. Passing `--remove-config` alone only reports what would
be deleted (and names any other adopted projects that would lose their
entries); add `--yes` to actually delete it.

To deregister a single project -- e.g. a stale or broken project stub with
no repo and no worktrees -- use `unregister` instead:

```
agent-worktrees --project stale-stub unregister
```

It removes only that project's `projects.yaml` entry, its `repos.yaml`
entry (or, with `--keep-repo-entry`, downgrades that entry to a catalogued
`reference` repo instead of removing it), and its own per-project binstub --
never the shared runtime, never another project's registration. It refuses
(unless `--force`) when its own tracking still shows live worktrees, tracked
sessions, or an open PR, so deregistering never silently orphans in-flight
work. The project's own per-project state directory (`~/.<project>`) is left
in place; remove it by hand, or with `uninstall --project <name>
--remove-config --yes`, once it's no longer needed.

To remove only full project adoption while retaining a related repository's
editing class, agent preference and repository metadata, use:

```
<agent-worktrees catalog argv[0]> --project related-library unregister --keep-repo-entry unchanged
```

The bare `--keep-repo-entry` (or explicit `reference`) retains its legacy
reference/no-agent behavior. `unchanged` leaves the repository registry entry
untouched. Existing worktree/session/PR guards still apply, and an unowned or
foreign launcher is not transferred or deleted. A retained local worktree-class
repository remains usable through
`<agent-worktrees catalog argv[0]> --project related-library create --json`; it does not need to
rejoin `projects.yaml` merely to create and manage isolated worktrees.
Replace the placeholder with the selected installation's command from the
session catalog, quoted and invoked for the current shell; do not resolve it
through PATH.
On installation-cell hosts, first `create` initializes or validates the
registered repository's cell-local identity and state before loading lifecycle
configuration. It does not add project membership or replace a mismatched
identity receipt.

## Effort Focus

| Command | Description |
|---------|-------------|
| `effort-focus bind <README> --participant <name> --slice <slice>` | Bind this worktree to one open, repository-relative effort slice after validating containment, effort shape/status, and the declared participant/slice |
| `effort-focus show [--json]` | Inspect the raw binding and its current `open` / `closed` / `stale` state |
| `effort-focus bind ... --replace` | Explicitly replace the current effort/slice; silent replacement is refused |
| `effort-focus release --completed` | Release only when the effort is verified `Done`, every Plan and Validation Plan checkbox is resolved, and the active or standard dated archive path is verifiable |
| `effort-focus release --transfer "<tracked objective>"` | Release by recording the named objective that receives responsibility |
| `effort-focus lint <README> [--repo-root DIR] [--participant P] [--slice S] [--json]` | Standalone effort README schema validation -- the SAME checks `bind` runs, but every finding at once with field-specific guidance, and no tracked/registered worktree required (runnable at authoring time or in CI) |

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
| `repos` | Repos registry -- list, find, add, clone, srcroot, `sync`, `account`/`account-for` (owner→gh-login map), `allow-edits` (break-glass edit grants) |
| `accounts` | gh account identity catalog (`accounts.yaml`) -- list/show/set/remove logins, scopes, login flows |
| `validate` | Validate core infrastructure files |
| `pre-launch` | Check bootstrap staleness (JSON output, for launch wrappers) |
| `launch-update bootstrap\|reconcile\|staged` | Submit a fixed native maintenance phase to the shared installation driver (JSON result) |
| `reconcile-plugins` | Reconcile repo-adopted plugin payloads + gated runtimes (JSON output, for launch wrappers) |
| `uninstall-plugins` | Sweep every deployed core plugin runtime via each plugin's own uninstall action (JSON; dry-run by default) |

### Strict repository inventory (`repos list --strict --json`)

Machine consumers that need a complete inventory use `repos list --strict
--json`. Success adds `inventory_authority: "complete"` to the existing
`{repos: [...]}` envelope. A missing registry is a complete empty inventory;
unreadable, dangling-link, malformed, duplicate-key or non-regular registry
files fail with exit 2 rather than silently dropping entries. The command does not write,
discover unregistered checkouts, clone or provision anything. Individual local
checkout availability is separate: use `repos find <name> --registered-only
--json` when a read or operation needs that repository's current root.
Missing first-touch parent directories are normal absence; a broken link,
link loop or non-directory registry ancestor is unavailable storage and cannot
attest a complete empty inventory.

The default `repos list` remains the tolerant interactive listing. Its legacy
JSON has no authority marker and must not be interpreted as a completeness
attestation. An older runtime that does not return the strict marker cannot
establish inventory completeness even if it accepts or ignores the new flag.

### Bare-anchor sync (`repos sync`)

`repos sync` fast-forwards a bare anchor (`core.bare=true`) by moving its
default-branch ref with a compare-and-swap `update-ref`. When the anchor also
keeps an index and checked-out files beside its git dir (a converted ordinary
clone), those files move with the ref: before the ref moves, the sync checks
that no other git process holds the anchor's index lock, that the index
matches the old commit, that the files have no local edits, and that no
untracked or ignored path is in the way of a newly tracked one. If a
check fails, the ref is left unmoved. After the ref moves, it runs the same
two-tree `read-tree -m -u` a checkout would (so `core.autocrlf` applies). Linked
worktrees are never touched. A result that reaches the file-state checks ends
with `; files=<status>`. Earlier exits (a detached HEAD, a branch mismatch, a
missing ref, a fetch failure, divergence, or a failed `update-ref`) leave the
files untouched and carry no `files=` token:

| Status | Meaning |
|--------|---------|
| `refreshed` | Ref and files advanced together |
| `current` | Already up to date; the index matches the ref |
| `none` | A bare repo with no index or checked-out files; nothing to refresh |
| `stale-index` | The index does not match the ref (for example, an earlier ref-only sync); ref left unmoved |
| `dirty` | The checked-out files have local edits; ref left unmoved |
| `collision` | An untracked or ignored local path is in the way of a newly tracked one; ref left unmoved |
| `index-locked` | Another git process holds the anchor's `index.lock`; ref left unmoved |
| `refresh-failed:<detail>` | The ref advanced (the result names the commit it advanced from) but the files could not be refreshed, for example because another git process took `index.lock` after the checks; fix the cause, then run `git --git-dir <anchor>/.git -c core.bare=false --work-tree <anchor> read-tree -m -u <from> <branch>` (works from any directory; an anchor with checked-out files keeps its git dir at `<anchor>/.git`) |

Any status other than `refreshed`, `current`, or `none` is printed as a
warning.

### Repo-adopted plugin reconciliation (`reconcile-plugins`)

`--apply` submits its canonical work to that same durable execution owner,
including when invoked by the provisioning hook. It still returns the original
JSON summary and canonical exit status. Default planning, `--peek` and
`--check-update` do not enqueue installers. Public `stage-update` acquisition
also uses the owner while preserving its status file and optional JSON output;
`--indicator-state` remains read-only. These adapters do not replace the
stager's own implementation or the installers' existing inner custody.

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

Runtime planning also checks the addressed installation's
[`runtime-deployment.json`](../../../libs/installation-context/README.md)
before emitting an installer action. A managed, malformed, unsupported or
foreign policy suppresses only that runtime action and supplies a diagnostic;
it does not suppress requested marketplace payload refreshes or another
plugin's runtime. With no policy, older standalone payloads retain their
existing behavior. A present policy requires a capable owning helper; an older
helper cannot silently ignore it.

`reconcile-plugins --apply` revalidates the installation, plan root/receipt and
deployment permission before either its default or custom execution adapter.
This catches a policy added after planning. Healthy runtimes need no
publication permission to remain selected. Uninstall is a separate action,
not runtime publication.

`reconcile-plugins --check-update` remains a read-only compatibility probe with one runtime
action on JSON stdin. It returns
`{"allowed": true, "diagnostics": []}` with exit 0, or `allowed: false`
and recovery diagnostics with exit 3. It bypasses project resolution and never
discovers a repository,
replans or executes the action. Cached payload/root/context changes are refused
through the same execution admission used by `--apply`.
Only Worktrees' staged self-update carries `trigger: "stage-update"`: that
action repeats the stager's owning payload discovery, preserving its supported
`_direct` fallback. It still compares the planned payload/root/context and checks
deployment policy. The marker cannot select another plugin or an arbitrary
payload path, and ordinary marketplace reconciliation is unchanged.
An older standalone payload without policy needs no new policy helper.
Native wrappers now submit `launch-update reconcile`, `launch-update bootstrap`
and `launch-update staged --status <path>` rather than running cached argv.
The fixed reconciliation phase invokes the same canonical apply executor.
It does not reuse the older `reconcile-plugins --apply` command as a capability
probe: an old engine can support that command without the execution driver.
Bootstrap derives a fresh plan under custody and rechecks staleness after
execution. `--defer-worktrees` leaves only self-installation to the staged phase;
independent repository bootstrap actions remain eligible. Staged submission
binds the status bytes and discovered payload fingerprint, revalidates the
selected runtime root and publication permission, and reconstructs canonical
installer arguments/environment. A changed acquisition or source fails rather
than executing a different result. Status-provided argv/environment is not an
execution protocol. `--detach` returns an acceptance receipt with request/log
identity; the default waits for the durable phase result. All fixed phases
can run from a neutral directory; bootstrap without repository/configuration
returns its existing empty plan. An older engine without these request surfaces
fails visibly; launchers never fall back to raw installation.
Direct native installer admission remains that installer's responsibility.

### Teardown sweep (`uninstall-plugins`)

The symmetric counterpart to `reconcile-plugins`, but for removal rather than
install/update: it discovers every core-marketplace plugin payload actually
installed on this machine (independent of any repo's current
`enabledPlugins` -- disabling a plugin does not by itself remove its
already-deployed runtime/autorun state) and, for each with evidence of a
deployed runtime (`~/.<name>/deploy-manifest.json`), invokes that plugin's own
`install.{sh,ps1} uninstall` action. No purge/remove-config flag is ever
appended, so each plugin's own keep-list (durable state/history/DB files)
stays intact -- only executables, autorun registrations, and recomputable
runtime state are removed. Default is a **dry-run**: `uninstall-plugins`
prints the JSON plan (and any `diagnostics` naming what it found it cannot
remove) without touching anything; pass `--apply` to execute it in-process.

Known gaps this sweep surfaces as `diagnostics` rather than silently missing:
a plugin deployed only via an idempotent `init.*` bootstrap script (no
`install.{sh,ps1}`, e.g. `agent-containers`, `agent-mcp`, `agent-machines`)
has no supported uninstall action (`uninstall-unsupported`); `wsl-setup`'s
`WSL-Keepalive-Ubuntu` scheduled task is installed via
`skills/setting-up-wsl/references/wsl-keepalive.ps1`, outside this
convention (`manual-cleanup-required`). `agent-ssh` gets one cascaded step:
its own uninstall does not remove the separate dtssh transport's
Startup-folder host listener, so
`transports/dtssh/scripts/install-host.{ps1,sh} uninstall` is appended
automatically (or reported as `dtssh-host-script-missing`). `agent-worktrees`
itself is excluded -- use its own top-level `uninstall` verb.

### Service source checkouts

`services list`, `status`, single-service actions and batch actions resolve
sources from the project anchor, never from an arbitrary active worktree.
For a bare anchor, discovery and installers use a retained detached checkout
of the configured default branch's commit under the project's state root,
`service-sources/<anchor-key>/<commit>`. The checkout remains available for
redirectors and source provenance; it is not a scratch directory. Existing
checkouts with changed HEAD, tracked content or non-ignored untracked files
are refused, not overwritten. Repository-ignored installer build artifacts
(such as Python bytecode caches) do not prevent later discovery or reuse.
Checkouts are not automatically garbage-collected, since installed services
may still reference them.

Before `install`, `update` or `copy`, synchronization must succeed. Bare
anchors use the existing safe bare-ref synchronization; ordinary anchors
retain their checkout and fast-forward workflow. Fetch failures, divergence,
wrong branches and local-only commits now fail explicitly before discovery or
installer execution instead of deploying stale source. Read-only list/status
operations do not fetch or advance the anchor.

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
launcher and is not intended for direct use. `--worktree-id` defaults to
the current directory's resolved worktree when omitted (matching most
other subcommands); a caller that cannot be resolved either way fails
loudly rather than logging a `worktree_id: null` entry invisible to
`activity --worktree-id <id>`.

### Handoff trace (Tier C)

The 13-stage handoff-cutover lifecycle
(`efforts/active/handoff-cutover-lifecycle-journal/`) additionally writes
every stage-mapped event to a **durable, unrotated, per-project/per-worktree**
JSONL sink at `~/.agent-worktrees/logs/handoff-traces/<project>/<worktree-id>.jsonl`
(`handoff_trace.py`), so a full handoff history survives past Tier A's 7-day
rolling retention. Writes take a cross-process advisory lock
(`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) so the CLI, the launcher
hook client, the resident status monitor, and context-handoff's Node process
can append concurrently without corrupting the file. See
`docs/patterns/lifecycle-activity-logging.md` § Tier C for the full contract.
Read it with:

```bash
agent-worktrees handoff-trace <worktree-id|session-id>
agent-worktrees handoff-trace <id> --token <handoff-token>
agent-worktrees handoff-trace <id> --project <name> --json
```

The renderer resolves the owning project, defaults to the **most recent**
handoff attempt for that selector when `--token` is omitted, reads the durable
store with an `activity.jsonl` fallback, and prints the ordered 13 stages with
gaps called out explicitly (`stage 9: never observed`). When the predecessor's
or successor's session-state `handoff-request.json` still exists, the JSON
output also surfaces its lineage fields.

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
