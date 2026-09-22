---
name: searching-the-harness-index
description: >
  Search a harness's semantic + lexical index (agent-index) instead of a broad
  grep/glob sweep, when the current repository has agent-index enabled. Use
  this skill whenever a task needs the most-relevant few results across a
  large corpus by meaning/behavior, a "more like this" pivot from a known hit,
  or a near-duplicate/cluster view -- not for exact-string hunts, which remain
  grep's job. Trigger phrases include:
  - 'search the index'
  - 'semantic search'
  - 'agent-index'
  - 'find similar code'
  - 'find related chunks'
  - 'is agent-index enabled'
  - 'is the index available'
  - 'search across repos'
  - 'search this harness'
---

# Searching the harness index (agent-index)

`agent-index` is a semantic + lexical index of a harness and its coordinated
corpora. When enabled for the current repository, its search/similar/clusters
commands are available to **every agent** in that session -- no sub-agent, no
MCP tool call, and no `agent-index` install/setup step required.

## Am I allowed to use it right now?

Read this session's already-disclosed session folder (per the
`<session_context>` block) for
`instructions/agent-index/session-guidance.instructions.md`. That file is
written fresh every session and says exactly one of:

- **Enabled**, with the live command catalog (the exact `argv` to invoke) and
  the list of sources/corpora this session can search.
- **Not enabled for this repository**, with a short reason. If you see this,
  stop here -- do not try `agent-index` commands, do not install it, and fall
  back to `grep`/`glob` for this task. Enabling it is an operator/config
  decision (`.agent-index/config.yaml`), not something to do from an agent
  turn.

If the session-guidance file is absent entirely, treat agent-index as
unavailable and use `grep`/`glob` normally.

## How to search (once enabled)

Take `commands[id=agent-index].argv` from the session's injected command
catalog and append the arguments below. **Never** search `PATH` or substitute
a same-named command from another payload -- the catalog's `argv` is the only
correct invocation for this session.

- `<catalog argv[0]> search "<natural-language or code query>" [--source <name>]
  [--language <lang>] [--repo <repo>] [--limit N] --json` — ranked hits; each
  has `chunk_id`, `source`, `file_path`, `line_start`/`line_end`, `content`.
- `<catalog argv[0]> similar <chunk_id> [--source <name>] [--limit N]` — pivot
  "more like this" from a hit.
- `<catalog argv[0]> clusters [--source <name>] [--exact-dupes-only] [--limit N]`
  — near-duplicate groups.
- `<catalog argv[0]> status` — index health + per-source coverage; probe once
  if results look sparse.

## When to prefer this over grep/glob

Prefer `search` when looking **within an enabled source** for the
most-relevant few results by meaning/behavior across a large corpus, or to
pivot from a known hit. Pass `--source` to scope to one corpus (and to respect
trust-domain boundaries, not yet enforced at query time).

Fall back to `grep`/`glob` for exact-string hunts, files outside the enabled
sources, or whenever the index is reported unavailable/unhealthy.

## What never to do

- Never reindex from an agent turn (`<catalog argv[0]> index`/`index --full`)
  -- that is the operator/maintenance flow, not something a search task
  should trigger as a side effect.
- Never treat "not enabled" as something to work around by installing,
  configuring, or starting agent-index yourself.
