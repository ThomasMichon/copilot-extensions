# Startup progress and independent timeout clocks

Parent: [Dispatch fleet reliability and recovery](README.md), Phase 3.
Coordination: #8051. Shared contract: #8018 / #8022 and
[Progress and timeout contract](../../../docs/patterns/progress-and-timeouts.md).

This is a proposed implementation slice, not runtime adoption, deployment
permission or a replacement for the existing service/tool-wide contract.

## Request

Operator request, verbatim:

> So long as bridge can stream loading progress information to dispatch as it starts, we should be able to keep resetting the timeout on every message. An 8-minute pause of silence would be odd

> This is something we need to clarify service- and tool-wide: we should have communication timeouts, and process timeouts. Communication timeouts should be short, but reset every time we receive an update from our target. Process timeouts should be very long, allowing grade for disruptions. As we start agent-bridge, we negotiate worktree state, file locks, ssh, session-host startup, Git pull, mux creation, plugin sync, instruction sync, etc. But each step can emit a progress update that should reset our timeout. We don't just want "starting bridge...started [54s]". agent-dispatch can handle the events it cares about, but sitll treat the rest as signs of life.

The eight-minute example is not a selected cutoff. No universal numeric
default is prescribed. Short communication and very long process budgets are
separate policy choices; existing generic timeout values cannot silently
serve both purposes.

### Selected startup policy

Subsequent operator form response, verbatim:

> communication_seconds=30, process_minutes=5

Subsequent operator clarification, verbatim:

> There is no way that any process should take longer to start than 1 minute end-to-end, so I'm allowing 5

For this startup boundary, expect completion within one minute and allow
five minutes of independent end-to-end startup grace. Renew 30-second
communication silence on target updates, never the five-minute total.
The rejected two-hour recommendation is not a startup default. This is
not a five-minute execution limit on a long-running turn after readiness.
It does not select one universal lifetime for all service/tool operations.

## Scope and retained ownership

Qualify one actual composed startup path first: an ordinary Dispatch continuation
through the Bridge CLI/service and the nested preparation it invokes. Carry
the same rules across that path's local and supported remote boundaries.
The shared contract remains service/tool-wide; other consumers must not claim
adoption solely because this slice passes.

Preserve #6467 master reconciliation/watchdogs; #4028/#6934/#7337 lifecycle and
native-head acceptance; #3194's full hydration/reconnect/cancellation contract;
#4171's ongoing streaming/results/active-turn steering; #6812 updater progress;
and each transport, plugin-readiness, process-owner and installer authority.
#8015/#8046 fixes caller-discovery pipe cleanup separately. Do not reopen
completed work, supersede another owner's active PR or turn silence into
permission to replay an uncertain prompt.

## Required behavior

- Start a monotonic communication clock when waiting for the target, including
  before its first update. Renew from local receipt of every target update,
  independently of whether the consumer recognizes its event meaning.
- Count additive/unknown events and target-emitted keepalives. A local polling
  timer is not contact. Preserve the origin and operation/phase context when
  forwarding so an intermediate wait is not misreported as child progress.
- Keep a separately named long process-lifetime budget, never renewed by
  progress or reconnection. Compose nested budgets instead of allowing a short
  outer fixed process deadline to preempt a healthy, communicating operation.
- Stream progress while work happens: caller/state discovery, lock waiting,
  SSH, Session Host/ACP setup, Git/worktree preparation, mux, plugins and
  instructions. A target supervising a quiet phase can report that honest
  waiting state without inventing child completion.
- Keep progress separate from final JSON results, accepted receipts and error
  semantics. Preserve diagnostic redaction, bounded buffering and prompt
  privacy. Final-only elapsed summaries do not satisfy this requirement.
- Distinguish communication silence, independent process expiry, process exit,
  acknowledged refusal, accepted work and unobserved/uncertain acceptance.
  Report the expired clock, last contact/phase and elapsed process lifetime.
- Communication expiry can end observation but cannot kill a shared service,
  recreate a worker or resend a mutation. Retain exact operation/task/session/
  workspace identity and inspect/reconnect through existing receipt and event
  contracts. Process expiry follows the owning cancellation contract; bounded
  teardown includes captured pipes and owned descendants.

## Proposed implementation sequence

The following sequencing and mechanism preferences are **agent-recommended**;
they do not add requirements or authorize an alternate transport.

1. Trace the existing command, HTTP/event and nested subprocess boundaries.
   Record where target progress is generated, buffered or lost and which
   timeout currently acts as a total budget. Reuse existing session events,
   progress writers, identity and process ownership rather than a second launch
   path, operation ledger, scheduler or recovery loop.
2. Introduce explicit communication/process policy and observation at the
   existing seams, with compatibility behavior stated for older peers.
   Forward progress promptly, preserve final framing and prove unknown-event
   liveness before changing Dispatch's actual wait.
3. Qualify the full ordinary continuation consumer and nested forwarding
   under faults. Preserve intentional unkeyed follow-ups and existing keyed
   admission/receipt behavior; attaching observation must not submit twice.
4. Land source and documentation through normal review/CI/changefiles.
   Verify containing release separately. Any authorized deployment and retained
   real-task reconciliation remain explicit adoption gates, not consequences
   of a source test passing.

## Validation matrix

Every row applies to the actual composed consumer, using synthetic targets
where safe; clock-helper unit tests are supporting evidence only.

| Case | Required observation |
|------|----------------------|
| Healthy slow startup | Total duration exceeds the communication window while updates keep it observable; no short outer process deadline preempts it |
| Startup latency and grace | Measure the real composed path against the one-minute expected latency and separate five-minute ceiling; readiness ends the startup clock, not the turn |
| Recognized and unknown events | Both renew contact at receipt; unknown meaning does not fail or drop liveness |
| Target keepalive versus local timer | Received target keepalive renews contact; an observer's own timer does not |
| Silence before first update | Communication clock expires from initial wait, with no accepted-work claim |
| Silence between phases | Last contact/phase and process elapsed time are retained; no replacement or mutation replay |
| Independent process expiry | Continuing updates do not extend the long total budget; owner-specific cancellation/unknown semantics remain intact |
| Nested forwarding | Lock/state/SSH/Session Host/Git/mux/plugin/instruction updates arrive before nested completion and do not corrupt final JSON |
| Disconnect/reconnect | Identity and receipt/cursor continuity survive; a channel loss is not acknowledged execution failure |
| Lost acceptance response | Existing historical receipt/inspection rules reconcile the original operation; no automatic fresh submission |
| Explicit cancellation and teardown | Only authorized owned work is cancelled; descendant pipe draining is bounded and unrelated services survive |
| Compatibility and privacy | Older-peer behavior is explicit, current result/error shapes remain compatible, and progress never exposes prompts, credentials or opaque child output |

Run targeted units plus a simple real CLI/service/subprocess integration that
crosses the modified boundaries. Exercise fresh first-touch behavior when it
changes. Record native Windows and available Linux/remote qualification
separately; give a concrete reason for any unavailable lane. No production
service stop, rollout, real task resend or broad activation is part of this
proposal's validation.

## Completion boundary

Passing source tests or merging one helper is not completion of Phase 3 or
this parent effort. This slice closes only when its actual consumer matrix,
documentation, reviewed source and release are resolved; deployment and other
consumer realization stay explicit with their named owners. The parent retains
every unchecked supervision, recovery, category and cleanup obligation.
