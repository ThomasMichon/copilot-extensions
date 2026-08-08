# harness-knowledge

Binds a **stateless** Copilot CLI control harness to its private **knowledge**
repo, **harness-first**, so the shareable harness tree stays generic and
name-free while each machine points at its own knowledge repo.

A stateless harness (see the `stateless-harness` vision) holds the *intelligence*
— instructions, config, skills, sub-agents — but **no personal state**. Personal
state (efforts, logs, visions, notes, artifacts, personal issues) lives in a
separate knowledge repo, bound **per machine** via machine-local config. This
plugin performs that binding after you've cloned the harness.

## What it does

The **`binding-knowledge`** skill drives the setup:

1. Confirms the launch repo requires an external state root
   (`agent-worktrees state-root`).
2. Asks for the knowledge repo — use an existing local checkout, clone a remote,
   or **create** a new (private) one.
3. Registers both repos with agent-worktrees so the state-root resolver can find
   them by name.
4. Runs the idempotent configurator
   (`skills/binding-knowledge/scripts/bind_knowledge.py`), which writes **only
   machine-local** state:
   - `~/.<harness>/config.yaml` → the top-level `knowledge_repo:` pointer the
     state-root resolver reads.
   - `~/.<harness>/knowledge-binding.md` → a managed data fragment **emitted at
     session start by the harness-knowledge `sessionStart` hook** (dotfiles#1057)
     that labels the concrete harness/knowledge/product paths **for this
     machine**. (Previously written into `.github/instructions/` and auto-loaded
     via `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`; now hook-emitted, so it loads under
     any launch path with no file in the auto-load dir.)
5. Verifies the binding resolves.

## Why machine-local only

The committed harness tree must **never** name a knowledge or product repo (a
fork or a different operator changes only their machine-local config). So the
plugin writes the pointer + the instructions fragment into `~/.<harness>/`, and
**never** uses `agent-worktrees related add` (which would write a repo name into
the harness's committed `related.yaml` and break statelessness).

This is E1d of the `citadel-harness-split` effort (dotfiles#879).

## Bigger picture: the knowledge repo as a config-overlay (state-root grafting)

Binding is the **seam**, not the whole story. The knowledge repo is intended to
**extend the harness's base `.agent-*` config** as much as practical — it may
carry its own **`related.yaml` + narratives** (more coordinated repos than the
name-free harness can list), a **`machines.yaml`**, **`codespaces.yaml`**, and
other multi-machine topology that is inherently operator-specific. Realizing that
requires every plugin service/tool that reads harness config to become
**state-root-aware** and **graft** the knowledge (state-root) repo's config on top
of the harness base. The **state-root repo is a config source in its own right**,
kept distinct from the harness↔knowledge *binding relationship* modeled here. That
config-graft layer is tracked as its own phase of the effort; this plugin
establishes the binding it builds on.

