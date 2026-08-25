# harness-knowledge

Payload-only binding plugin for a **stateless** Copilot CLI control harness and
its private **knowledge** repo. It ships a setup skill plus small configurator
scripts; there is no runtime, venv, binstub, or installer. Enable the plugin,
then run the **`binding-knowledge`** skill once for the machine.

A stateless harness holds the shareable *intelligence* (instructions, config,
skills, sub-agents) but no personal state. Personal state (efforts, logs,
visions, notes, artifacts, personal issues) lives in a separate knowledge repo,
bound per machine so the committed harness tree stays generic and name-free.
This matches the payload-only shape described in `docs/patterns/README.md`.

## Usage

The front door is the **`binding-knowledge`** skill:

1. Confirm the launch repo requires an external state root with
   `agent-worktrees state-root --json`.
2. Choose the knowledge repo: use an existing local checkout, clone a remote, or
   create a new private repo. The flow should fail before configuration if the
   chosen checkout is missing or is not a git repo; the configurator assumes the
   path it is given.
3. Register the harness and knowledge repos with `agent-worktrees repos add` so
   the state-root resolver can find them by name.
4. Run the idempotent configurator:
   `skills/binding-knowledge/scripts/bind_knowledge.py`.
5. Verify `agent-worktrees state-root --json` resolves the knowledge checkout.

See `skills/binding-knowledge/SKILL.md` for the exact commands, repo-creation
flow, and re-pointing steps.

## Machine-local artifacts

`bind_knowledge.py` writes or refreshes these local artifacts:

| Artifact | What it contains |
|----------|------------------|
| `~/.<harness>/config.yaml` | Top-level `knowledge_repo: <knowledge-name>` pointer read by the state-root resolver. Existing config content is preserved. |
| `~/.<harness>/knowledge-binding.md` | Managed instructions data labeling this machine's harness, knowledge, and optional product repo paths. The harness-knowledge `sessionStart` hook emits this file as additional context only when the current project resolves to that harness. |
| `<harness>/.github/copilot/settings.local.json` | Optional personal-plugin overlay, written when both `--harness-path` and `--knowledge-path` are supplied. The canonical `agent-worktrees knowledge compose-plugins` implementation carries local and operator-specific remote marketplaces/enables, preserves the committed harness base and unmanaged local settings, and is invoked automatically for paired launches. If the tracked pair becomes invalid, launch preflight retires only unchanged marker-owned pair values and fails closed when safe cleanup is impossible. Keep this file gitignored. |

The plugin never writes a knowledge or product repo name into committed harness
config and does not use `agent-worktrees related add` for the binding.

Re-running is safe: the pointer is replaced in place, the binding fragment is
regenerated, and the compatibility assembler delegates to agent-worktrees'
single managed-overlay implementation.
