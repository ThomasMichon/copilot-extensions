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
| Existing component owners | Retain receiving acceptance, custody, startup, retention and permissions | #10636, #5930, #5898, #8669, #5579, #4417, #9364, #8375, #6009, #10052 |

## Coordination

- **Topology:** tightly coupled source work with one owner, one checkout and
  dependency-ordered functional outcomes.
- **Host (owns PRs):** Refactor driver.
- **Delegates:** none at proposal time.
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
- [ ] Publish the first complete functional flow and steward its PR to merge.

### Phase 3 - Common headed broker and all caller convergence

- [ ] Move canonical mux creation, prerequisite composition and pane execution
  into the same core; preserve platform behavior and literal prompt/argument transport.
- [ ] Connect the launch-injected, scoped trusted extension to Session Host.
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
