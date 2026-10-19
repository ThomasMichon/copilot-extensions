# Remote ACP Client Transport

- **Slug:** `remote-acp-client-transport`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-15
- **Status:** Done
- **Vision:** [agent-bridge / externally-hosted-acp-agents](../../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents)
- **Umbrella issue:** #6908

## Guiding Intent

Make an already-running authenticated remote ACP agent a bridge client target,
without conflating connection ownership with ownership of its server, workspace,
or durable sessions. Reuse the ACP lifecycle and existing credential owners.
Both Streamable HTTP and WebSocket belong to the selected experimental profile.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Remote transport implementer | Generic plan, client transport, tests, documentation and PR stewardship | #6908 |
| Protocol decision owner | Resolve the pinned draft's conflicting lifecycle statements | #6908 |

## Coordination

- **Topology:** serial plan and implementation PRs against `dev`.
- **Host (owns PRs):** Remote transport implementer.
- **Delegates:** none.
- **Completion:** #6908 and this archived public effort record the resolved
  lifecycle decisions and merged generic implementation. Release promotion and
  any separately authorized operator rollout are not claimed as complete.
- No server implementation, deployment, rollout, fleet controller or
  application-specific adapter is owned here.
- Existing bridge workspace and installation repair efforts retain their
  scopes; this effort must not supersede their open contributions.

## Context

The incoming bridge `/acp` WebSocket face is the opposite protocol direction.
`AcpClient.start` and `start_streams` initialize the existing SDK connection
over subprocess/Session Host streams. They already provide prompt, cancel,
session load, permission and elicitation handling. A remote transport should
extend that connection seam, not introduce a second chat protocol.

[Design and validation detail](design.md) records the immutable RFD pin, newer
public Python SDK prior art, credential ownership, proposed configuration,
approval requirements, and the decision gate.
[Qualified wire contract](contract.md) fixes the selected profile identifier,
extension metadata, approval identity rules and configured target shape.

## Request

Public-safe capture of the requested generic capability:

> Goal: allow agent-bridge as a CLIENT to an already-running remote ACP HTTP(S)
> endpoint, reusing existing ACP lifecycle instead of local/SSH child spawning,
> and credential-relay/auth-injection ownership rather than a new credential store.

> User selected a PINNED EXPERIMENTAL Streamable HTTP + WebSocket remote-transport
> draft profile, NOT WS-only and NOT a private POST-chat dialect.

> Headless approval requirement: remote agent permission/tool/structured
> elicitation requests must use bridge's existing answer/relay-to-operator path.
> They cannot require the remote app's UI. Requests must be correlated and retain
> option/request identity; no autoapproval merely from textual yes, restored
> history or reconnect.

Additional requested constraints, summarized: no owned remote process or
workspace allocation; new/load sessions, streamed updates, prompt, cancel and
explicit close; preserve local/SSH/provider behavior; bounded frames, queues,
timeouts and auth refresh; verified TLS, no cross-origin credential forwarding,
no non-loopback cleartext by default, no blind prompt replay or silent transport
fallback. Validate independent synthetic HTTP/2/SSE and WebSocket consumers,
auth construction, approval roundtrips and server survival. Follow the
repository's reviewed plan, changefile, contained-test, PR and release policies;
do not deploy or modify installed payloads. Stop on a design contradiction.

Coordination clarification: durable application decision IDs are not transient
ACP request IDs. Preserve arbitrary opaque server-owned decision/tool-call IDs
and structured input integrity. Authorized agents may answer explicitly or
relay a human answer. Missing required approval capability must produce an
explicit refusal with no write, never a forced remote web UI.

Decision clarification: option A is explicitly selected. Durable conversation
history is distinct from connection-local resources; resume only through
advertised `session/load`. No live-inference continuity or automatic prompt
replay is promised. Experimental headless approval negotiation in namespaced
extension metadata is authorized, not represented as a standard ACP field.

## Plan

### Phase 1 — Reviewed intent and lifecycle decision

- [x] Verify a clean isolated source worktree, scoped publication identity,
  open #6908 and absence of a competing remote-client implementation.
- [x] Pin the public transport draft and inspect current public SDK prior art.
- [x] Record owner seams, a configuration proposal, lifecycle contradiction,
  concrete options and the validation matrix in [design.md](design.md).
- [x] Land reviewed generic intent through the normal `dev` PR policy.
- [x] Resolve decision D1 in #6908 before implementing. A reviewed plan alone
  does not select an option or authorize a wire-profile deviation.
- [x] Record the explicitly selected qualified profile and experimental
  approval negotiation in [contract.md](contract.md).
- [x] Resolve SDK ownership decision D2: public factory interfaces cannot
  enforce the required transport bounds and redirect policy unchanged.

### Phase 2 — Scoped client implementation

- [x] Recheck active contributions and dependency admission; qualify reusable
  SDK transport behavior against the selected profile and extend the existing
  ACP connection seam rather than copying a protocol stack.
- [x] Add typed configured remote targets without worktree allocation,
  subprocess launch, process ownership or server termination.
- [x] Extend the shared credential/auth-injection owner seam; keep host token
  acquisition, source scope gates and expiry handling in their existing owners.
- [x] Implement both explicitly selected HTTP/2/SSE and WebSocket modes,
  cookies, capability negotiation, new/load, prompt, streamed updates, cancel,
  bounded transport resources and explicit connection close.
- [x] Preserve live request/option identity across agent and operator answers;
  reject wrong-session, stale and replayed answers; keep application approvals
  outside blanket local-tool autoapproval.
- [x] Persist only non-secret endpoint/profile/remote-session and negotiated
  capability evidence. Reconcile reconnect explicitly without prompt replay.

### Phase 3 — Validation, publication and completion

- [x] Stage new tests before the contained runner; execute the focused matrix
  in [design.md](design.md), then local/SSH/provider regressions.
- [x] Document configuration, experimental pin, auth setup, explicit reconnect
  and close semantics, resource limits and unsupported features.
- [x] Add required plugin changefiles and run touched lint, install-contract
  and repository gates; complete documentation/cutover-impact review.
- [x] Drive implementation PR review and required checks through actual merge.
- [x] Record merged/pending-release or released/pending-rollout accurately;
  no runtime update/restart or deployment is authorized by this effort.
- [x] Resolve every Plan and Validation Plan item before marking Done and
  releasing/finalizing ownership.

## Validation Plan

- [x] Effort/vision schema and changed-document links pass for this plan.
- [x] D1 has a recorded explicit decision and any profile qualifications are
  reflected consistently in code, configuration, docs and synthetic peers.
- [x] Unit coverage validates target admission, origin/identity-qualified auth,
  real header construction, bounds, protocol errors and callback identity.
- [x] Independent synthetic remote process exercises actual HTTP/2 with
  connection/per-session SSE plus actual WebSocket upgrade and text frames.
- [x] Both transports prove new/load, streaming, cancellation, explicit close
  and disconnect survival; unsupported load is an explicit refusal.
- [x] Explicit agent answers, operator-relayed answers, rejection, stale/replayed
  answers, withdrawal, cancellation and cross-session isolation pass.
- [x] Auth refusal/refresh, TLS refusal, redirects, cookie affinity, malformed
  frames, stalled streams and unknown outcome recovery pass without secrets
  entering output, persistent records or error messages.
- [x] Local/SSH/provider behavior and changed shared-auth consumers retain
  their focused regressions; install/lint/contract gates pass.
- [x] Every unexercised clean-room or external-venue lane has an exact recorded
  gap; synthetic or mocked coverage is never called live deployment evidence.

## Proposal

**D1 resolved: option A explicitly selected.** The distinct experimental profile
and approval metadata are recorded in [contract.md](contract.md). The bridge
must not claim unqualified RFD conformance.

**D2 resolved:** bridge-owned bounded HTTP/SSE and WebSocket implementations
of the SDK's public message transport interface are explicitly authorized.
SDK protocol/lifecycle/schema handling is retained; private SDK patches and
vendored forks remain forbidden.

The neutral profile is `acp-http-ws-durable-v1alpha1`; the minimal experimental
policy namespace is `remote-acp/explicit-approvals`. Native ACP identifiers,
permission outcomes and form content remain native. The extension negotiates
explicit-answer policy, which native ACP callback availability alone does not
express; a separate durable decision ID is optional when the native tool-call
ID already serves that role. Neither identity is an official ACP registration.

## Journal

### 2026-10-16 — Generic runtime merged; effort complete

- Runtime PR #6997 merged to `dev` as
  `040a0a2b11565a4afd0c2764f6d45986d086b5cd`. The current-head owner-authored
  review was a clean Comment with no open findings, all review threads were
  resolved and every required check passed. The public uv-manifest setup
  timeout was retried through normal CI; no required gate was bypassed.
- All implementation and validation items are resolved. The neutral profile,
  pinned draft, standard-first callback identities, scoped auth injection and
  explicit headless-answer contract are documented in the plugin's durable
  remote-client guide.
- Status at completion: merged/pending release. Main promotion is not asserted;
  the existing release pipeline owns that publication. Runtime update, restart,
  live deployment, downstream integration and server business validation remain
  outside this effort's authorization and scope.
- Archive publication and managed worktree finalization complete the repository
  lifecycle; they are not runtime rollout evidence.

### 2026-10-16 — Canonical diagnostic paths and completed-request forms

- Review identified symlink aliases bypassing the SDK diagnostic boundary and
  unanswered request-scoped forms remaining live after their originating RPC
  completed. SDK/log paths now resolve consistently; non-SDK/local behavior
  remains unchanged.
- A public bridge transport completion notification retires only the exact
  typed native request scope, emits correlated withdrawal and sends terminal
  cancellation without prematurely deleting callback response routing.
  Explicit form withdrawal uses the same terminal outcome.
- Native symlink/privacy and actual HTTP2/WSS completed-RPC regressions verify
  the marker stays redacted, late answers refuse, live attention clears and a
  subsequent prompt still succeeds. Remote/logging/attention selection:
  149 passed.

### 2026-10-16 — End fences remote replacement attachment

- Review identified end completing while resume waited on old-client
  shutdown. Registry identity and terminal state are now checked before client
  replacement and at adoption; stale callbacks/finalizers cannot mutate a
  deleted or superseding session.
- The regression pauses resume, completes end (removing registry/database
  state), then releases shutdown and proves no replacement connection/load
  occurs. Remote-manager selection: 19 passed.

### 2026-10-16 — Replay-safe progress after daemon rehydration

- Review followed the marked-history path through manager restart and found
  the shared progress reducer still accepting historical milestones. It now
  returns before changing either progress or output freshness.
- A real database-backed manager rehydration regression retains live
  `build=failed` when later marked history reports `build=ok`; an independently
  expected current-generation projection fixture records that contract.
- Remote-manager and legacy manager selection: 207 passed. No historical
  fixture bytes, protocol identity or rollout authorization changed.

### 2026-10-16 — Drain-safe attachment accounting

- Review identified a stopped-session attachment waiting on an old client
  before becoming visible to drain. Remote connect/resume now participates in
  the existing active-acquisition counter before its first await and releases
  that accounting on success, failure and cancellation.
- Drain is rechecked before new connection work and lifecycle adoption.
  A paused old-client shutdown cannot produce a clean drain or resume work
  after that gate. Remote manager selection: 17 passed.
- The carried-over accepted-ID thread is already covered by the explicit-stop
  boundary fix and real same-ID load regression; it is not treated as an
  unimplemented path.

### 2026-10-16 — Stop-boundary accepted IDs and challenged affinity

- Fresh review identified the explicit-stop path clearing a client before its
  accepted server ID was persisted, and WS retries omitting affinity cookies
  issued by a 401. Stop captures validated identity at its owned boundary;
  the independent HTTP2 regression now resumes that exact conversation.
- WS extracts refusal cookies with the existing in-memory jar and rebuilds
  each retry's Cookie header. Independent HTTP2/WSS challenge fixtures require
  the 401 cookie before accepting refreshed authentication.
- Combined remote capability, legacy ACP, manager and stop selection:
  357 passed. Required CI passed at the previous head after a normal failed-job
  retry; the unrelated native PowerShell fixture also passed locally under
  contained PowerShell 5.1. New-head full CI/review remain required.

### 2026-10-16 — Refresh privacy, actionable permissions and telemetry identity

- Current review found a provider invalidation exception escaping the WS 401
  handler, permission events omitting callback-only native action details and
  learned ACP identity missing from telemetry.
- Shared bounded refresh now sanitizes provider failures on HTTP and WS.
  Permission events preserve the native tool payload (input, content and
  locations); telemetry binds the validated remote ID before live/state events
  on success and accepted-ID recovery.
- Five focused regressions pass, including actual HTTP2/WSS provider-failure
  traceback privacy and callback-only action details. Historical fixture
  generations stay immutable; current-candidate provenance follows the commit.
- Repeated CI cancellations are not accepted results. The actual latest
  cancelled bridge job's steps completed without a failed step; required CI
  still needs a complete successful run before any merge.
- The provider annotation confirms the concrete cause: the job exceeded its
  eight-minute maximum, including cleanup. Keep fresh independent peer
  processes and full coverage; add bridge to the existing bounded 12-minute
  native-consumer budget exception, without changing other plugin limits or
  the required aggregate gate.
- Complete focused remote/logging/ledger/independent-peer/legacy ACP selection:
  134 passed. Contained CI/promotion workflow contracts: 62 passed.

### 2026-10-16 — Independent approval expiry and interrupted SSE readiness

- A delayed review identified idle/background approval waits lacking their
  own clock and a cancelled session-stream opener reporting ready success.
  Every live approval now has an independent deadline and invalidates its
  connection on expiry, without depending on an outbound RPC.
- Session SSE setup refuses closed/ended transports before and after its
  readiness wait. Tests cover both low-level cancellation and actual manager
  stop while a real HTTP2 session stream is opening; attachment fails and the
  session stays stopped.
- Remote transport and independent-peer selection: 64 passed, including late
  permission/form answers refused after expiry with no outbound RPC pending.
  Required review and full current-head CI remain open; no rollout.

### 2026-10-16 — Startup ownership and recoverable accepted identity

- Followed the concurrent-startup path through the manager: late continuations
  now refuse to overwrite stop/end or another client, and stale-client events
  are ignored. A validated server session ID survives later stream-setup
  failure, retaining explicit load recovery instead of creating another
  conversation.
- Manager regression selection: 13 passed, including stop during startup and
  accepted-ID recovery through load. Refreshed only the current candidate's
  source capture; all historical generations remain unchanged.

### 2026-10-16 — Asynchronous SDK diagnostic privacy

- Fresh review confirmed synchronous malformed-result fixes and identified the
  separate asynchronous notification-validation logger path. Added a
  remote-connection context propagated into SDK tasks and a public Python
  log-record-factory redactor for SDK diagnostics only; no SDK private method,
  protocol handler or local connection behavior is patched.
- Actual HTTP2/WSS malformed-notification and malformed-result marker tests,
  plus a local/non-SDK logging preservation regression, pass: 5 tests.
- Reconciled the candidate onto newer `dev`, preserving both independent
  testing-guide contributions. The rebased remote/logging/ledger/independent
  peer/legacy ACP selection passes 124 tests. Contract evidence (122 fixtures),
  shrink-only module caps, changefile presence and install contract pass.
- A fresh complete CI/review pass is still required. Cancelled CI is not a pass
  or a waiver. Implementation merge, effort closure and finalization stay open.

### 2026-10-16 — Sanitized SDK results and adopted session streams

- Current review identified malformed successful SDK results leaking through
  validation exceptions, WS application-idle incorrectly replacing ping/pong
  liveness, late handshakes escaping teardown and immediate session-stream
  traffic preceding new/load-result adoption. Corrected all four without
  changing wire identities or the qualified profile.
- Public SDK calls remain the schema/protocol owner; remote exceptions are
  sanitized before caller errors, logging or turn-failure persistence.
  Immediate HTTP2/WSS updates wait until the client adopts the lifecycle
  result; HTTP prompt sends wait for stream readiness.
- Quiet WSS is checked with actual ping/pong responses beyond the configured
  application-idle interval. A handshake completing after close releases its
  acquired socket. Session cardinality is bounded on both transports.
- The focused remote transport/ledger/independent peer/legacy ACP selection
  passes 120 tests, including malformed-result markers absent from real
  service events, logs and persisted turn failures on both transports.
- A prior-head Linux CI roundtrip lost an HTTP connection after a
  request-scoped form; the focused lane has not reproduced it. SSE failures
  now expose only the exception class for diagnosis, never remote contents.
  Required current-head CI remains a completion gate; no flake waiver or
  bypass is claimed.

### 2026-10-16 — Failed-connection cleanup and historical background inventory

- A delayed prior-head review found historical background-task output entering
  the client's live inventory and failed connections awaiting a later explicit
  stop to close resources. These are corrected in addition to log/telemetry
  replay isolation.
- Remote replay retains completed tool history without scanning it into live
  background work. Real manager stop/resume tests include that exact history
  and verify ordinary stop remains available.
- A single idempotent cleanup task owns failed-generation teardown separately
  from consumer tasks, avoiding self-cancellation/self-await. Consumer drain
  and HTTP/WS close are bounded. Independent authenticated HTTP2/WSS tests
  exercise RPC deadlines and stream loss (including only one HTTP SSE stream
  ending), proving resources close automatically and the server survives.
- The remote/ledger/peer/legacy ACP selection passes 113 tests. Extracted
  background-output normalization to its existing sub-agent helper owner to
  preserve the shrink-only client ceiling. No profile change or rollout.

### 2026-10-16 — Additional protocol and non-resumable-session corrections

- A subsequent review identified CR-only SSE framing and healthy non-loadable
  remote sessions entering the process idle reaper. Incremental parsing now
  accepts LF, CR and CRLF, including CR/LF boundaries split between reads.
- Remote ACP connections are excluded from process idle reaping as well as
  automatic process handoff; an idle conversation is not expendable merely
  because a remote agent cannot load it after disconnect.
- Focused remote transport/session and existing idle-reaper selection:
  50 passed. Current required CI is green; fresh review of these corrections
  remains required before merge. No deployment or transport-profile change.

### 2026-10-16 — Fresh review and full CI corrections

- Fresh review of #6997 found two additional issues: mixed-case HTTPS schemes
  could select the wrong WS scheme, and historical tool updates reached live
  tracking/telemetry reducers. Both are corrected without changing the profile.
- Actual TLS HTTP/2/WSS roundtrips now cover lowercase, uppercase and mixed-case
  HTTPS endpoints. Remote load contains an unfinished historical tool; it is
  retained in history but cannot become live tool state or emit tool telemetry,
  including on database restore and event-log rebuild.
- Required CI exposed an overbroad tolerant-reader inventory treating the two
  strict local endpoint/auth configuration models as peer request/response
  readers. Classify exactly those two immutable settings types; keep every
  other model and any future subclass under the tolerant-reader guard.
- Focused peer/event/telemetry/wire selection: 88 passed. Required CI and a new
  review remain pending; there is no merge or deployment claim.

### 2026-10-16 — Runtime review corrections and immutable contract evidence

- Runtime PR #6997 remains open while required review and CI are completed.
- Corrected all seven initial review findings: token-qualified request-scoped
  form attention, independent SSE idle budgets, volatile affinity retention
  across manager stop/resume, exclusion from process auto-handoff, terminal
  callback routing during cancellation, token-only CLI hints and draining
  previously unsent durable queued work after resume.
- Preserved opaque permission option identity in attention references rather
  than truncating identifiers. No interrupted prompt is automatically replayed.
- Focused remote/attention selection: 102 passed. Independent synthetic TLS
  HTTP/2/SSE and WSS now require the original per-session sticky affinity;
  manager resume drains one queued unsent prompt without replaying the failed
  prompt. An explicit-answer/cancel race preserves terminal callback routing
  and leaves the connection usable without an application write.
- Existing ACP/client/handoff/resume selection: 135 passed. Bridge guard lane:
  164 passed, 8 platform/environment skips. Contract-checker regressions:
  29 passed in the existing contained runner.
- Registered bridge control-plane generation 38 with 23 new fixture
  attestations; retained every historical fixture byte. External ACP remains
  version 1. Source capture hashes are refreshed from the committed corrected
  runtime, not fabricated from a passing mock.
- Install contract, source module caps, headless-launch and scoped Ruff F/E9
  checks passed. No installed payload, deployment or rollout was changed.

### 2026-10-16 — Neutral contract merged and client validation

- Neutral contract PR #6942 merged as
  `ba14c40e975b3888406a98aedb38b372c457a0a0` after fresh approval, no
  unresolved findings and successful required CI. Runtime publication is
  separate; the contract is not a released client implementation claim.
- Focused contained remote/client suite: 109 passed. Existing API
  answer/resume/start/stop selectors: 41 passed. Existing local/SSH/CodeSpace
  transport suites: 105 passed. Shared agent-mcp auth consumer: 46 passed;
  two existing POSIX process-group cases are intentionally skipped on Windows.
- Independent synthetic peers proved real TLS HTTP/2/SSE and WSS, header
  construction/refusal/refresh, explicit decisions and rejection, sessionless
  forms, cancellation, affinity, no redirects, no prompt replay, history-only
  recovery and server-process survival. No remote application deployment was
  exercised.
- Fresh managed test environments resolved the declared dependencies through
  the existing governed/seasoned runner. Full clean-room machine
  installation was not run: installers, launchers and provisioning were not
  changed; first-touch behavior is covered by fresh synthetic clients/servers
  and new ledger sessions. No authorized external endpoint/deployment lane is
  available within this client-only scope.
- Install-contract, headless-launch, changefile presence, touched Python lint
  and shrink-only module-size gates pass. The callback/answer seams were
  extracted rather than widening existing module caps.
- Pulled forward without dropping the independently merged inventory/cache
  capability; remote bridge-control negotiation now uses the next available
  HTTP contract version. The external ACP wire profile remains version 1.
- No installed payload, runtime restart, rollout or server-side business
  validation change was made. Implementation review/CI/merge and final effort
  closure remain open.

### 2026-10-15 — Bounded implementation and neutral contract correction

- D2 selected bridge-owned bounded transports. Implemented against public SDK
  connection/transport entry points without private SDK hooks or source copies.
- Replaced the undeployed draft's author/project-specific wire identities with
  neutral behavior-based identifiers, without compatibility aliases.
- Kept standard ACP permission/form messages and opaque native IDs; reduced
  extension capability flags to versioned explicit-answer policy.
- Actual separate-process synthetic TLS HTTP/2/SSE and WSS consumers have
  exercised initialize/new/load, permission/form roundtrips, cancellation,
  disconnect survival, scoped auth refusal/refresh, cookies and redirects-off.
- No installed payload, daemon rollout, live deployment or server business
  validation was changed. Publication and final validation remain open.

### 2026-10-15 — Qualified contract reviewed and merged

- PR #6927 merged to `dev` as
  `40c0fd93a6505255171e42e860b5a8a726333814` after Copilot approval with
  no open findings and successful required CI, including the PR gate.
- The stable contract is `contract.md`; D1 is resolved and D2 is the only
  remaining pre-implementation decision. The recommended next step is a
  bounded bridge-owned transport using the SDK's public message interface,
  not its private factory implementation.
- No runtime change, dependency installation, transport test or deployment
  occurred. Implementation and validation remain open; retain the worktree.

### 2026-10-15 — Explicit option A and SDK suitability gate

- Option A and namespaced experimental application-approval negotiation were
  explicitly selected. The wire contract now has its own qualified profile
  identifier and preserves opaque server decision/tool-call identity.
- Rechecked public SDK `main`; it still resolves to the inspected pin.
  Examined HTTP/WS factory signatures, producer queue, SSE parser and protocol
  response routing before any dependency installation or runtime edit.
- The public factory seams do not satisfy required bounds/redirect policy
  unchanged. D2 records the concrete choice between a bridge-owned bounded
  message transport and an SDK enhancement. No private patch was attempted.
- No runtime implementation or HTTP/2/WS execution evidence is claimed.

### 2026-10-15 — Reviewed intent merged; implementation remains blocked

- Plan PR #6911 merged to `dev` as
  `aac70d09d214962a5a00824e1f4c1e2a220b2748` after Copilot approval with
  no open findings and successful required CI, including the PR gate.
- Plan schema, new local links, file-size and whitespace checks passed.
  No runtime/integration test pass is claimed for this documentation-only slice.
- Next action: the protocol decision owner selects D1 and settles the
  application-required approval capability declaration through #6908.
  The implementation obligation remains open; the worktree is retained.
- This is a reviewed planning milestone, not a shipped transport, release or
  deployment. Runtime code and dependency changes await the decision.

### 2026-10-15 — Pinned investigation and decision gate

- Confirmed #6908 is open; no competing remote-client PR was identified.
- Inspected ACP client and permission/elicitation handling, shared
  credential-relay source/policy ownership, and agent-mcp auth injection.
- Pinned the RFD to `ccff4e7d2e431880225804a8c136c2ccfcb313d0`.
- Found newer Python SDK remote transport prior art at
  `bdb9ac7054fb2df76a922b60b356c3c65f8a8a64`; it cannot be assumed absent
  or consumed unchanged.
- Preserved D1 before runtime changes. No transport code, dependency change,
  installation, live server connection or deployment has been performed.
