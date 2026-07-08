# Control-Harness Runbook

> **Audience: an agent.** This is an opinionated, step-by-step runbook for
> turning a repo into an effective **agent harness** — a control-plane repo
> that drives Copilot CLI sessions, cross-agent/cross-machine work, planning,
> and MCP-backed tools using the `copilot-extensions` plugin suite.
>
> It is deliberately **opinionated about the harness itself** and deliberately
> **unopinionated about the product** the harness operates on. Read the
> [Opinion contract](#the-opinion-contract) before you touch anything.

For the *concepts* behind a control-harness repo (what it is, why one repo),
read the [README § Concepts](../README.md#concepts-the-control-harness-repo)
first. This runbook is the *procedure*; the README is the *why*.

---

## How to use this runbook

You will be invoked in one of three modes. Detect which from the operator's
ask and the state of the current directory.

| Mode | Operator says something like | Starting point |
|------|------------------------------|----------------|
| **Greenfield** | "Make me a control repo like this" | An empty/vanilla folder (e.g. a home dir). No repo yet. |
| **Brownfield** | "Build out my harness like this" | An existing repo that is not yet a harness. |
| **Audit** | "Make sure my repo follows harness best practices" | A repo already wired as a harness (possibly by an older version of this system). |

A fresh agent launched in a vanilla folder can reach this runbook by
**fetching it from the repo URL** — no plugins need be installed yet. Fetch
this file (`docs/harness-runbook.md`) plus the
[README](../README.md), then follow the phases below. The early phases install
the very plugins whose skills the later phases lean on.

> **In a loaded session**, the `customizing-copilot` plugin's
> **`building-harnesses`** skill is the trigger-discoverable entry point to this
> runbook (it routes here and frames the run), and **`reviewing-customizations`**
> operationalizes [Phase 8](#phase-8--validate-skills-and-agents).

**All three modes run the same phases.** The difference is only whether each
phase *creates*, *extends*, or *verifies*:

- **Greenfield** — create everything from scratch.
- **Brownfield** — add what is missing; leave the operator's existing product
  code untouched.
- **Audit** — treat every phase's **"Done when"** as a checklist; report drift
  and fix it in place. See [Audit mode](#audit-mode) for the condensed pass.

**Drive it, don't dictate it.** Where a phase names an *unopinionated seam*,
ask the operator (use a structured question) rather than guessing. Where a
phase is *opinionated*, apply the opinion and move on — don't relitigate it.

---

## The opinion contract

The single most important thing about this runbook: it draws a hard line
between the **harness** (opinionated) and the **product** (unopinionated).

### Opinionated — the harness *is* the opinion

These are the load-bearing conventions. Apply them. Each maps to a phase.

1. **Repo structure** for the harness itself — [Phase 1](#phase-1--repo-structure).
2. **Repo-scoped plugin registration** via `.github/copilot/settings.json` +
   experimental mode — [Phase 2](#phase-2--register-repo-scoped-plugins).
3. **Adopting the harness and its related target repos with agent-worktrees** —
   [Phase 3](#phase-3--adopt-the-harness-and-related-repos).
4. **`AGENTS.md` + "connective-tissue" skills** that bind the generic plugin
   skills to *this* repo — [Phase 4](#phase-4--agentsmd-and-connective-tissue-skills).
5. **SSH mesh + agent-bridge topology** — [Phase 5](#phase-5--ssh-and-agent-bridge).
6. **End-to-end validation through the Picker** —
   [Phase 6](#phase-6--validate-end-to-end-with-the-picker).
7. **efforts + visions to guide change** — [Phase 7](#phase-7--enable-efforts-and-visions).
8. **rubber-duck + customizing-copilot to validate skills and agents** —
   [Phase 8](#phase-8--validate-skills-and-agents).
9. **agent-mcp + delegating MCP handling to sub-agents** —
   [Phase 9](#phase-9--agent-mcp-and-mcp-delegation).

### Unopinionated — leave these to the operator

Never impose a default on these. Detect the operator's existing choice, or ask.

1. **Target/product repo structure.** The harness does not dictate how the code
   it operates on is laid out.
2. **Where product code lives.** Do **not** replicate a full `tools/`/`services/`
   suite (generators, installers, a whole service framework) into the harness.
   The harness carries only **lightweight helpers** it needs to drive work; the real
   product lives in its own repos, adopted as *related* repos (Phase 3).
3. **Git provider / hosting.** GitHub, Gitea, GitLab, self-hosted — all fine.
   Commands here use forge-neutral git; only fetch/push remotes are
   provider-specific.
4. **Issue-tracking provider.** GitHub Issues, Gitea, Jira, none — the harness
   does not require any particular tracker.
5. **PR / review policy.** Whether changes go direct-to-default-branch or
   through PRs, whether reviews are required, who reviews — operator's call.
6. **Execution substrate.** Local checkout, Codespaces, or local dev
   containers. The plugin suite supports all three; pick per the operator.
7. **Voice / personality / theming.** The suite ships **voice-neutral**. Any
   persona, quips, or theming are host-injected and out of scope here.
8. **Additional harness-control mechanisms.** Schedulers, dashboards, custom
   dispatch policy, extra automation — welcome, but not prescribed.

> **When opinion meets seam:** you *will* create files (Phase 1) inside a repo
> whose product layout is unopinionated. That is fine — the harness files live
> in well-known locations (`.github/`, `docs/`, `efforts/`, `visions/`,
> `tools/setup/`) that don't collide with product code. Keep harness helpers
> lightweight and clearly separated.

---

## Recommended plugin set

The suite is **eleven plugins**. That is a lot to manage, and you should treat
this section as the **curated master list** — enable the tier the harness
actually needs, not all eleven by reflex. You will encode the choice in
`.github/copilot/settings.json` in [Phase 2](#phase-2--register-repo-scoped-plugins).

| Plugin | Tier | Enable when |
|--------|------|-------------|
| `agent-worktrees` | **Core** | Always. Session isolation + repo adoption + the Picker. Install first. |
| `customizing-copilot` | **Core** | Always. Teaches authoring skills, sub-agents, MCP servers, plugin installs. Payload-only. |
| `efforts` | **Recommended** | The harness plans stretches of work. Payload-only. |
| `visions` | **Recommended** | The harness keeps a north-star and derives efforts from the delta. Payload-only. |
| `agent-bridge` | **Recommended** | More than one machine/agent, or you want inter-agent sends. |
| `context-handoff` | **Recommended** | Long sessions that risk auto-compaction. Payload-only. |
| `agent-mcp` | **Optional** | The harness wraps an authenticated MCP server for its tools. |
| `agent-logger` | **Optional** | You want structured session logs. |
| `agent-codespaces` | **Optional** | Execution substrate includes GitHub Codespaces. |
| `agent-containers` | **Optional** | Execution substrate includes local Docker dev containers. |
| `agent-dispatch` | **Optional** | Multiple agents must coordinate through an atomic task queue. |

**Minimum viable harness:** `agent-worktrees` + `customizing-copilot`.
**Recommended default:** add `efforts`, `visions`, `agent-bridge`,
`context-handoff`. Everything else is opt-in by substrate/need.

> **On consolidation.** Managing many plugins is awkward; this runbook is the
> single place to curate the set. Keep the tiers above as the recommendation and
> **adjust the repo's `enabledPlugins` to match the operator's actual needs** —
> don't enable an optional plugin "just in case." If the suite later consolidates
> into fewer plugins, this table is the seam to update; the phases reference
> capabilities (worktrees, bridge, efforts…), not a fixed plugin count. Whether
> to repackage the suite itself is weighed in
> [docs/plans/plugin-consolidation.md](plans/plugin-consolidation.md).

---

## Phase 0 — Prerequisites

**Opinionated.** Every harness assumes these.

- **Copilot CLI** (`copilot` on PATH), **Python 3.10+**, **Git 2.15+**.
- **`uv`** — bootstrapped automatically by the plugin installers if missing.
- **Experimental mode on, once per machine** — the CLI gates *all* extension
  loading on it. In `~/.copilot/settings.json`:
  ```json
  { "experimental": true }
  ```
- Provider CLIs only as needed by chosen optional plugins (`gh` for
  Codespaces/Containers; `docker` for Containers).

**Done when:** `copilot --version` works and `~/.copilot/settings.json` has
`"experimental": true`.

---

## Phase 1 — Repo structure

**Opinionated** about the harness scaffold; **unopinionated** about product
layout.

Greenfield: `git init` the harness repo. Brownfield/Audit: work in place.

Create only these harness-owned locations (leave everything else to the
operator's product):

```
<harness-repo>/
  .github/
    copilot/
      settings.json          # marketplace + enabledPlugins (Phase 2)
    skills/                  # connective-tissue skills (Phase 4)
    hooks/                   # optional guardrail hooks (Phase 4)
  AGENTS.md                  # harness identity + conventions (Phase 4)
  docs/                      # harness docs (what IS)
  efforts/                   # planning (Phase 7; scaffolded by efforts-setup)
  visions/                   # north-star (Phase 7; scaffolded by visions-setup)
  tools/setup/               # session setup script(s) (optional; see below)
  machines.yaml              # topology, if multi-machine (Phase 5)
  acp-agents.json            # bridged agents, if any (Phase 5)
```

**Keep it lightweight.** Do **not** scaffold a `services/` tree, tool
generators, or installers unless the harness genuinely needs a helper. The
product's real `tools/`/`services/` live in the *product* repos, adopted as
related repos in Phase 3. `tools/setup/` here is only for the **session setup
script** (below) and small harness helpers.

**Session setup script (optional but recommended).** If sessions should run
setup before the agent launches (install deps, print status, set env), use the
**`create-setup-script`** skill to generate an ACP-safe
`tools/setup/setup.ps1` / `setup.sh`. The script **must launch `copilot` last**
and must pass through `--acp`/`--stdio` unchanged.

**Done when:** the harness locations above exist (only those the harness needs),
and product code is untouched.

---

## Phase 2 — Register repo-scoped plugins

**Opinionated:** register plugins **at repo scope**, not globally. It pins the
set to the repo, keeps machines consistent, and lets the launcher keep payloads
and runtimes fresh automatically. (Skill: **`installing-plugins`**.)

Write `.github/copilot/settings.json`, declaring the marketplace and the
[tiered set](#recommended-plugin-set) the operator chose:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" }
    }
  },
  "enabledPlugins": {
    "agent-worktrees@copilot-extensions": true,
    "customizing-copilot@copilot-extensions": true,
    "efforts@copilot-extensions": true,
    "visions@copilot-extensions": true,
    "agent-bridge@copilot-extensions": true,
    "context-handoff@copilot-extensions": true
  }
}
```

Trim or extend `enabledPlugins` to the operator's needs — add
`agent-mcp`, `agent-logger`, `agent-codespaces`, `agent-containers`,
`agent-dispatch` only where a later phase or the operator calls for them.

**Restart before you rely on the new skills.** Plugins are scanned at session
**startup**, so a session that *wrote* `settings.json` does not yet have the
newly enabled skills. In greenfield/brownfield, the order is: write
`settings.json` → **restart Copilot CLI from inside the repo** → verify the
plugin skills loaded → then continue. (agent-worktrees especially only takes
effect after a restart.)

**Deploy the runtimes.** Payload registration only vendors skills/hooks; runtime
plugins also need a venv + binstub deployed once. Split by installer:

- **agent-worktrees, agent-bridge, agent-codespaces, agent-containers,
  agent-mcp** — run the **`copilot-extensions-setup`** skill ("set up copilot
  extensions"); it installs each under `~/.agent-*` with binstubs in
  `~/.local/bin`.
- **agent-logger** — deploy via its own installer / the **`session-sync-setup`**
  skill (not covered by `copilot-extensions-setup`).
- **agent-dispatch** — deploy via its own `scripts/init.*` / the
  **`agent-dispatch`** skill.
- **Payload-only** (`customizing-copilot`, `efforts`, `visions`,
  `context-handoff`) — nothing to deploy beyond being enabled.

**Done when:** `.github/copilot/settings.json` lists the chosen set, the session
has been restarted so the skills are live, `agent-worktrees --version` works,
and (if enabled) `agent-bridge version` works.

---

## Phase 3 — Adopt the harness and related repos

**Opinionated:** the harness is **adopted by agent-worktrees** (its own
worktree root + project binstub), and every **product/target repo** the harness
drives is registered as a **related** repo — not copied in.

### Adopt the harness repo

From inside the harness repo (skill: **`copilot-extensions-setup`** §2, or the
**`agent-worktrees-repos`** skill):

```bash
agent-worktrees register <harness-repo-name>
```

This writes `~/.<harness>/config.yaml`, picks a worktree root
(`<parent>/.worktrees/<harness>/`), and drops a project binstub
`~/.local/bin/<harness>` — the command that launches the Picker (Phase 6).

### Adopt related target repos

The harness *drives* other repos; it does not absorb them. For each product
repo, first make sure agent-worktrees knows the repo — register it in the
per-machine **repos registry** (skill: **`agent-worktrees-repos`**; e.g.
`agent-worktrees repos add <name> <path-or-remote>`) so `related resolve` can
report its class, path, and remote. Then use the **`agent-worktrees-related`**
skill to link it and write a **related narrative**
(`.agent-worktrees/related/<name>.md`) capturing that repo's point of view — its
class (reference / singleton / worktree), locus (local / a machine / a
codespace), and how to make a change there.

```bash
agent-worktrees related resolve <name>        # reports class, path, locus, plan
agent-worktrees related resolve <name> --json
```

Then, when acting across repos, follow the **`working-cross-repo`** skill: honor
the repo's management **class**, its **locus**, prefer **delegation** over
reaching across machines, and never hardcode a checkout path (resolve with
`agent-worktrees repos find <name>`).

> **This is the seam that keeps the harness unopinionated about the product.**
> Target repo structure, where product code lives, git/issue providers, and
> PR policy are all properties of the *related* repo, recorded in its narrative —
> not imposed by the harness.

**Done when:** `<harness>` launches the Picker; each product repo resolves via
`agent-worktrees related resolve <name>` with a concrete plan and a narrative.

---

## Phase 4 — `AGENTS.md` and connective-tissue skills

**Opinionated:** the plugin skills are **generic**; the harness supplies the
**connective tissue** that binds them to *this* repo. Two surfaces do that.
(Skills: **`authoring-skills`**, **`defining-subagents`** from
`customizing-copilot`.)

### `AGENTS.md` — the harness's identity and rules

Author (greenfield) or reconcile (brownfield/audit) a root `AGENTS.md` that
states, concisely:

- **What this harness is** and what it drives.
- **Conventions** the harness enforces: branch/commit policy, how work is
  planned (efforts), how change is reconciled to intent (visions), and any
  **destructive-action** and **error-response** discipline the operator wants.
- **Pointers, not prose** — reference the skills below and the plugin skills
  rather than restating them. `AGENTS.md` is a table of contents with rules,
  not a manual.

Keep it **voice-neutral** unless the operator explicitly wants personality
(unopinionated seam #7).

### Connective-tissue skills (`.github/skills/`)

Thin, repo-local skills that stitch generic capability to local reality. Common
kinds:

- **Repo redirect / narrative pointers** — a thin trigger-skill per related
  repo that routes to its narrative (substance stays in the narrative, per
  Phase 3).
- **Machine/context skills** — one per machine the harness runs on (hardware,
  paths, local scope), loaded on demand.
- **Binding addenda** — the short efforts/visions repo addenda (Phase 7) that
  specialize the generic pattern to this repo.
- **Domain glue** — any repo-specific convention worth a trigger phrase.

Author these with the **`authoring-skills`** skill (SKILL.md frontmatter,
folder convention, validation checklist). Prefer **many small, well-triggered
skills** over one giant skill — but only add a skill when a real trigger
justifies it (mind context budget).

> **Knowledge goes into reviewable flows, not agent memory.** Conventions →
> docs + skills; invariants → an architecture/contract doc; intent → a vision;
> plans → an effort. This is what makes the harness *self-reinforcing*: the
> validation flows in Phase 8 read these files.

**Done when:** `AGENTS.md` names the harness's conventions and points at skills;
`.github/skills/` holds the connective-tissue skills the harness needs; each
passes the `authoring-skills` validation checklist.

---

## Phase 5 — SSH and agent-bridge

**Opinionated** *if* the harness spans more than one machine or wants
inter-agent sends. Single-machine, single-agent harnesses may **skip** this
phase (agent-bridge still works locally via `agent-bridge send local ...`).

### SSH mesh — aliases, never raw IPs

Use the **`agent-ssh`** skill. Define a named SSH alias for every machine in the
mesh (encoding user, port, key, and any ProxyJump). **Never** put a raw IP
in an SSH command — aliases survive IP changes and off-network access.

### agent-bridge topology

Describe the mesh in two repo files (templates + guidance live in the installed
agent-bridge plugin payload / the upstream `copilot-extensions` repo at
`plugins/agent-bridge/docs/machine-config.md` — read it from there, not from the
harness):

- **`machines.yaml`** — machines, platforms, SSH aliases.
- **`acp-agents.json`** — the agents the bridge can address.

Then wire and start (skill: **`copilot-extensions-setup`** §3–4, or the
**`agent-bridge`** skill):

```bash
agent-bridge config adopt --repo . --profile <harness>
agent-bridge service restart
agent-bridge machines && agent-bridge agents
```

**Unopinionated:** how many machines, their roles, and the execution substrate
(local / codespace / container) are the operator's — the bridge just reads what
you record.

**Done when:** `agent-bridge send local "..."` returns; and, if multi-machine,
listing `agent-bridge agents` shows the remote agent and
`agent-bridge send <agent-name> "..."` returns over SSH.

---

## Phase 6 — Validate end-to-end with the Picker

**Opinionated:** the harness is not "done" until a real session runs through the
**Picker** — the interactive worktree launcher the project binstub opens.

Launch it:

```bash
<harness>            # the project binstub from Phase 3 → opens the Picker
```

Walk one full lifecycle:

1. **Create/pick a worktree** from the Picker; confirm the session starts in an
   isolated worktree (not the anchor).
2. Confirm the **session setup script** ran (if you added one in Phase 1) and
   that plugins loaded (skills available; `agent-worktrees status`).
3. Do a trivial edit, **commit on the worktree branch**, and take it through the
   harness's chosen finalization path (unopinionated seam #5): for direct-to-
   branch, `agent-worktrees push-changes` **then** `agent-worktrees finalize`;
   for a PR flow, `agent-worktrees create-pr`, then merge/reconcile the PR, then
   `agent-worktrees finalize` to clean up.
4. If Phase 5 is active, from the session run an `agent-bridge send <agent-name>`
   (list agents with `agent-bridge agents`) to a second agent/machine and
   confirm the round-trip.

**Done when:** a worktree session launches from the Picker, plugins/skills load,
and a trivial change completes the harness's finalization path cleanly.

---

## Phase 7 — Enable efforts and visions

**Opinionated:** the harness plans work as **efforts** and steers change against
**visions**. Adopt both (payload-only plugins from Phase 2).

### efforts — the planning system

Run the **`efforts-setup`** skill ("set up efforts"): it scaffolds `efforts/`
(README index + TEMPLATE) and writes a short **repo addendum** specializing the
bindings (grouping, participants seam, archive layout). Day-to-day work uses the
**`planning-efforts`** skill. Start new planning as an **effort**, not an ad-hoc
`docs/plans/*.md`.

### visions — the north-star

Run the **`visions-setup`** skill ("set up visions"): it scaffolds `visions/`
(README index + TEMPLATE) and writes the repo addendum (chiefly the
organization seam). Day-to-day work uses the **`envisioning`** skill. A vision is
**pure should-be**, revised in place; **efforts are carved from the delta**
between a vision and reality.

**How they steer change (bake this into `AGENTS.md`):** every architectural or
behavioral change reconciles to the vision — it either *closes* a stated gap
(cite the vision item), *extends* intent (revise the vision first), or is
*below-altitude* (trivial; just say so). Visions **guide**, never **gate**.

**Done when:** `efforts/` and `visions/` exist with their addenda; `AGENTS.md`
references the reconcile-to-vision habit and the "plan as an effort" rule.

---

## Phase 8 — Validate skills and agents

**Opinionated:** the harness's own skills and sub-agents get **reviewed** before
they are trusted. Two tools do this. In a loaded session this whole phase is the
**`reviewing-customizations`** skill (`customizing-copilot`) — trigger it with
"review my skills" / "rubber-duck my agents"; the steps below are what it runs.

### rubber-duck — critique the design

Use a **review sub-agent** to critique the harness's skills, sub-agent
definitions, `AGENTS.md`, and any hooks for logic gaps, ambiguous triggers,
contradictory rules, and footguns — the Copilot CLI's built-in **`rubber-duck`**
task sub-agent where available, or any equivalent reviewer the harness provides.
It reports bugs and design flaws, not style. Feed it the actual files and act on
high-signal findings.

### customizing-copilot — validate against the format

Cross-check each authored artifact against its authoring skill:

- **Skills** → **`authoring-skills`** (frontmatter, folder convention,
  trigger phrasing, validation checklist).
- **Sub-agents** → **`defining-subagents`** (`.agent.md` format, tool aliases,
  per-agent MCP ownership, the anti-recursion / MCP-readiness pattern).
- **MCP servers** → **`registering-mcp-servers`** (registration hierarchy,
  config format, env substitution).
- **Plugin registration** → **`installing-plugins`** (repo `settings.json`,
  payload-vs-runtime, launch-time reconciliation).

**Done when:** rubber-duck reports no high-severity issues on the harness's
skills/agents, and each artifact conforms to its `customizing-copilot` skill's
checklist.

---

## Phase 9 — agent-mcp and MCP delegation

**Opinionated** *if* the harness needs authenticated MCP tools: wrap them with
**agent-mcp** and **delegate MCP handling to sub-agents** rather than loading
heavy MCP toolsets into the primary session.

### Wrap an authenticated MCP with agent-mcp

`agent-mcp` wraps an upstream MCP server (HTTP or stdio) as a local **stdio**
MCP and injects **host credentials** (Entra/`az`, `gh`, git-credential, env) —
so no PATs are baked into config. It is **standalone**: invoked directly from an
agent's `mcp-servers` config, one bridge file per server. Prefer an **in-repo**
`--config` bridge for repo-scoped servers. (Skill: **`agent-mcp`**.)

### Delegate MCP to sub-agents

Give each MCP-backed capability its **own sub-agent** that owns that MCP server,
instead of registering many MCP tools on the primary agent. This keeps the
primary context lean and isolates credential scope. Define these with the
**`defining-subagents`** skill, and honor its **MCP-readiness / anti-recursion**
pattern: a sub-agent checks its MCP tools are actually available before using
them, and reports back to the host when they are not (the host can fall back to
a CLI). If `agent-dispatch` is in the set, its MCP surface is a natural fit for a
delegated queue sub-agent.

**Done when:** any authenticated MCP the harness uses is wrapped by an
`agent-mcp` bridge with host-credential injection, and each MCP capability is
owned by a delegated sub-agent (not piled onto the primary agent).

---

## Audit mode

For a repo already wired as a harness (including by an older incarnation of this
system), run every phase's **"Done when"** as a checklist and fix drift in
place. Highest-value checks, in order:

1. **Plugin registration is repo-scoped and current** (Phase 2). Old harnesses
   often relied on global installs or a stale plugin set — migrate to
   `.github/copilot/settings.json` and trim to the
   [recommended tiers](#recommended-plugin-set).
2. **Related repos use narratives, not copies** (Phase 3). If product code or a
   full `services/`/`tools/` suite was replicated into the harness, flag it and
   propose extracting it to a related repo.
3. **`AGENTS.md` points at skills instead of restating them** (Phase 4), and
   connective-tissue skills pass the `authoring-skills` checklist.
4. **efforts + visions are adopted with addenda** (Phase 7); planning lives in
   `efforts/`, not scattered `docs/plans/*.md`.
5. **SSH uses aliases, never raw IPs** (Phase 5).
6. **MCP is wrapped + delegated** (Phase 9), not a pile of raw tools on the
   primary agent.
7. **rubber-duck + customizing-copilot pass** (Phase 8).

Report findings as a prioritized list; fix the small ones in place (with atomic
commits) and surface the structural ones to the operator before acting.

---

## Where to go next

- [README](../README.md) — concepts, quick start, the full plugin catalog.
- [Architecture overview](architecture.md) — how the plugins fit together.
- Each plugin's `skills/` — the authoritative, always-current procedure for that
  capability. This runbook references those skills by name precisely so it stays
  thin and they stay the source of truth.
