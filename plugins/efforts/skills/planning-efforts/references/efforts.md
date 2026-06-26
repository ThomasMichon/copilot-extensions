# Efforts — Reference Guide

The canonical reference for the **efforts planning system**. This guide ships
as an asset of the `planning-efforts` skill; the skill is the how-to workflow,
this guide is the *why* and the full schema. An adopting repo specializes the
system through a short **addendum** (see *Adoption & the addendum* below) — it
does not fork this guide.

## What an effort is (and isn't)

An **effort** is a comprehensive planning folder representing a *stretch of
work*: its premise, evolving plan, validation plan, running journal, and the
coordination surface for the participants doing the work. One effort, one
folder, one README that humans and agents both read and write.

The name is deliberately **not** `feature`, `bug`, or `task` — those nouns are
owned by issue trackers (GitHub, Gitea, Azure DevOps). An effort is the
planning workspace *around* tracked work; it spawns and references issues and
outlives any single one.

| Construct | Question it answers | Tense | Home |
|-----------|---------------------|-------|------|
| **Effort** | "What are we doing, and how's it going?" | *should be* | `efforts/` |
| **Doc** | "How does this actually work?" | *is* (truth) | docs |
| **Issue** | "What discrete thing needs doing?" | *to do* | the tracker |

> A doc describing the efforts *system* (like this guide) is truthful "what is"
> documentation. The efforts themselves are "what should be." Don't conflate
> the two: when an effort establishes durable truth about how something works,
> promote that into a doc — the archived effort is a record, not living docs.

### Efforts and issues go hand in hand

An effort typically opens with an **umbrella issue** and breaks down into
**sub-issues**; the README cross-links them. The binding rule:

> **Only issues filed for *this* repo may directly reference effort files in
> this repo.** A cross-repo issue (or mirror) references the *tracked work*,
> not a path it cannot resolve.

## Layout

```
efforts/
├── README.md              # the repo's effort index + the local addendum
├── TEMPLATE.md            # the effort README schema (copy when starting one)
├── active/                # in-flight efforts
│   └── <slug>/README.md
└── <YYYY>/...             # archived efforts, dated by completion
```

- **Slug:** kebab-case, descriptive.
- **Grouping (a binding — set by the addendum):**
  - *flat* — `efforts/active/<slug>/` (default; for a repo that is itself the
    primary unit of work).
  - *by-repo* — `efforts/active/<repo>/<slug>/` (for a coordination repo that
    spans many target repos).
- **Archive (a binding — set by the addendum):** dated by when the effort
  closed, e.g. `efforts/<YYYY>/MM/DD <slug>/` (flat) or
  `efforts/<YYYY>/<repo>/MM/DD <slug>/` (by-repo). The archive is a
  chronological record, not a status bucket.

## The effort README — schema

`TEMPLATE.md` is the canonical template. The README is a **shared contract**:
the host agent, any dispatched agents, and the operator all read and write the
same file. It — not the conversation transcript — is the source of truth.

| Section | Purpose |
|---------|---------|
| **Header** | slug, repo, branch(es), created date, status, umbrella + sub-issues |
| **Guiding Intent** | the north star — what success looks like, why it matters |
| **Participants** | who/what does the work (the executor seam — see below) |
| **Coordination** | optional; multi-agent branch topology + host/delegate roles + PR ownership (the *branches* binding) |
| **Context** | background, source issue/plan/idea, prior decisions, references |
| **Request** | the operator's ask, captured verbatim |
| **Plan** | phased implementation plan with checklists |
| **Validation Plan** | how we'll know it works (efforts are validation-driven) |
| **Proposal** | optional detailed findings once the plan firms up |
| **Journal** | dated, append-only log of what actually happened |

An addendum may rename sections (e.g. **Participants** → **Machines**), add one
(some repos add nothing beyond the core), or drop **Validation Plan** for a
repo that doesn't want it. The header, Guiding Intent, Request, Plan, and
Journal are the irreducible core.

### Validation-driven

Every effort should carry **both** an implementation plan and a
validation/test plan. An effort may legitimately *start* as nothing but a
reproduction — a failing validation captured first, with the fix to follow. The
endgame is test-driven work across whatever participants the effort
coordinates: validate against the real target, feed failures back to whoever
(or whatever) is implementing, and loop until the validation passes.

## The participants seam

The `## Participants` section is the **pluggable seam** that makes efforts
reusable. It catalogs *who or what does the dispatched work*, and each adopting
repo binds it to its own executor provider:

| Binding | Participant | Reached via |
|---------|-------------|-------------|
| machine fleet | a workstation / server | SSH alias, agent-bridge |
| CodeSpaces | a GitHub CodeSpace | `agent-codespaces` |
| containers | a local dev container | `agent-containers` |
| branches | a shared feature branch several agents build on | git -- the `agent-worktrees` **`git-collaboration`** skill (turn-key `git sync` / `feature-branch` / `merge-to-feature` helpers); delegates dispatched via **agent-bridge** |

When the binding is **branches**, the effort README's `## Coordination` section
holds the topology: a **shared feature branch** (delegates ff-push slices; the
**host** owns PRs) for interdependent work, or **independent worktrees with
per-slice PRs** when each PR leaves the default branch green on its own. The
mechanics are turn-key helpers in the `git-collaboration` skill -- the effort
records only the plan and who owns what.

The effort README is where multi-participant coordination is planned and
journaled, so a fresh agent can pick up the effort from the file alone. Keep
everything else in the schema **executor-neutral** — participant specifics
belong in this section and in the repo's addendum, nowhere else. That
separation is what lets one plugin serve many repos and many executor plugins.

## Lifecycle

1. **Start** — point at an existing issue, plan, roadmap, or idea. Derive a
   kebab-case slug, copy `TEMPLATE.md` to `efforts/active/<slug>/README.md`,
   and capture the **Request** verbatim plus the **Guiding Intent**. Open an
   umbrella issue if the work warrants tracking, and cross-link it.
2. **Plan** — fill in Context, Plan, and Validation Plan. File sub-issues for
   discrete tracked work and link them.
3. **Review gate** — before executing, route the *plan* through review. After the
   operator's own review rounds, **if the control repo offers automated PR
   review**, submit the effort as a PR (with auto-merge), let it be approved +
   merged, then **pull the worktree forward onto the merged plan** (the
   `git-collaboration` skill's `git sync` helper) and execute on top. The operator
   may waive their own review; the agent's gate is non-optional when automated
   review exists. It guarantees a reviewed plan, makes the effort visible to other
   agents (dedupe / co-work), and leaves a crash-recovery point. Where no
   automated review exists, the gate collapses to "commit the plan, then execute."
4. **Execute** — do the work on a working branch; keep the **Journal** current.
   Record participant dispatches, decisions, and blockers. The README stays
   ahead of the conversation, but **batch effort edits** (each upstream edit costs
   a PR) to direction changes, phase boundaries, or commits that need pushing
   anyway. By **code-complete**, the README reflects the done state and names the
   *next* carry-forward effort; record **merged** PRs only (never the in-flight
   one the current commit is opening). For **cross-repo / tracking-only** efforts,
   propose-before-you-do: PR the not-yet-done plan first, make the external
   changes after it clears, then report completion as a separate delta.
5. **Archive** — when the effort is done (or abandoned), move
   `efforts/active/<slug>/` to the dated archive path, set the header
   **Status**, and write a closing Journal entry. Update the active index in
   `efforts/README.md`. Promote any durable "what is" truth into docs.

## Adoption & the addendum

The `planning-efforts` skill governs the canonical pattern above. An adopting
repo writes a short **addendum** that specializes only the bindings:

- **Grouping** — flat or by-repo.
- **Archive layout** — the dated path pattern.
- **Participants binding** — the concrete label and how participants are
  reached (machines via SSH, CodeSpaces, containers, branches).
- **Section additions/renames** — any deltas to the README schema.
- **Repo-local rules** — e.g. which tracker holds issues, where sources of new
  efforts live (a `plans/`, `ROADMAP`, or backlog).

The addendum lives **in the adopting repo** — typically a `## Local
conventions` section of its `efforts/README.md`, or a dedicated binding doc
(e.g. `docs/efforts.md`) that `efforts/README.md` links to. The
`efforts-setup` skill scaffolds the `efforts/` tree and the addendum.

> Keep the addendum small. If you find yourself re-explaining the core pattern,
> it belongs in this guide, not the addendum. The addendum is deltas only.

## For executor plugins

An executor plugin (e.g. `agent-codespaces`, `agent-containers`) integrates by
**owning the participants seam** for repos that adopt it:

- Provide the concrete participant binding the addendum points at (how a
  CodeSpace / container is named, created, reached, and torn down).
- Register/deregister participants on an effort as they are commissioned and
  reclaimed, and journal those transitions in the effort README.
- Leave the planning document and lifecycle to the efforts plugin — do not
  embed planning structure in the executor.
