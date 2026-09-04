# Evidence-Calibrated Model Routing Design

Back to the [effort README](README.md).

## Ownership

`delegation-guidance` remains the single owner of coordinator routing strategy:

- classify direct versus delegated work;
- classify the delegated purpose and required capability;
- resolve a model from reviewed eligibility data;
- define the bounded delegate contract;
- retain coordinator integration and synthesis.

It does not become a task queue, container runtime, billing ledger, or model
host. Existing owners remain authoritative:

- `context-injection`: attributable context composition, progressive
  disclosure, recovery, and fail-open startup delivery;
- `config-migrate`: versioned inert configuration migration;
- agent-dispatch: durable task/session lifecycle and outcome provenance;
- agent-containers: restricted execution and credential boundaries;
- external accounting/outcome systems: billing aggregation, longitudinal
  analysis, and promotion evidence reports.

## Registry contract

The portable strategy names purposes and capability requirements, not current
models. Layered configuration supplies current eligibility.

Minimum entry semantics:

| Field | Meaning |
|-------|---------|
| Purpose | Stable role/task class such as evidence, coding, testing, review, or domain-tool operation |
| Model | Provider model identifier supplied as inert data |
| State | Demonstrated, candidate, held, or failed |
| Surfaces | Launch/execution surfaces for which evidence applies |
| Requirements | Tool, context, reasoning, authority, language, or task-size constraints |
| Cost rank | Comparable preference within otherwise-qualified choices; never a correctness override |
| Fallback | Ordered demonstrated alternatives |
| Escalation | Named ambiguity, validation, conflict, or review conditions |
| Evidence | Dated outcome references and applicability bounds |
| Recheck | Model/runtime change or date that invalidates stale evidence |

The shipped plugin carries a schema and obvious examples, not real preferred
models. Repository and operator layers can name current models; the strategy
and safety boundaries stay plugin-owned.

## Selection sequence

1. Decide whether the work should remain direct or be delegated.
2. Classify the delegated purpose, separability, context volume, coupling,
   tools, execution surface, and failure impact.
3. Resolve layered registry data and current availability.
4. Filter to choices whose evidence state and applicability satisfy the
   assignment.
5. Prefer the least-expensive demonstrated eligible choice.
6. If unavailable, fall through only to another demonstrated eligible choice.
7. Return a candidate only when an explicit trial is armed and containment
   matches the requested authority.
8. Otherwise hold, escalate to a stronger demonstrated choice, retain the work
   with the coordinator, or report that no eligible route exists.
9. Emit the routing reason and bounded worker contract.

The resolver is pure and deterministic for one normalized input snapshot.

## Context delivery

The first-turn kernel adds only a compact decision cue:

> Before delegating, load the model-routing grounding and select from
> demonstrated choices for the classified purpose and execution surface.

The detailed strategy and resolved registry remain on demand through the
delegation skill. This is the minimum implementation compatible with the
current context-injection contributor contract, which does not distinguish
Task-capable coordinators from non-delegating workers.

Behavioral evidence, not architectural preference, decides whether a future
audience-gating capability is warranted. A missing registry falls back to
model-neutral delegation guidance without blocking startup; it does not turn a
candidate into a demonstrated choice.

## Outcome provenance boundary

This repository should emit the minimum identity needed for an external ledger:

- task/purpose and pair/assignment identity;
- coordinator and worker model/configuration;
- eligibility state and selection reason;
- execution surface and containment profile;
- parent/worker session relationship;
- pending and terminal disposition;
- accepted, repaired, retried, abandoned, rejected, or superseded result.

The record excludes raw prompts, source bodies, credentials, and private
repository context. Provider billing events remain authoritative for money.
External consumers join each unique event to one outcome and actor role; this
repository does not assign a second monetary value to context transfer or
duplicate parent/child totals.

Agent-dispatch is the single writer and storage owner. The assignment identity
is the existing spawn reservation key
`dispatch-task:<task_id>:<attempt>`. Agent-worktrees remains authoritative for
worktree/session/profile facts and agent-containers remains authoritative for
effective containment; the dispatch record references those facts without
copying their stores.

The record uses literal configured model identifiers. Provider billing-event
references are optional, opaque, and unique; no monetary amount is stored.
Repair work is a separate linked assignment rather than a rewritten terminal
outcome.

## Trial admission

A writable candidate trial is allowed only when a runtime launcher can verify:

- explicit trial arming;
- candidate model and purpose match;
- approved authority level and prior decision;
- dedicated worktree identity;
- required restricted-container profile;
- path, tool, network, and credential boundaries;
- no merge, deployment, administrative, or unrelated repository authority.

Guidance may explain these conditions, but enforcement belongs at the launch
surface. Restricted containers and credential relay boundaries should be reused
rather than recreated in a payload-only plugin.

A trusted demonstrated coordinator may arm a candidate worker trial when the
reviewed trial policy permits that purpose and authority. It cannot arm itself
as a candidate coordinator or promote either participant; admission and
promotion remain separate decisions.

## Independent promotion

Trial completion does not automatically change the registry. Durable evidence
feeds a separate reviewed configuration change. The evaluated coordinator,
worker, pair, and PR author may provide evidence but cannot solely author and
approve their own promotion.

A promotion remains scoped to the demonstrated purpose, execution surface,
context/reasoning configuration, and known constraints. Provider or runtime
changes can return an entry to candidate or held state.

## Failure behavior

- Contributor/configuration failure: fail open for session startup with an
  attributable diagnostic and model-neutral delegation fallback.
- Eligibility uncertainty: fail closed; do not silently treat a candidate as
  demonstrated.
- Preferred-model unavailability: fall through to another demonstrated choice
  with a recorded reason.
- No demonstrated choice: explicit trial, stronger-model escalation,
  coordinator retention, or a clear no-route result.
- Worker failure: preserve the terminal outcome and reason; do not hide it by
  launching unbounded replacements.
- Product-gate failure: the pair fails regardless of nominal cost.

## Initial PR boundaries

### PR 1 - Registry and grounding

- versioned schema and inert loader;
- deterministic layer merge;
- no real model defaults;
- on-demand skill guidance;
- one compact kernel cue;
- malformed-config fail-open tests;
- Bash/PowerShell parity and byte-budget tests.

### PR 2 - Pure selection helper

- normalized classification input;
- ordered demonstrated choices;
- availability fallback;
- explicit candidate trials;
- reasoned no-route/hold/escalation results.

### PR 3 - Provenance

- settle one-writer/storage ownership first;
- minimal assignment and terminal outcome record;
- task/session relationship and privacy tests.

### PR 4 - Restricted trial admission

- explicit arming and prior-decision checks;
- worktree/container/path/tool/network/credential boundaries;
- denial-path tests before a real candidate receives write authority.

### PR 5 - Adoption and promotion

- public configuration examples;
- normal install/update proof;
- behavioral trials;
- separately reviewed promotion workflow and final evidence.

## Non-goals

- No globally preferred model.
- No model IDs in the vision, strategy prose, or hardcoded defaults.
- No learned task classifier in the initial implementation.
- No new service or UI solely for routing.
- No in-plugin billing, quota, dashboard, or longitudinal analytics store.
- No audience-gating rewrite in context-injection without behavioral evidence.
- No self-promotion or automatic candidate promotion.
- No weaker product, review, security, publication, or deployment gate.
- No broad exhaustive test matrix on ordinary pull requests.
