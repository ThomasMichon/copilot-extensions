# Copilot instructions — copilot-extensions

Guidance for GitHub Copilot working in this repository. **These instructions are
read by every Copilot surface** — chat, the coding agent, and the automatic code
review agent — as well as by anyone browsing the repo, so they are written as
neutral engineering guidance, applying to *anyone* (human or agent) making
changes here.

For guidance specific to reviewing a pull request, see the dedicated
[`REVIEW.md`](../REVIEW.md) (read by Copilot code review specifically) rather
than this file -- keeping review-only directives out of the file that also
shapes Chat and coding-agent behavior.

The full guides are [`AGENTS.md`](../AGENTS.md) (development guide),
[`CONTRIBUTING.md`](../CONTRIBUTING.md) (versioning & release), and
[`docs/install-contract.md`](../docs/install-contract.md).

## What this repo is

A **Copilot CLI plugin marketplace**: a GitHub-hosted registry of plugins that
machines install via `copilot plugin marketplace add <owner>/<repo>`. Each plugin lives under
`plugins/<name>/` and is listed with its version in the marketplace catalog at
`.github/plugin/marketplace.json`. Plugins fall into two kinds:

- **Runtime plugins** (a venv, `~/.local/bin` binstubs, and/or a long-running
  service) — carry a `pyproject.toml`, `scripts/install.{sh,ps1}`, and an
  install skill.
- **Pure skill/hook/agent plugins** — ship only skills, hooks, and/or agents;
  no installer.

A typical plugin: `plugins/<name>/{src/,scripts/,skills/,tests/,plugin.json,
pyproject.toml}`. Shared libraries live under `libs/`.

## General standards — code quality

- **Python 3.10+**; type hints encouraged; docstrings for public functions.
- **Linter: ruff.** Changed Python should at minimum be clean on the high-signal
  **`F` (pyflakes)** and **`E9` (syntax)** rule groups — unused imports/variables,
  undefined names, and syntax errors. The repo carries pre-existing style debt.
- **Use `uv`** for dependency operations — never bare `pip`.
- **Tests: `pytest` per plugin.** Runtime plugins with a `tests/` suite should
  have new/changed behavior covered by tests. Keep required PR CI
  change-scoped and contract-focused; route exhaustive or subprocess-heavy
  cross-products to scheduled/manual workflows as defined in `TESTING.md`.
- **Commit messages:** imperative mood ("Fix Unicode crash on cp1252
  consoles"); reference this repo's GitHub issue numbers where applicable; keep
  the `Co-authored-by` trailer on Copilot-assisted commits.

## General standards — framework rules

These repo-specific rules must be followed by any change:

- **Version-bump triplet (most common miss).** Any change to a plugin's
  *payload* must bump that plugin's version in **all three** places, together in
  the same change:
  1. `plugins/<name>/plugin.json` (`version`)
  2. `plugins/<name>/pyproject.toml` (`version` under `[project]`) — runtime
     plugins only
  3. that plugin's entry in `.github/plugin/marketplace.json`

  A missing or partial bump makes machines silently skip the update ("already at
  latest"). Default bump is **patch with a `-devN` suffix**
  (e.g. `1.3.1 → 1.3.2-dev1`); do not bump minor/major unless the maintainer
  asks. A pure docs/CI change outside any `plugins/<name>/` payload (for example
  this file) needs **no** version bump.

- **Install contract.** A plugin that ships a runtime **must** provide
  `scripts/install.{sh,ps1}` plus an install skill; there is no shared vendored
  install module — each plugin's install flow is self-contained. Note that
  `copilot plugin update` refreshes only the payload, **not** the runtime.

- **Never copy source into runtime dirs.** Do not write into deployed runtime
  directories (e.g. `~/.<runtime>/`) or edit installed-plugin copies under
  `~/.copilot/installed-plugins/`. Fix the source here, bump, push, then update —
  hand-copying bypasses version tracking and the installer pipeline.

- **Cross-platform parity.** Installers and launchers generally ship both a
  `.sh` (Linux/WSL) and a `.ps1` (Windows) variant. A change to one usually needs
  the matching change to the other.

- **Public repo — stay identifier-neutral.** This repository is public. Do not
  introduce internal organization/account/project names, private hostnames, or
  personal aliases in code, docs, comments, examples, or PR metadata. Hidden
  HTML comments are still public. This repo must keep
  `pr.source_attribution: false`; closed-circuit repos may opt in through their
  own config. Use neutral placeholders.

- **Terminal status bars must not compute on the render path.** Nothing in a
  tmux/psmux `status-left` / `status-right` may spawn a process per render (no
  `#(...)` that shells out). The bar reads only precomputed values; a detached
  watcher computes segments off the render path. Regressions here cause severe
  input latency and are covered by guard tests.

## Full details

- [`REVIEW.md`](../REVIEW.md) — directives for the automatic code-review
  agent specifically.
- [`AGENTS.md`](../AGENTS.md) — repository structure, per-plugin lifecycles, and
  the complete "what NOT to do" list.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — the full versioning scheme, git
  hooks, and per-plugin deploy pipelines.
- [`docs/install-contract.md`](../docs/install-contract.md) — the install
  contract every runtime plugin must satisfy.
