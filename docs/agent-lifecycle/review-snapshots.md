# One PR review in snapshots

The job is **review PR 12033 at the requested head and return the review
reference**. Follow the records below to answer a practical question: has the
reviewer merely been allocated, or has it actually accepted the work?

This is a **worked example, not a production dump**. It uses a fresh finite
task in a configured, one-slot pool and a new local checkout. `T` stands for
the generated Dispatch task ID; `review-pr-12033` is a title, not a database
key. `R` is `dispatch-task:<T>:1`. `W` is
`my-dev-box-20261015-234705-d67a`, `B` is the Bridge control ID, and `N` is
the native conversation ID. Selected fields are shown; `null` means a field
is empty, **no row** means the object does not yet exist. Snapshots at
different owners are not one database transaction.

## 1. A producer creates the task

```text
Dispatch  tasks[T] = {title: "review-pr-12033", status: queued,
                      generation: 0, attempts: 0, owner: null, owner_session_id: null}
          task_events: creation recorded with destination queued
          spawn_reservations: no row for T
Worktrees no W record or checkout
Bridge    no B, Host or CLI registration for this job
Copilot   no worker or conversation for this job
```

The request identifies the repository, PR and revision to review. A matching
pool registration already exists independently of `T`. An exclusive resource
key, if configured, can prevent two attempts from working the same resource.
It is not the task ID. [Task creation](../../plugins/agent-dispatch/src/agent_dispatch/queue_storage.py)

## 2. Dispatch commits the slot before it calls a launcher

```text
Dispatch  tasks[T] = {status: claimed, generation: 1, attempts: 1,
                      owner: null, owner_session_id: null, lease_expires_at: null}
          spawn_reservations[R] = {task_id: T, attempt: 1, state: reserving,
                                   worktree: null, session_handle: null,
                                   startup_complete: false}
          task_pool_assignments[T] = {reservation_key: R, task_generation: 1,
                                      registration_id: reviewers, machine: my-dev-box, env: linux}
          pool_reservation_bindings[R] = the same selected pool instance
Worktrees / Bridge / Copilot  unchanged
```

**Nothing has been spawned.** Dispatch reserved the right to try. These queue
writes commit together; if the supervisor exits immediately afterward, another
reader can see the attempt without seeing a worker. With no capacity a fresh
task stays `queued`; no new attempt is spent merely because the slot is full.
In the legacy path only the reservation is made first: the task stays queued
until the worker claims it. [Placement transaction](../../plugins/agent-dispatch/src/agent_dispatch/queue_pool_placement.py)

## 3. Worktrees allocates the checkout

```text
Worktrees workspace-creation.sqlite3: creations[key_digest] pending → complete
          Git: branch worktree/<W> and checkout at the returned path
          worktrees/<W>.yaml: repo, worktree_id=W, branch, worktree_path,
                             creation_nonce, status=active, sessions=[]
                             dispatch_attempt: task_id=T, reservation_key=R, attempt=1, …
Dispatch  R: worktree=W, worktree_ownership=created, allocation_project,
             worktree_path and creation identity recorded on return
Bridge / Copilot  no conversation required to allocate W
```

The SQLite row is for **keyed creation**. The writer reserves the allocation
identity before Git creation, saves the YAML record and finishes any required
pairing before returning success. If an owning worktree requested the child,
its responsibility is recorded before the Git effect. The YAML is canonical;
the resident roster and status caches are not additional lifecycle commits.
An interrupted return can leave the journal pending even when the directory
exists. See [creation](../../plugins/agent-worktrees/src/agent_worktrees/worktree_creation.py),
[journal](../../plugins/agent-worktrees/src/agent_worktrees/creation_requests.py) and
[which startup step failed?](startup-boundaries.md).

## 4. Start the execution: choose one column, not both

| Observation | Hosted ACP | Interactive CLI in a mux |
|---|---|---|
| Before native identity | `sessions[B]` is persisted `starting`, targeting `W`; native ID not yet known. | Bridge reserves CLI mode for `W`. Worktrees creates or targets the mux; a pane is not a conversation. No ACP `sessions[B]` row is required. |
| Process tracking | The independent Host owns the child. Its private `host-…json` state file records startup evidence. Bridge's Host-state `index.json` associates `B` with the file, endpoint, Host/child PIDs, birth evidence and frontend ownership. | Manager can record the mux session incarnation and pane in `mux-mapping.json`. Native registration, when observed, records the session/head in Worktrees. The Bridge extension registers `live_sessions[N]` with `W`, PID/birth and heartbeat. |
| Ready, **before task prompt** | `session/new` returns `N`; Bridge persists `sessions[B].acp_session_id=N`, becomes `idle`, and binds native lineage to `W`. A fresh unprompted creation can have **zero turns**. | An unseeded acquisition waits for the expected live registration and matching Worktrees head. Before `N` is observed, report *native ID unknown*, not “the pane ID is N.” |
| First task delivery | The ordinary worker path creates without a prompt, records the returned identities and uses a separate keyed send. An admitted prompt has a `turns` row; a running turn is not a submitted review. | Exact delivery goes into `live_messages` for `N`. A queued inbox row, SDK send, acknowledgement and observed consumption are different observations. This is not an ACP `turns` row. |

**When does Copilot create a conversation?** Distinguish the native ID, the
first user message and the on-disk transcript. ACP gives Bridge a native ID
*before* the first task prompt. In the CLI path this guide can establish when
the hook/extension **reports** an ID, not the instant the CLI writes its private
transcript. Neither a running child nor an empty transcript proves a user
turn happened. See [ACP creation](../../plugins/agent-bridge/src/agent_bridge/acp_client.py),
[Host launcher](../../libs/copilot-driver-core/src/agent_remote_driver/session_host/launcher.py),
[Host index](../../plugins/agent-bridge/src/agent_bridge/session_host/host_index.py),
[ordinary worker startup](../../plugins/agent-dispatch/src/agent_dispatch/ordinary_bridge.py)
and [CLI acquisition](../../plugins/agent-bridge/src/agent_bridge/acquisition_plugins.py).

## 5. What the reviewer sees, and what `start` changes

An illustrative exchange, shortened from the [pool-worker guidance](../../plugins/agent-dispatch/src/agent_dispatch/embody_prompts.py)
and the [ordinary startup seed](../../plugins/agent-dispatch/src/agent_dispatch/ordinary_invocation.py):

> **Task:** Review PR 12033 at head H. Submit a reference to the review on H.
>
> **Dispatch guidance in the first prompt:** Task T is already claimed by the
> allocator at generation 1. Use the original coordinator. Read the operating
> procedures, the worker charter and the task. Check for duplicate work. Do
> not claim it again. If you accept, start it with your own workspace identity
> and the retained native session ID.
>
> **Worker action:** Read T, verify the PR head, accept with
> `start T --pool-generation 1 --owner-session-id N`, then report progress.
> If H has changed or the assignment cannot be accepted, report the blocker
> instead of reviewing a different head.

Before that `start`, `tasks[T]` can still be `claimed` and unowned while a
reviewer is reading the prompt. After a successful start:

```text
Dispatch  tasks[T] = {status: started, generation: 1,
                      owner: my-dev-box/<W>, owner_session_id: N, started_at: …}
          R is the SAME reservation; no new slot or native launch
          task_events records acceptance; task_progress can now record review work
Worktrees W is still active; head N is not a verdict on PR 12033
Bridge    the transport can be in a turn or idle between turns
```

**Task and reservation are coupled by checks, not by matching status names.**
Normally `R.state=spawned` once the supervisor publishes startup. The
[initial-start guard](../../plugins/agent-dispatch/src/agent_dispatch/queue_pool_placement.py)
also permits `reserving` if the exact worker, native, workspace, assignment
and other guards already match. `start` is the worker's acceptance, not a
request to set `R.state=started`; there is no such reservation state.

**Can R manage a taskless worker?** No. The supported
[spawn-reservation operation](../../plugins/agent-dispatch/src/agent_dispatch/queue_spawn_reservations.py)
takes an existing task ID. `R` identifies an attempt for that task. Bridge
and Worktrees can start a standalone session without Dispatch, but that is not
a free-standing Dispatch spawn reservation.

## 6. Wait for a new PR head, or finish the review

If changes are requested and this worker should review the next revision,
`T` can be `suspended` with the same owner and `N`. Suspending the **task**
does not itself park the **process**: `R` can remain `spawned` until cooling
completes, then become `cold`. See [who gets the next slot?](pool-wake-order.md).

If the goal is met, the reviewer instead submits the review reference:
`started → submitted`; an independent confirmation can record `completed`.
Neither step proves resource cleanup. For a created workspace, release can
show `R.state=releasing` while native retirement or checkout conclusion is
pending. Only the relevant owners can establish exit, settle the reservation
and finalize or retire `W`. A reused checkout need not be removed.

[Run map](README.md) · [Task and reservation states](dispatch.md) · [Workspace closure](worktree.md)
