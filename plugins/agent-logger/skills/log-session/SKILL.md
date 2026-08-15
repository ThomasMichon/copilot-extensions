---
name: log-session
description: >
  Write a Markdown session log for the current Copilot session on demand.
  Use this skill when the user explicitly asks to "write the session log" or
  "log this session" as a file. It prepares a one-session manifest and hands
  it to the session-log-writer agent. Voice-neutral by default; honors
  repository-owned organization configuration.
  Trigger phrases include: - 'write the session log' - 'generate a log file'
  - 'log this session to a file' - 'save a session log'
---

# Log Session (interactive)

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> agent-logger provisions its own runtime on first use and works standalone in any
> host (CLI, Copilot app, cloud agent). If `agent-logger` is not on PATH, deploy
> this plugin's own binstub first; it then self-provisions on first call.
>
> - Windows:
>   `pwsh -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.copilot\installed-plugins\copilot-extensions\agent-logger\scripts\install.ps1" stamp`
> - Linux/WSL:
>   `bash "$(ls ~/.copilot/installed-plugins/*/agent-logger/scripts/install.sh | head -1)" stamp`
>
> Then run `agent-logger version` once. The first call provisions the runtime
> (~30–120s; watch for `::agent-provisioning::`) and deploys the auxiliary tools
> (`session-sync`, `collate-session`, `prepare-session-log`, …). If it reports a
> provisioning failure (e.g. missing uv / network), surface the exact message —
> don't improvise a toolchain install.

Write a structured Markdown log for the **current** session, now. This skill
is the interactive, single-session entry point to the
`session-log-writer` agent. It produces a plain log unless repository
organization config supplies optional voice-seam instructions.

## Procedure

### 1. Prepare

Run the prep tool to detect machine, generate a cutoff, render the output
path, layer any repo-local organization config, and create the log directory:

```
prepare-session-log --json --title "<Title>" --session "<Session ID>"
```

`prepare-session-log` is deployed as a binstub in `~/.local/bin` by the
agent-logger installer. If it is not on PATH (payload installed but the
runtime installer hasn't run, or `~/.local/bin` isn't on PATH), invoke it via
the deployed venv interpreter instead:

```
# POSIX
~/.agent-logger/.venv/bin/python -m agent_logger.segmenter.prepare_log --json --title "<Title>" --session "<Session ID>"
# Windows
~/.agent-logger/.venv/Scripts/python.exe -m agent_logger.segmenter.prepare_log --json --title "<Title>" --session "<Session ID>"
```

Pass the session ID from the session context (omit `--session` to
auto-detect the most recently active session for the current project). The
tool prints `machine`, `session_id`, `session_dir`, `cutoff`, `log_path`,
`digest_dir`, `output_root`, `log_path_template`, `timezone`, `note_marker`,
`log_template`, `narration_style`, `exemplars`, and `closing_remark`.

`prepare-session-log` discovers repo-local organization config by convention
from the current repository root: `.agent-logger.yaml`, `.agent-logger.yml`,
`.config/agent-logger.yaml`, or `.config/agent-logger.yml`. Only the `log:`
block is honored. A repo that wants its own tree/format can set, for example:

```yaml
schema_version: 1
log:
  root: .
  path_template: "logs/{year}/{month}.{day} {title}.md"
  template: |
    # {title}

    **Date:** {date}
    **Branch(es):** {branches}
    **PR(s):** {prs}

    ## Summary

    {summary}

    ## Key Changes

    {key_changes}

    ## Commits

    {commits}

    ## Open Items

    {open_items}
  narration_style: null
  exemplars: null
  closing_remark: "End with one concise takeaway."
```

The repository file is validated before any log path is created. Unknown
fields, unsupported schema versions/placeholders, malformed YAML, invalid
timezones, and paths that are absolute or escape the repository fail with an
explicit error. Do not silently fall back when validation fails.

> **Stateless-harness binding.** When sessions are driven from a **stateless
> harness** (a shareable control plane that holds no personal state), logs must
> **not** land in the harness checkout. agent-logger already supports a
> configurable root: set the **user-level** `log.root` (in
> `~/.agent-logger/config.yaml`, which may be absolute) to the bound
> **knowledge** repo's logs directory — resolvable on this machine with
> `agent-worktrees state-root` (append `/logs`). The harness setup flow writes
> this per machine; the repo-local `.agent-logger.yaml` `root` stays relative
> (it can only point inside the launch repo, so it cannot cross into the
> knowledge repo). For a non-stateless repo, the default `repo_root/logs` is
> unchanged.

### 2. Build a one-session manifest

Write a manifest JSON to a scratch file using the prep output -- shape (full
example: [`references/manifest.json`](references/manifest.json)):

```json
{
  "mode": "single",
  "return": "result",
  "sessions": [
    { "session_id": "<session_id>", "machine": "<machine>", "session_path": "<session_dir>" }
  ],
  "output_root": "<prep.output_root>",
  "log_path_template": "<prep.log_path_template>",
  "timezone": "<prep.timezone>",
  "note_marker": "<prep.note_marker>",
  "log_template": "<prep.log_template>",
  "narration_style": "<prep.narration_style>",
  "exemplars": "<prep.exemplars>",
  "closing_remark": "<prep.closing_remark>"
}
```

Use the prep output verbatim for the organization fields. `log_template` may
be `null`; when non-null it is the repo's requested Markdown structure and the
writer must preserve it. Voice fields remain null unless repository config
deliberately supplies them.

### 3. Delegate

Spawn the **session-log-writer** agent (`agent_type:
"agent-logger:session-log-writer"`) synchronously with the manifest file path in
the prompt. The agent collates, reads the digest, writes the log, and returns a
short result.

### 4. Present

Relay the agent's result to the user (the log path and a one-line summary).
If repository config supplied a `narration_style` or `closing_remark` and the
agent produced styled output, present it verbatim. Then commit the log per the
host repo's git policy.

## Why sync

Logging is usually the last task in a session. Sync delegation means the
caller sees errors immediately and can retry, rather than silently blocking.
