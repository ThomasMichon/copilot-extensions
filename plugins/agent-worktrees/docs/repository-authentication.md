# Repository authentication inspection

## Portable GitHub account slots

An adopter can keep non-secret login choices in the bound knowledge repository's
`.agent-worktrees/github-accounts.json`:

```json
{
  "version": 1,
  "accounts": {"work": "operator-work", "public": "operator-public"},
  "repositories": {"Publisher/tool": "public"}
}
```

Slot names are operator-defined labels, not authenticated account types.
This optional file is selected through the native knowledge-binding owner.
It is not a credential store, a shared harness configuration or a
`copilot_account` preference. Edit it through the knowledge repository's normal
writable-worktree/publication flow; reconciliation never writes it.

The independent native CLI projects these choices into its machine-local
registry. Preview is the default; `--apply` opts into writing:

```text
agent-worktrees repos account reconcile --capabilities --json
agent-worktrees repos account reconcile --default-slot work --bootstrap-from-context --repository-slot Publisher/tool=public --json
agent-worktrees repos account reconcile --default-slot work --bootstrap-from-context --repository-slot Publisher/tool=public --apply --json
```

Run inside a registered project, or supply `--context-project <name>` to select
it explicitly without changing the caller's directory. Adopters choose the
default slot and optional exact repository exceptions; this engine supplies no
organization-specific defaults. When the default slot is absent,
`--bootstrap-from-context` may use only an existing explicit account or owner
mapping for the selected project. `--identity-source <registered-name-or-owner/repo>`
selects another explicit source instead. Namespace spelling, account suffixes,
ambient login and the global preferred account are not bootstrap evidence.

Existing repository, owner-map and preferred-default choices win. Reconciliation
records its own projections alongside the registry, so a later knowledge-slot
change or removal refreshes only that owner's unchanged managed values.
Native explicit `repos account default` and `repos add --account` take over
their fields, even when the value is unchanged; manual value changes are also
preserved. Warm reconciliation does not rewrite an unchanged registry.
Repository slots apply to matching registered repositories, not every repository
under their owner. An unregistered target remains `not-registered`; registration
is a separate native operation. Missing slots and conflicts are actionable
partial results, never a request to invent a login.

The response identifies `schema: agent-worktrees.account-reconciliation` and
integer `version: 1`. The effect-free `--capabilities --json` operation advertises
integer `portable_account_slots: 1`; callers must confirm support on their
selected executable before sending projection syntax. Older runtimes report
unsupported: keep all old account commands available and do not replay another
writer or install an update automatically. This is additive provider expansion,
not retirement of an existing operation.

The policy is strict, bounded JSON (64 KiB); invalid or concurrently changed
policy refuses projection. Existing native registry locking, read-baseline
comparison and atomic replacement protect concurrent local choices.
Exit 0 with `status: ready` means configuration composition only, not verified
identity or access. No operation contacts GitHub, reads credentials, signs in,
changes global authentication or provisions a runtime. Verify the selected
repository-scoped identity separately before publication.

## Read-only authentication observation

The native engine owns this optional, read-only observation. It is separate
from registration, future-launch Copilot preferences and credential acquisition:

```text
agent-worktrees repos authentication inspect --context-project sample --repository sample --json
agent-worktrees repos authentication contract --json
```

`inspect` requires both exact registered names. One strict registry snapshot
selects the canonical remote and configured repo, owner-map or preferred
`default_account` policy.
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
| `selection` | Provider, selected host, canonical repository, configured account and `repository`/`account-map`/`default-account`/`unconfigured` provenance |
| `credential` | Existing GitHub CLI credential `available`, or `not-observed`; availability alone verifies neither identity nor access |
| `identity` | Selected login `verified`, `mismatch`, or `not-observed` |
| `repository_access` | Exact `repository-metadata-read` verified, or `not-observed`; optional `owner_kind` is `user` or `organization`, never operator identity or write permission |
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
Owner kind comes only from the same repository metadata response's `owner.type`,
with no additional request. Missing or unfamiliar type is omitted and displayed as `unknown`.
Neither a user nor an organization namespace changes the configured account;
a user-owned upstream repository does not identify its consumer's public login.

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
The optional Manager Configuration consumer adds no executable sign-in action.
Personal GitHub authentication is not an upfront onboarding prerequisite:
requested repository work must actually require that account before an
acquisition prompt. Public tooling acquisition and this optional observation
are not proof of such a requirement.
The native response also carries `configuration: {entries, summary}`: the
provider-owned version-1 list/card projection. Its cards retain the target,
revision, time, stage and limitations rather than equating an unavailable
observation with an empty inventory. The original observation and exit semantics
are unchanged; old standalone callers can ignore the additive projection.

## Selected-runtime support and compatibility

The wheel includes `agent_worktrees/repository-authentication-v1.json`. This
provider-owned descriptor has contract identity/version, `operations`,
`providers` and Boolean `read_only`; optional additional JSON fields may be
ignored. Integer `configuration: 1` additionally advertises the provider-owned
Configuration projection, independently of support for standalone `inspect`.
A caller requires exact contract identity, integer version 1 (not a
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

The provider-first expansion precedes the optional Manager consumer. That
consumer conditionally uses the descriptor of its selected runtime and validates
the response identity/version/target, registry revision and projected summary.
It forwards and validates the selected installation receipt across the sanitized
list transport, and preserves Manager's explicit engine-command precedence over
an inherited handoff; invalid context does not select another legacy owner.
Only the resolver-generated bytecode-cache argument is excluded when comparing
two selections of the same interpreter/module; it does not select another
runtime. Missing, malformed
or unsupported metadata, a legacy explicit engine override without a qualified
descriptor, or provider rollback makes only this optional inspection visibly
unavailable. Never strip flags or replay a different operation. Older consumers
retain all prior registration/preference operations; there is no equivalent
legacy authentication baseline and no old operation is retired by version 1.
No new pivot is published in the marketplace payload: older Managers keep their
existing panels without accidentally emitting this optional syntax. A Manager
which supports the adapter but selects the earlier inspect-only descriptor
shows Authentication as unsupported until a qualifying runtime is selected.
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
