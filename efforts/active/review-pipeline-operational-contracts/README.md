---
visions:
  - visions/plugins/agent-bridge
  - visions/plugins/agent-dispatch
---

# Runtime Operational Contracts

- **Slug:** `review-pipeline-operational-contracts`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice PRs against dev
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** Existing bridge lifecycle and dispatch authority/readiness intent
- **Umbrella issue:** [#6274](https://github.com/ThomasMichon/copilot-extensions/issues/6274)
- **Sub-issues:** [#6266](https://github.com/ThomasMichon/copilot-extensions/issues/6266)

## Guiding Intent

An installer must distinguish installing a runtime from activating its serving
generation. Operator readiness must reveal unavailable authority without
escalation or state effects. Windows process fixtures must enforce the actual
window, focus and ownership contract rather than hide a regression.

This closes existing lifecycle and authority intent. It does not introduce
consumer-specific review policy or a new background monitor.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Contracts coordinator | Own reviewed plan, integration and completion | This isolated worktree |
| Evidence reviewers | Bounded read-only source tracks and plan critique | Coordinator-mediated reports |
| Windows census repair | Correct proven process-attribution defects and preserve native failure evidence; exact hosted cause remains open | Public issue #6266, source `tidy-rotor` |

## Coordination

Serial reviewable slices preserve existing owner boundaries: bridge installers,
unified updater result propagation, dispatch authority and Windows status-monitor
process tests. Before edits, claim relevant issues and reconcile active overlap,
including #6266 and existing Windows launch efforts. No participant edits the
anchor, deployed payload, another participant's worktree or runtime state.
The Windows census participant owns only the existing status-monitor regression
fixture and its focused documentation. Activation/readiness implementation and
any production launch change retain their existing owners.

## Request

**Public-safe summary:** build an effort and necessary design changes, then
implement truthful activation results, safe explicit control-authority
readiness and the tracked Windows terminal-host regression. Consumer-specific
CLI/report policy is not part of this repository.

## Context

Source inspection found bridge installers can reach a completion summary after
cutover refusal/rollback. The unified updater already has installer exit-code
boundaries; success must not depend on stdout wording or installed-slot markers.

Control-only calls correctly fail with absent/wrong client authority, but there
is no explicit, credential-safe client proof before a requested mutation.
Existing ordinary worker credentials must not gain control privileges.

#6266 reports seven terminal-host descendants where the fixture permits one.
The production spawn already uses the shared windowless helper; source alone
does not distinguish an effective launch defect from an observation assumption.
Diagnose the actual boundary before changing it.

## Plan

The mechanism/test details below are **agent-recommended**, not an expansion of
the operator's requested outcomes. [Design contract](design.md) pins interfaces
and negative controls before implementation.

### Phase 0 - Reviewed proposal
- [x] Land this effort and contract through normal review before implementation
      (#6457).
- [ ] Reconcile relevant existing lifecycle/auth/process patterns and active
      issue ownership. Keep every publication self-contained and neutral.

### Phase 1 - Truthful serving activation
- [ ] Preserve existing cutover result vocabulary and process exit boundaries;
      verify actual serving generation/version/readiness before completed rollout.
- [ ] Report installed-but-not-activated as explicit partial/failure, including
      rollback; propagate it through the parent update result.
- [ ] Preserve routine non-forcing cutover, active-turn ownership, generation
      fences and pre-commit rollback/post-commit forward semantics on both shells.

### Phase 2 - Read-only control readiness
- [ ] Add explicit `agent-dispatch control-readiness` with bounded/redacted
      configuration source/presence and authenticated authority proof.
- [ ] Add a dedicated read-only control-guarded probe rather than test by
      transition; preserve ordinary bearer and absent server-authority refusal.
- [ ] No service-env discovery, automatic credential copying, configuration
      mutation, elevated fallback or auth material in output/argv.

### Phase 3 - Windows terminal-host boundary
- [x] Correct proven stale-parent PID attribution and retain native recurrence
      diagnostics (#7310); this does not resolve the historical hosted cause.
- [ ] Reproduce #6266 through the existing bounded runner and owned fixtures;
      compare effective launch flags/process identity with census assumptions.
- [ ] Fix only the proven cause while retaining visible-window/focus,
      single-hidden-tree and containment/lifetime assertions.
- [ ] Missing native prerequisites require a recorded Blocked state/evidence on
      #6266, not a weakened assertion, retry loop or completion claim.

### Phase 4 - Contract adoption
- [ ] Update owning patterns/architecture/skills and portable negative examples.
- [ ] Add mandatory changefiles, run bounded focused regressions and appropriate
      contract/install guards; audit final public artifacts and required checks.
- [ ] Land each implementation through review, prove release promotion, and
      record authorized rollout responsibility separately from merge.
- [ ] Resolve every Plan/Validation Plan item before effort completion/archive.

## Validation Plan

- [x] Reused root/intermediate parent controls fail before the census correction;
      conservative unavailable-identity, multiple-real-host and bounded native
      interpreter/console controls pass after it (#7310, 21 cases).
- [ ] Both installer shells: busy/rollback/old serving version or failed
      readiness cannot report completed activation; successful cutover proves
      expected version/protocol and parent result propagation.
- [ ] No routine force/cancel, stale installer promotion, or active-session
      interruption introduced by rollback or result handling.
- [ ] Control readiness covers absent/wrong/worker credentials, missing server
      authority, command nonzero/empty/timeout and authorized direct/command
      sources, with no queue/config/generation/receipt effects or secrets.
- [ ] #6266 reproduction/fix preserves the exact native process-window-focus
      contract and cleans only owned descendants under bounded containment.
- [ ] Windows and POSIX focused tests, documentation-impact review, relevant
      install/contract guards and final provider reviews/checks pass.
- [ ] Public content contains no adopter-private identifiers, paths or rationale.
      Activation/rollout claims name verified evidence, not installed markers.

## Proposal

The [operational contract proposal](design.md) defines the activation,
authority-readiness and Windows process boundaries for review before
implementation.

## Journal

### 2026-10-20 - Bounded census correction merged; native causality retained

- #7310 merged as `77e5d33d537968073d11ea3303c2715076717bac` after current-head
  Copilot approval with no open findings and required checks passed. The source
  worktree pulled forward and the Plan records only that bounded completion.
- The historical hosted seven-host occurrence still needs discriminating native
  evidence. Its hosted Python was 3.12.10; the existing local source run used
  3.11.16, with the same psmux 3.3.8. The exact-version context comparison used
  worktree-local Python 3.12.10 after the existing interpreter lookup failed,
  without bin links or registry registration: 22 focused/native cases passed.
  One paired corrected/original numeric-census native control also passed
  (two cases); its temporary control was removed afterward. Neither reproduced
  the historical seven-host occurrence or established its cause.
- The remaining exact-context evidence is blocked on enrolled native Windows
  container capacity in the existing portfolio (#1303), not on another local
  retry. Keep #6266 open, preserve its claim and continue owner coordination;
  do not reactivate a disabled hosted job or call local non-reproduction fixed.
- Activation/readiness scope, exact Windows process/window/focus acceptance,
  containing external-lane qualification and authorized rollout remain open.

### 2026-10-20 - Windows census attribution correction

- Two fail-first controls reproduce false inclusion of a terminal host older
  than its current numeric root or intermediate parent. Toolhelp parent PIDs
  survive the original parent's exit and can name a later unrelated process.
- The fixture now rejects only ancestry disproved by Windows creation times.
  Unavailable identities remain conservatively included; a second real terminal
  host still counts. Production launch flags, the one-hidden-host bound, zero
  visible-window/focus assertions and all process deadlines remain unchanged.
- A contained native run observed one hidden root console across all eight
  existing psmux cycles. This does not establish the exact hosted seven-host
  cause or close #6266. Recurrence evidence includes terminal birth/parent and
  baseline observations plus the probe's actual console membership.
- Hosted Windows integration is currently unenrolled in the reviewed periodic
  portfolio. Do not reactivate a disabled workflow or treat relocated/skipped
  tests as a repair. Exact hosted causality and native containing-lane evidence
  remain open; publication/review of this bounded correction is still required.

### 2026-10-19 - Reviewed proposal and prerequisite source reconciled

- Proposal #6457 merged as `4ec7f4c5d`; the original Draft/proposal-review
  status is superseded. Adjacent receipt support #6420 (`66423aed2`) and
  backed publication recovery #7089 (`c81ed22bb`) are also merged.
  Their former review waits are historical, not current blockers.
- Next: the Contracts coordinator must reconcile the retained source/journal
  responsibilities with the receipt and lifecycle owners, verify containing
  releases separately, then select the truthful serving-activation slice.
  Do not repeat the completed prerequisite changes or absorb another owner's
  operational recovery.
- Truthful activation, read-only control readiness, the unchanged Windows
  process/window boundary and all contract-adoption validation remain open.
  Source merge establishes neither serving activation nor authorized rollout.

### 2026-10-12 - Proposal

- #6274 claimed the generic campaign; reused #6266 rather than another tracker.
- Four independent evidence reports and one plan critique informed owner
  boundaries. Truthful activation precedes ergonomics; no implementation yet.

## See Also

- [Design contract](design.md)
- [Graceful cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md)
- [Windows launch pattern](../../../docs/patterns/windows-background-process-launch.md)
