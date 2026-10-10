# Machine Transport Convergence

- **Slug:** `machine-transport-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serially landed per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/agent-fabric/README.md`, derive-don't-duplicate and graceful composition
- **Sub-issues:** #5737 · #5738 · #5740 · #5741 · #5972 · #5973 · #5974 · #6039

## Guiding Intent

Every named-machine operation should share identity matching and transport
selection rather than independently interpreting aliases, casing, or locality.
Preserve provider-specific launch protocols and standalone installation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign owner | Plan, integration, validation, and serial PR stewardship | Managed upstream worktrees |
| Evidence delegates | Read-only consumer-boundary assessments | Bounded task calls; reports integrated by campaign owner |

## Coordination

- **Topology:** Reviewed plan first, then independent per-slice PRs.
- **Host (owns PRs):** Campaign owner.
- **Delegates:** Evidence only; no shared-worktree git or edit authority.
- **Handoff:** Resume the first unresolved item from this canonical README.
- **Concurrent work:** #5689 introduces shared host/WSL execution identity for
  agent-machines and agent-worktrees. Do not supersede its still-open change.
  It must land before the Dispatch and agent-machines integration slices; it
  must not wait for those slices or for the independent Bridge/publication
  repair. The original PR owner retains implementation and merge stewardship.
  The negotiated direction retires the duplicate `machine-identity` library:
  shared matching, locality and host/WSL qualification live in `machine-transport`,
  with consumer-specific discovery and diagnostics in thin adapters.
- **Additional consumer slices:** #5972 covers Worktrees and Picker decisions,
  #5973 covers SSH WSL/mesh identity, and #5974 reviews Index designated-host
  routing. The campaign owner reserved #5972 before implementation; filing the
  other two issues does not reserve them. Do not modify peer-owned claimant,
  handoff or host/WSL paths while #5689 remains open. Independent Picker
  identity matching can proceed after this extension clears review, without
  changing the peer's execution-identity contract.

## Context

Phase 1 landed in #5687 after #5634: `libs/machine-transport` now owns normalized
registry entries, alias matching, canonical local-machine checks, SSH target
selection, and generic POSIX login-shell wrapping for agent-worktrees and
worktree-manager. Consumer-specific topology path discovery remains outside it.

Initial read-only evidence identifies these boundaries:

- Bridge duplicates topology parsing and identity lookup. Its ACP argv,
  breadcrumbs, forwarding, authentication, and Windows quoting remain local.
- Dispatch lowercases a requested machine into an SSH target; configured machine
  aliases and remote shell selection need the shared resolution boundary.
  Endpoint discovery, forwarding, health checks, and retries remain local.
- Codespaces' `_ssh_namespace()` assembles options for its dynamic GitHub venue
  provider. It does not resolve a `machines.yaml` machine and must not be forced
  into that registry.
- Agent-machines overlaps canonical key/alias/hostname/display-name matching,
  but also returns package-gate identities and diagnostics. #5689 is already
  changing that contract; the two shared libraries must not become duplicate
  identity authorities.

## Request

Public-safe operator request:

> "We'll want to make sure that across all agent-* commands which perform
> operations on machines, we uniformly use the same flow... we can produce a
> reusable resolver module that everything can share, and individually validate
> it... One place to resolve aliases, casing, etc., used everywhere."

Continuation:

> "Since we have context, let's tackle the broader objective"

Dispatch integration decision:

> "Use optional shared resolver CLI with standalone alias compatibility"

The operator selected a read-only agent-worktrees transport-resolution command
backed by the shared library. Dispatch consumes it when available and preserves
raw SSH-alias operation only when the optional provider is unavailable.

Publication-blocker decision:

> "Fix the tooling blocker, then continue"

Additional Logger boundary decision:

> "May I file this additional convergence issue and add its proposal to the
> campaign for review, leaving runtime implementation blocked on the shared
> contract?"

The operator accepted: `true`. This authorizes tracking and design review;
it does not waive the original shared-contract prerequisite or authorize
unreviewed changes to Logger's native/WSL compatibility behavior.

## Plan

### Phase 1 - Review the campaign boundary
- [x] Land this proposal through automated review before implementation.
- [x] Coordinate with #5689 on shared identity ownership and avoid overlapping edits.

### Phase 2 - Named-machine consumers
- [x] Repair the owned-PR publication blocker #5796, then resume the original
  consumer work. _(operator-approved prerequisite)_
- [x] Resolve #5737 by migrating Bridge registry and named-machine resolution;
  preserve ACP launch construction and compatibility metadata.
- [ ] Resolve #5738 by migrating Dispatch named-machine SSH resolution; preserve
  endpoint/tunnel behavior and explicit diagnostics.
- [ ] Resolve #5741 by integrating the reviewed host/WSL identity contract with
  transport identity, or record a justified distinct contract.

### Phase 3 - Provider boundary and completion
- [x] Resolve #5740 with a documented dynamic-venue boundary, or migrate only
  independently proven named-machine overlap.
- [x] Capture the additional inventory slices under #5972, #5973 and #5974 with
  operator approval; keep provider-specific contracts distinct.
- [ ] Resolve #5972 by sharing Worktrees/Picker machine matching, locality and
  SSH selection, or review a justified distinct contract for each candidate.
- [ ] Resolve #5973 by sharing WSL/mesh host identity while retaining SSH
  aliases, live provider projection, keypair selection and native/guest venues.
- [ ] Resolve #5974 through a reviewed Index designated-host identity boundary
  that preserves explicit indexer configuration and standalone operation.
- [ ] Complete the independent design review/merge gate for #6039's Logger
  boundary proposal; this gate does not wait for #5689.
  _(agent-recommended inventory finding; operator-approved tracking/review)_
- [ ] Resolve #6039's Logger ramp-up execution-locality and named-machine SSH
  implementation after the shared contract lands; preserve
  session-corpus filtering and standalone operation.
  _(agent-recommended inventory finding; operator-approved tracking/review)_
- [ ] Inventory other agent-* named-machine operation paths for remaining
  duplicated identity/transport resolution, including unresolved generated
  installer-context source ownership, shell-only paths and provider argv.
  _(agent-recommended verification of the requested all-consumer objective)_
- [ ] Land any remaining proven overlap, or transfer a specifically bounded
  item to a named tracked objective with explicit rationale.
- [ ] Complete deployment evidence, journal outcomes, and mark the effort Done
  only after every implementation and validation item is resolved.

## Validation Plan

- [ ] Per migrated consumer, run existing relevant contracts before edits and
  targeted regressions plus the full affected suite after edits.
- [ ] Prove key/alias/hostname/case matching, known-local direct operation,
  missing/invalid registry diagnostics, ambiguous identities, and remote shell
  selection without changing provider namespaces or ACP semantics.
- [ ] Run shared-library regressions for changed shared contracts; validate
  standalone dependency staging, release materialization, and pip-only fallback
  paths on both supported platforms.
- [ ] Exercise a lightweight real subprocess/SSH-command construction contract
  for each changed transport path, not only mocked resolver returns.
- [ ] Run applicable fresh-install clean-room and live venue checks when
  available; record explicit reasons for any unavailable tier.
- [ ] Pass touched-code lint, install-contract and relevant repository guards;
  add changefiles, complete documentation-impact review, and satisfy automated
  review plus required CI before each merge.
- [ ] Verify merged provider state, refresh installed consumers through the
  unified update flow after promotion, reconcile harness projections, and
  finalize only settled worktrees.

## Proposal

Consolidate identity and transport decisions, not unrelated launch protocols.
Keep path discovery and caller-specific errors at the consumer edge. Preserve
existing public interfaces through thin adapters where needed. Reject a second
identity authority: reconcile `machine-transport` with #5689's
`machine-identity` before changing the overlapping consumer.

### Additional consumer boundaries and sequencing

The bounded inventory found Worktrees' claimant SSH resolver, fleet listing,
launch helpers and roster locality; Picker source construction and roster
locality; SSH WSL selection and mesh host identity; and Index designated-host
read routing. This is candidate coverage, not a claim that every operation was
inspected. Generated installer-context references were excluded from source
reads and their canonical owner was not resolved, so they are not automatically
classified as receipt-only. Shell-only paths and some provider argv remain open
inventory gaps.

The first independent #5972 slice is strict launch/Picker identity matching in
`resolve_machine_cli.py`, reusing the shared matcher while preserving SSH
environment selection, public plan shapes and command construction. Claimant,
handoff, fleet/roster and native/guest locality integration must compose #5689's
reviewed contract rather than pre-empt it. A partial Picker slice does not close
#5972 or the whole campaign.

For #5973, stable SSH target aliases and rotating dtssh endpoints remain a
provider projection, not execution identities. For #5974, review whether the
configured indexer designation is a machine identity or a distinct provider
contract before choosing a portable shared boundary. Do not introduce another
topology configuration or silently turn a real resolver error into an optional
provider's absence.

### Index designation boundary proposal (#5974)

The Index read router has two different inputs, which must not be collapsed:
`indexers[].machine` designates the indexer execution host, whereas
`indexers[].ssh` is an explicit transport target. The latter may select WSL,
a tunnel, or another SSH configuration; it is never a machine-identity alias.
`AGENT_INDEX_MACHINE` is also an explicit designation override. The existing
SSH command forwards that override together with a one-indexer configuration
to terminate routing at the selected destination. Preserve this compatibility
contract, including standalone commands and worker-record labels; do not
reinterpret that forwarded value as evidence of physical-host identity.

Implement the following boundary only after this proposal clears review and
the original #5689 owner lands the shared execution-identity contract:

1. Use the vendored `machine-transport` execution-identity resolver for native
   versus WSL qualification, including standalone operation without topology.
   An explicit, non-empty `AGENT_INDEX_MACHINE` remains a designation override
   and matches the configured designation literally, case-insensitively; the
   remote handoff must not invoke optional topology discovery to terminate.
   Keep `machine_id()`'s existing public override/worker-label behavior. A
   separate routing helper owns execution-locality decisions.
2. When no override is present, optionally compose Worktrees' topology through
   an owner-attributed, read-only process boundary. Add a narrow batch identity
   capability to Worktrees rather than asking Index to locate or parse
   `machines.yaml`, import a sibling runtime, or infer identities from Picker
   launch plans. Worktrees owns configured local identity, registry layers and
   platform detection; the shared library owns matching and guest qualification.
   Index owns indexer selection, SSH/HTTP routing and error rendering.
3. The proposed version-1 batch contract receives the designation strings and
   returns the current execution key/platform plus one ordered resolution per
   input: canonical key, accepted identity labels, known-versus-explicit status
   and diagnostics. It performs no network request, provisioning, config write,
   session binding or claim. Do not dump unrelated topology or use a human
   display label to choose between native and guest execution keys.
4. Discover the logical Worktrees command through the existing attributed
   command resolver. A genuinely absent command or explicitly unsupported
   capability retains standalone shared-library routing; older providers must
   advertise unsupported capability without confusing an ordinary command
   failure with absence. A present provider's malformed response, timeout,
   unreadable configuration or ambiguous identity fails visibly. No broad
   exception-to-literal fallback and no PATH/same-name-provider substitution.
   Take one validated batch snapshot per routing decision.
5. Known local/remote designations compare canonical execution keys, never just
   physical hostnames. Unknown designations retain their explicit spelling and
   diagnostics, not a fabricated topology entry. Standalone matching supports
   the existing short-hostname/case contract and shared WSL qualification;
   key/alias/declared-hostname enrichment is available only through topology.
   An explicit SSH target remains byte-for-byte selected by Index, even when
   its designated machine resolves to a different canonical key.
6. Use the same routing helper in `plan_route`, `maybe_delegate`, `client_url`
   and setup's single/plural designation checks. Preserve ordered indexers,
   local service selection, client-local endpoint precedence, SSH-only corpus
   isolation, connection-only failover and remote command exit semantics.

The read-only provider is a proposed extension, not an already-shipped command.
The existing Picker environment-plan boundary is unsuitable: it depends on SSH
environments and produces a launch plan, not complete execution-locality
evidence. The legacy shared locality helper also swallows selected registry
errors; new Index routing must use the strict execution-identity contract
instead of treating such errors as optional-provider absence.

Validation must cover the existing explicit override and the actual forwarded
SSH child, standalone native/WSL distinction, local and secondary indexers,
topology key/alias/hostname/case equivalence, ambiguous labels, unknown targets,
exact SSH target preservation, absent/unsupported versus broken provider,
one-snapshot consistency and setup/read/HTTP agreement. Run the client-only
packaging/installer contracts for the new vendored dependency on both platform
lanes, a fresh standalone subprocess with no sibling present, and an available
live venue before claiming deployment. The proposal itself changes no runtime.

### Logger ramp-up boundary proposal (#6039)

Logger's ramp-up command is another named-machine consumer, not merely a
transcript label producer. Its local check strips `-wsl` and accepts either
hostname as a prefix of the other before choosing local session discovery or
SSH delegation. The existing test explicitly preserves native/WSL equivalence.
Separate that compatibility surface from execution locality: same physical
host does not prove access to the requested native or guest session store.

After this proposal clears review and the original #5689 contract lands:

1. Use shared execution identity for known-local versus remote decisions.
   Preserve `detect_machine()`'s public transcript/worker labels unless a
   separately reviewed compatibility change requires otherwise. Legacy
   worktree designation prefixes remain session-record filters, not proof of
   execution locality.
2. Compose optional topology through an attributed read-only process boundary
   and the shared matcher/transport resolver. Do not add a Logger topology
   parser, a mandatory sibling import, or a PATH-based same-name provider.
   A genuinely absent or explicitly unsupported provider retains standalone
   explicit SSH-alias operation. A present provider's ambiguity, configuration,
   schema or timeout failure is visible, never literal-alias fallback.
3. Keep session discovery, explicit `--session` behavior, local-path conflicts,
   suffix filtering, remote argument forwarding and child exit status owned by
   Logger. Do not rewrite explicit SSH targets as physical-host identities.
   Resolve one coherent identity/transport snapshot for each routing decision.
4. Prove native/guest separation, exact/case/declared aliases, hostname-prefix
   collisions, unknown aliases, absent versus broken providers, legacy suffix
   filters, explicit local paths and `--session`, real resolver-to-SSH argv,
   exit semantics and fresh standalone packaging on both platform lanes.

This proposal changes no runtime. Filing #6039 does not reserve its
implementation or claim final all-consumer inventory completeness.

## Journal

### 2026-10-08 - Planning
- Resumed the Phase 2 objective and claimed #5737, #5738, #5740, and #5741.
- Integrated four bounded, read-only consumer assessments.
- Found concurrent #5689; recorded the dependency before implementation.
- Operator confirmed the effort slug `machine-transport-convergence`.

### 2026-10-08 - Reviewed proposal and Bridge implementation
- Proposal #5753 merged into `dev`; cooperation with #5689 was recorded before
  implementation. Its machine-identity, agent-machines and agent-worktrees
  implementation paths remain untouched.
- Prepared the #5737 Bridge slice for campaign-owner integration/publication,
  not issue completion. Shared raw-data parsing preserves Bridge's missing
  SSH-alias/key and shell/bash defaults, unnamed environments and extended
  metadata. Shared matching adds case-insensitive machine alias, hostname and
  display-name resolution; ambiguous identities/SSH aliases now fail explicitly.
  SSH-alias environment binding and conflicts, platform-specific local
  loopback, default environment preference and ACP launch shapes remain
  consumer-owned.
- Both installer platforms refresh the shared dependency. Windows standalone
  snapshots stage it and rewrite its nested login-shell pointer; an actual
  isolated PowerShell snapshot test asserts that contract. Bridge requires `uv`
  on both platforms and has no pip-only fallback to modify.
- Focused validation:
  `python tools/run-plugin-tests.py agent-bridge -k 'topology or bare_name_resolution or test_agent_registry or test_transport or machine_transport_convergence or install_stale_cache_guard or installer_powershell51' --plugin-timeout 900 --subsuite-timeout 300`
  — **356 passed, 1 skipped**.
- Full validation:
  `python tools/run-plugin-tests.py agent-bridge --plugin-timeout 2400 --subsuite-timeout 600`
  — **3429 passed, 66 skipped**, all eight contained sub-suites green.
  The first attempt's 300-second file-group budget expired on Windows;
  the same group completed in 365.61 seconds within the bounded retry.
- Shared source validation:
  `.test-venvs/win32/agent-bridge/Scripts/python.exe -m pytest libs/machine-transport/tests -q --basetemp=.test-state/machine-transport --timeout=30`
  with `PYTHONPATH` pointing to this checkout's `libs/machine-transport/src`
  and `libs/remote-login-shell/src` — **73 passed**. The initial missing
  scratch-parent setup error was corrected before this successful run.
- Touched Python `ruff check --select F,E9`, `check-install-contract.py`
  (**13 plugins**), `check-module-size.py`, `check-vendored-libs-sync.py`
  (**5 shared libraries**) and staged/unstaged `git diff --check` passed.
  A patch changefile covers Bridge and both existing shared-library consumers.
- Lightweight integration exercises the real resolver-to-ACP-command
  construction, including auth metadata and breadcrumb-bearing command
  equivalence. Full fresh-box clean-room and live SSH tiers were not exercised:
  no disposable target or live venue was assigned, and this slice does not
  authorize host installation/service changes.
- Documentation impact: updated the shared-library API/compatibility contract
  and Bridge's machine-configuration guide. This realizes the reviewed
  derive-don't-duplicate intent without changing the vision, installation
  independence or daemon lifecycle contract. Self-reviewed against `REVIEW.md`.
  No commit, PR, push, merge or deployment was performed; #5737 remains open
  until parent-owned publication and merge.

### 2026-10-08 - Bridge review and next-slice decision
- Automated review found that registry assembly still used key-only coverage
  checks. Coverage now reuses the same resolver, including SSH-environment
  binding, so a differently named explicit local project suppresses its
  auto-discovered duplicate under alias, hostname, display-name or SSH-alias
  spellings. Ambiguous/conflicting hosts warn rather than suppressing evidence.
- Merge-path regressions and related coverage contracts: **59 passed, 1 skipped**;
  touched-code lint and module-size gate passed.
- Operator chose the optional shared-resolver CLI for Dispatch, preserving
  standalone SSH aliases without introducing a duplicate topology configuration.
- Additional read-only inventory identified agent-ssh's WSL registry emitter as
  remaining local-machine matching overlap. Its transport-specific projection
  remains outside the shared resolver; the dtssh alias default and mesh-status
  projection require boundary checks, not indiscriminate migration.

### 2026-10-08 - Provider boundary landed; publication repair
- #5834 merged into `dev` and #5740 was explicitly closed. Codespace discovery
  and `_ssh_namespace()` are a dynamic venue/options contract, not a static
  machine resolver. The clarification preserves existing behavior without a
  redundant dependency; all provider checks and automated review passed.
- Bridge's subsequent review corrections reject ambiguous local hostnames and
  decouple parser defaults from field normalization. Shared regressions:
  **76 passed**; Bridge coverage: **60 passed, 1 skipped**.
- Supported rebase reconciliation exposed #5796: publication rejects a rebased
  owned PR despite an exact remote-head lease. The operator authorized a
  separate source repair, which remains in review as #5825. Bridge publication
  is blocked on that repair; no guard bypass or deployed-code edit was used.
- The repair's actual blocked-branch lineage verified read-only, including the
  explicit conflict continuation. Real-Git tests cover leases, lost-work
  refusals and hooks; review added raw-byte patch/message preservation and an
  opt-in exhaustive lane. Default smoke measured **6 passed in 70.81 seconds**
  on Windows. A fresh consoleless-parent observation exposed an unguarded core
  Git spawn; the corrected path completed two cycles with zero newly visible
  windows and foreground transitions. Combined changed contracts:
  **127 passed, 15 skipped**.
- Dispatch's optional resolver CLI remains unimplemented until ambiguity and
  native/WSL execution locality are reconciled with #5689. Its still-open review
  findings are not superseded by this campaign.
- Further evidence shows the mesh parser also feeds operational refresh:
  #5375 already tracks agent-ssh's self-SSH probe loop. Include that path in the
  remaining inventory rather than treating the mesh parser as read-only only.

### 2026-10-08 - Dependency order, shared identity and source repair
- The #5689 owner had paused in draft because rebasing exposed the duplicate
  identity library and they believed Phase 2 had to land first. Explicit
  prerequisite comments corrected the order before implementation resumed.
  Negotiation between the campaign owner and the identity-PR owner agreed
  direction (1): retire the standalone duplicate and compose the host/WSL
  execution contract with the existing `machine-transport` identity primitives.
  No separate #5741 design PR blocks #5689. Actual WSL evidence, native-host
  precedence and the distinct SSH namespace remain required.
- Remaining inventory includes Worktrees' picker/handoff helpers in
  `resolve_machine_cli.py`, which still repeat first-match identity logic, and
  agent-ssh's WSL emitter, which uses key-prefix hostname heuristics. These
  are not silently counted as completed migrations.
- #5382 was confirmed merged into `dev` as
  `d841b53f8fd882cacc6c91bccf7d36ad9cccc332`; #5375 was explicitly closed.
  Its physical-host self-probe exclusion is a landed bug fix, not proof that
  every agent-ssh identity consumer has migrated to the shared authority.
  Promotion/deployment remain separate. Its originating worktree has
  uncommitted changes and is retained, not discarded or finalized.
- #5825 review corrections fingerprint credential-bearing push destinations,
  disable replacement objects in proof and strict ancestry checks, and replace
  patch-ID authorization with per-commit three-way tree reconstruction.
  Repeated-region tests distinguish legal upstream line shifts from relocated
  edits and false already-applied matches. Default proof contracts:
  **10 passed, 16 skipped**; explicit conflict/cache contracts: **3 passed**.
  The actual blocked Bridge lineage still verifies with one conflict replay.
  The full updated matrix and current-head review remain pending.
- A later runtime slot was marked installed while its shared transport package
  had only bytecode cache. The operator authorized official recovery; the
  installer refused another live build lease. Waiting for that builder restored
  CLI execution without clearing ownership, editing a runtime or forcing a
  service restart. This does not establish overall monitor/deployment health.
- Operator priority is to settle owned #5382 and #5825 next. The former is
  confirmed merged; the latter remains the publication prerequisite.
  This effort stays Active and its remaining implementation/deployment gates
  remain open.

### 2026-10-09 - Publication repair merged; deployment pending
- #5825 squash-merged into `dev` as
  `3743ff99012541f7cf33754ec1ad21501e3caad1` after current-head automated review
  reported zero findings and required CI cleared. The review's recommendation
  for a final human security-sensitive review remains advisory, not a claim of
  human approval.
- Final exhaustive Windows publication matrix: **27 passed, no skips** in
  1103.10 seconds, including real Git leases, per-commit tree reconstruction,
  conflict continuation, inherited Git-context isolation, real pre-push hooks,
  and native headless observation. Two fresh native observation cycles showed
  zero newly visible windows or foreground transitions.
- Linux representative contracts ran in required CI. Exhaustive Linux remains
  an explicit post-promotion gate: its new manual workflow was unavailable on
  the default branch before promotion, so no exhaustive Linux pass is claimed.
- Promotion to `main`, unified installed-consumer refresh, harness projection
  reconciliation and actual Bridge publication remain outstanding. The repair
  source worktree is retained until those live-consumer obligations settle.
- #5689's owner acknowledged direction (1) and resumed their original PR's
  rebase and validation. No additional coordination response is needed while
  they work; dependent Dispatch and agent-machines slices still wait for it.
  This campaign remains Active.

### 2026-10-09 - Promoted repair and real consumer publication
- The repair reached `main` in release #5901 as
  `db57da73b5ff9795380e9dec2d349861fba5c79a`. Unified `update --force`
  refreshed installed plugin payloads. Runtime installation initially refused
  another live build lease; that builder subsequently completed, and the
  catalog command selected `agent-worktrees 1.24.32-dev1` without a forced
  lease clear or runtime edit.
- The deployed command successfully published the actual blocked Bridge
  source-owned rebase to #5765. A subsequent provider read confirmed the new
  head, so this is live-consumer evidence, not only a read-only proof.
  A later sync against current `dev` exposed only an effort-index conflict;
  reconciled current upstream rows with this campaign's Active status and
  retained a pre-rebase backup. Rebased Bridge contracts:
  **242 passed, 1 skipped**.
- Consumer harness projection reconciliation completed through its own
  reviewed pull request.
  Its fifteen-file diff changes only projection provenance versions and their
  derived hashes; installed templates and guidance behavior remain unchanged.
  Local synchronization and harness lint passed; review recommended approval
  with zero findings. Required CI passed, the synchronization merged, and its
  dedicated worktree finalized through the normal lifecycle.
- The new exhaustive workflow's first live run, `37914963861`, failed on both
  platforms during action setup, before running tests: `setup-uv@v9` does not
  resolve. Follow-up #5905 uses the verified `v9.0.0` tag already used by the
  successful release pipeline. Both-platform external exhaustive evidence
  remains unresolved until that fix is reviewed, promoted and executed.
- No broader completion is claimed. Bridge review/merge, the exhaustive lane,
  dependent identity consumers, remaining inventory and deployment evidence
  remain campaign gates.

### 2026-10-09 - Bridge current-head review corrections
- Remote operations now require one case-insensitive exact SSH-environment
  alias for every multi-environment machine, including uppercase keys,
  machine aliases and hostnames. No machine-identity spelling silently chooses
  a default environment for these operations. Real-resolver regressions prove
  rejection and exact-alias acceptance; legacy key-as-environment-alias
  precedence remains covered in both cases.
- Local project coverage now selects the same default environment as spawning.
  Loopback metadata uses that same selection too. Merge-path regressions cover
  omitted environments, while native Linux versus default WSL checks prove
  that remote execution does not incorrectly suppress a local project.
- Removed unnecessary account-profile qualifiers from the earlier public
  journal entry; coordination records identify participants by campaign roles.
- Targeted remote-operation and convergence contracts: **91 passed, 1 skipped**
  before the final additional default-WSL and uppercase-key assertions.
  The subsequent full Bridge run passed all eight contained sub-suites,
  including those assertions, with platform skips reported by the runner.
  Touched-code lint, exact module ceilings and whitespace checks passed.
- #5905 merged as `867cc20eaa8faad3c6be62e80a144f29372f172b`.
  The follow-up promotion and corrected exhaustive live run remain outstanding.

### 2026-10-09 - Preserved metadata compatibility and release dependency
- Resolver indexing ignores preserved non-string SSH aliases with an explicit
  warning, and remote-operation alias matching treats null/non-string values as
  non-matches. Local coverage normalizes only its comparison value for an
  unnamed environment; source metadata remains untouched.
  New real-parser regressions cover null/numeric aliases and an unnamed
  environment's valid SSH alias. Related Bridge contracts:
  **308 passed, 1 skipped**. Touched-code lint and whitespace checks passed;
  the existing remote-operation module ceiling is retained without widening.
- Removed downstream repository/account references from the public journal;
  consumer deployment evidence remains expressed in generic completion terms.
- Promotion run `37917583420` failed its full agent-worktrees job on the real
  POSIX lease-wait timeout: a two-second budget returned after 0.7657 seconds.
  Existing #5907 tracks the failure and #5911's owner is implementing the
  wrapped-IOException correction. The already-merged #5892 adds native error
  codes but does not itself unwrap the exception. Posted this release impact
  to #5911; do not duplicate the owner's patch or bypass/retry the failed gate.
  Corrected external exhaustive validation remains blocked on that tracked
  remediation and successful normal promotion.

### 2026-10-09 - Additional inventory approved and tracked
- The operator approved tracking and continuing the three additional slices.
  Created #5972, #5973 and #5974 after duplicate checks, and verified every
  posted title, full body and label. #5973 is explicitly a consumer child of
  the existing #5674/#5689 contract, not a competing identity fix.
- Reserved #5972 atomically in the canonical issue queue and published a
  codename-safe issue claim; the immediate thread re-read found no competing
  claim. The other two issues remain unclaimed backlog entries.
- A bounded source inventory also found already-shared locality paths and
  distinct container, logging, cache and network-URL provider contracts.
  An initial incorrect Picker source root was corrected by a narrow read of
  the actual repository-root Worktree Manager source. Uninspected generated
  installer-context references remain an explicit evidence gap.
- This plan extension must land before the independent Picker implementation.
  Overlapping host/WSL consumer work still waits for #5689's original owner.
  The release correction was owned by #5885, not duplicated here; its landed
  receipt and still-open promotion evidence are recorded below.

### 2026-10-09 - Release remediation landed; deployment evidence open
- The original owner merged #5885 into `dev` as
  `3b1cbe29706cd8494157b06a5873d8dbb25895b9` and closed #5911 with a
  merge receipt. The wrapped-I/O source blocker is resolved; do not resume its
  implementation or treat the old failure entry as the current remediation state.
- The owner's required guard and native platform-classification evidence passed
  before merge, without weakening the real bounded-wait assertions.
  Normal promotion is running; successful promotion and the applicable
  installed-consumer refresh still need separate verification. Source merge
  does not close the campaign's deployment evidence.

### 2026-10-09 - Distinct ambiguity error contract
- The latest review identified a previously missed API mismatch: strict
  configured-identity ambiguity was mapped to `404 host_not_found`.
  `machine-transport` now exposes `AmbiguousMachineError` as a `ValueError`
  subtype. Bridge uses that same signal for SSH-alias/cross-namespace
  collisions and translates it through its existing remote-error module to
  `400 ambiguous_host`; genuinely missing hosts retain `404 host_not_found`.
  Existing consumers catching `ValueError` remain compatible.
- Real-resolver remote regressions cover colliding machine aliases, hostnames,
  display names and SSH aliases, plus missing-host preservation. Related Bridge
  contracts including standalone installer staging:
  **319 passed, 1 skipped**. Shared-library regressions: **76 passed**.
  The first shared run lacked its scratch parent; creating that parent resolved
  the setup-only failure before the successful run. Touched lint, exact module
  ceilings and whitespace checks passed; no baseline was widened.

### 2026-10-09 - Bridge merged and both-platform publication evidence
- #5765 merged into `dev` as
  `d7c22454cf114a06d2e897ff57baab29f51eb89a` after current-head approval
  with zero findings, no unresolved threads and green required CI.
  #5737 was explicitly closed. This settles the source migration, not its
  promotion/deployment or the campaign's other consumers.
- Corrected exhaustive publication workflow run `37923323675` passed against
  the exact merged `dev` source
  `40667090654f2d3264f0d7a91c585b0f2a044b0e`:
  Windows **27 passed, no skips**; Linux **26 passed, 1 skipped**.
  The skipped contract is Windows-native headless observation; its Windows
  counterpart ran. The read-only lane used the corrected workflow on `dev`
  because its merged feature ref was deleted and normal promotion is blocked.
  This proves the external exhaustive contracts, not default-branch deployment.
- The continuing campaign worktree was pulled past the squash without losing
  post-merge work. Cached PR metadata caused ordinary reconciliation to
  replay already-merged commits; before a zero-slice rebase, independently
  reconstructed the exact aggregate merge tree and verified equality with
  the provider's squash commit. Pre-reconcile backup refs were retained,
  there were zero later commits to replay, and normal `pr-complete` then
  reported current with `origin/dev`.
- Dispatch and agent-machines integration still wait for the original #5689
  identity owner. Remaining inventory and normal promotion/deployment evidence
  are open; #5911's source remediation subsequently landed through #5885.
  No duplicate peer patch or release bypass is authorized by this journal.
  The effort remains Active.

### 2026-10-09 - Reviewed consumer extension and independent Picker slice
- Plan extension #5976 merged into `dev` as
  `314b3ac189b92fc193628806fe7b2aa4cd982f29` after current-head approval
  with zero findings and green required CI. The independent #5972 slice now
  uses shared strict matching for launch/Picker identities, preserving eligible
  handoff-target filtering, environment selection and public command-plan shapes.
  Peer-owned claimant, handoff and host/WSL paths remain untouched.
- Existing baseline contracts passed **9 tests** before edits. The expanded
  consumer run passed **229 tests, 12 skipped**, including a real headless
  subprocess using YAML parsing and the public five-field JSON plan.
  Ambiguous identities explicitly refuse to emit a plan; exact keys retain
  precedence and unknown/offline display identities preserve compatibility.
- Touched-code lint and the headless-spawn guard passed. The full affected
  suite used the documented 120-second per-test allowance:
  an unchanged real Git-heavy handoff contract exceeded the default 30-second
  Windows watchdog, then passed in **66.16 seconds** with that allowance.
  No production assertion, peer-owned source or module ceiling was weakened.
- The full run then exposed a stale publication-lock test double: the earlier
  publication repair passes `push_timeout_seconds`, but the double accepted
  only the worktree path. It now accepts and explicitly asserts a non-default
  configured deadline while retaining the lock-error assertion. Combined
  publication and Picker contracts passed **55 tests**. On publication,
  independently merged #5989 already contained the stronger parametrized
  deadline regression; rebasing retained that upstream file verbatim instead
  of publishing a duplicate fixture fix.
- A later full run passed its first five file groups, then two real Bash
  setup-child contracts failed because the selected shell could not find its
  POSIX utilities on the host PATH. Selecting the existing Git Bash directory
  through process-local PATH resolved both without code, host configuration
  or assertion changes. Current rebased Picker, publication-deadline and real
  setup-child contracts passed **28 tests**. Full-suite evidence remains open.
- Normal promotion run `37984196435` succeeded. The unified installed-consumer
  refresh completed, with Worktrees `1.24.36-dev1` and Bridge `0.9.41.dev1`
  verified. Consumer projection reconciliation is complete after its required
  CI's separately tracked
  fixture lease-isolation failure was repaired and the reviewed reconciliation
  merged with green required CI.
  No broader service-health claim follows from this refresh.

### 2026-10-09 - Generated installer-context boundary clarified
- `tools/sync-installation-context.py` identifies `libs/installation-context`
  as the canonical owner of the generated script and Python-package copies.
  The earlier source-absence observation was a discovery error, now corrected.
- Canonical directory-lock and maintenance-sidecar checks compare physical
  hostnames to decide local PID liveness and ownership; source URL normalization
  compares Git network hosts. These are distinct local process-owner and URL
  contracts, not registry aliases or named-machine SSH routing. Keep them
  independent of transport identity. No installation-context code was changed.
- This closes that bounded source-ownership inventory gap, not the remaining
  shell-only and provider-argv inventory or the whole campaign.

### 2026-10-09 - Picker current-head review corrections
- Review of #6002 found that strict shared matching mixed case-insensitive
  keys with alias/hostname/display matches. Strict matching now resolves a
  unique case-insensitive key first; genuinely colliding keys remain ambiguous
  unless the input exactly spells one key. Non-strict first-match behavior is
  unchanged. Key-versus-identity regressions cover both insertion orders.
- Environment-specific JSON resolve ambiguity now emits the existing versioned
  error envelope on stdout instead of leaving stdout empty. A real headless
  subprocess proves nonzero status, the exact envelope and clean stderr.
  Both review regressions failed against the prior head before correction.
- Corrected consumer contracts passed **52 tests**, shared-library regressions
  **84 tests**, related Bridge contracts **447 tests, 1 skipped**, and the
  standalone Picker locality contract **13 tests**. The first shared test run
  used mutation on a frozen fixture; constructing a replaced dataclass fixed
  that test-only error. Touched-code lint passed.
- The previous full run was stopped before editing after these review findings;
  completed file groups passed, but that is not full-suite success. Publication,
  fresh current-head review, required CI and the corrected full affected suites
  remain gates. Shared consumer release intent covers Worktrees, Bridge and
  Worktree Manager; no peer-owned host/WSL code was changed.

### 2026-10-09 - Preserve handoff environment eligibility
- The next review identified that matching retained eligible machine entries
  but discarded their filtered environment lists. A local Windows machine
  could therefore select its excluded native target instead of its eligible
  WSL target. Matching now uses immutable entry copies carrying only the
  loader-returned environments; the registry and locality filter are unchanged.
- Four key/alias/hostname/display regressions using the actual loader failed
  against the prior head and now select the guest SSH target and POSIX wrapper.
  They also prove the original registry entry remains unchanged. Combined
  matching, envelope and seed-delivery contracts passed **56 tests**.
  Touched-code lint passed; fresh full validation and review remain required.

### 2026-10-09 - Full-suite cold-start correction and tracked exception
- Full affected validation exposed a pending-generation staging failure in
  Bridge. Real subprocess instrumentation showed stable process identities but
  two writers exhausted the unchanged two-second lock-admission budget while
  cold liveness-package imports ran inside another writer's critical section.
  Preparing that callable before acquiring the lock reduced measured locked
  work without moving live PID checks, pruning or writes outside the lock.
- A fresh-process import observer failed against the old code and passes with
  the correction. The existing eight-process generation round trip, PID-reuse
  rejection and exactly-once consumption assertions are unchanged. Runtime
  version contracts passed **21 tests, 1 skipped**.
- The credential-pin race fixture now synchronizes each competing pair and
  stubs ambient account discovery. It still requires all ten real Git writes
  and final helper/username consistency, without assuming native-lock fairness
  over repeated acquisitions. Credential-pin contracts passed **24 tests**;
  production locking and timeouts are unchanged. Touched-code lint passed.
- Full and focused Windows descendant-cleanup failures match the existing
  accepted gap #4644. The repository's documented tracked-flake exception
  permits excluding that exact contract; do not disguise it as a passing test
  or bundle an unvalidated process-management change here. Final affected-suite
  validation excludes only `test_run_bounded_kills_grandchild_on_timeout`;
  no other observed failure is waived. Current-head review and CI remain gates.

### 2026-10-09 - Retained validation batches and publication-release proof
- Complete Bridge validation passed all nine contained file groups:
  **3782 passed, 72 skipped**. Worktrees' first seven groups passed, and two
  additional files completed before an interruption. Two short peer review-fix
  suites received explicit admission gaps; completed evidence was retained on
  unchanged source instead of restarting it.
- Negative pytest selection collapsed the runner's normal file partitioning;
  exact node deselection restored it. Large real-Git groups still exceeded
  group wall budgets while individual contracts kept passing. Remaining
  validation runs in smaller bounded groups with unchanged per-test watchdogs.
- The large PR-operation file completed **316 passed, 1 failed**. Its release
  assertion treated a thread still resolving the real Git directory after
  300ms as a held publication lock. It now waits for actual cross-thread lock
  acquisition with a bounded future, propagating any acquisition exception.
  Nested reentrancy and locked-snapshot assertions are unchanged; related
  release/snapshot contracts passed **3 tests**. No production lock changes.
- Remaining Worktrees files, fresh current-head review and CI still gate merge.
  A completed Bridge run or partial Worktrees batch does not close the campaign.

### 2026-10-10 - Complete affected-suite evidence
- Retained, nonduplicated Worktrees coverage totals **7791 passed, 57 skipped,
  1 deselected**: seven original completed groups, two completed files, the
  complete PR-operation file with its corrected release assertion, and all
  remaining 123 files. Complete Bridge coverage is **3782 passed, 72 skipped**.
  Earlier and final batches differ only in the corrected release test and this
  journal, not production source or other fixtures.
- The only deselected contract is the exact accepted Windows gap #4644.
  Current-head required CI run `38034875866` passed; no other failure is waived.
- The evidence re-review found that the paired credential fixture needed an
  explicit ten-result count to reject worker exceptions/truncated rounds.
  That assertion is now required before accepting any successful result list.
  The trusted identifier scan also flagged legacy example identifiers in the
  touched Picker test file; examples now use the same generic project fixture
  as its new regressions. Production code is unchanged.
- Focused validation of these test-only corrections, fresh review and the
  updated-head identifier/CI checks remain publication gates. Do not rerun
  settled complete production-source coverage absent a relevant source change.

### 2026-10-10 - Preserve the cold-import I/O boundary
- Final review found that moving the liveness import outside the lock also
  moved it outside staging's established best-effort `OSError` handler.
  The import now remains before lock acquisition but inside that existing
  narrow handler. No import exception class, admission budget, identity check
  or lock-protected mutation contract is broadened.
- A fresh-process import-failure regression failed against the prior source;
  it now proves staging survives an I/O failure without creating a pending
  record or lock file. The complete runtime-version contracts passed
  **22 tests, 1 skipped**, including cold import ordering and real concurrent
  round trips. Touched-code lint passed.
- Existing complete Worktrees coverage is unchanged. Bridge affected-file
  validation is refreshed for this narrow exception-boundary correction;
  current-head review and checks still precede merge and deployment.

### 2026-10-10 - Independent Picker slice merged
- #6002 merged into `dev` as
  `651d47248cc9406982a358a3d16867a40ec93d7e` after current-head approval
  with zero findings, all resolved threads, and green required CI and trusted
  identifier checks. No review or CI bypass was used.
- The slice settles strict Picker identity matching, eligible-environment
  preservation and versioned ambiguity output, together with the narrowly
  coupled cold-start/validation corrections. Worktrees affected coverage is
  **7791 passed, 57 skipped, 1 deselected** for the accepted #4644 gap;
  Bridge full coverage is **3782 passed, 72 skipped**, refreshed by **22 passed,
  1 skipped** for the final cold-import I/O boundary. Final generic-fixture and
  ten-result assertions passed **52 tests**.
- Normal `pr-complete` reconciled the campaign worktree past the verified
  squash merge, preserving a backup. Promotion and installed-consumer refresh
  for this new slice still require separate evidence.
- #5972 and its issue claim remain active: claimant, fleet/roster and remaining
  locality/transport paths are not closed by this partial slice. #5689 remains
  open under its original owner and precedes overlapping host/WSL integration.
  #5973/#5974 and remaining shell/provider inventory are still campaign work.

### 2026-10-10 - Picker promotion and installed-consumer evidence
- Normal serialized promotion run `38040324652` succeeded after merged-dev CI
  `38039724743`. Generated release #6022 carries merged source `651d47248cc9`
  into `main` as `690f1ae9b9a38aaac4fdfb6055eafc6157222572` and consumes
  this slice's changefile. No promotion bypass or manual release was used.
- The unified installed-consumer update succeeded. Worktrees `1.24.40-dev1`
  and Bridge `0.9.43.dev1` were verified; Worktree Manager updated to
  `0.6.21-dev1`. A read-only call through the installed Worktrees runtime proves
  case-insensitive key precedence, eligible-environment copies and versioned
  ambiguity output, not just version markers.
- Consumer guidance reconciliation was proven provenance-only, reviewed with
  zero findings, passed its required full CI, merged and finalized in its
  own managed workspace. No downstream-private identity or state is recorded
  in this public campaign.
- The independent Picker slice is source-merged, promoted and deployed.
  Remaining #5972 paths, #5973/#5974, overlapping #5689 integration and the
  remaining inventory still keep the overall effort and issue claim Active.

### 2026-10-10 - Index boundary investigation and review proposal
- Reserved #5974 through the canonical dispatch subject and published its sole
  active issue-thread claim before substantive work.
- Existing Index designation, setup, ordered-indexer and client-routing
  contracts passed unchanged: 88 tests passed, 771 deselected in the contained
  Windows runner. This establishes the baseline, not new-behavior validation.
- Traced the explicit designation carried by the SSH command and the repeated
  locality comparisons in planning, delegation, HTTP selection and setup.
  Preserving that override is necessary to terminate a standalone remote child
  without confusing SSH targets with physical execution identity.
- Added the proposal above for review before runtime changes. It uses the
  original #5689 execution-identity library and an optional, read-only attributed
  topology boundary; neither a second topology authority nor a mandatory
  sibling tool is introduced. The provider extension and Index integration are
  not implemented by this planning slice.
- Documentation-impact review: the effort owns this future boundary and its
  evidence. As-is Index, Worktrees, architecture and install documentation
  remain unchanged until implementation. No vision intent is extended:
  this realizes the campaign's existing shared-machine-decision intent and
  the architecture principles of independent installation, graceful optional
  composition, attributable command ownership and explicit real errors.

### 2026-10-10 - Index proposal merged and remaining adapter inventory
- Proposal #6023 merged as `2e387c8a49a4ab78baa5b3a61cd65d0cbaefbdfc`
  after a current-head approval with zero findings and all required checks
  successful. No review or CI bypass was used. Supported post-merge
  reconciliation preserved the campaign workspace and advanced it onto `dev`.
  The earlier Picker promotion/deployment journal is now upstream too.
- #5974's design-review gate is settled; runtime implementation and deployment
  are not. Its optional batch identity capability is still a future extension.
  The shared host/WSL contract remains with #5689's original owner: its
  current checks pass, but its latest review still reports unresolved findings.
  Posted a coordination update to that PR rather than opening competing work.
- The bounded shell-only inventory found no second shell identity authority.
  `worktree-manager/bin/launch-session.sh:706-714` and
  `launch-session.ps1:907-918` consume the already-resolved remote plan and
  execute its exact `ssh_alias`/`remote_command`; neither resolves machine
  locality. Their hostname and `SSH_CONNECTION` uses are display/session
  context. Installer, package, control-master and preview matches were
  classified separately from named-machine decisions. This classification
  excludes generated vendors and the already-settled installation-context
  host/PID ownership contract.
- The bounded provider-argv inventory inspected the Picker descriptor parser
  (`provider_sources.py:135-187`), container wrapper/Docker spawn builders
  (`resolver.py:99-205,317-412`) and container SSH-profile adapter
  (`provider_ssh.py:90-110,203-211,272-292`). These select attributable
  provider commands and resource/instance keys, not shared machine identities.
  A container name rendered as an SSH profile hostname remains a provider
  namespace; it must not be canonicalized as a physical host. No source change
  is warranted by those specific adapters.
- These reports close the previously unexamined shell handoff and
  provider-command construction questions within their stated bounds. They
  do not reclassify the known #5972 Picker source-materialization/locality
  candidates or prove every provider/backend handler in the suite was inspected.
  The campaign's remaining-consumer inventory item stays open until that final
  coverage boundary is reconciled with the integration slices.

### 2026-10-10 - Logger inventory extension proposed
- Rechecked #5689 at the same current head: 18 checks succeeded, six were
  skipped, and the latest current-head review still lists 11 open findings.
  No new owner response followed the existing campaign coordination comment.
  Its source/root attribution could not be resolved in the current local
  registry; that is not evidence of abandonment or authority to take it over.
- The independent source inventory found Logger's `ramp_up.py:210-245,436-452`
  strips `-wsl`, accepts hostname-prefix equivalence, and delegates the
  supplied name directly to SSH. `test_ramp_up.py:167-172` explicitly expects
  native/WSL equivalence, so a corrected execution-locality boundary requires
  reviewed compatibility treatment rather than an unannounced test rewrite.
- The operator approved filing this finding and adding the proposal for review,
  with runtime implementation still blocked on the shared contract. Filed
  #6039 after duplicate search and verified its complete title/body. It remains
  unreserved; this planning amendment claims no runtime completion.
- The bounded scan also located Vault's local WSL IPC selection and
  Codespaces' lease/connection-owner host labels. These are classification
  candidates, not newly proven named-machine resolver overlap; their mere
  platform/hostname use does not justify a migration. Previously settled
  generated installation-context, shell-handoff and provider-argv bodies were
  not re-investigated. Final coverage remains open.
- Supported pull-forward retained the unpublished Index post-merge journal
  while advancing onto current `dev`. That journal accompanies this proposal.
  Documentation impact: the campaign and active index own the future boundary;
  as-is Logger, architecture and install docs stay unchanged until code lands.
  No new vision intent or runtime dependency is introduced.

### Current continuation gate

The source slices #5737/#5740 and the publication repair are settled; the
additional #5972 Picker matching slice is merged in #6002, with complete
affected-suite evidence, green publication gates and verified promotion and
deployment. Remaining #5972 paths and
#5973/#5974 are not implemented. #5974 is now reserved with its portable
composition proposal reviewed and merged in #6023; implementation must follow
the original peer's shared identity contract. The campaign extension is merged.
#5689 retains its original host/WSL
implementation owner and remains the overlapping-consumer prerequisite.
Logger #6039's proposal has its own independent review/merge gate in the Plan.
Once that gate is complete, do not repeat design review: implementation waits
only for the original shared contract. Neither its runtime nor its native/WSL
compatibility change is implemented or reserved.

#5885 is merged and #5911 is closed: do not rework that completed remediation.
Normal promotion and installed-consumer refresh are verified; consumer projection
reconciliation and the new Picker slice's deployment are complete. The effort
and the #5972/#5974 issue claims remain active until their complete scopes are resolved
or explicitly transferred; no single planning or implementation PR closes them.
