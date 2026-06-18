# Rollout Readiness Plan — Team Onboarding for copilot-extensions

**Goal:** make the three-plugin suite (agent-worktrees, agent-bridge,
agent-codespaces) installable and usable by a teammate from a **fresh Windows
dev box** plus **their own control-harness repo**, using only the published
docs. End state: every module is installed **from the Copilot CLI marketplace**
and runs **from its local install path** (`~/.agent-*` + `~/.local/bin`) — no
dependency on a git checkout of this repo.

## Context

Teammates share the same ADO org and the same CodeSpaces repo
(configured per-team in their adopted repo's `codespaces.yaml`) but each
maintains their own **control-harness repo** (referred to throughout docs as
`my-control-harness`). That repo:

- is adopted by agent-worktrees (gives it a project binstub + worktree root),
- holds `machines.yaml`, `acp-agents.json`, and `codespaces.yaml`, and
- doubles as the **CodeSpaces dotfiles repo** (provisioned into each CodeSpace).

## Target install topology

```
Marketplace (GitHub: ThomasMichon/copilot-extensions)
        │  copilot plugin install <plugin>@copilot-extensions
        ▼
~/.copilot/installed-plugins/copilot-extensions/
    agent-worktrees/   agent-bridge/   agent-codespaces/      ← vendored source
        │ scripts/install*        │ scripts/install*    │ scripts/init*
        ▼                         ▼                     ▼
~/.agent-worktrees/        ~/.agent-bridge/        ~/.agent-codespaces/   ← local runtimes
~/.local/bin/agent-worktrees  ~/.local/bin/agent-bridge  ~/.local/bin/agent-codespaces
```

The agent-bridge venv additionally imports `agent_codespaces` (for the
`codespace:` resolver + credential relay) from the agent-codespaces install —
**without** owning the `agent-codespaces` binstub.

## Findings → Fixes

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | 🔴 | README installs only `agent-worktrees`; never says install all three | README Quick Start installs all 3; remove false "installs automatically" claim |
| 2 | 🔴 | Relay/`codespace:` support silently no-ops if agent-codespaces plugin absent | Make bridge installer **WARN loudly** on missing sibling; doc requires installing all 3; `status` surfaces "codespace support: available/unavailable" |
| 3 | 🟠 | No bootstrap for bridge/codespaces; setup skill omits codespaces | Add agent-codespaces to `copilot-extensions-setup`; single guided "set up everything" flow |
| 4 | 🟠 | agent-codespaces has two install homes → binstub conflict / version skew | Bridge installer installs the package into its venv for import only; **does not** write the `agent-codespaces` binstub. `~/.agent-codespaces` is canonical |
| 5 | 🟠 | codespaces-lifecycle SKILL uses relay port 9847 | Replace 9847 → 9857 (code default) |
| 6 | 🟠 | Linux/WSL bridge port is 9281 but docs say 9280 | Document platform default (9280 Win / 9281 Linux-WSL); fix health-check example |
| 7 | 🟡 | A personal project name leaks into teammate-facing strings | Genericize to `my-control-harness`; generic installer error/migration text; fix `~/.<personal-project>` temp-path bug |
| 8 | 🟡 | Repo AGENTS.md still says "two plugins" | Update to three plugins throughout |
| 9 | 🟡 | `agent-codespaces create`/`cleanup` undocumented | Document in README + codespaces-lifecycle SKILL |
| 10 | ⚪ | Codespace examples must stay generic | All org/repo/URL values live in the adopted repo's `codespaces.yaml`, never in this repo |
| 11 | ⚪ | Marketplace under personal account | Confirm teammate read access to `ThomasMichon/copilot-extensions` |

## Phases

### Phase 1 — Docs & portability (no runtime risk)
- Land these plans in `docs/plans/`.
- Overhaul README (purpose → Quick Start → usage flows → links) with Mermaid.
- Add repo-level `docs/architecture.md` component breakdown (Mermaid).
- Genericize the personal project name → `my-control-harness`; fix the temp-path bug.
- Port fixes (9847→9857; 9281 notes).
- Update AGENTS.md to three plugins.

### Phase 2 — Setup-flow correctness
- `copilot-extensions-setup` skill: install all 3 plugins from marketplace, run
  each installer so runtimes live under `~/.agent-*`; add agent-codespaces.
- getting-started docs: same canonical flow; remove checkout assumptions.

### Phase 3 — Installer hardening (validate on dev box)
- Bridge installer: stop clobbering the `agent-codespaces` binstub; emit a loud
  WARN (not silent skip) when the sibling package can't be found.
- `agent-bridge status` / `agent-codespaces status`: report codespace-relay
  availability.
- Re-run `pytest` in `plugins/agent-bridge/`.

### Phase 4 — Empirical validation
- Execute `docs/plans/fresh-devbox-validation.md` on a clean Windows dev box +
  a fresh `my-control-harness` repo. Every place the run must deviate from the
  written docs becomes a required fix before broad rollout.

## Done criteria
A teammate, using only the README + linked guides, reaches "send a prompt to my
CodeSpace through the bridge and it can authenticate to GitHub/ADO via the
relay," with all modules installed from the marketplace and running from local
install paths.
