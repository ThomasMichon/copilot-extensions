# Optional pinned native ADO review transport

The in-process `providers.azure_review_transport.ado_review_context_factory`
supplies the concrete HTTPS request path for the existing `AdoReviewContext`.
Select `agent-worktrees[fleet-review]` explicitly. This composes Fleet's
separate [protected credential delivery](../../../fleet-manager/docs/gateway-protected-review.md)
and deadline-bounded DNS/connect owner; it does not select a normal host,
enroll a backend, supply a credential verifier or enable a CLI write option.
Default and observation-only configurations remain read-only.

The trusted embedding supplies the canonical six-field native review scope,
an exact `ReviewScope`, bounded `credential_source`, expected source/account/
audience, `checkpoint(material)` and the existing action-relevant
`review_check(operation, action, current, context=...)`. Both checks are
mandatory and raise on refusal. The checkpoint must verify current owner
admission, revocation and the actual material generation; a no-op callback
does not qualify an owner. Fleet's custodian still independently verifies
actual acquired permissions. Configured scope and routing restrictions do not
downscope a broad native token.

Construction first resolves the optional provider types and trusted TLS
configuration, then acquires exactly one immutable `CredentialMaterial`.
Observation material, mismatched native target/principal/actions, source/
account/audience, malformed tokens and expired material refuse. Every context
returned by that factory retains the same private material object, including
identity, candidate and policy reads, dispatch and reconciliation. The factory
never renews it, retries a native request or falls back to ambient auth.
An owner needing another generation must not replay an unknown operation.
Actual Gateway worker acquisition separately checks signed current support
through its version-2 review selection.

Only the existing adapter's PR/identity/iteration/discussion/status/policy
reads and selected comment, reply, owned-edit and own-verdict requests are
accepted. Direct unguarded writes, arbitrary routes, completion settings,
other principals' votes, redirects and ambient proxies are not supported.
Native state and action policy are rechecked before a selected effect and
again after connecting, before dispatch, through that same pinned transport.
The existing executor retains exact-candidate/ownership checks and persists
its unknown-effect fence before entering the native effect path. Its bound
policy callback reruns those same candidate/discussion/owned-target checks on
every fresher snapshot supplied by the transport, including the post-connect
read. Observed drift refuses dispatch while retaining the unknown attempt.

Each HTTPS exchange shares the caller's finite remaining budget, additionally
bounded by credential expiry. The existing Fleet resolver owns DNS cleanup;
TLS verifies the original hostname. A joined deadline timer interrupts stalled
headers/body/chunk buffering, response bytes are bounded, and connection
cleanup finishes before return. Malformed, incomplete, denied or lost replies
are unavailable, not proof of no effect. Complete late responses retain the
existing adapter's unconfirmed/partial-receipt semantics. Headers and private
material do not enter receipt JSON or error messages.

`tests/test_azure_review_transport.py` exercises synthetic credentials against
contained TLS, real request headers and native parsing, all selected action
routes, old/missing dependency, observation substitution, binding/expiry,
current-policy refusal, wrong native identity, unsupported routes, denied/
malformed/oversized/stalled replies and one-shot response loss. These are source
controls. A real replay-store/executor case retains the original durable
unknown attempt after a lost TLS effect response, reopens storage and refuses
resend without attributable readback. This is not live permissions, installed
support, normal-host enrollment or complete feedback/unknown-effect
integration qualification.
