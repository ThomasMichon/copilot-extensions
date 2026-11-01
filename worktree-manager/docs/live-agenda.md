# Companion live agenda consumer

The optional Agenda tab presents committed agenda observations. Efforts retains
schema, mutation, durability and planning authority. Agent-worktrees retains
source/head qualification, resident capability negotiation, routing and
publication authority. Manager owns only process lifetime, wire admission and
presentation. No plugin package is imported and Manager reads no agenda file,
resident route, token or socket directly.

After the ordinary current-worktree reload, project comes from `worktree.repo`.
When that is absent, the constructor's observer project is usable only if its
worktree ID matches the selected worktree. Exactly one `is_head: true` session
supplies the head session ID; a native pane incarnation is not that ID.
Missing project/worktree/head or a failed session read leaves Agenda unavailable
without starting a command.

The held read appends this literal command to `engine_client`'s resolved argv:

```text
agenda-feed --project <project> --worktree <id> --session <head-session-id> --json
```

Before invoking that new verb, Manager probes the actual resident through the
existing provider-owned `worktrees_client.read_health()` API in a separate,
isolated process using the same resolved interpreter. It does not enter the
legacy `agent_worktrees.__main__` dispatcher, where an unknown verb may launch.
The probe requires `worktree-agendas-v1`, ready roster authority and a valid
resident generation. Missing/offline/malformed/failed probes refuse without
sending any agenda argv; the owning feed CLI rechecks capability afterward.
Every reconnect reprobes, and the feed retains the exact argv captured before
that probe rather than resolving a different provider afterward.
The probe's internal error envelope retains typed codes: connection and timeout
failures are retryable, while unsupported, malformed, authentication, permission
and unclassified failures are terminal. Exception message text never determines
retry policy. After authenticated capability and generation validation, the same
probe passes its support proof internally to the worker, without an additional
Manager probe/process. This permits bounded initial-unready recovery before any
snapshot and continued recovery when a later reprobe temporarily goes offline.

An unresolved or non-absolute executable refuses. The admitted normal argv
shape ends in `-m agent_worktrees` or `-m agent_worktrees.current_roster_entry`.
Other wrappers refuse explicitly because no safe existing health entry can be
inferred. There is no bare-PATH fallback, in-process provider import, legacy
agenda filesystem fallback, default-launch dispatch or provisioning command.
Manager does not separately call resident control actions
`agenda-read`, `agenda-publish` or `agenda-feed`, and never publishes or submits
agenda changes.

## Admission and lifetime

Version-1 feed frames are one complete JSON object per newline, at most 35 KiB
including the delimiter. The first publication is `snapshot`; later `update`
publications have the same 32-lowercase-hex resident generation and strictly
increasing integer sequence. Heartbeats and going-away notices retain the current
generation/sequence. Every snapshot/update explicitly matches the requested
project/worktree/session. Duplicate JSON members, malformed data, invalid
versions, repeated publications and stale source/store revisions fail visibly,
including reconnect snapshots that regress a previously
observed resident sequence or rewrite/regress the same store revision.

The observation must carry `efforts.agenda-observation` version 1, the requested
worktree/session, a 32-hex store ID, positive revision, UTC commit timestamp, and
the complete versioned snapshot. Domain data stays display-only; structural wire
validation is not a replacement for the efforts owner's movement/budget validator.
The current snapshot uses a text root, text-list stack/exits/queue/notes/rechecks,
a text-map declared-outcomes field and an integer parked count. Nullable action
and revisit text remains display-only. Core fields are exact, cursor versions
1/2 are supported, and observations are capped at 32 KiB.
Admission also bounds strings to 4 KiB (no NUL), nesting to 16, container members
to 128 and integers to signed 63-bit magnitude. The preflight has a five-second
owned-command deadline; startup and each subsequent complete frame have a
five-second deadline;
partial bytes do not renew it. Stderr is drained separately and retains at most
8 KiB for explicit older-CLI/command errors.

One owned Textual thread worker retains the subscription and reconnects. Each
reconnect requires a new snapshot; previous presentation is cleared while
unavailable. Once support has been authenticated, transient disconnect/deadline or
going-away permits at most three retries, delayed 1, 2 and 4 seconds. Invalid
data and unsupported errors are terminal. Structured relay errors retain that
policy: `disconnected`, `unavailable`, `unready`, `draining` and `superseded`
are transient after support was observed; malformed/unsupported errors do not
retry. Known temporary closures (`draining`, `unready`, `unavailable` and
`disconnected`) end that connection but do not retire its generation:
a failed replacement or temporary fault can reactivate the same owner and
re-admit a fresh snapshot without Refresh. Resident sequence and store revision
fences remain in force.
Other going-away notices conservatively retire the generation, including actual
supersession/shutdown; a retired generation cannot be re-admitted.
No automatic rapid retry or timer/file poll
is used to obtain agenda data.

Reload fences the local source generation and data object immediately, cancels
the old read, and waits for its worker before admitting a replacement.
Unmount cancels the owned subscription. Pipes are drained under the existing
Manager POSIX group-owner / Windows kill-on-close Job primitives, preserving
contained-test behavior. Late callbacks cannot replace a newer selection.
`head-changed` clears the observation and ends the subscription with Refresh
required; previous agenda data is never treated as active. No Agenda control
executes, accepts, approves or changes planning state.
Cancellation after a health child completes is a normal subscription return,
so Refresh can join that worker. An unexpected interruption without a cancelled
owner is an explicit unavailable error, not a silent cancellation.

## Source checks and deployment boundary

From the repository root, run through the bounded supervisor and existing
disconnected devcontainer wrapper:

```text
test-supervisor -- python tools/run_tests_in_devcontainer.py worktree-manager --test-file tests/test_agenda_contract.py --test-file tests/test_companion_agenda_live.py --test-file tests/test_mux_companion.py --test-file tests/test_companion_topology_ui.py --test-file tests/test_companion_attention_ui.py
```

The wrapper snapshots tracked files: stage only newly authored Manager files
when necessary, without changing another contributor's index entries. The
ordinary `tools/run-plugin-tests.py` runner is the documented fallback when
Docker/devcontainers are genuinely unavailable.

Synthetic peers drive actual CLI stdout pipes and real Textual `run_test`
widgets. Controls cover complete atomic frame admission, partial frame
non-publication, pushed widget updates, cancellation before/after a snapshot,
reload retirement, late callbacks, head/source/generation invalidation,
unsupported CLI/capability errors, bounds and reconnect exhaustion. They do not
substitute for the coordinator's actual efforts commit-to-provider integration,
native Windows window/Job qualification, installed runtime acceptance, or
active-agent migration.

`tests/test_agenda_end_to_end.py` is the separate actual whole-chain witness.
It uses unmodified core mutations and the real publisher subprocess, owning
agenda parser/handler, authenticated control client/server, resident projection,
canonical YAML record and sidecar lock, held relay, Manager preflight/transport/
subscription and real Textual widget. Only endpoint/project/current-worktree
discovery points at private fixture state; observation bytes and admission are
not replaced. It checks the first unpublished snapshot, commit-before-delivery,
exact committed-body equality, a pushed parked-count change, offline pending
delivery without mutation replay, recovery publication, immutable original
acceptance, canonical head-change clearing and birth-qualified child/subscriber
settlement. It does not extend native/platform or rollout qualification.

Until the new file is tracked by its coordinator, run the wrapper's explicit
untracked-source option instead of modifying the shared index:

```text
test-supervisor -- python tools/run_tests_in_devcontainer.py worktree-manager --include-untracked --test-file tests/test_agenda_end_to_end.py
```

Its explicit source closure adds the provider source, core scripts and
`libs/config-migrate/src`, `libs/lazy-cli-dispatch/src` and
`libs/repository-policy/src` to the prepared Manager interpreter. No installed
provider, package-install workaround or production endpoint supplies a missing
test dependency.

Expand the owning provider before enabling independently deployed consumers.
An older provider remains an explicit optional-feature failure; Manager is
still usable without agenda support. Source implementation and synthetic
evidence do not establish a release, runtime rollout, or whole-effort completion.

## Integration entry points

These are source-test entry points, not permission to access an installed
resident or another agent's store. Resolve all paths from the test checkout.

| Leg | Actual entry point |
| --- | --- |
| Core store | Add `plugins/efforts/skills/driving-effort-agendas/scripts` to the test import path; import `agenda_store`. `mutate(home, request, session=..., actor=..., publisher_configured=True)` returns `(result, newly_accepted)` after commit. `read(home)` returns the durable state/snapshot. |
| Committed publication | `agenda_store.delivery_record(home)` returns `(revision, observation)`. `agenda_publish.publish(home, explicit_argv)` invokes the real publisher subprocess and records delivery separately; never rewrite its body or replay a mutation to repair delivery. |
| Provider authority | `agent_worktrees.worktrees_runtime.WorktreesRuntime(db_path, elected=...)`; call `activate()`, then `publish(project, roster, observed_at=...)`. Its `agendas.read(request)`, `agendas.publish(payload)` and `stream(action, payload, disconnected)` are the actual projection/held-feed paths. |
| Authenticated control | `agent_worktrees.status_monitor_cutover.ControlServer(handle, token=..., stream_handle=owner.stream)`; `start()`, then a real `ControlClient(base_url, token=...)`. Dispatch `health` to `owner.health()` and agenda actions to `owner.agenda_handle(action, payload)`. |
| CLI publisher/feed | `agent_worktrees.agenda_cli.cmd_agenda(args)` performs health/capability negotiation, bounded stdin publication and the actual `relay(client, request, generation)` socket-to-stdout stream. `agent_worktrees.__main__.main(argv)` is the full owning parser/entry. |
| Manager process boundary | `worktree_manager.engine_client.set_engine_command(absolute_argv)` selects a fixture-only owned provider command. `agenda_transport.frames(project, worktree, session, cancelled=Event(), cwd=...)` owns the actual child and pipe. |
| Textual view | `worktree_manager.mux_companion.MuxCompanionApp(cwd=...)`, `async with app.run_test() as pilot`, then activate `#views` / `agenda-tab` and inspect the actual `#agenda` Static. |

The existing Manager fixture's `_PEER` only relays synthetic frames; it is
deliberately **not** a whole-chain witness. For the coordinator's integration:

1. Use only `tmp_path` state, a real synthetic tracking record/head, a private
   `WorktreesRuntime` SQLite database and an ephemeral loopback `ControlServer`.
   Redirect the owning tracking locator to that exact fixture record; retain
   actual head resolution, record locking and projection admission.
2. Load the core example documents from the efforts skill's `assets/`, replace
   only the synthetic worktree identity, and submit an `efforts.agenda-request`
   version-1 init with expected revision 0 and a stable operation ID. Supply
   the explicit synthetic head session on every mutation.
3. Point both the publisher and Manager at the same actual provider CLI fixture
   argv. A fixture entry may substitute only project/endpoint discovery with
   the private real `ControlClient`; keep `cmd_agenda`, health negotiation,
   authentication, publication, `relay` and Manager transport real. For a
   script-shaped test entry, explicitly admit its read-only health argv through
   the `agenda_transport.health_argv` fixture seam; that command must execute
   the actual provider `worktrees_client.read_health()` against the same private
   client. Do not mock `require_capability` or its health result.
4. Start Textual with selection data derived from that fixture's real record.
   Observe `not-published`, invoke the real post-commit publisher, and require
   `app._data.agenda["observation"]` to equal the unmodified
   `delivery_record(home)` observation.
5. Submit a second real semantic operation with the current expected revision
   and a distinct operation ID, then publish. For example, a nonblocking `park`
   operation changes the real snapshot's parked count; require the visible
   widget to change from `Parked: 0` to `Parked: 1` without reload or file polling.
   Retain the same data object and subscription owner.
6. Close Textual, confirm its owned CLI/held reader retires, then close admission,
   shut down the owner and close the fixture server. Never normalize malformed
   committed metadata in the relay to make this witness pass.

Use the bounded runner above, not raw pytest. In its disconnected container the
Manager test interpreter is
`/workspaces/copilot-extensions/.test-venvs/linux/worktree-manager/bin/python`;
fixtures should use their actual `sys.executable`, not a host-path assumption.
The wrapper owns interpreter preparation and source snapshots. A Manager-only
venv does not automatically install the provider package: explicitly admit its
source/dependency closure for a combined test, and treat a missing dependency as
a setup failure rather than importing an installed production runtime. The
existing provider fixtures are `plugins/agent-worktrees/tests/test_agenda_runtime.py`;
the core fixtures are `plugins/efforts/tests/test_agenda_store.py`.
