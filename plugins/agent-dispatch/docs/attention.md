# `agent-dispatch attention` -- what needs the operator

One ordered queue of everything that needs the operator, across sources, with
CLI parity for any UI that renders it. It is ordered and deduplicated here,
not in each client, and a source that fails reads as `degraded`, never as an
empty "all clear".

```bash
agent-dispatch attention [--json] [--source NAME ...]
agent-dispatch attention next [--after CURSOR] [--json] [--source NAME ...]
agent-dispatch attention source add NAME [--timeout S] -- ARGV...
agent-dispatch attention source remove NAME
agent-dispatch attention source list
```

Exit 0 for any read (the `status` field carries the outcome), 2 for a usage
error: an unknown `--source` name (nothing is read), a cursor this command
didn't issue, or a registration that isn't valid.

## Sources

- **`dispatch`** (built in): this coordinator's tasks. A task that awaits an
  operator answer (`awaiting_steer`) is `awaiting_input` and carries the card's
  `request_input` form as `input` (submit it with `agent-dispatch steer
  submit`; its action shows the card). An operator hold (`hold_reason`) is
  `blocked`; a self-tracked completion claim awaiting confirmation
  (`submitted`) is `review`, even under a stale steering flag (a concluded task
  can't be steered), while one with an `evaluator_ref` waits on its evaluator
  and is no item. Those actions show the task itself. `completed` is never an item.
  One item per task, the worst condition winning. A lane that isn't draining
  (the coordinator's `backlog`) is one `stalled` item per repo (`entity: queue`):
  its oldest queued task waited longer than
  `AGENT_DISPATCH_ATTENTION_QUEUED_AFTER_SECS`, or a **live** owner made no
  progress for longer than `AGENT_DISPATCH_ATTENTION_HELD_LIVE_AFTER_SECS`
  (both strictly greater, default 1800; `0` turns that half off). Held tasks
  whose owner is `unknown` or `gone` never count. A read is capped at 5000 open tasks; one that hits the cap reports `uncertain` (the queue reads `partial`), never a complete `ok`. Each active lane's backlog is one coordinator read, started only while it can still finish inside the source's deadline; a lane it doesn't reach, or whose read fails, counts toward `uncertain` too, so backlog probing never fails the source's task items.
- **Command sources**: a command registered on this machine
  (`attention source add`, stored beside the coordinator's install as
  `attention-sources.json`) under a name that is its identity: unique,
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
  "items": ["<item>, in queue order"]
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
| `entity_ref` | the durable reference within its kind (a task id, ...) |
| `lifecycle_state` | the owner's state (`started`, `submitted`, ...), or `null` |
| `display_state` | `failed`, `stalled`, `awaiting_input`, `blocked`, `review` (worst first) |
| `severity` | the rank of `display_state` (0 = `failed`) |
| `reason` | one line, at most 200 characters |
| `created_at`, `updated_at` | when the condition began / was last observed, as canonical UTC (`YYYY-MM-DDTHH:MM:SS+00:00`); a command source may send any ISO-8601 spelling with an offset, which is normalized |
| `confidence` | `reported`, `scanned` or `heuristic` |
| `actions[]` | `{verb, argv}` that run as-is (a dispatch action carries the read's own `--url`/`--shared`, or `--shared` when the default path failed over to the shared coordinator; never a token, so a read authenticated only by a `--token` argument offers no dispatch actions); the first is the default. `verb` is `show` (read-only), `resume` (mutating) or `open` (external viewer), or a source's own `x.<source>.<verb>`. A client may run `show` without confirmation only for a built-in source's item; every action of a command source is operator-initiated |
| `source` | the source that produced it |
| `input` | optional: the form an answer needs (the steering card's `request_input` field list, or an object) |
| `also[]` | lower-ranked items for the same entity from other sources (aggregator-owned) |

**Order:** severity, then `created_at` (oldest first), then `id`.
**Dedupe:** one item per `(entity, entity_ref)`; the rest become its `also[]`,
and the kept item's `created_at` is the earliest of them.

**First-observed times.** A source that only knows *that* something needs the
operator omits `created_at`; it is kept per source in a machine-local store
(`attention-observed.json`), so repeated reads keep the same order. A time is
cleared only when that same source reads `ok` without the item; a `failed` or
`uncertain` read keeps it, so an outage never reorders an unchanged queue.
Concurrent reads are ordered by a nanosecond token taken when each starts, so a
slower, older read that finishes last can't re-add a time a newer one cleared.

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
times before validating the item. A non-zero exit, a timeout, non-JSON output,
a `schema` other than `1`, a missing `items[]`, a status that contradicts
`uncertain`, a non-empty `also[]`, two items for one entity, or any invalid item
makes the source `failed`.
