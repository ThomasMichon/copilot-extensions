# Operator attention contract -- validation matrix

Part of the [Operator Attention Contract](README.md) effort: what each test tier must show.

- Unit: contract, ordering, dedupe — cross-source dedupe of one entity (two
  sources naming the same PR), cross-kind non-collision (a task and a session
  with the same id), and an equal-severity tie resolved the same way in any
  adapter order — repeated reads keep a source's first-observed `created_at`,
  including across processes (a fresh aggregator instance opening the same
  persisted first-observed store returns the same `created_at`, as two separate
  CLI invocations do),
  and so does an outage and recovery (the source reads `failed`, then `uncertain`
  without the item, then `ok` with it: the same `created_at` throughout), while
  an `ok` read without the item followed by its return gives a new one; two
  sources naming one PR, where one source's `ok` read drops it while the other
  still reports it, keep the item, with the remaining source's own `created_at` — and
  each aggregate status (`clear`, `attention`, `degraded`, `partial`, and a
  mixed failed-plus-uncertain read resolving to `degraded`) from fixtures, with `disabled` sources leaving it unchanged — degraded vs empty vs disabled, each adapter on fixtures.
- Unit, the envelope: `attention --json` and `attention next --json` match
  the exact documented shape (keys, types, `sources[]` order) and round-trip,
  including an item with `lifecycle_state: null` (a queue), and every item
  carries its own `schema: 1`;
  `clear` and `degraded` with zero items stay distinguishable; a `--source
  dispatch` read lists only `dispatch` in `sources[]`, carries `"selected":
  ["dispatch"]`, and its status ignores a failing unselected source; with a
  malformed registration named `foo`, `--source dispatch` carries an empty
  `config_errors[]` and keeps its status, while `--source foo` lists `foo`'s
  error and is `degraded`. Actions: a `verb` outside `show | resume | open` and
  not under the source's own `x.<source>.` prefix (including another source's
  prefix) is an invalid item, and so is an empty `argv`.
- Unit, the dispatch adapter: a `submitted` task is a `review` item and a
  `completed` one isn't; `stalled` at exactly the threshold isn't an item and one
  second over is; held tasks with an `unknown` or `gone` owner never count; a threshold of `0` turns its half off; a held task that also asks yields one `awaiting_input` item, then (once answered) a `blocked` item first seen at that read; a `submitted` task with a stale `awaiting_steer` flag is a `review` item, and one with an `evaluator_ref` is no item; a read authenticated only by `--token` carries no dispatch actions; a read through `--url <u>` or `--shared` yields actions carrying the same flag and never the token.
- Unit, command sources: a partial read (`status: uncertain`, `uncertain:
  2`) makes the aggregate `partial`; `{"schema": 1, "items": [...]}` alone reads
  as `ok`; a missing `schema` and `schema: 2` are each `failed`; a self-reported
  `status: failed` without an `error` gets the aggregator's fallback error; each
  contradiction in the failure contract is `failed`.
- Unit, the bridge adapter: a bridge-managed session parked on
  `permission_required` and one parked on `policy_required` each yield an
  `awaiting_input` item through `agent-bridge --json attention`, and so does a
  registered interactive one; an attention read that fails counts as
  `uncertain`. Every `AttentionReason` value maps as listed
  (`policy_required` and `unreachable` are items; `contract_changed` and an unknown
  reason count as `uncertain`); a session with both a represented
  `permission_required` and transcript `awaiting_input` yields one `reported`
  item whose reason names both, in any read order; a command source returning two
  items for one entity is `failed`; a parked session found only in the
  bridge-managed registry and one found only in the live-session registry each
  yield an item, one present in both yields a single item, and a failed listing
  of either registry makes the bridge source `failed`. Identity: a parked
  worktree session replaced by a successor (a CLI restart, a takeover, a bridge
  handoff) keeps its `entity_ref`, `id` and `created_at`, while its actions
  name the successor; two worktrees never share one, including one worktree id in two projects; no item's `id` contains a bridge escrow or ACP session id; and a session no managed worktree hosts (bridge-owned or interactive) counts as `uncertain`, not as an item.
- Unit, external identity: a command source registered as `dispatch` (or as
  a duplicate name) is rejected into `config_errors[]` -- never a second
  `sources[]` entry under that name -- and the aggregate is `degraded`; an item stating another `source` or a foreign
  `id` is invalid; an item omitting both is stamped and then validated; a
  custom `entity: "login"` and `entity: "x.<own>.login"` both canonicalize to
  `x.<own>.login` with the same `id` (`<own>:x.<own>.login:<ref>`), while
  `x.<other>.login` is invalid; an item
  omitting `created_at` gets the same first-observed time on two separate reads
  (two CLI invocations), and a new one after an `ok` read that dropped it; a
  stamped item's `id` and first-observed key are its own; an item arriving with a
  non-empty `also[]` fails its source. `--source bridgge` (an
  unknown name) exits 2 without reading anything, and `attention next --json
  --source dispatch` parses with the flags after `next`.
- Unit, the pr adapter: from a CWD outside any project, two registered
  projects each tracking a PR with a failing bar give both items; a project
  whose tracked PRs can't be enumerated makes the source `failed`; a record
  still saying `open` for a PR the provider reports closed gives no item, and a
  record saying `closed` for a reopened, failing PR gives one. Two projects
  tracking `owner/name#42` on different providers (`github.com` and a GHES or
  Gitea host), or under two organizations of one host (`dev.azure.com/<a>`
  and `dev.azure.com/<b>`), give two items that never dedupe, while one PR
  reached through two spellings of the same authority gives one. A worktree
  tracking two PRs, an active passing one and an older reopened failing one,
  gives exactly one item, for the failing PR; one PR tracked by two worktrees is
  read once and gives one item.
- Simple e2e: a local bridge session parked on `ask_user`, a task with
  `awaiting_steer`, and a tracked PR with a failing bar produce three items in the
  expected order. Kill one source and the result is `degraded` with the others
  intact. A command source that exits non-zero, prints non-JSON, or returns a
  malformed item is `failed`. Resolve the current item between two `next` calls
  (and have its entity deduped into another source's item) and the walk still
  advances deterministically.
- Live: an operator machine with real sessions and tasks; compare with what
  each owner's own CLI reports.
