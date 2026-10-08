# Operator attention contract -- the item and the aggregator

Part of the [Operator Attention Contract](README.md) effort: the versioned item, the order, deduplication, source results and the aggregate envelope (Phase 1).

- `attention_contract.py`: the item, versioned (`schema: 1`):

  | Field | Meaning |
  |---|---|
  | `schema` | the item's own version, `1`; carried on every item, separately from the envelope's |
  | `id` | stable per item: `<source>:<entity>:<entity_ref>`, e.g. `dispatch:task:<task-id>`, `bridge:session:wt:<machine>/<project>/<worktree-id>`, `pr:pr:<authority>/<owner/name>#<n>` -- unique per entity, so it's the order's deterministic final tie-breaker (and the last component of the `next` cursor's position; the id alone is never a cursor) |
  | `entity` | a shared kind -- `task` \| `session` \| `pr` \| `queue`, dedupable across sources -- or a pluggable source's own kind, namespaced by that source as `x.<source>.<kind>`, where `<kind>` matches `[a-z0-9_-]+` (so neither it nor the source name can contain `:`, and `id` splits back unambiguously) (two external adapters' `login` items never collide) |
  | `entity_ref` | the canonical, **durable** reference within its kind: a task id; a PR as `<authority>/<owner/name>#<n>`, where `<authority>` is the canonical provider authority agent-worktrees resolves for its repository: the provider's authority endpoint with its scheme, credentials, default port and trailing slash dropped and its host lowercased, but its path kept (e.g. `github.com`, `ghes.example.com`, `dev.azure.com/<org>`, `gitea.example.com/api/v1`), so a provider whose organization lives in the path never collapses two organizations' `project/repo#<n>` -- never a raw URL, so two spellings of one PR are one key, while the same `owner/name#<n>` under two authorities stays two; a queue as its canonical repo; a session as its logical delegate reference (agent-bridge's identity model), never a bridge escrow `session_id`, an ACP session id or a live registration, which a restart, takeover or handoff replaces: `wt:<machine>/<project>/<worktree-id>` when a managed worktree hosts it (the project too: one worktree id can exist in two projects). agent-bridge doesn't yet expose a compact logical reference for any other session, and its delegation contract forbids inferring one from an ACP session id, so a session no managed worktree hosts has no `entity_ref` yet: it counts toward its source's `uncertain` until agent-bridge projects one |
  | `lifecycle_state` | the owner's own state (`started`, `submitted`, `live`, `open`, ...); `null` for an entity with no owner lifecycle (a `queue`) |
  | `display_state` | `failed` \| `stalled` \| `awaiting_input` \| `blocked` \| `review` |
  | `severity` | derived from `display_state`: `failed` > `stalled` > `awaiting_input` > `blocked` > `review` |
  | `reason` | one line, ≤ 200 chars |
  | `created_at`, `updated_at` | when the condition began / was last observed. **Wire format:** ISO-8601 with an explicit offset; the aggregator normalizes every source's value to canonical UTC seconds (`YYYY-MM-DDTHH:MM:SS+00:00`, the spelling Python's `isoformat` gives), so offsets and precisions from different producers sort chronologically, and an offsetless or unparseable value makes the item malformed (its source `failed`). A source that only knows when it observed something (e.g. a `pr bar` read) gets `created_at` from the aggregator's persisted first-observed time, kept **per source** for `(source, entity, entity_ref, display_state)`, so repeated reads keep the same order. Each source's time is cleared only by proof from that same source that the condition ended: a read of it with `status: ok` that no longer contains the item. An item missing from a `failed` or `uncertain` read, or from a source that is now `disabled`, keeps its time -- an outage proves nothing, so recovery doesn't reorder an unchanged queue -- and one source's `ok` omission never clears another source's evidence for the same entity. A deduplicated item's `created_at` is the earliest of its *currently reporting* sources' times: when the source with the earliest time ends its evidence (an `ok` read without the item), the item's age falls back to the earliest remaining source's time. That is deliberate -- there is no aggregate store that outlives every source's own evidence |
  | `confidence` | `reported` \| `scanned` \| `heuristic` (presence's vocabulary) |
  | `actions[]` | `{verb, argv}`, in order (the first is the item's default): sanctioned commands that run **as-is**, with no placeholder to fill. So a built-in source's action reaches the endpoint its read did: the `dispatch` source's argv carries the read's `--url`/`--shared` (never `--token`, which the operator's environment supplies), and an action that would reach another coordinator is a defect. A read authenticated only by a `--token` argument (no environment credential), or one that reached its coordinator over an SSH failover (no flag pins that peer, so after the local coordinator recovers a bare action would read another queue), therefore carries no dispatch actions: none could reach the read's coordinator as-is, and an action never carries a secret. `argv` is a non-empty string array. `verb` is the machine-readable operation, never a display label (a client derives its own label from it), from a closed set: `show` (read-only: prints the entity's detail; a client may run it without confirmation **only** for an item from a built-in source, whose argv the aggregator generated), `resume` (continues or re-attaches the entity's owner -- mutating, so operator-initiated), `open` (opens the entity in an external viewer, e.g. a browser). A source extends it only under its own prefix, `x.<source>.<verb>`; a client treats a verb it doesn't know, and **every** action of an external (command) source -- `show` included, since its argv is the command's own -- as operator-initiated, never auto-run. A client runs a command source's action from that source's registry directory (the directory of the `file` that `attention source list` reports, where its command itself runs), never from its own working directory: a relative executable or argument (`./helper`, `python helper.py`) can then never resolve to checkout-controlled code when the client is opened in an untrusted checkout. Any other `verb` makes the item invalid (and its source `failed`). E.g. `{"verb": "show", "argv": ["agent-dispatch", "card", "show", "<task-id>"]}` (a local read; after `--url <u>` it is `["agent-dispatch", "--url", "<u>", "card", "show", "<task-id>"]`), `{"verb": "resume", "argv": ["agent-bridge", "resume", "<session-id>"]}`, `{"verb": "open", "argv": ["gh", "pr", "view", "<n>", "--repo", "<owner/name>", "--web"]}` for a `github.com` PR. An `open` action is provider-specific: the `pr` adapter emits one only when it has a command that opens that provider's PR (for another provider, or a host its tooling can't reach, it omits `open` rather than sanctioning an argv that would fail). An answer that needs operator input isn't an action: the item carries the card's own `request_input` form spec (`input`), and the client submits it with `agent-dispatch steer submit` once filled |
  | `source` | the adapter that produced it |
  | `input` | optional, **dispatch steering items only** (`source: dispatch`, `awaiting_input`): the card's `request_input` field list when resolving it needs an operator's answer, submitted with `agent-dispatch steer submit`. Its shape is exactly what agent-dispatch's `--request-input` produces, `[{name, type, options?, allow_other?, show_when?}]`: `name` a non-empty string, `type` one of `text`, `textarea`, `choice`, `multichoice`; `options` (a non-empty string list) and `allow_other` (a boolean) on a `choice`/`multichoice` only; `show_when` an `{field, equals}` pair of strings; no other keys. The producer's semantic rules hold too: names match `[a-zA-Z][a-zA-Z0-9_-]*` and are unique within the form (an answer is keyed by name), and a `show_when` references another, unconditional `choice` field whose `options` contain `equals`. One owner-neutral validator (agent-dispatch's `steering_fields`) defines all of this, and both the `--request-input` parser and the attention contract use it. Any other shape is malformed: the dispatch adapter omits `input` for such a card (its `card show` action still reaches it), so every client renders and answers one form. That is its only submission path, so a command source can't set it (an item that does is malformed, and its source `failed`); a command source's own answer flow is one of its `x.<source>.<verb>` actions |
  | `also[]` | **aggregator-owned**: the lower-ranked items deduplicated into this one (each a full item, in queue order); empty when none. A source never fills it -- an adapter or command item with a non-empty `also[]` is malformed, and that source is `failed` -- so a nested item can't slip past the identity stamping and the one-item-per-entity check |

- **Display, not lifecycle.** A `live`/`started` entity waiting on a human
  surfaces. `blocked` counts only when nothing inbound can still resolve it.
- **Order:** severity (worst first), then `created_at` (oldest first), then
  `id`, so the order is fully deterministic.
- **Dedupe** on `(entity, entity_ref)` — the canonical kind plus its canonical
  reference, never the bare reference (a task id and a session id can share a
  string; a source's own `x.<source>.<kind>` keeps its references to itself).
  The winner, and the order of `also[]`, use the queue's own precedence --
  severity, then `created_at`, then `id` -- so identical reads give identical
  results however the adapters' timeouts interleave: keep the highest severity; the others become `also[]` on the kept item,
  so nothing is silently dropped.
- **Source result:** each adapter returns `{items[], status, error?, uncertain, read_at}`,
  with **at most one item per `(entity, entity_ref)`**. An adapter with several
  pieces of evidence for one entity (the bridge's represented `permission_required`
  and its transcript presence `awaiting_input` for the same session) coalesces them
  before returning: it keeps the highest severity, then the strongest confidence
  (`reported` > `scanned` > `heuristic`), and the kept item's `reason` names the rest. The first-observed key is the *kept* condition's `display_state`: a lower condition masked by a higher one isn't displayed, so it isn't timed either, and its age starts when it becomes the item (e.g. a held task that also asks a question is an `awaiting_input` item; once answered, it becomes a `blocked` item first seen at that read). The order only ever reflects what the queue showed. So `(source, entity, entity_ref)` -- and with it `id` and the
  first-observed key -- is unique within a read, and cross-source dedupe never
  meets two equal ids. A source result (a command's too) that carries two items
  for one entity is malformed, and that source is `failed`. The `status` is one of `ok` (fully read), `failed` (couldn't be read), `uncertain`
  (read, but incompletely: `uncertain` counts the gaps -- each entity that
  couldn't be classified, e.g. presence `unknown` or a `pr bar` exit 12, and
  each part of the read that couldn't be done, e.g. a truncated listing or an
  unread lane, whose omitted entities are unknown -- so it is a lower bound on
  what was missed, never an entity count, and any non-zero value means the
  read is incomplete) or `disabled` (not installed). The aggregate's
  `status` is the first that applies, in this precedence: `degraded` when any
  enabled source `failed` or `config_errors[]` is non-empty; `partial` when any is `uncertain` (with the counts);
  `attention` when there are items; `clear` otherwise (every enabled source `ok`,
  no items). One read gives one status, however its sources fail -- so neither a failed nor a partly unreadable source can read
  as "all clear". `disabled` sources never change a full read. A selective read that **names** a `disabled` source is a usage error (exit 2, nothing read), like an unknown name: a scoped `clear` must mean the named sources were read.
- **Aggregate response** (`attention --json`), versioned on its own -- never a
  bare item list, so a client can always tell `clear` from `degraded`:

  ```json
  {
    "schema": 1,
    "status": "clear | attention | partial | degraded",
    "read_at": "<ISO-8601 UTC>",
    "selected": null,
    "sources": [
      {"name": "dispatch", "status": "ok | failed | uncertain | disabled",
       "error": "<one line; only when failed>", "uncertain": 0,
       "items": 0, "read_at": "<ISO-8601 UTC>"}
    ],
    "config_errors": [{"name": "<registered name>", "error": "<one line>"}],
    "items": ["<item>, in queue order"]
  }
  ```

  `sources[]` lists every discovered or validly configured source, `disabled` ones
  included, sorted by `name`; `uncertain` and `items` are counts. A **selective
  read** (`--source <name>...`) is scoped to the named sources: `sources[]` lists
  only them, `items` and the aggregate `status` cover only them, and the envelope
  carries `"selected": ["<name>", ...]` (`null` for a full read). `config_errors[]`
  is scoped the same way: a full read lists every rejected registration, a
  selective read only those whose `name` is selected, so a malformed registration
  of an unselected name doesn't degrade `--source dispatch`, while selecting the
  name a rejected registration claimed reports why it didn't run. It's sorted by
  `name`, then `error`. A scoped
  `clear` therefore says "nothing needs you *from these sources*", and a client
  can tell it from a full read. `attention next`
  returns the same envelope with `item` (one item, or `null` when the queue is
    empty) and `cursor` in place of `items`. **Schema evolution** (envelope and
    item alike): adding an **optional** field -- one a consumer may find absent --
    is compatible and keeps `schema`; renaming, removing or retyping a field, or
    adding one consumers must rely on, bumps it.
- **Only discovered sources take part** (standalone-first,
  [a-la-carte independence](../../../docs/patterns/a-la-carte-independence.md)):
  an optional sibling that isn't installed (agent-bridge, agent-worktrees) is
  listed `disabled` and stays dark — it never degrades the result. An
  agent-dispatch-only install is a complete, non-degraded queue of its own items.
- Unit tests: ordering, dedupe, degraded vs empty, item and envelope schema round-trip.
