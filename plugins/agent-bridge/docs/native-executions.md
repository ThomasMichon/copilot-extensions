# Shared native CodeSpace executions

Native Copilot and ACP use the same bridge/provider infrastructure but different
interaction surfaces. A native execution is not an ACP `SessionManager` row.
The bridge owns its durable execution record and presentation; the CodeSpace
provider owns preparation, claims, credential forwarding, and managed ports.
The officially installed remote bridge owns the native PTY execution host and
the existing native live-session registry.

Optional [on-demand host resources](native-host-resources.md) let the same
registered native session request a locally allowlisted resource after startup
or presentation detach. Launching native mode does not require or start any
resource. Browser/service policy remains with the locally registered provider.

## Stable CLI and receipts

Use the resolved agent-bridge command:

```text
agent-bridge native capabilities --json
agent-bridge native start --codespace NAME --owner OWNER --cwd /workspaces/example-web --request-id REQUEST --command-file PATH --no-plugin-staging --require-relay --local-forward 4321:4321 --reverse-forward 9000:9001 --json
agent-bridge native attach EXECUTION_ID --expected-generation GENERATION
agent-bridge native resume EXECUTION_ID --expected-generation GENERATION
agent-bridge native status EXECUTION_ID --json
agent-bridge native stop EXECUTION_ID --expected-generation GENERATION --json
```

`--interactive-command-file` aliases `--command-file`. The file is a trusted,
nonblank UTF-8 shell program, with optional UTF-8 BOM and LF line endings.
`OWNER` is an existing worktree directory on the controlling host; `--cwd` is
an explicit absolute directory on the venue. Forwards are repeatable, strictly
loopback, decimal `LISTEN:CONNECT` ports; duplicates in one direction are rejected.
The native path retains required relay/repository preparation and suppresses
provider-controlled plugin payload delivery. Applicable remote plugins,
including the native bridge extension, use official installation.

HTTP protocol 15 exposes `/api/v1/native-executions`. Receipts use schema
`copilot-extensions.native-execution`, version 1, with `executionId`, `generation`,
`mode: native`, `codespace`, `owner`, `state`, `sessionId`, `represented`, `ready`,
`phase`, `exitCode`, `ports`, and `recovery`. No attach nonce or bearer is public.
`sessionId` is the real CLI registration, not a fabricated ACP identity.

Start is idempotent by `requestId`: reusing a key with a different specification
fails. A receipt can be `starting` while infrastructure is prepared and
`unrepresented` while the CLI has not proved its registration. **Ready requires
the real native registration and the owned transport.** Capabilities or a live
PTY alone do not establish integrated readiness.

Attach/resume reconnect only to the recorded execution. They do not invoke
another launch. `Ctrl+]` detaches the presentation without stopping the venue
process. Transport loss retries the same execution/generation; uncertain input
is not replayed. The terminal has a bounded replay tail and supports resize.
The native CLI retains its own permissions and interactive prompts.

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

An ACP create or resume targeting a native-owned CodeSpace returns HTTP **409**
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

## Retirement and recovery

Stop requires the recorded generation. The remote authority pins host/child
incarnations and authenticates retirement against the original host; stale
identity cannot kill an unrelated process. The execution reservation is released
only after retirement proof. Retirement receipts and tombstones make lost
acknowledgements and repeated cleanup safe.

The provider invokes its existing agent-logger-backed transcript recovery and
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
These are internal bridge/provider changes; the public native verbs are unchanged.

The host backend currently requires Linux process identity. Frontend commands
support Windows and POSIX terminals. No WSL, private helper runtime, ACP
surrogate session, or copied host plugin/profile is introduced.
