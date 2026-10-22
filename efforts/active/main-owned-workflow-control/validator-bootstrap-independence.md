# Validator bootstrap independence

Supporting proposal for [Main-Owned Workflow Control](README.md), tracked by
#6865. Related: the [materialized candidate gate](materialized-candidate-validation.md),
#6848's original outage, and #6849's merged attribution repair.

**Status:** Complete for the bounded bootstrap-independence objective under the
reviewed [unit-only release contract](../dev-branch-release-pipeline/promotion-burndown.md).
The original generated/native candidate-execution strategy below is historical;
#7255 explicitly superseded it as a promotion requirement. Client adoption and
periodic integration remain with #3336/#6237 and their existing owners.

## Request

Operator request, verbatim:

> We will need a durable fx for this to avoid this catch-22 in the futre

The operator separately authorized the narrow reviewed main bootstrap after
#6849 clears review. That is emergency recovery, not permission to weaken
dependency admission, bypass unrelated checks, or call this proposal complete.

## Failure and required boundary

Promotion run `38722957706` validated the development snapshot, then checked out
trusted main `8abb234719a922e5a8e280f2bb0b6864adfae474` to provision its
metadata validator. The seasoning CLI eagerly derived the entire product
library graph, whose materialized attribution error prevented even that
hash-pinned, registry-only tool installation. The reviewed development repair
could not reach main through the failing main helper.

Trusted control must bootstrap its own minimal toolchain without interpreting
unrelated product graphs. Product-source installs must keep their existing
attribution and recursive validation. Both paths retain the same canonical
resolver, seven-day/configured cutoff, selected index, hashes, retained-input
comparison and source-refusal rules.

## Original narrow implementation proposal

The following agent-recommended decomposition was reviewed in #6866. Its
candidate-execution strategy was subsequently refined by #7255; it is not
authority to restore full/native integration as a release prerequisite.

1. Add an explicit registry-only mode to the existing canonical seasoning CLI.
   Use its existing Python API semantics with no trusted local source roots;
   do not invoke product-root discovery in that mode. This denies local
   projects rather than granting a repository-wide or invented temporary root.
   Reject combination with explicit trusted-source roots.
2. Preserve the default CLI behavior and all ordinary source installers.
   Synchronize independently shipped policy copies and add their required
   changefiles; do not create a second installer or policy implementation.
3. The release-control owner integrates this mode into the existing trusted,
   hash-pinned metadata-validator provisioning recipes, including build,
   attestation, promotion and publication. Keep their current configured index,
   `--no-config`, binary-only and required-hash restrictions.
4. Coordinate the actual bootstrap recipe with #6851's exact materialized
   candidate gate. A valid product test result from an already-provisioned
   environment is not bootstrap evidence.
5. Keep emergency recovery a separately reviewed, explicitly authorized
   critical-control transaction. Ordinary helper implementation still lands
   through development first; no unreviewed candidate code enters signer,
   merger or publisher privilege. Validate wrapper/helper compatibility on
   the exact composed commit before activation.

This avoids the specific product-graph catch-22. It does not promise that a
broken Python interpreter, unavailable registry or invalid trusted control
implementation can never require recovery. The parent effort retains its
independently repairable main-control and rollback contract.

## Ownership and sequencing

| Participant | Exclusive scope |
|-------------|-----------------|
| Bootstrap independence contributor | This supporting proposal, canonical CLI deny-local mode, its tests and synchronized copies |
| #6237 / #6851 release-control owner | Reserved workflow/candidate-validator integration, qualification, activation and normal release |
| #6848 emergency bootstrap coordinator | One explicitly authorized current recovery, preserving the reviewed source repair |

The new contributor does not edit #6851's reserved workflow or validator files
without agreement. Proposal review precedes implementation. Normal source
review precedes activation; a development merge alone is not deployment.

## Plan and validation

- [x] Review this supporting contract and confirm non-overlapping integration
      with the existing release-control and emergency-repair owners.
- [x] Prove the default path still discovers and validates canonical sources;
      registry-only admission never calls product-graph discovery.
- [x] Prove direct, editable, requirement-file, configured and transitive local
      sources remain refused in registry-only mode, with no dependency writes.
- [x] Prove resolver seasoning, stricter configured cutoffs, configured indexes,
      hashes and retained-input checks still execute; invalid or unsupported
      resolver evidence remains a visible failure.
- [x] Exercise a real fresh UV/venv consumer with a valid pinned registry
      requirement while the materialized product graph is deliberately broken.
      No mocked resolver or pre-provisioned environment substitutes for this.
- [x] Resolve the original candidate-execution obligation through the reviewed
      #7255/#7269 replacement: actual trusted recipes retain deny-local bootstrap,
      while exact source-unit job/upload/archive and Dev/candidate byte-equivalence
      admission reject unsupported, missing, failed or mismatched evidence.
      The superseded generated/native integration matrix remains periodic work,
      not an additional promotion gate.
- [x] Review and merge the source repair, then activate through the existing
      authorized control path and observe a normal repair/release cycle.
- [x] Record source, control and published release identities separately, without
      claiming installed client adoption. Broader client, native, periodic and
      recovery acceptance remains tracked by #3336/#6237 under their current
      contracts and owners.

## Completion

The bounded objective requires deny-local bootstrap independence and the current
reviewed candidate-admission contract to be implemented, reviewed and proven
through the existing release owner. A temporary Main seed, merged support alone,
passing development test or relaxed admission rule is not completion. The
qualified release and natural-cycle evidence below resolves this objective,
not the whole Main-control effort or installed client acceptance.

## Implementation evidence

Plan #6866 merged as `74f550dadf2b6e76dfee0c7bb205dd7fa2d1f73d` after
current-head Copilot approval with zero findings and successful required CI.
The local support selects the existing deny-local API semantics explicitly;
ordinary source admission and all installer policy functions are unchanged.

Regression-first coverage had twelve failures and one default-behavior pass
before the new mode. The final automatically selected policy and synchronization
matrix passes 256 cases with one platform skip. The native fixture invokes
the actual repository `tools/seasoned_uv.py` wrapper after real materialization,
removal of canonical product libraries, and deliberate embedded identity
corruption. Correctly pinned aged registry installation succeeds in a fresh
venv, while the ordinary graph still refuses and cutoff/hash/age negatives
remain failures.

All thirteen independent policy copies are synchronized. Touched Python lint,
module-size and install-contract checks pass. No workflow, validator, signer,
release trigger, main branch, runtime selection or client preference changed.
Those support results alone did not establish caller integration or activation;
the subsequent completion evidence below supplies those distinct outcomes.

## Reviewed integration and release completion

- Support #6868 actually merged as
  `aae202b31b0026e162a766bd000ae795dfa78ac7`. The canonical CLI selects empty
  trusted roots before product discovery; ordinary source installation and all
  configured-source, hash, age and retained-input checks remain unchanged.
- Reviewed policy #7255 and source-unit engine #7269 replaced the old full,
  generated and native promotion matrix. Source controller #7281 and independently
  reviewed Main #7282 installed the current contract without candidate signing
  authority. Actual Main `4186ffb4a19caca118b0a2c5dcaeb2ee6c6b985d`
  retains `--registry-only` in all four metadata-validator provisioning recipes,
  with binary-only, configured-index and required-hash restrictions.
- Canary `39344826377` qualified the actual v2 source packet, published and
  read back immutable assets, and merged promotion #7285 to Main
  `5ee1468f15967fd6e76a0be08ee3dc69da2551bb`. The annotated
  `promote-dev-6e2e59ceed9a5cb822bd02906f5d4e1173aeae71` tag points to that
  exact commit at `2026-10-20T00:09:51Z`. This was an authorized manual canary,
  not a natural-cycle claim or another emergency seed.
- Canonical state-only resume #7287 merged as
  `3aca996a9c712a86dc4b2cb0c3c649f3b150b621`. Natural
  `repository_dispatch` run `39348986302`, attempt 1 from that control,
  completed successfully from `00:40:01Z` to `00:51:39Z` on October 20.
  The governing burndown records subsequent qualified natural cycles separately.
- This closes #6865's bootstrap catch-22 and accepted release-integration
  objective. It does not certify installed clients, every periodic/native
  portfolio, arbitrary future registry failures or completion of #3336/#6237.
