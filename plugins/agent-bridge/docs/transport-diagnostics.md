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

## Observed ordinary-session startup

`send` can opt into startup observation for an **existing ordinary hosted
session**:

```text
agent-bridge --json send <session> --prompt-file - --no-wait --startup-communication-timeout 30 --startup-process-timeout 300
```

This requires daemon HTTP protocol 46. An older daemon or a missing/represented
target refuses before submission; observation never falls back to creating an
agent. The selected boundary defaults are 30 seconds of renewable contact and
five minutes of independent startup grace, with readiness expected within one
minute end-to-end. Explicit flags may select smaller test or operation budgets.
The five-minute budget does not apply to the turn after readiness.

Read-only health and target-resolution requests are supervised by the remaining
startup clocks, including response-body waits. A response arriving after expiry
cannot renew contact or permit a later resume/prompt mutation. Dispatch also
retains an existing session on availability/protocol exit 69, including the
non-JSON refusal renderer; that refusal is not evidence of session absence.

Progress is flushed to stderr as `@@agent-startup ` plus one JSON object per
line; stdout remains one final `send --json` result. Every target event,
including unfamiliar kinds and target keepalives, counts as contact. Progress
retains phase/event names without forwarding opaque event data. Local
reconnect timers and cached protocol metadata do not renew target contact.
The existing session SSE channel retains its event cursor and continuity
across observation-only reconnects; the resume POST is sent only once.
The controlled subscription uses a distinct observer audience and the cursor
endpoint's authoritative head/continuity snapshot. An empty log uses the
existing initial controlled-stream contract until its first epoch is known;
it is not an invalid transient resume. Subscription bookkeeping does not
acknowledge or advance the actual work caller's delivery cursor.

The service reports current lifecycle-lock, preparation, Session Host and ACP
phases, including honest waiting updates. Local and SSH Worktrees capture
forwards incremental preparation activity instead of withholding it until
the final JSON. A failed progress writer is logged without changing lifecycle
ownership.

Communication or startup-lifetime expiry exits 70 with `outcome: unknown`,
`retryable: false`, the expired clock, phase and elapsed lifetime. Losing this
observer does not cancel the service's original startup operation or prove it
stopped. Inspect that session and its existing receipt/history; do not infer
permission to resend or replace it. Explicit cancellation remains with the
operation owner.

A resume acknowledgement must identify the original session and confirm `idle`
or `running` before startup can become ready. An empty/204 response, a missing
status, another session's response or a non-ready status exits 70 with
`reason: resume_unknown`; no follow-up prompt is admitted.
An accepted native/ACP reference is first resolved to the fetched session's
canonical Bridge ID; cursor lookup, resume and acknowledgement validation all
use that same identity.

After readiness, prompt admission uses a separate short response wait and
`no_resume: true`: a race back to STOPPED cannot silently begin another
startup. A lost admission acknowledgement also exits 70 with
`reason: admission_unknown`, not a retryable refusal or a second submission.
This includes truncated HTTP bodies, invalid UTF-8/JSON, empty/unknown lifecycle statuses and incomplete
acknowledgement shapes after acceptance. An observation/decoding failure that
did not exhaust a clock reports `clock: null`, rather than claiming a timer
expired. Explicit HTTP refusals retain their existing classification.
Successful admission retains the existing turn-streaming behavior, including
long-running turns.
