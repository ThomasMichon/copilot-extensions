# Artifact Publication Boundary Hardening

- **Slug:** `artifact-publication-boundary-hardening`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal, implementation and completion PRs against `dev`.
- **Created:** 2026-10-13
- **Status:** Draft
- **Vision:** Closes the existing verified-delivery and rollback-safety intent of `visions/installer/README.md`; preserves the governed artifact contract of `efforts/active/governed-python-artifact-promotion/README.md`.
- **Umbrella issue:** #6639
- **Sub-issues:** Primary repair dependencies #6535 and #6632 retain their existing owner.

## Guiding Intent

Make the artifact publisher's real execution boundary a tested contract:
candidate-tree validation must not depend on the publisher's older checkout,
partial publication must be safely resumable, and promoted source must never
masquerade as published artifacts or installed runtime.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Hardening driver | Reviewed proposal, remaining hardening delta and completion evidence | Issue #6639 and its PRs |
| Primary publisher owner | Existing repair and hosted-publication dependency, not reassigned here | Issues #6535 and #6632 |

## Coordination

- **Topology:** Sequential independent PRs; no competing publisher implementation.
- **Host (owns PRs):** Hardening driver, for this follow-up only.
- **Delegates:** None.
- **Handoff:** Resume this README and #6639; preserve the primary publisher's custody.
- **Entry gate:** Review and merge this proposal first. Start implementation only
  after the primary repairs are present on `main` and their owner has recorded
  successful hosted attested artifact publication. A merged repair PR or source
  promotion alone does not satisfy this gate.

## Context

Run `38508930963` passed full validation and promoted source through release
PR #6631, then failed in the separate artifact-publication job. The shared
`_tree_excluding_state` helper loaded the merged candidate into a temporary
index, but ordinary cached removal applied checkout/index safety comparisons
against the publisher's older checkout.

Merged repair #6633 retains exact bookkeeping exclusions and confines removal
to the throwaway index. Its real-Git regressions cover state, nested coverage
pointers, both together, and absent bookkeeping while preserving unrelated
staged work and a similarly named sibling directory. This effort verifies and
builds on that repair rather than reimplementing it.

Coverage collection is not this effort's subject. Coverage pointers are one
bookkeeping input to the shared tree filter. The established promotion,
attestation, publication and consumer-admission mechanisms remain the owners of
their respective phases; no parallel scheduler or publisher is proposed.

## Request

> Is this part of the coverage baseline piece? What should be done to harden it?

> Great. Make a mini-effort and drive those improvements once the main fixes land

The approved follow-up is the four recommendations from that discussion:
checkout-independent exact-path filtering; real publication-boundary coverage;
hosted publication and safe partial retries; and distinct promoted, published
and installed completion evidence. The concrete tests and implementation seams
below are agent-recommended means of realizing that requested scope.

## Plan

### Phase 1 - Review the follow-up and admit the repair baseline
- [ ] Merge the proposal through the normal review and check gates.
- [ ] Verify the primary repairs are on `main`, successful hosted artifact
      publication is recorded, and the primary owner has no overlapping
      remaining implementation assigned to this follow-up.
- [ ] Inventory the existing tree-filter, publisher and workflow tests; mark
      already-satisfied recommendations as verified rather than duplicate them.
      _(agent-recommended)_

### Phase 2 - Close the remaining publication-boundary gaps
- [ ] Preserve or extend real-Git fixtures for newer merged candidates against
      older publisher checkouts, exact exclusions, unrelated staged work and
      unchanged real index/working files. _(agent-recommended)_
- [ ] Add only missing interrupted-publication/retry cases: completed identical
      assets are reused, mismatched existing bytes or tag bindings refuse, and
      unavailable verification never becomes success. Retain all source, hash,
      attestation and immutable-publication checks. _(agent-recommended)_
- [ ] Verify observable outcomes distinguish source promotion, complete
      publication and installation; tighten only the existing reporting or
      documentation seams that fail that contract. Installation itself remains
      consumer-owned and is not claimed by this effort. _(agent-recommended)_
- [ ] Land the coherent remaining delta through normal review and CI, with the
      appropriate release metadata if a plugin is changed.

### Phase 3 - Prove delivery and close
- [ ] Verify a containing hosted promotion and actual attested asset publication,
      including authoritative inventory/download evidence rather than only a
      green build, merged candidate or created tag.
- [ ] Record the evidence and any named transferred objective, resolve every
      Plan and Validation Plan item, then archive through review.

## Validation Plan

- [ ] Real-Git boundary tests preserve real index bytes, working files,
      unrelated staged changes and sibling paths for all bookkeeping cases.
      _(agent-recommended)_
- [ ] Publisher regression tests exercise interruption followed by an
      idempotent retry, identical-asset reuse and explicit mismatch/refusal.
      _(agent-recommended)_
- [ ] A post-merge publication failure is reported as publication incomplete,
      never as a fully published or installed release. _(agent-recommended)_
- [ ] Existing artifact/source/hash/signature/tag-binding refusal tests remain
      green; no replacement upload or verification waiver is introduced.
      _(agent-recommended)_
- [ ] The affected promotion/artifact/control test family passes through the
      bounded runner, with lint and documentation-impact review completed.
      _(agent-recommended)_
- [ ] Hosted evidence proves the final published asset set and its binding to
      the actual merged candidate; consumer installation remains explicitly
      unclaimed.

## Proposal

Use the existing release tooling and its tests, not a new publishing layer.
First derive the smallest remaining delta from the admitted repair baseline.
Keep exact tree exclusions and credentials-free local fixtures. Hosted evidence
is gathered from the ordinary trusted release lane without repeated dispatch,
asset replacement, direct `main` patches or bypassing the existing publisher.

This is a narrow realization of the established artifact-delivery contract,
not an extension of installer architecture or coverage-selection policy. It
follows the existing patterns' fail-loud principle and scoped ownership rules.

## Journal

### 2026-10-13 - Requested follow-up
- The operator confirmed the slug and requested execution after the primary
  repairs land.
- Confirmed target effort adoption and searched existing tracking before
  opening the bounded follow-up #6639 under the established artifact campaign.
- Preserved #6535/#6632 primary repair custody. Repair #6633 is merged to `dev`;
  successful containing hosted publication remains an entry gate, not a claim.
- Captured the operator's request verbatim and separated recommended execution
  mechanics from the requested outcome.
