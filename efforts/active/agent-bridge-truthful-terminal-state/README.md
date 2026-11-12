# Agent-Bridge Truthful Terminal State

- **Slug:** `agent-bridge-truthful-terminal-state`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `main`
- **Created:** 2026-09-11
- **Status:** Draft
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with §Behaviors/`own-the-lifetime`, `reattach-never-kill`,
  `drain-before-letting-go`, and `connection-loss-never-destroys-the-target`
- **Umbrella issue:** [#2464](https://github.com/ThomasMichon/copilot-extensions/issues/2464)
- **Sub-issues:** [#22](https://github.com/ThomasMichon/copilot-extensions/issues/22)
  (turn wedges in `running` forever on transport drop) ·
  [#2445](https://github.com/ThomasMichon/copilot-extensions/issues/2445)
  (stop doesn't halt background reconnect) ·
  [#2378](https://github.com/ThomasMichon/copilot-extensions/issues/2378)
  (no reconnect backoff) ·
  [#2379](https://github.com/ThomasMichon/copilot-extensions/issues/2379)
  (idle session with no consumer retries forever)
- **Related:** [#2449](https://github.com/ThomasMichon/copilot-extensions/pull/2449)
  (in-flight explicit-stop dormancy gate, the first implementation slice of
  this effort)

## Guiding Intent

A bridge session's terminal and lifecycle state must always be **truthful**,
viewed from either direction:

1. A **running** turn must eventually reach exactly one truthful terminal
   event — it must never wedge in `running` forever because a transport
   dropped mid-turn, and it must never silently disappear without a terminal
   marker any consumer can observe.
2. A **stopped, idle, or failed** session must never falsely revive itself.
   Explicit stop is a durable state, not a request that background recovery,
   heartbeat maintenance, or a daemon restart can quietly override — while an
   explicit resume (including a later `send`) must still recover it normally.

These read as two different bug classes — a hang vs. an unwanted retry — but
they are the same missing invariant seen from opposite sides: the bridge's
notion of "is this session alive, and who decided that" must be singular and
truthful at every boundary (client disconnect, transport loss, heartbeat pass,
daemon restart, explicit stop, explicit resume). Fixing them under one effort
keeps the state-machine reasoning coherent instead of accumulating unrelated
point patches that could reintroduce each other's failure mode.

This effort does not introduce new vision language — the agent-bridge vision
already asserts `own-the-lifetime`, `reattach-never-kill`,
`drain-before-letting-go`, and `connection-loss-never-destroys-the-target`.
It closes those behaviors by making the implementation actually uphold them
across every disruption boundary, with regression coverage that pins the
truthful-state invariant down.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|----------------------|-------------|
| Truthful-state driver | Session lifecycle/reap/resume state machine, event-log terminal markers, backoff policy | Isolated copilot-extensions worktrees |
| `agent-bridge-contract-evolution` | Durable-record tolerance, fencing, and generation identity that the dormancy state machine's records must follow | [`agent-bridge-contract-evolution`](../agent-bridge-contract-evolution/README.md) / [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460) |
| `venue-parity` | Cross-venue (local/container/CodeSpace) reconnect/backoff behavior parity and the shared fault-injection harness | [`venue-parity`](../venue-parity/README.md) / [#954](https://github.com/ThomasMichon/copilot-extensions/issues/954) |

## Coordination

- **Topology:** land Phase A (turn continuity) and Phase B (dormancy) as
  independent, separately reviewable PR lanes — they touch different code
  paths (`AcpClient`/`SessionManager._run_prompt` vs. the reap/heartbeat/resume
  gating) and neither blocks the other.
- **Host (owns PRs):** truthful-state driver.
- **Handoff:** every phase updates this Journal with the exact PR, the
  invariant it closes, and any residual race left open for the next phase or a
  named follow-on issue. A phase is not done merely because its PR merged —
  see each phase's exit criterion below.

The proposed finite #4231/#4223 child-diagnostic lane below remains with the
same truthful-state, Session Host/shared-driver, process, provider, relay,
native and source/resource custodians. Preserve the entire OPEN #2464
contract, this ROOT's Draft status, all existing rows and history. #4029's
direct historical #4171 progress/result/active-steering scope, #4309's
connection contract and #954's venue-only/P3 boundaries remain separate;
#7237 owns an external conversation relationship, not peer-process lifetime.
Normal reviewed complete Plan/local and global Validation/Coordination/Journal
delivery, actual direct issue receiving and complete current source/custody
readback precede a separate #4231 tracking judgment. No generic ACK, claim
expiry, reassignment, activation or operational authority is introduced.

## Context

`#22` and the `#2445`/`#2378`/`#2379` cluster surfaced independently, from two
different incidents, but both point at the same underlying gap: the bridge's
session lifecycle has enough state (running/idle/stopped/failed, heartbeat,
recovery, explicit-stop) that its transitions were never fully specified as a
single truthful state machine. Each incident fixed (or is fixing) its own
symptom locally:

- `#22`'s fix adds a transport-lost signal that wakes the hung `prompt()`
  future and appends a terminal `session_state_changed` event so no consumer is
  left watching a phantom `running` turn.
- `#2449` (in flight) adds an explicit-stop dormancy gate checked before
  provider-availability checks run, at both heartbeat and daemon startup, so a
  `stop`ped session's host record and conversation survive but its background
  recovery does not resurrect it.

Neither fix, by itself, is wrong — but reviewing `#2449` surfaced two lifecycle
races (reap-vs-in-flight-resume lock ordering, and an unlocked
`resume_on_reattach` clear racing a concurrent redeploy) that are exactly the
class of bug this effort exists to close systematically: two code paths
independently deciding a session's liveness without one arbiter.

## Request

> We need to get more pieces of this effort written upstream in
> copilot-extensions and made to coherently vision-align.

## Plan

### Phase A — Turn continuity (a running turn always reaches a terminal event)

- [ ] Land `#22`'s transport-lost wake signal and terminal-event append as
      this phase's first PR.
- [ ] Audit every other path that can leave `SessionManager._run_prompt` (or
      its ACP/host-mode equivalents) without appending a terminal event, and
      add the same guarantee there.
- [ ] Add a regression test that drops the transport mid-turn and asserts
      exactly one terminal event is observed by an SSE-equivalent reader,
      never zero and never more than one.

### Phase B — Explicit-stop and idle/failed dormancy (a stopped session never self-revives)

- [ ] Track `#2449` to merge as this phase's first PR, closing `#2445`.
- [ ] Close the two lifecycle races the 2026-09-11 review found still open at
      `#2449`'s reviewed head: make reap and in-flight resume mutually
      exclusive under one lock, and move `resume_on_reattach`'s clear inside
      the same lock `graceful_cancel_for_redeploy` and `stop_session` already
      use.
- [ ] Add growing backoff for sustained reconnect failure against an
      unreachable target, closing `#2378`.
- [ ] Confirm an idle session with no live ACP consumer stops retrying and
      goes dormant under the same gate `#2449` adds, with an explicit test for
      the no-consumer case, closing `#2379`.
- [ ] Add a regression test that explicitly stops a session, runs a heartbeat
      pass and a daemon restart, and asserts no reconnect/resume attempt
      occurs until an explicit resume or `send`.

### Phase C — Reconcile as one state machine (follow-on, not blocking A/B)

- [ ] Once A and B are individually green, write down the full session-state
      transition table (running/idle/stopped/failed/dormant × transport-drop /
      heartbeat / daemon-restart / explicit-stop / explicit-resume) as a single
      source of truth, so a future incident can be checked against a diagram
      instead of re-derived from code.
- [ ] Identify whether the reap-lock and resume-lock introduced in Phase B are
      the same lock used elsewhere in the session lifecycle, or whether this
      effort has now created the state machine's actual single arbiter; file a
      follow-on issue if a further consolidation is warranted.

### Proposed finite Session Host child-diagnostic lane from #4231/#4223

This is planned receiving under existing owners, not implementation or
resumption of the Draft effort. It is independent of finishing all Phase A/B/C
work. The [ordered tracking map](../backlog-reconciliation/reliability-ordered-tracking.md)
retains the whole wrapper and its separate source-specific receiving gate.

- [ ] **RD4231-P1:** Deliver authoritative startup diagnostic output from
  the ACTUAL supported ACP Session Host CHILD through child-to-Host relay
  and consumer persistence/query. Preserve bounded content, appropriate
  redaction and correct child/Host/process/session/native/provider attribution.
  Host bootstrap capture, filenames, a helper or classic-only child stderr
  is not this consumer.
- [ ] **RD4231-P2:** Retain the complete D4231 issue contract directly in
  OPEN #2464: credit finite #547/#553/#559 and bounded same-conversation
  retry/controlled fallback; preserve default no-recreate and exact native/
  provider/remote/privileged/cold-recovery custody; terminal launch-policy/
  cleanup-pending refusal, bounded exhaustion and truthful STOPPED/error.
  Where current authority permits opt-in fresh fallback, preserve old/new
  native identity AND `context_dropped=true`; unknown authority, uncertain
  cleanup and refusal remain holds. Do not reimplement the completed ladder.
- [ ] **RD4231-LV:** Qualify the actual child-to-Host and consumer
  persistence/query path, bounded/redacted content and correct attribution.
  Preserve finite retry-success/exhaustion, allowed-fallback/failed-fallback
  and terminal refusal/unknown controls. Reuse unchanged admitted evidence
  where applicable and retain original actual-consumer/native tiers;
  no new every-platform, every-host or whole-programme gate is added.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#2379** agent-bridge: let an idle session with no ACP consumer go to sleep instead of retrying indefinitely
  - An idle session retrying indefinitely instead of sleeping is directly this effort's lifetime-ownership scope.
- [ ] **#2378** agent-bridge: back off SSH/container reconnect attempts under sustained failure
  - Reconnect backoff under sustained failure is directly this effort's `connection-loss-never-destroys-the-target` behavior.
- [ ] **#2041** agent-bridge: Reattach surviving provider Session Hosts instead of recreating sessions
  - Reattaching instead of recreating is this effort's own named `reattach-never-kill` behavior, verbatim.
- [ ] **#1362** agent-bridge daemon flapping: stale active.json port mapping + failed auto-update cutovers + wedge on remote-session-host recovery
  - Daemon flapping / wedged remote-session-host recovery is the same lifecycle-truthfulness scope this effort closes.

## Validation Plan

- [ ] A running turn whose transport drops mid-turn always produces exactly
      one terminal event, never zero, never duplicated.
- [ ] An explicitly stopped session survives a heartbeat pass and a daemon
      restart without auto-resuming, retaining its host record and
      conversation for explicit resume.
- [ ] An idle or failed session with no live consumer stops retrying an
      unreachable target rather than continuing indefinitely.
- [ ] Sustained reconnect failure grows its backoff rather than retrying at a
      fixed short interval.
- [ ] Reap and in-flight resume cannot both act on the same session record;
      the loser observes a clean, explicit failure rather than corrupting
      state.
- [ ] `resume_on_reattach` cannot be left incorrectly set (or cleared) by a
      race between an ordinary stop and a concurrent redeploy/drain.
- [ ] Every new test is deterministic and does not depend on wall-clock
      timing beyond an explicitly injected fake clock or fault.
- [ ] **RD4231-G:** Resolve every RD4231 Plan/local Validation and the
  complete D4231 issue contract at the original supported Host CHILD
  diagnostic/recovery consumer. Keep classic stderr and Host bootstrap
  evidence separate; documentary receipt is not a diagnostic fix or native
  pass. Existing Phase A/B/C, exactly-once terminal, stop/dormancy, provider,
  cleanup, original native and current claimant conditions remain intact.

## Journal

### 2026-11-12 - Proposed finite Host-child diagnostic receiving

Added direct RD4231 Plan/local/global Validation and continuing custody
for the complete #4231 inherited #4223 diagnostic/recovery lane. The actual
shared Host child spawn still sends stderr to DEVNULL; classic bounded
events and the Host bootstrap tail are credited distinct partials.
This is a receiving proposal, not future review/merge, actual issue
delivery, implementation, native evidence, source disposition or resource
release. The ordered TRACK map and each operational keeper remain separately
adjudicated. Status stays Draft and every incumbent row is unchanged.

### 2026-09-11 — Kickoff and reconciliation

- Opened [#2464](https://github.com/ThomasMichon/copilot-extensions/issues/2464)
  as the public coordination token, reconciling two previously separate
  incidents (`#22`; `#2445`/`#2378`/`#2379`) under one effort.
- Positioned `#2449` as Phase B's first implementation slice, carrying forward
  its 2026-09-11 review findings (two open lifecycle races) as this effort's
  first concrete exit-criterion gate rather than a standalone follow-up.
- Left Phase C (full state-transition table) as an explicit follow-on so
  Phases A and B can land independently without waiting on a larger
  consolidation.
