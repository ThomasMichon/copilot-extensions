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

> **Before you start — payload-local readiness.**
> Use the exact `argv` for command id `prepare-session-log` from the
> agent-logger session command catalog. Append the arguments shown below; never
> search `PATH`, scan installed marketplaces, or invoke the legacy venv path.
> The payload-local command provisions the shared runtime on first use
> (~30–120s; watch for `::agent-provisioning::`). Surface an exact provisioning
> failure instead of improvising a toolchain install.
> If the catalog is absent or the command is unavailable, fail closed and ask
> the operator to select this payload explicitly through the host's plugin
> management surface; do not fall back to a global command.

Write a structured Markdown log for the **current** session, now. This skill
is the interactive, single-session entry point to the
`session-log-writer` agent. It produces a plain log unless repository
organization config supplies optional voice-seam instructions.

## Procedure

### 1. Prepare

Run the prep tool to detect machine, generate a cutoff, render the output
path, layer any repo-local organization config, and create the log directory:

```
<agent-logger catalog "prepare-session-log" argv[0]> --json --title "<Title>" --session "<Session ID>"
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
> `<agent-worktrees catalog argv[0]> state-root` (append `/logs`). The harness setup flow writes
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
  "target_log_path": "<prep.log_path>",
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
deliberately supplies them. If `prep.log_path` already exists, add that exact
path as `sessions[0].existing_log_path` so the renderer may evaluate whether it
is thorough or should receive an append-only supplement.

### 3. Delegate

Spawn the **session-log-writer** agent (`agent_type:
"agent-logger:session-log-writer"`) synchronously. Resolve command ids
`collate-session` and `read-session-digest` from this session's agent-logger
catalog and include their exact `argv[0]` values with the manifest path:

```text
Manifest: <manifest-path>
collate_argv0: <agent-logger catalog "collate-session" argv[0]>
digest_argv0: <agent-logger catalog "read-session-digest" argv[0]>
```

The writer must forward the exact digest-reader path into every explore
sub-agent prompt it creates. The agent is intentionally read-only: it collates,
reads the digest, and returns a complete artifact block for the exact
`target_log_path`.

### 4. Persist and present

Require the artifact path to remain under `output_root`. Reject a prepared path
that still contains `<Title>`. Verify that the boundary is 16 lowercase
hexadecimal characters, the closing marker carries the same boundary exactly
once, and the body does not contain that marker.

Enforce the action before writing:

- `create` -- the path must equal `target_log_path` and the target must not
  exist. If it does, write nothing and surface the conflict rather than
  overwriting it.
- `append` -- the target must exist and equal the manifest's
  `sessions[0].existing_log_path`. Recompute its SHA-256 immediately before the
  edit and require it to equal the artifact's `base_sha256`; otherwise write
  nothing because the file changed after rendering. Append only the returned
  delta.

Write the body with the caller's normal file-edit tool (`apply_patch`, `create`,
or `edit`); never use shell redirection. If the agent returns no artifact,
surface its exact skip/failure.

Relay the persisted log path and a one-line summary to the user. If repository
config supplied a `closing_remark`, quote only the rendered sign-off section
verbatim; do not dump the full log into chat. Then commit the log per the host
repo's git policy.

## Why sync

Logging is usually the last task in a session. Sync delegation means the
caller sees errors immediately and can retry, rather than silently blocking.
