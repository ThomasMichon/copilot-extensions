# Ordinary-hook endpoint recovery - proposed source slice

Back to the [Active parent](README.md). Coordination is the finite P8388 case
retained at #3761, including the
[bounded source claim](https://github.com/ThomasMichon/copilot-extensions/issues/3761#issuecomment-6649260548).
This proposal is not the whole authoritative-daemon migration, a receiving
receipt for other trackers, or permission to activate an installed service.
Review and merge the proposal before implementation.

## Context and observed gap

The current hook client returns to local advisory evaluation when the resident
rendezvous is absent. Continued pre/post hooks do not ask the existing lifecycle
owner to recover that endpoint. SessionStart registration is a different
consumer and is not recovery proof.

Healthy resident calls, intentional strict-local policy evaluation and the
exclusive implementation selector already have useful behavior. Preserve them.
In particular, Windows' existing post-tool fallback must not resend a packet
whose delivery is uncertain. An absent endpoint is not proof that its owner
died, and a process that started is not necessarily ready to serve.

This closes part of the parent
[resident availability intent](../../../visions/plugins/agent-worktrees/README.md)
and follows
[singleton correctness](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md).
The existing lifecycle, singleton/cutover, source/installation identity and
process-custody owners remain authoritative. No new daemon, polling loop,
installation authority, process reaper or generic discovery cache is proposed.

## Proposed functional flow

1. **Expand the existing receiver first.** Add a small internal entrypoint in
   the installed Worktrees package, invoked as
   `python -I -m agent_worktrees.hook_monitor_recovery`. It receives a supported
   pre/post event kind and a finite absolute monotonic deadline. It is not a
   new provisioning, update or operator cutover step.
2. **Qualify the selected provider.** Advertise optional
   `hook-monitor-recovery-v1` in the provider's packaged
   `hook-runtime-contract.json`. The payload reads the declaration beside the
   actually selected runtime, not its own current files or a package-version
   guess. Missing/old/invalid declarations retain local fallback without invoking
   unsupported syntax or provisioning anything.
3. **Ask only the original owner.** Resolve the same selected installation/state
   root and enter its existing nonblocking cutover admission. Re-observe current
   rendezvous/identity there. A live, foreign, malformed, incompatible, fenced or
   uncertain owner is not a cold-start invitation. Do not remove its metadata,
   replace it, release its fence, scan the worktree fleet or invoke stale-reap.
   Only a qualified cold absence may use the existing monitor ensure/spawn path.
4. **Bound repeated attempts.** Keep one small owner-written attempt observation
   under that installation root, separate from the resident rendezvous. It is
   cooldown/coalescing data, not a resident identity, authority, completion ACK
   or proof that an earlier effect failed. Serialize observation and admission
   with the existing owner guard. Healthy calls do not read/write this file;
   retries must re-observe actual ownership rather than treating expiry as
   takeover permission.
5. **Connect the actual eligible consumers.** On a currently absent rendezvous,
   the existing advisory fallback may request bounded owner recovery, then finish
   the current event through its original fallback. Never replay that event,
   including after an ambiguous send, timeout, interruption or delayed reply.
   Keep strict-local policy, irrelevant/no-op bypasses, implementation exclusivity
   and native/ACP compatibility. Both Python/Bash and Windows' actual fallback
   entry must be covered, not an unused helper alone.
6. **Let later events observe readiness.** The next ordinary event uses normal
   rendezvous/IPC selection. A recovery spawn only reports starting; it is not
   successful hook execution, a healthy endpoint or whole-case acceptance.

These are proposed implementation choices, not additional operator grants.
Start with a maximum 0.5-second recovery-helper contribution and a five-second
attempt cooldown. Measure cold process/cleanup overhead against that bound;
do not widen caller, test-item or parent budgets to obtain a pass. Existing
whole-hook/throughput requirements remain separate and unqualified.

## Receiver and compatibility contract

The internal receiver emits exactly one bounded JSON object:
`schema: "agent-worktrees.hook-monitor-recovery"`, `version: 1`,
`status` and a fixed, content-free `reason`. Status is one of `present`,
`starting`, `deferred`, `disabled` or `unavailable`.

`present` means an existing owner was observed, not that this hook decision ran.
`starting` means the owning lifecycle admitted a start, not readiness.
Busy admission, deadline exhaustion and cooldown are explicit deferred outcomes.
Missing installation, unsupported context and failed launch remain unavailable.
Preserve the original hook's stdout, stderr, exit, fallback and policy semantics.
Recovery bookkeeping contains no hook payload, raw tool text, credential or
session inventory.

The provider-first sequence is expand, migrate, then retain compatibility:
new payload/old provider does not send the new invocation; old payload/new
provider retains its original behavior; new/new uses the bounded owner; rollback
returns to explicitly unqualified local fallback. No lockstep deployment or
automatic installed rollout is assumed. The selected provider's ordinary source,
governance and contained-test suppression still apply.

## Implementation plan

- [ ] Implement the internal receiver using the original lifecycle/cutover and
  singleton/custody paths; retain uncertain identity and interrupted effects.
- [ ] Package the optional runtime capability and connect the actual eligible
  fallback consumers without resending the current hook event.
- [ ] Keep healthy/strict-local/no-op paths unchanged and make recovery
  starting/deferred/unavailable states truthful and cheap.
- [ ] Publish the implementation with exact-source review, required CI and
  applicable contract/documentation checks. Record source, release and installed
  acceptance separately; do not close #3761 or the full P8388 case.

## Paired validation

- [ ] Reproduce absent-endpoint continued-hook fallback before the change, then
  exercise the complete production caller -> selected receiver -> lifecycle
  owner -> later ordinary hook selection in an isolated synthetic installation.
- [ ] Cover old/new provider combinations, missing/invalid declarations, foreign
  source/installation roots, disabled   contexts and strict argument rejection.
- [ ] Prove no recovery work on healthy resident, strict-local and existing
  irrelevant/no-op paths; no duplicate command-policy evaluation.
- [ ] Cover missing versus malformed rendezvous, live/unknown/reused identity,
  fenced/cutover-busy ownership and refusal without metadata deletion or reaping.
- [ ] Exercise simultaneous attempts and later cooldown/expiry against the
  actual admission seam, including a start not yet advertised and interruption
  before/after effect admission. Retain unknown outcomes without packet replay.
- [ ] Verify fixed deadline and original fallback/output/exit behavior, including
  a hanging helper and caller-owned descendant cleanup. A cleanup timeout is not
  a successful hook.
- [ ] Run focused unit and real consumer contracts, appropriate fresh first-touch
  and Windows/Linux controls, then exact-source required CI. Synthetic/source
  evidence is not installed ordinary-session/model/ACP or whole-throughput proof.

## Completion and retained ownership

This slice completes only after its real consumer flow, exact-source checks,
review and source merge are verified. Preserve #918/#2619 scheduling,
#6748 publisher recovery, #7937 acknowledgements, #7946 publication,
#5664/singleton replacement and #6832 roster-client recovery separately.
It neither supersedes their open PRs nor acquires their files or resources.
Installed activation, live ordinary-session acceptance and composed performance
remain their explicit receiving gates; this proposal executes none of them.
