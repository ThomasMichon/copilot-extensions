---
name: log-session
description: >
  Write a Markdown session log for the current Copilot session on demand.
  Use this skill when the user explicitly asks to "write the session log" or
  "log this session" as a file. It prepares a one-session manifest and hands
  it to the session-log-writer agent. Voice-neutral -- produces plain logs.
  Trigger phrases include: - 'write the session log' - 'generate a log file'
  - 'log this session to a file' - 'save a session log'
---

# Log Session (interactive)

Write a structured Markdown log for the **current** session, now. This skill
is the interactive, single-session entry point to the
`session-log-writer` agent. It produces a **plain, persona-free** log.

> Personality is never added here. A host repo that wants a styled closing
> remark wraps this flow and injects `closing_remark` instructions into the
> agent prompt (see `docs/manifest-contract.md` -> closing-remark seam).
> This skill leaves `closing_remark` null.

## Procedure

### 1. Prepare

Run the prep tool to detect machine, generate a cutoff, render the output
path, and create the log directory:

```
prepare-session-log --title "<Title>" --session "<Session ID>"
```

Pass the session ID from the session context (omit `--session` to
auto-detect the most recently active session for the current project). The
tool prints `machine`, `session_id`, `session_dir`, `cutoff`, `log_path`,
and `digest_dir`.

### 2. Build a one-session manifest

Write a manifest JSON to a temp file using the prep output:

```json
{
  "mode": "single",
  "return": "result",
  "sessions": [
    {
      "session_id": "<session_id>",
      "machine": "<machine>",
      "session_path": "<session_dir>"
    }
  ],
  "output_root": "<repo logs root, e.g. logs>",
  "closing_remark": null
}
```

Set `output_root` to where logs should land (the host's convention; default
to the project's `logs/` directory). Leave `closing_remark` null unless a
wrapping host skill instructs otherwise.

### 3. Delegate

Spawn the **session-log-writer** agent (`agent_type: "session-log-writer"`,
`mode: "sync"`) with the manifest file path in the prompt. The agent
collates, reads the digest, writes the log, and returns a short result.

### 4. Present

Relay the agent's result to the user (the log path and a one-line summary).
If a `closing_remark` was injected by a host wrapper and the agent produced
one, present it verbatim. Then commit the log per the host repo's git
policy.

## Why sync

Logging is usually the last task in a session. Sync delegation means the
caller sees errors immediately and can retry, rather than silently blocking.
