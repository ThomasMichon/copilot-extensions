# Outlines, effort remainders and session backfill

These helpers shorten the path into a context-qualified agenda. They only
produce JSON and previews; initialization and every later change still go
through the normal agenda commands, under the adopting agent's control.

## Outline format

```json
{
  "schema": "efforts.agenda-outline", "version": 1,
  "source": {"kind": "effort", "ref": "efforts/active/demo/README.md"},
  "worktree": "demo-worktree",
  "provenance": "authored",
  "items": [
    {"action": "Land the parser", "status": "done"},
    {"action": "Wire the CLI", "status": "current", "exit": ["CLI tests pass"],
     "children": [{"action": "Add flags", "status": "done"}, {"action": "Add help text"}]},
    {"action": "Write the docs"}
  ]
}
```

- `source.kind` is `effort`, `task` or `aside`; `provenance` is `authored`,
  `derived` (from an effort) or `reconstructed` (from session history).
  Replayed outcomes are recorded as `judgment` evidence regardless of
  provenance: an outline asserts results, it does not measure them.
- Item fields: `action` (required), `label`, `exit` (string or list; defaults
  to "<label> is complete."), `status` (`queued`, `current`, `done`,
  `waiting`), `children`, `evidence`, `id`, `source_ref`. Children of a `done`
  parent default to `done`.
- Every item becomes a board definition with outcomes `done → advance`,
  `blocked → wait`, `needs-context → replan`. Items stay distinct: a repeated
  `source_ref` (compared after Unicode normalization, as the store does) gets
  the item identity appended.
- `caps` may override the four resource caps; defaults grow to fit the outline.

## Commands

Run from the skill's `scripts/` payload with the plugin's Python:

```text
agenda_outline.py expand <outline.json>              # request-v2 init JSON
agenda_outline.py preview <outline.json> [--format text|json]
agenda_outline.py adopt <outline.json> --state-home <new-dir> [--session ID] [--actor NAME]
agenda_outline.py from-effort <effort README.md>     # outline of open Plan/Validation items
agenda_backfill.py digest  <session-folder>          # bounded evidence for authoring
agenda_backfill.py outline <session-folder>          # first-pass reconstructed outline
agenda_backfill.py preview <session-folder> [--format text|json]
```

`preview` initializes a temporary standalone store, replays item statuses
(done items complete, current items start, nested items open subqueues) and
renders the same display graph the Companion uses. When replay reaches a
parent that is not `waiting`, its subqueue opens, so a parent's children are
shown even when their statuses are omitted.

`expand` emits only the `init` (definitions and order, no progress). To keep a
reviewed outline's progress and hierarchy, `adopt` initializes a **new or
empty** explicit state home (for example a worktree-local
`.effort-agenda/<lane>/`) and replays the same outcomes, expansions and
returns as `preview`, recorded under your session. It refuses a non-empty home
and never rewrites an existing lane. Managed placements still start from
`expand` plus the managed `init`; drive progress through the normal commands. Warnings report statuses the queue cannot
represent, such as a `done` item after the current one.

## Deriving from an effort

`from-effort` groups open `- [ ]` items by Plan / Validation Plan section;
a section with one open item becomes a single item. The first item (and its
first child) is marked current. Edit the result to match your actual next
step before adopting it.

## Backfilling a session

`agenda_backfill.py` reads `events.jsonl`, the session's todo table and its
checkpoint index:

- operator requests are user messages without a relay/automation source;
- requests delivered through agent-bridge are also kept: the request text is
  read from the `<agent-message>` envelope, the item's evidence names the
  sender, and generated reply-routing guidance is dropped. Bridge `notify`
  and `status-check` deliveries are skipped because they ask for no new work;
  harness continuity turns (context-handoff notices, fresh-window recovery
  prompts) and acknowledgements made only of conversational filler ("ok",
  "thanks, go ahead") are skipped; short instructions such as "Fix the
  parser" are kept;
- each todo is attached to the latest substantive request created before it;
- earlier requests are `done` with their finished todos; their unfinished
  todos move to a final "Open work carried from earlier requests" item;
- the last request is `current` unless the session ended with its work done.

The result is a reconstruction, not a record of intent. Use `digest` when the
first pass is too coarse, then author a cleaner outline yourself. Sessions
with no operator or bridged requests (for example headless automation) exit
with status 2.
