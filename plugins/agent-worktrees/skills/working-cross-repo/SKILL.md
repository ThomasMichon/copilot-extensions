---
name: working-cross-repo
description: >
  How to work on ANOTHER repo from the current (control-plane) repo as a good
  citizen: resolve where and how to work via the related-repos index, honor the
  target repo's management class and adoption status, honor its locus (local /
  another machine / a CodeSpace), and prefer delegating to the agent that owns
  the repo over reaching across machines yourself. Use whenever you are asked to
  make a change in, build, test, or investigate a repo that is not the one you
  are currently in. Trigger phrases include:
  - 'work on another repo'
  - 'make a change in <repo>'
  - 'cross-repo'
  - 'work in <repo> from here'
  - 'how do I work on <repo>'
  - 'edit <repo> from this repo'
  - 'change <repo>'
  - 'go work on <repo>'
  - 'dispatch to <repo>'
---

# Working Cross-Repo (good-citizen guide)

You are in a **control-plane** repo and need to do work in a **different** repo.
Do it without stepping on other flows, without editing things you shouldn't, and
without manually reaching across machines when an owning agent can do it. The
`related` index (see the **`agent-worktrees-related`** skill) plus
`agent-worktrees related resolve` give you the plan.

## The one command to start with

```bash
agent-worktrees related resolve <name>     # or: related resolve   (uses the primary)
agent-worktrees related resolve <name> --json
```

`resolve` reports, for **this machine**: the target's **class** (editing model),
its checkout **path**, the **locus** (where work happens), **availability**, the
**delegate** channel, and a concrete **Plan**. Follow the plan. If `<name>` is
not linked yet, link it first via `agent-worktrees-related` (offer to, then
proceed).

## The four rules

### 1. Honor the management CLASS (from the global registry)

- **reference** -- *read-only*. Resolve the path with
  `agent-worktrees repos find <name>` and read it. **Never edit** a reference
  repo locally.
- **singleton** -- edit the **anchor checkout directly**; one flow at a time.
- **worktree** -- never edit the anchor. Create an isolated worktree
  (`<name> --new`), edit/commit there, then `<name> push-changes` / `finalize`.
  If the repo is worktree-class but **not adopted**, adopt it first
  (`agent-worktrees register <name>`).

Always read the repo's `CONTRIBUTING.md` / `AGENTS.md` and its narrative
(`agent-worktrees related doc <name>`) before changing it.

### 2. Honor the LOCUS (where work actually happens)

- **local** -- work here, per the class above.
- **machine:&lt;key&gt;** and that machine **is** this one -- work here.
- **machine:&lt;key&gt;** and it is a **different** machine -- **delegate** to it
  via agent-bridge: `agent-bridge send <key> "<task>"`. Don't clone it locally
  just to avoid delegating.
- **codespace** -- provision/connect via **agent-codespaces** and dispatch via
  agent-bridge:
  `gh cs create -R <cs-repo> -m <cs-machine> -l <cs-location>` (or reuse an
  existing one), then `agent-bridge send codespace:<name> "<task>"` /
  `agent-codespaces ssh <name>`.
- **not available on this machine** (per `locus.machines`) -- do **not**
  blind-clone. Follow the locus: delegate to a machine that has it.

### 3. Prefer DELEGATION over reaching across machines

If the repo has an owning agent (a same-machine agent-bridge agent, another
machine's agent, or a CodeSpace agent), hand the task to it rather than driving
the repo yourself from here. `resolve` names the delegate channel
(`delegate.via`) and the concrete `agent-bridge` / `agent-codespaces` command.
This keeps each repo's work in the context that owns it.

### 4. Never hardcode a checkout PATH

A repo's local path **varies by machine**. Always resolve it with
`agent-worktrees repos find <name>` (it falls back to the per-machine
`repos srcroot`). Never write a fixed drive path into a doc, skill, or command.

## End-to-end shape

1. `related resolve <name>` (link it first if needed).
2. Read its narrative + `CONTRIBUTING.md`/`AGENTS.md`.
3. Act on the plan:
   - local -> edit per class (worktree `--new` / singleton anchor / reference
     read-only);
   - elsewhere -> delegate via agent-bridge / agent-codespaces.
4. Land changes through the **target repo's** own contribution flow (its branch
   naming, PR/merge policy, version-bump rules) -- not this repo's.

## Anti-patterns (don't)

- Editing a **reference** repo, or a **worktree** repo's anchor checkout.
- Cloning a repo locally to dodge delegating to the machine/CodeSpace that owns
  it.
- Hardcoding a checkout path instead of `repos find`.
- Applying *this* repo's conventions (branch prefix, merge style) to the target
  repo -- follow the target's.
