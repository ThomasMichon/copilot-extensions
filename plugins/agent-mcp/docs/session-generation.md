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
native ELF client, not a script/forwarder. Python builds that omit the optional
`os.memfd_create` or sealing constants use the process's native libc entrypoint
and stable Linux ABI values. The overlay still requires successful creation and
readback of all four seals; unavailable creation, kernel errors or unconfirmed
sealing refuse before client exec and close the unpublished descriptor. There
is no named-file or unsealed fallback. A configuration that produces no overlay
needs no anonymous-file allocation. Existing additional MCP overlays and
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
An ephemeral loopback Chat Completions provider also returns only the advertised
fixture tool. Actual Copilot must execute it once initially and after each
predecessor exits, then submit its real result to that provider and return the
matching completion over ACP. The private upstream ledger and physical-backend
witness must agree on each unique argument and retained upstream identity.
For each transition, an additional accepted tool call is held on the predecessor
while the incoming backend becomes ready. A copied-backend witness must confirm
that real handover reached quiescing with accepted work and settlers still
pending before the release gate opens. The call must remain pending without replay,
then complete on its original backend after release before fresh traffic is
required on the incoming backend. This does not simulate an unknown delivery
outcome.
The transport uses no API key or external inference and pre-approves only that
fixture tool; unexpected permission requests fail rather than gain authority.
This is source qualification, not installed acceptance or unknown-outcome
recovery; it requires `COPILOT_NATIVE_TEST_EXECUTABLE` on a disposable runner. The existing
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

The explicit native pair and optional physical receiver are advertised on
Windows and Linux. This provider-first declaration follows native bootstrap,
actual-client consumption and joined physical-handover source qualification;
it does not enable controlled production launcher capture or automatic caller
adoption. Unsupported platforms and failed physical/native admission still
report no support.

On Windows, a pre-effect ordinary fallback starts the original command with
explicitly inherited stdin, stdout and stderr handles, the restored original
environment and unchanged cwd, then returns that command's exit status. It does
not consume or relay application bytes. Windows does not use POSIX `exec` or
claim same-PID replacement. Owner loss or mismatch refuses before fallback;
after the admitted path may start work, failures remain terminal with no ordinary
replay. The private physical handover packet's `receiver` case refuses any
fallback and covers replacement, rollback, same-slot process replacement,
uninterrupted upstream identity and one initialization. Native fallback controls
separately cover small frames before EOF, argument/context fidelity, exit status
and rejected owner identities. Installed Windows optional entry and its launcher
remain unqualified.

### Windows native-owner section provider

The internal `owner_section` provider prepares a 4 KiB pagefile-backed section
before the existing caller creates its native client. The caller must put
`inherited_handle` in its explicit inherited-handle list, supply the actual
creation handle and PID to `publish`, then resume the still-suspended child.
The provider creates, resumes and supervises no process. Publication captures
birth and principal through a duplicated creation handle, not a numeric-PID
lookup or ancestor/session-file discovery. Publication is one-shot, including
an uncertain write failure; close the failed provider rather than retrying it.

The protected DACL admits only the current Windows user. The inherited handle
has section-read access only. Its retained native handle keeps the section
available after all producer handles close; closing the final handle removes
the section without filesystem cleanup. A 256-bit private binding determines
the local object name and must match the record. It prevents accidental
cross-wiring, not a hostile equally privileged Windows principal.

Version 1 uses an `AMO1` frame, a little-endian 32-bit JSON byte count and zero
padding. ASCII JSON has exactly `schema`, integer `version`, `binding`, native
`pid` and `birth`; the schema is `agent-mcp.windows-native-owner`. Unknown,
duplicate, missing, oversized, malformed or differently bound records refuse.
The section carries no credentials or durable ownership claim.
`acquire(binding)` opens read-only and returns `owner_snapshot.WindowsOrigin`,
which pins the native process and verifies birth, principal and liveness before
returning. It exposes `pid`, `birth`, `require_alive()` and idempotent `close()`;
its caller must close that observation. The complete `owner_snapshot.py`
source uses only the Python standard library and can be copied into an
anonymous startup snapshot without importing an installed MCP or shared
runtime. Missing sections, birth mismatch and owner death never become an
unowned ordinary call.

The packaged `owner_section.cs` companion supplies
`AgentMcp.NativeOwnerSection` for an existing in-process PowerShell/.NET creation
owner. Load it once with `Add-Type -Path`, prepare the object before creation,
include `InheritedHandle` in the native handle list, then call
`Publish(creationHandle, pid, birth)` while the actual child is suspended.
Only a true publication result permits the existing owner to resume. The
publisher verifies the supplied PID/birth and current principal against the
creation handle, writes once, and creates or waits for no process. Dispose its
handles after publication; the child's explicitly inherited read-only handle
retains the section. A headed coordinator's identity is not evidence of its
eventual native client's identity.
The existing native seed adapter's optional direct-client assertion is described
in [its architecture](../../agent-worktrees/docs/architecture.md). It refuses a
headed or substituted target before the publication callback, without changing
ordinary launch selection.

The focused `tests/test_owner_section.py` packet covers suspended native
creation-handle capture, protected DACL, read-only inheritance, delayed reading
after publisher-handle closure, collision, malformed/binding refusal, owner
death before and after acquisition, and final-handle cleanup. The focused
`tests/test_owner_snapshot.py` controls also run the C# publisher in the
existing suspended-creation seam, close all publisher handles and exit its
process before releasing a delayed, stdlib-only reader. They cover missing
inheritance, protected DACL, read-only handle rights, mismatched and dead
creation identities, one-shot publication and exact final-handle cleanup.
These are source provider controls, not actual-client or installed-launcher
qualification.
The internal `owner_exec.windows_overlay` builder embeds that stdlib snapshot
before the anonymous actual-start bootstrap and carries only its section
binding, not a parent-guessed PID/birth. The bootstrap opens and pins the
published native owner before querying any installed provider. Missing or dead
owners refuse without running the query or ordinary command. An eligible
provider supplies the physical interpreter used by the existing optional
receiver; the bootstrap does not reconstruct `sys.prefix` or import the selected
generation into its own interpreter.

Windows has no POSIX exec. This MCP-connection shim creates one headless
receiver child with the original inherited stdin/stdout/stderr, environment
and cwd, then returns its actual exit code. An unsupported provider retains
the ordinary command. Only failure to create the receiver permits fallback,
after another native-owner liveness check; wait errors and nonzero exits after
creation never replay the ordinary application. This is not a new native-client
supervisor, lifetime daemon or owner-discovery process.
`tests/test_owner_bootstrap_windows.py` qualifies this source boundary with
independent physical interpreter prefixes, actual-start selection, small
binary frames, EOF, context, native owner refusal and nonzero exits.
`tests/test_owner_entry.py` also checks failed creation versus accepted-child
wait failure without replay. These controlled packages do not qualify actual
Copilot consumption or an installed handover.

Windows automatic bootstrap adoption and production caller integration remain
disabled. The section requires no new process, network request,
filesystem scan or steady-state message work; installed launch/update latency
remains unqualified until caller adoption.

The opt-in native packet
`tests/native/test_owner_bootstrap_copilot_windows.py` supplies actual offline
Windows Copilot to that existing suspended-creation owner, publishes from its
real creation handle and exits the publisher before ACP initialization or
session creation. Actual delayed MCP startup must consume the anonymous
bootstrap, select its physical interpreter and report the same native client
PID/birth after a real upstream ping/client reply. An ordinary configuration
in the same workspace supplies the context and settlement baseline. The packet
observes the pinned publisher, client and physical receiver settle and the
final section disappear; the bounded runner owns all fixture descendants.
Late ACP session notifications are validated while draining through stream EOF.
Client exit must be natural, not test-forced termination.
The persisted launch plan uses a minimal Windows process-variable allowlist
with explicit private home, application-data and temporary directories. It
never copies the rest of the caller environment; a focused serialization
control plants synthetic inherited credentials and requires their absence.
Cleanup receives a process handle only after that same handle's native birth,
principal and liveness are verified. A mismatched candidate is closed without
becoming a cleanup target; a native negative control requires the mismatched
fixture process to remain alive until its own input reaches EOF.

The physical receiver's exit code and Python finalization/EOF witness must
match that ordinary baseline. A native client that abruptly settles its MCP
child on disconnect does not become a graceful upstream-EOF qualification.
The JUnit properties retain that distinction and bounded launch/round-trip
timings. This packet qualifies native configuration consumption and the
creation-bound handoff, not installed custody, Windows physical-generation
handover, productive caller enrollment or update latency. The existing optional
source workflow pins the public client asset and runs this packet only on its
Windows runner; required unit-only CI is unchanged.
The packet additionally composes the section publisher with the existing native
seed creation/wait owner. That owner remains alive while the actual native
client starts MCP after publisher handles close; the receipt must name the
client, not its waiting parent. Original ACP streams, fixture-provider context
and settlement match the ordinary baseline. Preparation, admission and execution
receipt responses are substituted, and this synthetic provider is not the
production optional receiver. The independent publisher-exit control remains
separate. These controls are not
installed-runtime acceptance and enroll no default or headed production caller.

The separate `tests/native/test_generation_handover_copilot.py` packet now
composes that Windows caller with the selected physical receiver and shared
custody. Replacement, rollback and same-slot replacement retain the original
native client, conduit, keeper and upstream, including accepted in-flight
tool calls, actual client ping replies and terminal unknown-delivery fencing
without replay. Its private runtime uses the real provider declaration, not a
test-only capability override. Windows independently runs that complete physical
packet after publisher exit and through the retained native creation/wait owner.
The latter still substitutes preparation, admission and execution receipt
responses, not the physical receiver or custody protocol. The wait owner must
remain alive through the physical transitions and exit naturally after its
actual client settles. The pinned Windows and Linux source lanes remain
separate from installed acceptance and productive caller enrollment.

### Opt-in retained Windows headed receiver

The explicit Python construction API
`agent_mcp.owner_headed_bundle.prepare_headed_command` composes the section
producer with the existing Driver headed creation owner. It accepts the
original native command, session, attempt directory, Host state file and cwd,
plus an existing absolute `configuration_path` and exact `names` selection.
Dash-prefixed server/session identifiers remain literal receiver values; the
original native argument vector is unchanged.
The optional `agent-mcp[headed]` dependency supplies the independent Driver core;
ordinary MCP installation does not require or enable it.

The constructor requires the selected core's additive
`headed_receivers: ["explicit-inprocess-v1"]` support before writing the
receiver. It retains a fixed, content-addressed executable containing that
core, the MCP producer and its native bindings. There is no serialized callback,
caller-supplied module or configuration in the archive. The copied executable
runs with a physical base Python under `-I -S`, without its original source
checkout or virtual environment. Its `--support-json` additively declares
`mcp_origin: ["windows-headed-native-v1"]`. An older undeclared core refuses;
package versions do not imply support.

At receiving time, the fixed MCP entrypoint reads the existing configuration
and constructs only the explicitly selected literal local entries. Every
selected entry must be eligible; partial selection refuses. The core retains
its argument, repository-policy, original child, Job, streams, wait and cleanup
owners. Publication uses the actual suspended native child's creation handle,
PID and birth, never the receiving coordinator. The explicit inherited list
contains the section's read handle and one synchronization-only namespace
reservation handle; neither grants configuration-write authority.
Authentication-wait execution, existing additional
MCP overlays, literal delimiters, scripts and forwarding client launchers are
not admitted by this opt-in path. Ordinary headed and MCP callers are unchanged.

Configuration crosses to native Copilot through one private, local, first-instance
named pipe, not argv, environment or a newly persisted plaintext overlay.
Its protected DACL admits the current user, remote clients are rejected, and
the connected reader must match the pinned original native creation identity
before any JSON bytes are sent. A different same-user process receives no
configuration. Before native creation, an occupied protected instance reserves
the name and a second instance supplies the actual configuration stream.
The native client explicitly inherits a synchronization-only reference to the
occupied reservation. The publisher closes all its reservation handles after
the actual suspended creation inherits that reference. Closing the separate
writer therefore supplies EOF without releasing the name; even failed or
cancelled transfer and parent cleanup cannot free it while the native owner
retains its reference. The OS releases that final reservation on native exit.
No configuration passes through the reservation, and no second listener worker
or new lifetime supervisor is introduced.

The shared native listener keeps its existing one-instance, first-instance
defaults; this explicit owner alone selects two bounded instances and an
additional instance of its already-protected name. Invalid/unlimited counts
and non-boolean selection refuse before native effects. Its focused controls
run with `python tools/run-plugin-tests.py zdd --test-file
tests/test_private_windows.py -k "explicit_dacl_and_native_listener_flags or
invalid_listener_reservation"`.

The existing overlapped-I/O owner supplies one 15-second
cancellable transfer deadline and a 1 MiB maximum. The one-use worker closes
its own connected pipe and duplicated process reference, retaining its channel
if either native release fails so the outer typed receipt can retry those exact
handles. No worker survives the transfer. A failed transfer immediately emits
one fixed, redacted diagnostic
without cancelling the original native wait or taking over Job cleanup.
Both resource finalizers are attempted. An already-active native cleanup or
cancellation exception remains the primary exception; its optional
`mcp_cleanup` receipt retains separately failed releases as an
`OwnedCleanupError` with exact resource references and a bounded retry operation.
Without an active error, failed finalization raises that receipt directly.
The pre-existing input
configuration retains its existing storage and permissions.

The explicit `tests/native/test_owner_headed_windows.py` packet checks the
actual copied receiver, native interactive Copilot, private mux and controlled
MCP provider. It requires the same native child PID/birth at physical MCP
startup, one initialization, preserved provider context, original Host
attachment/termination and exact fixture settlement. Separate native controls
check raw file consumption and a wrong-reader refusal with zero transferred
bytes. Two native lifetime controls keep the protected name unavailable for
replacement both before a delayed open and after successful consumption, then
require its release only after original-owner settlement, with every publisher
reservation handle already closed. These are source tests, not installed
reattachment, package replacement,
seed-prompt completion, default caller adoption or full Windows handover.
They require explicit `COPILOT_NATIVE_TEST_EXECUTABLE`,
`COPILOT_DRIVER_HEADED_MUX_E2E=1` and `PSMUX_TEST_BIN` selection through the
bounded runner; skips supply no native qualification. Required unit-only CI
is unchanged.

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
