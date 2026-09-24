# Review guidance — copilot-extensions

This file is read by GitHub Copilot code review specifically (this repo's
`copilot_code_review` branch-protection rule auto-requests it, non-blocking,
on every PR) — see [Customizing Copilot's reviews with custom
instructions](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review#customizing-copilots-reviews-with-custom-instructions).
Unlike [`.github/copilot-instructions.md`](.github/copilot-instructions.md)
(which also shapes Chat and the coding agent), this file's guidance is
review-only.

The full guides remain [`AGENTS.md`](AGENTS.md) (development guide),
[`CONTRIBUTING.md`](CONTRIBUTING.md) (contribution boundary, versioning &
release), and [`docs/patterns/README.md`](docs/patterns/README.md) (the
numbered design principles and binding design invariants) -- those are the
standing review authority; this file is a lens for applying them
specifically when reviewing a pull request, not a replacement for them.

## Directives for the automatic code-review agent

- **Flag any PR whose base is `main` directly.** `main` is regenerated
  wholesale by this repo's own CI promotion pipeline
  (`.github/workflows/promote.yml`, dev-branch-release-pipeline effort,
  ThomasMichon/copilot-extensions#3336) via an automated snapshot PR: base
  `main`, head branch matching `release/promote-<run id>`, authored by the
  pipeline's own worker identity (the `github-actions` bot). Contributors
  always target `dev` — a PR whose base is `main` and whose head branch does
  NOT match `release/promote-*` is not a legitimate use of this repo's PR
  flow (the repo's own CI has a hard guard for this too, but that only
  fails the build; this comment is the visible signal on the PR itself,
  since Copilot review never blocks a merge here). Post this comment,
  verbatim, exactly once per PR (do not repeat it on subsequent review
  passes of the same PR):

  > This PR targets `main` directly. `main` is only ever updated by this
  > repo's own CI promotion pipeline (dev-branch-release-pipeline effort,
  > `.github/workflows/promote.yml`) via an automated snapshot PR from a
  > `release/promote-*` branch. Please retarget this PR's base branch to
  > `dev` — see `CONTRIBUTING.md` § Release & Versioning.
- **Scope to the diff.** Review the code the PR actually changes. The repo
  carries pre-existing style debt — do **not** demand repo-wide cleanup or
  flag untouched code.
- **Concrete over cosmetic.** Prefer flagging concrete violations of
  `AGENTS.md`'s and `CONTRIBUTING.md`'s standards over stylistic nitpicks.
- **Lead with the highest-signal miss: the changefile requirement.** For any
  changed plugin *payload* in a PR targeting `dev`, verify a pending
  changefile names it (`python tools/changefile.py add --plugin <name>
  --type <major|minor|patch|dev> --comment "..."`) — a missing changefile
  means the CI promotion pipeline has nothing to bump for that plugin when
  it next regenerates `main`, so the marketplace silently keeps serving the
  old version. This replaced the old three-file hand-bump convention:
  contributors no longer hand-edit `plugin.json` / `pyproject.toml` /
  `marketplace.json` version fields themselves — the promotion pipeline's
  `tools/accumulate_bumps.py` writes the real version numbers mechanically
  when it consumes pending changefiles. Do not ask for a hand-bumped
  triplet on an ordinary PR; that is now only correct on the pipeline's own
  generated snapshot commit.
- **Tests for runtime logic.** Flag PRs that change a runtime plugin's logic
  without adding or updating that plugin's `tests/`.
- **Test portfolio growth.** Flag new exhaustive matrices, repeated process
  startup, or specialized suites added unconditionally to required PR CI.
  Require path gating, a focused smoke contract, and a scheduled/manual
  exhaustive lane. Do not recommend pooling where real process boundaries
  are the behavior under test.
- **Cross-platform parity.** When a PR edits an installer/launcher `.sh` (or
  `.ps1`), flag a missing matching change to its `.ps1` (or `.sh`)
  counterpart.
- **Identifier neutrality.** Flag any newly introduced internal
  organization/account/project names, private hostnames, or personal
  aliases — this repository is public. PR titles, bodies, labels, commit
  messages, and hidden comments count; the default `codename` marker (only
  the worktree's assigned codename, decodes to nothing on its own) is
  expected and not a violation, but flag any attempt to enable the full raw
  `pr.source_attribution: true` here.
- **Contribution boundary.** Flag a change whose value or implementation
  depends on a particular person's private state or a particular
  organization's internal systems, identity, process, or data — that
  belongs in the adopter's private control repo or that organization's
  internal marketplace, not here (`CONTRIBUTING.md`'s "Contribution
  boundary").
- **Architectural changes reconcile to both layers.** A design change (not a
  below-altitude lint/typo/dependency bump) should reconcile with the
  relevant `visions/` entry *and* check against `docs/patterns/README.md`'s
  design principles and invariants -- flag a design change that does
  neither.
- **Documentation impact.** Confirm the PR description's required
  Documentation-impact statement actually matches the final diff
  (`CONTRIBUTING.md`, "Documentation impact") -- flag a missing or
  inaccurate one.
- **ruff signal, not noise.** Hold changed Python to at least the `F`/`E9`
  groups; do not block on pre-existing style debt in code the PR did not
  touch.
- **Make every comment count.** Copilot review comments (it does not
  approve or block merges here), so each comment should be actionable and
  worth the author's attention.
