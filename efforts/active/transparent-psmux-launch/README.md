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
- [ ] Land the implementation through review and the required checks.
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
