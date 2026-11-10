# Session-generation implementation boundary

[agent-mcp](../README.md)

The current internal checkpoint, custody host and conduit are not yet an
activated production reattachment feature. The serve provider can advertise a
backend capability only from an admitted physical generation; ordinary
CLI/installer paths do not yet select custody adoption.

An explicitly custody-bound single-stdio `BridgeSession` can export quiescent
JSON data and reconstruct its decorator pipeline over a supplied retained
transport. The private binding covers the logical custody session and the
existing complete configuration, semantic environment and inherited cwd
equivalence check. Its comparison key is not included in the exported data.
Changes after initial upstream admission refuse export; incompatible context,
session or schema refuses import before transport startup.

Only known built-in decorator state and settled correlation counters can be
transferred. Live futures, tasks, locks, transports and arbitrary plugin objects
are not serialized. Cancelling a quiescence wait does not cancel the accepted
application dispatch it was observing. Resume requires an explicitly supplied
custody-owned transport; it cannot start a replacement upstream or replay
initialization.

The private ordered attachment retains published receipts and admission credit
after caller cancellation. Cancellation before publication consumes no
sequence. A disconnected attachment reports accepted effects as unknown and
never resends them as fresh work. These are source-level transport guarantees,
not exactly-once crash recovery.

Launcher adoption and actual Windows/POSIX process and lifetime qualification
remain required before activation. Routed,
HTTP, dynamic-auth and CLI sharing remain outside this bounded stdio slice.
Pre-feature live wrappers do not acquire a new protocol retroactively.

## Internal backend epochs

The backend entrypoint validates the existing private endpoint
authentication, actual immutable launch generation, selected installation,
caller-selected configuration root and complete execution context before
constructing an epoch. Preparation and activation revalidate admission.
An older custodian's package version or missing legacy runtime-root metadata
does not substitute for, or veto, incoming physical-generation evidence.

Each attachment has one positive integer epoch. It prepares once, imports only
compatible checkpoint data, and cannot send application work before activation.
The retained transport sends only through custody; detachment never closes or
recreates the upstream process, connection or client pipes.

Application and control windows are separately bounded. Quiescence stops new
application admission but keeps cancellation, server replies and upstream
notifications readable. Late control work invalidates a quiescent checkpoint
boundary until it settles. Joining dispatches includes client-output and
settlement acknowledgments; cancellation of a waiting caller does not cancel
accepted work. Lost delivery or settlement receipts remain uncertainty, never
permission to replay. A lost backend link cancels only its local execution
tasks, without closing custody's retained upstream.

These are contained source-level protocol controls. Custody-side exclusive
commit/fencing and upstream request ledgers compose with the provider and host
below. Launcher adoption and native separate-process qualification remain open.

The custody-side state owner now reserves separate application, control and
output byte budgets. It retains accepted client inputs, unacknowledged
deliveries and server-originated requests. A server request remains a checkpoint
blocker until its corresponding client response has actually been sent.
Generated upstream subcalls carry their admitted origin; an epoch number alone
cannot authorize a send.

The commit fence changes the local attachment identity before activation is
sent. All outgoing-epoch effects are fenced immediately. Lost activation or
output acknowledgment retains uncertainty and cannot resume the old epoch or
replay its request. Only an uncommitted preparation can be discarded. These
state transitions are exercised by the internal orchestration below, not yet
by production transport or client intake.

## Internal custody orchestration

The source-only single-stdio custodian retains the transport and ledgers while
private backend links are replaced. Its connector must separately authenticate
the current physical backend before supplying streams; the orchestrator itself
does not infer authenticity from a reader/writer or package label.

Handover fences application admission, joins input publications and complete
dispatch/output settlement, then waits for server requests to settle. The
input/upstream freeze is acquired only at a settled boundary, so waiting for a
server response does not hold the lock needed to deliver it. Current code
imports the quiescent data before custody publishes its exclusive commit
fence. The outgoing link closes only after new activation is confirmed, and
its detached pipeline cannot close the retained upstream.

Preparation failure can resume the unchanged owned outgoing epoch. Cancelling
a precommit handover resumes that epoch without cancelling its accepted
mutation. After an uncertain activation, the new fence remains authoritative:
no outgoing resumption, duplicate mutation or cold fallback is attempted.
Unacknowledged input publications have separately bounded application/control
windows even if execution finishes before the admission receipt arrives.

The contained composed packet uses real private socket peers and an injected
upstream transport. It is not proof of separate physical backend processes,
installed admission, stable external stdio pipes, multi-client process counts
or native Windows/POSIX reattachment. The shared-host and conduit composition
below adds source integration, not installed acceptance.

## Internal current-backend connector

The connector now performs authenticated readiness and backend admission on the
same owned connection, with bounded framing and a total handshake deadline.
It qualifies the peer's custody and caller-context contracts, runtime root and
current installation selector before sending the attachment request. It checks
the epoch, physical generation and installation acknowledgment, then rechecks
selection and caller context before returning streams to custody.

The installation root is supplied by the adoption owner, not inferred from a
legacy keeper's package version or missing runtime metadata. The connector does
not call that keeper's generation-specific startup/admission path, launch a
daemon, replace a handle or retire an outgoing process. Readiness alone does
not initialize an upstream: custody must still prepare and activate its epoch.
The backend independently revalidates physical admission during those steps.

Caller-selected configuration roots remain separate from installation roots.
The existing full-context owner computes the private equivalence digest from
the actual cwd, configuration and semantic environment. Changed context,
unqualified peers, missing authentication, timeout or canceled preparation
close only the uncommitted connection, without replay or upstream creation.
These are contained connector/protocol controls, not installed or native
separate-generation acceptance.

## Internal bounded client intake

The client reader no longer has to await an application dispatch or admission
receipt before reading the next control frame. Application and control input
have independent count and byte reservations, including inputs waiting for
admission receipts. The application lane preserves order. Bounded control
workers keep unrelated server responses readable even when a cancellation
must wait for its queued target to be admitted.

Custody can wait for capacity or a handover fence without holding its input
lock. Controls can settle already-admitted work while new application input
waits, and committed activation or precommit resumption wakes that input.
Waiting does not accept or replay a request.

Overflow and malformed frames terminate intake explicitly; notifications are
never silently discarded or retried. EOF joins queued publication, not
re-execution. Cancelling intake does not cancel already-published work or close
the retained upstream. The owning client-lifetime path must still settle those
resources; losing the backend connection is not client EOF.

The contained packet covers full application and notification queues,
independent control progress, byte pressure, delayed admission acknowledgment,
cancellation ordering, handover fencing, explicit overflow, malformed input
and EOF. The source custody host uses this intake; launcher adoption and native
separate-process and lifetime qualification remain open.

## Shared host and conduit composition

The serve provider dispatches authenticated backend attachments separately
from ordinary sessions. It advertises custody support only when its actual
immutable launch generation is selected and admitting work. A capable old
keeper is not a current backend merely because it understands the protocol.

The separate shared custody host owns retained streams for multiple clients in
one installation and actually equivalent execution context. Its publication
namespace and lease role are distinct from the central backend. It never
publishes the central lifecycle control route or applies its own old launch
generation as the current backend's admission gate. Caller configuration roots
remain separate from the installation root; cwd and environment are not
rewritten to manufacture eligibility.

One host watcher observes current selection and schedules independent session
handovers. A mutation in one client does not block another client's migration.
Repeated preparation failures for an unchanged selection and endpoint back
off; a confirmed handover alone updates the session's adopted generation.
Failed preparation resumes the still-owned outgoing epoch. Backend-link loss
is terminal uncertainty, never a new upstream or automatic replay.

The asyncio-free conduit authenticates the known custody installation, checks
its custody and configuration contracts, and sends complete caller-context
admission on the same connection. It does not require the keeper's version to
equal the backend's. The handshake uses a total deadline even for fragmented
replies, consumes exactly its acknowledgments, then returns a blocking socket
to the existing stdio byte pump. That pump's EOF half-close, final-output drain
and lifetime watchdog are unchanged.

Attach acknowledgment precedes early upstream notifications. Client EOF joins
an active handover before owning teardown; failed quiescence still closes
the exact retained transport while reporting uncertain accepted effects.
Losing only a backend link is not evidence that the external client died.

The combined contained packet passes 224 tests with two existing
platform-specific skips and verified cleanup. It includes two clients sharing
one listener, an in-flight mutation plus server request, early notifications,
preparation failure before and after the backend handshake, backoff,
quiescence failure and synchronous conduit use across a handover.
Physical-generation metadata and upstream transports are simulated in these
composition tests. No ordinary launcher, CLI or installer selects custody yet; native
Windows/POSIX separate-process, process-count and installed reattachment
acceptance remain unqualified.

## Internal launcher admission

The explicit internal custody entrypoint and launcher use the existing
attributed daemon lifecycle primitive, under the separate `agent-mcp-custody`
service identity and publication namespace. They do not publish or reclaim
the current backend handle. Publication parents must not resolve to the same
directory, including through an alias.

The launcher first authenticates the current backend's physical-generation
capability and installation selection. It then uses an existing compatible
custodian or performs one owned startup, followed by bounded connection
attempts. A live incompatible keeper is preserved, not replaced. A keeper
connection failure is never surfaced as authority to reclaim a healthy
backend's publication. Selection is checked again after attachment.

Startup requires an actually compatible stable cwd. It does not change the
caller's cwd to make an identity check pass. Production launch does not
silently accept inherited caller-Job lifetime when durable launch is refused;
contained tests retain the canonical primitive's explicit test containment.
Native lifetime independence remains to be qualified.

The expanded combined packet passes 239 tests with two existing platform
skips and verified cleanup. Launcher tests mock process creation and therefore
do not establish native process identity, production durability or process
counts. Ordinary forwarder selection remains unchanged pending separate-
process qualification; the internal entrypoint is not installed adoption.

## Native immutable-slot fixture

The explicit `tests/test_generation_handover.py` packet constructs two private
immutable runtimes with the existing installer engine. Each slot contains an
installed, distinguishable pipeline. The fixture checks actual `sys.prefix`,
the loaded pipeline path and the handling backend PID; it does not replace
launch-generation discovery or interpreter metadata.

Native Windows source runs now preserve the original external stdio conduit
and upstream PID through physical backend replacement. Initialization occurs
once, an accepted mutation settles once on the outgoing backend, and
cancellation, server requests and notifications remain readable. A fresh client
uses the incoming physical backend while that mutation is pending. Subsequent
calls from the original client execute installed incoming code even after the
outgoing process exits. Supported rollback reconstructs both clients' pipelines
on the prior physical slot without replacing either upstream.

The packet covers a prestarted custodian and the internal launcher's actual
attributed startup. Two clients share one custody process; the peak registered
service count is three while two backend generations overlap. Authenticated
lifecycle control retires an unselected fixture backend; application admission
does not become a cleanup bypass. Exact fixture processes and upstreams are
settled through their contained lifecycle owner.

The combined native Windows and existing custody regression packet passes
241 tests with two existing platform skips and verified cleanup. Both native
keeper-start variants pass physical replacement and rollback.

Both native POSIX variants also pass, using real AF_UNIX connections and
immutable Linux interpreter slots, with verified cleanup. Reading a Windows
worktree from WSL translates only that fixture's Git-directory lookup; it does
not override other repositories' Git context or fabricate revision metadata.

This is native source qualification, not an installed consumer or production
daemon-durability witness. Survival across caller-owned cleanup Jobs, ordinary
forwarder adoption, release and authorized installed acceptance remain separate.
No productive service or existing client is changed.
