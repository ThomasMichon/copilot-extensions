# Phase 4 - Portable core preferences

Parent: [Machine Health Heartbeats](README.md).

**Agent-recommended amendment, review before code.** This closes the
[supported-portable-preferences](../../../visions/user-state-carriers/README.md#supported-portable-preferences)
intent. It does not activate a carrier or widen the configuration-only grant.
The adopter has selected a OneDrive application-data home and all four domains
below; exact account, folder and deployment selectors remain adopter-owned.

## Required behavior

Store defaults for the mapping **canonical harness repository identity ->
preferred canonical knowledge repository identity** on the selected carrier.
Several harnesses may prefer the same knowledge repository; two harnesses with
the same checkout basename must remain distinct. Per-machine choices override
shared defaults. This is a first-class stored relationship, not an informal
text hint and not the effective machine-local binding.

Agent Machines owns bounded preference exchange. Each existing domain owner
validates its own data and controls adoption. Receiving or previewing a document
never restores a package, binds a repository, runs a module, starts a service
or enrolls a scheduled job.

## Existing owners and necessary extensions

| Domain | Reuse and proposed extension | Independent effect boundary |
|---|---|---|
| Copilot model/reasoning defaults | Machines `configuration.py` and `configuration_layers`; only the existing `model`/`effortLevel` ordinary scalar-floor cohort initially | Native live values and enforce constraints still win; no active-session model change |
| Telemetry capture/reporting defaults | Existing producer configuration and retained-sink setup owners; exchange typed desired choices, not raw events or machine paths | Explicit native configuration-only preview/apply; custom sinks preserved; running capture remains unknown until separately proved |
| Harness/knowledge mappings and venue suggestions | Worktrees canonical repository identity, registration, configuration catalog and selected `harness-knowledge` binding owner | Suggestion is not registration, binding, pairing, cloning or venue provisioning |
| Maintenance/update preferences | Existing Machines resource validators, authority and update-policy owners | Data cannot opt a machine into unattended execution, change maintenance-safe flags, install a schedule, update software or restart a service |

Today, Machines ordinary selection is deliberately narrower than requirement
packages. `configuration.py:eligible_values` admits only unauthoritative
`ensure-present` scalar floors. The native package parser, resource admission,
conflict analysis and live merge remain authoritative. Do not generate arbitrary
requirement YAML from received JSON or expand that ordinary allowlist to admit
resources, modules, permissions, activation or binding.

Worktrees currently reads the effective binding from machine-local configuration;
its knowledge preference overlay intentionally excludes `knowledge_repo`.
`repos_knowledge_configuration.py` delegates binding preview/apply to the selected
binding payload. It does not already provide a portable relationship registry.
Extend that existing relationship surface with a read-only preferred-mapping
projection and explicit adoption, rather than adding a second binder in Machines.
Use `project_state.RepositoryIdentity` and native registration resolution, not a
copied digest algorithm, folder name, account guess or portable absolute path.

## Carrier document and local acceptance

Proposed exchange commands live under `agent-machines preferences`, separate from
`health report`. Start with explicit local input/output paths and finite execution;
no directory discovery, login, sync-client installation or background watcher.
Carrier enrollment later supplies the account-qualified root, participants and
permitted domains. A missing carrier does not prevent local configuration.

Use one strict versioned JSON snapshot containing a publisher identity, a
monotonic revision, its parent revision and a bounded map of the four domain
payloads. A configured publishing participant edits shared defaults; other
participants receive them. This is an administrative publishing role, not a
distributed leader election or claim. Changing that role requires explicit local
configuration; an offline publisher is not permission for takeover.

The initial codec bounds the complete document to 256 KiB, 128 repository
relationships, 64 named-machine fragments and nesting depth 16. Reject duplicate
keys, unknown schema versions, unexpected fields, invalid UTF-8, partial files,
non-finite numbers and duplicate canonical relationship keys before producing
an adoption plan. Values are nonsecret typed data, never command strings,
credential references, environment expansion, local paths or executable templates.
Unsupported owner capabilities are visible per domain; they do not enable a
fallback writer. Unrelated admitted domains can still be inspected.

Publication atomically replaces a complete local snapshot inside the explicitly
selected namespace. It confirms only local publication, not remote durability or
receipt. Readers process only their configured exact document and report detected
provider conflict copies; they do not glob arbitrary files into configuration.
No synchronized lock, shared fleet-state writer or last-file-mtime winner exists.

Keep a local last-accepted snapshot and revision separate from the received
candidate. A repeated revision with identical content is idempotent. An older
revision cannot roll back accepted state. The same revision with different content,
a different publisher, a missing parent or a detected conflict blocks adoption
until an explicit reconciliation preview. A parent gap is recoverable by a
deliberate full-snapshot acceptance, not by guessing a winner. Receiver clocks
and receipt time never confer authority.

Every preview reports received revision, accepted revision, domain support,
source and scope, overridden choices, proposed effects and independent holds.
Apply rechecks the exact preview inputs through the owning native transaction;
changed inputs require a new preview. There is no cross-domain atomic-apply
claim. Record each owner's accepted/configured/running outcome separately.

## Mapping identity, precedence and adoption

The relationship payload is a list of typed records with `harness_repository_id`
and `knowledge_repository_id`, both supplied by the native identity owner.
Display labels are optional data, never lookup keys. An optional machine fragment
uses the native logical execution-machine identity, not a physical-host guess.
Machine-specific data must not leak between a host and its WSL guest.

For preference selection, the native adapter evaluates shared relationship
defaults, then admitted named-machine fragments, then explicit machine-local
relationship choices. Local overrides win, including an explicit local
suppression of a shared suggestion. A suppression is an owner-validated choice,
not inferred from a missing file or malformed binding. Exact CLI selection,
if any, remains outside preference composition under native validation.

An existing effective machine-local binding always remains authoritative.
A received default cannot silently replace it, even when the shared revision is
newer. An explicit but unresolved local binding is an error to repair through
its owner, not absence allowing fallback. Where no binding exists, show the
selected preferred relationship as an onboarding default; it still needs native
registration/readiness and explicit binding adoption before personal-state writes.

Resolve each identity to exactly one registered local repository, preserving its
knowledge/worktree class, account selection and effective remote. A missing,
ambiguous or unsupported identity remains unresolved; never search by basename,
create/clone a repository, repoint a registration or choose another account.
Local-only repositories without a qualified portable identity remain local-only
until their native owner supplies one; do not export an absolute path as identity.

After explicit selection, use the existing binding preview/apply transaction and
its selected payload, preserving conflicting-binding refusal, registration
rechecks, pointer lock and atomic write. Pair creation and personal-plugin
composition retain their native owners and separate readiness evidence.
Preference acceptance is not binding success or delivery into a running session.

Venue suggestions use the existing related-repository resolution surface; they
cannot override an explicit local route or provision a venue. If that owner has
no supported portable field yet, store and display the typed preference as
unsupported-for-adoption rather than inventing a route or executing it.

## Withdrawal and offline behavior

Deletion or unavailability of the synchronized file is not a request to undo
accepted local configuration. Report stale/unavailable transport and keep the
last accepted snapshot usable. A new complete revision can withdraw a shared
default explicitly. Withdrawal removes that default's contribution only; it
does not erase local overrides, unbind repositories, disable capture or uninstall
schedules. Effects already adopted through a native owner require that owner's
normal deliberate change flow. Reappearance of an old replica cannot undo a
withdrawal.

Telemetry summaries and health retain their separate namespace, schemas,
retention and publisher model. Raw telemetry stays local. A neighboring encrypted
Vault artifact does not protect plaintext preference identities or authorize
their publication. Qualify audience/account and minimize fields before carrier use.

## Dependency-ordered delivery and validation

1. **Review this amendment.** Preserve the original mapping request and private
   carrier selection; merge the design before implementation.
2. **Prove explicit local exchange.** Implement the strict codec, candidate versus
   accepted snapshot and preview-only domain projections. Exercise the normal
   CLI with generic fixture repositories and no installed-user state or cloud
   writes. This is the first functional slice, not completion of Phase 4.
3. **Prove native adoption.** Add each owner adapter with bounded capability
   checks. Prove ordinary model floors through existing live-value/enforce rules;
   prove at least two distinct harness mappings, shared-knowledge reuse, local
   override/suppression, host/guest isolation, missing/ambiguous registration and
   changed-preview refusal through the real native consumer. Keep binding
   application an explicit separate operation.
4. **Qualify carrier and activation.** Use isolated first-touch and reconcile
   controls on Windows/Linux, then an explicitly approved selected-carrier lane.
   No enrollment or real OneDrive write follows from source review alone.

Focused controls must cover malformed/oversize input, unknown version/domain,
same-revision conflict, old/reordered replica, missed parent, duplicate delivery,
publisher change, partial replacement, offline use and withdrawal. Assert no
restore, binder, module, installer, credential or service invocation during
receive/inspect/preview. Native apply tests retain exact configuration readback,
custom-sink preservation and original authority refusals.

Record source tests, normal CLI consumer proof, clean-room first use, native
platform execution, release availability, installed adoption, local publication
and peer receipt separately. Skipped lanes keep their acceptance items open.
