# Shared native venue executions

Native Copilot and ACP use the same bridge/provider infrastructure but different
interaction surfaces. A native execution is not an ACP `SessionManager` row.
The bridge owns its durable execution record and presentation; the selected
CodeSpace or trusted-container provider owns preparation, claims, credential
forwarding, and managed ports.
The officially installed remote bridge owns the native PTY execution host and
the existing native live-session registry.

Optional [on-demand host resources](native-host-resources.md) let the same
registered native session request a locally allowlisted resource after startup
or presentation detach. Launching native mode does not require or start any
resource. Browser/service policy remains with the locally registered provider.

## Scope and proposed AHP convergence

Here, `native` means a **bridge-owned CLI/PTY execution** using the existing
Session Host, not the CLI's native AHP host. This path creates its own hosted
execution; it is not an adapter that adopts an existing AHP-owned session or
duplicates that host's authority. The execution ID/generation, `native.v1`
terminal replay, and reduced-fidelity represented result positions are not AHP
resource identities or AHP ordering/reconciliation.

CodeSpace and trusted-container adapters share `ssh_manager.native_channel`,
but that channel remains bridge-specific: it invokes the remote bridge's
`native-host` commands. Sharing SSH mechanics does not make it an AHP adapter or
a general terminal-projection contract.

**Proposed, pending maintainer confirmation:** retain this as a bounded interim
bridge-owned backend under the existing Session Host owner, with convergence
governed by the AHP effort's
[ownership/equivalence matrix and transition criteria](../../../efforts/active/agent-bridge-ahp-convergence/compatibility-baseline.md#proposed-bridge-owned-native-backend-reconciliation).
This proposal does not resolve the architecture review HOLD or authorize landing.

## Stable CLI and receipts

Use the resolved agent-bridge command:

```text
agent-bridge native capabilities --json
agent-bridge native start --target codespace:NAME --owner OWNER --cwd /workspaces/example-web --request-id REQUEST --command-file PATH --remote-command-file PREPARED_COMMAND_JSON --json
agent-bridge native start --container NAME --owner OWNER --cwd /workspace --request-id REQUEST --command-file PATH --remote-command-file PREPARED_COMMAND_JSON --json
agent-bridge native attach EXECUTION_ID --expected-generation GENERATION
agent-bridge native resume EXECUTION_ID --expected-generation GENERATION
agent-bridge native status EXECUTION_ID --json
agent-bridge native stop EXECUTION_ID --expected-generation GENERATION --json
agent-bridge native attach EXECUTION_ID --expected-generation GENERATION --observer
agent-bridge native attach EXECUTION_ID --expected-generation GENERATION --takeover
agent-bridge native observe EXECUTION_ID --expected-generation GENERATION --expected-session-id SESSION_ID --position POSITION --json
```

`--interactive-command-file` aliases `--command-file`. The file is a trusted,
nonblank UTF-8 shell program, with optional UTF-8 BOM and LF line endings.
CRLF line endings are refused with `invalid_command_file` before resource loading
or controller allocation; save the file with LF endings and retry. The command
is not rewritten, and standalone literal carriage returns are preserved.
`OWNER` is an existing worktree directory on the controlling host; `--cwd` is
an explicit absolute directory on the venue. Forwards are repeatable, strictly
loopback, decimal `LISTEN:CONNECT` ports; duplicates in one direction are rejected.
The native path retains required relay/repository preparation and suppresses
provider-controlled plugin payload delivery. Applicable remote plugins,
including the native bridge extension, use official installation.

New provider-qualified launches require the preparation result's pinned
`--remote-command-file`:

```json
{
  "schema": "copilot-extensions.remote-command",
  "version": 1,
  "argv": ["/absolute/selected-payload/bin/agent-bridge"],
  "receipt": {
    "path": "/absolute/selected-installation/current-payload.json",
    "sha256": "<SHA-256 of the exact remote receipt bytes>"
  }
}
```

Preparation owns verifying that the selected command belongs to that installation.
Before allocating a venue or preparing a new launch, require the running
controller's `native capabilities --json` response to advertise
`capabilities.remoteCommand` with `schema: "copilot-extensions.remote-command"`,
`version: 1`, and `receiptHash: "sha256"`. The remote native-host capabilities
command advertises the same descriptor support. The legacy control capability
name, terminal capabilities, and installed CLI help do not establish that an
older running controller supports pinned remote commands. This additive
preflight capability does not change compatibility for existing saved records.
The descriptor is snapshotted in durable controller state and reused for
capabilities, service/readiness, launch, observation, messaging, and retirement.
Every invocation checks the same receipt bytes before executing the absolute argv;
an absent or changed receipt fails closed, never substituting a PATH command.
Legacy `--codespace` requests and saved records without a descriptor retain the
legacy command boundary; they do not acquire attributable-installation guarantees.

The container adapter accepts only a running, discovered, trusted/trusted fleet
member and pins its Docker incarnation. Its existing configured token bootstrap
and SSH credential relay remain required; it does not invent a CodeSpace identity,
project a local checkout, or install host plugins. To prepare official remote
plugins, run `agent-containers remote-exec NAME --owner OWNER --command-file FILE --stdin
--require-relay --no-plugin-staging --timeout 600`: the command file is UTF-8,
stdin is forwarded unchanged (up to 1 MiB), and stdout/stderr and exit code are
preserved. Container session files stay in the container; no host-workspace
transcript projection is claimed.
Preparation requires the launch's owner: an unleased member or an exact matching
advisory lease owner is accepted, while a foreign lease is refused before any
preparation. This check and the in-flight session admission share the provider's
lease lock; new borrowing cannot race into preparation. No advisory lease is
acquired, renewed, or released by remote preparation.

HTTP protocol 15 exposes `/api/v1/native-executions`. Receipts use schema
`copilot-extensions.native-execution`, version 1, with `executionId`, `generation`,
`mode: native`, `target`, `provider`, `owner`, `state`, `sessionId`, `represented`, `ready`,
`phase`, `exitCode`, `ports`, and `recovery`. No attach nonce or bearer is public.
`sessionId` is the real CLI registration, not a fabricated ACP identity.
Receipts retain `codespace` for CodeSpaces, use `container` for containers, and
include the non-secret `remoteCommand` descriptor when supplied.

Start is idempotent by `requestId`: reusing a key with a different specification
fails. A receipt can be `starting` while infrastructure is prepared and
`unrepresented` while the CLI has not proved its registration. **Ready requires
the real native registration and the owned transport.** Capabilities or a live
PTY alone do not establish integrated readiness.

If the hosting bridge's live-session registry lookup fails (HTTP/authentication,
connection, or response-decoding/protocol failure), status remains unrepresented
and unready and reports the scalar `error: "registration_lookup_failed"`.
Exception messages, response bodies, credentials and endpoint URLs are not
included. An ordinary missing registration (including HTTP 404), a late
registration, or a stale/non-live row does not produce this diagnostic. A later
successful lookup clears it; readiness still requires the unchanged freshness
check. Unexpected implementation faults are not silently treated as missing
registrations.

Attach/resume reconnect only to the recorded execution. They do not invoke
another launch. `Ctrl+]` detaches the presentation without stopping the venue
process. Transport loss retries the same execution/generation; uncertain input
is not replayed. The terminal has a bounded replay tail and supports resize.
The native CLI retains its own permissions and interactive prompts.

Terminal attachments have one explicit writer and independent read-only
observers. Ordinary second-writer attachment is refused; only `--takeover`
revokes the previous writer. Busy/revoked/read-only failures use nonretryable
WebSocket codes 4409/4410/4403, so auto-reconnect cannot seize ownership back.
The authenticated terminal endpoint accepts `role=observer|writer`,
`takeover=true|false`, and `after=SEQUENCE`; it emits an identity-bound `attached`
message and explicit `gap` messages when the 1 MiB replay tail no longer covers a
cursor. Observer input, resize, and lifecycle control are rejected by the Session
Host itself. ACP retains its existing single-frontend protocol. New native writer
requests fail closed against older Session Hosts rather than silently displacing
their frontend.

Windows console output incrementally decodes UTF-8 across terminal frames and
writes only complete codepoints. This avoids blocking `_WindowsConsoleIO.flush`
on a frame ending partway through a character. ANSI sequences and carriage
returns remain intact; redirected output and POSIX terminals retain raw bytes.
Acknowledgement follows consumption of a frame, including at most three
pending UTF-8 bytes held by the decoder. Invalid bytes are displayed as Unicode
replacement characters. On exit, detach, cancellation or transport loss, the
connection closes and any truncated codepoint is finalized with replacement;
a reconnect starts a new decoder for its independently replayed tail. This
changes only presentation output, not input, remote execution or service state.

The activated terminal is attachable during `starting` / `registration`, before
a live session is represented. This lets the operator answer folder-trust and
extension-consent prompts that may otherwise block registration. The terminal
still verifies the authenticated host identity; answering a prompt or attaching
does not itself establish readiness. No blanket permission approval is applied.

Before the first successful terminal attachment, the presentation allows a fixed
**1800-second preparation window**, matching the provider's readiness budget.
After an attachment has succeeded, transport loss gets **120 seconds from the
last disconnect** to reconnect. A recovery status never resets that window back
to preparation. Healthy attached terminals are not limited by either deadline;
TCP/WebSocket handshakes and status waits remain bounded. Expiry only ends the
presentation with an explicit error: it does not restart the owner, relaunch
Copilot, clear ownership, or change execution/generation. `Ctrl+]` and caller
cancellation remain available while waiting. Stopped/rejected or mismatched
identities are handled explicitly rather than retried as preparation.

During preparation, `phase` can report
`preparing/<substage>/<started|reached|failed>`. Substages are a fixed allowlist:
local configuration, owner admission, SSH, target authentication, target setup,
worktree, and native-host readiness. These identity-bound checkpoints reuse the
provider's tracker and carry no raw stderr, commands, paths, credentials, or
arbitrary detail. Older providers remain compatible with generic preparation
status. Late progress cannot overwrite launch, ready, stopping, or stopped state.

Account pinning changes only GitHub credential variables. Unrelated inherited
environment entries, including empty counted Git configuration values on
Windows, remain intact for owner-origin resolution and the shared fence.

## Ownership and restart

The native host reuses Session Host framing, connect nonces, process survival,
and authority records, with a PTY adapter instead of ACP pipes. The child starts
behind an authenticated activation gate: managed forwarding and the host's
identity are proved before the caller's command executes.

Provider interaction reservations are durable and do not expire merely because
a frontend or registration disappeared. Native and ACP mode admission rejects
the other mode, including same-owner fallback. The remote authority catalog is
also checked and admission/publication serialized before a child starts.
Uncertain or corrupt authority fails closed.

An ACP create or resume targeting a native-owned venue returns HTTP **409**
with `detail.code: native_incumbent`. The refusal occurs before ACP session
allocation or spawning. Session/worktree resume and handoff adapters preserve
that classification instead of reporting an internal error or trying a fresh
ACP session.

The current serving bridge generation owns provider transports. A superseded
controller detaches its infrastructure without terminating the remote native
execution; its successor reconnects from durable identity. The native registry
extension refreshes its local endpoint and bearer and retries missing
registration. Neither path converts loss into an ACP dispatch.

The shared provider renews owned claims/relay tenants, monitors relay serving,
re-establishes ports, and retains the command channel for settlement. Its native
transport is a bridge-owned infrastructure process, not the owner of the remote
Copilot lifetime.

## Representation and control

The remote official bridge extension associates its real session with the
execution generation. The hosting runtime checks that the registering process
belongs to the original native child before accepting the association. The
existing `/api/v1/live-sessions` event and inbox contracts remain authoritative.
Their reduced-fidelity representation does not grant remote permission approval.

`agent-bridge send native:EXECUTION_ID ...` and known native targets route through
the native record to that verified live session. Unrepresented/unreachable
native ownership is an error, never a reason to fall through to ACP. Generation,
session freshness, and message idempotency are checked before delivery.

`native observe` reuses the existing represented result snapshot, including its
opaque `position`, process-lifetime retention, and coverage/gap reporting. Omit
`--position` for an initial snapshot. The result is bound to the execution,
generation, and real session; any number of readers observe the same agent.
It is reduced-fidelity structured observation, not parsed terminal output.
Capabilities declare prompt admission and retirement but no typed interruption,
hidden reasoning, or permission-response API.

Requested reply waits are finite values in `(0, 300]` seconds. SSH allows the
requested wait plus 30 seconds, the provider channel plus 60, and the public
HTTP caller plus 90. An observation-transport timeout is reported separately
from an ordinary admitted message's reply timeout; it never proves non-delivery.
Inspect the result or reuse the same message ID rather than submitting a new one.

## Retirement and recovery

Stop requires the recorded generation. The remote authority pins host/child
incarnations and authenticates retirement against the original host; stale
identity cannot kill an unrelated process. The execution reservation is released
only after retirement proof. Retirement receipts and tombstones make lost
acknowledgements and repeated cleanup safe.

The living Linux host observes leader exit without reaping the PID that anchors
its original process group. This preserves exact group authority until explicit
retirement even when background descendants survive the leader. After host loss,
saved PGIDs never authorize killing an unrelated or uncertain process group.

The CodeSpace provider invokes its existing agent-logger-backed transcript recovery and
preserves the CodeSpace. A recovery failure is reported explicitly and leaves
venue files intact; terminal exit is not permission to delete the venue.
Unconfirmed retirement retains ownership and remains a blocker.

Transcript recovery is a separate, optional capability: the controlling
provider needs an installed and enabled **agent-logger from the same
marketplace**, its working `session-sync push` runtime, and configured storage.
Native startup/registration and verified retirement do not require enabling
logger. Without that prerequisite, a stop can correctly return `state: stopped`
and `recovery.ok: false`, with an explicit unavailable reason. This means the
process was retired, **not** that its transcript was rescued; even a nonzero
`session_count` does not prove successful storage. Preserve the venue and its
session files. Do not silently enable logger or treat that result as permission
to delete the CodeSpace.

Admission failures before infrastructure acquisition retain proof that no
resources were started. Generation-bound stop can retire that unlaunched
reservation; an interrupted or unconfirmed cleanup cannot overwrite uncertainty
with this proof. A launch specification remains immutable: changing rejected
forwarding requires retiring its generation before using a new request.

Retirement reconnects an authenticated, identity-bound control transport without
recreating application listeners or activating the native child. An occupied
development/CDP listener therefore cannot strand a paused or disconnected native
execution. Status reads preserve retirement intent rather than resuming or
activating it. Retirement still requires the remote host's verified proof; a
successful transport connection or empty reservation is not a retirement receipt.

The host backend currently requires Linux process identity. Frontend commands
support Windows and POSIX terminals. No WSL, private helper runtime, ACP
surrogate session, or copied host plugin/profile is introduced.
