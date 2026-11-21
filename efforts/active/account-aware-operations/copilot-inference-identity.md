# Independent Copilot inference identity

Back to [Account-Aware Operations](README.md), Phase 6 and CI3296-G.

This is the complete #3296 phase contract, separate from Git/gh operation
identity. It advances the Worktrees vision's
[per-repo account identity](../../../visions/plugins/agent-worktrees/README.md#per-repo-account-identity-resolution)
without changing standing intent. The original proposed credential-extraction/
log-parsing mechanism was a feasibility question, not an accepted mechanism
or authority to access credentials. Private deployment examples are omitted.

The shared README carries the nine unchecked progress rows; the sections
below define their complete acceptance, not another checklist or effort.
Draft status and the temporary-token implementation restriction are preserved
by this planning amendment. Later authorized progress uses the normal effort
lifecycle; no technical gate requires those planning-time states to stay frozen.

## Repo selection

**CI3296-P1.** Resolve intended Copilot inference identity from an optional
exact repository `copilot_account` override, then machine-level
`default_copilot_account`. Use stable repository identity, including repositories
hosted outside GitHub. Never substitute an owner namespace, gh `account_map`,
active gh account or sibling preference. Keep Git/gh mapping and its scoped
credentials unchanged.

Distinguish a successfully read absent preference from an unreadable or
malformed repository-preference source. The latter produces an actionable,
non-secret unresolved result, not selection of a different configured machine
account or automatic correction. Resolve this through the existing preference
owner; do not treat a lookup failure as evidence that no repo override exists.
When default resolution is actually needed, apply the same rule to every
applicable machine/default source: an unreadable or malformed higher-priority
source is unresolved, not empty and not permission to choose a different
lower-priority account. Validate account-selection values against their owning
schema rather than coercing malformed input into an identity decision.

With no inference preference, preserve ambient behavior only in a mode that
already permits it, visibly unpinned rather than verified. Strict host-Copilot
mode still refuses an unset required account even if ambient credentials exist;
no-preference compatibility must not bypass that mode's source/account contract.

## Safe correction

**CI3296-P2.** Preserve durable default-off identity-switch policy independently
of an account preference. Disabled automatic shared-store enforcement remains
a pure no-op even when an account preference exists: no automatic identity
comparison, mismatch warning or correction. Explicit read-only diagnostics
may still report their requested resolution result; strict container credential
admission remains independent.

Failed switch-policy reads and non-boolean policy values never opt in.
Only the owning schema's explicit valid enabled value enables automatic
enforcement; strings such as `"false"` cannot become enabled through truthiness.
Invalid/unavailable policy keeps enforcement off with an explicit diagnostic
available, without an automatic account comparison or switch.

After opt-in, compare intended versus actually effective inference identity
through a supported source, keeping unknown, unavailable and verified results
distinct. An unresolved preference source cannot authorize automatic correction.

Automatic correction of a shared machine-wide identity store requires the
existing opt-in and otherwise-idle Copilot condition. Unknown concurrent-session
state is not idleness proof. Otherwise warn clearly without blocking launch or
changing another session, unless the operator explicitly accepts that shared-
session risk. Do not silently enable switching on update or adoption.
This nonblocking correction warning does not relax strict credential admission.

Protect pending launches as well as visible Copilot processes. The existing
launch/identity owners must coordinate the eligibility check, any shared-store
correction and the dependent startup so two different-account launches cannot
both observe apparent idleness and start with each other's selection.
Retain the protected selection through the point where startup has established
its effective identity. If safe coordination cannot be established, take the
existing nonblocking warning path rather than switching optimistically.
This is a concurrency outcome and validation obligation, not a new daemon,
broker, account-store format or source/resource owner.

## Launch and policy owners

**CI3296-P3.** Inventory and reconcile actual supported local Manager/direct,
Windows/POSIX, remote/venue and repository-context-change launch boundaries
through their existing owners. Preserve selected repository/account, effective
identity reporting, argument/exit behavior and explicit unsupported outcomes.
A Windows prototype call, successful credential injection or gh result is not
proof of effective inference elsewhere.

Worktrees owns resolution/integration. agent-machines owns declarative
machine-default policy and restore/reconcile behavior, not a second resolver
or credential store. Launcher, source, credential and native custodians remain.

## Supported authentication

**CI3296-P4.** Reuse supported authentication/account-selection interfaces.
Resolve the original prerequisite question about selecting an already-cached
account versus a vendor-supported login/switch interface. If unsupported,
retain actionable mismatch guidance instead of inventing credential-store
extraction or private token-format parsing.

Preserve cold/stale/unavailable/expired-source distinctions and explicit
host-Copilot versus legacy launch modes. Configured strict-source/account
failure, including an unset strict-mode account, refuses that credential route;
never silently substitute ambient or legacy credentials. This differs from
refusing an unsafe shared-store correction while retaining P2's warning.

## Claim and delivery boundaries

**CI3296-P5.** The existing trusted-container claimant retains bounded token
resolution/native child-environment isolation and its exclusions: no vendor
source-parser, auth-store, expiry/refresh-schema or restricted-proxy redesign
is received implicitly. Preserve process-only credential lifetime, no secret
disclosure/persistence and independent Git authentication.

Credit #3344/#3498's prototype/default-off policy, #6415's four trusted builders
and explicit strict/legacy isolation, and #6725's local preference editing at
their reported source scope. None proves complete machine-policy restoration,
live OAuth/inference, all-launcher identity or deployment. Publish owner-
appropriate configuration, diagnostics and migration guidance; qualify source,
release and authorized installed behavior separately from any shared-store
rollout.

## Selection and safety validation

**CI3296-V1.** Use synthetic non-secret repository/machine settings to prove
exact-repo override/default/no-preference precedence for GitHub and non-GitHub
targets, alias-equivalent identity and sibling isolation. Include no-preference
ambient compatibility only where permitted, plus strict-mode unset-account
refusal with ambient credentials present.

Include unreadable and malformed repo preferences with a distinct configured
machine account: prove actionable unresolved results, no fallback to that
account and no automatic correction. With automatic enforcement disabled,
prove no comparison/warning/correction even with a preference. Preserve
explicit diagnostic results and independent strict admission.

Include failed higher-priority machine/default reads when defaults are needed,
with a distinct lower-priority account; neither parse/read failure nor an
invalid account value may select that alternate account. Include failed policy
reads and non-boolean values, particularly the string `"false"`, alongside
valid false/true controls: automatic enforcement stays off for invalid input.

Exercise opted-in mismatch, unknown/unavailable identity, opted-in idle,
concurrent/unknown-session warning and explicit risk-acceptance paths.
Ordinary launch remains nonblocking when only unsafe shared-store correction
is refused; independent strict admission remains enforced. Unchanged Git/gh
identity and other sessions are controls.
Exercise two different-account launches interleaved before either Copilot
process becomes visible, through shared-store selection and effective startup.
Prove no cross-account switch/launch race; inability to protect the pending
selection takes the nonblocking warning path, not an unsafe correction.

## Consumer validation

**CI3296-V2.** Qualify each actually supported P3 boundary through its real
owning launcher/configuration consumer, including machine-default restore/
reconcile and context changes. Use controlled identity sources and fixture-
owned state before separately authorized native/live checks. Preserve literal
arguments, exit and independent Git authentication. Mark unavailable/
unsupported boundaries honestly; successful Windows or container injection
does not stand in for effective inference elsewhere.

## Authentication validation

**CI3296-V3.** Exercise strict-source/account failures, cold/stale/expired/
unavailable providers, explicit legacy selection, incompatible/missing vendor
capability and successful scoped launch. Prove no strict-to-ambient/legacy
fallback, shared-store surprise, secret leakage or second credential owner.
Keep isolation, supported mechanism, actual effective inference, containing
release and authorized installed evidence distinct.

No credential lookup, login, account switch, model request, container operation
or runtime mutation is authorized by this planning document.

## Complete acceptance

**CI3296-G.** Resolve every P1..5/V1..3 clause at its actual supported source/
consumer and separately authorized native/installed tier. Preserve independent
repo-keyed inference versus Git/gh identity, machine-policy ownership,
default-off/shared-session safety, strict/legacy compatibility and unknown
versus verified identity. Respect the bounded claimant and existing custody.

Reviewed receiving is not technical completion, source-claim release, live
authentication, deployment or authority transfer. Planning-time preservation
does not prohibit later authorized checkbox progress, activation of the effort
or resolution of its temporary-token restriction through the normal owner.
