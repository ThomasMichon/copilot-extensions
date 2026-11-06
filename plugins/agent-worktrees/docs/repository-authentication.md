# Repository authentication inspection

The native engine owns this optional, read-only observation. It is separate
from registration, future-launch Copilot preferences and credential acquisition:

```text
agent-worktrees repos authentication inspect --context-project sample --repository sample --json
agent-worktrees repos authentication contract --json
```

`inspect` requires both exact registered names. One strict registry snapshot
selects the canonical remote and configured `account` or owner `account_map`.
An inferred remote owner is not an explicitly selected account: without a
configured account the result is `selection-required`, with no credential read.
Version 1 supports only the exact `github.com` host. Other providers/hosts return
`unsupported`; they are not reported as unauthenticated or required to adopt
GitHub CLI for their Git transport.

For a supported selection, the credential owner reads the selected account's
existing GitHub CLI store using explicit `--hostname` and `--user`. It removes
ambient token, host, routing and debug overrides from its child environment.
It then uses only that credential for absolute HTTPS API reads of `/user` and
the selected repository's metadata. A different login stops before the
repository read. A repository rename/redirect to a different canonical target
does not verify the originally selected target.

No operation signs in, changes the global account, writes a credential or
registration, binds knowledge, provisions a runtime, launches a worker or tests
Git transport. Existing registration and preference operations are unchanged.

## Response and meaning

Responses identify `contract: agent-worktrees.repository-authentication` and
integer `version: 1`, independently of the package version. An inspection
includes its native Configuration `target`, `registry_revision`, UTC
`observed_at` and provider-owned fields:

| Field | Meaning |
|---|---|
| `selection` | Provider, selected host, canonical repository, configured account and `repository`/`account-map`/`unconfigured` account provenance |
| `credential` | Existing GitHub CLI credential `available`, or `not-observed`; availability alone verifies neither identity nor access |
| `identity` | Selected login `verified`, `mismatch`, or `not-observed` |
| `repository_access` | Exact `repository-metadata-read` verified, or `not-observed`; never write permission |
| `status` | `observed`, `selection-required`, `unsupported`, `unavailable`, or a sanitized request/registry `failed` |
| `failure` | When present, bounded `stage`/`code`, and actual child `exit_code`/HTTP `http_status` when available |
| `unobserved` | Git transport, GCM, write permission, CodeSpaces, MCP and Copilot are explicitly outside this observation |

Exit 0 means the complete requested observation succeeded, or the metadata-only
`contract` read succeeded. Other outcomes return nonzero JSON; they are not an
authenticated empty result. Unknown arguments and abbreviations are rejected.
Failure codes distinguish missing command, unavailable credential, timeout,
output limit, invalid response, identity mismatch and unconfirmed access through
their stage and observation fields. A 401/403/404 or missing metadata is not
diagnosed as an expired credential: denial, hidden repositories, scope limits
and transient failures can be ambiguous.

Tokens, credential output, raw provider stderr, API bodies and private headers
never enter the response. The three stages share one 18-second execution and
cleanup budget, reserving two seconds for cleanup, with 256 KiB stdout and
64 KiB stderr limits per child. A failed stage is never retried. Unconfirmed
descendant cleanup retains the existing `OwnedCleanupError` and exact custody;
it must not become an ordinary unavailable observation or a retry.

This is a point-in-time read, not cached readiness or an acquisition receipt.
Consumers retain the target/revision/time with the result, visibly mark a prior
result stale while refreshing or after selection changes, and never enable
credential acquisition or first-task admission solely from this observation.
The first slice adds no executable sign-in action or Manager consumer.

## Selected-runtime support and compatibility

The wheel includes `agent_worktrees/repository-authentication-v1.json`. This
provider-owned descriptor has contract identity/version, `operations`,
`providers` and Boolean `read_only`; optional additional JSON fields may be
ignored. A caller requires exact contract identity, integer version 1 (not a
Boolean), `inspect` support and `read_only: true` before offering the optional
inspection.

Resolve the exact immutable runtime slot through the existing installed-runtime
resolver. Read the descriptor from that slot's installed package:
`Lib/site-packages/agent_worktrees/` on Windows, or its
`lib/pythonX.Y/site-packages/agent_worktrees/` on POSIX. Retain the same selected
slot/interpreter for invocation. Do not select support from a marketplace
payload, another installation, root-wide stamp, command-binding provenance or
package-version comparison. Do not import the domain package into Manager.
The `contract` operation is a standalone diagnostic of the executing runtime,
not a per-refresh negotiation subprocess or a universal capabilities protocol.

This is provider-first expansion: no ordinary consumer emits the new syntax
yet. A later consumer must conditionally use the descriptor of its selected
runtime and validate the response identity/version/target. Missing, malformed
or unsupported metadata, a legacy explicit engine override without a qualified
descriptor, or provider rollback makes only this optional inspection visibly
unavailable. Never strip flags or replay a different operation. Older consumers
retain all prior registration/preference operations; there is no equivalent
legacy authentication baseline and no old operation is retired by version 1.
See [service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and the [delivery design](../../../efforts/active/worktree-manager-control-plane/phase-5-authentication-inspection.md).

## Focused source validation

Run the isolated, opt-in packet:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_repos_authentication.py --allow-explicit-tiers
```

The packet covers strict native CLI/metadata reads against private test
registries, synthetic credential/API results, unchanged registry/sentinel
bytes, one aggregate budget, real owned synthetic processes and a real aggregate
timeout. It never uses the operator's stored credentials or a live backend.
It is not cold-machine, installed-runtime, real-authentication, Git/GCM,
Manager-UI or complete onboarding acceptance.
