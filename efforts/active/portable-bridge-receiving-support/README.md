# Portable Bridge Receiving Support

- **Slug:** `portable-bridge-receiving-support`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed functional slices targeting `dev`
- **Created:** 2026-11-05
- **Status:** Draft
- **Vision:** [agent-bridge](../../../visions/plugins/agent-bridge/README.md#host-selected-receiving-support) - portable receiving support; [cli-default-bridging](../../../visions/cli-default-bridging/README.md#standalone-remote-driver-and-scoped-managed-delivery) - independent driving capability
- **Umbrella issue:** #10636

## Guiding Intent

An otherwise supported Bridge-managed Copilot launch must work when the
receiving venue's full coordination suite is absent, old or independently
updated. Bridge brings the minimal compatible support for its selected mode.
Repository hosting policy, workspace identity, authenticated transport and
exclusive session custody remain authoritative at the receiving boundary.

This is composition of existing owners, not another launcher or a switch from
ACP to CLI mode by default.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Portable-support driver | Canonical plan, receiving-support composition, tests and serial PRs | #10636 and this effort |
| Existing launch owners | Retain canonical Manager mux/pane assets and installation-independent lifecycle | #8669 and #5579 |
| Existing driver and hosting owners | Retain mutation generations, host recovery and session custody | #5930, #3194 and #6467 |

## Coordination

One owner drives the functional sequence in one source worktree. Publish the
first complete ACP flow as soon as its gates pass, then extend the existing
muxed flow; neither partial delivery closes this effort.

Consume canonical launcher assets rather than fork their source. Coordinate
actual overlapping edits with their existing owners before changing those
paths. This effort does not assume their custody or resume their retained
attempts. No parallel daemon, allocator, installer or control protocol.

## Context

- Bridge's provider preflight invokes guest Worktrees for `agent-policy`, and
  for selected managed-worktree resolution. Its portable Session Host receiver
  also delegates policy to installed guest Worktrees. These are separate
  consumers; repairing only preflight would leave launch enforcement broken.
- `libs/repository-policy` already transports the canonical resolver, portable
  PyYAML source and license notices for a fresh read without site packages.
  Its current guest probe is explicitly preflight-only, refuses an explicit
  installation context, and is not fallback permission after an incompatible
  installed receiver. Review an explicit receiving contract amendment first.
- The Session Host already ships the selected host-role closure over existing
  venue transports. The standalone remote-driver already supplies extension
  driving without Bridge or Worktrees installed in the guest.
- Manager owns the canonical mux launch and copied pane-shell assets;
  Worktrees consumes that boundary. #8669 retains installation-independent
  lifecycle and #5579 launch-depth reduction. Packaging should compose these
  assets, not reproduce their terminal, seed, exit and process-ownership logic.
- #10622 and its [completed source effort](../../2026/11/05%20responsive-codespace-admission/README.md)
  provide responsive claim/relay operations and cancellation custody. They do
  not remove guest-suite requirements or qualify installed receiving support.

## Request

Portable source direction:

> We've reduced the complexity around the mux-session launcher to be pretty compact. If necessary we could merge it with agent-remote-driver to be a small "deployable" which registers the plugin-wrapped extension, sets up TMux, and has just the necessary pieces to create mux-wrapped copilot runs which are compatible with launching by agent-worktrees and worktee-manager. But this becomes agent-bridge's "foreward-deployable" tool to drive Copilot in any location, whether our full suite is present, updated, or otherwise.

The bounded acceptance is recorded in #10636. Combining packages is conditional
on useful simplification, not a required rewrite. Preserve standalone use,
existing launch owners and the chosen session mode.

The phased sequence and detailed validation controls below are
**agent-recommended implementation refinements**, not additional deployment
authority.

## Scope Approvals

Source investigation, reviewed implementation and ordinary issue/PR publication,
with caller-contained synthetic process and transport fixtures. A reviewed
plan does not authorize shared rollout, daemon restart, guest full-suite
installation, global plugin-setting rewrites, credential inspection/export,
forced claims or replacement of retained sessions. Native/external execution
requires its own applicable authority and is recorded separately.

## Plan

### Phase 1 - Reviewed contract and composition boundary
- [x] Reserve the public issue, confirm compatible effort adoption and deduplicate existing owners.
- [x] Trace both guest-policy consumers and identify the canonical policy, Session Host, remote-driver and Manager launch assets.
- [ ] Review the standing-intent clarification and this plan before implementation.
- [ ] Inventory supported local, SSH, Codespace and container receiving paths in ACP and muxed modes; reuse their owners rather than add a venue runtime or transport.
- [ ] Specify the portable receiver's selected marketplace/context and compatibility boundary without weakening negative policy or adding a mandatory guest-owner RPC.

### Phase 2 - Complete ACP flow without a guest suite
- [ ] Deliver canonical policy support through the existing Bridge transport/bundle, independent of absent, old or current guest Worktrees/Bridge installations.
- [ ] Check the actual execution checkout and selected overlays freshly at preflight and launch/resume/successor boundaries; a preflight answer is not a launch grant.
- [ ] Preserve managed workspace identity through its existing allocation/resolution owner. Do not reinterpret a worktree ID as a path or replace allocation with raw Git operations; distinguish a genuinely unavailable managed operation from ordinary provider-workspace launch.
- [ ] Separate no-wake inspection from an authorized activating launch through the venue owner; preserve claim, relay readiness and original-attempt cancellation.
- [ ] Prove the complete ACP consumer flow, publish it through required gates and steward its PR to merge before extending it.

### Phase 3 - Complete the existing muxed flow
- [ ] Stage the same standalone remote-driver plugin at session creation and compose canonical mux/pane launch assets with only their required support closure.
- [ ] Supply required TMux/native-mux setup through its existing owner, scoped to the selected launch. Preserve installed guest state and unrelated sessions; unavailable genuine prerequisites remain explicit, not a request for the full suite.
- [ ] Preserve Worktrees/Manager launch ownership, literal arguments, seed delivery, native process/terminal lifetime, exit handling and driver generation/exclusive mutation semantics.
- [ ] Consolidate packaging only where it removes duplication. Preserve independent remote-driver use, ACP behavior and explicit mode selection.
- [ ] Prove the complete muxed consumer flow, publish it and steward its PR through merge.

### Phase 4 - Compatibility and closeout
- [ ] Document supported caller/receiver combinations, retained-host recovery and rollback using actual selected capabilities rather than package-version equality.
- [ ] Update reality docs, focused tests and changefiles; retain historical protocol evidence and the required unit-only portfolio.
- [ ] Record source, release and installed/native/external results separately; resolve every Plan and Validation Plan item before Done.

## Validation Plan

- [ ] Real isolated receiving processes exercise sparse, old-suite and current-suite guests; guest suite commands can be unavailable or incompatible without becoming the selected Bridge receiver.
- [ ] Allowed, denied, malformed and unreadable repository policy, canonical/legacy paths and selected-marketplace overlay denials use the same canonical evaluator.
- [ ] A policy change after successful preflight refuses actual launch; resume/successor checks retain the actual execution checkout and context, with no silent legacy-context downgrade.
- [ ] Ordinary provider workspaces and managed worktree identities remain distinct; unknown/mismatched allocation refuses before creating another workspace or session.
- [ ] No-wake reads leave stopped fixtures untouched; explicitly admitted activation reaches only its selected fixture through the venue owner.
- [ ] ACP and muxed flows each prove launch, observation, one admitted send and reconnection to the same conversation; no effectful probe-and-fallback replay.
- [ ] Slow/cancelled preparation, failed delivery, unconfirmed cleanup and rollback retain original resources and pending effects. Cleanup cannot remove a newer receiver or driver generation.
- [ ] Scoped extension loading, real native mux consumption where available, human takeover and exclusive control retain existing semantics; fake-SDK controls are not native acceptance.
- [ ] New/prior caller and receiver combinations, provider rollback and missing capability produce a supported result or a specific pre-effect refusal, not blanket guest-suite upgrades.
- [ ] Focused unit/real local consumer and fresh-environment controls, required source gates, review and CI pass. Record unavailable native/external tiers honestly and obtain effect authority before any live qualification.

## Proposal

Extend the host-owned support already shipped by Bridge. The portable receiver
is a deliberate execution owner, not fallback after an installed owner's
failure. It carries canonical enforcement code, not cached permission, and
qualifies its selected context against the actual receiving checkout.

Keep the current legacy probe's meaning until the new receiving contract is
reviewed and implemented. Prefer one shared canonical evaluator and existing
bundle/transport lifecycle. For CLI mode, stage the existing plugin and launch
assets rather than requiring a full guest Manager or inventing a second mux
implementation. The exact support closure follows the consumer trace and tests.

## Journal

### 2026-11-05 - Reviewed-intent preparation

- #10622 and its archive #10672 are merged. This proposal is the next finite
  source objective, not installed acceptance of those changes.
- Confirmed both guest Worktrees requirements and the probe's narrower current
  contract. Preserved hosting refusal instead of substituting a weaker reader.
- Compared Request, Guiding Intent and Plan: retained conditional packaging,
  launch-owner compatibility and guest-suite independence without adding a
  default-mode change or deployment grant. Implementation awaits review.
