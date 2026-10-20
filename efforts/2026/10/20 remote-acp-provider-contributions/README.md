# Remote ACP Provider Contributions

- **Slug:** `remote-acp-provider-contributions`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed proposal and implementation PRs targeting `dev`
- **Created:** 2026-10-18
- **Status:** Done - generic source merged to dev; release and adopter acceptance transferred, no rollout
- **Vision:** [externally-hosted-acp-agents](../../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents), [topology-aware-agent-resolution](../../../../visions/plugins/agent-bridge/README.md#topology-aware-agent-resolution)
- **Umbrella issue:** #7218

## Guiding Intent

An already-running ACP agent should be addressable by a configured name or
explicit endpoint through the same bridge catalog and conversation contract.
An enabled capability plugin should supply reusable connection requirements;
the adopter supplies private endpoint and approved credential bindings.
Discovery is not connection, authorization or evidence of readiness.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| drowsy-piston | Proposal, integrated implementation, validation and PR stewardship | Public issue #7218 and its claimed managed worktree |

## Coordination

- **Topology:** Serial, independent PRs targeting `dev`; proposal merges before implementation.
- **Host (owns PRs):** drowsy-piston.
- **Delegates:** Bounded authentication, discovery and contract-evidence repairs;
  the host retains integration and publication ownership.
- **Handoff:** Record the current slice, evidence and unresolved gates here and transfer the public issue claim explicitly.
- Existing remote transport/session owners and dispatch-catalog efforts retain
  their scopes. This effort consumes the transport from #6908/#6997, not a new
  ACP stack. It does not absorb the independent SSE repair in #7186 or
  dispatch-extension catalog work in #7091/#7143.

## Context

The existing remote ACP configuration, transport and session manager can attach
to configured named endpoints without owning their remote process or workspace.
The ordinary agent roster does not yet expose those targets consistently.
`provider_sources.py` already resolves declarative command-provider manifests
with enabled-plugin provenance. Plugin activation and configuration layering
also have existing owners; installed directories alone do not prove activation.

Remote authentication already has origin/identity/generation isolation and an
injector seam, but daemon-created remote sessions do not yet compose an approved
provider-qualified source with a configurable constrained token scheme.
The shared credential relay owns credential sourcing and HTTP injection.

The proposal extends the vision's explicit target contribution and binding
contract. It reuses existing configuration, plugin activation, provider
discovery, credential relay and remote session abstractions. Concrete adopter
systems, secrets and deployment decisions are outside this public effort.

## Request

**Public-safe summary of the operator request:** Implement generic agent-bridge
URL targets, provider discovery and scoped authentication, so enabled harness
plugins can supply `.agent-bridge/config.yaml` and `providers.d` connection and
authentication requirements and any agent can address the contributed target.
Keep concrete endpoint and credential bindings adopter-private. Publish through
normal reviewed generic-source and consumer-plugin PR flows.

**Execution constraint:** CI availability does not lift the separate
`dev`-to-`main` promotion hold. Do not bypass promotion, update installed
payloads/runtimes, restart a shared daemon, grant live credentials or infer
deployment from a merged PR.

The summary removes consumer-specific identifiers without changing intent.
The implementation decomposition and additional failure-case coverage below
are **agent-recommended**.

## Plan

### Phase 1 - Reviewed contribution contract

- [x] Deduplicate and claim the public coordination issue.
- [x] Review and merge this effort and the vision delta before implementation.
- [x] Sync execution forward onto the reviewed plan without dropping ownership.

### Phase 2 - Declarative configuration and target resolution

- [x] Compose enabled-plugin remote target requirements through existing
  activation/config/provider seams; retain command-provider compatibility.
- [x] Define and document the minimal target declaration, adopter binding,
  precedence, conflict diagnostics and canonical endpoint identity.
- [x] Expose named and contributed remote targets through ordinary roster,
  detail and project-context resolution, including explicit `requires-binding`
  or unavailable state for incomplete bindings.
- [x] Resolve direct HTTPS/WSS targets through an explicitly matching local
  policy and the same remote session manager; refuse unmatched URLs before
  network, credential lookup or workspace/process acquisition.

### Phase 3 - Scoped source binding and conversations

- [x] Wire an explicitly approved provider-qualified credential source and
  constrained token scheme into daemon-created remote sessions through the
  existing credential relay injector.
- [x] Preserve origin, identity, resource, scope and generation boundaries;
  reject unknown/ambiguous sources and unsafe header values without fallback.
- [x] Exercise ordinary create/send/resume, correlated manual approvals and
  remote session identity through the same connector for all target forms.
- [x] Preserve named-target compatibility, bounded pre-admission auth retry,
  non-replay of uncertain prompts and remote process/workspace non-ownership.

### Phase 4 - Reviewed delivery and closure

- [x] Update bridge/credential-source configuration docs and agent-facing usage;
  add required plugin changefiles and shared-library consumer validation.
- [x] Satisfy the validation matrix, address review findings and merge the
  implementation PRs to `dev`.
- [x] Journal merged evidence, resolve every plan/validation item and archive
  this effort. Transfer any release or adopter acceptance obligation to a
  named tracked owner; never describe a `dev` merge as a running capability.

## Validation Plan

All cases below are **agent-recommended** acceptance detail.

- [x] Unit tests cover active/inactive/indeterminate plugins, verified payload
  roots, supported empty providers, malformed declarations, duplicate target
  ownership and project/operator precedence. One broken contribution does not
  hide healthy unrelated providers or become an empty-success result.
- [x] Discovery and detail tests assert zero credential-source calls and zero
  network connections; unbound contributions remain visible and non-runnable.
- [x] URL tests cover accepted secure schemes, invalid/ambiguous endpoints,
  matching policy, origin/path identity and refusal before side effects.
- [x] Auth tests inspect actual constructed header bytes for a synthetic token
  scheme and prove source approval, identity/origin/resource/scope/generation
  isolation, missing/ambiguous bindings and bounded 401 retry.
- [x] A synthetic TLS peer exercises the normal catalog-to-create/send/resume
  path for configured, URL and plugin-contributed targets, with approval,
  denial, reconnect and no-prompt-replay checks; assert no local spawning or
  worktree/filesystem acquisition.
- [x] Existing remote ACP, provider discovery, roster/routes/client-connect
  and shared credential-relay suites remain green using the canonical isolated
  runner; lint, install-contract and applicable shared-library vendoring gates
  pass without weakening thresholds or skipping encountered failures.
- [x] Run a fresh-environment contribution/binding check when practical;
  record exact tier coverage and reasons for any omitted clean-room lane.
- [x] Record live-venue validation availability explicitly: real endpoint,
  source grant, promotion and installed-runtime proof remain unauthorized.
  No live acceptance is claimed.
- [x] Deferred to `#7237`: host relationship and separately authorized
  adopter acceptance; the existing host-ownership effort owns that next
  integration boundary rather than this target-contribution effort.
- [x] Deferred to `#3336`: release promotion; separately authorized adopter
  rollout remains governed by the existing promotion/runtime holds.
- [x] Public issues, docs, fixtures, commits and PR descriptions contain only
  generic examples and approved public attribution.

## Proposal

**Agent-recommended design:**

1. Extend existing bridge configuration composition, not the transport or
   service lifecycle. Enabled plugins contribute inert target requirements and
   provider manifests from attributable payload-owned roots. Canonical
   `.copilot-extensions/agent-bridge` and compatible `.agent-bridge` paths keep
   their documented authority. Do not create a second independent merge order.
2. Separate contribution identity from adopter binding. A contribution may
   name required endpoint/profile and credential-source slots without supplying
   secrets. Operator/project settings bind them explicitly. Plugin contributions
   cannot approve a source, execute an auth command at discovery, supply
   arbitrary headers or turn ambient credentials into authority.
3. Feed one normalized remote target representation into ordinary list/detail
   and session resolution. An explicit URL must match approved configuration;
   a URL is an address, not permission. Keep named-target compatibility and
   durable remote identity. Resolve endpoint identity consistently, including
   path distinctions; reject ambiguous policy rather than guessing.
4. Reuse the relay's registered source contract and HTTP injector. A constrained
   validated token scheme is data, not an arbitrary header/template language.
   Retain exact credential context checks and existing refresh/retry ownership.
5. Keep target state honest: discoverable, bound, connectable and running are
   different facts. Missing/invalid activation, declaration or binding has a
   diagnostic that refuses the affected target before consequential work.

Schema details must reconcile with current configuration and provider patterns
during Phase 2; any material expansion returns through proposal review first.

## Journal

### 2026-10-20 - Generic source merged and effort closed

- #7231 merged at `2026-10-20T03:29:30Z` as
  `28db8c9a2c700317824e96e1880fdcb5bd4df478`. Current-head review
  `5580394495` is an owner-authored passing Comment with no open findings,
  as permitted by the repository's contribution policy. All applicable checks
  passed and all review threads were resolved. Managed merge reported no
  review-gate bypass.
- Current-base consumer validation passed 154 cases; the unchanged contract
  closure passed 265 fixtures. Earlier calibrated roster/startup/relay suites
  remain recorded below. Fresh temporary plugin roots, actual provider
  commands and a fresh bridge database exercise first-touch contribution and
  binding through the contained TLS peer. A whole-machine installer clean room
  was not run: no installer changed and installed-runtime rollout is explicitly
  held. Real endpoint/grant/installed-version evidence is likewise not claimed.
- Re-read every Plan and Validation Plan item. Generic source acceptance is
  complete; #7237 retains host relationship and separately authorized adopter
  acceptance, and #3336 retains release responsibility. Historical
  #7020/#6266 cause/qualification work remains with its existing owners and is
  not reclassified as fixed by current CI policy or this source merge.
- Pull-forward initially aborted safely on the multi-commit squash history.
  Verified the provider's exact merged head and compared every owned file:
  all source changes were preserved, with only an unrelated upstream testing
  note added. Kept a backup, rebased the fully merged stack onto its exact merge
  commit and completed managed sync; no reset, stash or unpublished work loss.
- Archived this resolved source effort. `dev` merge means pending release and
  authorized adoption, not an installed or running capability.

### 2026-10-20 - Current contribution policy and dependency repair

- The former source head had a clean review but failed historical native
  installer and Windows console jobs. Preserve the unresolved #7020 and #6266
  causal investigations; a passing control is not a fix or accepted-flake waiver.
- Actively unblocked the accepted #7020 repair owner with its exact existing
  native source/interpreter location. The separate, proven Windows census
  attribution defect merged through #7310, followed by reviewed effort
  accounting in #7315. Neither claims the historical native causes are closed.
- Current reviewed `dev` independently moved required PR validation to its
  eligible-unit portfolio, with native integration retained separately. Apply
  that repository policy normally rather than treating old-source jobs as a
  permanent exception. No workflow, skip, deadline, forced rerun or bypass was
  introduced by this contribution.
- Kept a recovery ref and performed the ordinary managed source sync. Declared
  HTTP41 source closure, expected bodies, historical vectors and floors remain
  unchanged; all 265 contract fixtures pass. The current-base catalog, scoped
  authentication, provider command, target contract and actual TLS peer lane
  passes 154 cases.
- Publish the legitimate rebase and await fresh current-head review and all
  required CI before merge. Historical native causal/qualification obligations
  remain with their tracked owners; release, installed rollout, credential
  grants and real-endpoint acceptance are still separately gated.

### 2026-10-19 - Uncached snapshots and listener-disabled credentials

- Fresh review confirmed cached project reconciliation but found that the
  uncached resolver and fallback routes could retain remote rows across later
  awaits. Eight calibrated cases failed: scoped/unscoped requests, both
  topology-not-ready and cache-absent routes, and withdrawal during row
  composition or namespace scans.
- Uncached listing now composes project-aware rows once and uses the same
  report-identity reconciliation helper as cached listing after its final
  await. Fallback routes do not add another composition await; stale remote
  rows are omitted with a unique incomplete diagnostic while healthy namespace
  rows remain. The catalog/cache/roster/runtime/historical-route lane passed
  148 cases and Python lint passed.
- Another review case showed that disabling the TCP credential relay skipped
  process-local HTTP source registration too. A real lifespan regression with
  the listener disabled failed binding an otherwise approved native source.
  Startup now registers HTTP-only sources in that role without contributing,
  starting or adopting a TCP listener. Approval and lazy acquisition are
  unchanged.
- Native startup/source/profile/readiness/relay-adoption validation passed 58
  cases, including the real disabled-listener startup and actual synthetic
  credential command. Python lint passed. Final source attestation and fresh
  review/CI remain required; no rollout or live credential grant occurred.
- Re-attested current HTTP41 metadata to committed source
  `2ecd9cff2436e7abab212023ae17307a7a7823d8`, preserving the 35-source,
  32-provenance closure and explicit source-only classifications. Independent
  checking passed all 265 fixtures and the contract/provider consumer lane
  passed 115 cases. Expected bodies, historical vectors and floors are unchanged.

### 2026-10-19 - Project composition before final snapshot assembly

- Fresh review confirmed off-thread row composition but found a later route
  await: project-specific binding composition ran after cache reconciliation
  and `require_complete` checks, allowing replaced-provider rows through.
- Two route-level cases failed before repair. The cache now composes project
  rows before final namespace assembly and completeness, with no later route
  await. Replaced providers remain incomplete; definitively removed providers
  disappear without making an otherwise complete snapshot incomplete.
- Extended the same await boundary to remote contribution withdrawal: two
  additional calibrated cases demonstrated stale remote rows. Composition now
  fences the captured remote report identity, omits superseded rows and reports
  `remote-acp` incomplete; simultaneous source errors do not duplicate that
  namespace diagnostic.
- The combined catalog/cache/HTTP roster/runtime/historical-route lane passed
  138 cases before adding the explicit simultaneous-source-error cases.
  Python lint passed; final validation and contract re-attestation follow.
  No authority or deployment gate changes.
- The final lane passed all 140 cases, including simultaneous source errors.
  Strict module limits passed for 35 changed files. Re-attested current HTTP41
  metadata to committed runtime `b9658fe848ab7f13f87f454b642e472c238ce2a5`;
  independent checking passed 265 fixtures and the contract/provider consumer
  lane passed 115 cases. Expected bodies, historical vectors, capability floors
  and source-only evidence classifications remain unchanged.

### 2026-10-19 - Roster binding reads off the event loop

- Fresh review confirmed production HTTP-source registration and identified
  another roster consumer boundary: cached synchronous row assembly inferred
  a single project's repository binding on the event loop before the later
  project-specific offload.
- Calibrated failures in both cached and uncached roster polling. Both now
  compose repository-backed rows off-thread; uncached discovery also uses the
  existing scan/apply split so publication and its generation guard stay on
  the event loop.
- Reconcile cached namespace membership and freshness again after asynchronous
  row composition. A deterministic replacement-during-composition test proves
  the old provider's rows cannot reappear across that added await.
- Catalog, cache, HTTP roster and runtime validation passed 117 cases; Python
  lint passed. Contract re-attestation, fresh review and final-head CI remain
  required. No scope, capability-floor or rollout gate was relaxed.
- Re-attested the same current HTTP41 metadata to committed source
  `460a21bfbc9f614d98820294e6cf041a315f70da`, preserving all expected bodies,
  historical vectors and capability floors. Independent checking passed all
  265 fixtures and the combined contract/provider consumer lane passed 115
  cases. The 35-source/32-provenance closure retains explicit source-only
  classification for native credential-command dependencies.

### 2026-10-19 - Production HTTP source registration

- Fresh current-head review confirmed the global/project repair and found
  that production relay registration did not populate the new HTTP source
  registry. Earlier peer tests inserted a source directly and therefore did
  not prove the startup boundary.
- Replaced that shortcut in all six named/URL/contributed TLS peer cases with
  real startup registration from an enabled, identity-verified native provider
  manifest and a separate provider command process. All six failed at truthful
  `requires-binding` state before repair.
- Native providers explicitly opt in with `http_credentials_version: 1`;
  their existing `relay-profile` advertises source IDs/scoped-field support.
  References derive from the verified plugin identity. Approved acquisition
  uses the provider's fixed `http-credential <id> get` operation and the
  existing credential-source key/value framing, not imported adapter code,
  arbitrary profile commands, inline secrets or a second credential store.
- Registration acquires no credentials and HTTP-only sources do not start a
  TCP relay. Invalid or unavailable profiles remain visible refusals without
  disabling healthy providers; duplicate source identities are ambiguous.
  Actual endpoint/identity/resource/scope/scheme approval remains service-owned.
- Native TLS peer and adjacent provider/relay/auth validation passed 122 cases.
  The focused registration/refusal/provider/profile/auth lane passed 110 cases,
  including real command-boundary scope forwarding, inactive/unknown/legacy
  refusal, malformed declarations, duplicate sources and sanitized failures.
  Python lint passed. Contract re-attestation, fresh review and hosted CI
  remain required; no live deployment or credential grant occurred.
- Re-attested the current generation-41 metadata to committed runtime
  `862f3f48f8fd0af32344cbf148c9228726c6bc27`, with 35 source hashes and 32
  provenance records. The four production registration/command dependencies
  are source-only non-HTTP closure entries, not invented HTTP captures.
  Independent checking passed 265 fixtures and the combined contract/provider
  consumer lane passed 115 cases. Expected bodies, historical vectors and
  capability floors are unchanged.

### 2026-10-19 - Global and project activation boundary

- Fresh review after reconciling the contribution branch with `dev` identified
  a combined global/project activation case: an unscoped request could infer
  the current repository's project binding and expose it as a global target.
- Global activation now requires an explicit project or a service binding
  before selecting project-owned endpoint/auth policy. Project-only activation
  retains repository inference; explicit project and service bindings retain
  their intended precedence.
- Four calibrated regressions failed before repair. The corrected catalog lane
  passed 96 cases; independent catalog/cache/roster/HTTP/session validation
  passed 141 cases. Strict size and Python lint checks passed.
- Re-attested the same current generation-41 metadata to committed runtime
  `ea573a4458a8e5d9c9b58ff54661245f48e11df4`, retaining independent expected
  bodies, historical vectors and capability floors. Independent checking
  passed all 265 fixtures and the combined contract/provider consumer lane
  passed 115 cases. Fresh review and hosted CI on the published repair remain
  required before merge.

### 2026-10-19 - Explicit-load and lifecycle review races

- The next review confirmed the preceding eight code repairs and identified
  three remaining cases. Concurrent first-time explicit loads now match the
  requested native ID while it is still unconfirmed, rather than creating
  another bridge attachment to the same conversation.
- Stopped caller reuse acquires the same lifecycle lock as explicit resume/
  stop. After lock acquisition, it resolves policy again and rechecks the
  registered session's binding and state; an in-flight explicit resume is not
  replaced or shut down by an ordinary start.
- Named-target clients retain legacy reuse with generation-38 through
  generation-40 daemons, which cannot perform binding-aware reuse. Generation
  41 delegates that decision to the daemon. Direct URL capability floors stay
  unchanged.
- Calibrated the three defects before fixing them: five new cases failed while
  54 passed. The corrected session/CLI lane passed all 59 cases, with strict
  module-size and Python lint checks passing. Expanded native TLS/peer/HTTP
  isolation/session/historical-route validation passed 223 cases, before
  generation-41 re-attestation to committed source
  `aa2723309669097da9d30c04377d63802c53b4c4`. Current-head review and hosted
  CI remain open.
- Extended the lifecycle race to explicit stop, which may hold the lock while
  status still reads idle. That added case failed before repair; all settled
  reuse candidates now wait for the lock and revalidate, while an already
  reserved STARTING attachment remains immediately identifiable. The final
  session/CLI/historical-route lane passed 117 cases and strict size/lint checks
  passed. Final source is `bef972393d699a64dbc06e6049577efb74979cd1`.
- Refreshed current generation-41 provenance to that final committed source;
  independent checking passed all 265 fixtures and the contract/provider lane
  passed 115 cases. Historical expected bodies and capability floors remain
  unchanged.

### 2026-10-19 - Second review repairs and conversation identity

- Corrected project-aware `agent-show` admission in the real CLI parser guard.
  Remote named and URL starts now bypass unqualified CLI reuse and resolve the
  canonical target, selected project, current binding and credential approval
  in the daemon before reuse or a busy-session takeover is offered.
- Reuse requires caller, project, remote cwd and the complete binding snapshot.
  Matching URLs and names share a conversation; stopped sessions load the
  retained native ID. Changed bindings refuse until an explicit new attachment,
  while another project's binding and anonymous requests cannot select the old
  conversation. Starting reservations precede asynchronous connection/shutdown
  so simultaneous requests do not duplicate a remote attachment.
- Isolated built-in Git credential fills between remote authentication
  instances. Discovery repairs preserve unrelated daemon startup readiness,
  activation-derived scope on invalid bindings, known unavailable detail and
  alternate `.claude-plugin/plugin.json` contribution declarations.
- Independent combined review regressions passed (188); expanded native
  transport, TLS peer, HTTP isolation and CLI cases passed (218). The final
  conflict-owner extraction passed 77 session/CLI cases, Python lint and the
  strict module-size guard without widening a ceiling. Install contracts passed
  for all 13 runtime plugins. These counts overlap, not additive coverage.
- Independent historical route vectors exposed an unnecessary internal reuse
  keyword for anonymous requests. Omit that inert default without changing
  their wire expectations; the historical contract and session lane passed
  84 cases. Runtime evidence is pinned at
  `feed1caefa7f06811bd53d83125428afc4b25568` before refreshed generation-41
  metadata. Independent contract checking passed all 265 fixtures; the
  contract/provider lane passed 115 cases. Current-head review and hosted CI
  remain gates.
  Qualified host-worktree conversation ownership is a separate proposal,
  tracked in #7237; this contribution does not claim to implement it.

### 2026-10-19 - First implementation review and CI repairs

- #7231's current-head Copilot review identified project binding gaps in
  ordinary rosters and CLI attachment. Both cached and direct roster paths now
  compose remote rows with the requested project; plain/streaming CLI listings,
  detail and real client creation requests propagate that context.
- URL ambiguity considers only policies enabled in that context. A drain
  entering during off-thread policy lookup refuses before creating any ledger
  row; the acquisition counter still unwinds.
- Hosted Python 3.12 exposed nondeterministic ordering in the new SSE regression
  test, not a missing reader guard: the test could request its second chunk
  before it had marked the wire closed. Closure now occurs inside the buffered
  reader before returning the chunk. The unguarded reader was calibrated red;
  guarded cases cover cancellation and ordinary closure.
- Focused project/session/cache cases passed (129), adjacent CLI/API roster
  consumers passed (56), and actual TLS HTTP/2/SSE/WSS peer checks passed (46).
  The native plugin cases now use repository-only bindings for a multi-project
  contribution, including project-aware roster/detail, create and resume.
  Review and hosted full-suite gates remain open until the repaired head passes.

### 2026-10-19 - Generic implementation and bounded evidence

- Pinned implementation sources in `d9081f44c` before generation-41 contract
  capture. Direct URL requests need the new capability floor; named remote ACP
  retains generation 38. Immutable compatibility evidence is being updated
  before publication, with historical floors and vectors retained.
- Targeted local evidence: 617 bridge core cases, 131 configuration/cache cases,
  185 shared-relay cases, and 45 actual TLS HTTP/2/SSE/WSS peer cases passed.
  The peer lane crosses configured, URL and plugin targets with real custom
  headers, exact manual decisions and durable history resume.
- Shared-relay consumer checks passed: 78 codespaces, 80 containers and 16 MCP
  cases; existing platform skips remain explicit. A CLI regression also proves
  ordinary/streaming project filtering and visible unbound diagnostics.
- Encountered and calibrated a buffered-read cancellation race during ordinary
  direct shutdown, then fixed the closed-state boundary without changing any
  deadline; follow-up evidence is recorded on #7186.
- The first native full-suite group reached its unchanged wall limit. Smaller
  10-file groups retained all limits and passed five groups (1,363 cases) before
  identifying three historical route-vector kwargs differences. The runtime
  compatibility fix is included in the pinned source; contract and remaining
  full-suite checks are still open, not waived.

### 2026-10-19 - Reviewed plan merged

- #7220 merged as `5d304dff3859623eb5e2d93f53bbfe5d5c35108d` after an
  approving Copilot review with no findings and successful observed CI.
- Synced the claimed worktree onto the reviewed `dev` plan and began Phase 2.
  Promotion, installed rollout and live credentials remain separately gated.

### 2026-10-18 - Proposal and public coordination

- Claimed #7218 as drowsy-piston after checking adjacent public work.
- Source evidence identified reusable remote connector/session, plugin
  activation/provider discovery and credential-relay seams. No implementation
  or runtime changes have started.
- Submitted intent is generic and consumer-neutral. The contribution branch
  remains `dev`; release promotion, installed rollout and live grants are not
  authorized by this effort.
