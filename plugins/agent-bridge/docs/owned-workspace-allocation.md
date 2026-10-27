# Origin-owned SSH workspace allocation

Back to [Agent Bridge](../README.md) and the
[allocation/acquisition contract](../../../docs/patterns/workspace-allocation-and-session-acquisition.md).

For a new owned project-backed SSH session, Bridge composes the origin's
ordinary Worktrees destination allocator before normal native startup.
The session target retains a stable allocation key in its existing extensible
request metadata before allocation effects. The origin command proves its
actual local creator and advertises bridge-shaped destination creation before
Bridge sends the corresponding creation arguments. Worktrees owns protected
pending responsibility, receiving creation, replay and asynchronous linkage;
Bridge adds no allocator, claim writer, operation store or reverse callback.

The actual worktree ID and path are persisted in Bridge's existing session row
before native transport. Receiving resolution then selects that existing child,
never another `resolve --new`. The bridge kind, caller navigation identity,
qualified owner and actual receiving nonce remain distinct. Receiving creation
may be complete while origin linkage is pending or currently unknown; startup
does not wait for acknowledgement. The existing hosting, native-head, holder,
prompt and session admission behavior remains in force.

An interrupted allocation retains its original key in the session target.
Process-owned resume uses that same origin operation before spawning; a lost
capture does not authorize a new key or another receiving child. A historical
legacy attempt without that intent refuses this recovery path and must be
inspected through its original owner. No current attempt is retroactively
adopted or cleared.

Ordinary resume admits a failed or stopped pre-native allocation only when its
original session-bound intent remains valid, no receiving child has yet been
recorded and no native conversation or client exists. Recovery replays that
allocation and permits one first native launch. If native startup then has an
unknown result, it does not reroll or recreate the conversation. A retained child
without a native identity requires explicit original-operation reconciliation;
it is not evidence that native launch never started. That retained fence also
rejects automatic recreation requested by ordinary send, including after a
Bridge restart.
Initial owned SSH startup holds the existing session lifecycle lock through
allocation and native registration or failure. Stop and resume wait for that
startup to settle; they cannot overlap a delayed allocation with a second
native-creation path or race its terminal state publication.

## Supported boundary

This consumer requires a distinct canonical SSH destination execution-space key
and an origin ledger that is actually local to the selected Worktrees command.
The actual creator probe may normalize a supported legacy machine label; it
does not move the ledger or connect back to the caller. A Bridge daemon on
another machine cannot write a foreign caller's ledger through this path and
refuses before receiving effects. Caller-side composition for that topology,
including outbound-only caller qualification, remains separate work.

Older Worktrees runtimes lacking the declared bridge mode, caller metadata or
creator probe refuse before creation. There is no metadata-dropping fallback.
Unowned remote creation, local targets, provider-instance creation, explicit
existing-workspace selection and the strict local keyed-embodiment protocol
retain their separate contracts. This slice does not enable keyed SSH
embodiment, replace Dispatch reservation/acquisition authority or qualify
installed/native deployment.

The [Worktrees provider reference](../../agent-worktrees/docs/worktree-allocation.md)
owns destination options, capability fields, protected claim recovery and
receiving lifecycle semantics.
