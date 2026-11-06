# Passive handoff/head observation

The optional `agent-worktrees.handoff-head-observation` version-1 contract
compares actual handoff consumption with the durable head and the Picker's
read-time resume choice. It does not repair any of them.

## Discovery and invocation

The selected executable's existing `list-sessions --json` response adds:

```json
{
  "handoff_head_observation": {
    "contract": "agent-worktrees.handoff-head-observation",
    "version": 1,
    "command": "handoff-head-observation"
  }
}
```

That declaration incurs no receipt reads. Existing callers can ignore it.
A consumer must validate it before invoking the new optional command against
that same selected executable:

```bash
agent-worktrees --project example handoff-head-observation \
  --worktree wt-example --contract-version 1 --json
```

The project is explicit or normally selected; the worktree ID must be exact.
There is no suffix inference or search across other projects. Missing legacy
declarations mean unavailable support, not permission to probe new syntax.
Unknown arguments and unsupported contract versions remain parser errors.
No existing command, flag or response field is retired.

## Response and meaning

The normal version-1 JSON envelope includes `contract`, `contract_version`,
`project`, `worktree_id`, `subject`, `status`, `reason`, `expected`,
`durable_head`, `picker`, `limits` and `work`. `subject` is absent when the
current record cannot be qualified. This read exits zero for an observed
unknown/unavailable result; callers must inspect `status`, not just the exit.

- `match`: one independently qualified actual consumer matches both the
  durable head and the Picker's sampled resume target.
- `mismatch`: the qualified consumer differs from either target. An observed
  null head is a known empty head, not an unsuccessful read.
- `unknown`: evidence is missing, legacy, ambiguous, stale, incomplete or no
  longer identifies the current worktree/consumer incarnation.
- `unavailable`: a source is unreadable, malformed, unsupported by the local
  filesystem, or exceeds an observation budget.

`expected` reports only the token, predecessor, consumer, source kind
(`file`/`task`), actual consumption time and captured consumer identity.
`durable_head` and `picker` each have an independent fact status and, when
observed, `session_id`. Durable head also includes its revision. Stale facts
are explicitly marked; failed reads never manufacture a known null.
`reason` is a short identifier, not source content. Prompt, payload, title,
workspace contents and unrelated session data are never returned.

## Owning evidence and limits

The reader derives the state namespace from the selected project, not from
paths inside a receipt. It reads one exact tracking record and only its
registered sessions' exact predecessor-request paths. Candidate discovery is
limited to **that exact worktree's own existing handoff directory**, at most
64 directory entries, followed by exact filename reads. This is necessary
because a task can be consumed before head promotion records its token.
It never enumerates the session-state root, projects or unrelated worktrees.
A missing state directory is not created.

File records require actual consumed state and consumer identity. Task
checkpoints require `steps.taskConsumed`, not preparation, attempted consume,
prompt injection, or a proposed candidate. Token/predecessor and available
consumption mirrors must agree. Original author identity is checked against
the current worktree incarnation and retained activation history; consumer
identity must match its exact current activation. Reading a mutable binding
cannot upgrade an older receipt. A completed abort is not consumption.

The most recent actual consumption supplies the expected identity. Ties,
newer incomplete deliveries, conflicting mirrors, an unreadable candidate or
a missing known receipt cannot fall back to an older matching pass.
Retained/pruned legacy evidence can therefore conservatively remain unknown.
This is not a guarantee of complete historical delivery retention.

Each read admits at most 64 registered sessions, 64 handoffs, 256 transitions
and 256 activations per session; 512 observed paths; 1 MiB per owner file;
64 KiB per Picker workspace; and 8 MiB total file bytes. A one-second
cooperative elapsed-work check surrounds bounded stages. The caller still
owns cancellation, a hard process deadline and complete child cleanup:
filesystem calls and parsers are not forcibly interrupted by that check.
`work` reports actual file reads, bytes, observed paths and elapsed time.
Receipt-directory and read-file identity/size/modification observations are
rechecked before publication, including on an inconclusive result. Conversation
files are presence/identity probes only: ordinary appends do not change the
Picker's existence test or require reading a transcript. Changed evidence
invalidates the comparison rather than repairing or repeating it.

The Picker observation uses its existing resolver with bounded filesystem
inputs. Its head/yielded-head preference and registry-latest fallback retain
their normal meaning. No Git, native process census, resident wake, network,
handoff/head mutation, session launch, input or report action occurs.
There is no cache, timer, TTL, second registry or new persistence.

## Compatibility and qualification

Provider support is additive and precedes any Companion adoption. Old callers
retain their normal listing. New callers must leave unrelated features usable
when the declaration is missing, malformed, unsupported or rolled back; a
package version is not support evidence. Consumer adoption remains separate.
The source fixtures exercise both actual JavaScript consume paths, refused
promotion, the real CLI parser/dispatch, and private tracking/receipt files.
They are not installed/native performance or live manual-cutover evidence.

## See also

- [CLI reference](cli-reference.md).
- [Independent expected-head implementation plan](../../../efforts/active/mux-companion-manual-cutover-diagnostics/expected-head-observation.md).
- [Exact-ID session-state access](../../../docs/patterns/session-state-access.md).
- [Version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md).
