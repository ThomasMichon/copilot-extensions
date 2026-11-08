# Optional protected review credential delivery

Back to the [Gateway runtime](gateway-runtime.md) and
[reviewed enrollment contract](../../efforts/active/durable-pr-watch-delegation/comment-producer-enrollment.md#provider-first-protected-writer-expansion).

This is an explicitly selected in-process SDK extension of the existing
Gateway, broker, controller and encrypted credential delivery. It supplies no
native writer, host enrollment, permission verifier, CLI option or deployment.
Ordinary version-1 Gateway/worker configuration, `credential.get`,
`ForgeScope`, observation renewal and string-only index consumption keep their
existing meanings.

## Separate binding and support

`gateway_contract.ReviewScope` has contract identity
`fleet-manager.protected-review-credential/v1`. It binds the ADO HTTPS
`api_base`, two-component `repository`, `authentication` (`bearer` or `basic`),
native `project_id`, `repository_id`, positive PR `number`, `principal_id` and
nonempty unique `actions` (a tuple in Python, a list in JSON). The only action
kinds are `comment`, `reply`, `edit_owned_comment` and `verdict`; no merge,
completion settings, arbitrary HTTP or other principal's vote is included.
Its `to_dict()` includes that exact `contract`. Syntax validation is not
verification of an acquired credential's actual permissions.

An explicitly selected `fleet-manager.gateway` **version 2** permits a
PR-worker credential rule with `review: ReviewScope-JSON` instead of `forge`.
The two bindings are mutually exclusive. Version 1 still refuses the new
form. Version 2 also preserves all prior observation and index rules.
The signed existing session response adds `contracts` for receiver support.
It advertises `credential.review` only while the selected worker has a review
rule and its current enrolled controller has an unexpired review-capable
subscription. Receiver metadata alone is not whole-path readiness.

The custodian explicitly constructs `Controller(..., protected_review=True)`
with a provider declaring `review_contract` equal to the contract above.
The controller checks the actual Gateway's signed support before subscribing
with that contract through the existing route; renewals preserve the
selection. An old/default controller never advertises it.
`RelayPolicy.review` selects the exact existing relay policy, independently
of `forge`, and still requires the mandatory actual provider-result verifier.
`RelayCredentialProvider` declares review support only when it has such a
policy. A custom provider has the same verification obligation; setting the
attribute does not implement native permission, expiry or revocation checks.

## Explicit consumer and receipts

`gateway_worker.review_credential_provider(Path(...), source=..., account=...,
review=ReviewScope(...), timeout=30)` selects a **version-2**
`fleet-manager.credential-worker` file with the same private path references,
controller keys and enrolled identity as the ordinary worker. Every binding
must carry a review selection. Ambiguous/wrong source, account or review
selection refuses before key/access-token reads. Version-1 observation
loaders refuse version-2 input instead of silently consuming it.

Each acquisition rechecks the signed current session/active-controller support
before submitting the separate `credential.review` operation. The broker
rechecks exact current enrollment, rule and controller support; withdrawal or
rollback refuses without calling the credential source. Controller acquisition
and mandatory verification use the original exact parameters. Sealed delivery
and recipient checks preserve `CredentialMaterial.review: ReviewScope` with
`forge is None`, including native identity/action selection and effective
expiry. Wrong/missing/substituted binding refuses before ACK or token return.
The token remains excluded from representations and never enters a receipt log.

The separately selected [native ADO factory](../../plugins/agent-worktrees/docs/azure-review-transport.md)
pins one delivered material generation through `AdoReviewContext`, current
owner/policy checks and finite requests. Credential delivery alone does not
select it. Live permissions, installed controller/worker support, complete
unknown-effect integration, native effects and normal-host adoption still
require separate qualification and authorization.

The contained synthetic TLS tests in
`tests/test_gateway_review_credentials.py` cover current/prior support,
withdrawal/rollback, exact selection, material substitution/expiry, sealed
native-principal checks, unchanged observation and the existing verifier seam.
They are source evidence, not native permission or installed adoption proof.
