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

> **Before you start — payload-local readiness.**
> Use command id `agent-logger` from the agent-logger session command catalog
> for organization and chronicle actions. The writer sub-agent uses the
> `collate-session` and `read-session-digest` entries from that same catalog.
> Invoke each exact `argv`; never search `PATH` or substitute a same-named
> command from another payload. The first call provisions the shared runtime.
> If either catalog entry is absent or unavailable, fail closed and ask the
> operator to select this payload explicitly through the host's plugin
> management surface.

Turn a backlog of unlogged Copilot sessions into Markdown logs on this
machine -- the no-service alternative to a chronicle runner. Logs are
plain unless repository organization config supplies optional voice seams.

## When to use

- The user wants logs written *now* for several recent sessions.
- No processing service is running; you just want to clear the backlog.

For a single current session, prefer the `log-session` skill. For automated,
scheduled fleet processing, use a host-owned chronicle runner around
`<agent-logger catalog "agent-logger" argv prefix> chronicle tick`.

## Procedure

### 1. Load repository organization

From the target repository/worktree root, run
`<agent-logger catalog "agent-logger" argv prefix> organization`.
Use the returned `manifest` object for output location, naming/template, note
marker, and optional voice seams. Invalid config is an explicit error.

### 2. Enumerate candidate sessions

Choose the session source:

- **Local store** -- `~/.copilot/session-state/<id>/` on this machine.
- **A sync target root** -- a directory previously populated by
  `session-sync`, laid out as `<root>/<machine>/session-state/<id>/`.

For each candidate, read `workspace.yaml` for `repository`, `branch`, and
the auto-summary, and check `events.jsonl` exists (skip empty sessions).

### 3. Classify existing logs

For a session whose `session_id` already appears in a log file's YAML
frontmatter under `output_root`, set that file as
`sessions[].existing_log_path`; the renderer decides whether it is thorough
(skip) or thin (append-only supplement). Do not authorize arbitrary existing
paths. A daily digest file is the exception: the renderer may append to its
derived, path-validated digest target when it supplies the SHA-256 of the exact
file it read.

### 4. Build a batch manifest

Full example: [`references/manifest.json`](references/manifest.json). Shape:

```json
{
  "mode": "batch",
  "return": "json",
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

Copy those fields exactly from
`<agent-logger catalog "agent-logger" argv prefix> organization`.

Cap the batch to a sensible size (e.g. 1-2 substantial sessions or one compact
day) so both the agent's context and the returned full-content bundle remain
bounded; repeat for more.

### 5. Delegate

Spawn the **session-log-writer** agent (`agent_type:
"agent-logger:session-log-writer"`) synchronously. Resolve command ids
`collate-session` and `read-session-digest` from this session's agent-logger
catalog and include the same keys required by the writer:

```text
Manifest: <manifest-path>
collate_argv0: <agent-logger catalog "collate-session" argv prefix>
digest_argv0: <agent-logger catalog "read-session-digest" argv prefix>
```

Require the writer to forward `digest_argv0` into every explore sub-agent
prompt. In batch mode it triages each session (standalone / digest / skip) and
returns a JSON render bundle. It does not write files.

### 6. Persist and report

For every `status: rendered` result, resolve `output_root` and `log_path` to
real absolute paths and require their common path to equal the real
`output_root`. Reject non-`.md` targets and any target whose existing parent is
a symlink. Then enforce its action:

- `create` -- the target must not exist.
- `append` -- the target must exist. For a standalone result it must match that
  session's manifest-supplied `existing_log_path`; for a grouped daily digest it
  must be the renderer-derived digest target. In both cases, recompute SHA-256
  immediately before editing and require it to equal `base_sha256`; otherwise
  reject the stale artifact. Append only the returned delta.

Reject an unsafe or malformed result and report it without discarding other
independent valid results. Write valid `content` with the caller's normal
file-edit tool (`apply_patch`, `create`, or `edit`), never shell redirection. If
the response is truncated or cannot be parsed, write nothing and rerun with a
smaller batch.

Summarize what was persisted and skipped for the user, then commit per the host
repo's git policy.
