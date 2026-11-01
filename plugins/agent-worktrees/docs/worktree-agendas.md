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

The new contract is additive. Existing version-1/2 roster contracts, rows and
feeds retain their meanings. Older residents do not support the agenda actions;
callers must report unsupported delivery, not replay agenda mutations or infer
support from an installed package version. No existing contract is retired.

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
There are at most 32 held agenda subscriptions, accounted in the existing
monitor drain and retirement path; no additional listener or daemon is created.

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
