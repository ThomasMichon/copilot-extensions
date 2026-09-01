# Herdr Live Handoff

- **Slug:** `herdr-live-handoff`
- **Repo:** copilot-extensions
- **Branch(es):** `feat/herdr-live-handoff`
- **Created:** 2026-09-01
- **Status:** Active
- **Vision:** Extends context-handoff live cutover to an already-active local pane host without making that host authoritative for completion
- **Umbrella issue:** #1584

## Guiding Intent

Let a context-handoff successor start automatically in a sibling Herdr pane while
the durable baton remains owned by context-handoff and the predecessor remains a
recovery point.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| PR owner | context-handoff implementation, validation, and publication | `feat/herdr-live-handoff` |

## Coordination

- **Topology:** One feature branch and one pull request.
- **Host (owns PRs):** PR owner.
- **Delegates:** None.
- **Handoff:** Evidence is returned through issue #1584 and the pull request.

## Context

`continue_handoff` currently invokes only `agent-worktrees handoff-cutover`.
Herdr already exposes its pane identity through `HERDR_ENV` and
`HERDR_PANE_ID`, and an independently installed `copilot-pane` launcher accepts
opaque initial text through `launch --task-file`.

## Request

Detect an active Herdr pane, persist a machine-local baton without requiring an
agent-worktrees worktree, launch one seeded sibling through `copilot-pane`, and
retain the predecessor pane.

## Plan

### Phase 1 — Host and storage seam
- [x] Add a checkout-local machine-state fallback for file-backed handoffs when Herdr is active.
- [x] Route live cutover through `copilot-pane launch --task-file` before considering mux.

### Phase 2 — User-visible contract
- [x] Report the selected host and predecessor retention accurately.
- [x] Update context-handoff guidance and README language.
- [x] Bump the context-handoff payload version in every required location.

## Validation Plan

- [x] Focused Node tests prove Herdr wins over mux, writes the exact task file once, and retains the predecessor.
- [x] Existing context-handoff tests pass.
- [x] A real local Herdr launch creates exactly one sibling and submits the seed.
- [x] Changed-file secret scan is clean.

## Proposal

Use the existing Herdr environment markers as the one routing trigger. Store
non-worktree batons under a deterministic machine-local directory derived from
the checkout path, pass the generated successor seed through a short-lived task
file to the installed launcher, and leave pane retirement to the existing mux
path only.

## Journal

### 2026-09-01 — Kickoff
- Claimed #1584 after confirming no existing Herdr-specific issue or pull request.
- Traced the root cause to context-handoff's store and live-cutover trigger; the installed launcher already supplies the required sibling-pane mechanics.

### 2026-09-01 — Implementation
- Added Herdr-owned checkout state, exact one-call `copilot-pane launch --task-file` routing, and predecessor retention while preserving the existing mux path.
- Corrected the extension's Herdr success response and aligned the subprocess timeout with the launcher's observed slow-start contract.
- `node --test plugins/context-handoff/tests/*.test.mjs` passes 61 tests; real installed-plugin validation and the changed-file secret scan remain.

### 2026-09-01 — Live validation
- Installed the owner checkout through `copilot plugin install` and confirmed `copilot plugin update context-handoff` retains the direct `0.1.0-dev58` source while upstream publication is pending.
- A real `/handoff-continue` run started with panes `w1:p1,w1:p2,w1:pS`, created only `w1:pT`, submitted the file-backed seed through `copilot-pane launch --task-file`, and consumed the baton once in the successor; `w1:pS` remained available until test cleanup.
- Follow-up review found that an empty stderr buffer could hide useful stdout from failed Herdr commands; the shared extractor now selects the first non-empty diagnostic, with focused regressions. The full Node suite passes 63 tests.
- Upstream merge is blocked only by the available account lacking merge permission.
