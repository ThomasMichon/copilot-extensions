# Optional current-head agenda projection

Agent-worktrees can cache committed agenda observations and stream them on its
existing authenticated resident control endpoint. It remains standalone: no
efforts or Manager package is imported, and neither a projection nor a delivery
acknowledgement grants actor permission, external acceptance or completion.
Task/action text is display data; this provider never executes it or emits agenda
files.

## Owned CLI and support

Use the selected installation's `agent-worktrees` argv from its command catalog:

```text
agent-worktrees agenda-publish --project PROJECT --json
agent-worktrees agenda-read --project PROJECT --worktree FULL_ID --session HEAD_SESSION --json
agent-worktrees agenda-feed --project PROJECT --worktree FULL_ID --session HEAD_SESSION --json
```

The publisher reads one UTF-8 JSON committed body from stdin, capped at 32 KiB
including its newline. Reads and feeds use an exact full worktree ID, never a
suffix, codename or path. All three commands negotiate `worktree-agendas-v1`
against the existing authenticated resident health and its current
`worktrees.generation`. Missing capability, credentials, route or readiness is a
structured failure, not an empty successful projection. There is no advisory
fallback, automatic ensure/provisioning or alternate runtime selection.
Agenda read/publish control request envelopes use bounded compact UTF-8 JSON,
so non-ASCII text is measured as transmitted UTF-8 bytes rather than expanded
ASCII escapes. Existing non-agenda control encoding is unchanged.
Valid reads, publications and initial feed requests retain the existing owner's
bounded project demand. An unpublished current project roster returns
`error.code: "unready"` and coalesces a wake of the existing producer; demand
survives an initial feed error and expires through the normal owner linger
budget. Admission closure does not register demand. A worktree absent from an
already published complete project roster instead returns `not-found`.
Capability/readiness health therefore does not guarantee that every project's
first snapshot is ready; consumers may receive an initial `unready` error.

The new contract is additive. Existing version-1/2 roster contracts, rows and
feeds retain their meanings. Older residents do not support the agenda actions;
callers must report unsupported delivery, not replay agenda mutations or infer
support from an installed package version. No existing contract is retired.

## Explicit version-2 agenda support

The running resident separately advertises `worktree-agendas-v2`. All three
agenda commands accept `--contract-version 2`; omitting the option preserves
version 1. A caller must establish support against its selected executable and
the authenticated resident before emitting a new option. The CLI checks the
requested resident capability before sending a control action; it never
downgrades a failed version-2 publication or repeats the canonical mutation.

Read/feed/publish request fields remain exactly those below, with
`contract_version: 2` when selected. Successful replies and structured errors
carry that selected version; heartbeat and going-away fields are unchanged.
Version-2 requests can carry an unchanged version-1 observation for a legacy
cursor, or an `efforts.agenda-observation` version-2 body for execution cursor 4.
The latter retains exactly the same Snapshot fields; its recognized statuses are
exactly `working`, `waiting`, `replanning` and `complete`. `steering` remains a
legacy observation-version-1 status and is not accepted for cursor 4.
Observation version 1 admits only cursor 1/2; observation
version 2 admits only cursor 4. Cursor 3 is not an execution observation.

A version-1 request selecting a current cursor-4 publication receives explicit
`unsupported` instead of an invented version-1 body or an older cached
publication. The provider does not relabel, truncate or tunnel new queue,
attempt or context state through another field. Those data remain with the
canonical planning owner; this boundary carries its bounded coarse projection.
An older provider lacking version-2 support leaves new delivery unsupported or
pending, never acknowledged through mutation replay.

Both versions retain the existing UTF-8 size/shape limits, head and store
qualification, generation/sequence barriers, revision/digest fences, durable
commit-before-notification and held-reader lifecycle. A version change is not
permission to reset a revision, replace a same-head store, or clear observed
evidence through a null availability update.

## Optional display graph

A resident that also advertises `worktree-agendas-graph-v1` admits an optional
top-level `graph` member on a version-2 observation. It is a bounded,
display-only projection of the planning owner's queue, frame stack, cursor and
recent history:

```text
{version: 1, revision, cursor: {frame, item, stack[]},
 frames: [{id, parent, via, state, label, returns}],
 items: [{id, frame, label, state, attempts}],
 history: [{at, kind, frame, item, text}],
 omitted: {frames, items, history}}
```

`revision` matches the observation's. Frames form a tree, `via` naming the
parent item a subqueue expanded from. The cursor names the active frame and an
item of that frame, and `stack` lists frame ids from the root frame down to the active
frame. Counts in `omitted` report what the bound dropped. The graph is
validated with the observation, so it has the same size limit.

Read and feed callers receive `graph` only when they request it with
`--agenda-graph 1` (request field `graph: 1`), which needs
`--contract-version 2`. The CLI checks both resident capabilities before it
sends the request. Without that request, the reply omits `graph` and is
otherwise unchanged, so existing consumers see the same body. Publishing
commits the graph as part of the body. A publisher must drop it for a resident
that does not advertise the capability, and must not tunnel it through
another field. `agenda-publish` requests and checks the graph capability
whenever its input body carries a graph. Revision identity excludes the graph:
the same committed revision may be republished with or without it (after a
capability upgrade or rollback) and replaces the cached projection, while any
other difference is still a `revision-conflict`. The resident stores the graph
in its own table, bound to the cached revision identity, so the durable
agenda cache keeps the graph-free body an older resident can reopen after a
rollback. A cached graph is rejoined only while its identity still matches the
cached revision. The publisher keeps the
active stack, deferred frames and every kept frame's ancestors in the graph,
and omits the graph when that structure exceeds its bound. The graph never
grants acceptance, permission or an action.

## Version-1 control contract

`agenda-read` and `agenda-feed` accept exactly:

```json
{"contract_version":1,"project":"PROJECT","worktree_id":"FULL_ID","session_id":"HEAD_SESSION"}
```

`session_id` may be null to represent unknown source identity; it cannot qualify
an observed agenda. `agenda-publish` accepts exactly `contract_version: 1`,
`project`, `expected_generation` (the health generation) and `observation`.

The committed observation contains exactly `schema: "efforts.agenda-observation"`,
`version: 1`, `source: {worktree, session, store_id}`, positive integer `revision`,
UTC ISO `committed_at` and `snapshot`. `store_id` is 32 lowercase hexadecimal
characters. The snapshot contains exactly:

```text
worktree revision status root stack next_action exit_criteria declared_outcomes
queued parked notes cursor_version deferred due_rechecks next_revisit_at
```

`root` is text; `stack`, `exit_criteria`, `queued`, `notes` and `due_rechecks`
are text lists. `declared_outcomes` is a text map, `parked` a nonnegative count,
`deferred` a bounded list, and `next_action`/`next_revisit_at` nullable text.
The provider validates bounded JSON and recognized projection fields, not the
planning domain's full board semantics. Duplicate keys, nonfinite numbers,
malformed UTF-8/JSON, excessive depth, unknown core fields and oversized data
are rejected. Text is capped at 4096 UTF-8 bytes, collections at 128 members,
and integers at signed 63-bit magnitude. Snapshot cursor versions 1 and 2 are
supported. Embedded paths, commands and action data are never followed.
Recognized status values are `working`, `waiting`, `steering` and `complete`;
non-null revisit timestamps must be timezone-aware ISO values. Non-UTC offsets
are accepted and the original text is preserved; generated `committed_at`
remains strictly UTC.

Successful replies contain exactly:

```text
ok:true contract_version:1 generation sequence project worktree_id session_id
availability observation
```

`generation` is the resident roster owner's 32-hex generation; `sequence` is a
nonnegative, bounded, generation-local monotonically advancing cursor.
`availability` is `observed`, `not-published`, or `head-changed`. Only `observed`
contains an observation; the other states carry null. Failure replies contain
`ok:false`, `contract_version:1`, and `error:{code,message}`.
`head-changed` is reserved for an absent canonical head or a request that does
not select it. When the request selects the current head but the cache contains
only another head's observation, availability is `not-published`: the old body
stays private and the held feed remains eligible for the current head's first
publication.

Feed frames are complete, atomic JSON lines of at most 35 KiB. The first frame
is `type:"snapshot"` plus the reply. Changed selections produce
`type:"update"` plus the reply, in the same generation with a strictly greater
sequence. Slow readers have one notification slot and see the latest complete
state, not an unbounded event history. Idle frames are
`{type:"heartbeat",generation,sequence}`; they do not read agenda files or
refresh every tracking record. Admission closure emits
`{type:"going-away",generation,sequence,reason}`. Errors are
`{type:"error",ok:false,contract_version:1,error:{code,message}}`.
The CLI validates identity, generation, cursor and frame size before stdout.
Non-text frame types are rejected with a structured `invalid-response` error,
not a traceback or an empty successful relay.
There are at most 32 held agenda subscriptions, accounted in the existing
monitor drain and retirement path; no additional listener or daemon is created.
Activation failures and writer replacement close agenda admission and already
held streams alongside the roster feed. A writer replacement's terminal
retirement reason cannot be downgraded by a subsequent activation failure.
Temporary same-generation recovery admits a new snapshot without resetting
sequence or source-store fences; a replaced writer remains fenced and its new
generation must requalify current roster/cache data before observation.

## Default-on policy

The portable `agendas.default` preference (see
[Configuration reference](config-reference.md#default-on-agendas)) makes an
agenda the expected way to run a session. With `expected`, session-start
guidance adds an `Agenda:` line stating whether this worktree already has a
lane (`present`, `absent`, or `unknown` with the
`worktree-data-read --inventory --json` command to check) and the next commands
from the `driving-effort-agendas` skill: derive from an effort
(`agenda_outline.py from-effort`), rebuild from the session history
(`agenda_backfill.py preview <session-folder>`), or author an outline, then
`preview` and `adopt`. A throttled post-tool reminder repeats while no lane is
detected. Lane detection is local and read-only: a managed `agendas` placement
in the worktree-data inventory or a non-empty worktree `.effort-agenda/` home.

The post-tool reminder never resolves configuration. Session start (and every
resume), which already runs in the selected runtime, publishes the effective
value to `<runtime>/nudge-state/<worktree>.agenda-policy.json` as
`{"version":1,"policy":"off"|"expected","written_at":<epoch>}`, written
atomically and explicitly even when `off`. The record is written under both the
selected runtime root and the per-user `~/.agent-worktrees` root (relocated
under `AGENT_HOME` when set, so sandboxes never write to the real home). The resident
monitor, the standard-library fallback hook and the prepared Windows PowerShell
read path each read only this small record and the local lane evidence, with
their own `<worktree>.agenda.json` window independent of the disposition
counter. They do not load configuration, run git, start Python or make a
synchronous call. A missing, malformed or unknown-version record means no
reminder. `agent-worktrees status` write mode (for example `status --activity`,
which the disposition nudge already asks agents to run) republishes the record
too, so a changed preference reaches a running session at its next disposition
write, start or resume.

The policy is guidance, not automation or a lock. Agent-worktrees never adopts,
creates or edits an agenda; the agent decides whether to adopt, owns its edits,
and may deviate when the work calls for it. `off` (the default) changes nothing.

## Ownership, durability and recovery

Membership comes from the current owned roster; identity and head come from
the exact owning tracking record's `resolved_head_session`. A roster
`last_session_id` can be a resume target and is not publication authority.
An absent head, wrong session, removed worktree or wrong project refuses
publication. Tracking records are loaded through the existing bounded locator,
with no cross-project scan or suffix matching.
Head qualification is demand-driven for unobserved worktrees. Ordinary roster
publication does not load agenda heads until a read/feed/publication requests
them or a committed cached observation needs requalification.

Publication holds the existing owner's activation fence and a short,
sidecar-required canonical record lock. The head is checked before and after
the cache write; election, generation and SQLite writer identity are checked
before commit and freshly after commit, before changing live rows, sequence or
notifications. Head refresh and roster reconciliation likewise stage their
results until the final fresh owner/election fence succeeds. A rejected
post-commit publication can retain durable cache data for a newly qualified
owner, but does not expose it live or replay a canonical mutation.
A same-store/same-head revision must advance;
an identical same-revision replay is idempotent, conflicting content or a lower
revision is refused. A different store for the same head is refused: this
contract does not register source changes or infer them from time.
Bounded source metadata retains the store, revision and content digest for up to
256 source sessions per current worktree. A canonical head returning to an older
session therefore cannot admit a regressed revision or silently substitute its
store. Reaching the bound refuses another source instead of evicting a fence.
Removing the worktree prunes both its observation and source metadata.

The separate `worktrees_agenda_cache` table in the current roster database
persists the committed latest observation before notification. It is only a
projection cache, never the canonical agenda store. Entries are bounded to the
actual roster. A new owner exposes no old cached observation until it publishes
its current roster and requalifies the exact current record/head. Reconnection
then receives a complete snapshot without scanning agenda files.

Committed in-process tracking writes invalidate affected agenda heads through
the existing record cache; the next selected read/update qualifies the current
record. Roster publication also reconciles heads and removal, including
out-of-process changes. Heartbeats do not poll files. A source should retry only
publication of its committed latest body after notification loss, never repeat
the canonical mutation. Corrupt/unavailable agenda cache state is explicit on
agenda reads and feeds without breaking ordinary roster publication.
An admitted predecessor's cached errors and unready results use its in-memory
read/generation fence, not the successor's SQLite writer identity. Writer
acquisition alone therefore cannot erase a cached failure before serving
handoff; publication and cache refresh keep their independent writer fences.
If writer authority is lost during a read that already began refreshing, the
runtime restores pending work and retries only the admitted cached read.
Uncommitted refresh results never change rows, sequence or notifications.
Activation/schema failures are resident-wide. Reconciliation failures are
project-local and remain unavailable until that same project successfully
reconciles, including across same-generation drain/reactivation. Another
project's success cannot clear the failure or expose the failed project's old
body. A new generation resets these failure latches but must requalify its own
current project rosters and committed cache data.
Agenda cache initialization, observer registration and the fresh owner fence
must all succeed before agenda feed admission reopens. Any initialization
failure closes already-held agenda streams; known agenda initialization faults
leave the resident's ordinary roster service available. Unexpected defects
propagate after stream closure rather than becoming a successful initialization.
A repaired same-generation retry can admit a fresh snapshot without resetting
sequence, source-store or project-failure fences.

## Evidence and limits

`tests/test_agenda_runtime.py` and `tests/test_agenda_cli.py` exercise bounded
schema/parsing, ownership and head switches, commit rollback, duplicate/stale
writes, cached restart requalification, one-slot coalescing, the subscriber cap,
real synthetic held control sockets and the owning main parser. Run them
through the repository's contained plugin runner and bounded test supervisor.
These controls do not qualify native installed rollout, live agent changes,
Manager paint latency or whole-effort completion.

See [Agent Worktrees](../README.md) and the
[engine/Picker contract](engine-picker-contract.md) for the owning roster and
installation boundaries.
