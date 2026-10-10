# `agent-dispatch attention` -- what needs the operator

One ordered queue of everything that needs the operator, across sources, with
CLI parity for any UI that renders it. It is ordered and deduplicated here,
not in each client, and a source that fails reads as `degraded`, never as an
empty "all clear".

```bash
agent-dispatch attention [--json] [--source NAME ...] [--include-remote]
agent-dispatch attention next [--after CURSOR] [--json] [--source NAME ...] [--include-remote]
agent-dispatch attention dismiss ID [--until TIME | --forever]
agent-dispatch attention undismiss ID
agent-dispatch attention dismissed [--json]
agent-dispatch attention source add NAME [--timeout S] -- ARGV...
agent-dispatch attention source remove NAME
agent-dispatch attention source list
```

Exit 0 for any read (the `status` field carries the outcome), 2 for a usage
error: an unknown `--source` name, or one whose plugin isn't installed here
(nothing is read), a cursor this command didn't issue, or a registration that
isn't valid. `--include-remote` also reads the transcripts of bridge sessions
on a remote target (an SSH read each; see `bridge` below).

## Sources

- **`dispatch`** (built in): this coordinator's tasks. A task that awaits an
  operator answer (`awaiting_steer`) is `awaiting_input` and carries the card's
  `request_input` form as `input` (submit it with `agent-dispatch steer
  submit`; its action shows the card). An operator hold (`hold_reason`) is
  `blocked`; a self-tracked completion claim awaiting confirmation
  (`submitted`) is `review`, even under a stale steering flag (a concluded task
  can't be steered), while one with an `evaluator_ref` waits on its evaluator
  and is no item. Those actions show the task itself. `completed` is never an item.
  A handoff baton (labelled `handoff`, or from `context-handoff`) is completed
  by its pickup, so a `submitted` one is spent and never a `review`; one that no
  session has claimed for longer than
  `AGENT_DISPATCH_ATTENTION_HANDOFF_AFTER_SECS` (strictly greater, default 600;
  `0` turns it off) is `stalled`: its predecessor already stopped, so the work
  waits on a successor -- but only while its worktree still does. The baton's
  worktree (`target_worktree`, else its `worktree` affinity) keeps its own
  handoff ledger (`agent-worktrees head-session --worktree <id> --json`), the
  authority for which handoff it waits on; a baton that ledger no longer lists
  was picked up, replaced by a later handoff or cancelled (or was only saved,
  never handed over), so it is no item. Each worktree's ledger is read once,
  four at a time, alongside the lane reads; a ledger that can't be read for
  certain in the read's backlog budget (no agent-worktrees, an untracked
  worktree, a failed reply, any malformed entry) keeps its batons' items. A
  stalled baton's actions are `resume` (`agent-worktrees
  embody --worktree-id <wt> --seed <seed>`, a successor in its worktree taking
  the handoff over through context-handoff's seed), when its worktree is known,
  then `abandon` (`agent-dispatch abandon <id> --permit --reason ...`), then
  `show`. Abandoning a baton also cancels its entry in that worktree's ledger
  (`agent-worktrees cancel-handoff`, which leaves an entry a successor is
  already taking over untouched).
  One item per task, the worst condition winning. A lane that isn't draining
  (the coordinator's `backlog`) is one `stalled` item per repo (`entity: queue`):
  its oldest queued task waited longer than
  `AGENT_DISPATCH_ATTENTION_QUEUED_AFTER_SECS`, or a **live** owner made no
  progress for longer than `AGENT_DISPATCH_ATTENTION_HELD_LIVE_AFTER_SECS`
  (both strictly greater, default 1800; `0` turns that half off). Held tasks
  whose owner is `unknown` or `gone` never count. A read is capped at 5000 open tasks; one that hits the cap reports `uncertain` (the queue reads `partial`), never a complete `ok`. Each active lane's backlog is one coordinator read, started only while it can still finish inside the source's deadline; a lane it doesn't reach, or whose read fails, counts toward `uncertain` too, so backlog probing never fails the source's task items.
- **`bridge`** (built in, when agent-bridge is installed; otherwise `disabled`):
  sessions parked on the operator. Candidates are the bridge-managed sessions
  (`agent-bridge --json sessions`, not stopped or ended) and the live registered
  interactive ones (`agent-bridge --json live-sessions list`), keyed by their
  logical reference `wt:<machine>/<project>/<worktree-id>`: a session in both
  registries, or a successor after a restart, takeover or handoff, is one item
  that keeps its place, and its `show` action (`agent-bridge result <session>`)
  names the session heading the worktree now. Each is read through `agent-bridge
  --json attention <worktree-id>...` (the bridge's own attention evaluator):
  `input_required`, `permission_required` and `policy_required` are
  `awaiting_input`, `failed` and `unreachable` are `failed` (all `reported`);
  settled reasons (`turn_complete`, `turn_cancelled`, `stopped`, `ended`) are no
  item; `contract_changed`, a reason this version doesn't know, a request that
  predates the bridge's restart, an unreadable session and an older bridge
  (`unsupported`) count toward `uncertain`. A bridge-managed session's
  transcript presence is read too (`agent-bridge --json presence`): `awaiting_input`
  is an item (`scanned` or `heuristic`), `unknown` counts toward `uncertain`, and
  an item from both is one item at the stronger confidence, its reason naming
  both. A transcript on a remote target is read only with `--include-remote`. A
  session no managed worktree hosts has no durable reference yet and counts
  toward `uncertain`. If either listing fails, the source fails. Deadline 30 s.
- **`pr`** (built in, when agent-worktrees is installed; otherwise `disabled`):
  every PR agent-worktrees tracks, in every adopted project
  (`agent-worktrees claims find pr --state all --json`), read once each by its
  project, repository and number (`agent-worktrees -p <project> pr bar
  <owner/name> <n> --json`, ten at a time). An `OPEN` PR whose merge bar
  `failed` is a `failed` item keyed `<authority>/<owner/name>#<n>` (the same
  `owner/name#<n>` under two authorities is two items), with `show` (`pr bar`)
  and, on github.com, `open` (`gh pr view --web`) actions. The live state
  decides: a PR whose record still says open but which closed is no item, and a
  reopened one is. A record whose PR merged is skipped unread (a merge is
  terminal). A bar read that is `unknown` or fails, and a record without an
  authority, an `owner/name` or a number, count toward `uncertain`; a project
  whose PRs can't be enumerated fails the source. Deadline 90 s: each bar read
  makes several provider calls.
- **Command sources**: a command registered on this machine
  (`attention source add`, stored beside the coordinator's install as
  `attention-sources.json`) under a name that is its identity (adding a name that is already registered replaces it):
  `[a-z0-9-]+`, and not a built-in name (`dispatch`, `bridge`, `pr`). A rejected
  registration is not a source: it is listed in `config_errors[]`, and it makes
  a read that includes it `degraded`. `source add` pins a bare command to its
  absolute path, a registration whose command isn't absolute is rejected, and
  the command runs from the registry's directory: nothing in the checkout a read
  runs in can supply or redirect it (pass file arguments as absolute paths).

Each source runs under its own timeout (a reader that hangs past it is abandoned, never keeping the command alive) (a command's own `--timeout`, default
20 s, at most 120 s), as a contained process tree. A timeout, a crash or any
contract violation makes that source `failed` with a one-line `error`.

## The envelope (`--json`)

```json
{
  "schema": 1,
  "status": "clear | attention | partial | degraded",
  "read_at": "<ISO-8601 UTC>",
  "selected": null,
  "sources": [{"name": "dispatch", "status": "ok | failed | uncertain | disabled",
               "uncertain": 0, "items": 0, "read_at": "<ISO-8601 UTC>", "error": "<only when failed>"}],
  "config_errors": [{"name": "<registered name>", "error": "<one line>"}],
  "items": ["<item>, in queue order"],
  "dismissed": ["<item>, with its dismissal: {mode, at, until?}"]
}
```

`status` is the first that applies: `degraded` (a selected source `failed`, or
a selected name's registration was rejected), `partial` (a source couldn't
classify `uncertain` of its entities), `attention` (items), `clear`. A
selective read (`--source`) lists only those sources and their config errors,
and carries `"selected": [...]` (`null` for a full read), so a scoped `clear`
means "nothing needs you *from these sources*".

`attention next` returns the same envelope with `item` (one item or `null`)
and `cursor` in place of `items`. The cursor is opaque but carries the queue
position of the item shown, so the next call returns the first item strictly
after it even when that item was resolved meanwhile; it wraps to the top.

## The item

| Field | Meaning |
|---|---|
| `schema` | `1`, on every item |
| `id` | `<source>:<entity>:<entity_ref>`; unique per entity, the order's final tie-breaker |
| `entity` | `task`, `session`, `pr`, `queue`, or a source's own `x.<source>.<kind>` (`<kind>` matches `[a-z0-9_-]+`, so the `id` splits back unambiguously) |
| `entity_ref` | the durable reference within its kind: a task id, a repo for a `queue`, `wt:<machine>/<project>/<worktree-id>` for a `session`, `<authority>/<owner/name>#<n>` for a `pr` |
| `lifecycle_state` | the owner's state (`started`, `submitted`, ...), or `null` |
| `display_state` | `failed`, `stalled`, `awaiting_input`, `blocked`, `review` (worst first) |
| `severity` | the rank of `display_state` (0 = `failed`) |
| `reason` | one line, at most 200 characters |
| `created_at`, `updated_at` | when the condition began / was last observed, as canonical UTC (`YYYY-MM-DDTHH:MM:SS+00:00`); a command source may send any ISO-8601 spelling with an offset, which is normalized |
| `confidence` | `reported`, `scanned` or `heuristic` |
| `actions[]` | `{verb, argv}` that run as-is (a dispatch action carries the read's own `--url`/`--shared`, or `--shared` when the default path failed over to the shared coordinator; never a token, so a read authenticated only by a `--token` argument, or one that went over an SSH failover (no flag pins that peer), offers no dispatch actions); the first is the default. `verb` is `show` (read-only), `resume` (mutating), `open` (external viewer), `abandon` (mutating, retires the entity) or `dismiss` (hides the item on this machine; see *Dismissals*), or a source's own `x.<source>.<verb>`. Every queued item's last action is `dismiss` (`agent-dispatch attention dismiss <id>`; a `dispatch` item's carries the read's own `--url`/`--shared`, and none is offered when its other actions aren't). A client may run `show` without confirmation only for a built-in source's item; every action of a command source is operator-initiated |
| `source` | the source that produced it |
| `input` | optional, dispatch steering items only: the steering card's `request_input` field list, exactly as `--request-input` produces it -- `[{name, type, options?, allow_other?, show_when?}]`, `type` one of `text`, `textarea`, `choice`, `multichoice` (`options`, a non-empty string list, and `allow_other` on a `choice` or `multichoice` only; `show_when` is `{field, equals}`) -- answered with `agent-dispatch steer submit`. A card whose form isn't that shape has no `input` (its `card show` action still reaches it); a command source can't set it |
| `also[]` | lower-ranked items for the same entity from other sources (aggregator-owned) |

**Order:** severity, then `created_at` (oldest first), then `id`.
**Dedupe:** one item per `(entity, entity_ref)`; the rest become its `also[]`,
and the kept item's `created_at` is the earliest of them.

**First-observed times.** A source that only knows *that* something needs the
operator omits `created_at`; it is kept per source in a machine-local store
(`attention-observed.json`), so repeated reads keep the same order. A time is
cleared only when that same source reads `ok` without the item; a `failed` or
`uncertain` read keeps it, so an outage never reorders an unchanged queue.
`dispatch` times are also kept per coordinator: a read that reached another
queue (`--url`, `--shared`, a silent failover to the shared coordinator, or an
SSH failover, keyed by the peer machine) never clears this machine's own times,
nor they its.
Concurrent reads are ordered by a read number each takes when it starts --
persisted in the first-observed store and strictly increasing across processes
(no clock: same-instant reads and clock rollback can't tie or reorder them) --
so a slower, older read that finishes last can't re-add a time a newer one cleared.

## Dismissals

`agent-dispatch attention dismiss <id>` hides an item the operator has decided
to leave alone; it moves from `items` to `dismissed` (each carrying its
`dismissal`: `mode`, `at`, and `until` for a snooze), so nothing is hidden
silently, and a queue whose every item is dismissed reads `clear`. Three modes:

- **until it changes** (the default): the item returns when its
  `display_state`, `lifecycle_state` or `reason` changes. Numbers in a reason
  (a waiting time, a count) are ignored, so a condition that only ages stays
  dismissed; `updated_at` isn't compared (some sources stamp it with the read
  time). The command reads the item's own source first, so it records the
  condition the operator saw, and refuses (exit 2) an item that isn't queued.
- **`--until TIME`** (ISO-8601 with an offset, in the future): a snooze; the
  item returns after that time even if nothing changed.
- **`--forever`**: until `attention undismiss <id>`, whatever changes.

A dismissal that no longer applies is removed by the read that finds so, and
one whose item an `ok` read of its source no longer has ends with the
condition (a later recurrence is new); a `failed` or `uncertain` read proves
nothing. `attention dismissed [--json]` lists them. They are this machine's
own, keyed by item `id`, in `attention-dismissed.json` beside the
coordinator's install, never in a repository; a store that can't be read
hides nothing and is reported in `config_errors[]` (as `*dismissals`).

## Writing a command source

The command prints one JSON document:

```json
{"schema": 1, "items": [...], "status": "ok | failed | uncertain", "uncertain": 0,
 "error": "<one line, when failed>", "read_at": "<ISO-8601 UTC>"}
```

Only `schema` and `items` are required. No `status` means `ok` (or `uncertain`
when `uncertain` > 0); no `read_at` means the receipt time. An item is the item
schema with `source`, `id`, `created_at` and `updated_at` optional. The
aggregator canonicalizes `entity` first (a bare custom kind `login` becomes
`x.<name>.login`; another source's `x.` prefix is rejected), rejects a `source`
or `id` that isn't its own, then sets `source`, derives `id`, and fills the
times before validating the item. A non-zero exit, a timeout, more than
1,048,576 characters of output (stdout and stderr together; the command is
stopped), non-JSON output,
a `schema` other than `1`, a missing `items[]`, a status that contradicts
`uncertain`, a non-empty `also[]`, two items for one entity, or any invalid item
makes the source `failed`.
