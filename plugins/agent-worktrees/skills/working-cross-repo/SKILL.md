---
name: working-cross-repo
description: >
  How to work on ANOTHER repo from the current (control-plane) repo as a good
  citizen: orient at the target repo's root `AGENTS.md` waypoint before crawling
  it, resolve where and how to work via the related-repos index, honor the
  target repo's management class and adoption status, honor its locus (local /
  another machine / a CodeSpace), and prefer delegating to the agent that owns
  the repo over reaching across machines yourself. Use whenever you are asked to
  make a change in, build, test, investigate, or find your way around a repo
  that is not the one you are currently in. Trigger phrases include:
  - 'work on another repo'
  - 'make a change in <repo>'
  - 'cross-repo'
  - 'work in <repo> from here'
  - 'how do I work on <repo>'
  - 'edit <repo> from this repo'
  - 'change <repo>'
  - 'go work on <repo>'
  - 'dispatch to <repo>'
  - 'where do I start in <repo>'
  - 'this repo is huge'
  - 'how do I navigate <repo>'
---

# Working Cross-Repo (good-citizen guide)

Use the exact `argv[0]` from each plugin's session command catalog for the
interactive operations below. Replace `<agent-worktrees catalog argv[0]>`,
`<agent-bridge catalog argv[0]>`, `<agent-codespaces catalog argv[0]>`, and
`<agent-containers catalog argv[0]>` with their published paths as quoted,
single argv tokens; never search `PATH`.

You are in a **control-plane** repo and need to do work in a **different** repo.
Do it without stepping on other flows, without editing things you shouldn't, and
without manually reaching across machines when an owning agent can do it. The
`related` index (see the **`agent-worktrees-related`** skill) plus
`<agent-worktrees catalog argv[0]> related resolve` give you the plan.

## The one command to start with

```bash
<agent-worktrees catalog argv[0]> related resolve <name>     # or: related resolve   (uses the primary)
<agent-worktrees catalog argv[0]> related resolve <name> --json
```

`resolve` reports, for **this machine**: the target's **class** (editing model),
its checkout **path**, the **locus** (where work happens), **availability**, the
**delegate** channel, and a concrete **Plan**. Follow the plan. If `<name>` is
not linked yet, link it first via `agent-worktrees-related` (offer to, then
proceed).

## Orient before you crawl -- the root `AGENTS.md` is the map

Before reading source, **read the target repo's root `AGENTS.md`** (and, if you
are working in a subtree, the nearest `AGENTS.md` up the path). Treat it as the
repo's **waypoint index**: a lean top-level map that orients you and links out to
the detailed guidance -- `CONTRIBUTING.md`, `docs/` (architecture, patterns),
`visions/`, and the connective-tissue skills. A well-built `AGENTS.md` names
*where things are*, not *everything there is*.

Navigate from those waypoints to the specific guidance you need, and **prefer
this over crawling the tree**. A large repo is only overwhelming when you read it
at random; entered through its `AGENTS.md`, even a huge repo is a short hop from
"what am I looking at" to "the doc that answers my question." This is the
antidote to balking at a repo's size.

- **No root `AGENTS.md`?** Fall back to `README.md` / `CONTRIBUTING.md` as the
  entry, then the repo's `docs/` index -- and consider adding an `AGENTS.md`
  waypoint if you own or contribute to the repo (see the **`authoring-skills`**
  skill for building one as a map rather than a manual).
- **The target's `AGENTS.md` is *its* POV; the narrative is *ours*.** The related
  narrative (`<agent-worktrees catalog argv[0]> related doc <name>`) is this control-plane's view
  of the target and points at these same waypoints -- read both: the narrative
  for how *we* relate to the repo, the target's `AGENTS.md` for how the repo
  wants to be worked.

## The four rules

### 1. Honor the management CLASS (from the global registry)

- **reference** -- *read-only*. Resolve the path with
  `<agent-worktrees catalog argv[0]> repos find <name>` and read it. **Never edit** a reference
  repo locally.
- **singleton** -- edit the **anchor checkout directly**; one flow at a time.
- **worktree** -- never edit the anchor. Create an isolated worktree, edit and
  commit there, then `push-changes` / `finalize`. To make the worktree from a
  tool call, use `<agent-worktrees catalog argv[0]> create` (prints the worktree path; cd in and
  edit in your current session -- no new session, no mux). `<name> --new`
  launches a fresh *interactive* muxed session and is refused without a TTY, so
  it is for humans at a terminal, not agents. If the repo is worktree-class but
  **not adopted**, adopt it first
  (`<agent-worktrees catalog argv[0]> register <name>`).

Always read the repo's `CONTRIBUTING.md` / `AGENTS.md` and its narrative
(`<agent-worktrees catalog argv[0]> related doc <name>`) before changing it -- orient at the root
`AGENTS.md` waypoint first (see *Orient before you crawl* above).

### 2. Honor the LOCUS (where work actually happens)

- **local** -- work here, per the class above.
- **machine:&lt;key&gt;** and that machine **is** this one -- work here.
- **machine:&lt;key&gt;** and it is a **different** machine -- **delegate** to it
  via agent-bridge:
  `<agent-bridge catalog argv[0]> send <key> "<task>"`. Don't clone it locally
  just to avoid delegating.
- **codespace** -- provision/connect via **agent-codespaces** and dispatch via
  agent-bridge:
  `<agent-codespaces catalog argv[0]> create <cs-repo>` (headless, no TTY -- routes around
  `gh cs create`'s interactive billing/devcontainer prompts via the REST
  fallback; reuses an existing idle box per the pool guard), then
  `<agent-bridge catalog argv[0]> send codespace:<name> "<task>"` /
  `<agent-codespaces catalog argv[0]> ssh <name>`.
- **not available on this machine** (per `locus.machines`) -- do **not**
  blind-clone. Follow the locus: delegate to a machine that has it.

> **The locus governs EXPLORING too, not just changing.** Reading and
> understanding a repo whose locus is a **CodeSpace / container / another
> machine** belongs *in that venue*, against the full checkout -- not reassembled
> from piecemeal remote/ADO-API file reads on this box. Bring the venue up **once**
> (`<agent-codespaces catalog argv[0]> ssh <name>` /
> `<agent-containers catalog argv[0]> up <name>`; on a fleet host
> like dev6 reuse an already-provisioned/exited container), then grep/read/build
> there, or delegate a read-only task to it. `related resolve <name>` prints an
> **Explore** block with the exact command for the repo's locus; follow it before
> you start reading source one file at a time. (Piecemeal API reads are slow,
> partial, and error-prone -- the venue gives you real search, cross-file tracing,
> and a build to check assumptions against.)

> **Mind cross-repo plan/effort state on a venue.** When you delegate to a
> CodeSpace/container agent but the task tracks against a **plan, effort, or spec
> doc that lives in a *different* repo** than the one on the venue, the on-venue
> agent **cannot see it** unless that repo is *also* materialized there
> (`/workspaces/<repo>` by convention). Don't point the agent at a path that
> isn't present: either ensure the doc's repo is on the venue and name its
> `/workspaces/<repo>` path, or **relay the needed context inline in the dispatch
> prompt and have the agent report results back** for you (the host) to record.
> Your control-plane's own dispatch skill owns the concrete host↔venue interop.

> **A cross-repo PR you open is an obligation on your worktree — journal it.**
> When you open a PR in *another* repo (e.g. an **example-web ADO PR** created with
> the AZ CLI / ADO REST / `gh`, on a CodeSpace or locally) it is **not**
> auto-journaled — only `<agent-worktrees catalog argv[0]> create-pr` in *this* repo is. So your
> worktree's `finalize` won't know that cross-repo work is still open. Record it
> as a claim so the gate keeps you accountable, then settle it when the PR merges:
> ```
> aw='<agent-worktrees catalog argv[0]>'
> "$aw" claims add pr <pr-url> --owner-ref "$("$aw" get owner-ref)"
> # when it merges/closes:
> "$aw" claims settle <pr-url>     # (sweep spares pr-kind — manual)
> ```
> See the `worktree` skill's finalize-gate section for the full model
> (example-operator/dotfiles#1351 tracks auto-journaling these).

### 3. Prefer DELEGATION over reaching across machines

If the repo has an owning agent (a same-machine agent-bridge agent, another
machine's agent, or a CodeSpace agent), hand the task to it rather than driving
the repo yourself from here. `resolve` names the delegate channel
(`delegate.via`) and the concrete `agent-bridge` / `agent-codespaces` command.
This keeps each repo's work in the context that owns it.

### 4. Never hardcode a checkout PATH

A repo's local path **varies by machine**. Always resolve it with
`<agent-worktrees catalog argv[0]> repos find <name>` (it falls back to the per-machine
`repos srcroot`). Never write a fixed drive path into a doc, skill, or command.

## End-to-end shape

1. `related resolve <name>` (link it first if needed).
2. **Orient at the target's root `AGENTS.md`** (the map): follow its links to the
   docs you actually need, then read its narrative + `CONTRIBUTING.md` for the
   contribution flow. Don't crawl the tree to figure out the repo.
3. Act on the plan:
   - local -> edit per class (worktree
     `<agent-worktrees catalog argv[0]> create` / singleton
     anchor / reference read-only);
   - elsewhere -> delegate via agent-bridge / agent-codespaces.
4. Land changes through the **target repo's** own contribution flow (its branch
   naming, PR/merge policy, version-bump rules) -- not this repo's.
   - **Check the target repo's PR flow before you drive one:**
     `<agent-worktrees catalog argv[0]> get pr-profile` reports `direct` (no PR),
     `pr-human-merge` (PR-gated, a **human** approves + merges -- `pr-merge`
     does not apply), or `pr-agent-merge` (author signals consent with
     `pr-merge` and the gate merges). Do **not** assume the flow your home repo
     uses. When a `pr-*` verb reports it does not apply to the target, follow
     its pointer (and the repo's `CONTRIBUTING`) rather than hand-merging.

## Anti-patterns (don't)

- Editing a **reference** repo, or a **worktree** repo's anchor checkout.
- Crawling a large repo's tree blind instead of entering through its root
  `AGENTS.md` waypoint and following the links from there.
- Cloning a repo locally to dodge delegating to the machine/CodeSpace that owns
  it.
- Hardcoding a checkout path instead of `repos find`.
- Applying *this* repo's conventions (branch prefix, merge style) to the target
  repo -- follow the target's.
