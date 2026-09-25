# Pattern: entity-relationship model

**Serves:** *Vision plugin-services* §Concepts &
Components/`Entity-relationship diagnosability`;
§Features/`entity-relationship-diagnosability`;
§Behaviors/`traversal-questions-stay-answerable`.

**Exemplars:** agent-worktrees (worktree, session, handoff chain, claims
ledger), agent-bridge (session, bridge/liveness state), agent-dispatch (task,
machine), agent-codespaces (codespace), agent-containers (container).

## Problem

The suite tracks ten **entity types**. Nine are durable and each has exactly
one authoritative owner; the tenth (**agent**) is transient and has none.
**session** is the one entity whose authoritative store splits by *kind*, not
joint ownership: a bridge-hosted session (one agent-bridge drives) is owned by
agent-bridge; a raw interactive-CLI session the Copilot CLI itself started
(with no bridge involved) is tracked only in its own
`~/.copilot/session-state/<id>/` directory, which agent-bridge's
`live-sessions` registry additionally *represents* when that session is
opened for observation — see rows 3-4 below for exactly which command answers
which kind:

| Entity | Owning plugin | What it is |
|---|---|---|
| **machine** | agent-machines | A reconciled physical/virtual host running the suite. |
| **repo** | agent-worktrees | A versionable source corpus (a git remote + its local checkouts). |
| **project** | agent-worktrees | A repo declared to agent-worktrees as an agent harness — the unit `agent-worktrees` commands operate against. |
| **worktree** | agent-worktrees | A space carved for one agent in a project's repo — an isolated checkout + branch. |
| **session** | agent-bridge (bridge-hosted) **or** the Copilot CLI's own session-state store (raw interactive-CLI, no bridge) — see the split above | Accumulated conversation context awaiting the next agent action, built from a worktree. |
| **agent** | (transient, not separately persisted) | The active loop currently driving a session — an identity + running process, not a durable row of its own. |
| **task** | agent-dispatch | A unit of durable, assignable work with its own lifecycle (queued → claimed → active → completed). |
| **bridge** | agent-bridge | The live connection to Copilot actually driving an agent right now. |
| **container** | agent-containers | A borrowed/owned Docker container venue. |
| **codespace** | agent-codespaces | A cloud-hosted container attached to a single repo. |

Each plugin sits at its own tier in the one-way dependency stack (see
`a-la-carte-independence.md`), and **claim refs** are the suite's one
generic cross-entity link: a namespaced string (`dispatch-task:<id>`,
`codespace:<id>`, `container:<id>`) that agent-worktrees' claims ledger
records against an owning worktree (see the `claim-provider-pattern` effort
and the `state-root-coordination` pattern). A claim ref tells you *that* a
worktree holds some external resource and lets you check its live status —
it is not itself a general entity-to-entity index.

This layering is exactly right for **ownership** (one writer per entity,
no cross-tier imports) and exactly wrong for **diagnosis**: understanding
"why is this broken" routinely means walking *across* owners — a session's
worktree, that worktree's entire handoff chain, a task's current bridge
state, a session's rendered history and usage. Without a documented map,
every agent (and every operator) rediscovers the same traversal from
scratch each session — usually by grepping a CLI's `--help`, writing a
throwaway wrapper script, or worse, reading a sibling plugin's private
SQLite/YAML store directly (which breaks the moment its schema changes,
since nothing published that schema as an interface).

## Standard approach

**Every cross-entity traversal question a diagnosis actually needs answers
through a documented CLI command — or the gap is a tracked issue, never a
silent absence.** The table below is that map today; keep it current as
commands are added, renamed, or filled in.

### Diagnostic playbook

| # | Question | Answer today |
|---|---|---|
| 1 | Given a session id, what worktree is it assigned to? | `agent-worktrees session-lineage --session-id <id> --json` |
| 2 | Given a worktree, enumerate all sessions ever assigned to it | `agent-worktrees list-sessions --worktree <id> --json` (machine-wide: `--all-projects`) |
| 3 | Given a session id, what is its active bridge state (live? driving agent? observed/steered?) | For an **interactive-CLI (represented) session**: `agent-bridge live-sessions resolve --handle <session-id>` returns status, liveness, `turn_state`, and `latest_progress` (`live-sessions` registers only represented CLI sessions, not general bridge-hosted ACP sessions). For **any** bridge-hosted session (represented or not), `agent-bridge session-usage <session-id>` reports `status` (a liveness proxy: e.g. running/idle/completed) plus turn/context usage — but not `driven_by`/observed-or-steered detail, which is a `live-sessions`-only (represented-session) concept. |
| 4 | Given a worktree, what is its active bridge state | For an interactive-CLI (represented) session on that worktree: `agent-bridge live-sessions resolve --handle <worktree-id>` (or `live-sessions list --worktree-id <id>`) — same represented-session-only scope as row 3. |
| 5 | Given a task id, resolve its assigned worktree, session, and bridge state | **Composable for the live case, a genuine gap for the durable case.** `agent-dispatch show <task_id>`/`list` already **overlay** live-session status for a CLI-embodied task: it joins the task's owner worktree against agent-bridge's live-session registry and adds an `embodiment` block (`driven_by`, `status`, `turn_state`/`liveness`, heartbeat) — cross-machine too (resolves over the SSH mesh when the owner is a remote machine). See `agent-dispatch`'s own skill, § Inspect. That overlay is **absent by design** the moment the task's worktree is no longer live (best-effort/read-only) — resolving the session for *that* case is the same durable gap as the `—` row below (issue #3555), not a separate one. Tracked as [issue #3556](https://github.com/ThomasMichon/copilot-extensions/issues/3556) to decide whether a single command spanning both the live-overlay and durable-fallback cases is worth adding once #3555 lands. |
| 6 | Given a session id, enumerate its conversation history and compute stats (turn count, token usage, `context_pct`, duration) | Composable, not a gap (rule 4): `agent-bridge session-usage <session-id>` for stats; `agent-bridge read <session-id> --no-follow` (or `GET /api/v1/sessions/{id}/transcript`) for the rendered conversation. Tracked as [issue #3557](https://github.com/ThomasMichon/copilot-extensions/issues/3557) to decide whether a single combined command is worth adding, or whether keeping stats cheap and transcript rendering separate is the intended design. |
| 7 | Given a worktree, identify its handoff chain | `agent-worktrees worktree-lineage --worktree <id> --json` (or `handoff-trace <worktree-id>`) |
| 8 | Given a session id, identify its predecessor/successor/head in the handoff chain | Composable, not a gap: `agent-worktrees session-lineage --session-id <id> --json` already returns this session-scoped -- `relations[].projection.lineage.predecessor`/`.successor` and `relations[].projection.is_head` -- directly, no need to parse the broader worktree-lineage graph. |
| 9 | Given a session id, find the task(s) it worked (reverse of row 5) | `agent-dispatch find-by-session <session-id>` -- lists every task on this host's coordinator that session ever attached to, with `worktree_id`/`machine` and attach/detach timestamps (local-only; no cross-machine peer-browse for this direction yet). |
| — | Given a task id (only), resolve the session it worked once its worktree is no longer live | **Genuine gap**, tracked as [issue #3555](https://github.com/ThomasMichon/copilot-extensions/issues/3555). `GET /api/v1/dispatch-tasks/{id}/session` on agent-bridge exists over HTTP; no CLI equivalent, forcing every CLI-context consumer to hand-roll an HTTP client. |
| — | Given a claim ref (`dispatch-task:`/`codespace:`/`container:`), resolve the owning worktree | **Genuine gap**, tracked as [issue #3558](https://github.com/ThomasMichon/copilot-extensions/issues/3558). The claim-provider's `claim-status <ref>` callback reports the external resource's liveness, not its owning worktree; only the claims-ledger *entry* (already keyed by worktree) carries that link, and there's no composed reverse resolver yet. |

The two `—` rows above are the genuine gaps at the time this pattern was
written (no command exists at any layer) — each now has a tracking issue
linked in its row. Rows 5, 6, and 8 are composable, not gaps, per rule 4
below -- their linked issues (#3556, #3557) track ergonomics improvements,
not missing capability, except row 5's durable-fallback case, which shares
issue #3555 with the equivalent `—` row above. Add a tracking issue link the
moment a new gap is found, so this table stays the single source of truth
instead of drifting from reality.

### Rules for keeping the map honest

1. **A new entity type or claim namespace updates this table in the same
   change that introduces it.** Adding `dispatch-task:` without adding its row
   above is the same class of omission as shipping an undocumented flag.
2. **A genuine gap gets filed, not silently worked around.** If answering a
   traversal question requires reading a sibling plugin's private on-disk
   format, that is the signal to add the missing CLI command (or, at minimum,
   file the tracked issue and cite it in this table) — never to normalize the
   private-format read as the answer.
3. **Prefer a real command over documentation of a private schema.** This
   table names commands, not file paths or table names, wherever a command
   exists — a private on-disk schema is an implementation detail its owning
   plugin may change without notice; a documented CLI surface is the contract
   consumers should depend on.
4. **Composable is good enough; a fused command is a nice-to-have.** A
   "partial" answer that chains two documented commands is not a gap — only
   note it as one if the composition is genuinely awkward (e.g., requires
   parsing free-text output, or a race exists between the two calls).

## Rationale

A single playbook beats scattered tribal knowledge for the same reason the
claim-provider registry beats each plugin hardcoding calls to its siblings:
it is discoverable by grep, it has one place to keep current, and a new
contributor (human or agent) can extend it by adding a row instead of
reinventing a wrapper script that the next session won't know to reuse.

## See Also

These deeper patterns live only in the `copilot-extensions` repo itself (they
are not part of any plugin's marketplace payload, so a mirrored copy of this
file cannot link to them relatively) — read them from a checkout, or resolve
one via `agent-worktrees related resolve copilot-extensions` if this doc's
own repo is not already registered:

- `docs/patterns/a-la-carte-independence.md` — the plugin-stack tier ordering
  and provider-manifest registry pattern claim refs build on.
- `docs/patterns/state-root-coordination.md` — how claim/lease producers
  resolve a qualified owner's coordination identity.
- `docs/patterns/session-state-access.md` — the registry-not-sweep discipline
  behind worktree↔session discovery (questions 1-2 above).
- `docs/patterns/drop-in-registry-hygiene.md` — how claim providers stay
  legible when a contributing plugin is absent or stale.
- `efforts/active/claim-provider-pattern/README.md` — the claim-ref namespace
  schema (`dispatch-task:`, `codespace:`, `container:`).
