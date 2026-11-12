# Session-generation implementation boundary

[agent-mcp](../README.md)

The current internal checkpoint, custody host and conduit are not yet an
activated production reattachment feature. The serve provider can advertise a
backend capability only from an admitted physical generation. Paired explicit
owner CLI arguments select custody; default callers and installers do not.

## Explicit Linux client exec

`exec-client --mcp-config <entries.json> --server <name> -- <native-copilot> ...`
is an opt-in originating-owner producer. The input contains a `mcpServers`
object describing already configured entries; repeat `--server` for the exact
names to override. It does not discover or activate other servers.

The launcher captures its own native PID and birth, then replaces itself with
the selected native Linux `copilot` executable. The client therefore retains
that identity instead of inheriting a shell or headed coordinator's identity.
No parent waiter, new daemon, session-file discovery, owner environment variable,
SDK extension or desktop observer is introduced.

Each selected entry must have a literal absolute `agent-mcp` command, or an
absolute Python command with `[-I] -m agent_mcp`, followed by `bridge` or
`forward`. A stdlib-only bootstrap snapshot runs at the actual MCP start,
with the entry's unchanged environment and inherited cwd. It queries that
entry's selected provider with probe-only `AGENT_MCP_NO_SELFPROVISION=1`;
the eventual server does not inherit that probe-only override.
The snapshot pins the originating Linux process through a pidfd and its native
birth before probing. Every physical or ordinary exec path rechecks that retained
observation, including unsupported-query fallback and failed-exec fallback.
This keeps origin admission independent of an installed MCP runtime; it adds no
observer thread or lifetime waiter.
Pair support alone does not qualify adoption. The new optional-entry
capability identifies a physical interpreter, prefix, receiver module, actual
probe cwd and explicit wrapper-owned environment delta, including payload-root
and bootstrap-trace fields. The bootstrap execs that immutable
receiver, rather than emitting new syntax through a mutable wrapper.

The receiver rechecks its actual code, probe cwd, selection, startup opt-outs, native
owner and cold custody context before any backend startup. An incompatible
cwd or unsupported sharing context executes the original ordinary command
with its original environment and cwd; it never forces the project into the
custody home. A changed selection, old provider, failed read-only query or
unsupported optional contract also retains ordinary behavior before effects.
No application operation is retried after startup or intake. The existing
explicit paired `bridge`/`forward` flags remain strict and never acquire this
optional fallback. A lost or unverifiable originating owner also refuses rather
than becoming an unowned ordinary call. The receiver pins that owner before any
context/fallback decision and rechecks the retained native observation immediately
before either ordinary exec or adopted startup. Interpolated and already
ownership-bound entries are not overridden.

Unreferenced bootstrap-trace fields remain in the physical upstream environment
but supporting peers exclude them from shared-context identity. The existing
`agent-mcp.execution-context/2` descriptor additively declares
`trace_bookkeeping: 1`; callers select that mode in their context request only
when the peer advertises it. Missing declarations and old requests retain the
original v2 hashing algorithm, including its limitations; unknown requested modes
refuse before configuration loading. There is no package-version inference.
Without the negotiated mode, each launch timestamp can reject an equivalent
second client against an older custodian. Explicit configuration references
always keep those values semantically significant.

Request-context negotiation does not upgrade the private checkpoint binding.
Existing bindings retain their original identity mode. When trace normalization
changes the legacy identity, the optional receiver first probes the actual
running keeper through its existing authenticated ping. An older or incompatible
keeper retains ordinary execution before backend startup or session preparation.
A capable keeper selects the strict required-mode path; an unreachable keeper
may use the existing cold-start owner, with actual backend and keeper admission
still required before attach. This read-only preflight starts no software and
creates no upstream. The attach handshake independently rechecks the live
keeper, requires the current backend's support and confirms the selected mode.
A replacement between preflight and attach can fail that strict path, but
cannot re-enable ordinary fallback after startup. Installed acceptance remains
an open #10486 adoption item, not an incoming-installation gate. The
configuration-consumption fixture does not establish installed reattachment.

This adds one synchronous local connect/ping per trace-sensitive optional entry
start, with a two-second protocol deadline and no steady-state message work.
The existing attach revalidates the serving peer after startup instead of
caching the earlier capability fact. The source host packet's
`test_optional_keeper_probe_cost_and_pre_effect_boundary` records cold, warm,
non-listening and slow-peer probe segments in JUnit properties. These isolated
transport measurements are not complete native caller-launch latency or Linux
AF_UNIX composition qualification.

The session-only `--additional-mcp-config` overlay is a sealed anonymous file,
read through the client's inherited Linux descriptor. Configuration and env
values stay in the entry environment, not the bootstrap request or a persistent
file. Only the provider's declared wrapper-owned path/interpreter/trace delta travels
to the physical receiver. Cwd,
environment and literal native arguments otherwise remain unchanged. The OS
reclaims the descriptor on client exit; no filesystem artifact needs a cleaner.
Failed exec closes it and reports failure, without starting a replacement client.

This first eligible caller requires Linux `memfd`/sealing support and a direct
native ELF client, not a script/forwarder. Existing additional MCP overlays and
literal `--` delimiters are refused rather than reinterpreted. Windows and
other unqualified launch paths refuse this new command before client/provider
effects; existing `bridge`/`forward` behavior is unchanged. The real-process
fixture proves identity retention and anonymous-overlay reading with a
synthetic native client. A separate offline native Copilot packet compares
ordinary and overlay launches in the same environment, checks actual MCP
consumption and owner identity against a controlled physical receiver, and
requires exact child settlement. That fixture is not installed custody
adoption, Windows pre-execution capture or whole-effort acceptance.

The Linux `optional` case in `tests/test_generation_handover.py` composes the
real optional bootstrap and selected physical receiver with the existing
trace-varying replacement, rollback and same-slot replacement fixture. Its
controlled native caller supplies its own PID/birth and retains the original
pipes. The test checks both caller and original conduit identities, shared
service counts, one upstream initialization, accepted-operation preservation
and EOF settlement. It does not substitute that controlled caller for actual
Copilot, qualify installed rollout or extend eligibility beyond Linux. Run it
through `python tools/run-plugin-tests.py agent-mcp --test-file
tests/test_generation_handover.py --allow-explicit-tiers --timeout 240
--plugin-timeout 260 --test-timeout 180`.

The separate Linux native packet
`tests/native/test_generation_handover_copilot.py` uses actual offline Copilot,
the same physical runtime constructor and the real optional receiver. A private
upstream sends protocol `ping` requests; actual client replies must traverse
replacement, rollback and same-slot replacement without replacing the native
client, original conduit, keeper or upstream. Initial discovery pagination
requires a client-generated follow-up cursor. Each later ping requires both
a physical-backend send witness and the upstream's matching client reply;
a deliberately withheld ping must produce no reply before release. It is then
released and acknowledged before closing the client's input. Fresh round trips and identity
checks also run after each predecessor exits. The copied receiver briefly holds
before qualification so the private fixture can prepare its backend from the
native client's actual semantic environment; production matching is unchanged.
Fixture-only witnesses record physical interpreter/module and native lifetimes.
This tests protocol traffic, not model-driven tool invocation or accepted mutations;
it requires `COPILOT_NATIVE_TEST_EXECUTABLE` on a disposable runner. The existing
path-gated `mcp-owner-exec` workflow supplies the pinned asset. A platform or asset
skip is not qualification, and this source packet does not enable installed
adoption or qualify Windows. Under the preserved runner containment, native
client shutdown terminates the upstream with SIGTERM; the fixture records that
exact exit boundary, single initialization and birth-matched cleanup. This is
not graceful upstream-EOF qualification; controlled-caller cases cover that
separately.

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

Export budgets the complete checkpoint receipt and subsequent preparation
frame, including the private binding and contract metadata. Wire sequences
and epochs are positive signed-64-bit integers, bounding envelope overhead.
An oversized checkpoint is refused before publication; an oversized handler
result becomes a bounded error receipt rather than disconnecting the old
attachment. Precommit refusal can therefore resume the unchanged owner.

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

Capable physical backends add `checkpoint-trace-bookkeeping/1` to their existing
`agent-mcp.connection-custody` descriptor. This is a provider expansion, not
automatic caller adoption. The original three required custody capabilities,
strict two-field binding, snapshot shape and hashing remain the legacy baseline.
Only a caller that has qualified this optional backend capability may select
`trace_bookkeeping: 1` in its private binding. The same explicit integer mode
must accompany its checkpoint; snapshots cannot silently switch identity modes.
Unknown modes, booleans and unrelated binding fields refuse before configuration
or upstream preparation. Missing modes retain legacy behavior.

The optional mode uses the existing execution-context normalization: only the
three unreferenced bootstrap-trace fields are excluded from identity; explicit
configuration references, credentials, other environment and cwd remain bound.
Fields remain in the physical process environment. Contained backend-epoch
controls cover changed diagnostics and return to an earlier diagnostic value
without initialization replay; they are not physical replacement or rollback
qualification. The separate physical packet varies launch diagnostics for
every actual keeper, backend and conduit, checks observed backend/upstream
values, and retains the original upstream through update, capable rollback
and same-slot backend-process replacement. Optional-launcher admission now
selects the requirement only after the separate running-keeper preflight above.
No caller emits this mode
to an unqualified peer, and no existing session's binding is rewritten.

The authenticated connector records optional binding support only when both the
selected backend's ping and attachment acknowledgment advertise it. A new
custodian selects mode 1 once, before its first preparation and upstream
admission, only through that qualified connector. Legacy connectors and peers
retain mode 0. Later capable attachments never upgrade an already selected
legacy binding. Conversely, rollback to a peer without the optional capability
refuses before checkpoint preparation for a mode-1 session; the existing
uncommitted-handover path resumes the original epoch without replay. It does
not downgrade the snapshot, create another upstream or weaken incoming
installation completion. Supported rollback between capable physical providers
and unsupported legacy rollback are distinct qualification cases.

The running custody host independently advertises
`checkpoint-trace-bookkeeping/1` through its existing authenticated ping. That
declaration means it can enforce a requested mode, not that its current backend
supports the mode. An internal conduit may request integer
`checkpoint_trace_bookkeeping: 1` only after that live declaration; an older
running keeper is refused before attach. The host then requires actual connector
support before checkpoint preparation or upstream startup, and confirms the
selected binding mode in its attach acknowledgment. Missing, boolean, unknown
or mismatched confirmations cannot qualify adoption. The acknowledgment still
follows activation; a lost acknowledgment is not permission to retry the
application through another path. Once attach is sent, transport or decoding
failure, invalid acknowledgment, or changed caller context is a terminal
`ConnectionError`, never the pre-effect `SharingUnavailable` fallback signal.

Absent requirements preserve the existing attach request and acknowledgment.
Legacy backends remain usable for legacy attachments; their missing optional
capability cannot reject unrelated baseline work. Conditional optional-launcher
selection does not replace a running keeper, enable default callers, upgrade
existing bindings or establish installed acceptance.

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
to the existing stdio byte pump. Its EOF half-close and final-output drain
are unchanged. Ordinary callers retain their existing parent watchdog;
explicit adopted callers use the separate owner boundary below.

Attach acknowledgment precedes early upstream notifications. Client EOF joins
an active handover before owning teardown; failed quiescence still closes
the exact retained transport while reporting uncertain accepted effects.
Losing only a backend link is not evidence that the external client died.

### Explicit originating-session lifetime

The internal `custody_launch.run_adopted` entrypoint requires the originating
session's explicit PID and native birth, captured before an interposed launcher
can exit. It pins that exact process before incoming or custody startup and
never discovers an owner by walking ancestors. The Windows pin requests only
limited query and synchronization rights; Linux retains a birth-checked pidfd.
Neither path signals the owner. Unsupported or unverifiable admission refuses
before startup, and observation errors after admission retain the session
rather than claiming owner death. This explicit path currently supports Windows
and Linux; native macOS ownership remains unqualified.
The first post-attach observation follows the same unknown-lifetime rule as
later probes; it does not undo a successful native pin and admitted connection.

The adopted conduit observes that pin instead of the ordinary ancestor
watchdog. A shim's exit alone therefore does not close its original stdio or
custody connection. Verified owner exit closes only this client's connection,
initiating the existing exact upstream/session settlement. Other attached
clients and the shared custody host retain their independent lifetimes.
`agent-mcp bridge` and `agent-mcp forward` accept the paired
`--session-owner-pid PID --session-owner-birth NATIVE_BIRTH` arguments to invoke
this path explicitly. The controlled originating launcher must supply the actual
client process identity captured at its native launch boundary, before a shim
can exit. These arguments are lifetime inputs, not an authentication grant.
They are not discovered from environment assertions, ancestors or another
plugin's session files. A missing pair, unverifiable owner or disabled serve
startup refuses rather than silently starting an ordinary replacement session.
The `forward` command retains its explicit `--socket` selection; `bridge` uses
the normal configured handle.
An older CLI can refuse these additive arguments before startup. A launcher must
qualify the selected consumer's support before supplying them, including after
rollback; passing the pair is not capability negotiation.

Without those arguments, both commands retain their existing behavior.
Controlled production launchers do not yet populate the pair automatically.
Source controls are not native caller-Job, installed-consumer or rollout proof.
`run_adopted` is a dedicated process entrypoint, not a returning library pump.
After the connection and native owner pin close, successful completion exits
the process without Python's buffered-stdio finalization: another process may
still hold the input writer after the originating owner dies.

### Selected-executable support discovery

`agent-mcp session-owner-capabilities` reports the
`agent-mcp.session-owner-handoff` version-1 capability from the executable
actually running the query. It validates the physical installed slot and current
selection, acquires and releases only its own native process pin, and rechecks
selection before returning its generation, root, prefix and interpreter.
An unmanaged runtime or unsupported platform reports `supported: false`.
Selection or native-probe failures return a nonzero exit with `supported: false`,
not a supported result inferred from payload or package-version equality.

`invocation_allowed` reflects the same multiplex/serve-startup opt-outs as the
explicit owner CLI path. It is not serving readiness, proof of another caller's
identity or permission to start anything. The query never ensures a backend,
attaches custody, loads bridge configuration or acquires credentials. The real
invocation must still pin its supplied owner and qualify incoming serving.

This is a provider expansion, not automatic launcher adoption. An older
executable may reject the query itself; callers must retain truthful unsupported
handling and bind any successful report to their actual selected command before
supplying mandatory ownership arguments. Missing or stale discovery is not
permission to replay a possibly accepted session.

### Hosted originating-owner qualification

The explicit `mcp-caller-lifetime` hosted Windows qualification packet uses the
existing native test Job owner and runner-renewed daemon leases. It selects one
functional fixture: actual custody startup, caller-Job closure with a bounded
child control, restrictive shared-outer refusal, shim exit, physical backend
replacement and owner death while the original input pipe remains open.
The independent owner launches its shim inside a real caller cleanup Job.
That shim uses canonical daemon admission with explicitly borrowed stdio
descriptors and the real `agent-mcp forward` CLI with paired owner arguments
to launch the conduit outside the caller-owned Jobs. Native
membership checks precede closure; a separate bounded child must die when the
shim's Job closes, while the same conduit and original owner remain alive.
The restrictive-outer negative covers that stdio launch as well as custody
startup. The packet does not infer conduit independence from keeper survival.
There is no desktop observer, visible control, Copilot binary, terminal host,
private credential or debugger prerequisite. Controller support must be
promoted before that packet can be submitted; preparing it does not qualify it.

Read-side EOF reaches custody independently of queued application admission.
Already-read responses, including those still awaiting publication, remain
possible until their admission settles. A retained
server request with no remaining possible client response, including one
arriving after EOF, terminates that session as uncertain instead of stranding
handover. Upstream validation, ledger overflow and unexpected reader termination
also fence execution, wake admission/migration waits and close the owned links.
They never authorize replay or affect another client's retained upstream.

Cancelling handover before quiescence may resume the still-active committed
epoch idempotently, with the same epoch and context checks. A failed or
unconfirmed resumption makes custody uncertain and wakes blocked admission;
it cannot leave a known session permanently fenced.
Resumption invalidates earlier quiesce tokens, including lock waiters, so a
stale operation cannot refence the backend after repeated cancellations.
Invalidation also wakes and settles those protocol operations promptly,
without cancelling their accepted mutations or retaining canceled waiters'
wire credit until the mutations finish. Request IDs retain the MCP
string-or-integer contract, including an empty string.

Completed dispatch releases backend execution credit before publishing its
settlement to custody. The accepted record and quiescence wait remain until
the settlement receipt is confirmed. Coalescing that receipt with the next
input therefore cannot expose custody capacity before the backend can admit it.

The private wire's receive window releases at receipt publication, not at a
later drain completion or task done callback. Published-but-draining tasks
remain owned for connection cleanup; a following uncredited frame is still
refused before dispatch. Receive admission and cleanup ownership are separate.

The watcher compares the last successfully adopted slot and endpoint
publication identity. Republishing a different backend from the same
immutable slot still migrates existing sessions. Admission verifies that
the observed publication remains unchanged across its handshake; a failed
candidate never becomes the successfully adopted identity.

Clean teardown keeps the backend attached while upstream input is closed and
final output drains, then detaches the backend and closes the transport.
The native half-close fixture requires the real child process's final EOF
notification to reach the original external stdout pipe.

The combined contained packet passes 224 tests with two existing
platform-specific skips and verified cleanup. It includes two clients sharing
one listener, an in-flight mutation plus server request, early notifications,
preparation failure before and after the backend handshake, backoff,
quiescence failure and synchronous conduit use across a handover.
Physical-generation metadata and upstream transports are simulated in these
composition tests. No default launcher or installer selects custody yet; native
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
Both clients also adopt a new process from that same rollback slot, then
continue after the superseded same-slot backend exits.

The packet covers a prestarted custodian and the internal launcher's actual
attributed startup. Two clients share one custody process; the peak registered
service count is three while two backend generations overlap. Authenticated
lifecycle control retires an unselected fixture backend; application admission
does not become a cleanup bypass. Exact fixture processes and upstreams are
settled through their contained lifecycle owner.

The combined native Windows and existing custody regression packet passes
265 tests with two existing platform skips and verified cleanup. Both native
keeper-start variants pass physical replacement and rollback.

Both native POSIX variants also pass, using real AF_UNIX connections and
immutable Linux interpreter slots, with verified cleanup. Reading a Windows
worktree from WSL translates only that fixture's Git-directory lookup; it does
not override other repositories' Git context or fabricate revision metadata.

This is native source qualification, not an installed consumer or production
daemon-durability witness. Survival across caller-owned cleanup Jobs, ordinary
forwarder adoption, release and authorized installed acceptance remain separate.
No productive service or existing client is changed.
