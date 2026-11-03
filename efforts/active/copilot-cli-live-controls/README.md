# Copilot CLI Live Controls

- **Slug:** `copilot-cli-live-controls`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed proposal and implementation PRs against `dev`
- **Created:** 2026-11-02
- **Status:** Active
- **Vision:** `visions/machine-fleet/README.md` - Existing service authorities; `visions/native-convergence/README.md`
- **Umbrella issue:** #10149

## Guiding Intent

Make Copilot CLI update behavior understandable and quickly adjustable through
live user configuration, without a committed adopter-policy change. Delegate
downloads and exact-version acquisition to Copilot's native mechanisms rather
than creating another installer, scheduler, or fleet authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| bolted-pendulum | Proposal, implementation, review and release stewardship | Managed source worktree and #10149 |

## Coordination

- **Topology:** Serial proposal and implementation PRs.
- **Host (owns PRs):** bolted-pendulum.
- **Delegates:** None.
- **Handoff:** This README and the coordination issue preserve the remaining gate.

## Context

The native `autoUpdate` setting stops future downloads without preventing
activation of already cached packages. In contrast, `--no-auto-update` and
`COPILOT_AUTO_UPDATE=false` can keep selecting an old bundled binary. Bridge
currently adds the former to ordinary ACP launches unconditionally.

Agent Machines already owns declared machine state, including a Windows-only
`copilot-cli-update` resource. Native Copilot settings remain the authority for
the proposed live control; legacy desired-state declarations are not silently
overwritten or replaced by another preference store.

This is vision-closing operator tooling. Existing native transport, process
custody, installation admission and configuration-authority boundaries remain.

## Request

Public-safe capture; private identifiers have been redacted without changing
the requested behavior:

> Sweep machine and harness settings for logic that blocks Copilot auto-update
> or pins a version. Make auto-update easy to enable or disable quickly through
> live configuration across machines, without an adopter-repository PR, and
> make updating, rolling back or pulling forward the local current version
> easier. An old Linux/WSL installation needs recovery from a previous workaround.

Settled operator choices: enable normal auto-update, clear stale blocking
overrides, and update the explicitly selected stale environment on its effective
current channel. The exact private rollout scope remains outside this public
effort.

## Scope Approvals

- The operator approved live preference changes and the selected native update.
- Existing sessions and active installers must remain untouched. Existing
  isolated test, acquisition and sandbox controls remain effective.
- A reviewed source plan does not itself authorize service restarts, installer
  bypasses or an expanded machine rollout.

## Plan

### Phase 1 - Review the control contract

- [x] Land this proposal through normal review before implementation.
- [x] Confirm additive CLI semantics and native delegation, with no new daemon,
  binary replacement, download implementation or committed adopter-policy edit.

### Phase 2 - Implement the bounded operator surface

- [x] Add Agent Machines commands for update-policy/override status, explicit
  live auto-update on/off, native update delegation, cached-version inventory
  and deliberate one-launch cached-version selection.
- [x] Use the native configuration command for writes; refuse unsupported older
  configuration commands with actionable update guidance rather than editing
  malformed or unknown settings documents.
- [x] Make clearing stale environment overrides explicit. Report inherited
  overrides and partial outcomes honestly; a child cannot change existing parents.
- [x] Remove Bridge's unconditional ACP auto-update opt-out while preserving
  explicit caller flags and isolated preparation/test behavior.
- [x] Document explicit propagation over existing selected SSH/local transports,
  retaining per-target outcomes rather than introducing a second fleet registry.
- [x] Keep exact uncached-version acquisition with native `/downgrade`; cached
  launch must reject missing, malformed or unsafe selections before execution.

### Phase 3 - Qualify and publish

- [x] Add focused policy, command, cache-selection and ACP launch regressions.
- [ ] Complete documentation and contract-impact review, changefiles and the
  normal implementation PR/release flow.
- [ ] Record authorized installed acceptance, or transfer an explicitly held
  rollout to a tracked owner without claiming it is deployed.
- [ ] Resolve the Plan and Validation Plan before archiving the effort.

## Validation Plan

- [x] Native configuration delegation preserves unrelated settings and refuses
  unsupported or failed operations without a success-shaped fallback.
- [x] Read-only status distinguishes live preferences, inherited/persistent
  overrides, bundled-version queries and cached packages.
- [x] Preview performs no mutation; explicit application changes only the
  approved user preference/overrides and reports any incomplete effect.
- [x] Cached launch validates version syntax and ordinary cache paths, preserves
  explicit version intent, and never downloads or substitutes another version.
- [x] Native update uses the existing updater and effective channel unless the
  operator explicitly selects another supported channel.
- [x] Local and remote ACP assembly no longer injects the opt-out; explicit
  caller opt-outs and isolated acquisition/test controls remain intact.
- [x] The additive command surface and generic propagation procedure document
  old-runtime refusal, provider-first rollout and current-parent limitations.
- [ ] Required scoped tests and repository contracts pass; reviewed release
  and actual authorized acceptance are recorded separately.

## Proposal

The command names and focused skill are agent-recommended implementation seams.
They expose native operations, not a new update policy service or persistent
global version pin. Deliberate cached-version launch is explicit per invocation.

## Journal

### 2026-11-02 - Proposal

- Claimed #10149 after searching for existing Copilot update-control work.
- Traced the distinct native download and cached-package activation controls.
- Captured the requested live-control boundary and operator choices; no portable
  implementation is claimed before proposal review.

### 2026-11-02 - Proposal accepted

- Proposal PR #10152 merged after a fresh approving review and required checks.
- Preserved concurrent effort-index updates while resolving the proposal rebase.
- Pulled the managed source worktree forward before starting implementation.

### 2026-11-03 - Implementation qualification

- Implemented the additive native controls and focused operator skill. Real
  isolated native on/off/on writes retained unrelated settings; native cache
  inspection also recognizes a CLI architecture different from its Python host.
- Qualified 31 control regressions and 179 ACP/native-shell regressions. The
  independent workspace-owner process passed another 8 cases with 3 opt-in skips.
  Earlier aggregate Windows disposal failures remain tracked in #10225; the
  successful independent run also completed sandbox and process cleanup.
- Removed unconditional ACP suppression without changing explicit caller flags
  or isolated preparation. Windows explicit-backend setup now avoids unused
  default discovery, matching the existing POSIX selection order. Its real-shell
  fixture keeps legacy-shape rejection and envelope acceptance in separate
  bounded cases rather than increasing deadlines.
- Documentation, install-contract, lint and enforced static-type checks passed.
  Review, promotion and authorized installed acceptance remain separate gates.
