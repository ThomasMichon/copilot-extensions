# Resilient Interactive Session Startup

- **Slug:** `resilient-interactive-session-startup`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase worktrees
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** `visions/picker` launch handoff and programmatic parity; `visions/plugins/agent-worktrees` standalone ground-layer authority
- **Umbrella issue:** #1534
- **Sub-issues:** _Pending decomposition._

## Guiding Intent

Make interactive worktree startup transactional from the operator's point of
view: a transient multiplexer failure must not kill the launch surface and
silently strand a newly-created worktree. Preserve agent-worktrees as the
standalone owner of worktree/session mechanics while allowing the optional
Picker to provide the richer create-and-cut-over experience.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Maintainer | Owns design, implementation, review, and release | Per-phase worktree and PR |

## Coordination

- **Topology:** Independent per-phase PRs, serialized where they touch startup contracts.
- **Host (owns PRs):** Maintainer.
- **Delegates:** None initially; add focused slices if implementation separates cleanly.
- **Handoff:** Each phase records its decision, tests, and follow-up issue in this effort before the next phase begins.

## Context

Interactive `resolve --new` currently creates and registers the worktree before
`launch-session.ps1` invokes `psmux new-session`. A failed `new-session` is
cleaned up when ownership can be proven, logged as `mux_failed`, and returned as
an error. The terminal hosting the launcher then exits. The worktree remains a
valid durable unit, but the operator is left with a dead terminal and no
immediate recovery path.

This boundary exists for good reasons:

- Multiplexer creation needs the real worktree path and pane command, so a
  pre-created placeholder session is not equivalent to the actual launch.
- Programmatic `create --json` deliberately creates a worktree without a mux
  and must remain independently useful.
- The Picker vision makes the optional Manager the human-facing launch surface,
  but the agent-worktrees vision requires the ground layer to remain correct
  and usable without that Manager.

The current behavior and expected recovery contract are tracked by #1534.

### Option analysis

| Approach | Advantages | Costs and risks | Role in the plan |
|----------|------------|-----------------|------------------|
| Bounded automatic retry in the launcher | Directly absorbs transient CPU/load failures; works for Picker, direct `--new`, and other interactive callers; keeps the reliability primitive beside the failing `new-session` operation | Must clean partial owned sessions between attempts; needs bounded delay, visible progress, and deterministic tests; retrying non-transient failures wastes a small amount of time | **Implement first.** Retry up to three total attempts with a short bounded backoff and per-attempt activity/log detail |
| Interactive `Retry? y/n` after failure | Keeps the terminal alive; gives the operator control when automatic recovery is exhausted; preserves the already-created worktree for a deliberate next step | Prompt-only recovery makes common transient failures manual; complicates redirected/non-interactive launch; a yes/no loop can become unbounded or inconsistent across shells | **Use as the exhaustion UX**, gated on an interactive terminal, after automatic retries |
| Condition worktree creation on mux readiness | Appears to avoid dangling worktrees by ordering the fragile step first | The real mux launch depends on the worktree CWD and command; a dummy preflight does not prove the real session can start; deleting a successfully-created worktree on later failure risks losing a useful recovery artifact; couples programmatic creation to an optional transport | **Reject as the primary design.** Treat worktree creation and mux attachment as a recoverable multi-step transaction instead |
| Move new-worktree launch orchestration into the Picker | Aligns with the Picker's launch-handoff role; the Picker can stay visible, show progress, retry, and dismiss only after readiness; supports the planned separation of presentation/mux UX from agent-worktrees | Larger architectural change; the Picker is optional and cannot become required for `resolve --new`; needs a structured launch API rather than importing agent-worktrees internals; remote/cross-environment launches add more failure states | **Adopt as the target UX**, after the standalone launch primitive exposes structured progress and recovery |

### Proposed direction

Use a layered solution rather than choosing only one option:

1. Harden the agent-worktrees launch primitive with bounded automatic retries,
   ownership-safe partial-session cleanup, and a recoverable exhausted state.
2. For an interactive direct launcher, remain open after exhaustion and offer
   retry or cancel while clearly naming the preserved worktree and recovery
   command. Non-interactive callers receive a structured failure and never
   block on a prompt.
3. Define a machine-readable create-and-launch contract that reports phases
   such as `worktree_created`, `mux_attempt`, `mux_ready`, and
   `launch_failed_recoverable`.
4. Move the Picker's new-worktree flow onto that contract so it retains the
   screen until mux readiness and cuts over only on success. Keep direct
   `resolve --new` as a standalone adapter over the same primitive.

This fixes the symptom without waiting for the Picker migration, while making
the immediate work a foundation rather than throwaway launcher-only UX.

## Request

> Bug in agent-worktrees: sometimes due to transient CPU load, PSMux refuses to
> create a new session, but the worktree is already created. The failure to
> create the Mux terminates the tab, leaving the worktree dangling without a
> session. We should fix this.
>
> Ideas:
> 1. Have auto-retry, up to 3 attempts on an interval, to create the Mux
> 2. Offer a manual "Retry? y/n" prompt.
> 3. Somehow condition creating the worktree on the Mux actually loading, since
>    the CWD of the Mux can change later.
>
> It also occurred to me: given our goal of splitting Mux responsibility out of
> agent-worktrees and into the Picker, we could move the new-worktree start UX
> into the Picker. We'd then only exit the Picker and cut over once the Mux
> started and we'd created the worktree to it.
>
> Consider the pros and cons here of these approaches. File a bug on the symptom
> (dead terminal on mux failure after worktree creation), create an effort, list
> the potential solutions, and plan out what to do.

## Plan

### Phase 1 - Reproduce and specify the failure contract
- [ ] Add a deterministic PSMux launch seam that can simulate transient failure, partial-session residue, eventual success, and exhaustion.
- [ ] Capture the current sequence from worktree creation through launcher exit and identify which layer owns each state transition.
- [ ] Specify retryable versus terminal failure classification, attempt count, delay policy, cancellation behavior, and activity-log fields.
- [ ] Specify the preserved-worktree recovery message and machine-readable failure shape.

### Phase 2 - Harden standalone mux startup
- [ ] Add bounded retry around PSMux `new-session`, with ownership-safe cleanup before each retry.
- [ ] Emit visible attempt/backoff status and structured activity records without duplicating the final failure event.
- [ ] On exhaustion, keep an interactive launcher alive for explicit retry or cancel; never prompt a non-interactive caller.
- [ ] Ensure cancellation/exhaustion leaves the worktree valid, discoverable, and accurately classified rather than silently deleting it.
- [ ] Apply equivalent failure semantics to tmux where practical, while keeping backend-specific retry classification.

### Phase 3 - Expose a composable launch transaction
- [ ] Separate create, mux-start, readiness, attach, and recovery outcomes behind a machine-readable agent-worktrees contract.
- [ ] Preserve `create --json` as mux-independent and preserve direct `resolve --new` without requiring the Picker.
- [ ] Make repeated launch requests idempotently join a ready session or retry a recoverable failed launch without creating another worktree.
- [ ] Document the contract boundary: agent-worktrees owns mechanics and state; presentation layers own interactive progress and choices.

### Phase 4 - Move the new-worktree startup UX into the Picker
- [ ] Keep the Picker visible while the launch transaction creates the worktree and starts the mux.
- [ ] Render attempt progress, retry/cancel choices, and the preserved-worktree recovery path in the Picker.
- [ ] Dismiss/cut over only after the mux reports ready; on failure or cancel, return to the fleet view with the new worktree visible.
- [ ] Cover local, nested-mux, SSH/ConPTY fallback, and cross-environment launch paths.
- [ ] Retire duplicated prompt/progress UX from direct launcher adapters once parity is proven, without removing their standalone behavior.

### Phase 5 - Release and follow through
- [ ] Update mux, Picker, lifecycle, and activity-event documentation to describe the recovery contract.
- [ ] Bump the agent-worktrees plugin and marketplace versions with each implementation PR.
- [ ] Deploy through the unified update flow and confirm behavior under induced PSMux contention.
- [ ] Split any deferred architectural work into linked issues rather than leaving unchecked design TODOs in the effort.

## Validation Plan

- [ ] A transient PSMux failure succeeds automatically within the configured attempt bound and creates only one worktree/session pair.
- [ ] Partial PSMux processes owned by a failed attempt are removed before retry; unrelated or concurrently-won sessions are never killed.
- [ ] Exhausted retries keep the interactive launch surface alive and present explicit retry/cancel and recovery information.
- [ ] Non-interactive launch never waits for input and returns a stable structured recoverable-failure result.
- [ ] Cancelling or exhausting launch preserves one discoverable worktree with accurate state and no falsely-live mux/session record.
- [ ] Retrying a preserved worktree starts or joins its mux without creating a second worktree.
- [ ] `create --json` remains mux-free and unchanged for programmatic callers.
- [ ] Direct `resolve --new` remains functional when the optional Picker/Manager is absent.
- [ ] Picker tests prove it remains visible until readiness, cuts over only on success, and returns to a usable fleet view on failure.
- [ ] Linux/tmux behavior either matches the contract or documents a tested backend-specific exception.

## Proposal

Proceed with Phase 1 and Phase 2 as the immediate bug fix. Do not block the fix
on moving the Picker or try to pre-create a placeholder mux. Design the retry
and recovery result as a reusable launch transaction from the start, then use
that contract for the Picker-owned UX in Phases 3 and 4.

## Journal

### 2026-08-31 - Kickoff
- Filed #1534 for the dead-terminal and dangling-worktree symptom.
- Reconciled the design with the Picker's launch-handoff/programmatic-parity
  intent and agent-worktrees' standalone ground-layer responsibility.
- Chose bounded automatic retry plus interactive exhaustion recovery as the
  immediate fix, with Picker-owned orchestration as the target UX.
