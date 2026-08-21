---
name: authoring-skills
description: >
  Author Copilot CLI skills -- the SKILL.md format, the per-skill folder
  convention (SKILL.md + references/, scripts/, assets/), the validation
  checklist, plus the related hook and custom-instruction surfaces. Use when
  creating or editing a SKILL.md, organizing a skill's companion files, writing a
  lifecycle hook, or adding custom instructions.
  Trigger phrases include:
  - 'create a skill'
  - 'new skill'
  - 'SKILL.md'
  - 'skill folder structure'
  - 'skill best practices'
  - 'skill audit'
  - 'write a hook'
  - 'lifecycle hook'
  - 'custom instructions'
  - 'AGENTS.md'
---

# Authoring Skills

How to write Copilot CLI **skills** -- task-specific instruction bundles loaded
on demand -- plus the two always-/lifecycle-adjacent surfaces that pair with
them: **hooks** and **custom instructions**. This supplements knowledge the
Copilot CLI does not ship natively.

> **Declarative first.** Skills, custom instructions, hooks, sub-agents, and MCP
> servers are *declarative* surfaces -- prefer them. The CLI also has an
> *imperative* **Extensions API** (a JS `extension.mjs` calling `joinSession`),
> but it is heavier and **may be on its way out**: the native runtime (1.0.66+)
> already **removed extension SDK callback hooks**, and the declarative hook
> system below now covers what they did -- including injecting `additionalContext`
> into the model. Reach for an extension only when no declarative surface fits.

## Two ways to add an in-repo skill or agent — prefer the local-plugin model

Before writing a skill or sub-agent for a repo, decide **how it is packaged**:

| Approach | What it is | When |
|----------|-----------|------|
| **Local plugin** (preferred) | The skill/agent is a plugin in the repo's **in-repo `.ai` local marketplace** (`.ai/<name>/skills/...` or `.ai/<name>/agents/...`), declared via a `directory` marketplace source in `.github/copilot/settings.json`. | **Default.** Whenever it should be modular, individually toggleable, travel with the repo, and compose across contexts (repo launched directly, consumed by another harness, or dispatched to via agent-bridge). |
| **Loose local skill / agent** | A bare `.github/skills/<name>/SKILL.md` or `.github/agents/<name>.agent.md` (or `.copilot/…`). Loaded directly by the runtime, no marketplace. | A quick one-off / experiment, or a repo that deliberately wants a single flat set with no plugin packaging. Simplest to create; loads **only** for the launch repo. |

**Prefer the local-plugin model when practical.** It costs one extra `plugin.json`
+ a `marketplace.json` entry, but it makes each capability independently
enable-able, reviewable, and portable — and it is the only form that loads when
the repo is *consumed* rather than *launched* (a bound data/knowledge repo, an
agent-bridge dispatch target). Use a loose skill/agent only when that portability
genuinely doesn't matter.

> **The two are otherwise identical to author.** A skill is the same `SKILL.md`
> and a sub-agent the same `.agent.md` either way — the *only* differences are
> **where the file lives** (`.ai/<name>/skills/<name>/` vs `.github/skills/<name>/`)
> and, for a plugin, the small `.claude-plugin/plugin.json` + `marketplace.json`
> entry + the `directory` marketplace declaration. See the `installing-plugins`
> skill (§ *the `.ai` local marketplace*) for the packaging + **required
> marketplace declaration** mechanics; this skill covers authoring the SKILL.md
> itself.

Reference documentation:

| Feature | URL |
|---------|-----|
| Skills | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-skills |
| **Skill Best Practices** | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices |
| Custom instructions | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions |
| Hooks | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks |
| Hooks config reference | https://docs.github.com/en/copilot/reference/hooks-configuration |

When in doubt, fetch the relevant URL for the latest details.

---

## Skills

A skill is a SKILL.md file (and optional companion resources) in a named
subdirectory. Copilot auto-discovers skills from known locations and loads them
when relevant.

### Locations

| Scope | Path |
|-------|------|
| Project | `.github/skills/<skill-name>/SKILL.md`, `.claude/skills/<skill-name>/SKILL.md`, or `.agents/skills/<skill-name>/SKILL.md` |
| Personal | `~/.copilot/skills/<skill-name>/SKILL.md` or `~/.agents/skills/<skill-name>/SKILL.md` |
| Plugin | `plugins/<plugin>/skills/<skill-name>/SKILL.md` (shipped by an enabled plugin) |
| In-repo plugin (`.ai`) | `.ai/<capability>/skills/<capability>/SKILL.md` — a plugin in the repo's own **local marketplace**, enabled via a `directory` source in `.github/copilot/settings.json`. **Preferred** for a repo's modular, individually-toggleable, travels-with-the-repo skills; see the `installing-plugins` skill. |

Add extra search paths with `/skills add`.

> **Choosing a home for a repo's own skill.** See § *Two ways to add an in-repo
> skill or agent* above — prefer the **`.ai` local-plugin** model over a loose
> `.github/skills/<name>/` (which loads only for the launch repo), and remember
> the `.ai` marketplace **must be declared** in `.github/copilot/settings.json`
> (`installing-plugins` → *the `.ai` local marketplace*).

### SKILL.md format

YAML frontmatter (`name` required, `description` required, `license` optional)
followed by markdown instructions. The description drives auto-matching -- be
specific about trigger conditions.

- **`name`** -- lowercase letters, numbers, and hyphens only; max 64 chars; no
  reserved words (`anthropic`, `claude`). Prefer gerund form (`authoring-skills`,
  `processing-pdfs`).
- **`description`** -- non-empty, max **1024 characters**, third person, no XML
  tags. State both **what** the skill does and **when** to use it, with concrete
  trigger terms.

### Per-skill folder convention

Lay every skill out the same way so companion files are discoverable and the
SKILL.md stays a lean table of contents:

```
<skill-name>/
  SKILL.md            # required: frontmatter (name + description) + body
  references/         # companion docs the SKILL.md points to, loaded on demand
    <topic>.md
  scripts/            # executable utilities the agent RUNS (not loaded as text)
  assets/             # templates / fixtures the skill copies or fills in
```

Rules:

- **Only `SKILL.md` lives at the top level.** Everything else goes in
  `references/`, `scripts/`, or `assets/` -- don't scatter loose `.md` siblings.
- **`references/`** holds prose the SKILL.md links to (progressive disclosure).
  Keep links **one level deep** from SKILL.md -- no nested reference chains.
  **Decompose liberally:** SKILL.md is loaded whole when the skill triggers, but a
  `references/` doc is fetched **only when the agent follows the link**. So bias
  toward a lean SKILL.md that links out to focused reference docs — pull each large,
  self-contained topic (a deep procedure, a full schema, a worked example set) into
  `references/<topic>.md` and leave a one-line pointer. That keeps per-trigger
  context small; the trade is an extra read on demand. Link out *and* back; no
  orphan references. The same bias applies to any long doc a skill owns.
- **Keep the skill body target-agnostic — factor target-specific reactions into a
  linked "troubleshooting"/"errata" reference, indexed by target.** A skill teaches a
  *general* procedure; the specific ways one downstream **target/project** reacts to it
  (a build target that fails on stderr, a service that rejects a header, a repo whose CI
  flags a warning) are **errata**, not general guidance — inlining them into the body
  bloats it, dates it, and privileges one target over the skill's general audience.
  Instead keep a `references/errata/` (or `references/troubleshooting.md`) **index
  broken down by target/project**, add a row + a focused file per quirk, and give the
  skill body a single neutral *"Troubleshooting specific errors"* pointer to the index.
  The body then stays stable and general as targets come and go.
- **`scripts/`** holds code the agent **executes by path** ("run `scripts/x.py`")
  rather than pasting inline -- more reliable, fewer tokens.
- **`assets/`** holds templates/fixtures (e.g. a `TEMPLATE.md` the skill copies).
- **Use forward slashes** in skill-internal references so they resolve on every
  platform (documenting an OS-specific *command* path is fine).
- **Keep `SKILL.md` under 500 lines — and split *before* that when a topic can
  stand alone.** 500 is the ceiling, not a target; move detail into `references/`
  proactively at a natural seam and leave a pointer.

### Validation checklist

When creating or modifying a skill, validate against Anthropic's best practices:

- **Description:** specific, third-person, includes key trigger terms, under
  1024 chars. Avoid "I can" / "You can use this".
- **Body:** under 500 lines. Split into companion files if larger.
- **Conciseness:** only add context the agent doesn't already have. Challenge
  each paragraph: does it justify its token cost?
- **Degrees of freedom:** match specificity to fragility -- exact commands for
  fragile ops, high-level guidance for flexible tasks.
- **No time-sensitive data.** Use "old patterns" sections if needed.
- **Progressive disclosure + folder structure:** per the convention above.
- **Consistent terminology:** one term per concept throughout.

### Invocation & CLI

- **Explicit:** `/skill-name` in a prompt. **Auto-match:** Copilot matches the
  prompt against skill descriptions and loads relevant skills automatically.
- Commands: `/skills list`, `/skills info`, `/skills` (toggle), `/skills add`,
  `/skills reload`, `/skills remove DIR`. The same list/add/remove operations
  are available outside an interactive session as `copilot skill list`,
  `copilot skill add <FILE|URL|DIRECTORY>`, and `copilot skill remove ...`.

### Referencing skills or agents from another plugin

When a skill (or its docs/references) refers to a skill or sub-agent **shipped by
a different plugin**, name it in the **namespaced `plugin:name` form** — e.g. the
`agent-bridge` skill in the agent-bridge plugin is `agent-bridge:agent-bridge`,
agent-worktrees' worktree skill is `agent-worktrees:worktree`, and the
agent-logger writer agent is `agent-logger:session-log-writer`. This disambiguates
same-named skills across plugins and tells the reader (and the agent) exactly
which plugin owns it. **Within the same plugin, keep the bare name** — only
*cross-plugin* references are namespaced. The same rule applies to a sub-agent's
`agent_type` (see the defining-subagents skill).

### Skills vs custom instructions

Use **custom instructions** for simple, always-on guidance (coding standards,
repo conventions). Use **skills** for detailed, task-specific instructions
Copilot should load only when relevant.

### Action-sequence skills vs ambient-guidance skills

A skill's guidance applies **most strongly during the turn it is invoked**, and
fades on later turns. Author with that grain, not against it:

- **Action-sequence skills** — a procedure the agent runs *now* (setup steps, a
  deploy flow, a review pass). These fit the model perfectly: the sequence is
  consumed in-turn. Write them as concrete, ordered steps.
- **Ambient-guidance skills** — standing rules meant to hold for the *rest of
  the session* (a voice/persona, a style bar, a safety discipline). A one-shot
  skill body decays after its turn, so the guidance quietly stops applying.
  Put the durable guidance in its authoritative always-on channel: genuinely
  repository-owned invariants stay in `AGENTS.md` / custom instructions, while
  generic plugin-owned policy is injected by that plugin as a concise
  `sessionStart` context kernel. Keep detailed mechanics in a skill or linked
  file and have the kernel point at them with a faux-link. The skill's job is
  the task-time procedure, not a transient or materialized copy of ambient
  policy.

This preserves one owner for each rule and prevents both skill decay and
`AGENTS.md` bloat. Follow *sessionStart context injection* below when a plugin
owns the ambient policy.

## Custom Instructions

Always-on context injected into every prompt.

| Scope | File |
|-------|------|
| Repo (always loaded) | `AGENTS.md` in git root and cwd |
| Repo (always loaded) | `.github/copilot-instructions.md` |
| Repo (always loaded) | `.github/instructions/**/*.instructions.md` |
| Personal (all repos) | `~/.copilot/copilot-instructions.md` |
| Personal (all repos) | `~/.copilot/instructions/**/*.instructions.md` |
| Host/machine-scoped (deployed) | a generated instructions directory loaded via `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` |

Suppress with `--no-custom-instructions`.

> **Static instructions dir vs. a dynamic `sessionStart` hook.** The
> `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` row is a **static** delivery: a plugin
> deploys `*.instructions.md` files into a directory and the launcher injects the
> dir path, so the *same bytes* load into every session for that project. When the
> guidance is **computed at session start** or must be **targeted by which repo
> the session is in**, a plugin `sessionStart` **hook** that emits
> `{"additionalContext": "..."}` is the better delivery (see *Hooks →
> sessionStart context injection* below): the hook runs a script that can read
> live state and the session's `cwd`, so a single globally-installed plugin injects
> *different* context per repo -- something a fixed instructions dir cannot do. It
> also removes the launcher's `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`-injection
> dependency for launch paths that load plugin hooks. Some headless/cloud paths
> do not load plugin hooks, so irreducible safety and publication rules still
> need a minimal static fail-safe there. Prefer the static dir for truly
> fixed, always-identical text; reach for the hook when the payload is dynamic,
> conditional, or repo-scoped. Because hook `additionalContext` is capped (10 KB,
> joined across hooks) and shares the context budget, keep the injected text lean:
> inline only what every turn needs and **point at a file** (a backtick faux-link
> the agent reads on demand) for the rest.

**Avoid auto-load in AGENTS.md:** Copilot follows valid Markdown links in
custom-instruction files and auto-loads them. Reference docs with backtick code
spans (`` `docs/tools.md` ``), not `[text](path)` links, so Copilot reads files
on demand instead of loading them into every session.

**Build `AGENTS.md` as a waypoint, not a dumping ground.** The root `AGENTS.md`
is the first thing an agent reads on entering a repo -- including one arriving
from *another* repo (see the **`agent-worktrees:working-cross-repo`** skill) -- so it should read
as a **map**: orient the reader and link out (backtick faux-links) to the
detailed homes (`docs/`, `visions/`, `CONTRIBUTING.md`, the connective-tissue
skills) rather than inlining reference detail that has a home elsewhere.

**What stays inline vs links out depends on the repo's *purpose*** -- `AGENTS.md`
is the always-on instruction file, so keep what the session genuinely needs
*every turn* and link the rest:

- A **control harness** (the agent *is* the operator) keeps repository-owned
  identity, irreducible local invariants, and minimal fail-safes inline. Generic
  plugin-owned ambient policy is injected by its owner; the repository should
  configure or override it rather than copy it. `AGENTS.md` still faux-links
  reference material instead of pasting it.
- A **product / library / marketplace** repo (the agent is a *contributor*, not
  a persona) carries little always-on guidance, so its `AGENTS.md` is a
  mostly-navigational contributor guide -- a lean map with a few contribution
  invariants.

The shared failure mode is an `AGENTS.md` bloated with *reference* detail agents
must crawl, or that auto-loads the tree every session. Factor *reference* detail
out; keep repository-owned ambient guidance and fail-safes inline, and inject
plugin-owned ambient policy from its owner.

## Hooks

Shell commands that run at agent lifecycle points. The `preToolUse` hook can
**block** tool execution -- the primary mechanism for guardrails and policy
enforcement. Config lives in `.github/hooks/*.json` at the repository root, user-level
`~/.copilot/hooks/*.json` (or `%USERPROFILE%\.copilot\hooks\*.json` on Windows),
inline `hooks` blocks in Copilot settings, and plugin-declared `hooks.json`.
Cloud agent reads only `.github/hooks/*.json` from the cloned repository.

### Config format

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      {
        "type": "command",
        "bash": "./scripts/check.sh",
        "powershell": "./scripts/check.ps1",
        "cwd": ".",
        "env": { "LOG_LEVEL": "INFO" },
        "timeoutSec": 15
      }
    ]
  }
}
```

### Events

Configure events in **camelCase** (native, fields camelCase) or **PascalCase**
(VS Code / Claude-compatible, fields snake_case). Command hooks are the default;
**`http`** hooks POST the payload to a URL, and **`prompt`** hooks (`sessionStart`
only, and **new interactive sessions only** -- not resume, not `-p`) auto-submit
text or a slash command.

| Event | Fires when | Output |
|-------|-----------|--------|
| `sessionStart` | New or resumed session begins | Can inject **`additionalContext`** |
| `sessionEnd` | Session completes or terminates | Ignored |
| `userPromptSubmitted` | User submits a prompt | Command/HTTP output ignored; SDK programmatic hooks can rewrite |
| `userPromptTransformed` | Prompt is transformed into model-facing content | Can rewrite `modifiedTransformedPrompt` |
| `preToolUse` | Before any tool invocation | **Allow/deny/modify** -- `{"permissionDecision":"deny","permissionDecisionReason":"..."}` or `modifiedArgs` |
| `postToolUse` | After a tool completes successfully | **Inject `additionalContext`** (appended to the result, same turn) or `modifiedResult` |
| `postToolUseFailure` | After a tool fails | Recovery guidance via **`additionalContext`** |
| `notification` | Async CLI notification (`shell_completed`, `agent_completed`, `agent_idle`, `permission_prompt`, ...) | Can inject **`additionalContext`**; fire-and-forget, never blocks |
| `permissionRequest` | Before the permission service runs | `{"behavior":"allow"|"deny"}` (CLI only; great for `-p`/CI) |
| `preCompact` | Before context compaction (manual/auto) | Ignored |
| `agentStop` | Main agent finishes a turn | **Block** -- `{"decision":"block","reason":"..."}` forces another turn |
| `subagentStart` | A sub-agent is spawned | `additionalContext` prepended to its prompt |
| `subagentStop` | Sub-agent completes | **Block** (force another turn) |
| `errorOccurred` | Error during agent execution | Ignored |

> **`additionalContext` is the declarative way to talk to the model.** Several
> events (`postToolUse`, `notification`, `sessionStart`, `postToolUseFailure`)
> let a hook write `{"additionalContext": "..."}` to stdout and the string is
> surfaced to the model. This is the supported replacement for the **removed**
> extension SDK `onPostToolUse` callback: a command hook can read a small
> **state file** (e.g. a sidecar maintained by a background process) and inject
> a nudge when some condition holds -- no `extension.mjs` required. Multiple
> hooks' `additionalContext` are joined (double newline) and capped at 10 KB.

> **Hooks are reactive -- they can't originate or schedule a turn.** Every hook
> fires in response to activity the session is already producing. The only hook
> that injects a *follow-up prompt* is `agentStop` with
> `{"decision":"block","reason":"..."}` (the `reason` becomes a new user turn,
> verified) -- but it fires only at a turn boundary, so it's a continuation
> loop, **not a scheduler**, and never fires once the agent is idle. No hook
> fires on a clock or from an external/async event; `notification` is
> fire-and-forget, has no turn-forcing output, and does not fire in
> non-interactive mode. Waking an idle session asynchronously (callbacks, peer
> messaging, scheduled prompts) still needs `session.send()` (an extension) or
> the runtime's own scheduled prompts -- not a hook.

### Plugin-contributed hooks

A plugin ships hooks in a **`hooks.json`** (or `hooks/hooks.json`) at the root of
its install dir, same `{ "version": 1, "hooks": { ... } }` format as a repo's
`.github/hooks/*.json`. The runtime **combines** hooks from all sources (policy,
repo, user, plugins); when an event appears in several sources every entry runs.
Two consequences shape how a plugin author writes them:

- **A plugin hook fires for every session the plugin loads into** -- there is no
  per-repo `matcher` on `sessionStart`. A repo-scoped plugin's hooks fire only in
  its repo, but a **globally**-installed plugin's `sessionStart` hook fires in
  *every* repo. So the hook **script must self-gate**: read the payload's `cwd`
  (and `source`) from stdin and decide whether -- and what -- to emit. This
  cwd-gating is what lets one plugin **target its emission at the calling repo**
  (the whole premise of using a hook to replace a per-project instructions dir).
- **Reference the plugin's own files by absolute path.** The hook's `cwd` is the
  *session's* directory, not the plugin's, so a plugin hook typically shells to a
  script under its install dir (`~/.copilot/installed-plugins/<marketplace>/<plugin>/...`)
  or a deployed sidecar under `~/.<tool>/bin/`, guarded by a `Test-Path` / `[ -f ]`
  existence check so a partial install fails open. Keep it under the perf budget
  below; do expensive work in a background process and have the hook read a cheap
  state file.

### sessionStart context injection

`sessionStart` is the declarative replacement for a deployed
`COPILOT_CUSTOM_INSTRUCTIONS_DIRS` instructions dir: a command hook reads the
start payload from stdin and writes `{"additionalContext": "<guidance>"}`, and the
string is injected into the session as context -- **dynamically computed, and
targeted by `cwd`**. Unlike a **`prompt`**-type `sessionStart` hook (new
interactive sessions only -- not resume, not `-p`), the `additionalContext`
mechanism also applies on **resume**, so it can carry the always-on guidance an
instructions file used to. Shape:

```json
{"cwd":"/path/to/repository","source":"<runtime-provided value>"}
```

```text
read one JSON payload from stdin using the platform standard library
extract cwd and source without assuming a source vocabulary
resolve cwd and applicable repository markers/config to canonical paths
if cwd/config proves applicability and source is not explicitly excluded:
    emit {"additionalContext":"[owner: example-plugin@1.2.3]\n<concise kernel>"}
else:
    emit {}
```

Reference output:

```json
{"additionalContext":"[owner: example-plugin@1.2.3]\n<concise kernel>"}
```

Discipline (context is a **shared, capped** resource -- 10 KB across all hooks):

- **Inline only what every turn needs**; for the rest, inject a short pointer -- a
  one-line summary plus a **backtick faux-link** to a file the agent reads on
  demand (`` `~/.my-tool/notes.md` ``) -- rather than pasting the whole document.
- **Mark ownership.** Begin every injected kernel with a stable owner marker:
  the plugin name, preferably plus its version. Budget reports and diagnostics
  need to attribute the emitted bytes.
- **Self-gate hard on applicability.** Emit `{}` (not a partial nudge) when
  resolved cwd/config does not prove the policy applies. Prefer a capability or
  repository marker; use configured target-root lists only as a fallback. Never
  use substring matching or repository-name inference.
- **Allow source by default.** Source is an opaque runtime value. Exclude only
  explicitly documented incompatible sources; do not invent an allowlist.
  Command-hook `additionalContext` applies on resume, so preserve resume
  behavior.
- **Treat configuration as data with bounded delegation.** Apply documented
  repository-over-operator-over-default precedence only for an explicit
  plugin-declared allowlist of repo-delegable keys. Reject unknown keys and
  unauthorized repository overrides. Safety, publication, attribution, and
  sanitization policy remains operator/plugin-owned and non-overridable. Never
  source, import, or execute configuration.
- **Fail open.** Missing optional config or hook errors emit `{}` and may log to
  stderr; they do not block startup. Retain a minimal static fail-safe for
  critical rules on launch paths that do not load plugin hooks.
- **Own written fallbacks.** If setup writes compatibility/fallback prose into
  `AGENTS.md` or custom instructions, idempotently reconcile it inside a stable
  region naming the plugin (or a dedicated plugin-named rule file). Future setup
  versions use the same marker to update, shrink, or remove the fallback without
  duplicate text or collateral edits.
- **Prefer this over a fixed instructions dir** when the guidance is dynamic
  (machine/account/state-derived), conditional, or repo-scoped; keep the static
  dir for genuinely always-identical text.

This section is the portable normative summary for installed skills. The suite
design rationale remains in the copilot-extensions repository's
`docs/patterns/context-injection.md`.

### Script I/O

- **Input:** read all of stdin as JSON (`jq` in bash, `ConvertFrom-Json` in
  PowerShell). Tool hooks also receive `toolName` / `toolArgs`; post-tool hooks
  include `toolResult`.
- **Output:** a single JSON object on stdout. Decision/injection events read it
  -- `preToolUse` (`permissionDecision`/`modifiedArgs`), `postToolUse` /
  `postToolUseFailure` / `notification` / `sessionStart` (`additionalContext`),
  `permissionRequest` (`behavior`), `agentStop` (`decision`). Emit **exactly
  one** final JSON object (progress lines `{"type":"progress",...}` are stripped
  first; two decision objects concatenate into invalid JSON and are ignored).
  Other events ignore stdout.
- **Stderr:** debug logging, ignored. **Exit code:** 0 = success.
- **Performance:** hooks run synchronously and block the agent -- keep them under
  5 seconds; background expensive work.
