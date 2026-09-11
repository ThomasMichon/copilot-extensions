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

## Design (Phase 1 — settled; revised after #2433)

**Role resolution.** GitHub's `gh api repos/<owner>/<repo>` response carries
the *authenticated caller's* permission on that repo via a `permissions`
object. **Revision (2026-09-11):** while Phase 2a's PR was still open, this
same repo's `main` landed `agent-worktrees: gate pr-self-merge on live
per-identity permission (#2433)` — an overlapping, more complete capability:
`pr_contract.RepoPolicy.viewer_permission` (read inside the *existing*
`get_repo_policy` call, no second request), a pure `actor_merge_authority()`
classifier, `providers.actor_viewer_permission()` (the fail-open read
helper), support for **both** GitHub and Gitea, and GitHub-Enterprise
host-awareness (`--hostname`). Phase 2a's own provider-level
`get_viewer_permission` method was **removed** in favor of
reusing #2433's primitives directly — see the Journal. `config.GITHUB_ROLE_LEVELS`
was extended to include `"none"` (a confident no-access read, distinct from
an unresolved/`""` one) to match `actor_merge_authority`'s vocabulary.

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

**Confirmation gate.** `create_pr` never forks or pushes anywhere on a
caller's first call when the resolved flow needs a fork: it returns
`needs_confirmation: "fork_setup"` with a human-readable `message` for the
calling agent to relay. Only a second call with `confirm_fork=True`
(`--confirm-fork` on the CLI) actually creates/verifies the fork and
publishes there.

## Plan

### Phase 2a — Role resolution + config shape (landed this session, then reconciled)
- [x] `config.GITHUB_ROLE_LEVELS` (later extended with `"none"`),
      `config.ForkConfig`, `config.PRRoleOverride`.
- [x] `PRConfig.roles: dict[str, PRRoleOverride]` and `PRConfig.fork: ForkConfig`
      fields + `_parse_pr` support (unknown role keys dropped, not raised).
- [x] `config.resolve_role_pr_config(prcfg, role) -> PRConfig` — pure, total,
      backward-compatible.
- [x] ~~`PRProvider.get_viewer_permission`~~ — **removed** after rebasing onto
      `main`'s #2433, which landed the same capability more completely
      (`RepoPolicy.viewer_permission` + `providers.actor_viewer_permission()`,
      GitHub + Gitea, GHE-host-aware). Phase 2b consumes #2433's primitives
      directly instead of a duplicate provider method.
- [x] Unit tests: `test_config.py` (parsing + resolution), `test_providers.py`
      (superseded `get_viewer_permission` tests dropped during the rebase;
      #2433's own `viewer_permission`/`get_repo_policy` tests kept as-is).
- [x] Landed via PR [ThomasMichon/copilot-extensions#2435](https://github.com/ThomasMichon/copilot-extensions/pull/2435).

### Phase 2b — Wire resolution + fork publish into `create_pr` (landed this session)
- [x] At the top of `create_pr` (right after the `dry_run` short-circuit, so
      `dry_run` itself is unaffected), resolve `default_pr_repo` early, call
      `_resolve_caller_role()` (thin wrapper around #2433's
      `providers.actor_viewer_permission`, GitHub-only, `None` on any
      failure/non-GitHub-provider) when `prcfg.roles` is configured, and layer
      it via `resolve_role_pr_config` — only when the repo opts in, so an
      unconfigured repo's `prcfg` is untouched.
- [x] When the resolved `PRConfig.fork.enabled` is true: return
      `needs_confirmation` unless `confirm_fork=True`; then
      `_ensure_fork_and_remote()` creates/reads the fork (`GitHubProvider
      .ensure_fork`, idempotent `POST /repos/<repo>/forks`) and points a local
      `fork` remote at its `clone_url` (`git_ops.ensure_remote`, new — add or
      repoint, never touches any other remote).
- [x] Publish targets a new `publish_remote` variable (defaults to `remote`,
      diverges only in fork mode) at every push/exists-check site — the
      refspec push, the snapshot push, the "branch already exists" guard, and
      `_push_existing_feature`'s re-run push. `remote`/`upstream` themselves
      are untouched, so fetch/rebase always target the true upstream even in
      fork mode.
- [x] `scope_from_create_result` prefers `result["pr_head"]` (an explicit
      `<owner>:<branch>`) over the plain branch name — the only provider-layer
      change needed, since `gh pr create --head owner:branch --repo
      <upstream>` already natively supports a fork PR.
- [x] Tests: `TestCreatePRForkFlow` in `test_pr_ops.py` (4 cases) — the
      confirmation gate does nothing on a first call; a confirmed call pushes
      to a real local bare "fork" repo (not origin) and opens with the right
      `<owner>:<branch>` head; an explicit `fork.owner` override wins; an
      unconfigured repo is provably unaffected.
- [ ] **Known gap, deferred:** the branch-reuse/stale-PR-pruning
      reconciliation loop near the top of `create_pr` (detecting a prior
      active PR's branch as pruned/merged) still checks `remote` (origin)
      unconditionally. A repo that enables fork mode *after* already having a
      live PR published under the old direct-push flow is unaffected (that
      PR really was on origin); a worktree with a live PR that was itself
      already published to a fork from a previous fork-mode `create-pr` call
      would have this reconciliation loop check the wrong remote. Not fixed
      this session — flagging it explicitly rather than leaving it a silent
      surprise.
- [ ] `pr-merge`/`finalize` refusing self-merge for a non-`submitter-direct`
      resolved role is **not yet done** — #2433 already gates `pr-merge --now`
      on *live merge authority* (`actor_merge_authority`), which covers the
      most important case (a contributor cannot self-merge regardless of what
      `pr.merge_actor` says); a `pr.roles`-driven override of `merge_actor`
      itself for `pr-merge`'s flow-classification (not just the authority gate)
      is left for a follow-up increment if it proves necessary in practice.

### Phase 3 — Guidance + downstream adoption
- [x] `docs/config-reference.md`: documented `pr.roles` / `pr.fork` + the
      confirmation-gate behavior, alongside the existing PR-flow legibility
      matrix fields.
- [ ] `gim-home/odsp-web-harness` (separate repo, its own PR): adopt
  `pr.roles`/`pr.fork` in its `.agent-worktrees/config.yaml`, replacing its
  current "Tooling note" plain-`git`/`gh` workaround in `CONTRIBUTING.md` with
  real `agent-worktrees`-driven guidance. Tracked by that repo's own
  `role-aware-fork-pr-flow` pointer effort, not here.
- [ ] Point that repo (and any other adopter) at `turnkey-reviewer-loops` for
  the "automated self-reviewer with approval/verdict power" piece, rather than
  treating it as new work.

## Validation Plan

- [x] Phase 2a+2b: new config/provider/pr_ops unit tests pass (14 config/
      provider cases + 4 fork-flow `create_pr` cases); full pre-existing
      `test_config.py`, `test_providers.py`, `test_pr_ops.py`,
      `test_pr_merge.py`, `test_pr_merge_now.py`, `test_pr_contract.py`,
      `test_cli_routing.py` suites pass unchanged (584 tests total after
      rebasing onto #2433, 0 regressions).
- [x] Phase 2b: a synthetic `pr.fork.enabled` repo config drives `create-pr`
      end-to-end onto a real local bare "fork" git repo with a correct
      `<owner>:<branch>` head, proven never to touch origin, without hitting a
      real GitHub API (fake provider `ensure_fork`).
- [x] Phase 2b: a repo that never configures `pr.fork`/`pr.roles` is provably
      unaffected (`test_fork_mode_off_by_default`).
- [ ] Phase 3: `gim-home/odsp-web-harness` successfully drives a real fork-based
      PR via `agent-worktrees` (not by hand) as its own validation.

## Proposal

Phase 2a + Phase 2b are implemented and tested this session, submitted
together as one PR (Phase 2a's own PR #2435 was still open/unmerged when
Phase 2b work started, so it absorbed both slices rather than stacking a
second PR on an unmerged one). Phase 3's downstream adoption in
`gim-home/odsp-web-harness` remains open, blocked on this PR merging.

## Journal

### 2026-09-11 — Phase 2b: fork publish flow + reconciliation with #2433
- Resuming to drive the actual fork-remote-and-PR flow, found PR #2435
  (Phase 2a) had gone stale: `main` had moved and, more importantly, landed
  `agent-worktrees: gate pr-self-merge on live per-identity permission
  (#2433)` — an independently-built, more complete version of exactly Phase
  2a's role-resolution primitive. Rebased onto it and **removed** Phase 2a's
  own `PRProvider.get_viewer_permission` (protocol method + GitHub impl +
  Gitea/Azure DevOps stubs + its 6 tests) in favor of consuming #2433's
  `providers.actor_viewer_permission()` / `RepoPolicy.viewer_permission`
  directly — no duplicate provider-call shape to maintain. Extended
  `GITHUB_ROLE_LEVELS` with `"none"` to match #2433's vocabulary.
- Implemented Phase 2b: role resolution + fork-publish confirmation gate at
  the top of `create_pr` (right after the `dry_run` short-circuit); a new
  `git_ops.ensure_remote()`/`remote_url()` pair; `GitHubProvider.ensure_fork()`
  (idempotent fork create/read via `gh api -X POST repos/<repo>/forks`);
  threaded a `publish_remote` variable through every push/exists-check call
  site in `create_pr` and `_push_existing_feature`, leaving `remote`/
  `upstream` (fetch/rebase) untouched; `scope_from_create_result` prefers an
  explicit `pr_head` (`<owner>:<branch>`) over the plain branch. Added
  `--confirm-fork` to the CLI and a `needs_confirmation` human-readable branch
  in `cmd_create_pr`.
- Deliberately left two things for later, named explicitly in the Plan rather
  than silently glossed over: the branch-reuse/stale-PR-pruning
  reconciliation loop still assumes `origin` unconditionally (a corner case
  for a worktree transitioning into fork mode mid-flight), and `pr-merge`'s
  flow classification doesn't yet consume `pr.roles`' `merge_actor` override
  (though #2433's live-authority gate already covers the safety-critical
  case).
- Ran the full regression surface after every slice: 275 (config+providers),
  254 (pr_ops+pr_merge+pr_merge_now+pr_contract), then 584 (adding
  test_cli_routing) — all passing, 0 regressions, before adding the 4 new
  fork-flow tests (also passing).

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
