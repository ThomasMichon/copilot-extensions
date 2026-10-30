# Proposed continuation: owner-executed read adapter

Parent contract: [PR Review Workspaces](README.md).
Existing source slice: [authorized acquisition](acquisition-slice.md).

This is an **agent-recommended proposal**, not an implemented interface,
credential grant, operational command or rollout instruction.

## Concrete dependency

The merged acquisition CLI consumes `fleet-manager.forge-credential` v1; it
does not supply an issuer. Neither an authenticated Azure client nor a
secret-retrieval command establishes the caller policy, account namespace,
target binding and permitted operation needed to assert that DTO.

An adapter may serialize material only when its existing trusted credential
owner already establishes the real binding and expiry. It must not manufacture
`forge` labels around a raw secret, copy a credential cache, change scopes or
select ambient auth. Absence of such an owner leaves that profile unavailable.
The schema name does not require installing Fleet Manager.

The other legitimate integration is to keep credentials entirely inside an
already authorized ADO read owner. That owner, rather than a caller-side secret
wrapper, must implement and explicitly adopt the finite operation below.
No operational owner profile has yet been qualified by this campaign.

## Minimal proposed owner operation

Proposed capability: `owner_executed_review_evidence/v1`.

The existing read owner exposes one finite, read-only evidence operation through
its existing authenticated transport and normal authorization/argument-validation
pipeline. A request selects an **already owner-established binding reference**,
PR number and bounded work budget. It does not supply credentials, endpoints,
account labels, arbitrary URLs or commands. The owner resolves the reference
inside the current authorized caller/account/target context; possession of its
text is not authority.

The handler uses the existing acquisition orchestration and bounded native read
implementation, adapted to its authorized read context. It performs only the
fixed identity, PR, iteration/change, all-status discussion and policy-evaluation
reads. Do not copy another HTTP/pagination implementation into the adopter.
If the generic collector needs a narrow read-owner interface, factor that seam
in the existing provider rather than fabricating a credential DTO to enter the
credential-command path.

The owner retains credential renewal, caller policy, role checks, scope
validation and lifetime/cleanup responsibility. It must detect loss or change
of actual account/principal across requests and pages, not merely compare
caller-supplied labels. The actual principal remains the full native ADO
`authorizedUser.id`, not an AAD identity. A current owner checkpoint and
authenticated request context supply authority; constructing a callback or
deserializing an evidence envelope does not.

Return the existing version-1 review-evidence envelope, section intervals,
completeness/errors, exact range binding and measured timings. Include
owner-generated context attribution where supported, but never treat a trace ID
or receipt as a permission grant. Return no token, header or secret-protocol
response. Keep policy configurations distinct from evaluations and preserve
completion/reviewer state as observations only.

## Generic source and adopter ownership

- **Generic source:** define and test the narrow transport/read-owner seam and
  capability selection; reuse the current collector and normalizer.
- **Existing ADO owner:** implement the actual protected handler using its
  already authorized context, publish its attributable callable/capabilities and
  retain its normal authentication, authorization and input gates. An external
  owner's source change requires that repository's own explicit assignment and
  contribution flow; this proposal does not authorize editing it here.
- **Adopter:** explicitly adopt the owner profile and exact binding through its
  existing configuration/permission owner. Source publication alone does not
  make that API operational or authorize deployment.
- **Consumer:** select the qualified profile explicitly and normalize its
  evidence without an agent semantically assembling another packet. Missing
  profile/adoption is unavailable, not a reason to run a password helper or
  alternate backend.

The current credential-command profile remains valid for adopters that actually
have its trusted owner; it is not silently replaced or weakened. Receiver
support and active-owner capability discovery precede consumer migration.
Do not advertise the proposed profile from an installed manifest or source
package before the executing owner actually supports it.

## Validation and admission

- [ ] Review this proposal before changing the generic transport contract.
- [ ] Synthetic adopted-owner controls prove allowed fixed reads, wrong
  caller/account/target and expired-context refusal, principal change,
  request/page deadline propagation and owned cleanup without credential export.
- [ ] Missing/unadopted owner profiles refuse before provider access; old
  credential consumers retain their supported behavior, including rollback.
- [ ] Exercise the owner handler and successor normalization with actual parser/
  transport boundaries, including partial paging and head/iteration drift.
- [ ] Obtain an explicitly authorized existing-owner implementation and adoption
  before live qualification; source simulation alone does not close that gate.

No new issuer, login, role grant, secret-store mirror, service, poller, runtime
installation, deployment or PR review effect is part of this proposal.
