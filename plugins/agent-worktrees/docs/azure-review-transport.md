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
reads, the derived native permission reads below, and selected comment,
reply, owned-edit and own-verdict requests are
accepted. Direct unguarded writes, arbitrary routes, completion settings,
other principals' votes, redirects and ambient proxies are not supported.
Native state and action policy are rechecked before a selected effect and
again after connecting, before dispatch, through that same pinned transport.
The existing executor retains exact-candidate/ownership checks and persists
its unknown-effect fence before entering the native effect path. Its bound
policy callback reruns those same candidate/discussion/owned-target checks on
every fresher snapshot supplied by the transport, including the post-connect
read, plus the original captured-target check when feedback is selected.
Observed drift refuses dispatch while retaining the unknown attempt.
The executor restores the original policy callback after each action,
including refusal and early exits, so an owner can reuse one pinned context
without retaining a prior action's guard.

## Native effective permission

The native factory extends `AdoReviewContext.check_review` with an effective
caller-permission prerequisite. The executor invokes it before its durable
attempt fence and before entering the effect path; the transport repeats it
with fresh native state before and after connecting. Existing embeddings keep
their original mandatory policy callback. Neither that callback nor the native
permission check replaces current custodian admission or credential-source
grant/audience verification.

The provider reads the organization's
[security namespaces](https://learn.microsoft.com/en-us/rest/api/azure/devops/security/security-namespaces/query?view=azure-devops-rest-7.1)
and requires one supported hierarchical `Git Repositories` namespace with
unambiguous `GenericRead` and `PullRequestContribute` actions. Namespace UUIDs
and action bits come from that native definition, not caller fields, translated
display names or a hardcoded identifier. Unsupported, duplicate or incomplete
definitions refuse.
Both native boolean `useTokenTranslator` forms are accepted: translation is
performed by the security service, not by replacing the documented logical
Git resource token on the client.

The documented
[Has Permissions GET](https://learn.microsoft.com/en-us/rest/api/azure/devops/security/permissions/has-permissions?view=azure-devops-rest-7.1)
evaluates each action bit separately for both the actual repository and PR target
branch with `alwaysAllowAdministrators=false`. Both results must be literal
`true` in a complete response. This deliberately conservative contract requires
repository-wide PR access as well as an allowed target; branch-only grants
do not qualify. A repository-only positive cannot hide a branch denial.
The [native security-token format](https://devblogs.microsoft.com/devops/git-repo-tokens-for-the-security-service/)
uses native project/repository UUIDs and the current PR's `targetRefName`:
`repoV2/<project>/<repository>/refs/heads/<encoded-branch>/`.
Each non-slash branch component is UTF-16LE hex; case, nested branches and
Unicode remain distinct. Missing or unsupported refs never fall back to a
broader token.

These are three additional bounded GETs per policy check, with no cross-check
cache, administrator shortcut, write probe, reacquisition or separate client.
Separate action evaluations avoid assuming an undocumented all/any aggregation
rule for a combined mask.
Denied permissions raise the existing authorization refusal; unavailable
information remains unavailable. Denial before the durable fence leaves the
action unattempted; denial afterward preserves its original unknown attempt.
An older embedding without `check_review` retains its existing callback in the
new executor. Conversely, an older executor using the new native factory still
receives mandatory native checks before dispatch, but does not gain the new
pre-fence check. Normal-host enrollment must qualify its actual selected pair;
the presence of new provider source alone does not establish that path.
A successful evaluation is not OAuth grant verification, token downscoping,
installed support or live qualification. The strict normal host remains
read-only until its separate enrollment prerequisites are met.

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
`tests/test_azure_review_permissions.py` additionally qualifies native namespace,
action, exact-token and complete-result semantics with synthetic responses.
