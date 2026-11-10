# Phase 5 - Knowledge repository provider preflight

- **Status:** Contract reviewed in #11957; standalone native reader and optional
  binding consumer qualified in source; publication and receiving gates remain.
- **Parent:** [Configuration workbench](phase-5-configuration-workbench.md).
- **Coordination:** #1419 KO3; bounded source slice `shiny-actuator`.
- **Vision:** [Knowledge selection](../../../visions/installer/configurator/README.md#knowledge-selection-and-portable-defaults).

## Outcome and existing owners

Give knowledge create-or-bind callers one non-mutating native observation of
canonical registration, origin provider, selected account and server landing
policy before they choose authentication or propose default-branch work.
This closes part of the existing knowledge-selection intent. It does not make
binding, API access and publication readiness interchangeable.

The existing
[project-registration inspection](../../../plugins/agent-worktrees/src/agent_worktrees/repos_registration_inspect.py)
selects project/exposure identity. The
[authentication inspection](phase-5-authentication-inspection.md)
observes a configured GitHub credential, identity and repository metadata.
The [binding owner](../../../plugins/harness-knowledge/skills/binding-knowledge/SKILL.md)
already distinguishes canonical registration from discovery and classifies
origins before its provider-specific account check. Preserve these meanings
and reuse their registry, provider and scoped credential-read owners.
Neither current observation establishes server landing policy.

The standalone native owner supplies the shared result; the binding payload
and later Configuration consumers compose it. Do not copy policy inspection
into the harness, infer a provider from a short repository name or personal
issue-routing configuration, or add a new credential store or daemon.

## Proposed native boundary

Add a distinct read operation under the existing registration namespace:

```text
repos registration preflight --repository NAME
```

Its stdout is one versioned JSON result. Keep the existing `registration
inspect --anchor ... --default-name ...` syntax, result and exit semantics
unchanged. The proposed identity is
`agent-worktrees.repository-registration-preflight`, version 1. Publish support
with the exact executing immutable runtime, separately from package versions,
before a consumer emits the new syntax. Argument rejection remains strict.

The result keeps these independent observations:

| Observation | Required meaning |
|---|---|
| Registration | Canonical registry versus fallback-discovered, unresolved or unverified; actual class and usable local path are not invented. |
| Repository | Canonical origin/provider identity and its source; local-only and unsupported origins remain explicit. |
| Account | Selected repository-scoped identity and selection source, then credential/identity observation only through that provider's owner. |
| Landing policy | Proven server mode with attributable target/default branch and policy evidence, or explicit unknown, insufficient-permission or unsupported evidence. |
| Limits | Point-in-time read, unobserved capabilities and supported next step; no mutation or whole-onboarding authorization. |

Read one strict registry snapshot. A discovered checkout or unverified remote
cannot substitute for canonical registration and trigger provider credential
work. Preserve knowledge-class registrations without turning them into
executable projects. Missing, conflicting or unreadable sources remain visible
failures, not an empty ready result.

For the first implementation, support the existing scoped `github.com` owner.
Resolve its account before credential/API observation, verify the returned
identity and bind repository metadata to the requested canonical origin.
Missing selection, unavailable credentials and identity mismatch stop that
provider stage without ambient fallback, login or a global account switch.
Other providers return explicit unsupported policy observation without running
GitHub authentication. A local-only repository has no server policy to inspect;
that fact neither grants publication nor blocks independently permitted local
registration or binding.

### Server policy is evidence, not a local default

Read the actual server default branch rather than substituting registry branch
metadata or the current checkout branch. Account mappings, local `pr.*`
defaults, token presence, metadata access, administrative rights and a
bypass-adjusted mergeability summary are not proof of ordinary direct-write
permission.

Account for both applicable rulesets and classic branch protection. A partial
page, inaccessible protection source, ambiguous 404, malformed response or
unsupported rule cannot prove the absence of a PR requirement. A positively
observed applicable PR requirement may be reported even when another restriction
remains unobserved; do not erase that positive fact with a guessed direct mode.
Report direct mode only when the complete relevant provider evidence proves
that the selected identity may use ordinary direct publication. Otherwise keep
the mode unknown and explain the missing observation.

Qualify archived/disabled repositories, branch/target changes, update
restrictions, provider permissions and account-specific exceptions before
claiming a usable mode. An exception is not permission to bypass policy.
Keep proven policy separate from the caller's repository contribution rules:
the stricter applicable publication flow still governs.

Return bounded sanitized evidence, not raw policy bodies, credential values,
backend stderr or tokens. Every subprocess, page and provider read shares one
finite aggregate deadline and the existing owned descendant-cleanup boundary.
Bound response sizes and page counts; exhaustion is incomplete evidence.
Browsing does not create lock/state directories, fetch Git objects, rewrite
configuration, pin credentials, initialize a repository or start a service.

## Conditional consumer and compatibility

Follow [service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
Expand the provider and declare selected-runtime support before migrating the
binding consumer. Existing callers retain their old inspection meanings;
older providers do not receive guessed new syntax. Missing or unsupported
metadata leaves landing policy explicitly unobserved, without breaking an
independently valid local bind or unrelated Configuration operation.

The binding consumer presents canonical provider/account and the actual policy
limitation before suggesting authentication or publication work. This is not a
new mandatory policy RPC for every local write, task creation or ordinary
binding. Any later effectful caller must use its own current admission,
confirmation and revalidation; a saved preflight is not a durable grant.
Do not silently remove a required policy check or probe/replay an effectful
fallback across provider rollback.

## Dependency-ordered delivery and validation

- [x] Review this contract before implementing or advertising the new syntax.
- [x] Deliver the standalone native reader and selected-runtime support while
  preserving existing registration and authentication operations.
- [x] Migrate the shared binding consumer conditionally; keep unsupported
  server observations distinct from local binding readiness.
- [x] Cover canonical/fallback/unresolved/unverified registration, knowledge
  roles, local-only/GitHub/other origins, exact account selection, missing or
  mismatched credentials and target/default-branch drift.
- [x] Cover actual PR requirements and direct mode only when proven, classic
  protection plus rulesets, denied/ambiguous/malformed policy, incomplete
  pagination, unknown restrictions and aggregate deadline/cleanup failures.
- [x] Prove the real native CLI and shared consumer process boundaries with
  synthetic provider fixtures; compare complete fixture registry, Git,
  configuration and credential-sentinel bytes before and after the reads.
- [ ] Exercise old/new caller/provider, missing support and supported rollback;
  run required source checks, exact-head review and CI before merge.
- [ ] Record source fixtures, native consumers, actual provider/installed
  observations and separately authorized receiving independently. No live
  credential, installation or deployment trial is implied by these controls.

## Retained acceptance

#1419 retains full KO/KOV and K/PF acceptance. This slice does not implement
authentication acquisition, repository create/clone/register/bind writes,
commits or default-branch changes, rejected-push recovery, protection writes,
first-restarted-model admission, general pairing/overlays, state-write routing
or cleanup. #10844 retains optional authentication presentation; #8545/#356
retain repository bootstrap/acquisition/setup/trust; #11769 retains its Windows
first-use correction.

The source plan adds no installation, credential, policy, resource, binding,
service or rollout authority. A reviewed contract or successful fixture is not
complete public-first onboarding or receiving acceptance.

## Source qualification

The final native/provider/inventory packet passes 207 cases, including the
actual binding payload through the native parser and owned synthetic provider.
Direct/PR/legacy-owner and real aggregate-timeout cases preserve the complete
fixture tree. The binding regression packet passes 156 cases; its one exact
capability expectation was updated for the additive declaration, then the
affected consumer and real capability-probe packet passes all 19 selected
cases. No failed assertion is waived.

Strict binding-owner checks pass for Python 3.10 Linux and Windows targets.
Scoped Python lint, all 13 install contracts, version/documentation consistency,
local documentation links and whitespace checks pass. The actual fixed source
GraphQL query also succeeds through the existing scoped public-repository
reader; that schema observation is not full provider or installed acceptance.

These are source/contained Windows results. Native POSIX, installed UI/runtime,
first-restarted-model, cold bootstrap, actual Git transport and receiving remain
unqualified; no installation, authentication acquisition or rollout occurred.
Required current-head review and CI still precede publication acceptance.
