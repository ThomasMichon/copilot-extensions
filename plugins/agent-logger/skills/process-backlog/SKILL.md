---
name: process-backlog
description: >
  Write Markdown logs for a backlog of unlogged Copilot sessions locally,
  with no service required. Use this skill when the user wants to catch up
  on session logging -- e.g. "log my session backlog", "write logs for my
  recent sessions". It builds a batch manifest and hands it to the
  session-log-writer agent. Voice-neutral by default; honors repository-owned
  organization configuration. Trigger phrases include: - 'log my backlog' -
  'process my session backlog' - 'write logs for recent sessions' - 'catch up
  on session logs'
---

# Process Backlog (local, no service)

Turn a backlog of unlogged Copilot sessions into Markdown logs on this
machine -- the no-service alternative to the orchestrator daemon. Logs are
plain unless repository organization config supplies optional voice seams.

## When to use

- The user wants logs written *now* for several recent sessions.
- No processing service is running; you just want to clear the backlog.

For a single current session, prefer the `log-session` skill. For automated,
scheduled fleet processing, that is the orchestrator daemon (separate).

## Procedure

### 1. Load repository organization

From the target repository/worktree root, run `agent-logger organization`.
Use the returned `manifest` object for output location, naming/template, note
marker, and optional voice seams. Invalid config is an explicit error.

### 2. Enumerate candidate sessions

Choose the session source:

- **Local store** -- `~/.copilot/session-state/<id>/` on this machine.
- **A sync target root** -- a directory previously populated by
  `session-sync`, laid out as `<root>/<machine>/session-state/<id>/`.

For each candidate, read `workspace.yaml` for `repository`, `branch`, and
the auto-summary, and check `events.jsonl` exists (skip empty sessions).

### 3. Filter out already-logged sessions

Skip any session whose `session_id` already appears in a log file's YAML
frontmatter under `output_root`, or whose target path already exists. This
mirrors the agent's own skip check -- do it here to avoid spawning work that
will be skipped.

### 4. Build a batch manifest

Full example: [`references/manifest.json`](references/manifest.json). Shape:

```json
{
  "mode": "batch",
  "return": "result",
  "sessions": [
    {
      "session_id": "<id>",
      "machine": "<machine>",
      "session_path": "<abs path to session-state/<id>>",
      "repository": "<owner/repo>",
      "branch": "<branch>",
      "summary": "<auto-summary>",
      "created_at": "<iso>",
      "updated_at": "<iso>"
    }
  ],
  "output_root": "<organization manifest output_root>",
  "log_path_template": "<organization manifest log_path_template>",
  "timezone": "<organization manifest timezone>",
  "note_marker": "<organization manifest note_marker>",
  "log_template": "<organization manifest log_template>",
  "narration_style": "<organization manifest narration_style>",
  "exemplars": "<organization manifest exemplars>",
  "closing_remark": "<organization manifest closing_remark>"
}
```

Copy those fields exactly from `agent-logger organization`.

Cap the batch to a sensible size (e.g. 1-4 substantial sessions or a day's
worth) so the agent's context isn't overwhelmed; repeat for more.

### 5. Delegate

Spawn the **session-log-writer** agent (`agent_type: "session-log-writer"`,
`mode: "sync"`) with the manifest path. In batch mode it triages each
session (standalone / digest / skip), writes logs, and reports.

### 6. Report

Summarize what was written and skipped for the user, then commit per the
host repo's git policy.
