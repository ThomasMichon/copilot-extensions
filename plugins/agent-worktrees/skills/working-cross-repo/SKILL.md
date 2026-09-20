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
`<agent-containers catalog argv[0]>` with their raw published paths. Quote each
path at its shell call site; never search `PATH`.

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

### Capability-aware effort placement

When the cross-repository task is coordinated by an effort, invoke
`planning-efforts` before deciding where a target-local effort belongs. Resolve
this skill's authoritative local target checkout/worktree as above, resolve the
efforts plugin root by walking two parents up from the loaded
`planning-efforts` skill base, and use its read-only probe:

```bash
bash <efforts-plugin-root>/scripts/emit-policy.sh --check-adoption <absolute-target-path>
```

```powershell
& <efforts-plugin-root>\scripts\emit-policy.ps1 -CheckAdoption <absolute-target-path>
```

Only exact JSON
`{"version":1,"capability":"efforts","adopted":true}` permits a target-owned
effort. `{}`, malformed output, an unavailable checkout, and a remote-only
target keep orchestration host-owned. The probe reads the target's bounded
adoption data and Git root only; it does not execute target code or infer
capability from a repository name. `planning-efforts` (and its
`references/efforts.md`) owns the resulting placement decision -- the three
placement models, the one-way-reference rule, and the no-drifting-copies
guarantee -- follow it rather than duplicating that decision here.

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

## The five rules

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

> **Mind cross-repo plan/effort state on a venue.** A task dispatched to a
> CodeSpace/container that tracks against a plan/effort/spec doc living in a
> *different* repo needs that context relayed inline or the repo materialized
> on the venue -- the on-venue agent can't see it otherwise. Detail:
> [`references/venue-and-claims.md`](references/venue-and-claims.md) §
> *Mind cross-repo plan/effort state on a venue*.

> **A cross-repo PR you open is an obligation on your worktree -- journal it.**
> A PR opened in *another* repo is **not** auto-journaled the way
> `<agent-worktrees catalog argv[0]> create-pr` is here, so `finalize` won't
> know it's still open. Record it as a claim
> (`claims add pr <pr-url> --owner-ref ...`) and settle it when it merges.
> Investigating someone *else's* PR in a target repo instead? Use
> `tracing-claimant-graphs`. Commands and the full model:
> [`references/venue-and-claims.md`](references/venue-and-claims.md) §
> *A cross-repo PR you open is an obligation on your worktree*.

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

### 5. Resolve the target's SOURCE, ACCOUNT, and POLICY -- don't assume your home repo's

A target repo can require a different git/gh identity, remote host, and
contribution policy than the one you're already using. Never reuse your home
repo's account or assume ambient `gh auth` applies:

- **Account -- GitHub targets only.** `<agent-worktrees catalog argv[0]> repos
  account-for <owner|owner/name>` prints the resolved `gh` login for a GitHub
  repo (explicit `account:` -> `account_map` -> the remote owner itself ->
  none/ambient); route every `gh`/API call for it through
  `<agent-worktrees catalog argv[0]> repos gh <owner|owner/name> -- <args>`,
  never a bare `gh <cmd>` or a machine-global `gh auth switch`. A non-GitHub
  remote (Gitea, Azure DevOps) resolves **no** account through this path --
  `related resolve` still surfaces the resolved account (`explicit` vs
  `derived`) when one applies, alongside the class/locus facts above.
- **Source/host** -- check the registry entry's actual `remote` (via
  `<agent-worktrees catalog argv[0]> repos list` / the repo's `repos.yaml`
  entry) before assuming a target is on GitHub and the account commands above
  apply -- `repos find`/`related resolve` name the local path and class, not
  the provider; don't infer either from habit.
- **Policy** -- both the PR flow (`get pr-profile`, below) *and* the target's
  own **issue tracker and coordination convention** are repo-specific. Before
  filing a bug or claiming work, read the target's `CONTRIBUTING.md`/`AGENTS.md`
  for where issues are tracked and how concurrent drivers claim work (some
  repos require a claiming issue before you start, per their own convention) --
  never file against your home repo's tracker, and never assume a convention
  from one target repo carries over to another.

Full account/source mechanics (the `account_map` decoupled-identity layer,
the accounts catalog, repo-scoped identity resolution) live in the
**`agent-worktrees-repos`** skill -- read it before your first cross-repo
identity operation on an unfamiliar target.

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
   - **Check the target repo's PR flow before you drive one -- every time,
     never from memory.** `<agent-worktrees catalog argv[0]> get pr-profile`
     reports `direct` (no PR), `pr-human-merge` (PR-gated, a **human**
     approves + merges -- `pr-merge` does not apply), `pr-agent-merge`
     (author signals consent with `pr-merge` and the gate merges), or
     `pr-self-merge` (PR-gated, the submitter merges directly with
     `pr-merge <#> --now` once required checks/reviews allow it). The same
     facts also surface automatically in the target worktree's bounded
     session-start context (a `PR:` line: profile + enabled/required/
     merge_actor) and as an extra `Note:` line on every `pr-*` verb's
     reminder text when the target repo sets `pr.notes` -- read both, they
     exist precisely so you don't have to guess or reuse your home repo's
     protocol. Do **not** assume the flow your home repo uses, and do
     **not** infer a target's flow from a different repo you worked in
     earlier in the same session. When a `pr-*` verb reports it does not
     apply to the target, follow its pointer (and the repo's
     `CONTRIBUTING`) rather than hand-merging.
   - **Drive the PR through to merge, regardless of whose repo it is** --
     the same default-conduct rule as your home repo (see
     `pr-workflow.md`'s *Default conduct*), applied to the *target's*
     profile: self-merge means you merge once eligible, human-merge means
     you wait for and don't skip the reviewer, agent-merge means you signal
     consent once approved. A PR left open because you weren't sure which
     protocol applied is the single most common way cross-repo work goes
     stuck -- resolve the uncertainty by re-running `get pr-profile` and
     checking `pr-status`, not by leaving it for later.
   - **Filing a bug or claiming work?** Resolve the target's own issue
     tracker and coordination convention first (its `CONTRIBUTING.md`/
     `AGENTS.md`) -- some repos require claiming a stretch of work with an
     issue before you start (see *Resolve the target's SOURCE, ACCOUNT, and
     POLICY* above). Never file against your home repo's tracker, and never
     assume one target's convention applies to another.

## Anti-patterns (don't)

- Editing a **reference** repo, or a **worktree** repo's anchor checkout.
- Crawling a large repo's tree blind instead of entering through its root
  `AGENTS.md` waypoint and following the links from there.
- Cloning a repo locally to dodge delegating to the machine/CodeSpace that owns
  it.
- Hardcoding a checkout path instead of `repos find`.
- Reusing your home repo's `gh` account/identity, or a machine-global `gh auth
  switch`, instead of resolving the target's own account with `repos
  account-for` / routing through `repos gh`.
- Applying *this* repo's conventions (branch prefix, merge style, issue
  tracker) to the target repo -- follow the target's.
- Opening a PR on a target repo and leaving it stuck open because the
  protocol was unclear or assumed rather than checked (`get pr-profile` +
  the session `PR:` line + any `pr.notes` exist to remove exactly this
  ambiguity -- use them before acting, not after a PR is already stalled).
- Filing an issue against a target's tracker without reading its own
  coordination convention first, risking a duplicate or a claim collision.
