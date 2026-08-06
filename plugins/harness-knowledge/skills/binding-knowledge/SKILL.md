---
name: binding-knowledge
description: >
  Harness-first setup: bind a stateless Copilot CLI control harness to its
  private knowledge repo on this machine. Use after cloning a stateless harness
  (or forking one) when it has no knowledge repo bound yet -- it asks for (or
  creates) the knowledge repo, registers both repos, writes the machine-local
  knowledge_repo pointer, and assembles a machine-local instructions fragment
  labeling the concrete harness/knowledge/product paths. Also use to re-point the
  harness at a different knowledge repo or repair a broken binding.
  Trigger phrases include:
  - 'bind the knowledge repo'
  - 'set up this harness'
  - 'harness setup'
  - 'connect my knowledge repo'
  - 'bind knowledge'
  - 'point the harness at my knowledge repo'
  - 'no knowledge repo is bound'
  - 'set knowledge_repo'
  - 'onboard this machine to the harness'
---

# Binding a stateless harness to its knowledge repo (harness-first)

A **stateless harness** is a shareable/forkable control plane that holds the
*intelligence* (instructions, config, skills, sub-agents) but **no personal
state**. Personal state -- efforts, logs, visions, notes, artifacts, personal
issues -- lives in a separate **knowledge** repo, bound **per machine**. This
skill performs that binding **harness-first**: you already cloned the harness;
now point it at your knowledge repo (or create one).

The harness tree stays **generic and name-free**. Everything concrete (the
knowledge repo's name + path, product repos) is written to **machine-local**
config, never committed into the harness.

## When to run

- Right after cloning/forking a stateless harness on a new machine.
- When `agent-worktrees state-root` reports the harness **requires an external
  state root but none is bound**.
- To re-point the harness at a different knowledge repo.

## Preconditions

Confirm the launch repo is a stateless harness:

```
agent-worktrees state-root --json
```

If `requires_external` is `true` and `bound` is `false` (or `state_root` is
null), it needs binding -- proceed. If it already resolves to a knowledge path,
it's bound; only continue to **re-point** it.

## 1. Decide the knowledge repo (ask, don't assume)

Ask the operator (use the ask-user affordance) for the knowledge repo, offering
three ways:

| Option | What you need | Then |
|--------|---------------|------|
| **Use an existing local checkout** | its path | verify it's a git repo |
| **Clone an existing remote** | the remote URL + where to clone | `git clone <url> <path>` |
| **Create a new one** | a name (+ owner/visibility) | create it (below) and clone |

Do **not** hardcode or guess a name -- the whole point is that a fork/other
operator chooses their own.

### Creating a new knowledge repo (option 3)

Prefer a **private** repo (personal state is sensitive). With `gh`:

```
gh repo create <owner>/<name> --private --clone --description "Personal knowledge repo for <harness>"
```

Seed it minimally (a README plus the state trees the harness routes to):
`efforts/`, `logs/`, `visions/` (with `.gitkeep`s). Commit + push. Knowledge
repos are **direct-commit, low-ceremony** -- no PR gate.

## 2. Register both repos with agent-worktrees

So the state-root resolver can find the knowledge checkout by name:

```
agent-worktrees repos add <knowledge-name> "<knowledge-path>" --class singleton
```

(The harness itself is normally already registered from its own adoption. If not,
register it too.)

## 3. Write the machine-local binding

Run the configurator (idempotent -- safe to re-run). It writes the
`knowledge_repo:` pointer into `~/.<harness>/config.yaml` and assembles the
machine-local instructions fragment labeling the concrete paths:

```
python skills/binding-knowledge/scripts/bind_knowledge.py \
  --harness <harness-name> \
  --knowledge <knowledge-name> \
  --knowledge-path "<knowledge-path>" \
  --harness-path "<harness-anchor-path>" \
  [--product <name>=<path> ...]
```

`--product` (repeatable) labels any coordinated product repos so the assembled
instructions name them for this machine. The fragment is **machine-local**
(loaded via `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`) -- it is **never** committed into
the harness, and it does **not** use `agent-worktrees related add` (that would
write a repo name into the harness's committed `related.yaml` and break
statelessness).

## 4. Verify

```
agent-worktrees state-root --json
```

Expect `requires_external: true`, `bound: true`, and `state_root` pointing at the
knowledge checkout. As a final proof, a fresh ask like *"start an effort for X"*
should land the effort in the **knowledge** repo, with the harness tree clean.

## Idempotence & re-pointing

Re-running is safe: the configurator replaces the `knowledge_repo:` line in place
(preserving the rest of the config) and rewrites the fragment. To re-point at a
different knowledge repo, register the new one (step 2) and re-run step 3 with the
new `--knowledge`/`--knowledge-path`.
