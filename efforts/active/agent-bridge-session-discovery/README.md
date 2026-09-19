# agent-bridge Session Discovery — Any-Repo / Any-Machine

- **Slug:** `agent-bridge-session-discovery`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-12
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** [#2530](https://github.com/ThomasMichon/copilot-extensions/issues/2530)
  (agent-bridge: no session discovery across same-machine/any-repo or
  any-machine/same-repo)
- **Vision:** **vision-closing** — extends two already-stated features with no
  revision needed to their intent:
  - [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
    — §*Features*/`topology-aware-agent-resolution` ("Named agents and
    sessions resolve through project context, worktree state, machine
    topology... while the caller sees one catalog and one resolution
    contract") and `reach-active-worktrees-and-configured-repos` ("A caller
    can address agents in active worktrees and in configured projects the
    fabric knows how to resolve... The reachable set is a catalog, not a
    collection of one-off connection recipes.").
  - [`visions/agent-fabric`](../../../visions/agent-fabric/README.md) —
    §*Features*/`discover-before-duplicate` ("is an agent or worktree already
    covering this repo/target?").
  - Note: PR #2490 added `cache-is-a-hint-never-authority` to the same
    agent-bridge vision this session; that is a sibling concern (state
    honesty of a resolved target), not this one (locating a target at all).

## Guiding Intent

Complete agent-bridge's addressing model along the two axes it currently
can't serve: **same-machine, any-repo** (what else is running on this box,
regardless of project) and **any-machine, same-repo** (where — if anywhere —
is a live session already working this repo). Today a caller can only ask
"what's running for *my* project on *my* machine" or reach an *explicitly
named* machine for *my* project; there is no discovery primitive for either
of the other two legs of that grid. This effort adds them as CLI/wire
primitives, without changing how existing project-scoped calls behave.

## Participants

Single-machine, single-agent effort — no multi-participant coordination
needed for the initial phases (server-side same-machine listing is local;
the cross-machine lookup phase reuses the existing peer-bridge mesh
transport agent-bridge already has for `send`/`create`).

## Context

Filed as [copilot-extensions#2530](https://github.com/ThomasMichon/copilot-extensions/issues/2530)
after the operator, mid PR-triage session, hit a live psmux status-bar
regression and separately surfaced this discovery gap. Read the issue in
full for the original narrative; summarized findings from reading the
current implementation (`plugins/agent-bridge/src/agent_bridge/__main__.py`,
`session_manager.py`, `client.py`, `models.py`, `routes/sessions.py`):

- **Bridge topology today:** exactly one bridge daemon runs per machine
  (`models.default_port()` — a single ephemeral port advertised via the
  routing table; there is no per-project daemon). `SessionManager.list_sessions`
  (session_manager.py) and the `/api/v1/sessions` route
  (`routes/sessions.py::list_sessions`) apply **no project filter at all** —
  every session registered with this machine's one daemon is already in the
  returned set, regardless of which repo it belongs to.
- **The actual gap is at the CLI/argparse layer, not the data layer:**
  - `sessions` and `live-sessions list` (`__main__.py`) have **no
    `--project`/`--all-projects` flag at all** in their subparsers — unlike
    `agents`/`machines`, which already accept `--all-projects` (see
    `_listing_project()`, `_cmd_agents`, `_cmd_machines`).
  - `_PROJECT_CONSUMING_VERBS = {"agents", "create", "machines", "send"}`
    (`__main__.py` ~L3674) explicitly excludes `sessions`/`live-sessions`; an
    *explicit* top-level `--project` before either verb is rejected by
    `_guard_project_scope` with "is not meaningful for '<verb>'" (confirmed
    empirically: `agent-bridge sessions --project X` errors as an
    unrecognized argument — the subparser itself has no such flag; a
    top-level `--project sessions` hits the guard's rejection message).
  - There is currently no way to **attribute** which repo/project a returned
    session or live-session row belongs to in the human-readable output
    either (sessions carry a `project` field server-side —
    `routes/sessions.py` `project=getattr(s.target, "project", None)` — but
    `_cmd_sessions`/`_live_session_summary_line` never print it).
  - Net effect matches the issue's reported behavior: a caller has no
    explicit, discoverable way to ask "what's running here for repo X" or
    "show me everything on this box, labeled by repo" — even though the
    underlying data already crosses project lines.
- **Cross-machine, same-repo (primitive 2) has no server or CLI support at
  all today.** `agents --all-projects` only enumerates the **topology**
  (known machines/agent profiles), not *live sessions* on peer machines'
  bridges. `send <machine> ...` requires the caller to already know which
  machine to target. There is no broadcast/fan-out verb that asks every
  reachable peer bridge (per the existing `peer bridges` / `mesh federation`
  concepts in the agent-bridge vision) "do you have a live session for repo
  X?" and aggregates the answers.

## Request

> agent-bridge currently addresses sessions along a single axis: the
> caller's own project on the caller's own machine (or an explicit remote
> machine target via send/create, still scoped to resolving that machine's
> copy of the same repo the caller is in)... Two new (or extended) discovery
> primitives: (1) same-machine, any-repo session listing... (2) cross-machine,
> same-repo lookup...

(verbatim from copilot-extensions#2530; see the issue for the full text and
motivating example — the `worktree-manager-control-plane` effort's
independently-diverging Picker/Mux implementations.)

## Plan

### Phase 1 — Same-machine, any-repo listing

**Decided compatibility contract (resolves review finding "Do not silently
narrow existing session listings"):** `_cmd_sessions`/`_cmd_live_sessions`
apply **no filter today** — the no-flag default already returns every
session on the daemon regardless of project, straight from
`SessionManager.list_sessions()`/the unfiltered `/api/v1/sessions` route.
That default **must not narrow**. `--all-projects` is therefore added only
for **symmetry/discoverability** with `agents`/`machines` (an explicit,
self-documenting way to say "yes, all of them") and is a no-op relative to
today's default.

**Exact syntax (resolves review finding on `--project` ambiguity):** there is
already exactly **one** `--project`/`-p REPO` flag in this CLI — the
**top-level**, required-value option consumed by `_sender_repo()` and
rejected for non-consenting verbs by `_guard_project_scope`
(`_PROJECT_CONSUMING_VERBS`). This effort does **not** invent a second,
subparser-level `--project` with different (optional-value) semantics. The
only change needed is adding `sessions`/`live-sessions` to
`_PROJECT_CONSUMING_VERBS` so that already-existing top-level flag is
*accepted* (and used to filter) for these two verbs instead of rejected. A
caller narrows with `agent-bridge --project <repo> sessions`; omitting it
keeps today's unfiltered default; `--all-projects` (new, subparser-level,
listing verbs only — mirrors `agents`/`machines`) is the explicit synonym for
that same default. `--project` (top-level) and `--all-projects` (subparser)
are mutually exclusive, matching how `agents`/`machines` already reject that
combination.

**Elevated sub-daemon coverage (resolves review finding on the one-daemon
premise):** `/api/v1/sessions` does not describe the full picture on
Windows — `elevated.py` can run a **sibling elevated bridge** on a separate
loopback port/config directory, and the primary daemon's `list_sessions`
route already merges in that sub-daemon's *persisted* session rows
(`routes/sessions.py`, the `elevated.persisted_session_rows()` /
`is_subdaemon()` merge block) when it is not itself the sub-daemon. Phase 1's
any-repo listing must cover both authorities as today's listing already does:
the new `--project`/`--all-projects` scoping applies uniformly across the
merged result, and a live-but-elevated row whose sub-daemon is unreachable is
represented the same way the existing merge already represents it
(persisted-row fallback, `daemon_running` flag) — no new elevated-specific
gap to introduce, but the plan must not accidentally filter *before* that
merge happens.

- [ ] Add `sessions`/`live-sessions` to `_PROJECT_CONSUMING_VERBS` so the
      existing top-level `--project REPO` is accepted (and narrows) instead
      of rejected by `_guard_project_scope`; confirm the no-flag default is
      byte-for-byte unchanged (still unfiltered, still includes merged
      elevated sub-daemon rows) via a regression test before touching
      anything else.
- [ ] Add `--all-projects` to the `sessions` subparser and to
      `live-sessions list` (mirroring `agents`/`machines`), wired through
      `_listing_project()`.
- [ ] Filter (only when `--project` is given), applied **after** the
      existing primary+elevated merge — no new server-side filtering route
      needed since the fields are already returned. **Field-name note
      (resolves review finding: `LiveSessionInfo` has no `project` field):**
      `SessionInfo.project` (regular ACP sessions) and
      `LiveSessionInfo.repo` (live interactive sessions, `models.py`) are two
      differently-named fields for the same "which repo" concept — there is
      no single literal `project` attribute shared by both registries.
      `sessions` filters/compares against `SessionInfo.project`;
      `live-sessions list` filters/compares against `LiveSessionInfo.repo`;
      both accept the same `--project <repo>` CLI value on the caller side
      (the flag name is a user-facing convenience, not a literal field-name
      passthrough).
- [ ] Surface the owning project/repo in both the human-readable and JSON
      output for `sessions` and `live-sessions list` (the field already exists
      server-side under each registry's own name above; only display is
      missing) — needed so an any-repo listing is actually useful rather
      than an undifferentiated dump.
- [ ] Tests: **no-flag default unchanged** (mixed-project fixture: sessions
      from 2+ distinct projects registered, no-flag call returns all of
      them including a merged elevated-sub-daemon row, unchanged from
      pre-change behavior), `--all-projects` (explicit, equivalent to
      default), explicit top-level `--project <repo>` (narrows), and JSON
      field presence for the project label.

### Phase 2 — Cross-machine, same-repo lookup
- [ ] Design the wire shape: a new verb (working name `agent-bridge find
      --repo <repo>` per the issue's suggestion) that fans a lookup out to
      every peer bridge in the mesh and aggregates machine + session-id
      matches. Confirm naming against existing verbs (`agents`, `machines`,
      `send`) before implementing — avoid a near-duplicate of
      `agents --all-projects`.
- [ ] **Deliverability (resolves review finding on the `send` assumption):**
      today's `send <target>` CLI resolves `<target>` through the caller's
      own local `BridgeClient` — it has no "machine + session id" addressing
      shape. Machine-addressed delivery already exists server-side as
      `send_remote_live_message`/the remote route, but is not exposed as a
      `send` CLI argument today. `find` must do one of:
      (a) return a target string that today's `send`/`create` can already
      consume as-is (e.g. an agent/machine name it already resolves), or
      (b) land a documented remote-send/converse CLI shape (extending `send`
      to accept `--machine <key> --session <id>`, or a new verb) in the same
      phase, so a `find` result is actually actionable and not just
      informational. Decide and record which before implementation.
- [ ] Server-side: a peer-bridge endpoint that answers "do you have a live
      session for repo X" from its own (already project-tagged) session list
      — reuses Phase 1's any-repo listing internally, filtered to one repo.
- [ ] **Protocol-version gating (resolves review finding "Account for
      protocol-version gating in the wire plan"):** this is a new,
      behaviorful HTTP capability on the peer-bridge wire contract, so it
      follows the existing pattern other capability additions use
      (`plugins/agent-bridge/src/agent_bridge/protocol.py` version
      constants; `BridgeClient.daemon_supports()` /
      `assert_client_supported()` in `client.py`, e.g. the
      `REMOTE_OPERATIONS_PROTOCOL_VERSION` / `REMOTE_COMMANDS_PROTOCOL_VERSION`
      gates already in place for `remote.py`'s cross-machine routes):
  - [ ] Reserve a new protocol version constant for the find/locate peer
        endpoint.
  - [ ] Client-side: gate the fan-out call per peer with
        `daemon_supports(...)`; a peer running an older daemon is reported as
        "unsupported" (not a hard failure) rather than erroring the whole
        lookup.
  - [ ] Define the exact unsupported-peer result shape returned to the CLI
        (distinct from "unreachable" and "no session for this repo").
- [ ] CLI: implement `find`/`locate`, including a reasonable timeout/partial-
      result story when some peers are unreachable (mirror how `machines`
      already reports topology errors via `_report_topology_errors`), and
      surfacing unsupported-peer results distinctly from true misses.
- [ ] Tests: single match, multiple matches, no matches, partial mesh
      unreachability, and an unsupported (old-protocol) peer.

### Phase 3 — Docs
- [ ] Update `plugins/agent-bridge` CLI reference docs for the new
      flag/verb.
- [ ] Cross-link from `visions/plugins/agent-bridge` if the feature
      description needs a concrete-capability note (no vision revision
      expected — this closes existing feature intent).

## Validation Plan

- [ ] `agent-bridge sessions --all-projects` / `agent-bridge live-sessions
      list --all-projects` on a machine with 2+ distinct-project sessions
      registered returns rows from every project, each labeled with its repo.
- [ ] `agent-bridge sessions` (no flag) continues to behave exactly as
      before for a caller whose only registered sessions are for their own
      project (regression check — today's default already returns everything
      unfiltered, since there is no data-layer filter; confirm the new
      default-scoping logic doesn't regress that for existing callers).
- [ ] `agent-bridge find --repo <repo>` (or the chosen verb name) against a
      real 2-machine mesh locates a live session on the peer machine and
      returns a target actually deliverable through the Phase 2
      deliverability decision (an existing-`send`-consumable name, or the
      new remote-send/converse shape) — not just an informational row.
- [ ] `agent-bridge find --repo <repo-with-no-sessions-anywhere>` returns a
      clean empty result, not an error.
- [ ] Existing `agents --all-projects` / `machines --all-projects` behavior
      is unchanged (no regression from touching `_listing_project`/
      `_PROJECT_CONSUMING_VERBS`).

## Proposal

_Pending — Phase 2's exact wire shape needs a short design pass before
implementation (see Phase 2's first checklist item)._

## Journal

### 2026-09-19 — Address remaining review finding; drive to merge

Resumed after a worktree closeout sweep. Reconciled the review thread against
the live PR state: CI is green (`mergeStateStatus: CLEAN`,
`mergeable: MERGEABLE`), 5 of 7 file-scoped review findings from prior rounds
are already addressed inline in the Plan text (unfiltered-default contract,
`--project` syntax, elevated sub-daemon coverage, protocol-version gating,
`send` deliverability — see the "resolves review finding" call-outs above)
and were simply awaiting a fresh re-review to clear. The remaining
genuinely-new finding (`LiveSessionInfo` has no `project` field, only
`repo`) is fixed above. The review UI also surfaced several comments against
`plugins/agent-worktrees/...` files that are not part of this PR's actual
diff (`gh pr view --json files` confirms only the two effort README files
changed, across all 3 commits) — those are a stale review-tool artifact, not
real findings; a fresh review should drop them. Umbrella issue #2530 remains
open and unclaimed by any other PR — this plan is still the sole vehicle for
it and remains relevant. Requesting a fresh review next.

### 2026-09-12 — Kickoff (operator-directed split)

Split out of a predecessor session's PR-triage work on this repo via an
explicit context handoff (see the handoff for the full narrative). Read
issue #2530 in full and confirmed both primitives against current code:
`sessions`/`live-sessions` have no `--project`/`--all-projects` flag at the
CLI layer at all today (unlike `agents`/`machines`, which already have it),
and there is no cross-machine same-repo lookup verb. Note that the
**underlying session data already has no project filter** at the
`SessionManager`/route layer — the gap is specifically the missing
CLI-level scoping/labeling and the missing cross-machine fan-out verb, not a
project-siloed data model. Effort created; submitting for the review gate
before implementing Phase 1.
