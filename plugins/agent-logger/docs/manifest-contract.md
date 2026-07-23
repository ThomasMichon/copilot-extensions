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
  "log_template": null,
  "narration_style": null,
  "exemplars": null,
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
| `log_template` | no | Optional Markdown skeleton/instructions from repo-local config. `null` = use the built-in structured-frontmatter log. |
| `narration_style` | no | `null` (default) or caller-injected instructions for **interleaved** personality woven through the narrative body (primary voice seam). |
| `exemplars` | no | `null` (default) or a list of short few-shot tone/depth reference passages (or a path to them). |
| `closing_remark` | no | `null` (default) or caller-injected instructions for an **end-of-log** sign-off (end-only complement to `narration_style`). |

## Output contract

- `return: result` -- the agent returns a short human summary (log paths +
  one-line description) and, if a closing remark was produced, that remark
  verbatim. Used by interactive callers.
- `return: json` -- the agent prints a JSON results object (per-session
  `category` / `log_path` / `status`, plus counts) to stdout for a harness
  to parse. Used by batch/service callers.

## The voice seam (how voice is injected)

**The agent has no personality of its own.** Voice is injected through three
optional, null-by-default fields. The generic skills copy them from repository
organization config; when all are null the agent writes a plain log with no
remark, quip, or persona.

| Field | Where the voice lands |
|-------|-----------------------|
| `narration_style` | **Interleaved** through the narrative body -- asides and character beats *between* thematic passages. The primary seam. |
| `closing_remark` | A single **end-of-log** sign-off after a trailing `---`. The simple, end-only complement. |
| `exemplars` | Few-shot **tone samples** that inform the writing (not copied). |

This is the single, deliberate seam through which a repository adds voice
**without the plugin ever containing one**. To inject a persona:

1. The host owns a **voice skill** (its character/quip rules) in its own
   repo -- not in this plugin.
2. The repository's organization config sets `narration_style` (and optionally
   `exemplars` and/or `closing_remark`) to the voice skill's instructions --
   e.g. a
   directive like *"Consult the `my-voice` skill; weave brief in-character
   asides between thematic sections where they add warmth or wit, never
   forced; then close with a 2-3 line sign-off."*
3. The agent weaves `narration_style` through the body, emulates any
   `exemplars` for tone, and appends any `closing_remark` after a `---`
   separator in each standalone log.

Without repository configuration all three fields remain null, so out of the
box every log is persona-free. Only a repository that deliberately supplies
instructions gets styled output.

## Repo-local organization config

The interactive `prepare-session-log --json` helper layers a repo-local
organization config on top of machine-local defaults. A repository may commit
one of these files at its git root:

- `.agent-logger.yaml`
- `.agent-logger.yml`
- `.config/agent-logger.yaml`
- `.config/agent-logger.yml`

Only the `log:` block is honored from repo-local config. This lets a repository
choose its own output tree and Markdown skeleton without letting a checkout
change machine-local sync targets.

Example:

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

The writer treats `log_template` as the repository's complete standalone-log
contract: it fills placeholders with real session values and preserves the
requested section order. It does not add built-in YAML frontmatter unless the
template asks for it. Leave `log_template` null for the backward-compatible
built-in body organization and frontmatter. The same `log:` block may supply
`narration_style`, `exemplars`, and `closing_remark`;
`prepare-session-log --json` and `agent-logger organization` copy them into
the manifest unchanged, eliminating wrapper-only injection.

`schema_version` may be omitted for compatibility and is then treated as
version 1. The loader rejects unsupported versions, malformed YAML, unknown
fields/placeholders, invalid timezones, and output paths that are absolute or
escape the repository. Repo-local config accepts only `root`, `path_template`,
`timezone`, `note_marker`, `template`, `narration_style`, `exemplars`, and
`closing_remark` under `log:`; it cannot change sync or other machine-local
behavior.

**Interleaved vs. end-only.** `narration_style` exists precisely so voice
need not be *"jammed at the end"* -- a host that wants personality *woven
through* the narrative sets `narration_style`; a host that only wants a brief
sign-off sets `closing_remark`; a host that wants both sets both.

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

## Example: batch with repository-configured voice

```json
{
  "mode": "batch",
  "return": "json",
  "sessions": [ /* ...N sessions... */ ],
  "output_root": "logs",
  "narration_style": "Consult the aperture-voice skill. Weave brief in-character asides between thematic sections where they add warmth or wit -- interleaved, never forced, never all at the end.",
  "exemplars": "visions/knowledge/permanent-record/reference-entries.md",
  "closing_remark": null
}
```
