# Workspace allocation and session acquisition

This pattern realizes the worktree vision's
[replay-safe-workspace-allocation](../../visions/plugins/agent-worktrees/README.md#replay-safe-workspace-allocation),
[origin-coordinated-remote-allocation](../../visions/plugins/agent-worktrees/README.md#origin-coordinated-remote-allocation),
the bridge vision's
[workspace allocation and session embodiment](../../visions/plugins/agent-bridge/README.md#workspace-allocation-and-session-embodiment),
and dispatch's
[execution workspace continuity](../../visions/plugins/agent-dispatch/README.md#execution-workspace-continuity).
The implementation campaign is
[workspace-acquisition-continuity](../../efforts/2026/10/13%20workspace-acquisition-continuity/README.md).

## One workspace, distinct owners

`agent-worktrees create` allocates space without starting a session.
For a worktree-capable repository, `agent-bridge create` composes that same
allocation boundary with session embodiment. An explicitly supplied existing
sessionless worktree is an ordinary input to embodiment, not a forced takeover.
Existing or uncertain conversation history retains the normal head, holder,
retirement, and task-affinity checks.

Dispatch chooses the execution target once. Allocation returns a
repository-qualified workspace identity, including the worktree and its
resolved path. Acquisition, recovery, and cleanup retain those owner-returned
facts. They never independently rederive the execution repository from the
task's queue lane or code target.

Three repository roles can legitimately differ:

- **Queue lane:** the producing task namespace and its claim authority.
- **Execution project:** the registered repository owning the worker worktree.
- **Code target:** an optional repository the worker is asked to change.

Only the execution project qualifies allocation and session embodiment.
A mismatch between requested and owner-returned workspace facts refuses before
launch; it is not a reason to search another registry or create a replacement.

## Origin-owned remote allocation

This is the **required target architecture**, not a claim that remote owned
creation is implemented. [#5839](https://github.com/ThomasMichon/copilot-extensions/issues/5839)
retains the cross-registry implementation and qualification gap. Existing
cross-space and unsupported-capability refusals remain until the complete
ownership protocol is implemented and verified; this contract is not permission
to remove them.

### Origin-local creation interface

With the calling managed worktree as the current directory, the target CLI
contract is:

```text
agent-worktrees create --json --machine=<target-machine> --repo=<target-repository>
```

Resolve and validate the creator through its existing worktree authority
**before** selecting destination configuration. The explicit allocation request
establishes responsibility; merely observing a directory does not.

- `--repo` selects the receiving repository, not a replacement caller or local
  project context. If omitted, use the repository resolved from the calling
  worktree's current directory.
- `--machine` selects the receiving machine through the existing machine and
  execution-environment resolver. Preserve explicit environment selection and
  refuse ambiguity rather than infer ledger locality from a physical host name.
  If omitted, preserve ordinary local creation.
- A successful allocation exit and JSON result mean the receiving worktree
  exists and the originating worktree retains durable allocation responsibility.
  Report exact child-claim linkage separately as pending or linked. Return the actual
  owner-selected machine/environment, repository, worktree identity and path,
  together with the creation/ownership result needed for recovery. Do not return
  a planned path or transport acknowledgement as completed allocation, or
  pending claim linkage as completed linkage.

The command allocates a worktree only. It does not launch a Copilot session,
deliver a prompt or imply agent-hosting permission. Source-only allocation and
existing standalone/unowned policy remain distinct from owned creation; a
supplied managed owner that cannot be established must never degrade to unowned
success.

### One coordinator, two local authorities

The origin's `agent-worktrees` allocation operation coordinates responsibility
and remote creation. It records a durable pending obligation before requesting
receiving-side effects. The receiving `agent-worktrees` owner creates and tracks
the child through its own allocation boundary, retaining the qualified origin
and logical creation identity. Once the receiver has durably created and
journaled the child, a composing host may start the agent under its ordinary
hosting, task and session admission rules. The origin asynchronously binds the
exact returned identity while the agent boots; linkage or acknowledgement
failure must not hold up, terminate or recreate that agent.

The pending origin obligation and receiving journal retain enough original
request and identity evidence to recover linkage after interruption. The existing
responsible owner continues bounded reconciliation independently of startup and
resumes it from durable state after restart; a fire-and-forget coroutine is not
the recovery contract. This introduces neither a second allocator nor a separate
scheduler or daemon. A confirmed link is reported only after it is durable.

Each owner mutates only its own state through its existing named operation
boundary. This is not a shared registry, a cross-machine filesystem lock, or
authority for a remote command to open the caller's tracking files. Hold local
locks only for local state transitions, not across network requests.

SSH initially carries the receiving operation; an explicitly adopted Gateway
manager may later route that same operation. The transport owns connectivity,
not worktree allocation or claim semantics. No mandatory receiving-to-origin SSH
connection is needed: the origin consumes the receiving owner's retained result.
Optional notifications cannot substitute for durable allocation responsibility
or completed-link evidence.
Bridge and Dispatch use this same origin-coordinated allocator rather than
maintaining separate remote-create or callback-to-claims implementations.

### Interruption and settlement

Use the logical creation identity described below throughout the transaction.
Its effective facts distinguish the origin authority/worktree from the receiving
authority, machine/environment and repository. Retain the identity before effects
even when the caller did not supply a key; expose it for recovery. A deliberate
fresh invocation and recovery of an existing invocation remain different actions.

An interrupted request or missing creation reply leaves allocation pending or
unknown until receiving creation is proven. A lost claim acknowledgement leaves
linkage pending, without reclassifying proven receiving creation or blocking
ordinary child execution. Recover the receiving owner's same
allocation and the origin's same claim; do not generate a replacement creation
identity, drop owner metadata, or delete an uncertain child to make a retry pass.
Parent finalization must remain blocked while allocation responsibility is
unresolved. Explicit ownership transfer must carry that pending responsibility,
not leave it behind.

Receiving creation must be durable before embodiment; exact origin-claim
linkage is explicitly not an additional launch-time gate or callback. Neither
this separation nor pending linkage bypasses ordinary hosting permission,
session-head, holder, prompt or task admission. Later child conclusion settles
the originating obligation through
the same recorded authorities and existing lifecycle operations. Lost settlement
responses remain reconcilable without a second writer or mandatory reverse
reachability. Unsupported peers refuse before effects; no compatibility fallback
may silently turn an owned request into an unowned allocation.

## Creation identity is not delivery identity

A logical creation request may carry an opaque caller-selected key. Qualify it
by the owning installation and canonical target repository; bind the key to the
effective creation facts and original result. Do not use it as the worktree ID.
An exact concurrent or lost-response replay returns that same result, or an
explicit pending/recovery outcome if creation is not yet proven.

Both front doors use the same allocation key and allocation facts. Bridge
binds its additional embodiment outcome to that allocation without redefining
the workspace request. A completed space-only result does not pretend a session
was created; completing embodiment does not allocate the space a second time.

Record intent before allocation effects and completion before returning the
result. Recovery uses the recorded identity and the owner's actual effects;
it never repeats creation simply because the client lost its response.
Changed target facts under the same key are a conflict. A removed or retired
result remains terminal history, not permission to allocate again. A caller
chooses a new logical revision for deliberate replacement.

Dispatch derives creation identity from the task's deduplication identity
(or durable task identity when it has no deduplication key) and an explicit
allocation revision. A reservation attempt number, transport retry, or ordinary
task claim generation must not silently become a new allocation revision.
Per-attempt acquisition/delivery IDs remain separate: they fence prompt
admission without redefining the workspace or permitting a duplicate turn.

Ground creation accepts optional `--creation-key`; local worktree-backed
bridge creation accepts the same option through protocol 34's distinct
workspace-creation endpoint. Explicit dispatch allocation revisions likewise
use a non-fallback revision endpoint. Older or unsupported owners refuse
instead of ignoring a safety-affecting option. Existing unkeyed standalone
creation keeps its deliberate fresh-create behavior; remote/fleet keyed
worktree embodiment remains explicitly unsupported.

Those are current capability limits. The origin-owned remote contract above is
the required extension, not evidence that these CLI or protocol generations
already implement it.

An explicitly selected provider-exec instance may instead use protocol 36's
`provider_instance` keyed-native option. Its provider owns the concrete
workspace; Bridge retains one physical provider instance and native result,
without manufacturing a ground-layer worktree. This is not a remote-worktree
allocation fallback. The separate provider journal records launch intent before
effects, refuses changed facts and ambiguous history, and never starts a second
conversation on replay. No first prompt or task authority is implied.

## Refusal and unknown outcome are different

Only a positively proven pre-effect failure may report `not_started`.
A deterministic invalid execution target follows dispatch's bounded failure
path. Unknown or interrupted effects retain the same workspace and consult the
owner's durable acquisition history; a deferral does not release allocation
identity or grant another create.

No timestamp, missing response, empty directory, unset head, or absent local
receipt alone proves that a previous conversation was never launched.
Authoritative sessionless creation history permits a first conversation;
existing native identity or ambiguous effects require continuity/reconciliation.
Missing optional capabilities fail explicitly and do not change execution mode.

## Connected validation

Exercise the public allocation/acquisition boundary, not only mocked factories:
different queue and execution repositories must still produce one target
workspace; replay and concurrent callers must allocate once; interrupted
responses must recover that result; conflicts and terminal keys must refuse;
and a first conversation in a positively sessionless existing worktree must
retain the same project/path. Preserve refusal for foreign/live holders,
missing legacy history, and ambiguous launch effects.

Before enabling origin-owned remote allocation, additionally prove:

- The origin-local public CLI creates in the selected remote repository while
  the caller's ledger remains the owner, including a different destination repo,
  omitted repo/machine selectors and independent Windows/WSL environments.
- Real owner command boundaries preserve the pending obligation before effects,
  receiving-side creation before agent startup, and asynchronous exact claim
  linkage while startup proceeds. Both the pending origin obligation and
  receiving journal must be durable before reporting successful allocation;
  completed linkage is a separate observed result.
- Concurrent/restarted requests, crashes before and after receiving creation,
  and lost creation/claim replies recover one child and one matching claim.
  An unresolved operation blocks parent finalization and unauthorized transfer.
- A delayed, unavailable or lost linkage acknowledgement does not delay a
  durably allocated child's startup. Recovery links that same child without
  starting another session or delivering another prompt, including after the
  originating coordinator restarts.
- A caller reachable only through outbound transport still completes allocation
  and later settlement; neither path requires a reverse SSH session.
- Bridge and Dispatch exercise the same allocator; SSH and a synthetic adopted
  Gateway adapter preserve the same target/owner semantics. A real Gateway
  implementation and its native qualification remain separate delivery work.
- Unsupported peers, changed request facts, invalid/frozen origins and mismatched
  receiver results refuse without owner-dropping fallbacks, duplicate children,
  premature session launch or fabricated absence.
