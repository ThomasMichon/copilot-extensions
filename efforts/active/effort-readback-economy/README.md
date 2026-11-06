# Effort Readback Economy

- **Slug:** `effort-readback-economy`
- **Repo:** copilot-extensions (`plugins/efforts`)
- **Branch(es):** per-slice worktree branches into `dev`
- **Created:** 2026-11-06
- **Status:** Active
- **Umbrella issue:** #11143

## Guiding Intent

Effort prose stays authoritative, but agents should reach the current state of an
effort cheaply. After a merge, a resumption or a new context window, an agent
should read only the effort content it lacks: current open items, sections that
changed and new journal entries. It should not re-read whole multi-hundred-KB
documents. The opt-in agenda companion and every completion and approval gate
stay as they are.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Design, implementation, PRs | Worktree branch into `dev` |

## Context

Seven days of one heavy user's local session events (20 sessions) were measured
with a read-only analysis of `view` and shell read calls against `efforts/**/*.md`:

| Category | Calls | Result tokens | Share |
|---|---:|---:|---:|
| First read after a new context window | 2,330 | 2.53M | 34.8% |
| Same window, paging a new range of a large file | 1,358 | 1.78M | 24.4% |
| Same window, overlapping re-read of an unchanged file | 1,809 | 1.57M | 21.6% |
| First read in the session | 1,173 | 0.83M | 11.4% |
| Same window, after a local edit to that file | 482 | 0.32M | 4.5% |
| Same window, byte-identical duplicate | 193 | 0.24M | 3.3% |
| **Total** | **7,345** | **7.27M** | 10.3% of all tool-result tokens |

README files were about 82% of the re-read tokens among the 20 most re-read
files. Several active READMEs are 100–370 KB. The bulk is an inline journal, or
Plan checkboxes that carry their full PR-by-PR history.

Causes:

- The completion gate says "re-read the effort's own README" after a merge, and
  agents read that as a full re-read.
- Plans themselves prescribe "read the entire parent" steps.
- The layout guidance assumes "the README is loaded whole every time" and only
  splits the journal at about 800 lines.

The measurement categories and caveats are in [measurement.md](measurement.md).

## Request

> Efforts are prose (with agendas now the programmatically-accessible companion),
> but we should do some optimization to avoid repeat, expensive readbacks of
> efforts. Make a small effort to drive the design updates to efforts and do
> implementations. [...] Anything which cuts down on wasteful token spend is good.

## Plan

### Phase 1 — Readback digest helper
- [x] Add `planning-efforts/scripts/effort_digest.py` (stdlib only, read-only):
  - outline with per-section byte sizes;
  - open `- [ ]` Plan and Validation Plan items only;
  - latest journal entry headings, whether inline or under `journal/`;
  - `--since <git-rev>`: only the sections and files that changed since that
    revision.
- [x] Focused tests in `plugins/efforts/tests/`.

### Phase 2 — Guidance
- [x] `planning-efforts` skill, reference and template:
  - targeted readback rule: digest first, then read only the needed sections;
  - README size budget;
  - one-line Plan items with state and a link to detail;
  - journal in dated files much earlier than 800 lines;
  - plans must not prescribe whole-tree re-reads.
- [x] Completion-gate instruction: after a merge, re-read the effort's current
  Plan and Validation Plan state and the sections the change touched, not the
  whole document tree. Keep "never rely on a PR summary".

### Phase 3 — Adoption follow-ups
- [x] Deferred to `#11148`: Track restructuring of the largest existing active
  READMEs as separate follow-up issues. Restructuring them is not part of this
  effort.

## Validation Plan

- [x] Digest tests cover: open-item extraction, inline and split journals,
  `--since` changed-section output, and refusal outside a git checkout for
  `--since`.
- [x] Running the digest on a large existing effort README produces a summary
  under 10% of the README's size.
- [ ] Plugin suite passes through the bounded runner; docs-consistency and
  projection checks pass.

## Journal

### 2026-11-06 — Kickoff
- Measured the weekly readback cost (Context). Filed #11143. Effort created
  with a three-phase plan.

### 2026-11-06 — Digest and guidance implemented
- Added `effort_digest.py` and six tests. On the eight largest active READMEs
  (226–387 KB) the text digest is 1–9% of the README.
- Updated the planning skill, reference, template and completion-gate
  instruction for targeted readback. Checked-in projection copies resync at
  release promotion.
- Fixed two stale efforts tests (skill line cap, root projection now a locator).
- Filed #11148 for restructuring the oversized READMEs (Phase 3).
- Efforts suite: 488 passed, 35 skipped. Docs and version consistency pass.
  The last Validation item closes when the PR merges with CI green.
