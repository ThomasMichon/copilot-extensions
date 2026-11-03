# Coverage-auditability selection amendment

Parent: [Coverage-Guided CI](README.md). Coordination: #4453; implementation
re-scopes existing draft #6783 after this plan-only amendment merges.

Everything below is agent-recommended decomposition of the operator's
verbatim follow-up and selected scope in the parent Request.

## Objective and boundaries

Diff-select only tests that can be completely audited through ordinary,
trustworthy coverage attribution. Never coverage-exclude an unauditable or
unclassified test from its already-eligible lane. Reduce that always-run
footprint through testability improvements, not universal collection,
relabeling, removed assertions or expanded required CI.

Required PR and promotion remain unit-only. Native, integration and manual
packets retain their current platforms, admission and cadence: "always" means
coverage cannot exclude a test once its lane admits it, not that every lane
runs on every PR. Existing guards, changed-test inclusion, diff/debt fallback,
dependency detectors, containment and failure propagation remain intact.

## Preserved research and sequencing

The prior #6783 native prototype is retained at commit
`3a7bf8aa4bd1b3542fe2f99916d82a886f38ef39` through a dedicated recovery ref.
Its deferred enrollment/qualification is tracked in #10209 under #4453.
It remains unqualified research. This amendment neither ships it behind a
flag nor claims its original native coverage or timing requirements passed.
Any future use owes its own reviewed scope and original qualification.
Independent Windows-wave, collector/publication and shadow-consumer owners
retain their obligations; this bounded change does not complete the umbrella.

- [x] Merge this plan-only amendment before implementation. #10194.
- [x] Before re-scoping #6783, move its still-unearned native baseline
      enrollment/qualification to a tracked successor under #4453. Update
      active references in the parent Phase 5 and `dispatch-pr-selection.md`
      to that successor; retain the original draft and prototype as historical
      provenance, not completion evidence. The successor owns deferred research,
      not permission to resume universal collection or alter required CI.
      Retained in #10209; active references now point there.
- [ ] Base the owned #6783 branch on reviewed current `dev`, preserving the
      exact prototype first. Replace that PR's scope and description through
      the managed rewrite flow, retaining its discussion and identity.
- [ ] Implement and qualify the bounded selection contract below.
- [ ] Merge/finalize #6783 and journal this slice's evidence and remaining
      umbrella obligations; no installed runtime update or rollout.

## Implementation plan

- [ ] Add auditability classification to the existing portfolio metadata
      owner (`tools/pytest_portfolio_guard.py`), separately from tier/effect
      and contract tags. Unknown or malformed classification cannot grant
      diff-exclusion permission.
- [ ] Define auditable admission against complete relevant execution and
      dependency attribution for the actual profile. Cover source plus
      configuration/data dependencies; preserve conservative invalidation
      when coverage cannot attribute them. A coverage hit, percentage or
      author-supplied tag alone is insufficient.
- [ ] Carry classification and profile-correct proof through the existing
      baseline/selection pipeline rather than add a collector, daemon or
      independent coverage store. Legacy or incomplete evidence remains
      usable only where it supports conservative execution, not exclusions.
- [ ] Obtain the current eligible test inventory independently of baseline
      membership. Compose the actual run set from eligible unauditable and
      unknown tests, implicated proven-auditable tests, and existing required
      guards/changed tests/fallback additions. Missing baseline entries or
      inventory drift cannot drop tests. Never budget-prune the mandatory union.
- [ ] Wire the existing selected-test executor and actual CI consumer, inspect
      shadow versus active modes, and report why each retained/selected test
      ran. Preserve existing enrollment and lane gates; no new broad native
      or integration release prerequisites.
- [ ] Initially classify only bounded families whose completeness can be
      demonstrated. Report remaining unknown/unauditable populations honestly.
      Further testability improvements are separately scoped work, not a
      prerequisite to aggressively classify every configuration in this PR.

## Validation Plan

- [ ] Metadata controls reject conflicting/invalid declarations and keep
      missing declarations conservative; tier/effect/platform admission
      remains independent of auditability.
- [ ] Hand-computed mixed inventories prove unaffected proven-auditable tests
      can be omitted while every eligible unauditable/unknown test runs,
      including tests with no baseline entry.
- [ ] Missing/partial/legacy coverage, mismatched profiles, missing dependencies,
      changed configuration/data/tests, deleted/added/renamed test inventory,
      and debt/remapping uncertainty preserve conservative run sets and
      observable reasons. Existing guards and fallback cases still pass.
- [ ] Required-unit selection never imports excluded native, integration,
      platform-inapplicable or manual-only nodes from another profile.
      Mandatory retained tests cannot disappear because a budget is exhausted.
- [ ] Exercise the actual inventory/metadata-to-selection-to-execution command
      in an existing hosted lane, with exact expected node IDs and counts.
      Inject a regression caught by a retained test and prove the selected
      command fails, rather than trusting a pure selector result.
- [ ] Run focused unit/lint/contract gates and current-head required hosted CI;
      record the exact source and evidence. Avoid a heavy local test run on
      a contended host. This is not native timing or installed-rollout proof.
- [ ] Record validation tiers and reasons for skipped tiers in the
      implementation PR. Fresh-environment checks cover metadata/baseline
      first-touch compatibility where changed; native venue qualification is
      not required for behavior this bounded change does not alter.
- [ ] Clear current-head review and mergeability gates, verify real merge and
      managed finalization, then settle only this slice in the parent Journal.
