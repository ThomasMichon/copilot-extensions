# harness-knowledge

Payload-only binding plugin for a **stateless** Copilot CLI control harness and
its private **knowledge** repo. It ships a setup skill plus small configurator
scripts; there is no runtime, venv, binstub, hook, or installer. Enable the
plugin, then run the **`binding-knowledge`** skill once for the machine.

A stateless harness holds the shareable *intelligence* (instructions, config,
skills, sub-agents) but no personal state. Personal state (efforts, logs,
visions, notes, artifacts, personal issues) lives in a separate knowledge repo,
bound per machine so the committed harness tree stays generic and name-free.
This matches the payload-only shape described in `docs/patterns/README.md`.

## Usage

The front door is the **`binding-knowledge`** skill:

1. Inspect the launch repo with `agent-worktrees state-root --json`. A resolved
   path can still come from fallback discovery, so it does not replace the
   canonical registration check below.
2. Choose the knowledge repo: use an existing local checkout, clone a remote, or
   create a new private repo. The flow should fail before configuration if the
   chosen checkout is missing or is not a git repo; the configurator assumes the
   path it is given.
3. Run the single idempotent registration-and-binding command:
   `skills/binding-knowledge/scripts/bind_knowledge.py
   --agent-worktrees-path "<catalog argv[0]>" --register`, along with the
   harness/knowledge names and exact checkout paths documented by the skill.
   Add `--account <writable-login>` when the repository owner is an organization
   with no usable account mapping.
4. Require both `registration.status: ready` and `state_root.status: ready`.
   The registration result distinguishes `canonical_registry`,
   `fallback_discovery`, `unresolved`, and `unverified`, and reports the
   effective class, path, remote, default branch, and account.
5. Inspect the configurator's structured personal-issue routing status. A
   non-GitHub origin requires an explicit repository-owned GitHub route in the
   knowledge repo's `.agent-worktrees/config.yaml`.
6. Verify `agent-worktrees state-root --json` resolves the knowledge checkout
   and launch through the worktree picker so the harness worktree receives a
   paired knowledge worktree.

See `skills/binding-knowledge/SKILL.md` for the exact commands, repo-creation
flow, and re-pointing steps.

## Machine-local artifacts

`bind_knowledge.py` writes or refreshes these local artifacts:

| Artifact | What it contains |
|----------|------------------|
| `~/.<harness>/config.yaml` | Top-level `knowledge_repo: <knowledge-name>` pointer read by the state-root resolver. Existing config content is preserved. |
| `<harness>/.github/copilot/settings.local.json` | Optional personal-plugin overlay, written when both `--harness-path` and `--knowledge-path` are supplied. The canonical `agent-worktrees knowledge compose-plugins` implementation carries local and operator-specific remote marketplaces/enables, preserves the committed harness base and unmanaged local settings, and is invoked automatically for paired launches. If the tracked pair becomes invalid, launch preflight retires only unchanged marker-owned pair values and fails closed when safe cleanup is impossible. Keep this file gitignored. |

The configurator reads, but never modifies, the knowledge repo's
`.agent-worktrees/config.yaml` to report whether personal issue routing is
ready. State binding remains valid for any Git provider; GitHub issue filing is
reported as a separate required follow-up when the origin is non-GitHub and no
explicit route exists. Any route change is committed from a writable knowledge
worktree, never written directly into the registered anchor. The readiness
result validates configuration; the consuming harness remains responsible for
honoring the route in its personal issue-filing skill.

The plugin never writes a knowledge or product repo name into committed harness
config and does not use `agent-worktrees related add` for the binding.

Re-running is safe: the pointer is replaced in place, stale managed instruction
fragments are retired, and the compatibility assembler delegates to
agent-worktrees' single managed-overlay implementation. The agent-worktrees
`session-conduct` hook owns live binding, pairing, routing, and worktree context.
