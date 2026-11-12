# agent-pull-requests - Vision

- **Subject:** The first-class pull-request plugin and independently operable PR service.
- **Scope:** leaf (child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-11-11
- **Reality docs:** [PR plugin](../../../plugins/agent-pull-requests/README.md)

## Purpose & Intent

Provide one coherent PR-focused client and service capability for an operator's
authorized repositories, independent of a local checkout or an interactive
agent session. Author-side operations, reviewer-side operations and durable PR
observation compose the established provider-neutral contract rather than
requiring each consumer to build another forge client or watcher.

The same service can operate natively or as a declared containerized role on
the operator's fleet. A centralized, primarily IO-bound watcher and subscription
registrar shares observation across clients without compromising independent
subscription lifetimes or delivery reliability.

## Concepts & Components

### PR plugin and thin clients

`agent-pull-requests` is the PR-domain surface. Worktree-aware callers retain
their source identity, publication obligations and repository policy while
composing this capability. Compatibility adapters share provider semantics;
they do not become competing PR implementations.

### Independently operable service

The service owns PR observation, durable registrations and delivery state.
It is installable, diagnosable and operable without Copilot, a marketplace
payload or a roaming client process. The plugin supplies commands, guidance and
connections rather than acting as its required supervisor.

### Fleet-managed placement

An adopted fleet-manager supplies declared placement, service discovery,
authorized routing and deployment lifecycle. The PR service supplies its own
supported role and stateful activation boundary. One operator owns the whole
fleet; sharing a watcher does not introduce another operator's state or
credentials into it.

### Two perspectives on one live conversation

Author and Reviewer are perspectives on the same PR, not independent stores or
forge clients. The PR is a collaborative conversation around a current code
candidate; provider-native facts, participant relationships and action-specific
authority remain explicit. Human participants and multiple reviewers remain
first-class even when two agents drive the main exchange.

## Features

### first-class-provider-neutral-pr-surface

PR operations and observation have a shared, conformance-tested domain contract
across supported providers and clients. Existing worktree PR workflows remain
usable without surrendering their policy and source-control obligations.

### centralized-watcher-and-registrar

Independent clients register durable transition criteria and deadlines while
sharing observation for an authorized target/account scope. Increasing
subscriber count does not require one polling or wait process per subscriber.
The registrar owns PR subscriptions, not a replacement task queue or fleet store.

### complete-author-publication-and-metadata

Authors create PRs and repair their metadata through the same shared domain
surface used for status, discussion and observation. Full descriptions,
requested expert reviewers, work-item relationships and provenance labels
retain their intended meaning and are verified against authoritative provider
state. Metadata repair does not require another source push or a duplicate PR,
and preserves unrelated metadata and other reviewers' state.

### delegated-source-with-preserved-custody

A source-owning worker and a publication-owning caller can cooperate without a
local target checkout at the caller. Their source identity, target repository,
validation evidence, repository policy and credential scopes remain explicit
and distinct. Worktrees retains source-control and resource obligations;
delegation neither waives publication checks nor turns a configuration export
into ownership or authority.

### caller-driven-backend-connections

Clients can ask the central service to connect to supported forge backends
under the operator's admitted accounts and credential providers. Connections
are shared and demand-driven; each new PR or repository does not require a
service restart or a separately maintained watcher. Backend references select
existing authority rather than granting new credentials or access.

### explicit-sets-and-selected-pr-populations

Subscriptions can describe explicit PR sets or bounded repository and
criteria-selected PR populations. The service maintains discovery and shared
observation as matching PRs enter, change or leave a selection. Independent
subscribers retain their own lifetime, baseline and durable delivery state,
even when their selected targets overlap.

### driver-aware-filters-and-owned-demand

Common selection concepts cover repositories, authors and groups, reviewer or
assignment relationships, age, native tags and draft/publication state.
Drivers preserve their native meanings and explicitly declare supported
selection semantics; incomplete identities or provider evidence remain
unknown, and unsupported requirements never silently broaden a population.

Reusable named filter definitions are distinct from active demand. Both
configured startup sponsors and dynamic callers activate selections through
the same subscription owner. Every interest has an accountable owner and
explicit lifetime; overlapping interests share acquisition without sharing
delivery positions. Losing one interest stops its unneeded acquisition, not
another consumer's watch or reconciliation of already delivered events.

### author-and-reviewer-service-consumers

When central observation is adopted, PR creation and tracking workflows enroll
their confirmed PR identities through the service. Reviewer task emitters
request subscriptions to the PR populations they need to consider rather than
building their own discovery and polling systems. Publication policy, PR
claims, reviewer eligibility and task lifecycle remain with their existing
owners.

### standalone-and-fleet-service

Native use and fleet-managed container deployment are supported realizations
of the same PR service, not independently evolving watch implementations.
Declared fleet machines can host the service without installing an interactive
Copilot harness, and clients need not host its daemon to use it.

### durable-transport-appropriate-delivery

Notifications survive disconnection, restart and supported upgrades. Consumers
can reconcile acceptance, resume pending delivery and acknowledge their own
event identity without confusing another registration. Remote delivery does
not presume that a client's local executable exists on a service machine.

### reviewer-owned-load-sync-and-submit

A reviewer obtains a coherent local PR representation, synchronizes meaningful
upstream changes, analyzes exact source and submits explicitly selected comments
and a named verdict through this domain surface. Provider details do not require
another agent to interpret evidence on the reviewer's behalf.

### scope-isolated-review-generations

Immutable review generations retain endpoint, repository, account and revision
identity, section completeness and observation intervals. Discussion-only changes
remain visible without a source push. New source evidence cannot leave an older
source attachment appearing current. Rebuildable observation caches and durable
submission intent/receipts have distinct recovery and retention semantics.

## Behaviors

### cli-through-one-service-contract

Agents use the PR CLI for both perspectives; its HTTP service contract supplies
the same current model, event cursor and action semantics. Local standalone
hosting and optional Gateway coordinator hosting are realizations of one core.
An optional web UX is a consumer, never the source of domain or merge authority.
Existing Worktrees PR verbs retire only after complete semantic equivalence,
supported compatibility and consumer migration; source publication and resource
obligations remain with their rightful owner.

### role-aware-fair-attention

Reviewer subscriptions identify actionable PRs for repository Maintainers,
path-scoped Codeowners and interested Subject Matter Experts. Initial review,
follow-up conversation, merge nudges, rebase needs, CI recovery and final-round
completion remain distinguishable. Confirmed feedback returns handled work to
the back of the line without swallowing newer events; priority and aging keep
other admitted PRs moving. Assignment lineage belongs to the task owner, and
an interest or cached recommendation never grants mutation authority.

Authored feedback should consume the shared
[compact comment-provenance contract](../../harness-guidance/README.md#compact-forge-comment-provenance).
Logical comment and intentional-write identity remain reconcilable through
the current native observation and receipt owners; a matching tag alone
cannot establish an echo or mark another participant's work handled.

### bounded-conversation-and-terminal-tombstones

Current commits, comments, per-file threads, CI gates, reviews and participants
have a normalized, provider-faithful representation. Push and polling reconcile
into the same bounded event model, with independent consumer cursors and
explicit gaps when retained history no longer covers a cursor.

The service is not a full diff or chat archive. At authoritative merge it
compacts the live PR into a tombstone: existence, original author, other
reviewers, our author/reviewer/merger relationships, final verdict and compact
round/count/timing statistics. Closed-unmerged and abandonment remain distinct
terminal outcomes. Body-bearing caches, events and duplicate payloads do not
silently become another archive. Minimal unresolved effect identity remains
reconcilable without indefinite conversation retention; reopening begins a new
live observation epoch rather than reviving old messages.

### optional-policy-gated-automation-and-statistics

Mechanical actions and auto-merge are explicit options subject to fresh
candidate, independent review, required checks, consent, holds and actual
repository authority. The reusable core reports round and throughput statistics
without preserving full conversations. Read-only shadowing can compare
attention and outcomes without triggering tasks or provider effects.

### three-provider-native-meaning

ADO, GitHub and Gitea are supported drivers of the same domain contract.
Native votes, reviews, thread anchors and CI/policy states retain their meaning;
unsupported or incomplete evidence is explicit rather than coerced into another
provider's passing result.

### one-operator-with-separated-scopes

All fleet-hosted PR state and credentials belong to the fleet's sole operator.
That operator's repository, provider-account and installation scopes remain
distinct; membership alone does not grant every PR capability. Another
operator's fleet retains its own state and credentials even if future explicit
federation permits collaboration.

### reliable-without-hidden-second-owners

Registration, replay, acknowledgement and cancellation preserve durable
identity and bounded deadlines. Ambiguous outcomes reconcile before fallback,
and the selected namespace has one active state writer. A healthy replacement
container is not evidence that writer authority transferred safely.

### efficient-and-fair-observation

Shared IO is bounded and fair. A slow forge or consumer does not stall
unrelated targets, and account isolation is not sacrificed to deduplicate
requests. Durable retry does not claim exactly-once external effects.

### explicit-backends-and-credential-authority

Clients select supported native or fleet access explicitly. An unavailable
fleet route does not silently use another account or local service. Credential
providers retain issuance, renewal and revocation authority; the PR service
does not turn its subscription store into a secret store.

The client choice of native or fleet access is distinct from the forge
backends to which a central service connects. A connection request or PR
selection cannot change that service's credential, account or repository
authority. Initial selection membership is distinguishable from subsequent
PR changes; an unchanged observation or incomplete discovery cannot fabricate
a change, a closure or a complete empty result.

### honest-observation-and-partial-effects

Local generation consistency does not imply an atomic forge snapshot. Missing
or partial evidence remains unknown rather than empty success. Review submission
refreshes action-specific checks, retains confirmed and unknown outcomes and
reconciles before retry; it never invents atomic multi-action or head-conditional
guarantees. Completion settings, other reviewers' state, caller policy and
independent finding checks retain their existing owners.

### one-supported-workflow-in-consumer-guidance

Generic guidance and repository-specific consumers direct author publication,
metadata repair, status and waiting through the supported PR-domain contract.
Compatibility helpers compose or forward that contract rather than requiring
agents to select another forge client. Repository-specific content supplies
policy and evidence, not a parallel publication or polling implementation.
Missing capabilities and unqualified installations remain explicit rather
than authorizing a raw-backend workaround.

Author mutations distinguish confirmed creation, persisted metadata and
unresolved effects. Recovery keeps the existing PR identity and reconciles
uncertainty before retrying; transport success alone is not proof of exact
description storage, complete work-item relations or an accepted publication.

## Non-Goals / Boundaries

- No multi-operator tenancy within a fleet, and no hosting another operator's
  state or credentials there. Federation between separately owned fleets is
  not authority to merge their stores, identities or credential custody.
- No competing fleet controller, release authority, credential issuer, task
  lifecycle or worktree publication authority.
- No arbitrary remote shell, callback-command or destination forwarding.
- No required fleet adoption for useful standalone PR commands and watches.
- No fixed host inventory, ports, wire schema or packaging recipe in this vision.
- No indefinite chat-message archive, historical patch archive, or persona
  requirement in the portable service core.

## See Also

- Parent: [agent-fabric](../../agent-fabric/README.md)
- PR capability contract:
  [worktree PR operations](../agent-worktrees/pull-requests/README.md)
- Consumers: [agent-dispatch](../agent-dispatch/README.md) and
  [reviewer loops](../agent-dispatch/reviewer/README.md)
- Fleet: [machine-fleet](../../machine-fleet/README.md)
- Lifecycle: [plugin-services](../../plugin-services/README.md)
- Reality: [PR plugin](../../../plugins/agent-pull-requests/README.md)
