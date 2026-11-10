# Copilot Driver Core Refactor

- **Slug:** `copilot-driver-core`
- **Repo:** copilot-extensions
- **Branch(es):** serial functional PRs from one source worktree
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [Copilot Driver](../../../visions/plugins/agent-remote-driver/README.md)
- **Umbrella issue:** #10765
- **Sub-issues:** Existing #10636, #5930, #5898, #8669 and #5579 retain their scopes.

## Guiding Intent

Implement the reviewed standalone Copilot Driver architecture, not another
launcher facade. Move Session Host and canonical Copilot mux execution into
one independently installable, quickly hoistable core. Manager launch-session,
Worktrees embody/copilot and Bridge create/resume must consume it. Headless
and headed execution expose the same broker, preserve native conversation
identity and obey their distinct lifetime policies.

The completion gate is the complete caller-convergence and receiving flow,
with real consumer validation and required source publication. A package,
an extraction-only PR or one migrated caller is not completion.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Refactor driver | Owns the implementation, tests, serial PRs and closeout | This effort and #10765 |
| Receiver implementation contributor | Owns source-auth receiver/enforcement implementation and its callable contract; renewal stays disabled | Implementation #11781; proof prestage #11758 |
| Existing component owners | Retain receiving acceptance, custody, startup, retention and permissions | #10636, #5930, #5898, #8669, #5579, #4417, #9364, #8375, #6009, #10052 |

## Coordination

- **Topology:** tightly coupled source work with one owner, one checkout and
  dependency-ordered functional outcomes.
- **Host (owns PRs):** Refactor driver.
- **Delegates:** source-auth receiver/enforcement implementation and its callable
  contract move to the contributor owning #11781 (proof prestage #11758).
  The refactor driver releases
  that build claim, reviews the implementation, and owns native qualification
  before positive renewal can be enabled. Qualification infrastructure is not
  a prerequisite for the receiver contributor to start.
  The released source scope includes receiver-owned modules and auth-specific
  changes in `headed/acp-adapter.mjs`, `headed/extension.mjs`,
  `headed/backend.py`, and the coupled `headed/launcher.py`
  `run_headed`/`_PaneChild` lifecycle, plus minimal receiver-owned containment
  support and focused tests/docs. Generic launcher behavior, unrelated Host
  changes, raw Host write observation, Worktrees seed/handoff/native fixtures
  and global process/Job policy are not transferred.
- **Handoff:** continue this canonical effort across contexts. Preserve its
  parent completion gate, exact current slice, effects and one next action.
- Consume the source delivered in #10692/#10748; do not supersede its separate
  native/installed acceptance or claim its live resources. Coordinate actual
  overlapping changes through those existing issues before touching them.

## Context

The [finite vision effort](../../2026/11/05%20copilot-driver-core-vision/README.md)
landed the architecture in #10768 and archived it in #10775. This effort owns
realization. The [implementation map](implementation.md) records the current
source seams, proposed app boundary and validation obligations.

Apply the existing [application installation contract](../../../docs/patterns/fast-application-install-and-cutover.md),
[service contract evolution](../../../docs/patterns/service-contract-evolution.md)
and [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
Incoming readiness and complete current selection define installation success;
old recovery and keeper retirement are separate. Preserve automatic/manual
classification, configuration, work, live keepers and independently owned
connectivity. No second installer, supervisor or global broker is proposed.

## Request

Current execution request, verbatim:

> Now continue with the main refactor effort

The governing architectural request and caller/lifetime requirements are
preserved in the [reviewed vision effort](../../2026/11/05%20copilot-driver-core-vision/README.md#request).
They require a compact redistributable, common Session Host broker, scoped
launch-time extension injection/trust, all named caller migrations, CLI
persistence until closure, and caller-stopped or pending-work-aware idle ACP
retirement. A possible `agent-driver` rename is optional.

## Scope Approvals

This request advances the completed vision into source implementation, normal
review/publication and bounded validation using owned, isolated fixtures.
Preserve the existing namespace during extraction unless a concrete
implementation need changes that recommendation. Naming is not the objective.

No shared runtime/fleet rollout, production session migration or shutdown,
global settings rewrite, credential export, forced claim, new cloud resource
or takeover of another owner's work is authorized here. New effect scope
must use its actual owner. Local isolated native controls are distinct from
installed or external-venue adoption; unavailable lanes remain explicit.
Review of this proposal precedes implementation and is not deployment consent.

## Plan

### Phase 1 - Review the implementation boundary

- [x] Reconcile the merged vision, existing source owners and actual launch seams.
- [x] Record source scope, package recommendation, lifecycle contract and
  dependency-ordered consumer acceptance in [implementation.md](implementation.md).
- [x] Land this proposal after review and required CI before changing runtime code.

### Phase 2 - Standalone headless core and real Bridge consumption

- [x] Extract the canonical Host, protocol, lifecycle and receiving support
  into the independent core package; leave only bounded compatibility/client adapters.
- [x] Produce the ordinary app and compact receiving artifact through existing
  build/install owners, with selected-runtime support discovery.
- [x] Prove direct headless launch, ACP WebSocket/TCP access, reservation/process/native
  identity, prompt acceptance and reconnection without a target coordination suite.
- [x] Migrate Bridge creation and resume through that core while preserving
  policy, workspace, preference, cancellation and retained-host custody.
- [x] Publish the first complete functional flow and steward its PR to merge.

### Phase 3 - Common headed broker and all caller convergence

- [x] Move canonical mux creation, prerequisite composition and pane execution
  into the same core; preserve platform behavior and literal prompt/argument transport.
- [x] Connect the launch-injected, scoped trusted extension to Session Host.
  Headed mode uses the common broker, not a second conversation or Bridge-only inbox.
- [ ] Migrate Manager launch-session and Worktrees embody/copilot, plus existing
  local/remote adapters, to the selected core entrypoints.
- [ ] Prove fresh/resume/reuse, detached/attached/new-window and no-mux behavior
  through the real callers without duplicate creation or seed replay.
- [ ] Publish and merge the complete headed/caller flow without regressing headless use.

### Phase 4 - Lifetime, receiving parity and completion

- [ ] Prove CLI persistence until user closure or explicit authorized retirement,
  including survival after caller cleanup and package replacement.
- [ ] Prove caller-stopped ACP and configured idle grace with no pending tasks,
  agents or schedules; unknown state is not an empty pending set.
- [ ] Qualify ordinary/hoisted receiving, supported skew/rollback, endpoint and
  process identity, exact cleanup and measured cold/warm readiness.
- [ ] Remove duplicated launch implementations; preserve supported adapters and
  retained-session recovery without effectful fallback replay.
- [ ] Update reality docs, packaging, capability declarations, changefiles and
  focused tests; pass required source gates, review and CI at final heads.
- [ ] Resolve every Plan/Validation item or explicitly transfer an independently
  owned adoption gate, then mark Done, archive and finalize the source workspace.

## Validation Plan

- [ ] Core works from its built/copied artifact without source checkout or
  installed Bridge/Worktrees/Manager; extension assets and notices are complete.
- [ ] Each named caller actually invokes the same core; negative controls
  forbid legacy internal spawning and mux creation on the selected new path.
- [ ] Both modes expose the broker and truthful stage distinctions: reservation,
  process/mux, native binding, input readiness, prompt acceptance and restoration.
- [ ] Exact native identity, multiline/quoted seed, permissions and preference
  selections survive new/resume/reuse without staging or delivering a duplicate.
- [ ] Structured pending-work and schedule evidence controls ACP idle expiry;
  work arrival during grace cancels expiry, and CLI foreground idle never expires it.
- [ ] Caller loss, all fixture-owned cleanup Jobs, concurrent creation, takeover,
  stale identities, interrupted delivery, unsupported peers and rollback retain custody.
- [ ] Unit contracts, a real-process consumer check, clean first-touch installation,
  Windows-native and available POSIX/external lanes are recorded separately.
- [ ] Test containment/leases, no-window observations and exact fixture cleanup
  pass; no production prompt or retained session is used as a fixture.
- [ ] Changed source passes relevant lint/type/install/headless/contract and
  unit-portfolio gates, final-head review and required CI without widening them.

## Proposal

The detailed source map, migration boundary and evidence requirements live in
[implementation.md](implementation.md). Its refinements are agent recommendations,
not extra operator authority. Runtime implementation has not started before
this proposal's review gate.

## Journal

### 2026-11-05 - Main refactor preparation

Continued #10765 after the completed vision slice. Confirmed no newer core
implementation claim; existing portable mux source #10748 is merged and its
remaining acceptance stays with #10636. Mapped the real Host package, Manager
launcher, Worktrees creation seam and current receiving artifact. The old
reapable flag and textual sub-agent recovery do not by themselves establish
complete pending tasks/schedules; native structured-state qualification is
an explicit implementation gate. No runtime, installed session or fleet changed.

### 2026-11-05 - Reviewed implementation plan accepted

#10807 merged at 23:51 UTC as
`6dbc0fa6a95bb765f6819bbd49ed1056f255654b`, after exact-head approval with no
active review issues and green required CI. The GitHub ref-rule error cleared
on the retained publication retry; no rules, credentials or branch policy changed.
Re-read the canonical effort and continue Phase 2. Plan delivery is not
refactor completion.

The standalone Node baseline passes. The existing Host packet reports
140 passes, four platform skips and one containment-fixture failure:
`test_containment_reaps_real_session_host_tree` reaches its temporary-storage
limit before writing the Host PID marker. This repair is owned by the immediate
code slice and must pass before functional promotion; no budget or cleanup
assertion is waived.

The failing containment case now passes with the original 20-second wall,
16-process, 1-GiB memory and 1-MiB temporary-storage limits unchanged. The
fixture suppresses unrelated interpreter bytecode writes in its isolated
environment and asserts that its actual trigger file exists. It records both
Host and child identities and confirms their exit after containment. The
focused rerun passed in 7.56 seconds; F/E9 lint passes. This local repair will
ship with the first functional code slice, not as a claim that core extraction
or native receiving acceptance is complete.

### 2026-11-05 - Initial headless executable extraction

Moved the canonical Host, wire protocol/client, continuation observer,
execution preferences and OS support into `agent-remote-driver`'s independent
Python application. Bridge imports the same implementations through bounded
compatibility adapters. Ordinary local launch now probes and runs a retained
core archive with a physical base interpreter, not the caller venv. The
receiving builder is core-owned; its ordinary export contains no Bridge package.
The legacy export retains import adapters and the old guest-policy boundary.

New ACP execution retains the child across disconnect and exits with its child
or an explicit stop. It truthfully reports automatic idle expiry unavailable
until structured task/agent/schedule evidence is qualified. Legacy receiving
preserves disconnect behavior and refuses original local custody it cannot
validate. This does not promote a mode default or migrate an installed Host.

The initial full Host/attachment checkpoint passed 141 tests with four platform
skips. After isolated artifact selection was added, its refreshed packet passed
138 tests with four skips and exposed three issues: two launch fixtures also
intercepted the new support probe, and linked-root validation occurred after
that probe. The fixtures now substitute only actual Host launch; the shared
plain-root validator runs before any process creation. All three focused
controls pass.

Four core receiving tests now pass: source-independent support/preference
imports, relocated real Host/child identity, literal prompt frame transport,
cursor-preserving reconnection and explicit exact-child stop, plus refusal of
unvalidated original custody. The Windows fixture uses the selected physical
interpreter rather than accepting a venv redirector PID as Host identity.
These are real-process synthetic-ACP controls, not native Copilot acceptance.
The moved preference authority packet passes 45 tests with six platform skips,
including executable tamper refusal and materialized-core parity. Its old
Bridge-materialization fixture exceeded its test timeout; it now materializes
the actual smaller core closure without widening the bound. The final Bridge
receiving/containment/failure subset passes eight tests. F/E9 lint and the
installation contract pass.

This is local, unpublished implementation, not Phase 2 completion. Original
keyed creation/resume still uses its Bridge startup admission adapter and
caller environment; preserve those journal checks while making its executing
closure independent. Ordinary application construction/install, ACP-native
acceptance, HTTP/headed execution and all remaining caller/lifetime/receiving
gates remain open. No runtime installation or production session changed.

### 2026-11-06 - Original keyed startup isolation

Keyed Bridge creation and acquisition new/load now execute the same retained,
Bridge-free core archive and physical base interpreter as ordinary launch.
A bounded launch-scoped channel asks the original live Bridge owner to run
the unchanged strict journal readers immediately before child creation.
Requests retain Host/caller PID and birth, exact argv/workspace/session and
effective child session environment. This is live admission, not a durable
receipt, another journal, or permission to replay. Original late readiness
remains held after a frontend timeout; caller loss or refusal fails closed.
New keyed Hosts use the ACP lifetime; supported direct-import/receiving
legacy adapters retain their prior contract.

The three real-process synthetic-ACP create/new/load controls pass. Their
Windows peer now uses the physical base interpreter, avoiding a venv
redirector whose PID is not the eventual native peer identity; the identity
assertions remain intact. Seven standalone core receiving controls pass,
including rejection at final pre-exec and unavailable-caller controls with no
child created. The larger keyed journal/cancellation/lost-reply/late-readiness
packet passed 68 checks and exposed one closed-admission support-probe ordering
issue. The existing original-custody check now also runs before the probe;
that focused control passes. The final core packet passes ten checks, including
strict mode/wire support and missing live-admission refusal before Host creation.
F/E9 lint and installation-contract validation pass. These source tests do not
establish native Copilot or installed adoption.

The slice remains local and unpublished. Ordinary app construction/install,
ACP-over-HTTP, native acceptance, headed and all remaining caller/lifetime/
receiving gates remain open. No runtime or production session was changed.

### 2026-11-06 - First-touch standalone application

The explicit native application packet now passes in 85.96 seconds, within
the application contract's unchanged 300-second bound. It uses the canonical
materializer, governed locked wheel builder, complete native-profile preparer,
application emitter and generated install/command front doors in a private
fixture home. Stage leaves selection absent; regular install publishes the
completed slot; repeat preserves its exact completion record. After removing
the copied input source, the installed app starts an actual Host/child,
exchanges a literal multiline synthetic-ACP frame, and confirms exact child
exit on stop. No shared command, service or installation changes.

Preparation first exposed fixture defects: a symbolic UV path at a native
process boundary, directory labels mistaken for distribution names, incomplete
transitive inputs and a noncanonical snapshot layout. The final packet
consumes the complete governed profile, including PyYAML, rather than editing
dependency metadata, reconstructing a third-party wheel or weakening installed
origin checks. F/E9 lint passes. This proves an isolated Windows first-touch
application, not native Copilot, promotion provenance, headed assets, external
receiving or production rollout. ACP-over-HTTP and native consumer acceptance
remain the next headless-flow gates; the parent effort remains Active.

### 2026-11-06 - HTTP access to the common headless broker

The explicit `--http-port` path now serves the same Host through a bounded,
loopback HTTP/SSE adapter to its TCP controller. The app advertises
`http-acp-v1`, a documented driver-specific ACP exchange, not conformity to
the unfinished ACP HTTP proposal. Both transports share child/native identity,
sequence/ACK retention, literal RPC/permission frames and explicit stop.
New ACP hosts refuse competing controllers without displacement; legacy TCP
hosts keep their supported behavior. Closing HTTP releases control, not the
child. Unknown outbound effects are never retried automatically.

The focused HTTP/TCP and relocated-core packet passes 19 checks in 22.61
seconds. It exercises private nonce/authority refusal, bounded malformed input,
permission responses, exact multiline bytes, cross-transport exclusion,
unacknowledged replay, same-child handover and terminal liveness. The actual
relocated executable performs TCP init/new/prompt/load, HTTP prompt, and exact
child retirement. That control caught a missing module in the receiving
closure; it is now included. A separate control caught premature TCP close
on stop; new ACP hosts now relay final frames/liveness first. F/E9 lint and
the installation contract pass.

This remains local source qualification with an inert ACP peer, not native
Copilot or installed/fleet adoption. Native consumer acceptance precedes the
first functional PR. Headed/caller, structured idle, lifetime and final gates
remain open; the effort stays Active.

### 2026-11-06 - Native headless acceptance and transport reconciliation

The opt-in native Copilot packet passes in 14.98 seconds. A real selected
executable uses a private home/workspace and an offline loopback synthetic model
provider, without account credentials. Three literal turns span TCP new,
HTTP same-process reconnect, and a fresh Host/child loading the same persisted
native session. The fixture verifies exactly one conversation, literal user
history, three provider requests and terminal process cleanup. This qualifies
native ACP, not model-service connectivity, installed adoption or headed mode.
The first attempt exposed a transient Windows state-read sharing error in the
fixture; bounded readiness observation now tolerates it without extending its
deadline.

The retained Host packet passed 161 checks with four platform skips and found
three regressions in cold frontend recovery. The new lower-bound cursor refusal
rejected Bridge's supported cursor-zero bootstrap after the Host had trimmed
ACKed frames. Future cursors still refuse, but an older cursor again resumes
the retained suffix without restoring trimmed history. All three focused
restart/disconnected recovery controls pass after that correction. Explicit
controller exclusion remains; a competing live frontend is not displaced.

Transport reconciliation now distinguishes three existing boundaries:
Bridge's public `acp.v1` WebSocket routes wrap `BridgeAgent`; its remote ACP
client supports WebSocket and HTTP/2 POST/SSE; its internal Host link uses the
sequence/ACK TCP protocol. The new unpublished `http-acp-v1` adapter is not
interoperable with those public ACP transports merely because it uses HTTP/SSE.
Before functional publication, resolve the core's public transport against those
existing contracts rather than promote a parallel dialect by accident.
Preserve the native/core acceptance already obtained and the common Host wire.
The parent remains Active; no installed runtime or production session changed.

### 2026-11-06 - Existing ACP WS contract qualified

Removed the unpublished custom HTTP/SSE prototype in favor of the existing
`acp.v1` WebSocket JSON-RPC convention. The ordinary locked app exposes `/acp`
with per-launch private authentication and shares the TCP Host's exclusive
controller. It creates no Bridge agent or second conversation. WS disconnection
releases control without stopping Copilot; explicit retirement remains the
owning TCP operation. Plain ACP WS has no resumable application receipt:
transport handoff is not proof of client consumption. Bridge's distinct
experimental durable remote-ACP profile is not claimed.

The compact dependency-free archive remains a TCP-only receiving export.
Its actual support declaration omits WS; the ordinary app carries the
`websockets` distribution through the existing governed dependency/profile
and application owners, not a bespoke vendor or receiving resolver.

Thirteen real-loopback WS controls pass in 6.63 seconds. The combined core/WS
selection passes its 23 controls. The existing Bridge `acp-connect` stdio relay
passes against the new endpoint in 2.68 seconds, exercising the real consumer
without changing it. F/E9 lint and installation-contract checks pass.
The actor-free receiving-policy packet now targets the canonical core file
and preserves its existing cases, effect restrictions and source binding.

Native Copilot WS re-initialization, same-process prompting, TCP handover and
fresh-Host native history restoration pass in 35.18 seconds. The fixture uses
the shared direct-Python owner to preserve exact Windows Host PID identity
without losing its venv. One prior native initialization exceeded its existing
30-second RPC bound; the successful rerun retains that bound and now includes
isolated CLI log tails on failure. This does not establish a performance SLA.
The fresh locked application passes in 111.54 seconds, including stage/install,
unchanged-slot repeat, source deletion, TCP/WS literal prompt and exact cleanup.
An initial combined invocation accidentally used the ordinary per-test timeout
for this explicitly bounded application lane; the final run uses its existing
300-second bound, not a widened production or general test limit.

The first headless functional PR remains the immediate publication gate.
Phase 3 headed/caller convergence and Phase 4 lifetime/receiving/native
qualification remain open; the parent effort stays Active.

Final source preparation preserves historical runtime counts with explicit
then-current wording and enrolls the new Python project as pending, matching
the extracted Bridge code's prior typing status. Documentation consistency
passes with fourteen Python runtime projects. Type ownership inventory passes;
an unnecessarily broad enforced-cohort invocation was stopped without claiming
a pass because no admitted typed implementation changed. The seven canonical
receiving-policy units pass under the bounded source test supervisor in
0.66 seconds. The final core/WS packet passes 23 cases in 37.82 seconds.
Python dependency seasoning passes all 553 declarations; the all-ecosystem
audit is blocked by local TLS certificate validation for four existing npm
declarations, with verification preserved.

Added an explicit read-only Windows visibility regression using the existing
desktop observer, with two windowless-core/console-child cycles and exact
cleanup. The attempted native lane refused before any fixture launch because
the shell or foreground window is unobservable in this session. This is
unavailable desktop evidence, not a pass, skip disguised as success or permission
to create a visible control, alter focus, unlock the desktop or move execution.
The source test remains opt-in for a genuinely observable native desktop.
Full desktop calibration and caller-Job durability remain separate parent gates.

### 2026-11-06 - First functional PR and canonical library correction

Opened #10863 after rebasing the committed slice onto current `dev`. The only
manual conflict was the effort index; both this effort's active state and the
other effort's newer status were retained. The published body preserves the
parent tracker, contract limits and unavailable desktop/external evidence.

Both required CI lanes failed before test execution while installing tooling:
the canonical seasoning graph rejected Bridge's sibling-plugin source edge.
The existing materializer and wheel builder impose the same shared-library
attribution rule. Corrected the package boundary instead of weakening those
owners or hiding graph discovery in CI: canonical Python source now lives in
`libs/copilot-driver-core`, distribution `agent-copilot-driver-core`, retaining
the `agent_remote_driver` import namespace and all implementation bytes.
Bridge and the Driver application consume that ordinary first-party library.
The plugin's minimal `src` entrypoint forwards to the same core; its extension
and application activation remain separate.

The actual canonical graph now accepts all forty-one attributed roots. A new
focused consumer control exercises both recursive build closures and that
real graph reader. The corrected core/WS packet passes 24 checks in 27.46
seconds; fresh locked-application acceptance passes in 64.02 seconds and native
Copilot continuity passes in 15.85 seconds. The source-bound receiving-policy
packet passes seven units in 0.84 seconds. Python seasoning passes all 558
declarations. Documentation, module-size, install-contract, library-sync and
version-consistency guards pass without changing their rules.

The core packet reports one held test-daemon cleanup record as `process-reused`;
no mismatched PID was signaled or registry obligation force-cleared. Explicit
Host/child controls still confirm their exact owned exits. Preserve that
diagnostic distinction rather than infer a cleanup grant from a green suite.
The PR update, final review and required CI remain pending; this is not a merge
or a claim that the parent refactor is complete.

### 2026-11-06 - Contract provenance follows the canonical core

The three current authority vectors now reproduce from the committed canonical
core, including startup admission in the attested source closure. Windows
capture reads explicitly separate the revision/path expression from filenames;
the native long-path regression passes without changing Git configuration.
All six former Bridge source names remain registered as thin compatibility
shims for historical provenance. Their current hashes do not relabel old
captures or claim another implementation.

The protocol and settings-reader implementations moved byte-for-byte. The
bounded capture now permits only those two fixed current fixtures to rebind
their source metadata when both the original Git blob and SHA-256 match the
committed canonical source. Their payloads are unchanged and provenance calls
this source-only relocation, not a fresh runtime observation. Changed identity
refuses; semantic-source coverage and historical checks remain enabled.

The focused authority/capture packet passes 69 tests with six platform skips;
the regenerated current capture/wire selection passes 31 cases, the frozen
corpus passes three, and nine bounded checker regressions pass. The complete
contract checker passes both contracts and all 1,022 fixtures. F/E9 lint passes.
Review fixes also retain real archive inspection and ordinary canonical-library
build attribution. Push these corrections to #10863, then require a fresh
passing review and CI before merge. Phase 2 publication remains open; the
headed/caller and lifetime phases are still the parent completion gate.

### 2026-11-06 - Authenticated ordinary control and bounded disconnect cleanup

The next review correctly identified an unauthenticated parallel TCP route on
ordinary standalone execution, a Python 3.10 timeout-class mismatch and WS
backpressure holding controller cleanup. Ordinary ACP now generates a private
nonce when omitted; TCP and WS share it, and the Python launch helper returns
it without exposing it through the handle representation. Explicit legacy
execution keeps its supported optional-authentication contract. No retained
Host is changed.

The WS handler independently observes closure, cancels its RPC pumps and bounds
TCP teardown. The ACP listener observes transport closure independently of child
stdin backpressure, releasing its exact controller without stopping the child
or replaying an uncertain RPC. Legacy listeners retain their stream behavior.
Both timeout handlers catch `asyncio.TimeoutError`, including a regression with
the distinct pre-3.11 exception class. Host/child cleanup runs even if WS cleanup
raises. A first shutdown correction exposed a cancellation race; the passing
regressions retain it as a failure control, not a timing retry.

The combined core/WS packet passes 31 cases. The broader retained Host packet
passed 131 cases with four platform skips and found two test callers still
omitting the now-required generated token; both corrected real-process callers
pass. Native Copilot continuity passes in 15.52 seconds, with unchanged RPC
deadlines and the explicit native test allowance. Fresh locked-application
stage/install/repeat/source-delete/TCP/WS/cleanup passes in 89.74 seconds.
An earlier native invocation selected only the subsuite allowance and hit the
ordinary per-item timeout; it is not acceptance evidence.

Required CI's admitted units passed, while the lifecycle guard found census
records still naming the former Bridge implementation. The census now follows
the canonical core, scans that executable library and counts its ACP
`create_server` site as well as legacy `start_server`. The real guard and two
focused census regressions pass without exemptions. Contract recapture, updated
publication, exact-head review and green required CI remain outstanding.

The committed-source recapture and readback now pass for this correction.
Full contract validation again passes both contracts and 1,022 fixtures;
all 26 capture/frozen-corpus cases pass. Historical captures remain unchanged.
Only updated publication, passing exact-head review and required CI remain
before Phase 2 merge; the parent effort is still Active.

### 2026-11-06 - Destination-local publication and remaining consumer seams

The next review found that archive staging used the system temporary filesystem
before atomic replacement into a caller-selected state directory. Staging now
lives under that destination, preserving atomic publication across selected
volumes. A real archive/reuse/cleanup regression injects the cross-device
boundary; all 12 core/receiving cases pass. This is a deterministic boundary
control, not a claim of a physical second-volume native test.

The committed creation fixture now permits the actual support query while
continuing to forbid Host creation after custody closes. The payload-context
consumer patches the canonical launcher rather than removed shim internals.
The broader creation packet exposed an attested fixture missing its required
session identity; it now supplies that original identity and still exercises
the post-attestation closure check. All 20 selected creation/payload cases pass.
The staged authority closure is unchanged by the archive-builder correction.

Required CI passed admitted units and the corrected source guards, then failed
on the upstream HTTP-52 capture checkpoint missing from fresh-runner preparation.
That exact failure is already owned by #10894 and included in #10888. Added the
downstream PR/run evidence there rather than duplicate its fix or relax the
local-only contract proof. Pull forward the reviewed correction when merged;
#10863 still needs updated publication, passing exact-head review and green CI.

### 2026-11-06 - Initial-output cleanup and truthful legacy selection

Pulled forward the merged HTTP-52 preparation correction from #10888 without
changing Driver implementation bytes. Required CI then passed on the rebased
head. The new review found two further lifecycle edge cases rather than a CI
failure: controller cancellation before the initial HELLO/replay cleanup scope,
and the legacy entrypoint advertising ACP lifetime that it silently replaced.

HELLO, initial replay and normal reads now share the same controller cleanup
scope. Two actual wire-send controls block HELLO or replay, disconnect, then
prove that a new controller can attach, the child remains alive and the original
unacknowledged frame is still replayable. The legacy artifact now advertises
only TCP and legacy lifetime, defaults to that policy and refuses explicit ACP
or WS before execution. The direct legacy API enforces the same boundary;
unknown core lifetime selections also refuse before policy or child effects.
No legacy request is silently coerced to another execution policy.

All 39 core/transport controls pass in 46.55 seconds. Native Copilot continuity
passes again in 12.14 seconds with the unchanged RPC deadlines and exact
fixture cleanup. F/E9 lint passes. The current authority vectors need an
exact-commit refresh for these staged-source changes, followed by updated
publication, exact-head review and required CI. Desktop visibility, headed
convergence and the rest of the parent validation remain open.

The exact-source current capture and readback now pass for this correction.
Full contract validation passes both contracts and 1,022 fixtures; all 26
capture/frozen-corpus controls pass in 8.58 seconds. No historical capture
was rewritten. Updated publication and the new exact-head review/CI remain
the immediate Phase 2 merge gates.

### 2026-11-06 - Headless functional slice merged; headed flow next

#10863 merged at 06:01 UTC as
`8d6b04509c4abc277e05093b23aa3cc3c6086dd5`. The exact published head
`a509439da8fdf140586f56fa7fe17ade98eaac78` received a fresh Copilot
Comment review with no open issues and green required CI (run 42171121737).
The review explicitly retains unavailable Windows desktop visibility and
deferred POSIX acceptance; neither is represented as passing evidence.
Verified that #10765 remains open.

Phase 2's complete headless flow is now published. Continue Phase 3 in the same
source workspace: establish the supported scoped extension-loading contract,
connect headed execution to the common Host and converge the real callers.
All parent validation, pending-work-aware lifetime, caller-Job durability and
receiving/skew gates remain open. No installed runtime or retained session
was updated, migrated or retired.

### 2026-11-06 - Headed broker seam and native-fixture isolation correction

Worktrees' short mux check/create implementation now lives in the independent
core, with an import-only compatibility adapter. Its existing creation-custody
and neutral-directory controls pass (63 tests), together with 18 core controls
including a real isolated-process literal-argument check.

The launch-bound SDK backend connects to the same Session Host and WS adapter.
The SDK edge declares no tools, permission callback, hooks or sensitive
environment access. An isolated native observer loads through launch-scoped
experimental/plugin arguments without a saved permission grant. This is native
extension-loading evidence, not full headed acceptance. The adapter preserves
native input revocation, reports unsupported creation/restoration honestly and
never replays an uncertain send. CLI detach does not retire its native owner.

The first redirected interactive controls did not establish a durable headed
session. A subsequent real-mux fixture invocation was interrupted during a
reported mux crash and has no terminal result. Inspection found an unsafe
whole-server cleanup command relying only on environment-based isolation.
It could address a shared mux; exact crash causality is not established.
Removed that cleanup, introduced an explicit unique socket namespace on every
fixture command, and restricted commands to the exact fixture session.
Whole-server operations, foreign targets and routing overrides now refuse
before process creation. The native mux lane requires a separate explicit
opt-in and has not been rerun after the incident. No native headed pass is
claimed, and no existing mux was stopped or repaired during recovery.

The recovery packet passes 52 Python tests without launching a native mux,
seven SDK-adapter race controls, F/E9 lint and the actual daemon census.
Phase 3 remains in progress: native headed acceptance, durable execution,
Manager and remaining caller convergence, distribution and final review/CI
are still outstanding. The parent effort remains Active.

The selected native mux's read-only namespace control confirms that a live
default-namespace session is absent under a fresh explicit fixture namespace.
After the safety regression packet passed, the private native lane was
explicitly selected. Collection first exposed a missing test-helper import;
the corrected lane then exposed an incorrectly joined executable path at the
pane boundary. Matching the real caller's executable-name and single-quoted
file transport fixes that fixture error without changing process permissions.

The namespaced native lane now passes in 14.61 seconds: one original literal
seed, a second prompt through WS, a third through TCP, one native history,
detach survival and exact native/extension cleanup through the fixture's
identity-checked session. Whole-server cleanup remains prohibited. This is
isolated native headed-broker evidence, not all-caller convergence, caller-Job
durability, desktop-visibility qualification or installed rollout.

### 2026-11-06 - Standalone headed execution coordinator

The explicit headed entrypoint now runs in the caller's existing pane, retaining
the exact native child and its original interactive argv. It verifies the
selected session, rechecks original startup admission, injects only its retained
extension and publishes broker readiness after the SDK's session/PID binding.
Native startup and full restoration remain distinct. SDK connection loss or
broker setup failure after creation retains the interactive session without
replay; explicit stop targets the retained child handle, never a mux name.

The canonical receiving export includes those same headed modules and extension
resources. Its command composer retains the content-addressed executable under
the original caller attempt and uses a physical base interpreter rather than
the caller's environment. Selected support is queried before launch effects.
The ordinary app supports WS and TCP; the compact export remains TCP-only.

Both explicitly selected private native mux coordinator cases pass (27.53 seconds
test execution, 58.12 seconds including preparation and cleanup): ordinary WS
and retained-artifact TCP, one literal original seed, a second prompt, physical
native PID in the broker handshake, detach survival, explicit exact-child stop
and confirmed coordinator/native/extension exit. The first coordinator run
exposed a fixture cleanup observation race after the functional assertions;
bounded absence confirmation fixes it without weakening identity checks.
The final focused Python packet passes 52 tests, F/E9 lint passes, and both
headless launch and daemon-coverage guards pass.

Manager, Worktrees and headed Bridge caller migration is still open. In
particular, original native-seed receipts must retain the actual Copilot PID,
not substitute the new coordinator's PID. No Phase 3 publication, installed
rollout, caller-Job durability, complete restoration or parent completion is
claimed by this checkpoint.

Tracing the original native console owner exposed one additional coordinator
requirement: Ctrl+C/Break must reach Copilot without cancelling Python's wait
and retiring the session. The headed CLI now defers those terminal events with
a callable handler (not an inherited `SIG_IGN`) and restores the caller's
handlers afterward. The focused coordinator packet passes ten tests, including
an actual raised SIGINT during its wait; F/E9 lint passes. This signal control
is not a native keyboard/desktop qualification.

### 2026-11-06 - Original-owner headed receipts and first invocation adapter

The core now advertises and publishes a physical native process receipt,
distinct from coordinator creation. Its bounded reader rejects duplicate
fields, unsupported state, another session/nonce and stale or unavailable
coordinator/native identities. Worktrees expands its finite provider first:
old direct callbacks and preparation frames keep their semantics, while a
negotiated headed callback resolves Copilot through the original caller's
private binding. Tokens remain off the command line.

The selected-session Python invocation boundary now calls the same retained
core executable. A live original-owner channel rechecks the original attempt,
incarnation, current seed and cold worktree immediately before native creation.
Only the physical native receipt completes the staged seed. A missing receipt
retains the wait and handoff, records the actual return separately from receipt
certainty and never replays or restores an admitted launch. Terminal events
continue to be owned by native Copilot. Consoleless coordinator/native
creation uses the existing no-window primitive.

The core receipt/caller packet passes 32 tests; the new Worktrees invocation
and unsupported-command controls pass ten. The prior finite-provider packet
passed 74 cases with two fixture expectation failures; the corrected
14-case residual passes, including both failures and parser/discovery controls.
Both original direct native coordinator paths pass in separately selected
ordinary and receiving runs. The first combined attempt stalled before
ordinary binding without useful retained diagnostics; a bounded exact-fixture
pane capture was added, and the ordinary diagnostic rerun passed in 20.68
seconds. This does not establish that the earlier stall's cause is resolved.

A third, real original-owner process lane passes in 24.05 seconds: the live
admission callback runs once, the parent receives the distinct native PID,
one literal seed and a second broker prompt share the native history,
detach retains the session, and exact stop confirms all four fixture processes
exit. This is native core-caller evidence, not a full native Worktrees/Manager
launch or caller-Job durability proof. Copied Windows/POSIX shell migration,
ordinary unselected-session launch, Manager and headed Bridge convergence
remain open; Phase 3 and the parent effort remain Active.

The refreshed regression packet passes 72 core/headed/broker tests in 46.52
seconds and 111 affected finite-provider/seed-lifecycle controls in 45.14
seconds. F/E9 lint, the actual headless-launch guard and daemon coverage audit
all pass. The latter classifies the new original-caller coordinator spawn as
venue-agent execution, not a durable service. No installed footprint changed.

### 2026-11-06 - Copied Windows original-owner convergence

The finite provider now separately advertises headed backend preparation,
restricted to full JSON prepare replies. The selected original attempt retains
the independent core and one local native session identity. A copied Windows
setup discovers support against a captured completed immutable slot; old
providers keep direct execution without receiving new syntax. Each finite call
still refuses a changed current selection before effects.

The retained PowerShell owner, not another Python waiter, serves the bounded
private startup channel. It verifies its exact coordinator handle, PID/birth,
nonce and original native argv/session/cwd, then invokes the original finite
policy recheck before granting native creation. The headed callback records
Copilot's distinct physical PID and finishes the seed. Lost admission/receipt
confirmation keeps the original wait and handoff without restore or replay;
uncertain return observations preserve the actual exit independently.
Ordinary no-seed native setup anchors this path in its retained setup record.
Custom no-seed launchers keep their existing direct invocation.

The first installed consumer check exposed a real artifact bug: zipapp's
generated entrypoint ignored the headed function's return value, reporting a
nonzero native exit as zero. The retained artifact now includes an explicit
exit-propagating entrypoint in its content-addressed source closure. A real
retained-executable regression confirms native exit 17. Both fresh disposable
installed-runtime consumers now pass (293.03 seconds test execution): seeded
and no-seed copied setup, one selected identity, the core's injected extension
asset, distinct physical native/coordinator/owner identities, exact native
receipt, seed settlement and actual exit 7. These use a synthetic native
executable, not Copilot SDK or complete Manager acceptance.

The regression packets pass 51 core/headed/artifact cases and 184 Worktrees
provider/copied-owner/shell-asset cases with 16 platform skips. Six new shell
controls cover both owners, changed argv, duplicate request fields, policy
refusal and a lost native receipt. Their initial fixture failure used a venv
trampoline that changed the coordinator's parent; the corrected fixture uses
the production physical-interpreter boundary. The original packaging failure
is fixed, not waived. F/E9 lint, the actual headless-launch guard and whitespace
checks pass.

Tracing Manager also confirmed its hidden `--ahp` client form. Both Worktrees
selectors and the independent core explicitly exclude AHP and remote-connect
from local native creation. The final focused selector/provider packet passes
24 cases and the pre-effect core refusal packet passes seven. Manager mux
creation, POSIX copied shells, headed Bridge, full native caller evidence,
caller-Job durability and Phase 4 remain open. No Phase 3 publication,
installed rollout or parent completion is claimed.

### 2026-11-06 - Manager Windows shared creation consumer

The selected core now provides a finite, duplicate-refusing literal JSON
creation boundary with its own read-only support declaration. Manager
captures the executing interpreter and physical entrypoint before creation,
then delegates to the canonical check/create-or-join engine without acquiring
its old lease. Selected older providers retain the pre-effect legacy path.
Manager still owns terminal attachment, UI, session options, handoff and
original native admission. Unknown or lost replies preserve the handoffs and
cannot trigger legacy retry or owned-session cleanup.

The focused packet passes 14 core parser controls, 20 Manager process/legacy
controls and 19 Worktrees/native lease controls. The latter runs actual
Manager PowerShell and Worktrees Python against a disposable native mux
protocol executable, in both creation orders and with both current and
legacy Manager selection. They join at discovery, release the same kernel
lease before client return, preserve literal argv and allow another target
to create concurrently. The Manager process controls also drain large stderr
and preserve unknown results without fallback. These are isolated synthetic
protocol controls, not native Copilot or full Manager UI/attachment evidence.
POSIX shells, headed Bridge, caller-Job durability and Phase 4 remain open;
the parent effort stays Active and no installed rollout occurred.

### 2026-11-06 - Seeded POSIX original-owner adapter

The selected native provider separately advertises `posix-headed-env-v1`.
Copied seeded POSIX setup selects it before preparation, without changing
the version-1 NUL reply. Its existing long-lived exec owner delegates eligible
local native commands to the retained core caller. Admission remains live
and tied to the original attempt; the native callback records Copilot's
distinct PID/birth and finishes the seed. Lost native binding preserves the
handoff and actual process exit without replay. Older providers and
unsupported commands retain their existing direct exec semantics.

The original mixed packet reports 154 passes, eight platform skips and three
fixture failures. Two old shell fixtures had replaced setup discovery but
not the newly selected backend capability; they now explicitly model both
legacy and headed support. The third supplied a nonnumeric synthetic birth,
which the real execution-receipt validator correctly refused; it now uses a
valid birth while preserving real identities for other processes. The
seven-case residual passes, including all three original failures and both
support selections. No failure is waived or deadline enlarged.

The shared CLI also maps negative POSIX native signal exits to conventional
shell status, while the retained state keeps the raw native result. The
68-case core/parser/artifact packet passes, including the ordinary retained
native exit and signal-conversion control. A native POSIX signal-artifact
case is separately platform-selected. F/E9, no-window and whitespace checks
pass. This Windows host has no development WSL distribution; only the Docker
internal distribution is present and was not used. These results do not
qualify native POSIX terminal/SDK behavior. Admission-only/no-seed POSIX
setup, Manager POSIX creation, headed Bridge and the parent Phase 3/4 gates
remain open. No source publication or installed rollout is claimed.

### 2026-11-06 - POSIX admission-only retained owner

The provider now separately advertises `posix-live-owner-v1` setup admission.
The planner and copied shell require it before migrating no-seed setup.
Setup and backend preparation explicitly carry admission-only, preserved-seed
and native-resume constraints, without claiming a prompt. The original headed
owner retains the kernel creation lease across the core wait and repeats its
original intent/liveness check in the native callback. Direct/custom exec
acquires the lease after descriptor scrubbing and retains it through exec,
with kernel close-on-exec release. Unsupported providers keep the legacy
owner before effects; late intent changes refuse rather than claim or replay.

The first focused packet reports 194 passes, 14 platform skips and three
new fixture failures: a mocked fork had no observer retaining the pipe reader,
and the native-resume fixture named a head without its required session entry.
The corrected portable controls retain real kernel-lease contention and
exercise the intended exec boundary without a fictitious reader. Both
failed direct cases and the resume case pass in the 20-case residual.
The final packet passes 27 admission/CLI/Bash controls with six native POSIX
skips. Added native cases cover actual exec identity, close-on-exec release
and late-intent refusal, but remain unexecuted on this Windows host. The
copied native setup fixture now waits for the no-seed backend's receipt too.
No deadline or assertion was waived. Native POSIX terminal/SDK qualification,
Manager POSIX creation, headed Bridge and the parent Phase 3/4 gates remain
open; this source slice is not parent completion or installed rollout.

### 2026-11-06 - Manager POSIX finite creation consumer

The shared core separately advertises `posix-tmux-create-or-join`; Manager's
Bash launcher discovers it from the selected Worktrees runtime before
creation effects and captures the physical interpreter/entrypoint. Literal
JSON requests preserve pane argv and environment flags; bounded caller-owned
provider processes preserve AHP auth scrubbing. Missing support keeps the
legacy path before effects. Once selected, rejection or unknown results never
enter legacy retry or owned-session cleanup, and unknown creation retains the
original AHP token handoff. A confirmed concurrent join follows the existing
attach-only path without pane creation or new-seed delivery.

The core's POSIX consumer checks the exact target before preparing a pane.
Nonzero dispatched clients are uncertain, including a native creation race;
neither the finite CLI nor Worktrees can mistake them for confirmed creation
or clean up a potentially admitted pane's arguments. The focused packets pass
22 core parser/creation cases, 22 Manager copied-Bash/auth/handoff cases and
63 existing Worktrees creation/lease/CWD controls. These are isolated source
and synthetic-protocol controls, not native POSIX tmux/Copilot or full Manager
UI evidence. Native POSIX qualification, headed Bridge and Phase 4 remain
open; the parent effort stays Active and no installed rollout occurred.

### 2026-11-06 - Bridge headed delivery on the common broker

The headed Bridge launch owners already delegate creation and native resume
to Worktrees, but its inbox extension still had an independent SDK sender.
That sender would look like foreign input to the new core adapter and revoke
its control. The original core launch now supplies a private versioned
client declaration and retains a bounded JavaScript TCP client alongside
the SDK edge. Bridge selects that client for retained inbox delivery,
preserving SQLite final admission, original handoffs and consumption history.
Ordinary old launches keep their selection; no global mode default changed.

The additive, explicitly advertised `copilot-driver/native-send` operation
preserves queued/immediate urgency and returns the actual SDK message ID,
not inferred completion. Standard ACP prompt semantics stay unchanged.
Both SDK uncertainty and native user input hold control without replay;
the client never reconnects or consumes a prior controller's buffered RPCs.
Portable native loadout observations still gate the common send, including
duplicate/missing-root refusal. Shutdown closes only the broker client,
never the native conversation. Old peer, missing support and selected-client
failure cannot fall through to a raw SDK send or the old driver.

The final JavaScript packet passes 57 controls, including actual extension
body wiring, retained receipt recovery, old-driver compatibility and the
new framed client/SDK edge. The core Python packet passes 58 cases with one
native POSIX skip; it includes the staged Node client talking to the real
Python Session Host and retained artifact tests. The isolated Windows native
Bridge-client case passes in 22.03 seconds: actual Copilot SDK loading, one
original seed plus one connector-delivered prompt in the same conversation,
native acceptance, detach survival, and all exact private fixture identities
retired. It uses an offline local model and the reserved private mux wrapper;
no shared mux, productive session or installed runtime was changed.

These are real connector/SDK observations, not full Bridge acquisition/UI,
ordinary/hoisted restoration, all caller cleanup-Job or remaining Phase 4
qualification. Controller restoration after a previous attachment remains
explicitly unqualified. The parent stays Active; source publication and
required final-head checks/review still remain.

### 2026-11-06 - Current contract refresh preparation

The committed headed implementation's current preference-authority capture
and no-write verification pass at `91af896dd9e2b6ed29c53775c68af4cd2e11a882`.
The publication guard identified stale registrations for the Bridge extension,
its selected-driver adapter and the core Host. The Host's non-semantic H1
registration now describes its separately selected CLI policy; historical
framing vectors remain unchanged. The current HTTP-52 owner's prepare pass
updates semantic source declarations for the two Bridge consumers. Those
declarations are not execution evidence: the bounded HTTP-52 capture and
readback still must pass before publication. Install-contract and canonical
library-sync checks pass. No historical generation or semantic source
classification was weakened.

The first HTTP-52 execution ran 133 passing cases but correctly refused
publication because three link controls had an unconditional Windows skip.
The host already supports the same native file/directory symlink operations
under its current user permissions. Removing that platform-only exclusion
preserves the real linked inputs and original assertions; all ten slot/
generation controls pass in 2.61 seconds, including the three skipped cases.
No elevation, OS setting, mock replacement, portfolio reduction or deadline
change was used. The complete capture must rerun from this committed source;
the earlier partial packet is not capture evidence.

The complete HTTP-52 writer and independent no-write verification both pass
all 136 cases from `29c76c46190445bba676d611ed1d4d3cd7af5861`, without skips.
The writer publishes only its 110 planned current outputs; historical
generations remain unchanged. The full contract guard passes two contracts
and 1,022 fixtures. Scoped module-size, documentation consistency, final-blob
size and changefile-presence checks also pass. Required CI/review, publication
and the parent's unresolved caller/lifetime/receiving gates remain open.

Self-review moved the POSIX existence query after the existing joined-gate
decision, preserving an admitted join without another native query or pane
preparation. The shared parser/creation packet passes 24 cases, including
both-platform negative controls. The default type checker completed its first
scopes, then refused an existing target-list artifact in a shared prepared
environment. That retained file was not overwritten or deleted. An existing
supported independent interpreter is selected for clean checker preparation;
the complete admitted-scope result remains pending.

The independent existing Python 3.11 checker, explicitly prepared through its
normal hash-locked owner, passes all 40 admitted strict projects on both
declared platforms. The retained Python 3.12 target-list artifact remains
untouched; no ownership check or pending-project classification changed.

An additional native-input control expires the original argv-seed exception
when the first controlled send is admitted. Missing an early seed event
cannot exempt a later identical human message. The final JavaScript packet
passes 58 controls; the isolated native Bridge connector flow is refreshed
and passes in 21.86 seconds with the same exact cleanup assertions. Upstream
has advanced during this slice, so the committed source must pull forward
before publication and refresh any genuinely affected evidence.

### 2026-11-06 - Upstream integration and current headed consumer evidence

Pulled forward the current contribution branch without changing the owned
runtime implementation. The conflicts were confined to generated contract
evidence. Canonical materialization confirms that upstream retained the
original session-host contract unchanged; its current authority update is
preserved. The HTTP registry reconciles disjoint source declarations and
retains both complete provenance histories, including upstream's current
consumer fixes. Immutable archived prefixes and historical generations are
unchanged.

The committed combined source's HTTP-52 writer and independent no-write
reproduction both pass all 150 controls without skips. This includes the
upstream additions rather than substituting the earlier 136-case packet.
The writer publishes only its 110 planned current outputs. Install contract,
canonical library sync, scoped module/final-blob size, changefile presence,
documentation consistency, headless-launch checks and focused F/E9 lint pass.
All 40 admitted strict type projects pass on both declared platforms; the
32 pending projects remain unqualified.

The full dependency-age audit reports four public npm TLS-handshake failures
with certificate validation enabled and no Python age violation. This is
unavailable local npm transport evidence, not a seasoning pass or a reason to
disable TLS. Required CI must independently pass the complete audit.

Complete contract validation passes both contracts and all 1,022 fixtures.
Publication, exact-head review and required CI remain the immediate gates.
The isolated native connector evidence does not
resolve the complete native caller matrix, all launching cleanup Jobs,
restoration, receiving/skew or pending-work-aware ACP lifetime. Those remain
the active parent objective; no installed runtime was updated.

The next publication attempt encountered another reviewed upstream expansion:
the retained acquisition writer adds real delegated CLI controls and expands
the current HTTP-52 packet to 190 cases. Its generated metadata was reconciled
without dropping either source history. The headed implementation is unchanged.

The expanded packet correctly refused publication after a failed case and the
existing 300-second suite bound. All 13 new writer controls pass without capture
instrumentation, but the committed-source writer case exceeds its unchanged
120-second limit. Live interpreter observations show serial helper/CLI startup,
with no rejected source. The fixture unnecessarily imports the full CLI before
its read-only snapshot and protect helpers. Deferring those CLI imports keeps
each real subprocess and all canonical command/parser/writer checks while
removing unrelated startup from those helpers. No deadline, portfolio or source
guard was relaxed. Committed-source requalification remains pending.

The narrower helper imports remove the failing control, but the complete
packet still reaches only 182 passing cases before its unchanged suite bound.
The capture guard also repeatedly scans the complete source-path portfolio
for every Python execution event. Its immutable path mapping is now indexed
once per interpreter admission; each matching execution still rereads and
verifies its bytes and code and retains the same durable receipt.

Nine focused lookup, real-process tamper and missing-receipt controls pass in
15.62 seconds under the original limits. The narrow runtime-import control
uses actual runtime bytes and an isolated request rather than constructing
the unrelated full historical publication cohort. Historical publication
tests and their corpus are unchanged. The full current-generation capture,
independent reproduction and publication gates remain pending.

The indexed capture completes within the original suite bound: 189 controls
pass and one correctly exposes PID reuse in the interpreter receipt filename.
The guard formerly refused a new process whenever Windows recycled an earlier
PID. Each admission now exclusively allocates a distinct receipt while retaining
the actual PID, parent, source/code checks and aggregate launch/entrypoint
matching. Existing receipts are never overwritten or deleted. Eleven lookup,
PID-reuse, real-process tamper and missing-receipt controls pass in 16.12 seconds.
The complete writer and independent reproduction must still pass.

All 190 assertions subsequently pass, but startup and cleanup exceed the same
300-second contained-run bound, so publication still refuses. Receipt writes
now use one interpreter-lifetime stream instead of reopening the file for every
event. A lock serializes records; every record still flushes and fsyncs before
returning. The eleven focused guard controls pass again in 17.70 seconds.
The complete contained run, not the inner pytest summary alone, remains the gate.

### 2026-11-06 - Preserve actual Windows launch and entrypoint audit coverage

The complete 190-case HTTP-52 portfolio passed in 287.31 seconds, with a
successful contained-runner exit, but capture correctly refused publication
at interpreter coverage. A bounded actual-owner reproduction found 27 child
entrypoints and zero launch receipts. Windows caller-owned execution uses
native CreateProcess rather than Popen, while ordinary Windows Popen audits
carry a command-line string, not an argv list. Venv redirectors also introduced
an intermediate native parent.

The native creation boundary now emits the standard subprocess audit before
creation, including refusal propagation. Capture decodes ordinary Windows
command lines through the native parser. The two record-owner fixtures select
the existing direct interpreter helper and preserve their venv environment,
without changing native PID/parent receipts or weakening launch counts.
Two native audit/refusal controls and eight argument-shape controls pass;
focused F/E9 lint passes. Exact committed-source capture, independent no-write
capture and the registry gate remain outstanding. No evidence was published
from the rejected run.

The corrected exact-source writer passes all 190 cases in 281.51 seconds;
the contained runner exits successfully in 295.47 seconds. Full entrypoint,
launch, installation-source and portfolio verification accepts the run and
publishes 135 source-bound outputs. The original 300-second suite and
120-second item limits are unchanged. Independent no-write reproduction and
the complete registry checker still gate readiness; the functional headed
slice is being published as a draft while those run.

Managed draft publication stopped before pushing because upstream moved again.
The pinned integration contained 132 generated-contract conflicts and no runtime
conflict. Canonical registry reconciliation preserves both complete histories
(9,074 HTTP and 86 Host provenance records), the upstream source changes and
seven additional inspection controls. The 190-case result remains scoped to
its original committed source. The now-197-case writer, independent no-write
check and full registry validation still gate readiness. A draft preserves the
functional slice for iteration rather than requesting review of stale evidence.

Draft #11016 is open, with no parent-closing reference. Its initial CI found
the native caller-owned source admission hash and six shared-library consumer
changefiles missing; both are corrected without changing the admitted node
inventory or budget. The first combined 197-case capture stopped at case 169,
the expanded actual-owner flow. A bounded single-case reproduction likewise
reached the final read-only snapshot at its original item deadline, with all
31 launches matching 31 interpreter starts. Neither rejected run publishes
evidence.

Repeated state assertions now use the canonical Worktrees record reader in
the test process, rather than cold-starting an extra interpreter for each
observation. Protection and every actual capability/read/parser/writer command
still run in separate real owner processes, each retaining the reachable
unqualified-resident trap. No write assertion, launch/entrypoint/source check,
portfolio item or timeout is removed. Requalification is still outstanding.

The state-reader change alone did not settle the captured case. A guarded
single-command profile measured 4.63 seconds and about 360 imports for an
ordinary capability query through the unrelated full CLI front door. The
finite owner fixture now composes the existing production parser and handler
for capabilities and exact record reads, retaining explicit project binding,
the reachable resident trap and separate native processes. Session writes
still traverse the full front door. This is a fixture cost reduction, not a
new production dispatch path or a mocked provider response.

All twelve changed-scope refusal controls pass. The actual owner flow passes
without capture in 47.24 seconds after correcting the narrow parser's active
project selection. Exact-source captured qualification remains required.
The complete changefile check now covers all fourteen additional shared-library
consumers; source admission verifies all 607 sources with the original 4,318
unit nodes unchanged.

### 2026-11-06 - Combined headed evidence reproduced

The complete 197-case HTTP-52 writer passes in 284.76 seconds, including the
seven upstream inspection cases. Its contained runner succeeds in 300.97
seconds including preparation and cleanup. The canonical source, role,
portfolio and launch/entrypoint verifier accepts all 132 bound outputs.
An independent run against the same committed runtime, without preparation or
write mode, also passes all 197 cases in 285.53 seconds; its contained runner
succeeds in 342.88 seconds including preparation and cleanup. The original
300-second suite, per-item limits and evidence checks are unchanged.

The two CI-reported process-census counts now include the added finite Windows
launch seams: three Worktrees and four Manager `Process.Start` sites. Both
retain their inspected caller-contained classification. The census check and
all 607 admitted source hashes pass without changing source admission, unit
inventory, lifetime classification or resource budgets. Full registry and
final-head required CI/review remain separate readiness gates. No installed
runtime, retained session or fleet is changed; the parent effort stays Active.

The full registry subsequently passes with two contracts and 1,044 fixtures.
Refreshed install-contract, canonical-library sync, documentation consistency,
headless-launch, changed-module size and complete changefile-presence guards
also pass. These source results do not close the native caller, cleanup-Job,
receiving, restoration or pending-work-aware lifetime acceptance matrix.

### 2026-11-06 - Preserve Windows admission-only launch intent

The first review of #11016 identified a real Windows copied-setup regression:
the headed backend lost admission-only, preserved-seed and native-resume
constraints after setup. Propagate those constraints through actual backend
preparation and the final pre-creation admission callback. Ordinary outcome
callbacks still accept only their original execution identity.

Nine focused controls pass, including the actual finite admission CLI's
preserved-seed, replacement-seed and now-live native-resume decisions and the
copied shell's preparation/admission argument transport. Three disposable
installed-runtime copied-shell cases pass in 242.29 seconds, with a successful
264.12-second contained runner: no seed, ordinary seeded execution and a
preserved seed. The last remains staged with no handoff or prompt argument.
This is real provider/copy/core composition with a synthetic native executable,
not another native Copilot or installed shared-runtime adoption claim.

The exact-branch CI run on the prior head passed unit coverage, strict typing,
dependency seasoning, AST/process census and the contract registry, then
reported a stale generated procutil copy in the payload admission closure.
Canonical payload generation repairs that copy; all thirteen manifests now
verify, changed Python passes F/E9, and all 609 current admitted sources verify.

The next pinned upstream integration auto-merges runtime code and source
admission; all 132 conflicts are generated contract evidence. Canonical
registry publication preserves both complete histories (9,481 HTTP and 86 Host
provenance rows). The core, native launch boundaries and headed client remain
unchanged by integration. New combined capture and exact-head review/CI remain
required after this review fix; the prior 197-case evidence is source-scoped.

The expanded 229-case monolithic capture reaches its unchanged 300-second
group limit and publishes nothing. A guarded diagnostic selects all 32 added
cases and both observer variants: all 34 pass in 115.46 seconds, with a
129.34-second contained runner. Named timings identify the new actual retained
serializer case at 75.43 seconds, rather than a hung child or failed assertion.

The HTTP-52 capture now separates its two independent real-owner flows from
the protocol controls. Each runner retains its original group/item/process/
memory/storage limits. Both groups share the original 600-second Windows or
240-second POSIX probe deadline, not a fresh allowance per group. The final
verifier still requires the exact complete portfolio, one unchanged source
proof, all interpreter/launch/role/source-closure receipts and the expected
observer report. This does not claim a monolithic 300-second or native
performance pass. Six contained controls pass for literal report transport,
complete partitioning, omitted/duplicate/foreign cases and aggregate expiry.
The new real-owner fixture also uses the existing direct interpreter helper
to preserve the selected venv without a Windows redirector parent.

The partitioned capture declares its exact observer count to the existing
verifier. Both observer entrypoints must be proven, with missing, extra or
entry-less observers refused. Single-group callers retain the original
one-observer default, and child launch/role/source-closure requirements are
unchanged. All five focused observer controls pass.

The first partitioned attempt passes the existing actual-owner flow, then
refuses the new fixture before its reachable-resident assertion: Bridge's
normal environment scrub removed the direct interpreter's selected venv.
Restore that explicit interpreter environment after the scrub, as the existing
owner fixture already does. No evidence is published by the rejected attempt.

### 2026-11-06 - Complete 229-case evidence accepted

The corrected real serializer case passes in 85.58 seconds, including its
78.09-second call, and the unchanged child verifier accepts its actual
interpreter/source/native-parent receipts. The complete writer then passes
all 229 cases: two real-owner flows in 156.68 seconds and 227 protocol controls
in 241.47 seconds. Their contained runners succeed in 169.48 and 255.69
seconds including preparation and cleanup.

An independent run against the same committed runtime, without preparation or
write mode, also passes all 229 cases: 163.29 and 228.46 seconds for the two
groups, with successful contained runners of 218.84 and 243.03 seconds.
Both captures satisfy the original shared probe deadline and every exact
portfolio, observer, role, launch/entrypoint and required-source check. The
writer publishes 139 bound outputs; this is grouped functional evidence, not
a monolithic 300-second or native-readiness performance claim.

The complete registry passes with two contracts and 1,050 fixtures. Refreshed
process census, install-contract (13 plugins), canonical-library sync (four
libraries), documentation consistency (24 plugins), headless-launch checks
(13 adopters), effort structure, all 13 generated payload manifests and all
609 admitted source hashes pass. Final-head review and required CI remain
independent gates; the parent caller/lifetime/receiving matrix stays Active.

### 2026-11-06 - Final generated-consumer and authority repairs

The existing finite CLI compatibility packet passes all 126 cases in 30.67
seconds, with a successful 131.55-second contained runner. The final six
capture/report controls also pass after the exact-observer integration.

Required CI then identifies the caller-lifetime source archive as stale.
Canonical installation-context synchronization regenerates its source archive
and thirteen consumer copies, followed by the Worktrees payload generator for
its nested admission copy. All installation-context and thirteen payload
manifest checks pass. An actual isolated archive consumer proves that its
audit hook can refuse native creation before the fixture child writes its
marker. No installed archive or service is changed.

The next review confirms the admission-only finding is resolved and identifies
three stale current-authority digests. Canonical final-source authority capture
and its independent no-write check pass; all 26 authority corpus/capture
controls pass in 6.54 seconds, with a successful 96.92-second runner. The full
registry still passes with two contracts and 1,050 fixtures.

These repairs change generated archives, current vectors and their
registry/provenance only. Their changed paths do not overlap the verified
HTTP-52 source closure, and all 609 admitted source hashes still verify.
The complete 229-case writer/no-write results remain scoped to that unchanged
runtime rather than being rerun for unrelated generated bytes. New-head
review and required CI remain mandatory before merge.

### 2026-11-06 - Reconcile the expanded CLI writer portfolio

Further upstream integration adds portable configuration and explicit keyed
creation CLI coverage. Runtime, the real-owner fixture and source admission
auto-merge; all 139 conflicts are generated contract evidence. Canonical
publication preserves both complete histories (9,895 HTTP and 91 Host rows).
The core, native boundaries, admission-only fix and headed client remain
byte-identical, and the current authority digest still matches its 17 modules.

The complete 245-case writer passes: two real-owner flows in 161.71 seconds
(238.81 including preparation/cleanup) and 243 protocol/CLI controls in
229.46 seconds (245.31 including preparation/cleanup). Independent no-write
reproduction against the same committed source also passes all 245 cases:
161.44 and 230.79 seconds, with successful runners of 250.05 and 246.03
seconds. All 139 outputs satisfy the unchanged aggregate deadline, exact
portfolio and observer/child/native-parent/source-closure requirements.

The full registry passes with two contracts and 1,050 fixtures. Final source
guards identify MCP's producer hash as another dependency of the generated
archive change. Canonical footprint refresh changes only that scripts digest;
contract revision 36, effects and runtime source are unchanged. Its path does
not overlap the captured HTTP source closure. Install-contract, library sync,
docs, headless launch, effort structure, all generated consumers and all 615
admitted source hashes then pass. New-head review and required CI still gate
merge; the parent effort remains Active.

### 2026-11-06 - Narrow native symlink prerequisite handling

Exact-head CI passes at the integrated 245-case head. Copilot's next review
identifies a portability gap in the linked-slot controls: an unprovisioned
Windows account can receive privilege error 1314 before the refusal assertion.
Use the existing test-capability pattern to skip only that actual Windows
error. All other link errors still fail; the three linked cases still create
real links and exercise their original refusal assertions on provisioned hosts.
The canonical capture continues to reject any skipped portfolio item.

All twelve focused controls pass, including the ten slot cases and explicit
1314-versus-other-error regressions, in 6.97 seconds with a successful
88.91-second contained runner. Changed Python passes F/E9, and all 615 admitted
sources still verify. Reproduce the complete capture on this changed fixture
source before updating the PR; a passing prior head is not its qualification.

The first refreshed capture passes the existing owner flow, then the other
real serializer flow reaches its original 90-second item limit in the final
lineage write. It publishes no evidence. Use the same canonical parent-side
record reader already used by the first flow for four assertion-only
snapshots, rather than cold-starting more helper interpreters. The protected
reservation, reachable unqualified resident, every actual capability/read/
parser/writer command, ordering assertion and full CLI session-write path
remain real and unchanged. No item/group/deadline or receipt check is widened.

The first guarded single-case run after that reader change still reaches the
original item limit, with only an async wait in its final stack. The existing
log does not identify the active owner operation. Add capture-only start/end
timestamps around the real asynchronous owner command and synchronous lineage
command, forwarding every argument and result unchanged. This supplies named
phase evidence for the next diagnostic instead of another blind full rerun.

The timed diagnostic passes in 87.15 seconds and proves all actual interpreter
receipts, but leaves little margin under the original 90-second item limit.
Every repeated owner operation takes roughly three to four seconds; no single
stuck operation appears. The read-only head handler forces the full CLI import
only to retrieve controller projection functions already defined by tracking.
Use those canonical functions when the CLI is absent and preserve its
already-loaded overrides. The finite fixture composes that existing head
parser/handler, with the original project binding and reachable resident trap;
all session writes still traverse the full CLI.

All seven focused reader controls pass in 3.39 seconds, including existing
head-command behavior, canonical projections without importing the CLI and
loaded overrides, with a successful 90.84-second contained runner. F/E9 and
all 615 admitted source hashes pass. Guarded real-flow timing and the complete
capture remain required; this is not yet a new qualification result.

The named two-case diagnostic identifies the original acquisition flow reaching
its 90-second limit during `note-handoff`; it terminates before the keyed flow
starts. The finite owner unnecessarily boots and closes a resident server for
every bounded record, head and capability read. Dispatch those same real
read-only parsers and handlers before constructing the server. All session
writes still use the full CLI with the reachable unqualified-resident trap.
Add the same capture-only command timings to acquisition so either flow's
next failure names its phase. Preserve all commands, assertions and deadlines.

The next trace completes the handoff in 88.31 seconds but reaches the same
90-second limit during inspection. A guarded import profile measures repeated
record/head/capability cold starts at 2.33/2.73/3.55 seconds and the full session
CLI at 5.99 seconds. Receipt `fsync` accounts for only 0.06–0.16 seconds, so its
checks and durability remain unchanged. Defer mutation-only imports from the
bounded head/capability frontends and share their existing parser definitions
with focused registration helpers. Preserve full-CLI compatibility and every
actual write, read and negative control in the real-process fixtures.

All 68 focused controls pass in 43.49 seconds, including fresh-interpreter
refusal of unrelated mutation imports, canonical/loaded head projections,
relationship CLI, producer request, full-parser consistency and conclusion CLI.
The contained runner succeeds in 131.81 seconds; F/E9 passes. The earlier broad
334-case selection reached its original 300-second group limit and is not a
completed-suite result. Guarded flows and the complete 245-case capture still
owe fresh source-specific evidence before publication.

The guarded acquisition flow still reaches the 90-second limit during its
handoff, with every completed command returning normally. The operator
explicitly approves increasing only the two actual-owner cases to 120 seconds
after reviewing those measurements. The original 300-second group safety
limit, 600-second Windows aggregate deadline, complete portfolio and all
source/interpreter/receipt checks remain unchanged. This is a declared
per-case budget adjustment, not evidence that the earlier 90-second contract
passed and not authorization for installed or fleet effects.

The full capture at the approved-budget source publishes no outputs: both
actual-owner cases fail at the separate, unchanged production lineage command
timeout of ten seconds. Acquisition reaches `note-handoff`; keyed creation
cannot complete its legacy-resident probe. After the contained runner exits,
an aggregate host sample reports 98% CPU utilization with memory and disk
headroom. Preserve the production limit and wait for execution capacity before
one further bounded attempt. This is a pending qualification gate, not a pass,
an accepted flake, or permission to bypass source evidence, review or CI.

The capacity-admitted full retry passes both actual-owner flows in 171.27
seconds (189.75 including preparation/cleanup), then the 243-case protocol
group reaches its unchanged 300-second limit and publishes no evidence. Stop
the capacity watcher. A named protocol-only diagnostic passes all 243 cases
in 279.43 seconds (302.36 including preparation/cleanup), with observer and
record-writer receipts verifying 728 executed sources. These separate results
do not establish the complete aggregate capture gate.

Twelve real receiving-side refusal controls dominate that protocol trace at
roughly 9–14 seconds each. Keep their real protection operation, complete
receiving CLI and resident-fallback trap; replace only the two assertion
snapshot subprocesses per case with a canonical nonce read and a stronger
unchanged-record-bytes assertion. This removes 24 assertion-only interpreter
starts without deleting a receiving command or weakening refusal coverage.
Full captures now retain named pytest progress and the slowest durations,
while preserving the quoted JUnit path and every original group/aggregate,
portfolio and source/receipt check.

All six contained capture-report controls pass in 0.97 seconds, including
paths with spaces/backslashes, named-output selection, the shared deadline,
and rejection of missing, duplicate or foreign portfolio entries. Changed
Python passes F/E9. The next complete capture must qualify this fixture and
reporting source; the earlier disjoint diagnostic is not substituted.

The changed-module guard catches the reader at 1,005 lines, above its existing
1,000-line cap. Stop the in-flight capture before changing its source. Compact
only the newly factored import and simple parser flag without altering import
order or behavior; all 24 changed modules now satisfy their existing ceilings.
The nine focused reader/parser controls pass in 4.65 seconds (122.25 including
preparation/cleanup), and F/E9 passes. The stopped capture is not qualification;
the final source still requires complete writer and independent reproduction.

### 2026-11-07 - Hosted qualification enabler accepted; exact-source packet pending

The owner-only Windows escape hatch in #11169 is now released and accepted:
its focused 68-test Windows run passed, actual concurrent submissions retained
both source commits through one append-only non-branch ref, and deliberate
failure and empty selection returned non-success. This avoids another local
full capture on the constrained development host.

Submitted this effort's exact retained source
`d79a4c4a81814b0f65601836df4ccfab1b1522f7` to the canonical
`bridge-http52` packet, request `f228a2d79c9740a183ab6d5d5b02e076`,
through protected controller
`ccea6c44019c7e9be7bafa5064e7fe6aad1340a8`.
The original request is accepted, not yet qualified. It owns the canonical
writer, separate same-source reproduction, contract verifier and artifact
export; existing portfolio, receipt and aggregate limits remain unchanged.
No local environment prewarming, shared runtime rollout or production-session
effect was performed.

This effort retains the packet's original request/result custody, validated
artifact materialization, #11016 review/CI/merge and every remaining parent
gate. It also retains eventual retirement of the qualification source
workspace after its source-owned client is no longer needed. Completing the
escape hatch does not complete this refactor.

The original hosted packet completed as a failed result in run
[42334415067](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42334415067),
artifact `13558912228`. Its protocol group passed 241 cases but failed the
two rollback observations; the independent reproduction and export did not
run. It is not qualification or a timeout. The source fixture loader used
the Windows default text encoding. Decoding the captured UTF-8 bytes as
CP1252 exactly reproduces the hosted mismatched hash; UTF-8 reproduces the
unchanged expected hash. Select UTF-8 explicitly for both the manifest and
captured sources, with a focused locale-regression test. Preserve the actual
fixture bytes, expected source hashes, complete portfolio and all deadlines.

### 2026-11-07 - Exact-source hosted capture and artifact return accepted

The focused CP1252-default regression qualified source
`b2b771d829b03b1518c726a210457844de040195` in run
[42335440675](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42335440675).
Then the same source's canonical packet, request
`4c32c68fa58e470ea88fde4dd8b83f52`, qualified in run
[42335682232](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42335682232),
attempt 1, artifact `13559319605`, protected controller
`72eb9adc4cf670dd6e70e5d1e3146be67853f53f`.
The result client validated the complete binding and returned exit 0.
Result-manifest SHA-256:
`3ce62539139e1d7382c9e799e6b04952a7999219e13e0f310185af18b62ae075`.

The canonical writer passed both real-owner controls in 45.81 seconds and all
243 protocol/CLI controls in 110.55 seconds. Independent same-source no-write
reproduction passed the same complete portfolio in 41.04 and 94.56 seconds.
No skips, partial-packet stitching or enlarged item/group/aggregate limits
were needed. Worker phases, including source/selection and preparation, ended
at 447.750 seconds: writer at 232.969, independent reproduction at 432.844,
contract verification at 443.297 and export at 447.641.

This ran on disposable hosted Windows Server 2025 AMD64 image
`win25-vs2026` / `20261103.298.1`, controller Python 3.12.10, not the
development machine. The existing independent registry verifier passed.
The source-owned materializer verified export-manifest SHA-256
`19055c151f46d09809d4fa0bf2e07aab3de385dd7df8ae0d0effb781c1f0dcf1`
against the actual export receipt, source identity, 245-case inventory and
all input preimages, then published all 139 canonical outputs successfully.
The earlier failed request remains retained and is not relabelled as a pass.

Fresh review and required CI on #11016 remain separate gates. Its current
published head now reports a genuine upstream conflict, so preserve this
qualified source and artifact before normal managed reconciliation. Any
changed execution closure needs impact-selected revalidation; this result
does not qualify an unexamined rebase or complete the parent.

### 2026-11-07 - Reconcile the qualified checkpoint with current development

Preserved the qualified source and generated outputs before reducing the owned
17-commit stack to one tree-identical local commit. Rebased that net change
onto `b206e572a1`, resolving the source conflict once: retain upstream's
precomputed suffix lookup and the distinct per-interpreter capture logs.
Update the focused lookup control to exercise that canonical implementation.
Both upstream's caller-association additions and the bounded head reader
remain in the shared CLI module, within its unchanged 1,000-line ceiling.

The canonical registry codec and locked publication owner retain every
provenance record from both histories: 10,874 HTTP records and 91 Session Host
records. The shared CLI source declaration names the genuinely merged bytes;
this is not new capture evidence. Regenerated the canonical lifecycle archive,
all 13 adopter copies, the nested Worktrees copy and the affected footprint
producer hashes from current source. All 652 admitted unit-source hashes,
installation-context synchronization, the Worktrees payload generator,
changed-module ceilings and focused F/E9 checks pass.

The current complete packet contains 270 cases, not the earlier 245. The prior
hosted result remains valid for its original source only. This reconciled
closure still requires fresh hosted controls, canonical writer, independent
same-source reproduction, contract verification and receipt-bound artifact
return before refreshing #11016 for review and required CI.

### 2026-11-07 - Reconciled 270-case hosted packet accepted

Exact source `312c243959b8f1ed6181839336d37b4aab1d3ee2` passes the five
focused lookup/log controls in hosted
[run 42338419548](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42338419548).
The complete packet, request `c0a2f2d751ed445f81437a338ed355fe`, then qualifies
in [run 42338550494](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42338550494),
attempt 1, artifact `13561445297`, protected controller
`de36510694d459d212e0d1854e3f5e975969d634`.
The result client validates the original request, source, selection,
controller, Windows platform and run/attempt binding. Result-manifest SHA-256:
`26a72ac28835a2fd12a21a11715c04abf95e682e67424a651436f14c502d788b`.

The canonical writer passes both real-owner controls in 50.51 seconds and all
268 protocol/CLI controls in 115.82 seconds. Runner-inclusive durations are
88.14 and 126.11 seconds. Independent same-source no-write reproduction passes
the same complete portfolio in 48.85 and 122.42 seconds, with runner-inclusive
durations of 80.36 and 133.64 seconds. No skips, stitched selections, local
prewarming or enlarged limits are used. The existing per-capture aggregate,
group, item, native-parent, observer and execution-closure requirements pass.

The separate contract verifier accepts 2 contracts and 1,054 fixtures.
All worker phases complete at 553.828 seconds: canonical capture at 272.313,
independent reproduction at 533.672, contract verification at 547.969 and
export at 553.688. The disposable hosted image is Windows Server 2025 AMD64,
`win25-vs2026` / `20261103.298.1`, Python 3.12.10.
The source-owned materializer verifies export-manifest SHA-256
`ecafd077bb39c195e64480aeae30120321b162a261f9383c98a0734124ed4780`,
the actual export receipt, exact source and all input preimages before
publishing all 143 outputs. Earlier qualified sources and failed requests
remain retained under their own identities.

Refreshed installation, library synchronization, documentation, headless
launch, daemon census, changefile, effort-structure and changed-Python F/E9
checks pass. This remains source qualification, not productive-session or
fleet deployment. Refresh #11016's evidence, review and exact-head CI next;
the complete parent acceptance matrix and the conditional peer-unblock
notification remain owned by this effort.

### 2026-11-07 - Headed broker source merged; caller and lifetime acceptance continues

#11016 merged at 04:52:42 UTC as
`d62de87ff737e387ef19213cb0b023f5d761ab89`. Copilot review `5810164430`
approved exact head `44ea590495a28627f6892530ba3dc5515ab1fc43`; required
[CI 42339864446](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42339864446)
passed at that head, including freshly executed unit coverage, admitted
typing, source/process guards, retained historical evidence, complete
registry and generated payload checks. All five review threads are resolved.
The managed merge used the normal repository-authorized Maintainer review
exception, not a CI bypass. #10765 remains open.

Re-read the Plan and Validation Plan after merge. The canonical headed
execution and launch-injected broker connection are now delivered; mark those
two implementation items complete. Do not mark the complete caller matrix,
native restoration, all caller cleanup Jobs, pending-aware lifetime or
receiving/skew qualification complete from this source slice.

The owned workspace is pulled forward through the native synchronization
owner. A first attempt safely auto-aborted while replaying the four original
commits individually over their squash merge; a tree-identical local squash
then allowed normal pull-forward without losing changes or rewriting shared
history. The next stretch is the real-caller fresh/resume/reuse and
attached/detached/new-window/no-mux matrix, followed by lifetime and receiving
acceptance. Keep original-owner custody, unknown-effect holds and isolated
fixture cleanup intact. No installed runtime or productive session changed.

### 2026-11-07 - Native Worktrees launch-owner fresh/resume checkpoint

The opt-in native Worktrees Python-owner test passes in 41.97 seconds
(150.11 seconds including managed environment preparation and cleanup).
It invokes the real launch/seed/admission/receipt owners with an existing
native Copilot executable, a loopback-only model provider, isolated state
and a runner-owned private PSMux namespace. A read-only adapter routes
Worktrees discovery to that same real private server.

Fresh launch consumes the literal staged seed once. Explicit native resume
uses the same conversation with a different process incarnation; subsequent
broker prompts preserve exactly one three-message user history. Correlated
receipts identify the native child rather than its coordinator, both original
owners return, and exact fixture processes and panes retire. No privileged
extension permission or host-state opt-in is used.

The first fixture attempted disposable installation and hit its item bound
during setup, before native launch. The final focused test instead exercises
the actual Python launch owner without reinstalling a runtime. This is not
copied-setup, full Manager/embody planning, guarded-head recovery, physical
terminal input or all-caller-Job survival acceptance. The complete caller,
lifetime and receiving gates remain open; no installed rollout occurred.

Review strengthened this checkpoint to compare the receipt birth with both
the broker record and live OS observation, then require a certain terminal
receipt and agreement between native, coordinator and original-owner exit
codes. That native rerun passes in 38.76 seconds (116.14 seconds including
preparation and cleanup), with the same limits. Live-state polling tolerates
only transient Windows open contention; complete malformed records still fail.
Each recovery leg owns a distinct private mux target while retaining the
same native conversation. Immediate reuse of a retiring mux name is not
silently treated as qualified.

### 2026-11-07 - Native Python-owner checkpoint merged; parent continues

#11260 merged at 06:05:03 UTC as
`7ce12dc8d83f7fb746bbb65fe50441a106b86a59`, after Copilot review `5810434897`
approved exact head `9744d817f999cecbc8754afb47e7874ddcee6b1f`, both review
threads were resolved and required
[CI 42344711712](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42344711712)
passed. The managed merge used the normal Maintainer review exception, not
a CI bypass. Re-read the parent Plan and Validation Plan; the partial native
checkpoint does not close their full caller/lifetime/receiving items.

Native pull-forward first auto-aborted the individual replay over the merged
squash. A tree-identical local fixup of the two owned commits then allowed
normal synchronization. Continue the real native input/auth-wait observation
and original-owner same-session controlled-recovery leaf, preserving its
unsupported/unknown states and the remaining caller matrix. No deployment
or receiving-acceptance transfer is inferred from this merge.

### 2026-11-07 - Native input fencing and fixture execution checkpoint

The focused Worktrees Python-owner packet passes both controls in 58.24
seconds (128.02 seconds including managed preparation and cleanup), within
the unchanged 180-second item, 300-second group and 600-second aggregate
limits. Literal native terminal input stays in the original conversation,
fences the old broker with error `-32001`, and never delivers the refused
prompt. An explicit stop through the original owner yields a certain terminal
receipt; native resume restores the same conversation under a new broker.
Exact user/provider counts, live PID/birth agreement, correlated exit codes
and identity-pinned fixture cleanup pass in both controls.

Fixture diagnosis found two pre-native failures: settings scrubbing removed
the runner's registry/lifecycle context, and a Windows venv redirector could
hang before script entry. The fixture now preserves `COPILOT_EXTENSIONS_*`
context and uses the physical base interpreter with the same managed test
packages. Private stderr capture identifies original-owner failures without
printing broker nonces. No production launch, credential or admission policy
was changed.

The unchanged Driver packet passes 20 controls, with the unrelated standalone
observer deselected, in 45.12 seconds (65.11 seconds including preparation
and cleanup). Thirteen unit isolation controls and seven native pane,
SDK/broker and coordinator controls exercise canonical private mux routing,
raw child argv and exact retirement. The native SDK/coordinator fixtures now
launch their actual target directly rather than through a Manager wrapper;
this evidence does not qualify that wrapper.

This test-only refinement is below architectural altitude. Unit and real
consumer/native lanes were exercised; first-install clean-room and external
venue qualification are not applicable to these local Windows fixture
changes. Installer, copied setup, full Manager/embody, auth-wait, physical
keyboard, caller-Job lifetime and receiving/skew acceptance remain separate
open gates. The parent Plan and Validation Plan remain unchanged and Active.

Review of #11275 found a visible-before-complete PID marker and inherited
PSMux config/data overrides in the finite pane control. Publish the PID and
original Worktrees owner-result markers by atomic replacement. Reject caller
overrides, retain a private data directory in both pane controls, and exercise
default-config versus null-config loading separately. The default-data probe
could hang before script entry even with the correct physical Python command
line; do not infer that the venv redirector explained every startup failure.
With explicit fixture-private data, both pane controls pass in 2.11 seconds
(28.25 seconds including preparation/cleanup).

A separate native joined-session observation passes in 9.97 seconds
(36.42 seconds including preparation/cleanup). The executing CLI exposes
`session.rpc.tasks.list()` and `session.rpc.schedule.list()` with the expected
`tasks` and `entries` arrays, in the same native conversation and without
granting extension permissions. Missing/failed reads remain unavailable.
This does not yet prove complete pending agents, initial/reconnect completeness,
active schedules or grace-race cancellation; automatic idle retirement stays
unavailable and the lifetime gate remains open.

The next review found that the foreign socket unit control was relative and
therefore valid inside the POSIX sandbox. Use an absolute outside-sandbox path,
and add a POSIX-branch control that distinguishes an owned relative socket from
that foreign path without spawning a mux. All 14 isolation controls pass in
0.20 seconds (16.95 seconds including preparation/cleanup).

The declared type-check prerequisite exposed old invocation files beside the
shared interpreter cache, then an occupied reusable source-view directory.
Keep each new check's target/configuration and emitted source views in its own
private staging directory under the existing environment lease. Preserve prior
files and stages; retire only verified current outputs and the empty current
directory. This is a validation-tool prerequisite, not a Driver runtime change.
Focused regression coverage retains previous invocation files while checking
the actual target/configuration read, source-custody failures and matrix result
collection. The two checker test files produced 221 passes and two
failure-path cleanup-assertion mismatches. After correcting those assertions
to preserve an empty stage on deliberate source drift, the focused three-case
control passes in 6.15 seconds; the other 221 controls were not repeated.
Current F/E9 lint and all 40 admitted strict projects pass their declared
Linux/Windows analysis targets. The module-size gate passes without widening
any ceiling. No prior cache file or source-view stage was removed.

Copilot review approved head `7066ee88b4dad89c1a0bfb051a3e3fce5131147f`.
Required CI then refused the older unit-admission digests for the changed
checker source and candidate test file, before executing the packet. Refresh
only those two reviewed hashes. Source verification passes with the admitted
nodes, coverage targets and whole-packet budget unchanged; fresh CI and review
remain required at the updated head.

The next review has no open issues but notes a shared startup/shutdown
deadline in the finite pane control. Give shutdown its own ten-second wait
after release, leaving the original item and packet limits unchanged. Both
native pane controls pass in 2.44 seconds (35.03 seconds including preparation
and cleanup). This fixture-only adjustment does not change the qualified
checker sources or require repeating their settled validation.

### 2026-11-07 - Native input checkpoint merged; parent continues

#11275 merged at 08:38:30 UTC as
`b3b2a49c3b15e9423219308f4c0c01c4647342df`. Copilot review `5811088766`
approved final head `87a701fe502e8c08bb1b295bafa31d68470d9409`,
all three review threads were resolved, and required
[CI 42355721035](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42355721035)
passed. The first merge attempt briefly reported the passing check as expected;
live check/commit/application bindings were verified, then one bounded retry
completed through the normal Maintainer review exception. No CI bypass or
protection change was used.

Re-read the parent Plan and Validation Plan. Native input fencing,
original-owner controlled resume, SDK task/schedule read support and the
checker prerequisite repairs are upstream; the complete caller, auth-wait,
caller-Job lifetime and receiving/skew items remain open. No broad item is
closed solely by this partial checkpoint, and the parent stays Active.

Preserved the owned history, collected the five merged commits locally
without changing their tree, and pulled forward through the native owner.
Continue with actual Manager-wrapper native execution and the remaining real
caller matrix, then pending-aware ACP lifetime and receiving acceptance.
SDK read availability is not complete pending-work evidence. No installed
runtime or productive session changed.

### 2026-11-07 - Native Manager pane-wrapper consumer

Parameterize the existing SDK/broker control with direct and actual Manager
PowerShell 7 pane owners, rather than duplicating its fixture. Preserve raw
argv, the private mux namespace/data/home, normal pane Job and exact session
incarnation cleanup. The wrapper PID is distinct from the extension-reported
native CLI PID; do not substitute a wrapper identity into the native handshake.
An initial fixture attempt made that substitution and was correctly refused.

Both corrected controls pass in 17.78 seconds (42.47 seconds including
preparation and cleanup). Each keeps one native conversation through the
literal seed, WebSocket prompt, TCP prompt and original-owner stop, checks
three provider requests without an extension permission grant, and verifies
pane, CLI and extension exit. No installed runtime or shared mux is touched.

This is a below-altitude acceptance extension, with no runtime, service,
installer or capability-contract change. Native real-consumer validation
was exercised; unit behavior is unchanged, and fresh-install clean-room and
external-venue tests do not apply to this isolated Windows fixture. The full
Manager launch-session planner, all caller cleanup Jobs, auth-wait/recovery,
pending-aware lifetime and receiving/skew gates remain open. No parent Plan
or Validation item is closed solely by this narrower result.

#11287 merged at 09:02:13 UTC as
`348c386cbe8c3698ed4c1c06b269f408e775f581`. Copilot review `5811195429`
approved exact head `a2b8909967d0d21b33de982c3be7cbe8692ee4cb` with no
open issues or inline findings, and required
[CI 42357638312](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42357638312)
passed. The managed merge used the normal Maintainer CODEOWNER exception,
not a CI bypass. Preserved the source head and synced through the native owner.
Re-read the parent Plan/Validation gates: continue with the actual Manager
launch-session planner and real caller matrix before pending-aware lifetime
and receiving acceptance. The parent remains Active; no installed rollout
or source-worktree finalization is implied.

### 2026-11-07 - Actual Manager no-mux caller convergence

The actual Manager launcher and Worktrees resolver exposed two gaps hidden by
direct native fixtures: unseeded custom direct plans did not enter the common
core, and Windows split a drive-qualified `-C=...` token at a PowerShell script
boundary. Retain the canonical launch-command wrapper, copy it through the
existing session-shell owner, and use its existing startup manifest to carry
literal argv. The inner admission-only Python owner now invokes the retained
core, keeps the creation fence through the wait and records the real native
PID/birth and actual exit. No new launcher service or transport is introduced.

Ordinary unseeded execution preserves its existing semantics: it cannot claim
a seed or ignore changed intent, but does not require mux absence. The
seeded and special native-head recovery paths retain their existing cold-stop
checks. #11274 intentionally makes negative PSMux presence unknown; do not
reintroduce a false absence to satisfy native tests. The current native
fresh/resume and input-fencing controls therefore use explicit native startup
input with unseeded admission. A real PowerShell cold-seed control verifies
refusal before backend entry and preservation of the original staged seed.

The actual Manager no-mux fresh/resume control passes in 63.09 seconds
(85.28 seconds including preparation and cleanup), using one conversation and
the common broker, with no installed runtime or shared mux mutation. Five
initial focused owner/transport controls passed; the expanded affected
shell/owner/resolver packet produced 297 passes and 15 platform/optional skips,
plus one old Windows cold-seed expectation inconsistent with #11274. That
expectation now checks refusal and non-entry rather than weakening admission.
The combined native/corrected cold-refusal packet produced three passes and
one native-input pane startup failure before its first owner marker, in
93.04 seconds. Move that marker before optional imports and retain native
stderr diagnostics; the failed native-input case alone then passes in
24.40 seconds (83.98 seconds including preparation and cleanup). This does
not claim the intermittent pre-entry startup symptom is solved. The actual
Manager, direct broker and cold-refusal results were not repeated.

Current F/E9 lint, all source module ceilings, headless launch, installation
contract, documentation consistency and effort structure pass. The changed
Worktrees project remains in the declared pending type-admission scope; no
admitted strict project input changed, so the prior strict portfolio was not
repeated or widened. These are isolated native real-consumer and portable
unit contracts, not installation/rollout or desktop visibility proof. A full
installer clean-room lane is not applicable because installation code is
unchanged; fresh copied-shell behavior is covered in the isolated fixtures.
No qualified external native CLI lane or observable desktop is supplied here.

This closes part of the reviewed caller-convergence intent without changing
CLI syntax, service schemas, independent peer support or the vision. The
internal admission callback's cold-stop option is restricted to ordinary
admission-only execution; it cannot be selected for seeded or special
native-head recovery. Existing current-runtime wrapper support is checked
before migration; unsupported forms retain their original path. No installed
rollout, all-caller Job lifetime, complete pending-work evidence or receiving
parity is claimed, and the broader parent items remain open.

Review of #11294 found that early Windows serialization hid a stale resume
argument from Manager's later authoritative AHP-binding rewrite. Defer
serialization until that existing consumer has finished its runtime/binding
mutations, rather than introducing an additional manifest reader/rewriter.
Refresh the inner Python owner as well as exposed runtime arguments, then
use the existing mux or direct-launch serialization boundary. Inline and split
resume forms are replaced consistently on Windows and POSIX.

The Windows producer now requires a per-call declaration that its consumer
owns final serialization. Manager scopes the declaration to resolution and
restores the prior process environment; older Manager callers retain the
legacy producer path, and older Worktrees providers can ignore the declaration
without being asked to accept new CLI syntax. The earlier reader-based
investigation passed two AHP consumer controls, then an expanded packet
reported 98 passes, seven platform skips and four extracted-harness failures.
After correcting their prerequisites, nine selected controls passed. Those
reader-specific cases are not evidence for the simpler final design: the
extra reader and its dedicated tests were removed. Focused final-boundary
contracts and actual native no-mux revalidation remain required before this
correction is published. Binding/token inputs remain synthetic; no
authenticated or live AHP effect is authorized or claimed.

The simpler final-boundary design passes eight focused Manager controls in
17.92 seconds (154.69 seconds including preparation and cleanup). The three
Windows/POSIX producer/capability controls also pass. Native revalidation
directly from the source checkout reached a generic process-spawn receipt
without headed metadata at 30 and 60 seconds; that source-location behavior
is not qualified by this result and is not declared repaired.

The isolated copied-payload diagnostic then completed the full native flow.
After removing its temporary command capture, the byte-identical private
Manager scripts pass fresh/resume through the real resolver and common broker
in 87.58 seconds (177.38 seconds including preparation and cleanup), including
exact native history, PID/birth, owner exit and retirement checks. The Manager
frontend has a sixty-second internal binding wait, within the unchanged
180-second case, 300-second group and 600-second aggregate limits. No further
timeout increase or claim of startup stability is made. The copied-payload
qualification does not stand in for source-checkout, installed, auth-wait,
attached/new-window or all-caller Job qualification. Current scoped source
gates pass; the corrected head still needs fresh review and required CI.

The next review found that hoisting Bash ahead of the admission owner bypassed
the existing effective-`BASH_ENV` guard. A startup primer can exit or exec before
that inner owner runs. The headed and native-setup paths now share that guard
and the existing `env BASH_ENV=` prefix: nonempty or unknown mux environments
retain outer admission, while an explicitly empty plan is pinned before Bash.
Real POSIX exit/exec refusal controls complement the portable composition tests.

The same correction preserves selector-shaped native option values during
AHP rebinding on both platforms, using the existing canonical argument tables.
Only real options count for experimental-mode insertion. Inner runtime refresh
also requires a Python executable, so a native model argument cannot resemble
and replace the owner. Nine focused Worktrees controls pass in 1.30 seconds
(70.19 seconds including preparation and cleanup); twelve Manager controls pass
in 29.95 seconds (184.17 seconds total), with two native-Bash cases skipped on
Windows. The new real Bash primer controls require a POSIX lane; portable
branch checks are not equivalent evidence. The binding path adds one
local interpreter invocation to read the selected runtime's existing argument
tables, without a new CLI/schema or optional peer feature. No settled native
Windows lifecycle test was repeated for these scoped argument/environment
changes. Parent acceptance and the source-location startup discrepancy remain
open; this correction still needs fresh review and required CI.

The next review confirms the Bash guard correction, but finds two further
argument-boundary gaps. Bare `--resume` has an optional selector and must not
consume the next option during AHP rebinding. Both launchers now use lookahead.
The retained Driver validator must also skip ordinary value options before
interpreting selectors or execution modes. Move the existing native option
table into the standalone core, retaining Worktrees' original import as a
re-export, and include that module in the retained artifact. This changes no
CLI/schema or independent peer feature and adds no dependency.

Thirteen focused Driver controls pass in 3.38 seconds (26.84 seconds total),
with one POSIX signal skip. They include actual retained-archive execution and
native exit with a selector-shaped log-directory value. Two Worktrees
copied-wrapper/real-validator controls pass in 1.87 seconds (63.41 seconds total).
Six Manager final-consumer rebinding controls pass in 36.95 seconds (144.36
seconds total), with two native-Bash skips. Native Copilot, the source-location
discrepancy and the remaining parent gates are not requalified by this packet.

The final bare-resume/model-order selection at `52d735872e` passes both cases
on disposable hosted Windows in 37.77 seconds through the owner-only escape
hatch ([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42370030834)).
The result validates the exact source, selection, Windows runtime and actual
test identities; it is not inferred from workflow success. Source transport
retains the one reserved non-branch ref without a new upstream branch.

Required CI then refuses before unit execution because the Worktrees argument
module was an explicitly hash-bound unit input. Its shared table must join that
admitted closure. The existing actor-free local-launch fixture now loads the
exact core table and Worktrees re-export under restored synthetic namespaces,
without importing application actors. Update only those source hashes and the
fixture hash; unit nodes, coverage targets, effect restrictions and deadlines
remain unchanged. The fixed admission still needs the required complete packet.

The normal required unit packet passes at `d264b15e88`, confirming the repaired
actor-free closure without dropping nodes or restrictions. The source guard
still identifies a missing Bridge consumer changefile, also found in review:
the shared core is packaged by Bridge as well as Worktrees and Driver. Add
that consumer's patch declaration rather than bypassing the release guard.

Worktrees' session detector also needs the same split `-C` value boundary that
Driver already honors. A selector-shaped directory must not invent a session
or hide a real one. Four focused detector/copied-wrapper/real-validator
controls pass in 1.63 seconds (65.95 seconds including preparation and cleanup).
These narrow corrections still require exact-head review and required CI.

The next review confirms both findings resolved and identifies the matching
Bash inner-runtime refresh gap. After an independent update, the copied wrapper
must invoke the newly selected Python rather than a path from the resolved
plan. The Bash consumer now refreshes only the typed inner Python owner,
including recovery and the existing empty-`BASH_ENV` prefix; native executable
arguments remain literal. This is the same boundary already covered in the
PowerShell consumer, with no new process or protocol.

Six final Bash grammar controls pass in 2.97 seconds (88.53 seconds including
preparation and cleanup), using actual Git Bash on Windows. They cover normal/
recovery/prefixed owners, native non-rewrite and authoritative AHP argument
preservation. This also executes the two AHP grammar cases previously skipped
on this host. It does not claim stock macOS Bash, native POSIX process lifetime
or a complete Manager launch; those remain separate gates.

The subsequent review has no inline findings but flags option-parsing risks.
Direct assessment identifies two real extension-injection boundaries: appending
flags after an option terminator makes them positional, and an experimental
flag spelled only as an option value must not suppress the real flag.
The core now inserts the scoped plugin and experimental flag at the native
option boundary, skipping canonical value options and preserving positional
arguments. An already-present actual experimental flag is not duplicated.
This closes the existing launch-injection intent without changing a public
contract, dependency, permission or receiving owner.

Eighteen focused controls pass in 15.56 seconds (36.24 seconds including
preparation and cleanup), with one POSIX-only signal skip. Four new cases
execute the real retained archive and observe actual child argv, including
a delimiter-shaped value, a positional experimental literal and an existing
flag. This is finite-process consumer proof, not another native Copilot,
external-venue, installation or complete caller-lifecycle qualification.
Those tiers are unchanged and remain explicit parent gates. Fresh exact-head
review and required CI still gate this correction.

The next review identifies another concrete option-boundary defect in the
persisted AHP rewrite: positional selector-like text after the native `--`
was removed, and authoritative options were appended too late. Both launchers
now use one bundled read-only argv adapter instead of separate filter loops.
It preserves typed copied-wrapper and inner-owner transport boundaries,
canonical option values and the complete native positional tail, inserting
the authoritative binding before the native terminator. It replaces the
existing one-shot option-table lookup, not a new process or service boundary.
The helper is packaged beside the launchers through the existing bin-resource
declaration and uses existing Worktrees imports; no new peer capability is
required.

Twenty focused controls pass in 44.97 seconds (136.89 seconds including
preparation and cleanup), including both real final-consumer grammar paths and
nested transport controls. Four initial Git Bash failures exposed a test-only
MSYS helper-path conversion setting; the corrected fixture keeps native option
arguments literal while allowing that helper path to reach Windows Python.
This remains grammar/finite-consumer evidence, not native POSIX lifecycle or
an authenticated AHP connection.

The next application-layout checkpoint also now has real first-touch evidence.
Canonical construction and installation succeed in a private home with an
empty project registry and pytest-suppressed resident startup. After source
removal, the installed application's native provider rejects its own selected
`.venv` runtime. The failure reproduces in 249.85 seconds (318.52 seconds
including preparation and cleanup), within the original 300-second case bound.
No completion marker, health response or installed code was fabricated.

Normalize the physical runtime slot using the existing application layout
and physical installation-root owner, retaining current selection, completion
and bootstrap checks. The registry/data root is not the physical runtime root.
Legacy/application and negative selection controls retain these protections.
This is incoming-readiness work under
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement);
it changes neither outgoing retirement nor operator configuration/state,
automatic/manual classification, delayed cleanup or independent connectivity.
The complete first-touch rerun remains pending; full Manager/default-setup,
caller lifetime and native authentication/recovery gates stay open.

The corrected real application rerun passes all thirteen selected controls in
235.31 seconds (300.75 seconds including preparation and cleanup): twelve
legacy/application selection controls plus the actual first-touch build,
installation, source removal and native-admission check. No deadline or
selection protection was widened. This closes only the physical-slot
admission discrepancy, not the remaining caller/default-setup or lifetime
matrix. Fresh review and required CI remain mandatory.

At `807d7cabac`, the hosted Windows escape hatch qualifies both selected AHP
positional-tail controls in 53.30 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42375128057)).
The separate one-case application request returns incomplete: the hosted VM
has no configured governed feed, so the test skips rather than claiming
first-touch qualification
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42375193659)).
Both returned manifests validate source, selection, controller, run/attempt
and actual Windows runtime. Local real application proof remains the pass
above. No new transport ref or source branch was created.

The next review identifies obsolete filter-loop assertions in the existing
AHP launcher contract file and a real custom env-prefix gap. Update only those
obsolete implementation pins to the bundled-helper delegation, retaining every
credential, hard-binding and post-exit assertion. The helper now traverses
environment assignments, unset/chdir options and env's own option terminator
before the native boundary. Opaque env split-string forms are explicitly
unsupported rather than reinterpreted.

All three coupled launcher/contract files pass: seventy controls and four
platform skips in 69.72 seconds (173.09 seconds including preparation and
cleanup). This includes both final consumers, original contract guards and
new custom environment-prefix regressions. The native application and retained
core inputs are unchanged, so their prior actual passes remain scoped proof;
the changed AHP helper still needs fresh hosted qualification, review and CI.

At `1fd1d934a0`, all five selected custom-env and final-consumer cases qualify
on hosted Windows, without skips, in 45.84 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42376106390)).
The source/selection/controller/run/attempt/Windows manifest is validated.
No new transport ref or source branch is needed for the published candidate.

The fresh review then finds that valid attached env short options were refused.
Use the standard option parser for the same supported env grammar, rather than
maintaining a partial hand-written spelling parser. Attached unset/chdir values,
combined short flags and split/long spellings retain the exact prefix. Add
regressions for the three affected forms and explicit no-command refusal for
an incomplete unset and an opaque split-string invocation.

Thirty focused controls pass without skips in 47.86 seconds (142.33 seconds
including preparation and cleanup), including both final consumers and the
existing credential/post-exit contract guards. Native application and retained
core inputs are unchanged. The new helper still requires fresh exact-source
hosted evidence, review and required CI; no review gate is bypassed.

### 2026-11-07 - Direct-launch integration merged; continue caller convergence

#11294 merged at 13:38:52 UTC as
`b830d4ab3d4d33e0794d0ce2aaa6f4ac625e2832`. Genuine Copilot review
`5812478895` approves the exact `10b5ce6d81` head with no active issues; all
four inline threads are resolved. Final same-head CI
[42377533233](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42377533233)
passes, including required gate `128418959411`. The managed merge uses the
repository-authorized Maintainer review exception, not a CI bypass.

The final attached/combined-env and consumer selection also qualifies all five
cases on hosted Windows, without skips, in 49.16 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42377184559)).
The exact-source, selection, protected-controller, run/attempt and Windows
manifest is validated. The earlier real local Worktrees application proof
remains distinct from the explicitly unavailable hosted governed-feed lane.
No new transport ref or source branch was created.

Re-read the parent Plan and Validation Plan. No broad caller/lifetime item is
closed by this slice. Preserve the owned pre-merge history, collect only its
eleven commits into a tree-identical local commit, and pull forward through
the native owner; the source now equals the merged default. Incoming changes
include canonical application/source-recovery and readiness owners, which the
next fixture must compose rather than reimplement.

Continue with real Manager wheel-application construction and its Worktrees
launcher/default-setup consumer. The source-location startup discrepancy,
complete fresh/resume/reuse and attached/new-window matrix, all caller cleanup
Jobs, native auth-wait/recovery, pending-aware ACP lifetime and ordinary/hoisted
receiving gates remain open. No installed runtime or productive session is
changed, and the parent remains Active.

### 2026-11-07 - Real Manager application and launcher-discovery acceptance

Compose the current canonical source materializer, standalone closure builder,
wheel profile and slot-local application installer in a disposable private home.
The first real construction installs thirteen packages, then refuses Manager's
own running application as belonging to another installation. The existing
engine deliberately uses an extended native Windows Python path; lexical
root/package comparisons do not establish physical identity. This failure
reproduces in 122.82 seconds (186.94 seconds including preparation and cleanup),
before resident activation or Worktrees launcher discovery.

Self-install now uses the existing installation-context physical equality and
containment helpers. Bootstrap activation uses the same root comparison.
Twenty focused self-install controls pass in 4.90 seconds (115.45 seconds total),
covering root/prefix/package aliases, foreign roots/packages and the existing
qualification/activation failures. Twenty activation outcome, base-interpreter,
alias/foreign-root and explicit-start controls pass in 1.70 seconds (96.28
seconds total). No root, qualification or startup guard is removed.

The real retry successfully installs and activates Manager, then reaches
Worktrees' incorrect flat-interpreter discovery branch. Its warning also exposes
the fixture probe's default Windows encoding. That run fails in 175.72 seconds
(253.17 seconds total); the fixture now uses explicit UTF-8 for those reads.
The framework settles the contained run and removes its private registry.
Strengthen the fixture's cleanup-receipt assertion to run on body failures too,
rather than infer successful cleanup from the inner assertion result.

Worktrees now selects only the current application's nested interpreter and
runs one read-only resource probe through the existing retention bootstrap.
The probe executes Manager's actual CLI version check, then its existing
packaged-launcher locator, returning only a slot-relative directory. Legacy
flat retained-payload and source paths remain unchanged; missing bootstrap or
interpreter, malformed/foreign/missing assets and recovery-generation fallback
are refused. This adds neither an installed-peer command nor another process.
Ten focused consumer controls pass in 0.98 seconds (69.36 seconds total).

The complete real first-touch flow then passes in 168.25 seconds (191.00 seconds
including preparation and cleanup): canonical build and installation, actual
resident activation, source-input removal, packaged assets, real retained
Worktrees discovery, unchanged completion evidence and nonempty, settled owned
daemon cleanup receipts. Home, project state and mux data are private; the
resident uses the native renewable test lifecycle under the original 300-second
case deadline. No shared service, productive session or installed runtime changes.

This closes an incoming-readiness prerequisite under
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement),
not outgoing retirement or caller-lifetime acceptance. No incumbent keeper is
changed, automatic/manual classification and operator state remain intact,
independent connectivity is preserved and cleanup remains with its existing
owner. Documentation and both consumer patch declarations accompany this slice;
fresh source gates, review and CI still gate publication. The actual
Manager/default-setup Copilot flow, broader caller matrix and parent lifetime/
receiving gates remain open.

The first review of #11334 identifies a Windows CPython 3.13 defect in the
resource probe: entering Manager through its process wrapper can hard-exit
before the launcher JSON is emitted. Call the actual callable
`main(["--version"])` and honor its result, then apply Manager's existing
finalization workaround only around the completed probe. This preserves the
shutdown workaround without letting the nested version check terminate work.

All fifteen shutdown/probe controls pass in 2.68 seconds (108.86 seconds
including preparation and cleanup). Two bounded-child regressions arm the real
hard-exit branch: successful version handling must emit the resource report
before exit, and a failed version result must preserve its exit code without
emitting a report. This is branch/ordering proof, not a native Python 3.13
installation. The source-removal/activation/cleanup fixture remains retained
at the prior source: construction, ownership and discovery are unchanged;
this scoped correction changes only the probe's callable/exit sequencing.
Fresh exact-head hosted controls, review and CI remain required.

### 2026-11-07 - Application-consumer slice merged; continue native setup

#11334 merged at 14:50:21 UTC as
`5d51060e7b74e2d58387ba88f77cd21642577e22`. Copilot review `5812896209`
approves the exact `ac4ec44a8b` head, explicitly resolving the process-exit
finding with no active issues. The inline thread is resolved. Final
[CI 42383031729](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42383031729)
passes, including required gate `128435532358`. The managed merge uses the
repository-authorized Maintainer review exception, not a CI bypass.

At the initial `a1845974a5` source, hosted Windows qualifies all forty Manager
controls in 45.11 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42381467017))
and all ten Worktrees controls in 27.80 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42381522609)),
without skips. At the corrected `ac4ec44a8b` source, both new exit-order controls
qualify in 41.02 seconds
([run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42382707736)).
All returned source/selection/controller/run/attempt/Windows manifests are
validated. The affected shutdown branch is armed on Python 3.12.10; this is
not a native Python 3.13 installation claim. No new transport refs or
qualification branches are created.

Re-read the parent Plan and Validation Plan before continuing. Preserve both
owned commits on a local backup, collect them without changing the source tree,
and pull forward through the native owner. Incoming retained-bootstrap and
constructor changes belong to their canonical owners and will be composed in
the next real construction, not replaced with a surrogate.

The next checkpoint is the actual packaged Worktrees/default-setup path and
native Copilot action, followed by the complete Manager caller flow. Resource
discovery and first-touch readiness do not close the fresh/resume/reuse,
attached/new-window, all-caller-Job, auth-wait, pending-aware ACP or receiving
matrix. The parent stays Active; no installed runtime or productive session
is changed.

### 2026-11-07 - Packaged Worktrees setup and finite native provider

The actual launch builder and normalized/copy owners still selected flat
runtime scripts. Four application controls fail while their four legacy
counterparts pass. Route those existing consumers through the executing
package's assets, preserving the legacy interpretation and refusing a
damaged application's fallback to flat assets. Both finite shell selectors
also require the application's nested interpreter: four new application and
incomplete-application controls fail before the correction, while both legacy
controls pass. An application declaration never permits flat-interpreter
fallback when its nested environment is missing.

The focused packet passes all forty controls in 10.70 seconds, including
literal option transport, setup-owned copies, incomplete application refusals
and legacy behavior. PowerShell is native Windows; the Bash controls use Git
Bash, not native POSIX lifecycle evidence. Python F/E9 lint, install contract
and headless-launch checks pass.

The first real application attempt reaches its existing 300-second group
limit before reporting the consumer. A diagnostic with a shorter 270-second
case limit identifies materialization at 7.61 seconds, wheel construction at
145.42, profile preparation at 240.11 and installation at 265.09; its terminal
stack is in the additional installed-CLI subprocess. Restore the original
300-second case limit and combine admission and selection in one interpreter,
not a wider budget. The resulting first-touch case passes in 252.35 seconds
(278.62 including runner preparation and cleanup): real build, profile,
installation, source-input removal, installed launch-builder selection,
packaged finite native provider and unchanged completion.

The additional real offline CLI case reuses the existing literal fresh/resume
history control through the application's actual builder, wrapper and default
setup, under the same case budget. It passes in 277.56 seconds (349.58
including runner preparation and cleanup): one native conversation, exactly
two literal prompts, one resume and the preserved prior-turn history, after
source-input removal. Setup recovery suppresses host reconciliation; this
does not establish Manager, headed admission, hook behavior or all-caller-Job
survival. The 300-second case/group and 600-second aggregate limits are
unchanged. The parent stays Active with its broader Plan and Validation Plan
open. No installed host runtime, productive conversation or shared mux is
modified.

Both original direct/source-setup native history cases still pass in 15.07
seconds after sharing their helper. The admitted type-checking gate passes
all forty strict projects for its declared Linux and Windows targets; pending
projects remain pending. Source gates are not native POSIX qualification.
The hosted application lane still lacks its required governed feed and is
not substituted for this local first-touch/native result. Fresh hosted
focused controls, final-head review and required CI remain publication gates.

### 2026-11-07 - Preserve file-level qualification and source admission

#11339 publishes the slice after normal rebase. Its initial CI identifies two
source omissions: the actor-free launch-boundary fixture still substitutes
the old installer seam, and its manifest retains the prior source bytes;
the strict runtime-resolution scanner also needs the precise immutable-slot
annotation for the marker-selected application environment. Update only the
fixture seam and two source pins, not unit nodes, coverage targets or budgets.
All fifty-three original boundary controls pass in 17.19 seconds, every
admitted source pin verifies, and the strict runtime-resolution guard passes.

Copilot review identifies a genuine duplicate-construction issue: the two
optional application cases fit separately but exceed a 300-second group
together. Collapse them into one original test. It always constructs once;
an explicitly selected native executable adds the offline history assertions
to that same construction. A whole-file run with native checks enabled
passes in 287.17 seconds (364.00 including runner preparation and cleanup),
with no selector, skip or wider limit. This also exercises the current rebased
application inputs. It does not expand the qualified consumer boundary.

The initial published head's forty focused hosted Windows controls qualify
without skips in 52.33 seconds
([run 42387795348](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42387795348)).
The bound source/selection/controller/run/attempt manifest validates on Windows
2025 and Python 3.12.10, with no staging branch or new transport ref. The
annotation, actor-free fixture/pins and single-construction test correction
do not change those forty controls' runtime logic. Retain their actual source
attribution; final updated-head CI and a new verdict remain required.

### 2026-11-07 - Consume packaged deferred arguments in Bridge

The updated-head review finds another real consumer: Bridge recognizes only
flat Worktrees scripts, so deferred packaged Windows launches append ACP
options outside the native envelope. Four producer-to-consumer application
controls reproduce the failure. Recognize package-owned scripts beneath the
admitted installation's application slots, without following an asset path
outside that root or recognizing an undeclared package. Keep copied launch
assets bound to their exact record and installation root, not the package's
asset directory.

All eighty-two Bridge argument-boundary controls pass in 52.56 seconds
(114.44 including preparation and cleanup), including the actual Windows
Worktrees builder's legacy/application, wrapped/deferred/prepared and
fresh/resume combinations, custom-path negative controls, and both native
PowerShell setup consumers. This is bounded argument composition and native
setup evidence, not Manager broker admission or complete caller lifetime.
The existing setup envelope and CLI remain unchanged; the compatibility
repair adds no protocol, dependency or deployment. The parent stays Active.

The next review identifies two additional supported spellings: legacy
resource-bearing wheels in a flat version environment, and physical package
paths beneath an aliased installation root. Sixteen wrapped/deferred controls
fail before the correction; the eight prepared-copy controls already pass.
Normalize the installation and candidate paths to their physical spelling,
constrain the result to that installation, and distinguish flat undeclared
wheel slots from declared nested applications. A declared application cannot
admit leftover flat wheel assets, and an asset alias cannot escape the root.

All 108 boundary controls pass in 49.42 seconds (105.08 including preparation
and cleanup), including the real Windows junction and legacy-wheel producers.
The preceding seventeen packaged-path controls also qualified at their exact
published head on hosted Windows with no skips in 40.77 seconds
([run 42390665519](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42390665519)).
That source attribution remains explicit; the expanded cases need their own
updated-head evidence. Required CI also finds the HTTP registry's stale source
pin. Refresh the existing non-semantic local-invocation source record through
the canonical lossless metadata owner; HTTP fixtures, generations and archived
provenance remain unchanged. No readiness, wire or lifetime claim is expanded.

The expanded forty-three compatibility controls qualify at head `5a80bf2bc1`
on hosted Windows with no skips in 29.03 seconds
([run 42392107109](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42392107109)).
Required CI is green at that head, but the next review identifies the Python
POSIX discovery function's remaining flat-interpreter assumption. Its shell
counterpart already selects the nested application environment.

Three focused controls reproduce nested-only rejection, leftover-flat selection
and incomplete-application fallback. Make the real discovery function select
the nested environment when the existing application declaration is present,
with no fallback to a flat interpreter. All twenty-two discovery controls pass
in 1.26 seconds (66.06 including preparation and cleanup), retaining legacy,
capability, selector-race and child-custody behavior. These exercise the real
discovery function with a stubbed child boundary on Windows; they are not
native POSIX lifecycle qualification. No available native POSIX lane is claimed,
and that broader parent validation remains open. No discovery protocol, child
count, deadline, dependency or runtime selection owner changes.

### 2026-11-07 - Packaged launch consumers merged; continue Manager flow

#11339 merged at 16:58:26 UTC as
`7371e4246cd84d01f2c8420810e3d1cd85768b69`. Copilot approved exact head
`910027b7377ae9a141381cbd716763276f1e7f5f` with no active findings; all three
review threads are resolved. Both final-head CI runs, `42393042928` and
`42393101191`, pass their guards, units and required gate. The managed merge
used the repository-authorized Maintainer review exception, not a CI bypass.

The three new discovery controls also qualify at that exact head on hosted
Windows without skips in 26.99 seconds
([run 42393195316](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42393195316)).
That remains discovery-function evidence with a stubbed child boundary, not
native POSIX lifecycle acceptance. The earlier real first-touch/native history,
108 Bridge controls and source-bound hosted packets retain their recorded
consumer and source limits. No staging branch or installed rollout is involved.

Re-read the parent Plan and Validation Plan after merge. No broad caller,
lifetime or receiving checkbox closes from this prerequisite slice. Preserve
the merged history and advance this same source workspace onto the merged
default branch; do not finalize it while the parent remains Active. The next
checkpoint is Manager's actual mux-backed launch and reuse through the common
broker, with one native conversation and exact fixture cleanup. Its existing
no-mux control and qualified application-asset construction are prerequisites,
not substitutes for that caller flow.

### 2026-11-07 - Qualify the real Manager mux-backed caller

Extend the existing native Worktrees packet with the unmodified Manager
launcher's mux-backed path. A private adapter forwards actual mux commands
only to the fixture's UUID-named Manager session. Fresh and resumed callers
return while their real broker/native child remains alive. A second caller
reuses that same Manager incarnation and native process births without another
mux creation or startup-input replay. Backend execution receipts, not the
earlier caller return, prove native exit and exact fixture retirement.

Initial runs expose intermittent pre-entry stalls in the outer fixture pane,
including a no-op control without Copilot or Manager. Minimal direct and
PowerShell controls also pass, so no general backend root cause is established.
Remove the unnecessary outer PowerShell wrapper and use a newly named private
outer pane for each caller; the Manager target remains unchanged for actual
reuse. Temporary diagnostic controls are removed, and the original item,
subsuite, aggregate and cleanup limits remain unchanged.

The final native source qualifies in two disjoint captures: Manager mux-backed
fresh/resume/reuse passes in 129.52 seconds (190.86 including preparation and
cleanup); the three existing broker, native-input and Manager no-mux controls
pass in 117.29 seconds (147.42 total). This is not a single uninterrupted
four-case capture. The native CLI uses an isolated offline provider, not a
productive conversation or credentials.

Documentation impact: update the existing packet recipe and its explicit
consumer limits in `TESTING.md`. No product behavior, CLI/wire contract,
dependency, admission policy, runtime installation or deployed session changes.
The parent remains Active: constructed Manager application/metadata integration,
externally attached/new-window paths, guarded-head/auth-wait, complete caller
cleanup-Job survival and receiving acceptance are not closed by this control.

### 2026-11-07 - Manager mux caller checkpoint merged; continue composition

#11356 merged at 18:47:11 UTC as
`d28f1c1c05b73aafaf166f35a74593656938fb55`. Copilot approved exact head
`a25989ef6e0ed7050dfa02db637267a3374a5dff` with no active findings or review
threads. Final-head CI `42402452363` passes guards, eligible units with coverage
and the required PR gate; lockdown, attribution and identifier checks also pass.
The managed merge uses the repository-authorized Maintainer review exception,
not a CI bypass. Publication's rebase does not change the qualified native
fixture, common core, Manager launch assets or selected Worktrees launch inputs.

Re-read the parent Plan and Validation Plan immediately after merge. Keep its
broad caller, lifetime and receiving gates open; retain this source workspace
and continue. The next consumer checkpoint composes the already-qualified
first-touch Manager application with actual launch/broker behavior, instead of
treating launcher discovery or a copied script as installed-runtime acceptance.
The separate all-caller-cleanup-Job and native pending-work gates remain explicit.

### 2026-11-07 - Compose the constructed Manager application with the broker

The existing first-touch application fixture now accepts explicit native
qualification through its actual installed Manager launcher after disposable
source inputs are removed. Reuse the native caller packet's assertions through
a small helper with optional Manager script/root/lifecycle inputs; its default
copied-script behavior is unchanged. The constructed application drives the
no-mux fresh/resume flow, with one offline native conversation, common broker,
process births, truthful backend exit receipts and exact retirement. Its
completion marker remains unchanged, and the original renewable resident
lifecycle settles every cleanup receipt.

The constructed application/native case passes alone in 231.35 seconds
(264.02 including preparation and cleanup), within its existing 300-second
case deadline. The copied Manager no-mux regression passes separately in
64.19 seconds (86.59 total). An initial combined capture exhausted its unchanged
300-second group limit after one case completed; it does not qualify the new
flow. Separate captures give each already-bounded case its own original budget,
without increasing item, group, aggregate or cleanup limits.

This closes a source-independent Manager application/no-mux composition
checkpoint, not the whole caller or lifetime portfolio. Worktrees still runs
from the managed test environment. Constructed Manager mux metadata, external
attachment/new-window, guarded-head/auth-wait, all caller cleanup Jobs, receiving
and native pending-work acceptance remain open under #10765. Documentation
impact is the existing `TESTING.md` recipe and these explicit evidence limits;
no production behavior, dependency, wire contract or installation policy changes.

### 2026-11-07 - Constructed Manager no-mux composition merged

#11360 merged at 19:11:33 UTC as
`68656f29d9ae126b37d8dfad5226d38557c4ae0a`. Copilot approves exact head
`c02fe24e71a1b969f3f423f41b0878e0545cd26a` with no active findings, inline
comments or review threads. Final-head CI `42404552097` passes guards, units
with coverage and the required PR gate; lockdown, attribution and identifier
checks also pass. The managed merge uses the ordinary authorized Maintainer
review exception, not a CI bypass. Its rebase adds only an unrelated terminal
refresh fixture correction; the constructed application/native inputs are
unchanged from qualification.

Re-read the parent Plan and Validation Plan after merge and keep its broad
gates open. Preserve the source workspace and continue with the constructed
Manager mux-backed/metadata consumer. Its private adapter must carry its own
routing when a resident or callback outlives the original caller; inherited
caller environment alone is not that receiving contract. Caller cleanup-Job,
external attachment and native pending-work gates remain separate.

### 2026-11-07 - Constructed mux metadata exposes nested exit shadowing

Make the private native adapter carry its exact Manager target and command-log
path, with a fixture-owned namespace file instead of inherited caller routing.
The existing copied Manager mux fresh/resume/reuse control passes in 129.88
seconds (200.28 including preparation and cleanup).

Extend the constructed application with a separately selected mux case. Read
its actual installed registry through its own interpreter; require exact live
pane/incarnation attribution and an exit tombstone matching the native execution
receipt. The first run finds a real defect: the native backend reports exit 1
but the pane tombstone records 127. A small real-process differential reproduces
the class without an application build: direct wrapper entry preserves exit 7,
whereas nested script entry records 127.

PowerShell updates the global native-exit automatic variable; the wrapper's
local reset shadows it when the real pane reader calls the wrapper in another
script scope. Reset and read the global variable explicitly. Eight focused
native controls pass, including native-command and script exits through the
real pane-launch reader. Bash already captures its child's exit status and
requires no matching behavior change.

The constructed rerun passes its fresh-leg metadata/exit comparison, then
fails the resumed pane's existing 30-second startup receipt deadline. Its
cleanup independently retains a reused-process identity rather than signaling
it. Neither failure is a passing qualification or permission to widen limits,
force cleanup, or claim complete caller acceptance. Preserve the parent gates
while completing the serial constructed flow.

A subsequent serial capture reaches the fresh broker, live mapping and prompt
but lacks the original caller's process-spawned receipt within its existing
window. A bounded diagnostic capture instead publishes that receipt but does
not bind the common broker before startup expiry. These are distinct failed
observations, not a proven common cause or successful full application flow.
Remove the temporary core diagnostic; retain bounded failure output and make
the fixture select its actual attempt directory before reporting early states.
The nested exit fix and its focused controls remain valid; constructed
fresh/resume qualification and all broader parent gates remain open.

The final serial constructed mux capture passes fresh and resume in 257.33
seconds (325.98 including preparation and cleanup), within the unchanged
300-second case budget with settled lifecycle cleanup. It verifies the common
broker, one native history, exact process births, installed live mappings and
matching exit tombstones after disposable Manager source inputs are removed.
Keep bounded failure diagnostics, including one pre-broker process snapshot,
for the distinct intermittent startup failures observed before that capture;
the exit-code fix does not claim to resolve those failures.

Validation tiers: the eight focused real-reader exit controls cover the changed
PowerShell behavior; the constructed application is the simple consumer and
clean-first-touch lane. The copied mux control separately covers warm reuse.
These are isolated native Windows flows, not a productive desktop, installed
Worktrees adoption, external receiving or survival after all caller cleanup
Jobs. Those remaining lanes retain their existing parent validation items.

Final source lint and the 13-plugin install-contract gate pass. The adjacent
pane argument/startup controls pass 82 cases with three platform skips in
183.62 seconds (197.59 including preparation and cleanup). An initial capture
used an unnecessarily short 120-second group budget and was terminated after
57 passes; the successful capture uses the normal 300-second group budget,
without changing any per-test or native application deadline.

### 2026-11-07 - Constructed mux mapping and nested exit checkpoint merged

#11367 merged at 20:41:02 UTC as
`9ea3b8958e990950626cb5c6638f81b6d48fab67`. The final published head received
a genuine Copilot approval with no findings, and required CI passed. The
managed publication rebase preserved the qualified pane-reader and live
mapping paths; the incoming observation change concerns closed historical
routes, not those consumer paths.

Re-read the parent Plan and Validation Plan after merge; #10765 remains open.
This closes the constructed Manager mux mapping/exit checkpoint, not the
complete caller or lifetime gates. Keep the source workspace active. Continue
caller convergence through the actual installed Worktrees application rather
than treating the managed test resolver as installed-client evidence. Then
retain the distinct attachment, cleanup-Job, receiving/skew and native
pending-work obligations. No shared runtime rollout occurred.

### 2026-11-07 - Installed Worktrees execution-owner qualification

Extend the existing first-touch application fixture with an independently
selected native broker case. Build/install through the same wheel/profile
owners, remove disposable source inputs, and invoke the execution owner from
the installed interpreter/root. Do not copy launch assets or inject the test
runner's site-packages on that path. Preserve the existing admission control
and 300-second construction/consumer budget.

The initial capture hits the group wall without retained phase output. A
same-budget diagnostic with pytest tee output builds by 153.00 seconds,
prepares the profile by 246.03 and installs by 274.59, then fails native
admission because the sanitized application environment dropped the runner's
daemon lifecycle declaration. It does not qualify the installed broker.
Supply an explicit renewable fixture lifecycle and require settled exact
cleanup, as the adjacent constructed Manager control does; this is test
custody, not a production fallback or relaxed startup budget. Construction
cost and complete fresh/resume qualification remain distinct unresolved
observations until the corrected consumer finishes.

Further same-budget captures hit the construction/install wall. A traceback
sample finds interpreter discovery active at 60 seconds, but an isolated
comparison completes that lookup in 0.42 seconds with the runner's bytecode
prefix and 0.08 without; this does not establish a cache or interpreter bug.
Remove that temporary diagnostic rather than changing the provider.

Timing the unmodified build operations measures a 4.75-second toolchain,
48.27-second Worktrees wheel, 18 total real wheels and completed construction
at 155.58 seconds; profile preparation reaches 254.61 and install 280.78.
The remaining pre-build interval includes hashing copied non-shipped tests.
Omit test trees from the disposable plugin/dependency inputs, not runtime
code, scripts, metadata or notices. This reduces fixture work without caching
an installed application, bypassing construction owners or changing the
300-second acceptance budget. The corrected native consumer still needs its
complete passing capture.

One explicitly diagnostic 420-second capture completes the installed consumer
in 347.07 seconds (444.69 including runner preparation/cleanup), with settled
native lifecycle cleanup. Fresh binds in 13.34 seconds and retires by 15.58;
resume binds in 7.88 and retires by 10.72, preserving the original native
identity/history, literal seed, broker prompts, process births and exact exit
receipts. Installation itself takes 306.47 seconds in that capture. This
separates working installed execution from the unmet original 300-second
construction/consumer budget; it is not qualification at that original bound.

Restore the 300-second marker/deadline and remove temporary per-wheel timing
wrappers. Further narrow fixture inputs to the application's Python source,
operational scripts, top-level build metadata and dependency source closure,
not unrelated plugin surfaces. Keep actual wheel construction, fresh install,
source-input removal, packaged imports, both native legs and exact cleanup.
This tests the application, not plugin activation, without changing production
hashing, installer behavior, native startup limits or the parent acceptance gate.

The narrowed-input capture installs at 245.62 seconds and completes fresh in
16.25 seconds, then reaches the combined group wall during resume. An isolated
two-worker deletion change measures source removal at only 1.83 seconds:
construction/install reaches 245.36, removal 247.19 and packaged admission
253.77. Fresh completes in 18.03; the same combined wall still interrupts
resume. Source disposal is not established as the remaining bottleneck.

Correct the newly introduced test's budget composition rather than repeatedly
rerunning or raising its number. The pre-existing admission case remains
300 seconds. Require application construction/install, source removal and
packaged admission within that same 300-second phase; run the unchanged
complete native consumer in a separately caller-owned child with its existing
180-second ceiling. Bound the composed case at 480 seconds and retain the
600-second plugin aggregate, native startup limits and exact lifecycle cleanup.
This is an explicit test-design correction based on measured phases, not a
combined 300-second qualification or a relaxed production requirement. Keep
functional and readiness/performance evidence separate in the parent.

The composed capture passes in 250.29 seconds (324.80 including runner
preparation/cleanup). Packaged admission finishes at 213.89 seconds, inside
its 300-second construction boundary. The separately contained consumer
completes fresh in 18.89 seconds and resume in 12.44, inside its unchanged
180-second ceiling, with all admitted lifecycle instances settled.
This proves the actual installed execution owner and embedded core, not the
public Worktrees command, Manager application pairing, desktop attachment or
survival after all caller cleanup Jobs. Those parent items remain open.

Remove the experimental parallel source deletion: its measured cost did not
establish it as the bottleneck, and the simpler exact serial removal retains
the source-independence assertion. Keep phase/leg timing and run the original
admission control against that final fixture. No production code, runtime
deadline, service installation or shared settings changed.

The original admission control passes against the final serial-removal
fixture in 250.34 seconds (322.83 including runner preparation/cleanup);
source removal takes 1.51 seconds and packaged admission finishes at 250.30.
This covers the last cleanup-only fixture simplification without repeating
the unchanged native consumer. Focused Python lint and the 13-plugin
install-contract gate pass. Both application cases remain explicitly selected,
outside required unit-only CI. The native installed flow is the real consumer
and clean first-touch tier; no productive desktop or external venue is used,
and those broader adoption/lifetime obligations remain in the parent plan.

Review identifies a release-layout omission in the fixture copy: an already
materialized checkout has local `libs` references that the escaping-reference
materializer cannot recreate. Preserve that existing directory in the same
copy helper. A focused regression constructs that layout, copies it, invokes
the real materializer/resolver and reads the resolved module bytes; it passes
in 0.72 seconds (77.19 including runner preparation/cleanup). The development
checkout has no pre-existing local `libs`, so this addition leaves its already
qualified construction/native path unchanged. Refresh final-head review and
CI rather than treating the earlier High finding as advisory.

### 2026-11-08 - Installed execution-owner checkpoint merged

#11426 merged at 00:39:45 UTC as
`f7185af6aaa353d1e71f34124a1d549ed4d011d7`. Copilot approved the final
`06a9e83bc8a0f34fac58f047738dd9556c691bb8` head with no active findings;
the materialized-layout thread is resolved and the latest required CI run
passed. Publication leaves the qualified launch path unchanged; the incoming
Worktrees main-module change only adds unrelated command help text.

Re-read the parent Plan and Validation Plan after merge; #10765 remains open.
Keep this workspace active. The installed execution-owner flow is qualified,
not the public Worktrees command or complete Manager/Worktrees caller matrix.
Continue through the installed public caller next, then the separate
attachment, all-caller-Job, receiving/skew and native pending-work gates.
No shared runtime rollout or production-session mutation occurred.

### 2026-11-08 - Public Worktrees caller prerequisites and measured failures

The public detached caller must execute the installed console entrypoint,
project configuration, record admission and native session selection, not
call the execution owner directly. The first Worktrees-only fixture completed
its application admission in 291.20 seconds, then failed before process
creation because its test-generated console path exceeded Windows MAX_PATH.
A shorter test directory and explicit path preflight correct that fixture
input; they do not claim general long-path support.

The next attempt exhausted the original construction deadline during install
after profile preparation reached 270.28 seconds. One explicitly diagnostic
construction allowance reached admission in 397.62 seconds, with the main
Worktrees wheel taking 107.17 seconds and profile snapshot writing 64.80
seconds. That attempt returned public creation but observed no headed broker.
It is not a 300-second admission or public-launch pass. Temporary timing
wrappers and the diagnostic allowance have been removed.

The source trace identifies a missing fixture prerequisite: normalized native
Windows setup selects the current Manager pane wrapper, while the
Worktrees-only fixture provides none. Correct the consumer fixture with both
real installed applications and selected-runtime launcher discovery; do not
substitute copied source wrappers or infer a production regression from the
incomplete pair. The initial composed public case retained separate 300-second
construction/admission limits for each application and a caller-owned
180-second native flow. Existing single-application cases remain unchanged.

The first corrected pair attempt exhausted Worktrees snapshot inspection
before Manager or the public native caller started: wheels completed at
233.45 seconds and the original 300-second preparation limit expired during
profile retention. The public-caller test now separates functional consumer
acceptance from the existing admission-performance case, with a measured
420-second Worktrees preparation allowance, the Manager preparation's
300-second allowance and the native consumer's unchanged 180 seconds.
The composed functional case is 900 seconds; it does not claim the separate
300-second Worktrees construction-performance gate. The original admission
and installed execution-owner budgets and behavior remain unchanged.

The functional-pair attempt also expired during Worktrees preparation:
inputs took 25.66 seconds and wheels completed at 375.86 seconds, leaving
too little of the 420-second preparation allowance for snapshot freezing.
Manager and the native public caller did not execute. Stop repeating local
construction or enlarging allowances; the measured preparation cost is
increasing and no common source-regression cause has been established.
The existing hosted controller admits generic focused selections and the
`bridge-http52` packet, not this native public caller's prerequisites or
explicit runner bounds. Resolve a supported hosted path rather than treating
skipped tests, arbitrary environment injection or unit-only evidence as native
qualification. No public detached-command pass is claimed.

A registry PATH drift detected during the diagnostic was confirmed by the
operator as an authorized concurrent installation/update. No rollback or
shared machine-state repair was attempted. The corrected installed-pair
fresh/resume/reuse flow is now under qualification. Public attachment,
all-caller-Job persistence, receiving and pending-work remain separate open
parent gates; no Plan or Validation Plan item is closed by these attempts.

### 2026-11-08 - Provider-first hosted native qualification prerequisite

The operator approved configuring the existing anonymous governed feed for an
owner-only hosted native packet, with pinned disposable Copilot and PSMux,
unchanged native deadlines and cleanup, and no new runner, host installation
or per-request branch. Only the feed URL is held in the repository's Actions
secret; no endpoint or credentials are committed or exported as evidence.
Local reachability is established; hosted access remains unproven.

The controller-only source slice passes 69 focused controls, including exact
native caller/version/phase acceptance and uploaded-file redaction. Lint,
headless launch, feed neutrality, module size and documentation guards pass.
These are controller contracts and real contained pytest-consumer checks,
not a native public caller pass. The controller must clear normal review, CI
and promotion before it can admit the new packet.

A source-only dependency workspace carries that controller slice so it can
be promoted before the parent fixture is published. The refactor owner and
original caller checkout retain the unqualified fixture and all parent gates;
no source, stage or ownership is discarded, and no second agent is launched.
After controller acceptance, resume the actual public caller packet rather
than another local construction retry. Copilot 1.0.105 is the seasoned hosted
pin; it does not establish qualification of a different installed CLI build.

#11546 merged at 08:45:55 UTC as
`7830a21ae8a5cd229dead31c17371b0c3dc693d8`, after exact-head Copilot approval
with no active findings and green required CI. Normal Maintainer completion
used the provider-confirmed review-rule exception, not a CI bypass. Re-read
this parent effort immediately after merge. Controller source is merged;
protected-default support and the actual native packet remain pending.
Continue with immutable fixture preparation and observed promotion. No parent
Plan or Validation item closes on this infrastructure slice.

Prepared the tracked caller-fixture input with its Agent Worktrees changefile.
The focused materialized-input layout regression passes in 1.20 seconds
(22.89 seconds including runner preparation and cleanup); the three native
cases were deselected, not qualified. A real offline UV compile also accepts
the controller's empty default-index variable for generic selections without
installing anything. Keep the immutable candidate local until actual
protected-controller support admits the named native packet.

### 2026-11-08 - First hosted public-caller request, preparation failure

Observed actual protected-default support at
`38db7da71b61c963c4572189e5f2bc61f891b179`, then submitted immutable caller
`0c9c61ee79c58843b9eb0f7a4c8b2ed14c3bd7f3` once through the existing
append-only qualification ref. Request `ebcb7f916b1945fe8127428a844dc47a`
ran as Actions run `42467376071`, attempt 1, against that controller.
Admission passed; pinned prerequisite acquisition failed with HTTP 504 before
the typed native selection executed. Sanitization and upload steps completed,
but no request-bound result artifact exists. Neither native caller behavior
nor native cleanup is qualified. Preserve the original request and transport
identities rather than submitting or redispatching an ambiguous replacement.

The separate promotion run `42465878191`, pinned to Dev
`fbd9db7dbe1d9e0ac0352f64c8141a5ed75b9fd4`, also failed on its pinned PSMux
download returning HTTP 504 before native execution; #11559 tracks that
failure. The preceding promotion succeeded. Follow up on bounded prerequisite
download resilience and truthful pre-execution evidence through normal source
review and promotion, without widening native deadlines or retrying local
construction. All remaining parent Plan and Validation gates stay open.

The controller-only follow-up is #11563, initially published at
`9622da5f8db5afc4e164fb384b8bc3f66fdb3e28`. Its 79 focused controls pass,
including six real loopback download-consumer cases and valid failed
preparation manifests. Native hashes, caller deadlines and request/ref
contracts are unchanged. The existing #11559 recovery owner retains the
single-job promotion retry and its admitted generation; this refactor does
not rerun or reset it. Review, required CI and protected-controller promotion
are pending, and no further native request has been submitted.

#11563 is now provider-confirmed merged. The final source
`e28d12b4d5070f0ee446274e0a4ee556e7d9b739` passed 83 focused controls and
required CI; the fresh Copilot comment verdict resolved the source findings,
and the remaining documentation-impact metadata finding was corrected and
resolved. Normal Maintainer completion omitted the additional CODEOWNER vote;
no CI gate was bypassed. A first merge attempt correctly refused while a
newer metadata-triggered source-CI aggregate was pending; completion followed
its green result, not the earlier green snapshot. Re-read this parent
immediately after merge. Continue by observing the corrected protected
controller's promotion before another known-terminal-failure qualification
attempt. The original hosted request and unqualified fixture are retained;
no parent Plan or Validation item closes on this controller repair.

The corrected workflow and worker are now verified on protected Main
`895ca1de917c742d70c690b207b53f56302101e9`, matching the reviewed controller
source; Dev CI `42470196366` passed. Submitted one new hosted request
`163a388fca4245798202f76a81a70cf7` for the unchanged public candidate
`0c9c61ee79c58843b9eb0f7a4c8b2ed14c3bd7f3`. Run `42472186638`, attempt 1,
is executing against that corrected controller. The already-published input
needed no new transport envelope or branch. The earlier terminal preparation
failure remains recorded, not overwritten or redispatched. Native execution
and exact cleanup remain unqualified until this run's request-bound evidence
is validated.

### 2026-11-08 - Preparation result and operator-directed pause

Run `42472186638` finished with a validated, request-bound failed result:
source and selection passed, prerequisite acquisition failed, and no native
test executed. Copilot archive acquisition completed; PSMux exhausted HTTP
504 and 30-second transfer timeouts. The result records the failure rather
than qualifying an empty selection. Hosted feed access, native caller
behavior and native cleanup remain unproven.

Retained the source-only deadline correction as local commit `976cfcb7fa`,
not a new PR or protected-controller update. One cancellation token now
preserves the original 120-second archive budget across headers, body, output
and at most two retries, instead of shortening every transfer to 30 seconds.
The 85-case focused file passed before the additional retry-delay deadline
case; all ten final real PowerShell download cases then passed, including
body cancellation, retry-delay cancellation and exact server cleanup.
Relevant lint, headless, trusted-CI, module, documentation and feed guards
pass. These local controls do not prove successful hosted acquisition.

Paused at operator direction after the existing controller PRs merged.
No further PR, dispatch, qualification run or schedule is started. Preserve
the unqualified public fixture, both terminal request identities and all
existing resource custody. Resume only on explicit operator direction, then
reconcile the retained deadline correction through the normal publication
and protected-controller gates before further native qualification. The
parent remains Active with its remaining Plan and Validation items open.

### 2026-11-09 - Resume the bounded caller-convergence gate

The operator lifted the pause. The root outcome remains complete driver
convergence and receiving/lifetime acceptance, not the controller repair.
The next observable outcome is a request-bound hosted public-caller result
that actually reaches the native consumer with its unchanged assertions and
cleanup. Acquisition is the current measured blocker; auth-wait,
same-durable-session recovery, default setup and all-caller-Job lifetime
acceptance remain open rather than inferred from controller merges.

First reconcile the retained deadline correction with current source and the
existing recovery owner's scope, then publish only that necessary correction
through normal review, required CI and protected-controller promotion.
The existing #11559 recovery owner records its original promotion incident
as recovered and explicitly retains no source claim on this independent
follow-up; do not rerun or reset that completed generation. After verified
receiver support, make one meaningful same-candidate qualification attempt
and return to the parent caller/default-setup/auth-wait/lifetime gates.
No new rollout, credential, custody, spending or session-lifecycle authority
is inferred from resumption. All task-specific holds remain in force.

Published the bounded correction as #11707 at
`b49eab7bf2cb2960bfc0308fbca90448ed9ad8aa`, after a clean current-source
pull-forward. The 87-case focused baseline passes; the final protected-helper
and server-backoff refinement passes all 15 impacted controls, including real
PowerShell download consumers. The promotion lane obtains the helper from
its protected workflow revision, so an older selected Dev candidate does
not acquire a new mandatory file dependency. Review and required CI remain
pending. No further native run is submitted before verified protected support,
and this publication closes no parent Plan or Validation item.

#11707 is provider-confirmed merged after exact-head Copilot approval
`5831455124` at `659321a8fac1a3f686b7b35536b02e8c8ac0c9d9`, no open
findings and green required CI. The timing finding was addressed with
subprocess elapsed bounds and a multi-retry shared-clock negative control;
all 16 real download cases pass. Normal repository-authorized Maintainer
completion omitted the additional CODEOWNER vote; required CI and accepted
review were not skipped. Re-read this parent immediately after merge.
Continue with actual protected-helper promotion and one real same-candidate
hosted caller attempt. All caller/default-setup/auth-wait/lifetime and
receiving acceptance gates remain open.

The next release is currently behind workflow-owned Main candidate #11701
and original publication run `42648954729`. Its source qualification and
immutable publication succeeded; post-brownout merge recovery failed before
the merge job started during the provider incident. A newer admission
correctly refused while that candidate remains active. The provider marked
the Actions/Checks incident resolved at 18:59 UTC. Posted that changed
availability evidence to the existing release record without rerunning,
resetting admission, creating a duplicate generation or taking over its
owner. Observe the actual protected workflow/helper before dispatching;
release recovery is not a native product failure or parent acceptance.

The existing release owner subsequently recorded an explicitly authorized
retirement of #11701 after the provider refused both supported recovery
paths. It closed unmerged at 19:55 UTC with Main, branch, assets and prior
qualification evidence retained. A fresh normal qualification is recorded
under that owner's existing control. This refactor neither closed the
candidate nor launches a competing generation; resume read-only observation
of the exact protected downloader before the public-caller gate.

The existing owner's fresh promotion `42688134895` succeeded. Verified the
reviewed shared-deadline workflow and downloader on protected Main
`c5d09c6315519dd8f549f7f35785c60800ef8e4e`, then actual receiver preflight
accepted the named native packet. Submitted one request
`65f14cb24c544072b89904c7e3829ed4` for unchanged candidate
`0c9c61ee79c58843b9eb0f7a4c8b2ed14c3bd7f3`; run `42691461498`, attempt 1,
is executing against that exact controller. No new transport envelope or
per-request branch was needed. Earlier terminal failures remain preserved.
Await the request-bound execution/cleanup result; none of the remaining
native, default-setup, auth-wait or lifetime gates is accepted by dispatch.

Run `42691461498` finished with a valid failed result at version verification,
not acquisition. Both pinned archives prepared successfully; the worker's
anonymous hosted feed probe and Copilot version check completed before the
PSMux check. The real output is `tmux 3.3.8` followed by
`psmux 3.3.8 (66cf613 2026-08-18)`: the verifier incorrectly counted the
compatibility banner as a second product version. Source identity passed;
native caller cases and native cleanup still did not execute.

The bounded two-file correction is #11723 at
`c69bb4c1025e92e974f329a3c19796eeb3f07b5e`. It checks exactly one PSMux
product line while rejecting a matching compatibility banner with a wrong
product version, tmux-only output, duplicate product lines and qualifiers.
All 22 focused prerequisite/execution/result cases pass, plus F/E9 and diff
checks. Pins, deadlines and caller assertions are unchanged. Review, required
CI and protected promotion are pending before another meaningful request.
No parent acceptance item closes on this verifier correction.

#11723 is provider-confirmed merged after exact-head Copilot approval
`5832245643`, no active findings and green required CI. Normal
repository-authorized Maintainer completion omitted the additional
CODEOWNER vote; accepted review and required CI were not skipped.
Re-read this parent immediately after merge. Continue by verifying the
product-banner verifier on the actual protected controller, then run one
same-candidate public-caller request. This preserves the exact product pin
and does not close the still-unexecuted caller/auth-wait/lifetime gates.

The actual protected controller now contains the reviewed product-banner
verifier at `158fbaf559ecadebde6f14167394a2260fc7434c`; provider preflight
passes. Submitted one request, `27e7257c95244567874e22442d6eaa93`, for unchanged
candidate `0c9c61ee79c58843b9eb0f7a4c8b2ed14c3bd7f3`. Run `42699294058`,
attempt 1, is in progress on that exact controller. No transport envelope,
new request branch, local native retry or changed acceptance bound was needed.
Await its same-run preparation, native execution and cleanup evidence;
dispatch alone closes no parent Plan or Validation item.

Run `42699294058` returned a valid failed result for that exact request,
source and controller. Archive acquisition, the hosted feed probe and pinned
Copilot/PSMux checks pass. Both real applications construct and install, and
the public caller admits the packaged Manager and creates its target pane.
The first native fresh launch then returns code 3 before a headed broker
receipt exists; resume, same-history, auth-wait and lifetime checks do not run.
The captured execution receipts retain both operation identities, but the
finite native-phase helper discards the failing child's stderr and the pane
has already closed. Preserve this failed run and improve that diagnostic
boundary before another hosted attempt. Do not infer native cleanup acceptance
from fixture teardown or widen any startup or construction deadline.

The finite PowerShell helper now preserves nonzero-child stderr on stderr
and in an already-configured setup log, while still returning no usable reply.
The native fixture supplies its own private setup-log path and includes that
file in failure diagnostics. Four focused real-PowerShell tests pass, covering
configured/absent logging, literal successful replies and the unchanged
ten-second child bound; F/E9 and diff checks pass. This is diagnostic work,
not native caller acceptance. The next hosted request must use a new immutable
candidate containing this change and must retain the original failed run.

Diagnostic candidate `07cb0bbbb7cfc5d5b62437bd7e56484ed8dbade8` ran as
request `20a655a00e7a47338bed5f4520670719`, run `42702195869`, attempt 1.
Its exact-source result again passes prerequisites and fails native fresh.
The retained stderr now identifies the refusal: "Could not verify cold
Copilot handoff; the seed remains staged." The authoritative liveness
check refuses unknown observations; no backend or headed broker started.
Do not weaken that refusal. The private mux adapter did not retain its
read results, so candidate `81dffebd2ad08089b72d9e9569cd1c6c8ec20a60`
adds only non-interfering read-result diagnostics. Submitted that candidate
as request `f5960a980a7740a38247809c00697dd6`, preserving the preceding
terminal run and the single append-only transport ref. Await that original
request's result before any further attempt; all parent acceptance remains open.

Run `42703475111`, attempt 1, failed for that exact probe candidate. Its
private adapter records successful target creation and `has-session` exit 0
after creation, so target creation alone is not the missing observation.
The aggregate verifier still hides which mux or binding operation failed.
Candidate `16c599957d7bc45279dba5bc5c0d68a6112d8f3d` now reports the original
exception for either failed probe without changing verdict fields, conservative
unknown-state handling or launch admission. All eight focused liveness cases
pass; F/E9 and diff checks pass. Submitted request
`6bc5827ad3074e52827ce9d76fd7dd04`, run `42705351257`, attempt 1, for that
immutable candidate. Preserve every prior terminal request and inspect the
new run's original exception before selecting a correction.

Run `42705351257` preserves the concrete failure: the authoritative mux
presence probe raises Windows error 2 (executable unavailable) from the
backend environment. The caller's private adapter exists and creates the
target, but the fixture did not declare that dependency through normalized
session setup. Candidate `f1f5262bfdd904e55c145d6c62f90349329a31f9` adds only
the fixture's supported `session_path.windows` declaration; embedded runtime
scripts compile and F/E9/diff checks pass. Submitted request
`fa92ee2a7b25425494e63e7aa2c5d5e3` against that immutable candidate without
changing liveness refusal, startup clocks or native assertions.

Source-auth recovery implementation remains with this effort's owner, not
only baseline qualification. After the current real-caller baseline, the next
contract milestone is a reviewed callable receiver/enforcement boundary for
native UI/in-flight source loss, conservative stop/preserve and cold resume
of the same durable conversation, followed by its actual native acceptance.
The proposed observation label is not a callable contract. Dependent renewal
work may prepare disabled/unsupported/unknown controls now, but positive
integration readiness requires that implementation and revision-bound proof;
activation and deployment remain separate gates.

Run `42707411094`, attempt 1, proves the session-path correction: the fresh
public caller binds the common broker in 12.36 seconds and reaches history,
live-reuse and controlled-prompt assertions. It then fails the fixture's old
single-execution-file assumption, before exit and resume qualification.
The real normalized caller has separate launcher and backend operation records;
do not discard the launcher records or confuse their process identity with
the native child. Candidate `979abb44b6d9b0afe9f0ea5201c268acaa683429`
selects exactly one `backend-command` receipt while retaining the existing
native PID/birth and terminal exit assertions and rejecting duplicate backends.
Three focused cases pass (launcher-only, one backend, ambiguous backends);
F/E9 and diff checks pass. Submitted request
`ea7c0179578c4f549dca6248ce241516` for the complete focused native case.

Run `42708953945`, attempt 1, proves fresh history/receipts at 21.30 seconds
and exact fresh retirement at 23.55 seconds. Resume then opens a new home
screen without a resume identity: this isolated application has no installed
sessionStart hook, and the fixture had neither a registered worktree head nor
the normal user-home `.copilot` layout. Candidate
`2cf9a5d75093d0c4a2e8bdc34ff30f5514182c2c` uses that normal layout and the
installed public `bind-session` command with the observed native identity and
pane, verifying the actual worktree head rather than writing fixture state.
The resume invocation still supplies no explicit resume argument. The testing
reference now explicitly excludes automatic-hook adoption from this evidence.
Embedded runtime scripts compile; F/E9 and diff checks pass. Submitted request
`b6f17302b9b9486f987a845a3c6d9d2e` for that immutable source.

The failed resume also exposed strict UTF-8 decoding of captured runner output
in the hosted controller: the bound JUnit preserves the native failure, but a
non-UTF-8 console byte prevents phase-log retention. This controller diagnostic
defect remains an owned follow-up before parent completion; it does not turn
the failed native case into a pass or authorize a blind workflow rerun.

The explicit binding attempt in run `42710860824` correctly refused a
directory without committed receiving-side repository authority. Candidate
`79d5adadbc9613e9df2b7e3ab9931e061f414a89` supplies a real local-only Git
repository and committed configuration without a remote or weakened fence.
Run `42712239092`, attempt 1, for request
`eac16a9b725a40c0b326b59717e60d32` then passes explicit native binding,
fresh history/receipts at 23.86 seconds and exact fresh retirement at 26.01
seconds. Resume selects the correct head and the actual native process
carries `--resume=<same-id>`, but it runs without a common broker.

This is now a production caller-convergence gap, not missing fixture
registration. The seedless public `embody` branch in `handoff_cli.py`
uses `copied_setup_command` only; unlike seeded setup and the normal resolve
path, it does not enter admission-only deferred execution. The next source
slice must connect that caller to the same broker while preserving staged
seed custody, selected resume identity, acquisition owners and liveness
guards, then requalify the complete real caller.

The separate encoding correction is provider-confirmed merged as #11765
after exact-head Copilot approval `5833815651` at
`dfae7ab7a8c1d213d38458501ddc79a75a8053bf`, no open findings and green
required CI. Nineteen focused controls include a real Unicode-producing,
failing Python child with inherited `cp1252`; the worker now explicitly
selects UTF-8 rather than relaxing decoding or outcomes. Normal
repository-authorized Maintainer completion omitted the additional
CODEOWNER vote; accepted review and required CI were not skipped.
Re-read this parent immediately after merge. Protected promotion and
live use of that corrected controller remain separate from this source merge.
No parent Plan or Validation item closes on the encoding fix.

### 2026-11-09 - Receiver implementation ownership reassigned

The operator reassigns receiver implementation to the contributor owning
draft #11758. Release this effort's build claim on that bounded receiver
scope; the preceding journal's implementation reservation is superseded.
The refactor driver retains interface coordination, implementation review
and revision-bound native qualification as the gate before positive renewal.
The existing qualification infrastructure and public-caller convergence
work continue independently and must not block receiver implementation.
No activation, deployment, release bypass or session-lifecycle authority
is added, and unrelated source/resource claims are unchanged.

Inspected #11758 at `4b818720e4bfe9b20fbb3e972e6d8b6434554eb2`: it is an
open draft with disabled source-loss controls and the expected native proof
matrix, not a callable receiver. Confirm that prestage boundary and transfer
the incomplete contract explicitly: the proposed observation label remains
non-callable. The implementation contributor must supply the actual interface,
receiver identity/revision rules and enforcement semantics for review; native
UI/in-flight source loss, conservative stop/preserve, obsolete-authority
negatives and same-durable-history resume remain qualification obligations.

The receiver contributor accepts the assignment and identifies the necessary
auth-specific `run_headed`/`_PaneChild` seam. Release only that coupled
launcher lifecycle for the default-off receiver: exact owned-child and
descendant stop with confirmed cleanup, preserved durable conversation/history,
seedless resume and a new receiving incarnation limited to the qualified
auth-wait transition. This does not authorize generic re-hook after lost
control, clearing unknown/pending/native-input holds, weakening caller Job
limits or changing the raw Host write path. No live action or activation
follows from this source-scope release.

### 2026-11-09 - Seedless public caller admission correction

Ordinary seedless `embody` now uses the existing admission-only deferred
execution owner rather than copying setup alone. It forwards the fenced
resume selection and preserves a staged Resume seed ID without claiming
that intent. Seeded, deferred-delivery and Bridge-acquisition ownership stays
on its existing path; preparation refusal starts no mux and has no legacy
fallback. The complete focused embodiment file passes all 102 cases, including
fresh/resume/preserved-intent/refusal regressions, existing seed and acquisition
controls, and corrected wrapper assertions. Two stale fixture expectations
were also reconciled to the existing uncertain-mux result and mocked live-pane
boundary; no production guard was relaxed. F/E9 and diff checks pass.

Documentation impact: the existing testing reference already requires the
same broker across fresh and cold resume, explicitly documents native binding
and excludes automatic-hook adoption. The correction restores that stated
consumer contract; broader caller migration remains incomplete in the core
reference. The existing Worktrees patch changefile now names this correction.
Native proof at the new immutable source remains required. The actual protected
controller at `2b66d4fe0974ee17051fb03687e53c848b8eb0e2` still has the prior
worker, not the reviewed UTF-8 capture correction; do not claim that fix live.

The actual protected controller now contains the reviewed UTF-8 worker at
`cc5e7389012575b675be2d61469602e1c37862f9`, with its merged-source CI green
and provider preflight passing. Submitted one corrected-caller request,
`bf9fd313971c45e8a75dcf73db8f4987`, for immutable
`91e1316540584922d288b20dd5c941bbfc32ca3f`; run `42719019792`, attempt 1,
is executing. The separate one-test Unicode capture regression uses reviewed
controller source `392f5cfd35765c0f3849ccd7de2b6713c7ca2a9c` as request
`18a0cb013085466283bcdbd28158dfe4`, run `42719430018`, attempt 1.
These are different focused acceptance questions, not a replay or broadened
suite. Await each original run-bound result; submission closes no parent gate.

Both original requests returned validated qualified manifests from controller
`cc5e7389012575b675be2d61469602e1c37862f9`, with no failed or skipped cases.
The installed public Worktrees caller at exact source
`91e1316540584922d288b20dd5c941bbfc32ca3f` passes in run `42719019792`,
attempt 1: the one complete native case takes 124.502 seconds and the full
worker 173.094 seconds. This exercises the real installed Worktrees and Manager
pair, fresh and cold-resumed common broker, same durable history, live reuse
without seed replay or another mux, exact process births and terminal receipts,
both native incarnations' retirement, and settled fixture cleanup. The returned
runtime is Python 3.12.10 on Windows Server 2025, with pinned Copilot 1.0.105
and PSMux 3.3.8. No allowance, native identity or cleanup assertion changed.

The Unicode consumer at exact source
`392f5cfd35765c0f3849ccd7de2b6713c7ca2a9c` passes its one selected real-child
regression in run `42719430018`, attempt 1 (3.532 seconds total). It preserves
UTF-8 diagnostics while the deliberately failing internal command and JUnit
remain failures. This closes the controller's live external encoding proof;
it does not qualify another native caller or auth-wait recovery.

The complete public detached-caller flow is now ready for source publication
checks and normal PR stewardship. Broader Manager/Worktrees modes, automatic
hook adoption, all-caller-Job lifetime, receiving/skew and native auth-wait
qualification remain open. Receiver implementation stays with its accepted
contributor; this driver retains review and revision-bound native qualification
before positive renewal. No whole Phase or Validation item is silently closed.

### 2026-11-09 - First receiver implementation review

The independent receiver is draft #11781. Reviewed its runtime at
`d0649d55037ec7a8c39c155555f4290a8979f097`; the observed current head
`88c810e52566e91ec9de11c32163e1ec81641a3b` adds only changefile/coverage
metadata, with receiver bodies unchanged. Direct review and bounded synthetic
Python probes identify two correctness blockers. Active renewal can shorten a
lease to 10 ms without waking the old expiry wait: the receiver remains running
after the new deadline, contradicting immediate sub-second stopping. Separately,
a native-control hold is treated as source-auth expiry; with an otherwise valid
five-second grant, the expiry path starts retirement with almost 4.9 seconds
remaining. Preserve irreversible broker-control holds without confusing those
holds with source validity or native CLI lifetime.

These are source/loopback probes, not native Copilot or history qualification,
and no productive process, credential or global clock was changed. Returned
the concrete locations and reproductions to the implementation owner without
editing its workspace or building a competing receiver. Await the corrected
head for delta review. Positive renewal and enablement remain disabled.
The existing public Worktrees native proof does not opt in to the new
`run_headed(auth_wait=...)` API; an explicit revision-bound native receiver
fixture and supported selection are still needed before receiver acceptance.

Caller publication is parked, not abandoned: its safe pull-forward auto-aborted
on conflict and preserved the clean qualified history. Local backups retain
source `91e1316540584922d288b20dd5c941bbfc32ca3f` and evidence-journal commit
`88de6715e80228eee456f9328ff276b61e872187`. Reconcile the owned local stack
against its original base without dropping the native fixes or reverting
controller corrections already upstream, then continue normal publication.

The owned 17-commit stack was squashed against its original base with a
byte-identical tree check and all backups retained, then reconciled once onto
current Dev. Already-merged controller copies keep the upstream versions;
the public caller, native fixtures and journal remain. Reconciled source
`759e2739e7b78df521ccd60d44829589b2b0adc6` passes the combined 118-case
focused packet, F/E9, whitespace and all 13 install contracts. Its exact
native requalification request is `f6c576c52d9d4e48a4e440f5f852092e`.
The shared append-only ref moved during compare-and-swap; the push was
explicitly rejected, then the read-only reconciliation fetch timed out.
The original request is preserved, not replaced. The supported resume path
currently refuses that unconfirmed envelope; resolve this client recovery
gap without replaying unknown effects or editing admission/request state.
No native test has been dispatched for this reconciled candidate.

Delta-reviewed receiver revision
`9cca7e19af416b103c4e571aeacd1e0e40898dd6`: lease replacements wake the
deadline watcher, sub-reserve grants close authorization before a new launch,
and active source renewal preserves native lifetime without clearing a
broker-control hold. Three independent bounded Python/loopback probes confirm
the two reported source blockers are addressed and recovery remains refused
after a genuine stop with a hold. This is source-level review, not native
acceptance or permission to enable anything.

The actual protected controller declaration at
`9324da33891644180b0237cccaadf8d9aa086a4b` still admits only the HTTP-52
and public-Worktrees native packets; its pinned binary preparation is exclusive
to the latter. Generic plugin/file selection does not provide those native
prerequisites. Prepare the smallest separately reviewed receiver-native fixture
and controller selection in the qualification workspace, without changing
receiver implementation, widening secret exposure, installing on a productive
host or relabeling the public-caller pass. Native receiver proof remains an
independent gate before positive renewal.

### 2026-11-10 - Same-request transport recovery

The source-owned qualification client now distinguishes an exact Git
contention rejection from an unknown push outcome. Recovery checks the
original envelope, candidate parents and retained shared history, keeps the
rejected attempt's diagnostic, and clears stale diagnostics before another
attempt. Unknown effects remain non-replayable even after the ref advances.
No request/admission record, installed runtime, shared Git setting or history
was manually rewritten.

Focused source validation passes 118 controls in an isolated Python
environment; the final ancestry and stale-diagnostic refinements pass the
25 affected cases. These include real Git contention and interrupted-fetch
recovery, wrong-envelope/ref/previous-tip and policy-rejection negatives,
lost-response refusal and unchanged single-dispatch custody. F/E9,
whitespace and all 13 install contracts pass. An initial run's two nested
consumer failures were missing isolated pytest dependencies, resolved by
using a prepared isolated environment rather than changing test semantics.

The original reconciled-source request
`f6c576c52d9d4e48a4e440f5f852092e` is now accepted for the same
`759e2739e7b78df521ccd60d44829589b2b0adc6` candidate. Its rejected
envelope and diagnostic remain in the request's attempt history; a normal
append to the same reserved ref supplied the replacement envelope. No fresh
request, per-request branch, forced ref update or dispatch replay was used.
The hosted result remains pending; acceptance is not native qualification.
The source-client correction still owes its normal review/CI/merge, and
the parent caller, receiver, lifetime and receiving gates remain open.

The original request completed as failed on run `42733468145`, attempt 1,
with exact candidate `759e2739e7b78df521ccd60d44829589b2b0adc6` and
protected controller `556cb3df2969b614704b827428ed9bf4e335b29c`.
Pinned prerequisites passed; the fresh public caller was refused before
broker binding. Its retained log says another mux became live before
handoff. Comparison against the earlier qualified source identifies the
new passive socket observer: unlike the fixture's CLI adapter, it was not
given the private PSMux namespace. Align each leg's observer environment
with its existing private namespace, preserving the cold-admission guard,
actual consumer, clocks and cleanup. The three focused namespace controls,
F/E9 and whitespace pass. This diagnosis and fixture correction are not
a native pass; qualify the changed committed candidate separately and
retain the original failed request unchanged.

The corrected fixture candidate
`b8ec1f5052ed4e91a203630033e211119be4a42d` now qualifies through
request `9f052ef1e2d34adf9c7f237dd1b03aff`, run `42735243674`,
attempt 1, protected controller `3993324d54525097b6355938b456d32f11183e8c`.
The validated result records the actual public caller, pinned Copilot
1.0.105 and PSMux 3.3.8, Windows Server 2025/Python 3.12.10, and the same
image as the failed comparison. The native case passed in 110.443 seconds,
with 157.922 seconds including preparation and test-runtime settlement.
Fresh/resume, broker/history, single-seed reuse, exact native identity and
cleanup assertions passed without another local construction run, larger
clock or weaker guard. This restores the reconciled public detached caller
proof; attached/new-window/no-mux, all-caller-Job persistence and receiver
auth-wait remain independent open gates.

The source-client recovery is under #11812. Its first review correctly
identified a history-check/append time-of-check race. The original ancestry
requirement now travels into append and is enforced on every adopted tip,
including contention retries. Real Git deletion, rewrite and contended
rewrite controls pass with the 28 selected recovery cases, preserving the
original identity and no-dispatch refusal. Required CI and a new review of
that correction remain owed before merging the prerequisite.

### 2026-11-10 - Published caller head and remaining source gates

#11830 publishes the functional public caller. Exact published head
`2ed5d7e31eb44db41ef14a301c6636db96386c96` qualifies through request
`9cb660e0d30a4d51aedaebbb27184753`, run `42738456204`, attempt 1,
protected controller `0ba41eeca0038c618c307d8b2705c36cd2787f17`.
The validated native case passed in 167.098 seconds, with 220.031 seconds
including preparation and settlement. Pins, platform, actual consumer and
cleanup remain unchanged. The preceding publication rebase preserved both
structured and emitted diagnostics; its five focused finite-phase/discovery
controls passed.

Required CI correctly rejects the command module's growth past 1,000 lines.
Move the new command-preparation selection into the existing cohesive
embodiment helper rather than widen the cap. Review also correctly identifies
inherited Git selectors in fixture mutation calls. The fixture now uses the
canonical repository-identity environment, with a real foreign-repository
control proving the other head, index and content remain untouched. These
refinements pass all 103 selected caller and Git-isolation cases, F/E9 and
whitespace. They still owe final committed-head CI, review and native evidence;
the previous pass is not silently relabeled as the next head.

#11812 remains a separate source-client prerequisite. Its first-ref history
anchor is now persisted before the reconciliation fetch and reused across
later resumes, including anchors learned during ordinary append contention.
The same request remains refused after an intervening deletion or rewrite;
an observed envelope cannot bypass an already retained history requirement.
All 36 selected recovery controls pass. A review summary's absence of inline
threads does not waive its substantive high-severity observation; the durable
anchor correction owes its own review and required CI.

The independent receiver-native fixture is prepared against the reviewed
receiver revision in a separate owned qualification workspace, with no
receiver implementation edits. It exercises explicit auth-wait launch,
renewal/shortening, exact native and extension retirement, same-session
seedless recovery/history and the native-input hold. It is not executed or
qualified yet; protected packet support and its prerequisites remain owed,
without expanding the Worktrees-only private-feed exposure.
