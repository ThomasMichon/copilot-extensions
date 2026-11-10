# harness-knowledge

Payload-only binding plugin for a **stateless** Copilot CLI control harness and
its private **knowledge** repo. It ships a setup skill plus small configurator
scripts; there is no runtime, venv, binstub, hook, or installer. Enable the
plugin, then run the **`binding-knowledge`** skill once for the machine.

An explicitly selected topology can carry both roles as
`control_plane: {project: example-harness, harness: example-harness,
knowledge: example-knowledge}`. The scalar `project` keeps older daemons
launching in the harness; the explicit pair retains the companion relationship
for first-touch binding. With both repositories canonically registered,
`bind_knowledge.py --control-plane <machines.yaml> --agent-worktrees-path
"<exact native command>" --json` reuses this plugin's existing writer after
native role/registration/configuration-root checks. It does not require a
running Bridge, silently re-point an existing binding, or turn knowledge into
an executable project. See the skill for unsupported-reader and registration
gates.

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
flow, provider-before-authentication checks, publication policy and re-pointing
steps. Registration/state-root readiness does not establish server landing
policy or authorize a direct push. Repository/provider PR and review requirements
still apply; unavailable policy evidence remains unknown, not permission to
trial-push a default branch.

## Provider policy read

Before selecting authentication or proposing default-branch publication, the
binding payload can consume the native owner's
[registration/provider preflight](../agent-worktrees/docs/repository-registration-preflight.md):

```text
python skills/binding-knowledge/scripts/bind_knowledge.py --preflight --knowledge <name> --agent-worktrees-path "<catalog argv[0]>" --json
```

This mode performs only the bounded read. It does not call the binding writer,
register/clone/create a repository, change credentials or mutate Git. Binding
and other effect options cannot be combined with it. Ordinary binding retains
its existing behavior and does not automatically request this observation.
The exact payload's existing `--capabilities` read advertises
`repository_registration_preflight: 1` for callers selecting this mode.

The consumer reads the exact selected native command's existing inventory
capability before emitting the optional new operation. Missing/unsupported old
support returns an explicit unknown policy without probing new syntax.
A failed, malformed, wrong-target or rolled-back native read is not replayed
through another account or effectful fallback. Partial policy remains separate
from independently valid local binding; repository publication rules still
govern every later effect.

## Machine-local artifacts

Manager's native Repositories contribution can invoke this same owner for a
confirmed **existing** canonical knowledge/worktree repository. Its
`knowledge-preview` / `knowledge-apply` CLI delegates to
`bind_knowledge.py --configuration preview|apply`, with exact selected
harness/knowledge names and the native engine entry. Preview is read-only;
apply requires the returned token and rechecks the canonical registration and
configuration baseline inside the existing pointer lock. Capability version 2
routes apply through the native registration transaction, which remains locked
through pointer commit; the internal `apply-locked` delegate is not a standalone
apply API. It never registers,
clones, creates a repository or silently replaces a conflicting binding.
An incomplete post-write result reports the committed pointer explicitly,
including failures during legacy cleanup after the write. Plugin composition
keeps its native delegate windowless on Windows.
See [Repositories Configuration](../agent-worktrees/docs/repositories-configuration.md#confirmed-existing-knowledge-binding).

The native preview/apply worker may provide a private, read-only in-process adapter.
Apply retains that registration lock; preview takes no writer lock.
The adapter runs the existing fresh inventory and
selected-project config/state handlers without repeated interpreter startup;
all validation and pointer rechecks remain. Writes, authentication, composition
and unsupported reads retain their ordinary subprocess path. Without the
optional adapter the payload behaves as before; no new CLI, execution grant,
cached-read authority or changed native deadline is introduced.

For portable-ID apply, `portable_binding_precondition: 1` advertises an
additional native-worker callback at that same pointer-write precondition.
It rechecks the confirmed portable resolution, including logical machine scope,
after registry-lock waits. Workers require this capability before portable apply;
older name-based version-2 callers retain their existing behavior.

`binding_precondition_freshness: 1` additionally guarantees that the writer
rechecks the exact captured configuration bytes after that callback and just
before replacement. Concurrent unrelated edits or file creation/removal refuse
without a pointer-commit receipt, including an otherwise already-matching
binding. Accepted-default consumers require this additive capability; existing
explicit callers keep their previous capability contract.

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

## Development checks

The `harness-knowledge` typecheck project covers the configurator scripts
including the extracted `binding_commands.py` adapter and its test modules,
without exclusions. Run
`python tools/typecheck.py harness-knowledge` from the repository root for
strict Python 3.10 checks on both Linux and Windows targets; use `--prepare`
first when the checker environment is missing. Native command and platform-lock
protocols describe the existing delegated interfaces. JSON input stays dynamic
until the existing shape validators have accepted it; the annotations do not
change registration, binding, or overlay ownership.
