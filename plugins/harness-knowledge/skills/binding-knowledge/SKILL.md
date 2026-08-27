---
name: binding-knowledge
description: >
  Harness-first setup: bind a stateless Copilot CLI control harness to its
  private knowledge repo on this machine. Use after cloning a stateless harness
  (or forking one) when it has no knowledge repo bound yet -- it asks for (or
  creates) the knowledge repo, registers both repos, writes the machine-local
  knowledge_repo pointer, and assembles a machine-local instructions fragment
  labeling the concrete harness/knowledge/product paths. When given both repo
  paths, it also renders the harness's machine-local personal-plugin overlay from
  the knowledge repo's local marketplaces. Also use to re-point the harness at a
  different knowledge repo or repair a broken binding.
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

Use the exact `argv[0]` from the agent-worktrees session command catalog for
state and repository operations below. Replace
`<agent-worktrees catalog argv[0]>` with the raw path and quote it at each
shell call site on POSIX; in PowerShell invoke it as
`& "<agent-worktrees catalog argv[0]>" <args>`.

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
- When `<agent-worktrees catalog argv[0]> state-root` reports the harness **requires an external
  state root but none is bound**.
- To re-point the harness at a different knowledge repo.

## Preconditions

Confirm the launch repo is a stateless harness:

```
<agent-worktrees catalog argv[0]> state-root --json
```

If `requires_external` is `true` and `bound` is `false` (or `state_root` is
null), it needs binding -- proceed. If it already resolves to a knowledge path,
it's bound; only continue to **re-point** it.

Fail loud on the chosen knowledge checkout before invoking the configurator:
verify the path exists and is a git repo (or clone/create it first). The
configurator writes the pointer and fragments for the names/paths it is given; it
does not prove that a missing checkout is valid.

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
<agent-worktrees catalog argv[0]> repos add <knowledge-name> "<knowledge-path>" --class singleton
```

(The harness itself is normally already registered from its own adoption. If not,
register it too.)

## 3. Write the machine-local binding

Run the configurator (idempotent -- safe to re-run). It writes the
`knowledge_repo:` pointer into `~/.<harness>/config.yaml`, assembles the
machine-local instructions fragment labeling the concrete paths, and (when both
repo paths are supplied) renders the personal-plugin overlay described in step
3b:

```
python skills/binding-knowledge/scripts/bind_knowledge.py \
  --harness <harness-name> \
  --knowledge <knowledge-name> \
  --knowledge-path "<knowledge-path>" \
  --harness-path "<harness-anchor-path>" \
  [--product <name>=<path> ...]
```

`--product` (repeatable) labels any coordinated product repos so the assembled
fragment names them for this machine. The fragment is **machine-local**
(`~/.<harness>/knowledge-binding.md`, **emitted at session start by the
harness-knowledge `sessionStart` hook**) -- it is **never** committed into the
harness, and it does **not** use the related-registry `add` operation (that would write
a repo name into the harness's committed `related.yaml` and break statelessness).

When both `--harness-path` and `--knowledge-path` are given, the bind **also
assembles the personal-plugin overlay** (see step 3b) -- so the operator's
personal skills/agents load in the harness in one step.

## 3b. Personal-plugin overlay

Copilot loads plugins (skills, agents) from the **launch repo's** settings, but
the operator's personal skills/agents live as **`.ai` local-marketplace plugins
in the private knowledge repo** -- not in the shareable harness tree. The bind
bridges that gap by rendering a machine-local, gitignored
`<harness>/.github/copilot/settings.local.json` that re-declares the knowledge
repo's local (`.ai`) marketplace(s) with an **absolute path** into the knowledge
checkout + the same `enabledPlugins`. Copilot merges `settings.local.json` over
the committed `settings.json` on launch (local tier wins), so the personal
plugins load while the harness tree stays name-free.

`bind_knowledge.py` runs this automatically through agent-worktrees' canonical
composer. To (re)assemble an explicit anchor binding directly, the legacy script
remains a compatibility delegate:

```
python skills/binding-knowledge/scripts/assemble_plugins.py \
  --harness-path "<harness-anchor-path>" \
  --knowledge-path "<knowledge-path>"
```

The canonical composer carries local (`directory`/`local`) marketplaces,
operator-specific remote marketplace declarations, and their enabled plugins.
Committed generic harness entries remain the base and are not duplicated.
The overlay is idempotent and merge-safe: unmanaged entries are preserved, and
exact managed values are tracked so stale entries can be retired safely.

> **Keep `settings.local.json` gitignored in the harness.** It is machine-local
> and names the concrete knowledge checkout; add
> `.github/copilot/settings.local.json` to the harness `.gitignore`.

> **Paired-worktree re-assembly.** The bind writes an overlay pointing at the
> knowledge **anchor**. In a paired harness/knowledge worktree, the launch
> preflight automatically runs:
>
> ```
> agent-worktrees knowledge compose-plugins # marketplace-isolation: allow launch-preflight
> ```
>
> The command resolves both paths by pair role and points local marketplaces at
> the paired **knowledge worktree** before Copilot discovers plugins. For an
> invalid tracked harness pair it retires only exact marker-owned pair values,
> preserving modified/operator settings; an ordinary unpaired repo is a no-op.
> Malformed or otherwise unsanitizable overlays fail closed before Copilot
> starts. The old `assemble_plugins.py --from-pair` spelling remains only as a
> compatibility delegate; it contains no separate composition logic.

## 4. Verify

```
<agent-worktrees catalog argv[0]> state-root --json
```

Expect `requires_external: true`, `bound: true`, and `state_root` pointing at the
knowledge checkout. As a final proof, a fresh ask like *"start an effort for X"*
should land the effort in the **knowledge** repo, with the harness tree clean.

## Idempotence & re-pointing

Re-running is safe: the configurator replaces the `knowledge_repo:` line in place
(preserving the rest of the config), rewrites the binding fragment, retires any
managed legacy auto-loaded fragment, and refreshes the managed local-plugin
overlay entries while preserving unmanaged local settings. To re-point at a
different knowledge repo, register the new one (step 2) and re-run step 3 with
the new `--knowledge`/`--knowledge-path`.
