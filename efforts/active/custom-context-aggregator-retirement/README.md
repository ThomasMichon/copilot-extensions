# Custom Context Aggregator Retirement

- **Slug:** `custom-context-aggregator-retirement`
- **Repo:** copilot-extensions
- **Branch(es):** sequenced plan, upstream retirement, and adopter-migration changes
- **Created:** 2026-09-07
- **Status:** Draft
- **Vision:** closes
  [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  §Non-Goals/`no-custom-cross-plugin-aggregation-authority`
- **Umbrella issue:** [#2173](https://github.com/ThomasMichon/copilot-extensions/issues/2173)

## Guiding Intent

Retire the custom session-context aggregation authority now that plugins have a
reliable checked-in pointer plus exact-session guidance-file path. Keep the
portable guidance contract independent of one cross-plugin rendezvous, cache,
or spill engine. When the host natively composes every plugin's
`additionalContext`, direct plugin-owned contributions may become the preferred
perfectly dynamic path after version-floor proof, without restoring a custom
authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Architecture driver | Vision, pattern, compatibility boundary, and reviewed sequencing | Isolated worktree |
| Producer migration lane | Remove aggregate-only hooks and preserve exact-session writers | Sequenced implementation commits |
| Validation lane | Scanner, plugin suites, clean-room launch paths, and adopter contract | Disposable fixtures |

## Coordination

- **Topology:** one reviewed plan PR followed by short serial implementation
  PRs.
- **Host (owns PRs):** architecture driver.
- **Delegates:** bounded inventory or validation may be delegated; the host
  integrates all changes and owns the public surface.
- **Handoff:** each implementation PR must leave the suite valid without
  requiring a later PR to restore session startup.

## Context

The completed
[Session Context Aggregation](../../2026/08/31%20session-context-aggregation/README.md)
effort built a deterministic compatibility authority for host releases that
discarded independently correct `sessionStart` `additionalContext` values. The
later
[Session-Scoped Dynamic Guidance](../../../docs/patterns/session-scoped-dynamic-guidance.md)
pattern established a more reliable baseline: a checked-in pointer directs the
agent to a plugin-owned file written beneath the exact session root.

The custom authority now duplicates responsibility across every producer:
source-qualified resolvers, wrapper copies, contributor declarations,
rendezvous state, aggregate admission, cache/spill behavior, and an
aggregate-specific clean-room witness. Native runtime composition remains the
right long-term dynamic mechanism, but it is a separate host capability and
does not justify retaining this compatibility layer.

## Request

> Remove the custom context aggregation path in favor of the exact-session
> guidance model. Preserve native multi-hook `additionalContext` as the
> preferred future dynamic path once the runtime fix is available and proven.

## Plan

### Phase 1 — Land the retirement contract

- [ ] Revise the harness-guidance vision with an explicit boundary against a
  custom cross-plugin aggregation authority.
- [ ] Record the retirement scope, native-composition future seam, ordering,
  and validation plan in this effort.
- [ ] Land the vision and effort through review before removing code.

### Phase 2 — Remove the custom authority

- [ ] Remove the `context-injection` plugin, marketplace entry, adoption
  config, aggregate-specific pattern/reality claims, and clean-room scenario.
- [ ] Remove aggregate-only producer hooks, wrappers, resolvers, and
  declarations while retaining checked-in pointers, exact-session guidance
  writers, and the underlying emitters those writers call.
- [ ] Update scanner and suite guards so a session-file-only stack is valid
  without an aggregate authority and ambiguous non-empty startup outputs still
  fail closed.
- [ ] Bump every changed plugin version and reconcile the marketplace/docs
  roster in the same change.

### Phase 3 — Validate and migrate adopters

- [ ] Run changed plugin suites, repository guards, install-contract checks,
  and static searches for active authority surfaces.
- [ ] Prove fresh/resume/ACP launch paths still deliver exact-session guidance
  with no stale session or CWD reuse.
- [ ] Publish migration guidance for adopters to remove the authority plugin
  and config without removing their checked-in pointers.
- [ ] Keep native multi-hook activation outside this effort until the supported
  runtime floor proves complete composition.

## Validation Plan

- [ ] No marketplace plugin, active config, hook, wrapper, resolver,
  rendezvous/cache/spill code, or aggregate-specific scenario remains.
- [ ] Every retained `sessionStart` hook emits one JSON object; the scanner
  reports no ambiguous non-empty startup output and requires no custom
  authority.
- [ ] Checked-in pointers and exact-session guidance writers remain
  deterministic, contained, resume-safe, and cross-platform.
- [ ] Changed plugin suites, repository consistency guards, lint, and install
  contract pass.
- [ ] Public docs identify native host composition as the only future
  multi-plugin dynamic preference and require version-floor proof before
  activation.

## Proposal

Use one current reliability path and one future convergence path:

1. **Current:** reviewed static pointer plus plugin-owned exact-session guidance
   file.
2. **Future:** direct plugin-owned `additionalContext`, composed by the native
   host only after supported-version proof.

There is no third, custom composition authority between plugins and the host.

## Journal

### 2026-09-07 — Kickoff

- Issue #2173 was claimed as the public retirement tracker.
- The removal is an explicit negative boundary, not an inference from silence:
  the harness must not depend on a custom cross-plugin aggregation authority.
- Native host composition remains the preferred future dynamic path after its
  supported-version validation gate.
