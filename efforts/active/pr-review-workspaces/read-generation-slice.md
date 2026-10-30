# First Slice: Read Generations

Parent contract: [PR Review Workspaces](README.md).

This agent-recommended slice realizes the reviewed architecture incrementally.
It is a finite, source-only candidate, not service activation or review authority.

## Contract and admission

Implement a pure PR-domain generation builder and a strict offline JSON CLI in
`agent-pull-requests`. An existing authorized transport supplies an explicit,
versioned evidence envelope; the builder never acquires credentials, reaches the
forge, starts a watcher or submits a review. ADO is the first adapter. This is
the interim feed-update seam, not a second forge client or policy interpreter.

Scope requires provider, HTTPS endpoint, project/repository IDs, account/principal
and PR number. Every supplied section carries observation start/end, completeness
and errors. Preserve field presence, including completion settings, votes,
discussions/deletions and iteration anchoring; do not infer missing values.
Input provenance remains caller/transport responsibility: scoped bytes are not
proof of authorization or a fresh live read.

The immutable generation hashes normalized content, scope, revision and section
evidence. Sync compares a previous generation of exactly the same scope, reports
changed sections and invalidations, and never mutates upstream state. Head or
iteration changes invalidate source/anchors; reply/edit/deletion changes invalidate
discussion decisions without inventing a source push. A missing section is
unavailable, not an empty complete collection. Partial collections retain their
known records but cannot establish full review readiness.

## Bounded decisions

- **Storage:** no database or current-generation writer yet. The initial CLI
  emits one JSON value to stdout. Its caller owns any private materialization.
  Later service/cache publication reuses this pure representation.
- **Contract discovery:** explicit schema/version and capability result from the
  exact executing CLI. Existing commands remain unchanged. Unsupported input
  versions fail before output; additive provider fields survive in selected
  domain sections. No older installed runtime is assumed to support this seam.
- **Migration:** adapter normalization stays in the successor. Existing native
  snapshot/watch and provider credential ownership are untouched. A later source
  adapter invokes the existing owner through its supported contract.
- **Submission:** not implemented or advertised. No operation journal is added
  to rebuildable read-generation state. The architecture's submission recovery
  decision remains open for its own slice.

## Acceptance

- [ ] Initial ADO generation retains identity, revision and exact presence of
  completion/reviewer/discussion fields with synthetic fixtures.
- [ ] No-change, reply/edit/deletion, new head/iteration, target-only movement
  and partial/missing-section sync controls report honest changes/invalidation.
- [ ] Reject cross-scope previous generations, malformed evidence, unsupported
  versions and incomplete identity without interpreting eligibility.
- [ ] Exercise the actual source CLI parser/serialization with no installed
  runtime selection or global side effects; give reviewers exact source/ref
  commands and documented limits.
- [ ] Run focused tests, contained plugin validation and relevant source guards;
  publish through normal reviewed `dev` PRs.

## Remaining campaign

Authorized live acquisition, service-backed atomic generations/CAS, exact source
attachment, explicit submission and unknown-outcome recovery are separate
remaining slices in the parent effort. This first adapter alone does not satisfy
the real eligible ADO end-to-end validation item.
