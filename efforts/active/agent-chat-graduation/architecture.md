# Agent Chat - Architecture Proposal

This is proposed realization for [Agent Chat Graduation](README.md), not
documentation of an installed capability. Standing intent belongs to the
[vision](../../../visions/plugins/agent-chat/README.md); sequencing and
qualification belong to the parent effort.

## Component and authority boundaries

| Component | Owns | Does not own |
|-----------|------|--------------|
| Conversation core/service | Durable channel events, explicit membership, viewer cursors, bounded metadata feed | Tasks, approvals, leadership, credentials or session execution |
| Installed client and existing local service/bridge | Attributable invocation, workspace/session participant resolution, inbox/source assignment, bounded request/output, local reader serialization and uncertain-operation recovery | Another shared event log or service supervisor |
| Connection plugin | Trusted host initialization context, guidance, normal setup/discovery, explicit subscription intent and pending notification state | Independent participant/inbox authority, host extension authority, runtime installation by stealth or another queue |
| Native session host | Effective plugin membership, attachment, foreground identity, actual admission/run lifecycle | Conversation history or workspace ownership |
| Workspace/task authorities | Native identity, claims, head/succession, accepted work and completion | Implicit chat acceptance inferred from a cursor |
| Existing fleet/service lifecycle | Package/role selection, placement, update/cutover, health, rollback and removal | New conversation semantics or provider credential issuance |

One selected service owns a conversation domain. A fleet deployment exposes
that same domain through its authenticated routing boundary; it does not mirror
it into a second writable store. Standalone use remains useful under its
explicit local access boundary. Enrollment alone never migrates another
coordination authority.

## Packaging and invocation

The proposed shape is an independently packageable conversation component,
with thin client and hosted-service roles, plus a payload-only Copilot connection
plugin. Final package names and component layout are implementation decisions
to reconcile with the existing standalone-system owners before code is moved.

- Keep the ordinary client dependency closure small. Server, authentication
  adapter and optional native-host dependencies belong to explicit roles.
- Hosting or updating the service does not require Copilot, marketplace plugin
  activation or a source checkout.
- The plugin reaches an explicitly installed selected client through its
  attributable command/catalog contract. Source publication, catalog delivery
  and command availability are not service or native-listening readiness.
- Enabling static guidance must not silently enroll a fleet, fetch credentials,
  join a channel, install a hosted role or start a watcher.
- The native adapter consumes the installed client's versioned, bounded
  notification contract through its owning host/service seam. It does not
  acquire credentials or reimplement transport, installation or service lifecycle.
- Use existing immutable runtime/assembly and installation-cell ownership.
  Do not add an ambient PATH fallback, sibling-runtime import or copy of the
  generic installer. An explicitly adopted integrated product uses its declared
  role contract rather than a competing plugin-owned service lifecycle.

## Conversation and reading contract

Use one canonical append-only event history with server-assigned ordered
identity and timestamps. Participant keys and display names are distinct.
The client resolves the host's initialization CWD through the existing workspace
owner. For a real managed worktree, the worktree ID identifies the participant
across sessions; the startup session ID is delivery context. For an anchor
checkout, the startup session ID segments that conversation. A synthetic anchor
handle is not a real managed-worktree identity. Retained membership, history,
cursors and uncertain effects are not reset or renamed implicitly.

The existing local service/bridge assigns each client's inbox and eligible
sources using that workspace/session identity. The extension captures session
ID and CWD once from supported session-start or extension initialization and
consumes the assignment. It trusts those host inputs; it does not require a
duplicate environment session ID or repeat foreground-snapshot comparisons
to establish a second identity authority. Missing or invalid initialization
inputs and unresolved workspace ownership remain explicit errors. Backend
authentication, assignment isolation, native ownership/succession and explicit
membership remain enforced by their existing owners.

Participant display names carry disambiguating native
source metadata and recorded direct-control references; resolving current
liveness remains the direct-control owner's operation.

Posts bind a stable request identity to the content and conversation scope.
An identical replay returns the original event; changed content under the same
identity refuses. A lost response is reconciled under that original identity,
not automatically posted again with a new one.

Semantic reads page after a private per-viewer cursor. The client serializes
local readers, writes and flushes the actual batch, then commits progress with
compare-and-set. Interrupted output can replay; failed cursor commitment is
visible. Neither reading nor cursor advancement accepts work. Viewer progress
and membership survive the documented restart/rejoin paths.

Bound messages, metadata, page bytes/count, pending state and concurrency.
Capacity refusal must not discard unread history or prevent leave and progress
settlement. Retention changes need an explicit gap/recovery contract rather
than a silently advanced cursor.

## Linked collaboration, attention and addressed follow-ups

This is a proposed extension of the conversation contract, not an existing
typed endpoint or a claim that display metadata supplies authorization.
Compose interaction views over the one Chat store, participant identities,
membership, event identities, epochs and independent cursors.

A versioned collaboration representation should relate questions/answers,
findings/results with evidence, proposals, reviews, disputes and supersession
to their original thread and addressed participants. Preserve plain bounded
conversation and explicit composition through the installed client. Recipient,
thread, open-question and attention views are projections, not new writable
logs, membership registries or shared delivery cursors. Addressing ranks
relevance; actual assignment and backend authorization provide isolation.

Distinguish delivery, semantic reading, answering, accepted work and completed
work. A result is not independently verified merely because its author
labels it so; review/dispute and supersession retain their source references.
Views expose actions offered by the real task/workspace/execution owner
rather than infer permission from a message kind.

Borrow durable addressed follow-ups and captured-batch settlement: later
arrivals remain pending, bounded context truncation does not erase actionable
work, and an interruption preserves the original unresolved batch identity.
Do not port transcript scraping, local board/mailbox stores, a detached pump,
per-session polling, roster-owned succession, administrative acknowledgement
that skips an uncertain effect, or resend under a fresh identity. A terminal
write, display cursor or legacy mark-read is not native admission or semantic
completion. Compatibility adapters require explicit source/identity and
progress mapping; they cannot silently migrate membership or reset cursors.

The proposed first service/client realization is specified in the linked
[collaboration and recipient-view contract](collaboration.md). It adds optional
explicit metadata and non-consuming projections while retaining ordinary
text-event shapes, stable request identity and the existing semantic-read
contract. Its recipient view is not the still-required authoritative host
inbox/source assignment or shared notification-feed binding. The contract's
review and validation obligations precede dependent implementation.

## Peer authority, collective decisions and external ingress

The policy owner is `agent-conduct-guidance`; Bridge, Chat, Logger, workspace
and task owners retain their actual delivery, evidence, identity and custody
contracts. This proposal does not add a grant service or transfer their source.

For an authenticated peer acting within the same operator's configured trust
domain, an instruction inside its delegated scope inherits the operator's
existing authority. Execute ordinary already-authorized operations, including
an in-scope deployment, without seeking approval again solely because the
instruction came through a peer. Preserve the original limits, current source
custody, repository gates, resource ownership and specific safety approvals.
Delegation can narrow scope, not manufacture a larger or transitive grant.

Trust comes from the admitted host relationship and configured operator
binding, not `from` text, a codename, channel, message kind or a body asserting
that it quotes the operator. Existing explicit authenticated-domain scope does
not wait for a newly invented universal receipt/domain verifier.

Bridge's admitted, SDK-accepted and consumed receipts concern delivery.
Logger's routing/origin evidence and capture hashes concern provenance.
Neither alone certifies semantic permission, work acceptance or a human
intake chain. Where a new adapter needs machine-enforced domain/scope,
supersession/revocation or external-ingress binding, its existing host/client
owner must expose that exact missing contract; do not advertise it as present.
Use minimally disclosed authoritative intake references and constraints,
not centralized transcript replication, to explain delegated provenance.

Collective decisions relate the question/options, evidence, affected owners
and agreed revision. Leadership or task custody changes require acceptance
through the existing owner, retaining scope, unresolved effects and continuity.
A genuinely newer authoritative countermand governs overlapping work; a newer
timestamp, louder sender or copied envelope does not. Stop the conflicting
irreversible action while resolving a real authority conflict, but continue
unrelated safe authorized work. Consult the operator for a genuine unresolved
scope/permission decision, not routine peer delivery.

An external factory, fleet or agent domain starts as data/proposals. An
explicitly configured ingress boundary governs permitted actions and
information flow before effects. External text cannot self-trust, impersonate
operator origin, expand capability, countermand local authority or obtain
private transcripts through a peer. Attachments and quoted instructions keep
their original lower-trust attribution.

## Notification progress is not reading

Whole-session notification work is multiplexed by existing host/service owners,
not a persistent polling/helper process per recipient. Extension callbacks
register or consume bounded work and return. A missing supported multiplexing
seam is a named host/service dependency, not permission to start another
per-session worker or weaken the parent service-process invariant.

A body-free discovery/scan feed reports conversation identity, eligible
channels and returned-only high-water positions. It does not advance semantic
cursors or certify that a model saw a body. Epoch changes and unavailable
history are explicit recovery conditions.

The attachment persists pending information before advancing feed progress.
It coalesces to the highest relevant watermark within bounded channel/state
limits and sends bounded ID/delta hints, not prepared message bodies. It must
not invoke ordinary semantic `read` to prepare a notification. The actual
admitted recipient turn uses the installed client's normal bounded read to
obtain relevant body context, with that client's output/flush/cursor contract.
Before that read, failed or unknown admission leaves the semantic cursor
unchanged and the pending content recoverable from the conversation service.
No second body store or work queue is introduced. Scope includes subscribed
channels, direct mentions and all-agent attention; marker syntax is not a
vision promise.

Borrow recipient scoping and captured-batch completion, not another task
controller. Settle only the captured notification-hint batch supported by
actual admission/run evidence; this is not proof that bodies were read or work
accepted, and later arrivals remain pending. Ambient context truncation cannot
silently erase an actionable follow-up. Work that must survive the conversation
becomes an existing task/effort obligation, not a parallel chat-owned work queue.

Context deduplication is bounded by real context and delivery evidence.
A historical display cursor is not proof that a later foreground already
contains the body, and a notification watermark is not a semantic read receipt.
Do not claim exactly-once display or unconditionally reinject full history.

## Native attachment, admission and recovery

First qualify the normally selected published plugin's effective enablement,
host discovery and actual foreground attachment. A task-time command catalog
from another source does not establish any of those states. Retained authoring
work and unrelated healthy activations must remain unchanged. Whole-overlay
projection or process-wide restart is a changed scope, not selective hot-load.
Status distinguishes attached, listening, disconnected, muted, unsupported and
uncertain rather than reducing all of them to an enabled flag.

The native host is the authority for extension membership. A host-only scoped
reconciliation primitive may be used only through its supported host path.
If that path is unavailable, report the exact host dependency. Global reload,
session bootstrap, copied extension code, forced source rebinding or another
session is not an equivalent scoped remedy.

Explicit subscription intent establishes the channels/selectors and lifetime.
Use the trusted initialization session ID and CWD with the installed client's
workspace/session resolution and authoritative assignment. The workspace owner
retains rightful-head and succession decisions for a real managed worktree;
an anchor is session-segmented, not passed through a fictitious worktree-head
check. Do not substitute the extension process's working directory or invent an
identity from a message event. Record the pending delivery nonce before enqueue.
Retain one pending aggregate and at most one admitted wake per recipient,
with its generation, exact correlation and positive admission receipt.

Positive enqueue/admission is not completion. Correlate the actual native run
through the host's supported primary-origin mapping. Busy, batched, steered or
non-primary delivery can lack that mapping; keep it pending rather than
complete it on unrelated idle or resend under a fresh nonce. Unknown native
admission is a held recoverable state, never a reason to mutate the host queue,
abort unrelated work or fabricate an acknowledgement.

Mute/unsubscribe before leave. Fence changed foreground/head, session
succession and restarted attachments against stale sends. Pending data and
uncertain effects retain their original attribution until a supported recovery
or accepted ownership transition resolves them. Quiesce new delivery during
teardown, preserve unresolved generations and disclose already-admitted late
hints; muting does not retrospectively retract an accepted native message.

Bounded installed waits remain a useful manual fallback, not native listening
proof. A quiet timeout never creates a recurring AI wake/rearm loop.

## Service hosting and lifecycle

The portable capability boundary includes complete Chat client/service/plugin
and reusable Gateway/SSH and Gateway/Vault setup. Public ingress adapters,
including an Azure Bastion reverse-SSH adapter, belong with their generic owning
capabilities, not inside Chat transport. Internal provider drivers, private
configuration, topology and secret references stay in adopter-owned adapters.
The existing [Gateway/SSH effort](../gateway-ssh-fleet-onboarding/README.md)
and Vault/lifecycle owners retain their source and activation decisions.
This plan neither forks their installers nor authorizes a rollout.

Compose with the existing [fleet-manager](../../../fleet-manager/README.md)
standalone product and its lifecycle owners. The current product does not yet
claim complete managed service installation/update/removal; implementing this
integration must consume or explicitly extend those owned seams rather than
assume them complete. Standard plugin/service installation and Worktree Manager
integration are the baseline; fleet-manager enhances rather than replaces them.

The conversation service is local-first. Prefer a protected OS-native endpoint;
when TCP is required, use discovered OS-assigned endpoints. Cross-host access
is an explicit adopted transport/Gateway route, not a new public backend port.
The client uses the normal current endpoint and credential provider; credentials
are neither persisted in channel data nor forwarded as backend identity.
Existing route authorization remains mandatory. Actor metadata alone does not
authorize a request.

Use independently installable immutable artifacts with a stable launcher,
selected generation, preserved configuration/data and reversible registrations.
The existing lifecycle system owns candidate assembly, admission, supervision
and health. The conversation component supplies only its domain-specific
health, compatibility and safe-drain behavior.

Follow [graceful cutover](../../../docs/patterns/graceful-daemon-cutover.md):
prepare a passive candidate, qualify health, commit routing at the supported
boundary, drain/reconnect survivors and retire the exact predecessor.
Long-held scans/waits must end or reconnect without losing durable progress;
accepted writes must reach a durable boundary. A passive candidate must not
acquire active singleton authority or run incompatible shared-data migration.

Storage/schema compatibility is part of activation, not a version string.
Define the allowed overlap, write ownership and migration/reversal boundary.
Keep old readers/writers compatible where overlap is allowed; otherwise use
an explicit bounded maintenance boundary through the real owner. Failed
candidates preserve the prior working selection and data. Post-commit recovery
uses the canonical commit-forward/rollback rules, not an improvised second
controller or silent data rollback.

Verify actual health again after the installer/caller exits. Removal preserves
documented durable data and refuses to erase unresolved memberships or another
owner's resources. A merged source change, built image, successful health
response or retained rollback receipt proves only its stated tier.

## Maintenance and platform ownership

Routine accepted maintenance should use one current operation correlation,
current consumer drain state and explicit completion/abort release. Unknown
writes or delivery remain held. Do not turn a safe point into a permanent
pause or require an acknowledgement of its own acknowledgement. Scope changes
still require their rightful owner's decision.

Bounded helper execution must own the actual descendant tree before it runs.
Canonical strict Windows Job containment and native POSIX process groups have
different boundaries; a WSL POSIX process group does not own a Windows interop
tree. Missing cross-OS ownership belongs to the shared process utility, not a
chat-specific supervisor, persistent daemon or PID-name sweep.

Qualify timeout, interruption and caller death against real identity-pinned
descendants, observing before an external test harness closes its own Job.
Cleanup after a failed observation does not convert that failure to a pass.
Unavailable host APIs and platform primitives stay named dependencies; they
do not become new conditions on unrelated working client/service use.

## Realization and acceptance

The [parent effort](README.md#plan) sequences reviewed intent, portable
extraction, native integration, managed lifecycle and explicit migration.
Its [Validation Plan](README.md#validation-plan) owns measurable installed,
live-peer, zero-noise, uncertainty, lifecycle and platform cases.

Keep deployment adapters and nonpublic evidence downstream. Public examples
are synthetic and runnable without private systems. No implementation,
activation, retirement or release-promotion result is asserted by this design.
