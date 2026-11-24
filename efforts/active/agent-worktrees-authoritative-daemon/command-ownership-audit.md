# Who owns an `agent-*` command?

**2026-11-24, source audit.** The largest gap is not a missing file lock. It
is a command whose provider effect, local receipt and visible status have
different lifetimes. Routing a setter to a daemon, renaming a file atomically,
or putting a CLI in front of a service does not close that gap.

**Scope and method.** The baseline is the verified [`dev` tree at
10084454bf](https://github.com/ThomasMichon/copilot-extensions/tree/10084454bf2c2abf0857a0ebedcafbb80ff06bd3).
Enumerate the `agent-` names in plugin and standalone-service `pyproject.toml`
`[project.scripts]`; cross-check payload invocation manifests, bin aliases
and package entry points. This yields **14 command families** (including
standalone Chat and the Remote Driver app) and **five service-host entry points**
(`agent-{chat,dispatch,index,pull-requests,ssh}-service`). The latter start the
owners; they are not five more interactive command families. `agent-roles`
and `agent-conduct-guidance` are payloads, not additional consoles. Logger's
unprefixed aliases and repository aliases are alternative entrances to the
same owners, not new stores. Adopter-only commands are outside this repository.

For each family, follow argument dispatch into the client, file, store, worker
or provider below. For Worktrees this includes `current_roster_entry`, the
lazy table, its `pr_author_commands` expansion and the manual routers in
`__main__`; enumerating `add_parser` alone misses commands. Rows group verbs
that reach the same boundary. This is static evidence about the named paths,
not a per-option crash test, an installed-runtime inventory or a declaration
that every command in a service-bearing plugin is transactional.

## Worktrees first

| Surface | Observed boundary and consequence | Change to qualify |
|---|---|---|
| Record, status, claims, binding and handoff | [`tracking_write`](../../../plugins/agent-worktrees/src/agent_worktrees/tracking_write.py) sends registered mutation verbs to the resident monitor, but `run_direct` also runs handlers in the caller on eligible pre-send failures. Same code does not share the owner's queue/cache. `AmbiguousWriteOutcome` deliberately avoids post-send replay. | Move admission and completion to the selected service. Preserve known-not-sent vs unknown. A distinct write gets a distinct identity; redelivery of the *same* intent queries its receipt. |
| `create`, `move`, `sync`, `mark-complete`, `finalize`, `cleanup`, `gc`, `reclaim` | The [dispatcher](../../../plugins/agent-worktrees/src/agent_worktrees/__main__.py) selects `worktree_ops_cli`, `finalize_cli`, `cleanup_gc_cli` and `reclaim_cli`. These are management operations spanning checkouts, processes/refs and records, not Git pass-throughs. A record lock cannot cover a caller dying between those steps. | Sequence by worktree, and by actual shared ref/anchor when necessary. Persist progress before external changes. Bound independent Git and file work rather than serializing all checkouts. |
| `create-pr` / `pr-create`, `push-changes`, `pr-status`, `pr-abandon`, `pr-merge` | `finalize_cli`, `pr_state_cli` and [`pr_authority`](../../../plugins/agent-worktrees/src/agent_worktrees/pr_authority.py) coordinate publication and claims. Status that reconciles or releases a claim is a write despite its name. In the [recovery draft](https://github.com/ThomasMichon/copilot-extensions/commit/572564bf38049b521d0403b86c8d4bd424b5629b), pending receipts preserve ambiguity; they are not daemon-owned operation lifetimes. | First vertical slice: create, lose the reply, inspect, retire local bookkeeping and finalize the same worktree. Keep independent real-WIP/claim checks. Never infer a provider closure from a local retirement. |
| `list`, current roster, observation/history, picker, `get`, `state-root` | [`current_roster_entry`](../../../plugins/agent-worktrees/src/agent_worktrees/current_roster_entry.py) selects current-read paths. Other list/classify paths can compute Git facts; a read that refreshes/persists a projection has a writer. Context resolution and rendering alone need none. | Use owner-published revisions for current views. Keep bounded advisory reads clearly labelled. Route refresh to its owner; do not rebuild or adopt silently during a read. |
| `repos gh -- …`, Git helpers, installer/monitor verbs | Repository-scoped forwarding selects an external identity/tool. `git sync` or lifecycle publication does more than forward a command. Installer, setup and `status-monitor` lifecycle have their own owners. | Preserve explicit external forwarding, help and bootstrap. Do not wrap arbitrary Git in a Worktrees DB transaction or use a forwarding verb to change managed records. |

## Other command families

Source paths below are relative to the named package's `src/agent_<name>/`
unless otherwise specified. The **remaining check** is an implementation
obligation for that owner, not a claim that data loss was observed.

| Family | Route found in source | Remaining check or justified exception |
|---|---|---|
| `agent-pull-requests` | [`__main__._cmd_create`](../../../plugins/agent-pull-requests/src/agent_pull_requests/__main__.py) delegates explicit native requests, but the default GitHub path calls `_github_create` **before** `_journal_pr_claim`. A claim failure produces a warning after a successful creation. Watch/report/workspace clients are separate read/service paths. | **High priority:** death between those calls leaves the PR created but unclaimed. Route both entrances through one managed create operation. Do not give the watch service ownership of a Worktrees claim. |
| `agent-codespaces` | `__main__` dispatches borrow/release, pool and lifecycle commands into `lease.py`, `pool.py` and `lifecycle.py`. [`_lease_lock`](../../../plugins/agent-codespaces/src/agent_codespaces/lease.py) uses exclusive file creation and age-based removal after a wait; writes use temporary replacement. | **High priority:** an old timestamp does not distinguish an interrupted owner from a slow live one. Put managed lease/provider transitions under the venue owner with recoverable intent. SSH transport and display are separate. |
| `agent-containers` | `__main__` routes `borrow`, `release`, `up`, `stop`/`remove` to lease, fleet and lifecycle handlers. [`lease_lock.provider_lock`](../../../plugins/agent-containers/src/agent_containers/lease_lock.py) uses a kernel-backed namespace and refuses unsafe legacy cutover. | Preserve that lock's exit semantics; it does not atomically couple a provider effect to a lease record. Qualify interrupted allocation and cleanup; retain direct stdio/runtime forwarding when it makes no managed claim. |
| `agent-dispatch` | [`task_lifecycle_cli`](../../../plugins/agent-dispatch/src/agent_dispatch/task_lifecycle_cli.py) uses `DispatchClient` for claim/release and other task transitions. `create_cli` also performs workspace preparation and Bridge/session acquisition. | Keep the coordinator as task DB owner. A successful claim is not proof of a launch. Reconcile remote acceptance and prepared workspace by identity after loss of the client. |
| `agent-bridge` | Session start and streaming use the Bridge client (`session_start_cli.py`, `session_streaming_cli.py`); [`config_cli`](../../../plugins/agent-bridge/src/agent_bridge/config_cli.py) also writes session-mode selection. Process startup/stop are a separate lifecycle boundary. | Keep long-lived session ownership at the Bridge/session host. Distinguish editing declared config from activating it; do not report a new effective mode from a saved file alone. |
| `agent-index` | `__main__.cmd_search` uses an HTTP client in client role and an engine directly in the local role. [`index_command.run_index_command`](../../../plugins/agent-index/src/agent_index/index_command.py) calls the selected reindex engine; `cmd_index_worker` is a service-launched worker. | Distinguish workers from an independent CLI writer. Admit index mutations once per corpus, publish the completed index revision, and retain prior/explicitly stale results during rebuilding. |
| `agent-logger` | `__main__._cmd_catalog_rebuild` discovers sidecars and calls `rebuild_from_sidecars`; [`catalog.py`](../../../plugins/agent-logger/src/agent_logger/catalog.py) owns a derived SQLite index. Chronicle tick/sync and local digest formatting are different operations. | Serialize catalog rebuild/annotation against the same source revision, including interrupted rebuild. A private digest over supplied files needs no daemon. Do not equate SQLite locking with sidecar/index consistency. |
| `agent-machines` | `__main__` leads to [`reconcile.py`](../../../plugins/agent-machines/src/agent_machines/reconcile.py) (`apply_surfaces`, `apply_resources`) and `self_update_state.write_status` (temp + replace). Discover/validate/plan also run locally. | Route managed apply/update through the machine's existing execution owner; record actual per-effect progress. Pure validation stays local. A status-file replace does not roll back host changes. |
| `agent-mcp` | `bridge`/`forward` and `call` route to transports; `serve`/`cutover` manage service lifetime. [`tool_cache_maintenance.clean`](../../../plugins/agent-mcp/src/agent_mcp/tool_cache_maintenance.py) scans then unlinks selected cache paths on `apply`. | Read-only scan is local. For shared cache cleanup, establish the cache owner's revision at deletion or serialize with materialization so a newer replacement isn't removed. Forwarding an upstream tool call does not make it safe to replay. |
| `agent-ssh` | `verify` probes connectivity; [`mesh_refresh.py`](../../../plugins/agent-ssh/src/agent_ssh/mesh_refresh.py) writes retained input and calls `ssh_profile.write_fragment`. Gateway/service administration and interactive transport have distinct destinations. | Keep transport/probe local. For managed configuration, order publication of the fragment and effective selection by host/profile; verify recovery from a partial refresh. |
| `agent-vault` | `cli.py` calls the read and mutation clients; [`mutation_client.py`](https://github.com/ThomasMichon/copilot-extensions/blob/10084454bf2c2abf0857a0ebedcafbb80ff06bd3/plugins/agent-vault/src/agent_vault/mutation_client.py) explicitly distinguishes unknown outcomes and avoids alternate-transport retry. Cache maintenance has its own client. | Reuse the existing Vault authority. Verify committed data and published cache agree after reply loss; don't copy secrets into generic receipts. Explicit output export and lifecycle bootstrap do not become database writers. |
| `agent-remote-driver` | App entry selects host/connector or [`service_commands`](https://github.com/ThomasMichon/copilot-extensions/blob/10084454bf2c2abf0857a0ebedcafbb80ff06bd3/plugins/agent-remote-driver/src/agent_remote_driver_app/service_commands.py); service `ensure/status/stop` go to the selected lifecycle binding, with an instance-specific stop. | Do not require a running daemon to start itself. Qualify connector/config publication separately from the retained session host; stopping a generation is not a terminal session result. |
| `agent-chat` | Standalone [`cli.py`](../../../services/chat/src/agent_chat_service/cli.py) uses `Client` and `GatewayTransport` for chat; offline catalog/workspace and local pending status do not require a connection. `store.py` is the service-side store. | Keep channel mutations at Chat, worktree membership obligations at Worktrees and per-client progress at its owner. Lost reply/leave must not silently release a claim or duplicate a post. |

## Delivery order

1. **Worktrees and PR creation:** extend the [existing daemon effort](README.md)
   and [PR conformance effort](../pr-lifecycle-conformance/README.md).
   Capability-negotiate complete operations before migrating aliases and
   removing their direct writer. Do not move an installation-wide CLI lock
   verbatim into the service.
2. **Resource allocation:** Codespaces and Containers own the provider-side
   lease; Dispatch and Worktrees own task/worktree claims. Bind those steps by
   operation identity, not one cross-service lock. Use the existing
   [capacity](../owned-codespace-capacity/README.md) and
   [container runtime](../podman-runtime-and-image-sources/README.md) plans.
3. **Views and secondary state:** qualify rebuilds, status stamps, config
   application and cache cleanup with the owning service. Keep the
   [Index engine](../agent-index-engine-daemon/README.md),
   [machine control plane](../agent-machines-declarative-control-plane/README.md)
   and [Vault](../vault-service/README.md) independent. No central daemon for
   all plugins and no mandatory resident dependency for offline utilities.

## Acceptance

| Interrupt or interleave | Observable acceptance |
|---|---|
| Before admission; immediately after admission but before reply | First case has no effect; second returns the same operation on lookup. The next agent need not delete a lock file or guess which case occurred. |
| During Git/provider work; after provider success but before local settlement | Unrelated work progresses within bounded I/O budgets. On restart the owner reconciles the known result or retains `unknown`; it does not issue another external write just to settle the receipt. |
| After durable commit but before cache publication | A current read sees a coherent committed version, or explicit unavailability while the view is rebuilt. It cannot combine a new disposition with an old active claim. |
| Client exits, service restarts, then an old worker returns late | No permanent active lock. The old worker cannot overwrite its successor. A live conflicting writer is still ordered; an unresolved remote outcome remains evidence, not process ownership. |
| Publish and finalize the same worktree; publish on an unrelated one | The conflicting pair has a defined order; the unrelated work is not blocked by that pair. The normal single-agent case requires no extra grant or fleet inventory. |

Run these through actual command entry points and disposable service/DB and
provider fixtures in the implementation efforts. A mocked transport is not
live-provider qualification. This inventory changes no installed service,
does not authorize a rollout, and does not declare any of those gates passed.
