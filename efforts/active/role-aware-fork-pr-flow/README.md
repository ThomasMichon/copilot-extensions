# Role-Aware Fork PR Flow

- **Slug:** `role-aware-fork-pr-flow`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees and pull requests
- **Created:** 2026-09-10
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees` — `contribution-aware-lifecycle`
- **Umbrella issue:** _none yet — file on submission of Phase 2a's PR_
- **Sub-issues:** _none yet_

## Guiding Intent

Today, `agent-worktrees`' PR orchestration (`create-pr`, `push-changes`,
`pr-merge`, `finalize`) assumes one flow per repo: a single `origin` remote
with direct push rights, and one static `merge_actor`/`reviewer`/`fork_actor`
declaration in `pr:` config that applies to every caller identically. That
breaks down the moment a repo has **tiered** contributors — some with direct
push (`Write`+) access who use the existing flow, others who can only
contribute through a fork and a Maintainer's approval. This effort makes the
PR flow **role-aware**: resolve the caller's actual GitHub permission on the
target repo, then select the matching flow (today's direct-push flow, or a
fork-and-PR flow) from repo-declared per-role config — instead of forcing one
repo config to describe every contributor identically, or requiring every
non-privileged contributor to bypass `agent-worktrees` entirely and drive
`git`/`gh` by hand.

**Scope: GitHub only**, per the request that opened this effort. Other
providers (Gitea, Azure DevOps) get inert stubs so the shape is provider-
uniform, but no fork/role behavior — those providers already model
consent/completion differently and aren't in scope here.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `copilot-extensions-host` | Design, implementation, tests, self-hosted validation | independent `agent-worktrees`-managed worktrees of this repo |

## Coordination

- **Topology:** independent per-phase PRs, sequenced by the plan below.
- **Host (owns PRs):** `copilot-extensions-host`.
- **Delegates:** none.
- **Handoff:** n/a (single host); each phase's PR records what merged and what
  the next phase still needs.

## Context

- Originating conversation: standing up tiered CoreIdentity
  Contributor/Maintainer access for `gim-home/odsp-web-harness` (a GitHub EMU
  repo) surfaced that its `CONTRIBUTING.md` had to document the fork-and-PR
  flow **by hand**, in plain `git`/`gh`, because `agent-worktrees` had nothing
  to offer a `Write`-but-not-`Maintain` contributor beyond "don't use this
  tool." See that repo's `CONTRIBUTING.md` ("Tooling note") and its
  `role-aware-fork-pr-flow` tracking pointer (a local/tracking-only effort
  there, since implementation is genuinely about this repo — see this
  effort's placement rationale below).
- **Existing seams this design reuses, not replaces:**
  - `PRConfig` (`agent_worktrees/config.py`) already carries a rich, static
    "PR-flow legibility matrix" — `reviewer`, `review_blocking`, `self_approve`,
    `merge_actor`, `automerge_label`, `hold_labels`, etc. — consumed by
    `pr_contract.classify_pr_flow` / `pr_reminder`. This effort does not touch
    that classifier; it adds a **resolution step in front of it** that picks
    *which* `PRConfig` to feed it, based on the caller's resolved role.
  - `head_pattern`'s `{username}` token (already shipped, unrelated prior fix)
    solves branch-naming collisions for *direct-push* contributors sharing one
    repo. It does **not** address contributors who can't push at all — that's
    this effort's actual gap.
  - `GitHubProvider.get_repo_policy` already reads `gh api repos/<repo>` for
    adopt-time settings research; `get_viewer_permission` (Phase 2a, below)
    is a sibling read against the same endpoint.
  - `turnkey-reviewer-loops` (done, `efforts/2026/09/03 turnkey-reviewer-loops/`)
    already generalized the agent-dispatch reviewer recipe into a turnkey
    repository capability — the "automated self-reviewer with real
    approval/verdict power" piece of the original request is **already
    available**; a repo adopts it through that effort's own declaration
    surface, not through anything new here.
- **Cross-repo placement:** this is the **canonical, target-owned** effort
  (copilot-extensions has adopted `efforts/` and the work is genuinely about
  this repo's own PR orchestration). `gim-home/odsp-web-harness` keeps only a
  tracking pointer to this effort plus its own Phase-3 follow-up (updating its
  `CONTRIBUTING.md` once this lands).

## Request

> Let's pivot to driving the fork-remote-and-pr modeling, flow, and guidance
> now. This only really applies to GitHub right now
>
> (from the originating conversation, unaltered:) My plan is that for most
> contributors, we're going to require sign-off by a maintainer, while
> maintainers can self-merge, though they should wait for Copilot review. ...
> We might also need some "role-based config" pattern for `agent-worktrees`,
> so the agent can determine the best PR flow based on the user's role with
> respect to the available options. ... I don't think our PR tools in
> copilot-extensions are yet equipped for [fork-based PRs] (requires a
> separate remote for `fork` compared to `origin`, concept of role-based
> settings, etc.), so we'll need to plan an effort for enabling these
> patterns.

## Design (Phase 1 — settled)

**Role resolution.** GitHub's `gh api repos/<owner>/<repo>` response carries
the *authenticated caller's* permission on that repo: a `role_name` field
(`read` | `triage` | `write` | `maintain` | `admin` — the same vocabulary the
GitHub Inside Microsoft ACL policy's `role:` field uses) and, as a fallback for
older responses, legacy `permissions` booleans (`admin`/`maintain`/`push`/
`triage`/`pull`). No new auth surface needed — this reuses `gh`'s ambient
login exactly like every other provider call.

**Config shape.** Additive to `PRConfig`, so an unconfigured repo is
byte-for-byte identical in behavior to today:

```yaml
pr:
  enabled: true
  merge_actor: submitter-direct   # repo-wide default (today's single-flow case)
  fork:                           # repo-wide fork-publish default
    enabled: false
    remote: fork
    owner: ""                     # "" = caller's own account
  roles:                          # per-role overrides, keyed by GITHUB_ROLE_LEVELS
    maintain:
      merge_actor: submitter-direct
    write:
      merge_actor: ""             # never self-merge; derive to reviewer/consent-gate
      fork:
        enabled: true
```

`PRRoleOverride` fields are all `Optional` — a role only states what's
*different*; anything left `None` inherits the base `PRConfig` value
(including `fork`, when the override doesn't set its own). `resolve_role_pr_config
(prcfg, role)` is a pure function: unknown/`None`/unconfigured role ⇒ `prcfg`
unchanged.

**Fork remote model.** `ForkConfig(enabled, remote, owner)` names the local
git remote (`fork` by default) and, when set, an explicit fork-owner override
(otherwise the caller's own `gh`-authenticated account). When active,
`create-pr`'s publish step targets that remote instead of `repo.remote`, and
the provider-open call passes an explicit `<fork-owner>:<branch>` head instead
of a same-repo branch name — `gh pr create --repo <upstream-slug> --head
<owner>:<branch>` already supports exactly this shape natively.

## Plan

### Phase 2a — Role resolution + config shape (landed this session)
- [x] `config.GITHUB_ROLE_LEVELS`, `config.ForkConfig`, `config.PRRoleOverride`.
- [x] `PRConfig.roles: dict[str, PRRoleOverride]` and `PRConfig.fork: ForkConfig`
      fields + `_parse_pr` support (unknown role keys dropped, not raised).
- [x] `config.resolve_role_pr_config(prcfg, role) -> PRConfig` — pure, total,
      backward-compatible.
- [x] `PRProvider.get_viewer_permission` added to the `Protocol`
      (`providers/base.py`); implemented on `GitHubProvider` (reads
      `role_name`, falls back to legacy `permissions` booleans, `None` on any
      failure); stubbed to `None` on `GiteaProvider` / `AzureDevOpsProvider`.
- [x] Unit tests: `test_config.py` (parsing + resolution, 8 new cases),
      `test_providers.py` (`get_viewer_permission`, 6 new cases). Full
      `test_config.py` + `test_providers.py` (217 tests) and
      `test_pr_ops.py` + `test_pr_merge.py` + `test_pr_merge_now.py` (142
      tests) pass unchanged.
- [ ] Land via PR (this phase's own review gate).

### Phase 2b — Wire resolution + fork publish into `create_pr`/`pr_merge`
- [ ] At the top of `create_pr` (and `pr_merge`/`pr_status` where the flow
      matters), call `get_viewer_permission` for the resolved repo slug and
      `resolve_role_pr_config` before reading `prcfg` fields, so the rest of
      the function is unaware anything changed (same `prcfg`-shaped object).
      Cache the resolved role for the call's duration — never re-resolve
      mid-flow.
- [ ] When the resolved `PRConfig.fork.enabled` is true:
  - Ensure the caller's fork exists (`gh repo fork <upstream> --clone=false
    --remote=false`, idempotent) before the push step.
  - Ensure a local `fork` remote (`ForkConfig.remote`) pointing at it exists
    (`git remote add`/`set-url`), without disturbing `repo.remote` (`origin`)
    which stays the read/rebase source of truth.
  - Publish the PR head to the fork remote instead of `repo.remote` — the
    existing `head_scheme`/`head_pattern` machinery is reused unchanged; only
    the *destination remote* differs.
  - `_open_via_provider` passes `<fork-owner>:<branch>` as the head and the
    upstream slug as the target repo (GitHub's native fork-PR shape).
- [ ] `pr-merge`/`finalize` must never attempt a self-merge when the resolved
  role's `merge_actor` isn't `submitter-direct` — surface "waiting on
  Maintainer approval" status instead (reuse `classify_pr_flow`'s existing
  `reviewer`/`consent-gate` derivation; no new state needed there).
- [ ] Tests: end-to-end `create_pr` fork-path coverage (fake `gh`/`git`,
  matching existing `test_pr_ops.py` fixtures), plus a `pr_merge` refusal test
  for a non-`submitter-direct` resolved role.

### Phase 3 — Guidance + downstream adoption
- [ ] `docs/config-reference.md`: document `pr.roles` / `pr.fork` alongside the
  existing PR-flow legibility matrix fields.
- [ ] `gim-home/odsp-web-harness` (separate repo, its own PR): adopt
  `pr.roles`/`pr.fork` in its `.agent-worktrees/config.yaml`, replacing its
  current "Tooling note" plain-`git`/`gh` workaround in `CONTRIBUTING.md` with
  real `agent-worktrees`-driven guidance. Tracked by that repo's own
  `role-aware-fork-pr-flow` pointer effort, not here.
- [ ] Point that repo (and any other adopter) at `turnkey-reviewer-loops` for
  the "automated self-reviewer with approval/verdict power" piece, rather than
  treating it as new work.

## Validation Plan

- [x] Phase 2a: new config/provider unit tests pass; full pre-existing
      `test_config.py`, `test_providers.py`, `test_pr_ops.py`,
      `test_pr_merge.py`, `test_pr_merge_now.py` suites pass unchanged
      (359 tests total, 0 regressions).
- [ ] Phase 2b: a synthetic `write`-role repo config drives `create-pr`
      end-to-end onto a fork remote with a correct `<owner>:<branch>` head,
      exercised against fake `gh`/`git`, without touching a real GitHub repo.
- [ ] Phase 2b: a synthetic `maintain`-role config is provably unaffected
      (identical behavior to today, byte-for-byte `PRConfig`).
- [ ] Phase 3: `gim-home/odsp-web-harness` successfully drives a real fork-based
      PR via `agent-worktrees` (not by hand) as its own validation.

## Proposal

Phase 2a is implemented and tested (this session); submitting its PR is the
immediate next step, per this repo's review-gate-before-execution norm
(applied here retroactively to the already-implemented slice, since it is a
small, additive, fully-tested change — Phase 2b's higher-risk `create_pr`
integration will go through the gate *before* implementation, not after).

## Journal

### 2026-09-10 — Kickoff + Phase 2a
- Effort created; canonical placement decided (copilot-extensions, not the
  originating odsp-web-harness repo, since this repo owns the implementation
  and has adopted `efforts/`).
- Found `turnkey-reviewer-loops` (done) already covers the "automated
  self-reviewer with real approval/verdict power" half of the original
  request — descoped from this effort's Plan accordingly.
- Implemented and tested Phase 2a: role-resolution primitives and config
  schema (`GITHUB_ROLE_LEVELS`, `ForkConfig`, `PRRoleOverride`,
  `PRConfig.roles`/`PRConfig.fork`, `resolve_role_pr_config`,
  `PRProvider.get_viewer_permission` + GitHub implementation + inert stubs).
  Deliberately stopped short of wiring it into `create_pr`/`pr_merge`
  (Phase 2b) given how large and heavily-depended-on that code path is —
  that phase gets its own review before implementation, not a same-session
  rewrite of `create_pr`'s core.
