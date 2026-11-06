# Phase 5 - Provider-owned repository authentication inspection

- **Status:** Native provider and Manager Configuration composition implemented
  in source; receiving acceptance remains open.
- **Parent:** [Configuration Workbench](phase-5-configuration-workbench.md)
- **Coordination:** #10844, claimed by `shiny-actuator`.
- **Vision:** [Authentication and onboarding readiness](../../../visions/installer/configurator/README.md#authentication-and-onboarding-readiness).

## Outcome and boundary

Make the selected repository's authentication observations inspectable from
standing Configuration without turning Manager into a credential owner.
This first consumer is bounded to repository access; it does not claim that
every enabled provider, Git transport, CodeSpaces or MCP target is ready.

The [version-1 contribution contract](../../../worktree-manager/docs/plugin-contribution-contract.md)
already owns configuration placement, provider commands, rows/cards, explicit
actions, progress/cancellation and refresh. Reuse it. No universal authentication
manifest, second modal, central credential store, mandatory Vault dependency,
helper daemon or label/action-name discovery heuristic is proposed.

This closes part of the existing Configurator intent rather than extending it.
The [device-authorization pattern](../../../docs/patterns/in-picker-device-authorization.md)
is existing interaction prior art, not permission to reuse a provider's vendor
registration, scopes or credential namespace.

## Source facts that constrain the design

The current native registration resolver
[`resolve_registration_account`](../../../plugins/agent-worktrees/src/agent_worktrees/repos.py)
intentionally treats missing `gh` as authenticated to avoid registration prompts.
Its meaning must not change merely to supply a new panel. The
[`resolution_snapshot`](../../../plugins/agent-worktrees/src/agent_worktrees/repo_accounts.py)
is account mapping, not credential or live-access evidence.

The actual configured-identity consumer uses
[`account_selection` / `gh_token_for_target`](../../../plugins/agent-worktrees/src/agent_worktrees/github_credentials.py)
and the
[`_gh_env_for_repo`](../../../plugins/agent-worktrees/src/agent_worktrees/installation_cli.py)
environment boundary. An unavailable configured credential fails closed;
legacy unconfigured selection can still use ambient authentication. A new
inspection cannot report that fallback as verified selected-account access.
Native token lookup alone also does not verify identity, scopes or access.

The existing
[Repositories contribution](../../../plugins/agent-worktrees/pivots/repositories-config.json)
and [effective-configuration contract](phase-5-effective-configuration-contract.md)
own future-launch Copilot preferences and confirmed existing-knowledge binding.
Those are not authentication operations. Keep their reads and write promises
unchanged; do not make opening the preference catalog start credential work.

## Native provider contract to deliver

The following requirements govern the native provider and later consumer.
The [native version-1 contract](../../../plugins/agent-worktrees/docs/repository-authentication.md)
defines the bounded standalone implementation; this is not rollout or
permission for a live credential observation:

1. Resolve the explicit selected project and canonical repository/provider
   through one strict native registry snapshot. Preserve the selected host
   and account plus their provenance; do not select a provider from a short
   name, personal issue route or current global account.
2. Keep selection, credential availability, verified identity, requested
   access and observation failure separate. A missing command, denied or
   expired credential, mismatched identity, inaccessible target and unsupported
   provider must not collapse into an authenticated empty result. A repository
   permission failure can be ambiguous; do not diagnose expiration without
   evidence.
3. Qualify only the exact capability observed. A GitHub API observation is
   neither a Git/GCM transport test nor proof of write permission, landing
   policy, CodeSpaces scope or MCP target access. Report unobserved claims
   explicitly. Preserve usable alternative credential owners rather than
   requiring GitHub CLI for every Git operation.
4. Use the existing credential-source and provider boundary. Never return
   tokens, raw credential output, secret-bearing stderr or private source bodies.
   Any native subprocess has a finite aggregate budget and attributable stage,
   result and descendant cleanup; Manager's outer read timeout is not the
   provider's only cleanup contract.
5. Deliver a standalone native read surface as well as a version-1 configuration
   contribution. Its rows/summary/card data are provider-owned presentation,
   not a mandatory cross-provider authentication schema. The CLI syntax,
   response identity/version and selected-runtime support declaration must be
   documented and tested before the consumer starts using a new operation.
6. Preserve the existing registration and preference operations. Do not silently
   widen their effects to avoid defining the separate inspection boundary.

The native read syntax, response and package-data support declaration are now
defined against the executing owner. Consumer migration remains separate:
it must select that exact immutable runtime before reading support or
emitting the optional inspection operation.

## Manager composition

Use an explicitly named provider-specific Authentication configuration surface
with the existing version-1 placement and native command resolution. Manager
does not import the domain package, read its private registry/credential store,
infer all authentication providers from their action names or mark a whole
selected harness ready from one row.

Opening/loading/refreshing performs only the declared bounded read. No listing
starts sign-in, changes selection or credentials, provisions a runtime, binds
knowledge or starts workers. Pending, stale, unavailable and unsupported
observations remain visible; only a fresh applicable read can enable a
corresponding executable action.

Inspection may display the correct owner's supported next step. An executable
acquisition action is a later separately defined provider/account operation,
not part of this first read-only slice. Until then, show its limitation rather
than fabricate a generic Sign in command. A future acquisition must independently
bind and verify identity/access, preserve unrelated stores/accounts and reuse
the current progress/cancellation and originating-scope refresh contracts.

## Dependency-ordered Plan

- [x] Review this delivery design and reconcile the native read contract and
  selected-runtime support declaration before implementing new caller syntax.
- [x] Deliver the standalone provider inspection and focused controls without
  changing registration, preference, binding or credential-store behavior.
- [x] Connect the actual native provider to standing Configuration through the
  current contribution contract; prove navigation, errors and refresh without
  rebuilding the shared shell.
- [ ] Record exact source/review/CI evidence, compatibility behavior and receiving
  limitations. Keep acquisition and broader onboarding acceptance separately
  tracked; source inspection is not completed authentication setup.

## Validation and compatibility

| Gate | Required evidence |
|---|---|
| Native contract | Actual parser/process selection, strict snapshot, provider/host/account binding and unsupported-provider behavior |
| Meaning | Missing executable/credential, mismatch, denied access, unobservable and stale state remain distinct from verified capability |
| Real consumer | Production Configuration invokes the native fixture owner; cards, loading/error states and originating-scope refresh work without mocked success |
| Read safety | Compare registry, source and credential-store bytes; no sign-in, global account switch, write, install or worker admission by browse/refresh |
| Process failure | Aggregate timeout, malformed output, nonzero exit, bounded sanitized diagnostics and exact descendant cleanup retain their real outcome |
| Skew | New/old caller-provider and rollback combinations either preserve the documented baseline or visibly refuse the unsupported optional inspection before effects |
| Receiving | Native installed/provider evidence is recorded separately from isolated fixtures and source tests; no actual credential or rollout side effects during source validation |

Apply [service-contract evolution](../../../docs/patterns/service-contract-evolution.md):
expand the provider and declare support for the exact selected immutable
runtime before migrating consumers. Do not infer support from package versions
or a newer payload. Do not add a universal handshake or probe/replay an
effectful operation. An unavailable optional inspection must not disable
unrelated existing panels or operations.

## Continuing owners and exits

#3004 retains its actual ambient first-restarted-model prompt and consolidated
provider/landing-policy preflight, rejected-push recovery, normal default offer
and new-repository protection. #3005 keeps related/lifecycle synthesis.
#8545/#356 retain repository bootstrap/acquisition/setup/trust; #542 keeps
first-run entry and complete readiness. Phase 5 retains the shared shell and
its fresh-install, installed UI and authorized rollout gates.

This plan does not alter installation/replacement behavior, enrollment, source
trust, personal state, resource custody or current holds. A plan merge, read-only
panel or successful API fixture cannot complete those owners' acceptance.

## Native provider source evidence

The provider-first slice adds strict `repos authentication inspect` and
metadata-only `contract` reads, the wheel-owned version-1 descriptor and a
bounded GitHub CLI credential/API adapter. It requires an explicitly configured
account and exact supported host. Registration, preference and binding
operations are unchanged; no ordinary consumer emits the new syntax yet.

Thirty-seven focused controls pass, including the native parser, unchanged private
registry/sentinel bytes, synthetic credential/API failures, identity mismatch,
real owned synthetic processes and a real aggregate deadline. Runner
`RuntimeError` outcomes are projected without swallowing retained cleanup
errors. Eighty-six legacy scoped-credential controls also pass;
twenty additional account-resolution controls retain their earlier evidence.
The actual built wheel contains the
exact inspection source, descriptor and preserved existing package data;
install-contract and documentation guards pass.

These are contained source/package results, not a cold installation, selected
installed runtime, live credential/backend, Git transport, Manager UI or
complete authentication/onboarding pass. The Configuration consumer and its
mixed-runtime/refresh evidence are recorded below. Broader receiving and
acquisition acceptance remain with the existing owners.

## Configuration source evidence

The optional native adapter uses the existing version-1 list/card shell.
The executing provider advertises its additive Configuration projection in
the wheel-owned descriptor and supplies rows, cards, target/revision/time and
limitations. Manager verifies selected interpreter/module and slot-local support
before a single native read. No new payload pivot, CLI operation, universal
authentication schema or executable acquisition action is added.

Thirty-nine native provider controls and eighteen Manager compatibility controls
pass. Twenty-eight Configuration controls pass, including the actual production
UI and process boundary to a private-venv fixture running the real native parser,
registry reader and projection with only its credential/API adapter substituted.
The journey proves hidden-section non-probing, loading/navigation, native detail
cards, stale refresh, unavailable observations, malformed-result errors,
originating-scope settlement, changed project and pre-invocation rollback refusal.
Registry and credential-sentinel bytes are unchanged. One unrelated existing
opt-in native-owner test is skipped; it supplies no evidence here.

The journey exposed and now covers a single-scope summary path that previously
omitted pending/stale metadata. The shared shell retains cached rows and their
identity while displaying that metadata. Compatibility controls separately cover
missing/malformed/unsupported descriptors, inspect-only older runtimes, newer
payloads, foreign explicit commands, provider rollback and retained cleanup errors.
Old callers keep the original standalone observation/exit semantics, and older
Managers do not discover the new optional read.

These are contained source/fixture results, not a cold product installation,
selected installed-runtime qualification, native performance budget, live
credential/backend, Git transport or complete onboarding pass. Required PR review
and CI remain independent publication gates. Receiving and acquisition stay open.
