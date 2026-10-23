# Bridge transport failures: classify the boundary, not the peer

This is the read-only classification procedure used by
[agent-bridge-troubleshooting](../skills/agent-bridge-troubleshooting/SKILL.md).
Unexpected failure is evidence, not permission to restart services, replace
sessions, update runtimes or resend work.

## What each observation establishes

Record the selected route, observation time, running runtime version and failed
boundary. Keep the intended target and existing session identity unchanged.

| Boundary | Positive evidence | What remains unproved |
|----------|-------------------|-----------------------|
| Local control plane | A response from the discovered local Bridge endpoint | Remote connectivity, remote service health and delivery |
| SSH route | TCP connection or an SSH server banner | Host authentication, command execution and Bridge health |
| SSH execution | Strict host verification, authentication and a harmless remote command completing | Carrier framing and hosting Bridge readiness |
| Carrier | A valid negotiated framed HELLO on the selected channel | Hosting Bridge authentication, session existence and operation acceptance |
| Hosting Bridge | An authenticated hosting-service response to the relevant read operation | Acceptance or completion of a different operation |
| Operation | The operation's authoritative acknowledgement or receipt | Completion beyond what that acknowledgement actually promises |

`carrier_unavailable` names a failed carrier path, not a remote daemon-health
verdict. EOF before HELLO says the framed peer was not established; a timeout
says that boundary was not observed within its budget. Neither establishes an
absent session or a stopped remote turn. An empty local registry is not an
authoritative remote lookup. Keep unavailable observations **unknown**, not
false, empty, dead or completed. Success in an independent probe does not prove
the original serving daemon used the same executable, environment or route.

Preserve literal host-key, authentication, permission and version errors when
they are actually observed. Do not replace them with an optimistic "transient"
classification. An explicit hosting-service failure is different from being
unable to observe that service at all.

## Read-only sequence

1. Preserve the original command/result and any acceptance identifier. Inspect
   already-recorded local health, carrier diagnostics and connection events.
   Do not send a second prompt as a connectivity test.
2. Name the last positively observed boundary and the first failed or unknown
   one. Carrier stages such as `hello-read` describe the local negotiation
   boundary; they do not establish which SSH, remote bootstrap or service call
   caused it to fail.
3. When diagnosis is authorized, use bounded, owned probes that preserve the
   configured alias, host-key verification, identity and headless launch shape.
   Test only the missing boundary. A harmless SSH command or HELLO-only
   negotiation must not launch a replacement agent, provision a runtime or
   invoke a mutating Bridge operation.
4. On Windows, account for observer interference before blaming the network.
   If installed, invoke `agent-ssh:agent-ssh` and read its Windows stdio
   diagnostic reference. No sibling installation is required: with the existing
   OpenSSH executable, a bounded configuration-only `ssh -G <configured-alias>`
   probe can isolate local startup without connecting. Check child liveness
   separately from pipe EOF, drain piped streams concurrently, and compare
   discarded stderr with an SSH-opened `-E` log only in verified private,
   task-owned temporary storage. Preserve the caller's no-window launch
   primitive. A diagnostic stderr pipe or inherited file handle can itself
   stall SSH; do not assume production has the same handle setup.
5. Report the original result separately from probe results, including what is
   still unknown. Reap only owned probes; preserve shared services and sessions.

Reconnectability concerns restoring a channel. It does not establish whether
an operation was sent, accepted, executed or acknowledged. Before retrying a
mutation, use its existing receipt/idempotency and reconciliation contract.
Lost acknowledgement can mean an uncertain outcome, not failed execution.
Read/event reconnects retain their existing cursor and replay rules; no new
retry policy is introduced by this procedure.

## Interpreting available diagnostics

The shared [ssh-manager](../../../libs/ssh-manager/README.md) implementation
records failed startup `stage`, `error_type`, pre-cleanup `exit_code` (or
unknown), and an allowlisted stderr `reason` in `startup_failure`; it clears
that snapshot on successful negotiation. Older installed runtimes may expose
only the exception name. Inspect the running version rather than treating
source changes as proof of deployment.

A reason is a hint, not a proven cause. Missing stderr evidence is not evidence
of no error. A cleanup-induced exit is not the child's original exit status.
Keep raw stderr, command arguments, environment values, credentials and message
payloads out of public error details. Use bounded classifications and preserve
more detailed evidence only through the owning tool's private diagnostic path.

The [connection pipeline](../README.md#connection-pipeline--diagnostics)
describes session-launch checkpoints. Those checkpoints and aggregate carrier
health do not substitute for an acknowledgement of a particular operation.

## Gateway direction is not a recovery command

The existing [Gateway vision](../../../visions/machine-fleet/gateway/README.md)
and [Bridge federation intent](../../../visions/plugins/agent-bridge/README.md#federate-over-mesh-or-gateway)
provide the direction for an explicitly adopted, authenticated outbound service
channel rather than running ordinary Bridge messaging through SSH subprocess
stdio. Gateway owns connectivity and admitted routing; Bridge retains session,
execution, authorization and acceptance ownership.

The [Gateway runtime documentation](../../../fleet-manager/docs/gateway-runtime.md)
owns the actual supported surface. Its existence does not establish a
general-purpose Bridge broker, a configured route or migration authority.
Do not invent a fallback to Gateway when SSH fails. A future Bridge transport
must preserve protocol negotiation, bounded buffering, cancellation, event
continuity and uncertain-outcome/idempotency semantics across reconnects.
SSH remains an independently supported transport and may serve bootstrap or
recovery; optional Gateway adoption must not become a hidden standalone
dependency or a second session ledger or supervisor.

## Caller discovery before startup

Before submitting or resuming a session, the CLI may query Worktrees for its
caller identity. This optional lookup retains an eight-second command deadline
and owns the finite subprocess tree, including Windows wrapper descendants that
can otherwise keep captured pipes open after the wrapper times out.

`Worktree lookup failed (<key>): <exception-type>` or `exit <code>` reports
bounded lookup failure without dumping child output. The existing unavailable
caller fallback is unchanged. This message is not a failed ACP launch or proof
that a prompt was accepted. Classify the later operation independently.
Bounding this metadata lookup does not extend a calling application's startup
deadline or implement progress-based waiting. The eight-second value is an
unchanged legacy lookup budget, not the shared communication/process timeout
policy. Startup callers still need independent clocks and incremental nested
progress under the
[shared contract](../../../docs/patterns/progress-and-timeouts.md); repairing
pipe cleanup alone does not qualify that adoption.
