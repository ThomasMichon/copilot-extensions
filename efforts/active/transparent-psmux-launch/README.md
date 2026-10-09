# Transparent PSMux Launch

- **Slug:** `transparent-psmux-launch`
- **Repo:** copilot-extensions
- **Branch(es):** Independent plan and implementation PRs against `dev`.
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/installer/README.md`, optional control-plane session launch.
- **Umbrella issue:** #5878

## Guiding Intent

Make Windows mux launch commands inspectable without changing their execution
semantics or weakening host security policy. This realizes the existing
installer launch contract; it introduces no new architectural intent.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launcher implementer | Plan, implementation, release and validation | Owning PR and issue #5878 |

## Coordination

- **Topology:** Sequential plan and implementation PRs.
- **Host (owns PRs):** Launcher implementer.
- **Delegates:** None.
- **Handoff:** Continue from this README and the owning PR's evidence.

## Context

Worktree Manager's PowerShell launcher and agent-worktrees' Python mux pane
generator encode a script that decodes the wrapper path and JSON argv before
invocation. PSMux space-joins pane argv, so merely replacing the encoded script
with unquoted arguments would lose the existing quoting contract.

## Request

Inherited request, summarized: replace the encoded PowerShell mux launch with
a transparent file-based command after the machine prerequisites are ready.
Preserve the complete argument vector and keep host security policy intact.
The operator confirmed the slug `transparent-psmux-launch`.

## Plan

### Phase 1 - Review the launch contract
- [x] Land this proposal through automated PR review before implementation.

### Phase 2 - Implement the file-based handoff
- [x] Replace the encoded Windows pane launches with a shipped PowerShell
  `-File` entrypoint and JSON argv manifest.
- [x] Preserve paths with spaces/quotes, empty arguments, Unicode, passthrough
  flags, wrapper control arguments, exit diagnostics and environment handling.
- [x] Preserve AHP token protection; never put plaintext credentials into the
  manifest. _(agent-recommended safety requirement)_
- [x] Define manifest ownership and cleanup for successful consumption, launch
  failures, retries and respawns without deleting data a live attempt needs.
  _(agent-recommended reliability requirement)_
- [x] Update affected asset/deployment declarations, focused tests, directly
  related documentation and release changefiles.

### Phase 3 - Release and verify
- [x] Land the implementation through review and the required checks.
- [ ] Confirm promotion to the release branch, deploy through the supported
  unified update and prove an actual Windows worktree mux launch succeeds.

## Validation Plan

- [x] Run focused source and PowerShell subprocess tests for exact argv
  round-tripping and both producer paths. _(agent-recommended)_
- [x] Exercise missing/malformed manifests, failed creation/retry, consumption
  cleanup and respawn ownership. _(agent-recommended)_
- [x] Verify real PSMux launch with synthetic payloads, including quoting and
  child exit propagation, on Windows. _(agent-recommended)_
- [x] Exercise launch from a windowless parent; observe no unintended windows
  or foreground transitions and no leaked descendants. _(agent-recommended)_
- [x] Exercise first-touch behavior in a fresh temporary state root where
  practical; record the reason for any unavailable clean-room/external lane.
  _(agent-recommended)_
- [x] Run applicable lint, install-contract, asset/deployment and documentation
  impact checks before publication. _(agent-recommended)_
- [ ] Record deployed version and affected-host launch evidence without private
  identifiers in public artifacts.

## Proposal

Use a shipped script rather than generated executable text. Carry the argument
vector as data, not nested Base64 code. Inspect existing file-based handoff and
quoting helpers before adding a new mechanism. Do not change unrelated SSH
encoded commands or initial-prompt transports.

## Journal

### 2026-10-08 - Proposal
- Claimed #5878 after searching existing open launcher issues.
- Recorded the inherited objective and explicit validation obligations.

### 2026-10-09 - Plan approved
- Proposal PR #5880 passed automated review and CI and merged.
- Beginning Phase 2 against the reviewed plan.

### 2026-10-09 - Implementation and native evidence
- Added the shipped `pane-launch.ps1` dispatcher and version-1 JSON
  `{version, wrapper, argv}` handoff. The existing pane wrapper stays unchanged.
- Preserved custom-wrapper exit codes and exact string argv, including
  date-shaped values that older PowerShell JSON conversion would coerce.
- Both actual producers passed two real PSMux cycles from a headless parent,
  using paths containing spaces, apostrophes, dollar signs and Unicode;
  window/focus observation and descendant-retirement assertions passed.
- Focused handoff and pane-lifecycle suite: 137 passed. PowerShell transport,
  retry, malformed-input and AHP contract coverage passed; deployment tests
  proved the new script copies into a fresh versioned payload slot.
- First-touch evidence used fresh user/profile and PSMux data roots with warm
  pooling disabled. A full disposable-machine installer lane was not run:
  installers were unchanged and their existing copy contract was exercised.
  No remote Linux venue can exercise native Windows PSMux; the actual Windows
  venue supplied the live consumer lane.
- Publication gates passed, including the committed-diff shrink-only module
  guard after extracting Windows manifest transport into a focused helper.
  The 137-test focused suite and both real PSMux producers passed again after
  that extraction.
- Documentation impact: updated the canonical launcher README with the JSON
  contract, quoting, credential separation and one-shot cleanup ownership.
  Unix launch remains unchanged; this replaces only the Windows encoded
  transport and preserves the existing wrapper contract.
- Publication and the release/deployed-worktree proof remain outstanding.

### 2026-10-09 - Standalone packaging review
- Implementation PR #5892 identified that the standalone agent-worktrees
  release also needs the new dispatcher in its materialized fallback assets.
- Added it to the asset manifest and mirrored cleanup list. A packaged-preview
  regression proves Windows fallback deployment and command generation with
  no separate Worktree Manager checkout; the deploy-contract guard now freezes
  the mirrored list. Focused packaging, deployment and removal tests: 22 passed.
- A subsequent review identified orphaned manifests after a timeout where no
  pane ever starts. Python now uses the runtime's dedicated `pane-args/`
  directory; the resident monitor and next producer expire unconsumed files
  older than 24 hours, while younger delayed consumers remain usable.
- Exact expiry-boundary, never-started, delayed-consumer, monitor and prior
  handoff/deployment coverage: 330 passed, one platform-specific skip.
  Both native PSMux producers again passed two cycles, including delayed Python
  manifests surviving a sweep and subsequently being consumed.
- Extended that registration to Worktree Manager's PowerShell producer using
  its already-resolved runtime root. Interrupted PowerShell launches now share
  the same expiry owner; neither producer leaves its manifest in loose temp.
  Both delayed native consumers passed again, plus PowerShell retry/transport
  coverage (48 checks) and five exact expiry/registration checks.
- Restricted rejected-command cleanup to the exact launcher basename and
  transport-owned filenames directly under the registered directory.
  Reused the existing cross-version link/junction guard for creation, expiry
  and rejection cleanup. Real Windows junction and unowned-command regressions,
  plus the handoff/lifecycle suite: 146 passed.
- Covered independent-update skew: an older otherwise-healthy manager slot
  lacking the dispatcher now falls back to the packaged wrapper/dispatcher
  pair. Made manifest consumption atomic with an exclusive delete-on-close
  handle; a coordinated two-consumer regression proves exactly one child runs.
  Handoff/lifecycle tests: 147 passed; all transport and both native producer
  tests, including concurrent consumption: 21 passed.
- Added a real resident-loop regression: one eligible loop iteration invokes
  expiry before mux reconciliation, removes an expired orphan and preserves a
  pending manifest. The isolated loop test passed.
- Made rejection cleanup tolerate Windows sharing violations with an explicit
  warning and retention for expiry. Python preserves its original structured
  failure; PowerShell continues its retry/failure path. Handoff/lifecycle tests:
  149 passed; real file-lock, retry and concurrent-consumer checks: three passed.
- Marked the manifest/deployment structural contracts for the required guard
  lane (five passed). Preserved the cmd shim's Windows PowerShell 5.1 fallback
  using its bundled date-disabled Newtonsoft reader for raw plan argv.
  Flat/nested date-shaped argv passed on actual PowerShell 5.1 and 7 (four checks).

### 2026-10-09 - Required CI repair
- PR #5892 received an approval with no unresolved launcher findings, but its
  merged-base CI exposed the new lease-wait tests introduced by #5893.
  Linux contention was classified using only Win32 error codes, so the wait
  returned immediately instead of honoring its budget (#5906, #5907).
- Rebased onto the newer `dev` and claimed #5907. Added platform-specific
  contention classification while preserving immediate persistent-error
  failures, with one-process coverage for both native code families.
  Actual Windows holder/release/timeout and all PowerShell installer contracts
  passed (21 passed, one platform-specific skip).
- The required Linux CI lane remains the live Unix proof. No local Linux
  distro or usable container engine was available; no CI bypass or blind
  failure rerun is used.

### 2026-10-09 - Implementation merged
- The repaired head passed the required Linux guard lane and all PR checks,
  received a fresh approval with no unresolved defects, and merged as #5892.
- The parent #5878 remains open: release promotion, supported deployment and
  the installed Windows worktree-launch proof are still the completion gate.
- Started the required unified consumer update. Continue with release
  observation, projection reconciliation and the affected-host launch proof.

### 2026-10-09 - Release gate fixture repair
- `dev` CI then failed the containment detachment fixture before its descendant
  PID receipt existed (#5916). Its two-second budget included fresh interpreter
  and helper startup, preventing the fixture from reaching the behavior it
  intended to prove.
- Aligned both descendant fixtures with the adjacent scenarios' ten-second
  startup-inclusive wall budget. The children still live for sixty seconds:
  the real containment timeout and descendant-retirement assertions remain
  mandatory, and no production limit changes.
- A full Windows harness run also encountered the six existing mention-guard
  fixture failures explicitly tracked in #5250. They are kept separate from
  this containment repair; the release gate runs that suite on Linux.
- All eleven contained runner tests passed locally. Repair PR #5917 passed
  the required Linux suite and all checks, received approval, and merged.
  Continued with the new `dev` run and release promotion; #5878 stays open.
- The first unified consumer refresh completed successfully and its guidance
  projections synchronized with zero blocking findings. This refreshed the
  prior released baseline, not yet the file-launch change; final release
  deployment and the installed-launch proof remain outstanding.

### 2026-10-09 - Full promotion gate contract repair
- The repaired `dev` CI passed and admitted the full promotion gate. Its only
  suite failure was the late paired-carve policy race (#5919): the test still
  expected a raw policy exception after the creation API gained the typed,
  identity-preserving `LaunchSeedStagingFailure` boundary.
- Kept the production boundary unchanged. The regression now verifies the
  typed failure, original policy cause, created worktree identity/path/branch,
  no-duplicate-create recovery payload and all previous residual-race message
  assertions. All 21 paired-carve tests passed locally.
- The reporter could not dispatch a fix agent because that workflow is
  disabled (HTTP 422). Claimed the unowned repair rather than enabling it.
- Repair PR #5920 received approval, passed all required checks, and merged.
  Pulled forward and continued with its new `dev` CI/full promotion gate.
- The next full run passed that repaired batch and exposed a removal fixture
  that still expected the old direct tracking-delete diagnostic (#5923).
  Narrowed its fault injection to the actual YAML path and verified that
  deletion was attempted, the record remains, and the serialized launch-seed
  removal diagnostic preserves the original lock error. Production ordering
  and safety gates are unchanged; all 33 managed-removal tests passed locally.
- Removal repair PR #5925 received approval, passed required checks and merged.
  Continued with the new validation run; release and installed-host proof are
  still outstanding.
- That full run exposed an eager daemon dependency import (#5928): the CLI
  composition root imports the creation module, which imported the transport
  merely to classify seed-staging exceptions. Deferred that import to the
  failure constructor and extended the existing missing-transport import
  regression to cover the creation class explicitly.
- All 69 audit, paired-creation and seed-staging tests passed. Lint and the
  install-contract gate passed. This below-altitude import repair changes no
  launcher or recovery behavior; no installer/clean-room lane is affected.
