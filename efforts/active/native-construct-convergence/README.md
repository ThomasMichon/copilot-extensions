# Native-Construct Convergence

- **Slug:** `native-construct-convergence`
- **Repo:** copilot-extensions (control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-23
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** [#985](https://github.com/ThomasMichon/copilot-extensions/issues/985)
- **Sub-issues:**
  [#986](https://github.com/ThomasMichon/copilot-extensions/issues/986) (Phase A — worktree layout),
  [#987](https://github.com/ThomasMichon/copilot-extensions/issues/987) (Phase B — roots/identity mapping),
  [#988](https://github.com/ThomasMichon/copilot-extensions/issues/988) (Phase C — delegate create, deferred),
  [#989](https://github.com/ThomasMichon/copilot-extensions/issues/989) (Phase D — steering onto cloud/agent-host)
- **Vision:** **vision-closing** against the new
  [`visions/native-convergence`](../../../visions/native-convergence/README.md)
  (authored alongside this effort — it states the standing intent and the two
  guardrails; this effort closes its delta vs. reality). Related:
  [`visions/agent-fabric`](../../../visions/agent-fabric/README.md),
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md).
- **Reality docs:**
  [`plugins/agent-worktrees/docs/architecture.md`](../../../plugins/agent-worktrees/docs/architecture.md) ·
  [`docs/architecture.md`](../../../docs/architecture.md)

## Guiding Intent

Copilot CLI is natively absorbing the spatial model this harness pioneered:
native worktree create/switch, per-session workspace metadata, an explicit
session/working boundary, catalogued working locations (projects), source and
worktree roots, and cloud session steering over its agent-host protocol. Where
the CLI now owns a primitive natively, the harness should **converge onto the
native construct** — delegate the primitive, align its vocabulary and on-disk
layout to the CLI's shape, ride the CLI's identity and steering surfaces, and
reserve its own effort for the durable value the CLI does not provide
(finalize/cleanup, cross-machine reach, resource claims, the picker, PR-gating,
clone/source policy, coordination).

This effort drives that convergence to reality against the standing
[`visions/native-convergence`](../../../visions/native-convergence/README.md)
vision, under two firm guardrails the vision states as Behaviors:

- **No capability regression** (`§Behaviors/no-capability-regression`) — no step
  removes or weakens an existing harness capability; anything the CLI lacks is
  retained on top of the native construct.
- **No hard dependency on an unreleased/unstable native construct**
  (`§Behaviors/feature-detected-convergence`, `staged-behind-released-surfaces`)
  — reliance is feature-detected and staged behind released surfaces, with the
  harness's own implementation kept as the fallback.

## Context

The harness's own worktree layout was previously aligned to the CLI's *original*
`<anchor>.worktrees/<worktree>` shape (see the June 2026 layout-alignment work).
The CLI has since realigned its native worktree/root model to the cleaner
`<worktree-root>/<repo>/<worktree>` layout, so the harness should follow it back
— a change entirely under the harness's own control (no external dependency),
which is why it leads as Phase A.

The remaining native constructs the harness converges onto — per-session
workspace identity, the working boundary, catalogued projects, source/worktree
roots, and cloud steering over the agent-host protocol — are enumerated in the
vision's §Concepts. Some of their delegation surfaces (native root flags, native
worktree create) are **not yet released/stable**, so this effort **maps onto**
them now (Phases A–B, D where the surface exists) and **defers delegation**
(Phase C) behind feature detection until they ship.

This effort records **delta-closure state only** — the vision states the target
and is not edited to log progress.

## Plan

Phases are ordered by dependency, not calendar. Each phase must prove it
preserves every existing harness capability before it lands.

### Phase A — Worktree layout alignment (#986)
- [ ] Revert the harness worktree layout to `<worktree-root>/<repo>/<worktree>`
      (harness-controlled; no external dependency), so worktrees created by
      either the CLI or the harness are mutually discoverable. Closes
      native-convergence §Features/`vocabulary-and-layout-alignment`,
      §Features/`mutual-discoverability`.
- [ ] Validate finalize, cleanup, the picker, and resource claims are all
      preserved across the layout change. Closes
      §Behaviors/`no-capability-regression` for this slice.

### Phase B — Roots, project & session-identity mapping (#987)
- [ ] Map the harness's **source root** and **worktree root** onto the CLI's
      native roots (mapping now; delegation deferred to Phase C). Closes
      native-convergence §Concepts/native-source-&-worktree-roots,
      §Features/`native-construct-as-substrate`.
- [ ] Map the harness's **project/adoption** concept onto the CLI's catalogued
      working location. Closes §Concepts/native-project.
- [ ] Align the harness's **session identity** to the CLI's per-session
      workspace metadata, deriving from the canonical record rather than a shadow
      copy. Closes §Concepts/native-session/workspace-identity,
      §Behaviors/`one-owner-per-primitive`.

### Phase C — Delegate worktree create/switch (deferred — #988)
- [ ] Once the CLI's native worktree create/switch surface (and its root flags)
      are **released and stable**, delegate creation to the CLI, feature-detected,
      keeping the harness's own creation as the fallback. Closes
      native-convergence §Features/`delegate-the-primitive-keep-the-value`,
      §Behaviors/`feature-detected-convergence`,
      `staged-behind-released-surfaces`, `reversible-and-gated-adoption`.
      **Blocked** until those native surfaces ship stable.

### Phase D — Live-session steering onto cloud/agent-host (#989, #1266)
- [ ] Drive the dedicated
      [`agent-bridge-ahp-convergence`](../agent-bridge-ahp-convergence/README.md)
      effort: expose agent-bridge as an AHP host, map live-session coordination
      and handoff onto released native host surfaces, and retain the
      cross-machine and claim semantics those surfaces do not carry. Closes
      native-convergence §Concepts/native-cloud-steering and
      §Features/`ride-native-identity-and-steering`.

## Validation

- **No-regression gate per phase.** Before a phase lands, exercise the harness
  capabilities the vision protects — worktree create/finalize/cleanup, asserted
  disposition, picker rendering, resource claims, and (where relevant)
  cross-machine reach — and prove each still holds after the change. This is the
  operational form of §Behaviors/`no-capability-regression`.
- **Feature-detected fallback.** For any step that relies on a native construct,
  prove the harness degrades to its own implementation when the construct is
  absent or older (§Behaviors/`feature-detected-convergence`).
- **Mutual discoverability.** After Phase A, prove a worktree created by the CLI
  and one created by the harness resolve as the same objects to both.
- **Clean-room / fresh-box.** Exercise the layout and mapping changes on a
  disposable fresh machine (the repo's clean-room rig) to prove no path assumes
  the old layout.

## Coordination

`copilot-extensions` is public and may be driven from more than one private
control repo. **[#985](https://github.com/ThomasMichon/copilot-extensions/issues/985)
is the shared coordination token** for this convergence work; claim a slice
there (comment/assign) before starting, and land changes serially through the
PR-required `main`. Downstream private plans may **link to** this effort and its
issues; the public artifacts stay self-contained and general-purpose.

## Journal

- **2026-08-27** — Expanded Phase D into the dedicated
  [`agent-bridge-ahp-convergence`](../agent-bridge-ahp-convergence/README.md)
  effort and public umbrella #1266. The narrower #989 remains the native
  live-session steering slice; the new effort owns the complete AHP host
  compatibility contract.
- **2026-08-23** — Effort authored alongside the new
  [`visions/native-convergence`](../../../visions/native-convergence/README.md)
  vision. Filed the umbrella (#985) and four phase issues (#986–#989) citing the
  vision items each closes. Recorded the plan: Phase A (worktree-layout revert to
  `<worktree-root>/<repo>/<worktree>`, harness-controlled), Phase B (roots /
  project / session-identity mapping onto native constructs), Phase C (delegate
  worktree create — deferred behind feature detection until the native surfaces
  ship stable), Phase D (live-session steering onto the CLI's cloud/agent-host
  surfaces). No implementation yet — the intent (vision + this effort) lands
  first.
