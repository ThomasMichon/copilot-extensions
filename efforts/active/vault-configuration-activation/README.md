# Vault Configuration Activation

- **Slug:** `vault-configuration-activation`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed plan and implementation PRs
- **Created:** 2026-11-02
- **Status:** Active
- **Vision:** [Vault explicit adoption and standalone use](../../../visions/vault/README.md#central-fleet-and-standalone-local)
- **Umbrella issue:** #2350 (bounded Vault adopter only)

## Guiding Intent

Loading Agent Vault exposes a capability; it must not by itself require a
runtime, service or KeePassXC installation. Explicit configuration selects
automatic runtime participation without moving database or credential custody.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Vault source driver | Plan, source, validation and PR stewardship | Managed source worktree, public issue #2350 |

## Coordination

- **Topology:** sequential plan and implementation PRs.
- **Host (owns PRs):** Vault source driver.
- **Delegates:** none.
- **Handoff:** canonical effort and issue retain remaining acceptance.
- #6928 and #8826 retain installer-engine/watchdog ownership. This slice does
  not replace either change or implement the family-wide activation convention.

## Context

Vault already composes portable repository and bound-knowledge preferences
through the shared configuration-layer owner, but bootstrap and readiness do
not consult an activation declaration before requiring its runtime/backend.
This closes an explicit-adoption gap in the existing Vault vision; it does not
extend fleet, authentication or service-lifecycle authority.

## Request

Public-safe capture: keep Agent Vault enabled as an available plugin, with
runtime participation explicitly selected through repository or qualified
bound-knowledge configuration. Do not make KeePassXC a general plugin-loading
requirement. Preserve independent standalone use and existing credentials.

## Scope Approvals

Source changes, disposable validation and normal issue/PR publication are in
scope. Installed-runtime rollout, live service changes, unlock, credential
migration and new daemon deployment are not. A reviewed plan grants none of
those effects.

## Plan

### Phase 1 - Reviewed contract
- [x] Review and merge this bounded proposal before implementation.
- [x] Specify activation in Vault's existing configuration namespace, using
  existing qualified configuration owners and portable layers.

### Phase 2 - Implementation and documentation
- [x] Gate automatic bootstrap, first-use/update participation and readiness
  before runtime/backend work; an unconfigured payload remains inert.
- [x] Distinguish missing configuration from malformed, unsafe or refused
  configuration. Preserve explicit configured use, standalone operation,
  installation-cell boundaries and existing service/data custody.
- [x] Document precedence, opt-in and withdrawal semantics; withdrawal does not
  silently stop a service used by another admitted scope.
- [ ] Add a Vault changefile and land source through ordinary review and CI.

## Validation Plan

- [x] Focused resolver/consumer tests: absent, enabled, disabled, malformed,
  qualified knowledge configuration and unavailable/refused owner.
- [x] Disposable native first-touch checks execute the actual payload startup
  and readiness entries with no Vault runtime or KeePassXC; no provisioning,
  service or backend action occurs when inactive.
- [x] Configured controls reach the existing owned consumer path without
  bypassing its checks; existing Vault tests and touched-Python lint pass.
- [x] Install-contract checks report zero violations.
- [ ] Exercise an available isolated external CI lane for platform parity, or
  record the exact unavailable boundary without claiming live qualification.
- [ ] Qualify provisioned-command withdrawal before backend probing, automatic
  cell selection without inherited routing, and accepted activation selectors
  executed under another worker's environment.
- [ ] Record merged source and release status separately from unapproved rollout,
  resolve every checklist item and archive this source effort.

## Proposal

Implementation details are agent-proposed within the captured scope. Reuse
`configuration_context` and `configuration_layers`; do not add a binding parser,
mandatory Worktrees dependency, parallel service owner or global diagnostic
environment-variable policy. Existing native database/service declarations
remain authoritative for their machine-local custody.

## Journal

### 2026-11-02 - Proposal
- Claimed the bounded Vault slice on #2350 after checking open installer PR
  overlap. No source implementation or installed-state mutation has occurred.

### 2026-11-02 - Reviewed implementation
- Proposal #10113 merged after approval and required checks.
- The proposed schema is a strict boolean `enabled`, default false, in existing
  `.agent-vault.json` and native machine configuration. Existing qualified
  four-tier composition determines precedence; no new topology resolver.
- The payload reads activation before bootstrap, installation, readiness or
  ordinary command provisioning. Withdrawal does not stop or delete existing
  services/data. Explicit stop/lock and pinned identity requests retain their
  existing native custody.
- Windows isolated full-suite execution covered both sub-suites: 380 passed
  with 69 skipped in the first, and 323 passed with 97 skipped plus seven
  fixture failures in the second. All seven failures were corrected and
  verified in targeted runs (31 passed/88 skipped, then 5 passed). The
  corrections retain real lock/lifecycle assertions, account for explicit
  opt-in and SDK materialization, and recognize existing external cache flags.
- The focused activation packet passed 166 tests with four platform skips.
  Native first-touch controls include absent/malformed declarations, disabled
  withdrawal retaining existing custody, isolated bundled-SDK reading, and
  actual legacy/namespaced payload commands without a runtime.
- Install-contract checks pass for all 13 adopters; touched Python lint and
  configuration SDK synchronization pass. Existing native-workflow contract
  tests pass 95 cases after replacing a stale last-step assumption with the
  named candidate-construction step.
- The existing on-demand installation workflow now has a bounded
  `vault-activation` portfolio for exact-source Linux and Windows qualification.
  It does not enter required unit-only CI or deploy an installed runtime.
- The first exact-source external packet passed Linux (170 cases) and Windows
  (166 passed/four skips), but review found warm-command, automatic-cell and
  queued-selector gaps. Required CI also refused the portfolio's dynamic runner
  route; the corrected portfolio uses two literal hosted-runner jobs.
- The revision gates the shared runtime-start path and explicit `start` before
  backend probing, carries validated runtime/cell identity into activation,
  shares that dispatch route with readiness and preserves accepted native
  activation selectors through both queued entry points. Canonical helper copies
  and producer metadata are synchronized, not patched independently.
- Revised Windows native controls pass 52 cases with two platform skips,
  including actual provisioned-command withdrawal with transport access
  explicitly forbidden. Two durable request controls pass against a different
  worker environment, including absent selectors. Final external revision and
  review acceptance remain open.
- Required CI and both native jobs passed on the reconciled `db826c9421`
  source. Subsequent review identified relative native configuration after
  self-staging, the preserved project for a no-task start, and use of prepared
  Python by activation. The revision now anchors relative selectors to the
  invoking project, retains both provider/native CWD for the CLI fallback, and
  passes the read-only selected interpreter to activation.
- Four new native controls pass, including actual marketplace self-staging.
  Windows' unchanged installation-context caller requires Python on PATH before
  installation selection; its no-global-Python control therefore qualifies the
  activation reader with owned Python, not unsupported whole-frontdoor behavior.
  POSIX retains a full payload no-global-Python control.
- The final local seven-file packet passes 207 cases with six platform skips in
  four bounded sub-suites. An earlier combined run exceeded its 300-second
  group budget; splitting at two files preserves the existing item/group and
  overall limits rather than increasing deadlines.
- Exact-source native qualification and required CI also passed on `dde5b4a9c3`.
  The remaining review correction applies the same two-file sub-suite shape to
  the on-demand workflow, preserving the existing runner budgets.
- Required CI on the next revision exposed a newly introduced canonical SDK
  module missing from Vault's bundled copy. Reconciliation and SDK regeneration
  restore that module; all 13 install-contract adopters pass.
- Queued execution now retains the accepted credential-free configuration
  projection as well as native selectors, clearing each when absent rather
  than inheriting another worker's environment. Both real queued controls pass
  with present and absent projections. Fresh native qualification, required CI
  and review remain required on the published revision.
- The reconciled revision passed required CI and native Linux/Windows
  qualification. Review then identified an inactive POSIX installer return
  before argument validation. The parser now runs before activation and staging,
  retaining original arguments for staged re-execution. Six native controls
  cover unknown options and missing values independently of disabled, enabled
  or malformed configuration; final revision qualification remains open.
