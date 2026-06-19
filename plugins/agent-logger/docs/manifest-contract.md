# Manifest contract & the closing-remark seam

The `session-log-writer` agent is driven entirely by a **manifest** -- a JSON
file whose path the caller passes in the spawn prompt. Three callers produce
this same manifest: the `log-session` skill (interactive, one session), the
`process-backlog` skill (local batch), and an orchestrator runner (fleet
batch). This document is the contract between them and the agent.

## Schema

```json
{
  "mode": "single | batch",
  "return": "result | json",
  "sessions": [
    {
      "session_id": "abc-123",
      "machine": "workstation",
      "session_path": "/abs/path/to/session-state/abc-123",
      "repository": "owner/repo",
      "branch": "main",
      "summary": "optional brief summary",
      "created_at": "2026-04-25T10:00:00Z",
      "updated_at": "2026-04-25T12:30:00Z",
      "existing_log_path": "logs/2026/.../Title.md"
    }
  ],
  "output_root": "logs",
  "log_path_template": "{year}/{month}/{day} {hhmmss} {title}.md",
  "timezone": null,
  "note_marker": "SESSION NOTE:",
  "closing_remark": null
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `mode` | yes | `single` writes the one session; `batch` triages + writes many. |
| `return` | yes | `result` = short human summary (+ remark); `json` = machine-parseable results. |
| `sessions[].session_id` | yes | Session UUID. |
| `sessions[].machine` | yes | Base machine name (no `-wsl`); used in paths/frontmatter. |
| `sessions[].session_path` | yes | Collation source -- an absolute path the segmenter can read. |
| `sessions[].repository` / `branch` / `summary` / `created_at` / `updated_at` | no | Metadata for frontmatter and triage. |
| `sessions[].existing_log_path` | no | A pre-existing log to skip / supplement / promote. |
| `output_root` | yes | Root dir for emitted logs. |
| `log_path_template` | no | Defaults to the agent-logger config template. Tokens: `{year} {month} {day} {hhmmss} {machine} {title}`. |
| `timezone` | no | IANA tz for timestamps; `null` = system local. |
| `note_marker` | no | Operator-note marker prefix (default `SESSION NOTE:`). |
| `closing_remark` | no | `null` (default) or caller-injected instructions for a closing remark. |

## Output contract

- `return: result` -- the agent returns a short human summary (log paths +
  one-line description) and, if a closing remark was produced, that remark
  verbatim. Used by interactive callers.
- `return: json` -- the agent prints a JSON results object (per-session
  `category` / `log_path` / `status`, plus counts) to stdout for a harness
  to parse. Used by batch/service callers.

## The closing-remark seam (how voice is injected)

**The agent has no personality of its own.** It produces a closing remark
**only** when `closing_remark` is non-null, and then follows those
instructions exactly. When `closing_remark` is `null`, the agent writes a
plain log with no remark, quip, or persona.

This is the single, deliberate seam through which a host repo adds voice
**without the plugin ever containing one**. To inject a persona:

1. The host owns a **voice skill** (its character/quip rules) in its own
   repo -- not in this plugin.
2. The host's wrapping caller (its own `log-session` variant, or its
   orchestrator runner) sets `closing_remark` to the voice skill's
   instructions -- e.g. the skill text, or a directive like *"Consult the
   `my-voice` skill and append a 2-3 line in-character sign-off reacting to
   this session."*
3. The agent reads those instructions and appends the remark after a `---`
   separator in each standalone log.

The plugin's own `log-session` and `process-backlog` skills always leave
`closing_remark` null, so out of the box every log is persona-free. Only a
host that deliberately injects instructions gets styled output, and the
styling lives entirely in that host.

## Example: interactive single session

```json
{
  "mode": "single",
  "return": "result",
  "sessions": [
    {"session_id": "abc-123", "machine": "workstation",
     "session_path": "/home/u/.copilot/session-state/abc-123"}
  ],
  "output_root": "logs",
  "closing_remark": null
}
```

## Example: batch with injected voice (host-side)

```json
{
  "mode": "batch",
  "return": "json",
  "sessions": [ /* ...N sessions... */ ],
  "output_root": "logs",
  "closing_remark": "Consult the aperture-voice skill and append a short, in-character sign-off reacting to the specific work in each standalone log."
}
```
