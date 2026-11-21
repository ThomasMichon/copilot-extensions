*The following contribution was assisted using Copilot.*

# Selected Current Force-Identity Contribution

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Integration: [scoped sequencing](roadmap-sequencing.md).
Evidence and delivery: [dated journal](journal/2026/11.21-current-force-identity.md).

## Current contract and source

This is an agent-recommended bounded contribution for OPEN #5006, not an
implementation assignment or permission to inspect or signal processes.
Its complete current original and all five discussions were qualified and
read before selecting source. The whole shared birth-identity keeper remains
independently OPEN; its four unfinished lanes are not one mandatory native
matrix or universal owner-acknowledgment gate.

The selected source is `1e8c45cc8e97631c2f1a6ce69135edf006888b8f`.
Seven actual later publications qualified the Bridge force/ordinary
termination and process-group files; fifteen qualified the shared terminator,
relevant service vision and cutover pattern. Current local/development file
versions matched before those source bodies were read.

The current [force operation](https://github.com/ThomasMichon/copilot-extensions/blob/1e8c45cc8e97631c2f1a6ce69135edf006888b8f/plugins/agent-bridge/src/agent_bridge/service_process_cli.py#L193-L243)
still tries `safe_killpg(SIGKILL)`, then falls back to bare
`os.kill(pid, SIGKILL)`. The preceding retirement wait checks Bridge command
identity, not a captured birth token bound through the signal.
The [group helper](https://github.com/ThomasMichon/copilot-extensions/blob/1e8c45cc8e97631c2f1a6ce69135edf006888b8f/plugins/agent-bridge/src/agent_bridge/procgroup.py#L27-L49)
refuses the caller's own group, but does not establish process birth identity.
That existing guard is useful, not proof of the complete force contract.

Credit the separate [ordinary `_kill_pid` path](https://github.com/ThomasMichon/copilot-extensions/blob/1e8c45cc8e97631c2f1a6ce69135edf006888b8f/plugins/agent-bridge/src/agent_bridge/_kill_pid_identity.py#L447-L507):
where an identity-bound primitive is available it captures birth identity
**before** the slower owner check, refuses unavailable per-target identity,
and uses the shared bound operation. Its explicitly separate unsupported-
platform fallback is not the selected force-path repair. Mux #5060, ordinary
Bridge #5524 and shared `new_process_start_time` publication/recovery remain
delivered foundations, not missing schema work.

The actual [shared POSIX terminator](https://github.com/ThomasMichon/copilot-extensions/blob/1e8c45cc8e97631c2f1a6ce69135edf006888b8f/libs/zdd/src/zdd/diagnostics.py#L271-L350)
opens a bound descriptor, compares birth identity and sends `SIGTERM`; its
public API has no force-signal argument. This is verified from its body, not
inferred from the force caller's retained comment. Calling it unchanged would
not preserve the requested `SIGKILL` semantics. A successful signal is also
distinct from authoritative exit and descendant settlement.

## Intent, priority and retained owners

Retain P1 source-correctness priority by the wrong-process signaling
consequence. Confidence is high in this static caller/API distinction;
there is no new runtime-incidence claim or P0 escalation, and no assertion
that it outranks every other P1.

The qualified current service vision's
[replacement](../../../visions/plugin-services/README.md#zero-downtime-cutover)
and [single lifecycle authority](../../../visions/plugin-services/README.md#one-authority-per-lifecycle-decision)
clauses require bounded replacement with mutation-time target protection,
not compulsory zero downtime or old-contract cooperation. The
[cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md#common-review-findings--a-pre-flight-self-check)
separates birth identity from rightful-owner validation and requires direct
positive and stale/reused-target controls. This contribution closes existing
intent; it creates no new lifecycle authority.

Original shared ZDD, Bridge force-tree and retirement/exit-custody owners
retain this interface. Whole-issue effort-home acceptance is not newly
established by related singleton or installer work. Existing resource claims,
source/copy restrictions and implementation/review authority remain unchanged.

## First contribution and finite exit

Through those owners and applicable implementation authority, characterize
the actual force operation before selecting a force-capable shared/native
binding. Preserve the requested process/group scope, not merely a safer
direct-child `SIGTERM`. Capture identity before slower owner validation and
bind the eventual signal to that validated target. A failed group attempt
must not authorize an unverified direct-PID fallback.

Focused contained regressions must cover matched-birth force success against
an actual fixture-owned process, retaining authoritative exit evidence,
stale/reused-birth and unavailable-identity refusal, owner mismatch and
own-group protection at the actual group and fallback boundaries. Include
replacement during owner validation and truthfully distinguish signal
delivery, authoritative root exit and unknown descendants. Root/command
mismatch alone is not the requested reused-birth negative; root exit alone
is not tree completion. No unsafe timer-based reap follows from uncertainty.

The finite source exit is a reviewed force-capable correction with those
controls, or explicit rightful-owner reconciliation of the concrete site
under the original contract. Release and originally authorized consumer
qualification remain separate. Name supported Windows/Linux/macOS behavior
or justified exemptions without equating POSIX with Linux or adding an
every-host matrix.

For installation, preserve
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and all current selections define completion. This
retirement slice cannot make predecessor recovery, retirement proof or
failed cleanup a new installation veto, restore old selection, or target
session hosts, keepers, foreground clients or a reselected generation.
Actual exclusive-owner exclusion and mutation-time target safety still
apply; unknown cleanup is reported rather than silently called success.

## Ordinary cleanup diagnostic: current receiving boundary

The separately qualified current publication #8045 supplies the actual
[local-cache refresh source](https://github.com/ThomasMichon/copilot-extensions/blob/99771fc26d06f9e3fc5b3e17c35cdb6c96d9eb2b/plugins/agent-bridge/src/agent_bridge/local_cache_refresh.py#L233-L449).
Credit its mandatory Windows Job, structured renderer outcomes, bounded
cleanup, cancellation handling and own-group/mismatched-birth refusal.
The [native adapter](https://github.com/ThomasMichon/copilot-extensions/blob/99771fc26d06f9e3fc5b3e17c35cdb6c96d9eb2b/plugins/agent-bridge/src/agent_bridge/local_cache_refresh.py#L116-L132)
already supports Darwin through the existing psutil dependency. This lane
is not a universal missing-macOS-backend defect.

The opt-in `retire_group=True` path already reports a missing baseline as
preparation stopped with descendant cleanup unconfirmed. That is real
progress, but not the ordinary caller: [refresh's render call](https://github.com/ThomasMichon/copilot-extensions/blob/99771fc26d06f9e3fc5b3e17c35cdb6c96d9eb2b/plugins/agent-bridge/src/agent_bridge/local_cache_refresh.py#L686-L710)
omits that argument, whose default is False. In that path `_kill_tree`
suppresses/skips an unverified group signal and returns no cleanup outcome.
The timeout/failure result therefore carries generic subprocess failure,
not the required specific missing-baseline/UNKNOWN-descendant diagnostic.
Its direct-child kill/wait cannot establish descendant cleanup.

Through the existing Bridge local-cache refresh/process-group cleanup
custodians, the first useful contribution is to preserve that diagnostic
through the existing run/refresh result on the actual default-False path.
Characterize missing birth identity and a fast leader exit independently;
retain opt-in behavior, baseline/mismatch/own-group protections, bounded
cleanup and cancellation propagation. A focused control must show the
specific unconfirmed-descendant result while no unverified group is
signaled; generic renderer timeout or direct-child exit alone fails it.
Preserve established-baseline cleanup and successful refresh controls.

This is the P2 tracking lane's finite contribution under #5006, not a new
severity assignment or a requirement to add a dependency, Darwin backend
or unsafe timeout reap. The source observation and fixture design are not
executed tests. This lane remains independent of force-capable retirement
and native live-monitor acceptance; neither supplies the other's exit.

## Other keeper lanes remain independent

The Bridge local-cache refresh/process-group cleanup workstream retains
#5063's ordinary default-False missing-baseline diagnostic and fast
leader-exit/UNKNOWN-descendant outcome. #5140 retains all five causal cases:
#6164's four registry-fixture repairs are credited, while the fifth actual
matched-process failure needs its own authoritative causal disposition.
#7659 credits Darwin birth-source/monitor work in #9935/#10101 while retaining
the supported native live-monitor cutover and birth/reuse controls, including
the original shared-source **OR** authoritative-lock comparison alternatives.
None is demonstrated complete by the selected force-path source study.

These lanes can advance independently under their actual owners. No whole
installer, singleton, claim, framing or handoff program must finish before
this finite contribution. All 43 parent check states, 24 open items and six
roadmap gates remain. The latest complete census is 899 open, 170 above fixed
729; this proposal adds no closure credit, product test, native probe,
process operation, runtime change or deployment.
