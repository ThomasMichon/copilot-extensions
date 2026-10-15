# Validator bootstrap independence

Supporting proposal for [Main-Owned Workflow Control](README.md), tracked by
#6865. Related: the [materialized candidate gate](materialized-candidate-validation.md),
#6848's current outage, and #6849's merged attribution repair.

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

## Proposed narrow implementation

The following decomposition is agent-recommended, pending review.

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

- [ ] Review this supporting contract and confirm non-overlapping integration
      with the existing release-control and emergency-repair owners.
- [ ] Prove the default path still discovers and validates canonical sources;
      registry-only admission never calls product-graph discovery.
- [ ] Prove direct, editable, requirement-file, configured and transitive local
      sources remain refused in registry-only mode, with no dependency writes.
- [ ] Prove resolver seasoning, stricter configured cutoffs, configured indexes,
      hashes and retained-input checks still execute; invalid or unsupported
      resolver evidence remains a visible failure.
- [ ] Exercise a real fresh UV/venv consumer with a valid pinned registry
      requirement while the materialized product graph is deliberately broken.
      No mocked resolver or pre-provisioned environment substitutes for this.
- [ ] Require the actual immutable candidate bootstrap recipe in the existing
      pre-merge gate; demonstrate refusal before main changes for broken
      bootstrap, unsupported helper mode and missing/failed evidence.
- [ ] Review and merge the source repair, then activate through the existing
      authorized control path and observe a normal repair/release cycle.
- [ ] Record source, control and installed identities separately; transfer
      unexercised platform or broader recovery obligations to named objectives.

## Completion

Complete only when deny-local bootstrap independence and exact-candidate
qualification are implemented, reviewed and live-proven through the existing
release owner. A temporary main seed, a merged plan, a passing development
test, or a relaxed admission rule is not completion.
