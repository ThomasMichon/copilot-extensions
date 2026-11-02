# Zero-configuration governed runtime defaults

[Canonical effort and completion gate](README.md). Extends the reviewed
[exact-profile contract](../governed-python-artifact-promotion/exact-profile-contract.md)
under #4876; native engine application remains #6737.

## Operator-approved decision

Most operators never adjust runtime configuration. The adopting harness supplies
a complete reviewed baseline to the existing installation authority: CPU-first,
release-preferred exact profiles, automatic governed source fallback and reuse.
Knowledge/machine overlays are optional for stricter policy or explicit
capability selection, not a prerequisite for normal installation.

Direct user or authorized-agent execution of the trusted harness setup flow
is the approval authority for dependencies required by the selected
configuration, including its installer and selected plugins. Component
installers carry that scoped authorization through dependency resolution;
there is no separate package approver, consent prompt or hand-built exact-lock
approval file. Source, integrity, seasoning and revocation checks still
constrain what the operation may acquire and assemble.

This is desired policy, not proof that the adapter or consumer is implemented.
The repository-only record API merged in #7044 remains inert. Approval is not a
candidate's `approval_source` string, a self-declared flag or a matching digest.
Runtime enabling and production rollout retain their existing gates.

## Ordered implementation

- [x] Land this repository's Runtime Assembly and Index vision amendments and
  this sub-plan before implementation (#7066).
- [ ] Extend the existing authority seam with an authenticated adopting-policy
  input. Its default requires no private approval file, invented provider or
  extra controller. Establish the actual existing release/configuration
  adapters and their ownership; do not expose the raw inert approvals map as
  user configuration.
  The installation-context `resolve_runtime_profile_policy` reader (#8494) supplies
  the configuration-intent side through the existing source/mode/deployment
  owner, with no new policy file. It does not approve an exact record or
  independently verify source bytes. Release authentication (#8568) and the
  policy/release planner (#8586) now supply repository-only composition. Actual
  invocation-bound source authorization, governed source closure and the native
  constructor remain required before checking this item.
  Bind source-mode authorization to the actual invoked setup and its selected
  configuration through the existing installation/custody owner. The read-only
  intent reader is not itself evidence of an authorized setup operation.
- [ ] Prefer compatible profiles through the verified release channel, then
  check their complete exact closure against actual approved-source policy.
  Preserve the selected release and capability when a profile is unavailable.
- [ ] Implement automatic source-mode selection at that same authority for
  allowed availability/admission triggers. Independently verify the selected
  source; resolve within its constraints and the existing feed/seasoning policy;
  freeze a distinct complete exact lock before assembly. A governed feed
  lagging an artifact pin is not permission to alter that pin or use a public
  source. Integrity, provenance and unauthorized-source failures refuse.
- [ ] Compose #6941's candidate-input work rather than duplicating or modifying
  that contributor's open PR. Input constraints are not resolved locks or
  approval. Keep #4876 publication and #6707 runtime admission independent.
  Preserve #7028's separately selected runtime source and generation identity;
  this default neither selects development source nor adds a deployment gate.
- [ ] Apply the seam through the existing immutable constructor under #6737.
  Persist actual mode, lock and explicit capability only with successful
  selection. Do not run default installation followed by a Torch replacement.
- [ ] Adopt the released mechanism in the consuming harness's shared baseline,
  with generic policy in the harness, optional private overrides outside it and
  no baked-in feed identities or credentials. Source behavior may not wait for
  optional artifact publication. Roll out only under separate authorization.

The adopter's reviewed default intent preceded #7066. Its own repository
maintains that policy-review evidence; its private identity and links do not
belong in this public effort. The checked item above records this repository's
review gate only. Neither review gate enables a runtime.

## Validation and completion

- [ ] Real fresh-adopter invocation with no runtime-profile settings or approval
  files reaches CPU selection and permitted construction without a prompt for
  individual dependency approval.
  Exercise the real setup-to-component installer boundary, including an
  authorized agent caller. A configuration read, forged invocation flag or
  unrelated component/closure must not inherit that operation's authority.
- [ ] Both a real admissible release profile and a missing/blocked artifact
  profile reach the correct mode. Source fallback records its own exact lock,
  independently verified source and trigger without requiring artifact assets.
- [ ] Actual inspected wheel metadata matches the frozen complete closure,
  including roots, extras, markers, tags and device requirements. Prove a
  permitted CPU closure; use an available approved accelerator lane or document
  its exact unavailable boundary without inventing parity.
  The complete-profile comparator (#8399), bounded shared inspector (#8419)
  and invocation-owned exact snapshots (#8523) supply preparation primitives,
  not authenticated acquisition, admitted reuse or engine-consumer acceptance.
- [ ] Changed policy/supplied revocation invalidates affected reuse; unchanged
  warm invocation reuses admitted inputs without resolution or a feed request.
- [ ] Wrong source/record/target, blocked fallback, integrity failure and
  budget exhaustion preserve prior selection and healthy engine; the lightweight
  client remains usable. No ambient cache or public-feed escape is accepted.
- [ ] Retained explicit accelerators, deliberate changes, unprofiled migration,
  failed-build cleanup, rollback and concurrent installers retain the existing
  lifecycle/installation-cell boundaries.
- [ ] Native Windows/POSIX and available Darwin lanes exercise real consumer
  entry points. Record first-touch, release attribution and applicable external
  validation; no source unit test substitutes for installed behavior.

This slice closes only with the default authority and consumer paths verified,
documented and landed, or an explicit accepted transfer to a named objective.
It does not close the parent campaign's other native/service or shared-helper
gates. No model/corpus mutation or production rollout is authorized by the plan.
