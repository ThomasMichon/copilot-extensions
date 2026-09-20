# agent-worktrees pull-request capability — Vision

- **Subject:** The **complete, provider-neutral pull-request capability**
  agent-worktrees exposes — one surface for author-side and reviewer-side PR
  operations against any registered repo, addressable by any authorized
  caller regardless of local checkout, and verifiable through a shared
  conformance contract that lets a fabricated provider stand in for a real
  one with confidence.
- **Scope:** leaf (child of the
  [agent-worktrees](../README.md) plugin vision)
- **Status:** Draft
- **Last revised:** 2026-09-20
- **Reality docs:** the agent-worktrees plugin `docs/`; `providers/base.py`
  (the `PRProvider` protocol and provider registry); `tests/test_pr_*.py`,
  `tests/test_providers.py` (the existing pr-command test suite)
- **Supersedes / superseded by:** none

## Purpose & Intent

A registered repo's pull-request behavior — opening, reading, reviewing,
commenting on, and landing a change — should be reachable through **one
coherent, provider-neutral surface**, the same way agent-worktrees already
owns repo identity and contribution posture. Today that surface covers the
**author's** side of a PR's life (open, watch, signal merge consent, check
status, complete). A reviewer, a scenario-eval, or any other consumer that
needs to read a PR's diff/comments/threads or publish comments and verdicts
has no equivalent first-class primitive, and either goes without or embeds
its own forge-specific client — exactly the duplication agent-worktrees
exists to prevent elsewhere.

This capability's north star is **symmetry and reach**: whatever an author
can do to a PR through agent-worktrees, a reviewer should be able to observe
and respond to the same way; whatever agent-worktrees can do for a repo it
has locally checked out, an authorized caller should be able to do for a
repo it hasn't — addressing it by name and letting agent-worktrees resolve
that repo's own registered provider and policy, exactly as it already
resolves identity and contribution posture for a repo that isn't the
caller's own worktree. And because a live forge is not always available or
desirable to exercise, this capability should be **verifiable without one**:
a fabricated provider that honors the same behavioral contract as a real
one, so anything that composes the PR capability — a reviewer loop, a
clean-room scenario-eval, a dry run — can be proven against it with the same
confidence as the real thing.

## Concepts & Components

### The PR capability, as one provider-neutral surface

A `PRProvider` implementation is the unit of forge-specific integration;
everything above it — repo resolution, policy, command surface, consumer
code — is provider-agnostic. A capability this vision adds (reviewer
operations, foreign-repo addressing, a mock provider) extends the shape of
that one surface; it never becomes a second, parallel PR mechanism a
consumer has to choose between.

### Author-side and reviewer-side operations, symmetric

Author-side operations (open, watch, signal merge consent, check status,
complete, request ready) and reviewer-side operations (read the current
diff and surrounding context, read and post comments, read and resolve
threads, publish a verdict) are two faces of the same capability, not two
separate systems. A reviewer flow composes this capability exactly as an
author flow does today — it never re-implements forge commands, response
parsing, or identity resolution on its own.

### Repo addressing independent of local checkout

An authorized caller acts on a named registered repo's PR surface whether or
not that repo is checked out where the caller runs. The caller's own
worktree/anchor is never conflated with the target repo's identity: acting
"on behalf of" a foreign repo resolves that repo's own registered provider,
policy, and contribution posture from the registry, the same way
agent-worktrees already resolves a related repo's identity and posture for
purposes other than editing it locally.

### Provider conformance, real and fabricated alike

The existing pr-command test suite already exercises real provider behavior
in detail. That same suite is the natural, authoritative conformance
contract a provider must satisfy — including one built to fabricate
plausible PR state rather than call a live forge. A fabricated provider is
trustworthy exactly to the extent it is held to, and passes, the same
behavioral contract every real provider is held to; conformance is proven,
not asserted.

## Features

### reviewer-capable-provider

The PR capability supports reading a PR's current diff and surrounding code
context, reading and posting comments, reading and resolving review
threads, and publishing a verdict — as first-class provider operations, not
something a reviewer flow builds beside the capability.

### foreign-repo-pr-operations

An authorized caller can direct a PR operation at a named registered repo it
has no local checkout of. Resolution finds that repo's own registered
provider and policy; the caller's own worktree identity is never used as a
stand-in for the target's.

### conformance-verified-mock-provider

A fabricated provider exists that satisfies the same behavioral contract
(the existing pr-command test suite, extended as needed) every real provider
is held to, so a consumer can exercise realistic PR-shaped interaction —
diff review, commenting, labeling, merging — without a live forge, sanctioned
credentials, or a real PR.

### provenance-attribution-without-identifier-leakage

Opening a PR through this capability may attribute it to the originating
worktree/session, but doing so must never be the mechanism by which a
private identifier (a machine name, a raw worktree id, a session id) reaches
a public audience by default. The capability's default attribution posture
is **informationless to an outside reader** — provenance is still
traceable to an authorized operator through the capability's own records,
but a public PR body carries, at most, an opaque, non-identifying handle
rather than a raw identifier. A repo may opt in to the fuller, raw form of
attribution explicitly, and may opt out of attribution entirely — both are
deliberate, explicit configuration choices, never the unconfigured default.

## Behaviors

### one-surface-no-forking

A reviewer-side operation, a foreign-repo operation, and a mock-provider
operation all resolve through the same repo -> provider -> operation path an
author-side, local-checkout, real-provider operation already uses. None of
the three additions above introduces a second code path a consumer must
choose between.

### foreign-target-resolves-honestly

When a named target repo is not registered, or its registered provider
cannot be reached, the operation reports that plainly rather than silently
falling back to the caller's own repo/provider or guessing at the target's
identity.

### mock-fidelity-is-provable-not-assumed

Confidence that the mock provider behaves like a real one comes from it
passing the same conformance contract real providers pass — never from
manual inspection or "it looks plausible."

### mock-is-explicitly-disclosed

A consumer operating against the mock provider is told so explicitly, up
front, rather than left to infer it — consistent with the harness-side
"stay on the rails" discipline for any disclosed simulation: authoritative
for the run, not a puzzle to probe for edges.

### unconfigured-attribution-never-leaks

A repo that has not explicitly configured its attribution posture receives
the safe, non-identifying default described above — an operator who never
touches this setting never accidentally exposes a private identifier merely
by omission. A capability that persists provenance across a config change
(so an operator can later tighten or loosen a repo's policy without
retroactively exposing or hiding a marker that was already published under
a prior policy) is expected of any such tracking, not merely a suggestion.

## Non-Goals / Boundaries

- **Not a forge UI, notification system, or webhook receiver.** This
  capability is a command/query surface, not an interactive review
  experience or an event pipeline.
- **Not a replacement for a repository's own review policy or rubric.**
  Provider-neutral operations execute policy; they do not define what a
  repository requires to approve or land a change.
- **Not a packaging decision.** Whether this capability remains part of
  agent-worktrees or is later split into its own dedicated plugin is a
  future wiring/packaging question outside this vision's scope; this vision
  describes the capability's shape independent of which module hosts it.
- **Not a specification.** This vision fixes the capability's shape and
  guarantees, not the provider protocol's exact method signatures, the mock
  provider's storage format, or a CLI flag's exact name.

## See Also

- Parent vision: [agent-worktrees](../README.md)
- Composing sibling:
  [agent-dispatch/reviewer](../../agent-dispatch/reviewer/README.md) — the
  cooperative reviewer loop this capability is meant to compose rather than
  have re-implemented beside it.
- Reality docs: the agent-worktrees plugin `docs/`; `providers/base.py`;
  `tests/test_pr_*.py`, `tests/test_providers.py`

## Provenance

- **2026-09-20** — Round-23 review of the `codename-attribution-by-default`
  effort (#2977) found this vision silent on the `source_attribution`/
  codename PR-marker mechanism `pr-attribution-codenames` (Done) already
  built and this effort further deepens — a genuine blind spot, not a
  vision-ahead gap. Folded back the mechanism's should-be intent (a Feature
  and a Behavior) at the detail ceiling: the public-safety
  informationless-by-default guarantee and its persistence-across-config-
  change expectation, without pinning the `source_attribution`/
  `codename_source` field names or exact config keys, which remain
  reality-doc/effort-level detail.
- **2026-09-14** — Authored from an odsp-web-harness clean-room session's
  real finding: a `code-review` scenario-eval run correctly reported a
  `noop`/BLOCKED verdict for "no PR available" rather than fabricate a
  review, which surfaced three related gaps the operator named directly: no
  mock PR provider to exercise such a flow without a real PR; the reviewer
  side of PR interaction (diffs, comments, threads, verdicts) has no
  first-class primitive the way the author side does; and PR operations
  require a local checkout of the target repo, which breaks for a repo like
  odsp-web that is worked on without one. Filed the mock-provider piece
  upstream as
  [#2691](https://github.com/ThomasMichon/copilot-extensions/issues/2691)
  before this vision existed; this vision generalizes that single issue into
  the fuller should-be shape all three gaps point at.
