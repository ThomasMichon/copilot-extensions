# Fresh Repository Consumption

- **Slug:** `fresh-repository-consumption`
- **Repo:** copilot-extensions
- **Branch(es):** one reviewed plan worktree followed by fresh serial
  implementation worktrees
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** installer `plugin-updating-and-alignment`; agent-worktrees
  ground-layer ownership of source-control mechanics
- **Umbrella issue:** #1533

## Guiding Intent

Make every newly created worktree and generated local marketplace binding
consume repository state that was freshly resolved for that operation. Safe
anchor advancement is an optimization and consistency aid; the authoritative
worktree base remains the fetched remote default-branch ref, and unsafe anchors
are preserved rather than rewritten.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| implementation host | Owns planning, code, validation, and PRs | isolated worktree |
| Copilot reviewer | Reviews public PRs and reports non-blocking findings | GitHub pull-request review |

## Coordination

- **Topology:** one host worktree with a reviewed plan followed by serial
  implementation.
- **Host (owns PRs):** implementation host.
- **Delegates:** read-only exploration and review only.
- **Handoff:** the effort README remains the durable checkpoint.
- **Shared surface:** Marketplace-Scoped Installations (#1096) owns installation
  identity and cell isolation; this effort changes only when a canonical
  registered source checkout is refreshed before an existing local override is
  materialized.

## Context

Worktree creation fetches the source repository and prefers the fetched remote
default-branch ref, but it does not opportunistically fast-forward a safe anchor.
Paired repositories repeat that behavior independently. Local marketplace
overrides resolve registered checkouts into directory sources without first
establishing a bounded freshness point for those repositories.

Consequently, consumers that still read an anchor directly can observe older
configuration than a newly created worktree, and a generated local marketplace
binding can serve plugin content from a stale checkout. The update command
already carries a conservative anchor fast-forward contract; creation and
binding need a reusable, narrower preparation primitive with the same safety
properties.

## Request

Before fresh worktree creation or local registered-repository marketplace
binding, fetch the relevant repository, safely fast-forward a clean
default-branch anchor when possible, and otherwise preserve the anchor while
using the fetched remote ref as the worktree base. Apply the same behavior to
paired repositories, bound marketplace sources, and failure reporting without
adding destructive source-control behavior.

## Plan

### Phase 1 - Define the freshness contract
- [x] Compose the existing fetch, remote-default resolution, and
  `fast_forward_worktree` safety implementation behind a reusable
  repository-preparation result; do not create a second fast-forward policy.
- [x] Report fetch, remote-base resolution, safe anchor advancement, opt-out,
  lock contention, and other degraded conditions structurally.
- [x] Preserve dirty, detached, non-default, ahead, and divergent anchors
  byte-for-byte.
- [x] Keep remote-ref selection independent from whether the anchor can advance.
- [x] Honor the existing `auto_fast_forward` policy for
  anchor movement; fetching a fresh creation base remains independent.

### Phase 2 - Apply freshness to worktree creation
- [x] Use the shared preparation path for ordinary worktree creation.
- [x] Apply the same path independently to paired repositories, including
  anchor-class knowledge repositories.
- [x] Keep fetch failure non-fatal: create from the last-known remote, local
  default, or `HEAD` fallback while reporting that freshness was degraded.
- [x] Preserve current pair semantics: a paired-repository preparation or carve
  failure leaves the primary worktree usable and explicitly unpaired rather
  than rolling it back.
- [x] Treat concurrent Git lock contention as a reported, non-fatal inability
  to advance the anchor; never race with a reset or forced ref update.

### Phase 3 - Apply freshness to local marketplace binding
- [x] Resolve a directory override back to its canonical registered repository
  before attempting refresh.
- [x] Refresh each relevant repository at most once per reconciliation
  transaction.
- [x] Keep the bounded session-start hook refresh-free; explicit create, adopt,
  and manual reconciliation use the bounded fetch timeout.
- [x] Keep fetch failure non-fatal for binding: materialize the safe existing
  checkout with an explicit degraded diagnostic rather than silently claiming
  freshness.
- [x] Preserve user-owned overrides and every existing path/manifest safety
  boundary.

### Phase 4 - Publish
- [x] Update behavior documentation and remediation text.
- [x] Bump agent-worktrees consistently and pass focused, changed-plugin,
  version, generated-payload, and install-contract gates.
- [ ] Publish, review, self-merge, deploy, and verify installed behavior.

## Validation Plan

- [x] A stale clean default-branch anchor fast-forwards after a successful
  fetch.
- [x] Dirty, detached, non-default, ahead, and divergent anchors remain
  unchanged.
- [x] New worktrees use the fetched remote default-branch ref even when the
  anchor cannot advance.
- [x] Offline creation succeeds from the last-known safe fallback and reports
  degraded freshness.
- [x] Ordinary and paired worktree creation share the same behavior.
- [x] A paired-repository failure leaves the primary worktree intact and
  explicitly unpaired.
- [x] A registered local marketplace source is refreshed once before binding.
- [x] Offline local binding proceeds from the existing safe checkout and
  reports degraded freshness.
- [x] Disabling automatic fast-forward preserves the anchor while still
  allowing fresh remote-ref selection for worktree creation.
- [ ] Concurrent creation/update attempts cannot corrupt or forcibly rewrite
  the anchor; lock contention degrades safely.
- [x] Fetch, authentication, missing-remote, and missing-default-ref failures
  produce honest degraded results without destructive fallback.
- [x] Existing marketplace ownership, ignore, path-containment, manifest-name,
  and user-conflict tests remain green.

## Proposal

Introduce one source-control preparation primitive below creation and binding.
It fetches, resolves the freshest safe base, opportunistically fast-forwards
only an eligible anchor through the existing fast-forward implementation, and
returns structured diagnostics. Creation and binding remain offline-tolerant:
they use safe last-known fallbacks and expose degraded freshness, while current
paired-worktree and marketplace reconciliation boundaries remain intact.

## Journal

### 2026-08-31 - Kickoff
- Filed #1533 and mapped ordinary creation, paired creation, anchor-update, and
  local marketplace override paths.
- Reconciled the change as a reliability extension of installer
  `plugin-updating-and-alignment` and agent-worktrees' ownership of
  source-control mechanics.

### 2026-08-31 - Freshness paths implemented
- Added one bounded source-preparation primitive that fetches, selects the best
  start ref, and reuses the existing safe fast-forward implementation.
- Ordinary and paired creation now opportunistically advance eligible anchors
  while always preferring the fetched remote ref for new worktrees.
- Explicit create, adopt, and manual marketplace reconciliation refresh
  registered local sources. The bounded session-start hook remains
  network-free.
- Focused creation, pairing, marketplace, anchor-sync, offline, launch-preflight,
  lint, version, generated-payload, and install-contract checks pass. The broad
  Windows suite reaches an unrelated pre-existing WSL Bash probe failure; a
  filtered monolithic run exceeds the runner's five-minute single-subsuite
  budget.
