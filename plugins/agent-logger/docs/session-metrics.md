# Session metrics

Back to [architecture](architecture.md).

`agent-logger metrics --json` measures one task carried by an ordered chain of
Copilot CLI sessions -- a session plus the successors it handed off to -- from
their `events.jsonl` logs alone. It reports when the task was kicked off, how
long it took on the wall clock, how much of that time the agent spent working,
and how many turns the user and the agent took.

```bash
agent-logger metrics --json <session-id> [<successor-id> ...]
agent-logger metrics --json --until 2026-01-31T17:00:00Z <session-id> <successor-id>
```

Pass session IDs oldest first; a repeated ID is measured once. Each ID resolves
through the [cold-store provider](architecture.md#cold-store-provider-agent_loggercold_store)
tiers (local live directory, on-device archive, locally synced corpus), and the
event log is read through the shared live/archive seam, so live, archived, and
synced sessions measure the same.

`--until` takes an ISO 8601 timestamp (UTC when it has no offset) and cuts the
measurement off there: events after it are ignored, work in progress at the
cutoff counts up to the cutoff, and a prompt after it cannot be the kickoff. A
cutoff never extends a measurement; when every log ends before it, the window
ends at the last logged event. A caller that knows when the task's deliverable
was published (for example, when a pull request was opened) passes that
instant.

The command always prints the JSON report. It exits `0` when the report is
`measured` and `1` when it is `unknown`; an invalid `--until` exits `2` with no
report.

## What is measured

- **Kickoff** -- the earliest human prompt at or before the cutoff across the
  chain, or, when the chain has no human prompt, the earliest prompt of any
  origin. A prompt is `human` when its `user.message` event has no `source`;
  `agent-*` and `schedule-*` sources are `agent` and `schedule`, and any other
  source is `other`.
- **End** -- the cutoff when some log continues past it (`end_basis: until`),
  otherwise the last logged event (`end_basis: last_event`).
- **Wall clock** -- end minus kickoff.
- **Working time** -- the main agent's busy time minus the time spent waiting
  on the user, combined with sub-agent busy time net of each sub-agent's own
  waits. Busy time is each model turn (`assistant.turn_start` to
  `assistant.turn_end`) plus the processing between a prompt and the first
  turn it triggers; a sub-agent's turn ends no later than its
  `subagent.completed` or `subagent.failed` event. An agent waits on the user
  during an `ask_user` call, until its answer or its completion with the same
  tool-call ID, and during a permission request (`permission.requested`),
  until its decision (`permission.completed`) with the same request ID.
  Failing a match, a wait ends at that agent's next turn boundary, prompt, or
  abort, or when the sub-agent ends; a tool call running in parallel does not
  end it. A sub-agent's wait is also removed from the main agent's busy time,
  because the main turn that launched the sub-agent stays open while the
  sub-agent waits. A prompt steered into an open turn is absorbed by that
  turn, and an abort ends the open turn. Each process that ran a session is
  measured on its own: a turn, wait, or prompt it left open is closed at its
  last logged event, so the time before a `session.start` or
  `session.resume` that started a later process is never counted, and a turn
  still open at a log's last event is closed there, never extrapolated.
  Overlapping time across sessions and sub-agents is counted once.
- **Turns** -- human prompts and `ask_user` answers are user turns. An answer
  is matched to its question by tool-call ID, so a question resolved by both a
  recorded answer and its tool completion counts once, and completions of
  other tools never count; an answer without an ID (older logs) resolves the
  oldest open question. Main-agent turns, sub-agent turns, tool calls, and
  sub-agent tool calls are counted separately. A sub-agent is recognized by
  its event's `agentId` or, in older logs, `parentToolCallId`; its own prompt
  is not a user turn, and its work counts through its turns and tool calls.

Durations are whole seconds, rounded down so a measurement never overstates.
Times are UTC ISO 8601 with millisecond precision.

Working time is inferred from event timestamps, so it has known limits. Time a
turn spends waiting on the model service -- for example, rate limiting or
retries -- counts as busy. A sub-agent's wait is removed from the main agent's
busy time even if the main agent kept working at the same moment -- for
example, while a background sub-agent waited -- which can understate working
time. Likewise, an agent counts as waiting for as long as any of its questions
or permission requests is pending, even while a tool call it started in
parallel is still running. A permission request is recognized only when it is logged with a request
ID, so a log that predates permission events leaves those waits counted as
busy. Process boundaries close open work at the last logged event, which can
understate the work in flight when a process stopped.

Only event types, timestamps, tool names, tool-call, sub-agent, and
permission-request IDs, and prompt source/delivery are read; message and tool
contents are never read or emitted. Correlating a report with a pull request,
work item, or any other artifact remains the caller's responsibility.

## Unknown is not zero

When any requested session is unavailable, the report's `status` is `unknown`,
`unknown_reason` is `session_unavailable`, `unknown_sessions` lists the
affected IDs, and `aggregate` and every session's `metrics` are `null`: a
partial chain would understate the task. When every session is readable but no
prompt is found at or before the cutoff, `unknown_reason` is `no_kickoff`.

A session row's `status` is one of:

| Status | Meaning |
|--------|---------|
| `ok` | The event log was read. |
| `missing` | No tier on this host has the session. |
| `invalid_id` | The ID is not a canonical session ID. |
| `unreadable` | Resolving the session or reading its event log failed (for example, an absent log or a corrupt or ambiguous archive). |

A log line that may hold a measured event but is not a JSON object, or a
measured event whose timestamp cannot be parsed -- including an out-of-range
date, time, or UTC offset -- is skipped and counted in `malformed_lines` or
`unparseable_timestamps`. It is never guessed at and never fatal.

## Report schema (`schema_version: 1`)

| Field | Meaning |
|-------|---------|
| `schema_version` | `1`. |
| `until` | The normalized cutoff, or `null`. |
| `status` | `measured` or `unknown`. |
| `unknown_reason` | `session_unavailable` or `no_kickoff`; `null` when measured. |
| `unknown_sessions` | IDs of unavailable sessions; empty otherwise. |
| `aggregate` | The chain's metrics block plus `sessions_in_window`; `null` when unknown. |
| `sessions` | One row per distinct requested ID, in request order. |

Each session row carries `session_id`, `status`, `archived` (whether the
session resolved to an archive rather than a live directory; `null` when it did
not resolve), `malformed_lines` and `unparseable_timestamps` (`null` unless the
log was read), and `metrics`.

A metrics block:

| Field | Meaning |
|-------|---------|
| `kickoff` | The kickoff; for a session row, its first prompt inside the window. `null` when there is none. |
| `kickoff_origin` | `human`, `agent`, `schedule`, or `other`; `null` with `kickoff`. |
| `end` | The end of the window, or for a session row, of its part of the window. |
| `end_basis` | `until` or `last_event`. |
| `wall_clock_seconds` | `end` minus `kickoff`; `null` when either is `null`. |
| `working_seconds` | Working time inside the window. |
| `user_turns` | `human_prompts` plus `ask_user_answers`. |
| `human_prompts` | Prompts of `human` origin, including steering prompts. |
| `steering_prompts` | Human prompts delivered as steering into work in progress. |
| `ask_user_answers` | `ask_user` questions answered by the user. |
| `automated_prompts` | Prompts of any other origin. |
| `agent_turns` | Main-agent model turns started. |
| `subagent_turns` | Sub-agent model turns started. |
| `tool_calls` | Main-agent tool calls started. |
| `subagent_tool_calls` | Sub-agent tool calls started. |
| `user_interrupts` | User-initiated aborts. |
| `open_turn_at_end` | A log ends inside an open turn -- the session is still running or its log is truncated -- so nothing after its last event is counted. |

Counts cover events inside the window. The aggregate block adds
`sessions_in_window`, the number of sessions whose logs overlap the window. A
session whose log does not overlap it keeps a row whose metrics block has
`null` times and zero counts.

## Carrying metrics in log manifests

A log-writer manifest's optional `sessions[].metrics` and a chronicle digest
manifest's per-session `metrics` carry one session row's `metrics` block
verbatim when a caller supplies it, and are `null` or absent otherwise; see
[manifest-contract.md](manifest-contract.md). Nothing in agent-logger
computes, estimates, or backfills these fields.
