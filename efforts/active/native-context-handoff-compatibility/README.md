# Native context handoff compatibility

- **Slug:** `native-context-handoff-compatibility`
- **Repo:** copilot-extensions
- **Created:** 2026-10-10
- **Status:** Active
- **Vision:** `visions/plugins/context-handoff/README.md`; native-convergence
- **Umbrella issue:** #6028
- **Branch(es):** independent implementation branches off `dev`; coordinator owns publication.
- **Implementation:** capability-contract investigation; runtime behavior unchanged.

## Guiding Intent

Keep one logical objective progressing across model context windows. When native
context management is enabled in effective settings or otherwise detected,
`context-handoff` enhances native checkpoint/recovery rather than creating a
new session solely to refresh model context. Custom handoff remains an explicit,
mode-governed fallback for unsupported venues and actual owner/session transfer.
Compaction remains emergency recovery rather than the planned continuity path.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| coordinator | Plan, evidence, integration, and publication | Managed planning worktree |
| compatibility worker | Future isolated implementation and validation | Dedicated worktree after plan review |

## Coordination

- **Topology:** one canonical public effort, independent implementation slices.
- **Host:** coordinator owns PRs and completion judgment.
- **Delegates:** none launched for implementation.
- **Handoff:** carry this effort, current slice, and unresolved obligations.
  A completed context or merged phase is not objective completion.
- No private deployment record is required to understand or execute this plan.

## Context

The plugin owns continuity policy, not terminal/process lifecycle. Its current
pressure response assumes a successor-session handoff. Native runtimes can
instead expose `get_context_remaining`, `session_artifacts`, `session_history`,
and terminal `new_context`, retaining session/workspace identity.

Implement capability-compatible selection rather than branching on a transport
name or treating a setting as permission to invoke a missing tool. Private or
host-specific adoption policy belongs outside this generic implementation.

## Request

Public-safe capture of the compatibility part of the operator request:

> We'll update our context-handoff plugin to switch behaviors when content management is enabled in settings or otherwise detected.

Here, the operator's phrase "content management" means native context
management, including the effective `contextManagementTools` setting. The
quotation above preserves the original request verbatim.

The operator also requested that this compatibility work have its own effort.
Unrelated repository lifecycle work is intentionally outside this scope.

## Plan

### Phase 1 - Reviewed intent and capability contract

- [x] Publish/review this plan and the corresponding vision extension before
  modifying runtime behavior.
- [ ] Trace effective settings, tool capability, permissions, and context events
  available to the plugin; document a supported detection seam.
- [ ] Define precedence for effective true/false/absent settings, detected native
  capability, stale/unknown evidence, and confirmed unavailable implementation.
  Enabled settings select native guidance; they do not manufacture tool support.
- [ ] _(agent-recommended)_ Distinguish supported-but-not-viable transitions,
  missing/stale checkpoints, policy caps, static-context limits, and errors
  after a successful clear. Do not collapse all failures into session cutover.

### Phase 2 - Native-aware guidance and checkpoints

- [ ] Update ambient instructions, targeted skills, templates, and writer output
  together so native-first guidance replaces unconditional pressure handoff.
- [ ] Preserve original objective, parent completion gate, current slice,
  unresolved requests, settled evidence, and background/external obligations
  in a compact native checkpoint, with exactly one next action.
- [ ] Preserve native owner isolation, bounded reads, and checkpoint revision
  guards. Do not write guessed internal artifact files.
- [ ] Refresh relevant durable guidance after rollover; do not rely on wiped
  conversational skill or one-shot startup context.
- [ ] _(agent-recommended)_ Start with guidance-first checkpoint generation.
  Add a scoped artifact argument modifier only if validation demonstrates a
  concrete completeness gap; preserve complete arguments and agent work state.

### Phase 3 - Pressure behavior and fallback

- [ ] Back off competing custom soft/hard/force triggers when the selected
  native path is viable; never block native checkpoint writes.
- [ ] Reset per-window pressure accounting after confirmed native transitions
  without changing worktree/session ownership.
- [ ] Retain explicit save/trigger/consume and real new-owner/process recovery.
  Preserve existing consent and automatic/manual/off-mode semantics.
- [ ] Retain diagnostic, authorized fallback for unavailable native support.
  Unknown capability is not a silent permanent fallback disablement.
- [ ] Preserve emergency compaction. Do not invoke low-level context clearing
  opportunistically from a timer or non-terminal hook.

### Phase 4 - Acceptance and deployment

- [ ] Run focused contained tests and an isolated CLI/ACP capability,
  checkpoint, rollover, and resume proof where available.
- [ ] Reconcile docs, declare a context-handoff changefile for implementation,
  clear contribution review/CI, and drive implementation through release.
- [ ] Verify deployed behavior through the supported update flow and retain
  explicitly documented unsupported-venue fallback.

## Validation Plan

- [ ] Selection matrix covers settings true/false/absent, detection present/
  absent/unknown/stale, policy denial, custom tool filters, and signal conflicts.
- [ ] No native tools are invoked merely because a setting was enabled.
- [ ] Checkpoints retain parent completion gates, not just latest phase success;
  framework boilerplate cannot conceal an empty recovery checkpoint.
- [ ] Multiple native windows retain session/workspace identity, authoritative
  instructions, objective ownership, and live-work references.
- [ ] Native rollover causes no competing automatic custom pickup or duplicate
  checkpoint mutation; automatic modes retain consent.
- [ ] Partial persistence failure does not provoke blind double rollover.
- [ ] Explicit handoff and unsupported-native recovery remain usable.
- [ ] CLI and ACP exercise actual host continuation, not only advertised names;
  subagent admission/isolation is tested separately.
- [ ] Tests use synthetic state and isolated settings/queues/processes; they
  do not clear or cut over a real operator session.
- [ ] Required implementation tests, docs, changefile, review, release, and
  deployment are resolved or explicitly transferred before marking Done.

## Proposal

[design.md](design.md) separates selection, semantic guidance, operational
backoff, and recovery. It is proposed behavior, not an implemented API.

## Completion Gate

This effort remains open until compatibility is implemented and accepted across
its declared matrix, required review/release/deployment are complete, and every
Plan/Validation Plan item is resolved or transferred by name. Planning publication
alone does not complete the update.

## Journal

### 2026-10-10 - Independent compatibility plan

- Created the canonical portable effort and public coordination issue #6028.
- Split compatibility from unrelated workspace preparation/cleanup proposals.
- No deployed handoff, compaction, native-context setting, or runtime behavior
  was changed by this planning slice.

### 2026-10-10 - Plan reviewed and merged

- Plan and vision publication merged through #6029 after review; addressed all
  three low-severity documentation findings.
- Preserved the operator quotation and clarified its context-management meaning.
- Continuing Phase 1: establish supported effective-setting and native-tool
  detection before changing pressure behavior. Planning merge does not complete
  this effort or authorize a live context reset.
- Added detection acceptance boundaries to the design: session-effective policy,
  provider/override provenance, uninitialized-versus-unavailable metadata, and
  root context-clear versus cwd/subagent events. Public protocol/installed-host
  verification remains open; no internal API or name-only detection is adopted.

### 2026-10-10 - Installed extension contract investigated

- Inspected CLI 1.0.88's installed extension guide, exported `CopilotSession`,
  `SessionCapabilities`, `ToolInvocation`, `CurrentToolMetadata`, and event/RPC
  declarations. The published metadata and capability surfaces do not establish
  session-effective native admission plus built-in/override provenance.
- Recorded the exact missing host observation and selection precedence in
  `design.md`. Settings-only/name-only automatic suppression remains blocked;
  generated declarations are not permission to call internal methods.
- Corrected the canonical effort index in response to #6035's review.
- No plugin runtime, deployed policy, live context, or ownership changed.
  Isolated CLI/ACP transition proof and the supported detection seam remain
  open; this investigation does not complete Phase 1 or the compatibility update.
