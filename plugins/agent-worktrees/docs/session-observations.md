# Retained session observations

`agent-worktrees session-observations <session-id> --json` is an optional,
read-only single-session provider. It serves the
[Mux Companion](../../../visions/mux-companion/README.md)'s accumulated
statistics and reported work topology without returning a conversation.
It does not change `worktree-status-bundle` or scan sessions for fleet views.
It does not require project adoption or enable source-repository agent hosting.
No sidecar, cache, service, installation or live-state update is involved.

## Contract impact and compatibility

The output declares `contract: "agent-worktrees.session-observations"`,
`version: 1`, `supported_versions: [1]`, and capabilities
`recorded_tool_events` and `reported_subagent_lifecycle`. This declaration is
produced by the executable actually invoked, not a payload/package stamp.
Version 1 readers must accept additive fields and reject unsupported contract
identities/versions. Unknown CLI arguments still fail with parser status 2.
With recognized syntax, exit 0 means the evidence envelope was emitted, **not**
that observations are available; read `availability` and `issues`.

This is provider-first expansion only: no ordinary consumer is migrated here.
A future optional consumer can probe this read-only command on its selected
executable, validate the declaration, then use the metadata. An older or rolled
back provider may reject the command with its existing parser/routing error:
retain that failure as **unsupported**, leave this optional feature unavailable,
and keep existing features usable. Missing/invalid declarations are unknown or
unsupported, never an invitation to parse transcripts or guess a package
version's support. There is no equivalent legacy statistics fallback.
Existing commands/outputs remain unchanged; nothing is deprecated or retired
and there is no synchronized-upgrade requirement.

## Evidence semantics (version 1)

- `session_id` is a validated session identifier, not an arbitrary path.
  State uses Worktrees' existing Copilot home/path resolution. Lexical traversal,
  drive-relative paths and resolved symlink escapes are refused.
- `source.kind` is `copilot.events.jsonl`; `snapshot_bytes` is the opened file's
  initial size and `observed_bytes` is the amount actually read. `as_of` is the
  provider's UTC observation time, not a producer heartbeat or liveness proof.
- The reader streams from byte zero through that initial size; appended bytes
  are not included. Memory holds one record plus aggregate distinct metadata
  keys, not the transcript or a list of every event. A record larger than
  1 MiB is drained, counted as `oversized_record`, and makes evidence partial;
  later records still accumulate. There is no record-count/history-tail cap.
  Cancellation and timeouts belong to the caller's CLI process boundary.
- `history.scope` is always `retained_file`, **never lifetime**.
  `complete` means the available retained snapshot was fully readable and
  unchanged at the checks, not that retention preserved the entire session.
  `session_start_records` counts exact `session.start` records; even an observed
  start does not establish continuous runtime or lifetime coverage.
  `valid_records` counts parsed JSON-object records, including ignored types.
- `availability` is `available`, `partial`, or `unavailable`. Missing,
  unreadable, empty, wholly malformed or incomplete-only files are unavailable,
  with `tools`/`subagents` null rather than invented zero success.
  Readable observations alongside malformed/oversized records, an unterminated
  final record, short reads, growth, modification, replacement or disappearance
  are partial. `issues` has sanitized reason-code counts, never raw lines,
  exception text, file paths or transcript snippets. Filesystem checks detect
  observable changes; this is not an atomic filesystem snapshot or runtime lock.
- `tools.started_records`, `completed_records`, and `by_name` count recorded
  `tool.execution_start`/`tool.execution_complete` events, **not distinct calls**.
  Repeated IDs are repeated records. Names come from validated `toolName` or an
  unambiguous matching `toolCallId` anywhere in this snapshot.
  `unattributed_records` retains name attribution gaps by phase; conflicting
  names on one ID disable inferred correlation and are counted separately.
  `parentToolCallId` is not a tool-call or agent identity fallback. No success,
  error, duration or token totals are manufactured.
- `subagents.availability` is `observed` when lifecycle records exist, otherwise
  `unknown` (absence is not proof of producer support or no agents).
  `lifecycle_records` counts exact `subagent.started`, `subagent.completed`,
  and `subagent.failed` records; missing/invalid actual `agentId` values count
  as `unattributed_records`, never invented agents.
  `agents` groups actual IDs with `last_reported_lifecycle`, optional
  `parent_tool_call_id`, `name`, `display_name`, `model`, and validated
  nonnegative integer `reported_duration_ms`/`reported_tool_calls`.
  The launching-task reference uses validated lifecycle `toolCallId`, falling
  back to `parentToolCallId`; neither supplies a missing `agentId`.
  The last valid report of each optional field is retained in file order.
  Completion/failure describes the reported invocation/turn, **not** permanent
  process retirement, deallocation, release or resource ownership.
  `runtime_continuity: "unknown"` preserves restart/reuse uncertainty.
- `pending_input.availability` is always `unknown`: pending input is not
  reliably persisted. No livepulse/tail, attention or question guessing is
  attempted. Messages, prompts, arguments, outputs, errors, raw event bodies
  and descriptions are not returned.

Focused validation:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_session_observations.py --test-file tests/test_session_observations_cli.py --test-file tests/test_lazy_dispatch.py
```

The process fixture uses only synthetic session state and a repository denying
agent hosting; the existing negative hosting policy remains enforced.
