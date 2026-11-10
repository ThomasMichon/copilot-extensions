# Repository registration/provider preflight

The native read separates canonical repository registration, provider/account
identity and observed server landing policy:

```text
agent-worktrees repos registration preflight --repository <registered-name>
```

It emits one JSON object with contract
`agent-worktrees.repository-registration-preflight`, integer version `1`.
Unknown arguments refuse. Existing project/exposure `registration inspect`
and optional `authentication inspect` keep their previous meanings.

## Result and limitations

| Field | Meaning |
|---|---|
| `registration` | Requested exact name, canonical/fallback-discovered/unresolved source, class and present registered local path. Discovery does not select credentials. |
| `provider` | Identity from the canonical registered origin, not an issue route or inferred owner. Unsupported and local-only origins are explicit. |
| `account` | Configured repository, owner-map or default-account selection; then verified/unavailable/mismatched identity, not an ambient account substitution. |
| `landing` | Policy mode `pull-request`, `direct` or `unknown`, server default branch, classic/ruleset completeness and separately retained positive PR evidence. |
| `status` | `observed` when the policy mode was proven; `partial` when an observation is missing or limited. This is not binding or publication admission. |
| `failure` / `next_step` | Bounded sanitized stage/code and supported continuation. No backend body, credential or raw child diagnostic is returned. |

Exit `0` means the read returned its observation, including partial, unsupported
or local-only policy. Exit `2` means an invalid request or unavailable native
observation. Never use exit zero as an authorization or onboarding-ready bit.
Owned descendant-cleanup failures remain fatal and retain their original
custody rather than becoming an ordinary policy result.

The first provider is `github.com`, using the native selected-account
credential-read owner. Other origins do not invoke GitHub authentication.
Unregistered fallback discovery never establishes a provider or consumes a
credential. A local-only repository has `landing.status: not-applicable`;
other providers have `unsupported` policy observation. Neither blocks an
independently valid local registration or binding.

The result names the **server default branch**, which can differ from a
repository's configured contribution/integration branch. It does not answer
policy for an arbitrary branch or override repository contribution/review rules.
Git/GCM transport, a particular candidate's acceptance, configured publication
policy, binding and writable pairing remain unobserved.

## Policy proof

The read verifies the exact configured GitHub identity, then observes the
repository/default branch and classic branch-protection rule through a fixed
GraphQL read. The effective branch-rules endpoint supplies active rulesets,
including inherited rules. A second metadata/policy read detects target,
default-branch, head or classic-policy movement during the observation.

A positive classic PR requirement or applicable `pull_request` rule is not
relabelled as direct because an administrator can bypass it. If later ruleset
inspection is incomplete, the positive PR fact remains while completeness
and top-level status stay partial. Target movement invalidates the usable mode;
old branch-specific positive evidence is not a new default-branch assertion.

Direct mode is intentionally conservative: the selected identity has ordinary
write permission, the repository is active, classic protection is explicitly
absent, and complete active rules contain only deletion/non-fast-forward
protections or no rules. Other restrictions, unknown rules or incomplete
evidence remain unknown. This is no bypass grant and no promise that an
arbitrary commit will pass provider enforcement.

An ambiguous 404 is not proof of absent protection. GraphQL errors, unavailable
policy, response/target mismatch and denied access stay explicit; HTTP 403 is
reported as insufficient permission rather than diagnosed as token expiration.
No direct-write trial, login, credential pin, global account switch, branch
mutation or protection update is performed.

## Bounds and compatibility

One 18-second aggregate budget includes a two-second cleanup reserve. All
credential, identity, GraphQL and ruleset subprocesses share the remaining
execution budget and existing owned cleanup. Rule inspection admits at most
eight pages of 100 rows; page exhaustion is incomplete, not an empty rule set.
Each captured response is bounded by the selected credential reader's output
limits. Registry bytes are rechecked without reparsing or writing them during
provider work; source movement invalidates the observation.

The executing runtime advertises
`capabilities.repository_registration_preflight: 1` in its existing
`repos list --json` response. This additive field is independent of package
versions and is ignored by legacy callers. Select that exact native command
before reading support; a newer payload cannot establish an older runtime's
capability.

The binding payload's [optional read consumer](../../harness-knowledge/README.md#provider-policy-read)
uses this declaration before emitting new syntax. Missing support stays
explicitly unobserved. Provider rollback or a failed/malformed read is not
retried through an authentication or effectful fallback. Ordinary existing
binding and unrelated Configuration operations acquire no new policy gate.

Source controls cover the real binding payload, native CLI and owned synthetic
provider processes, old-provider behavior, aggregate timeout and complete fixture
tree preservation. They do not establish installed behavior, actual provider
credentials, fresh-machine or complete first-session receiving acceptance.
