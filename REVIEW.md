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

- **Scope to the diff.** Review the code the PR actually changes. The repo
  carries pre-existing style debt — do **not** demand repo-wide cleanup or
  flag untouched code.
- **Concrete over cosmetic.** Prefer flagging concrete violations of
  `AGENTS.md`'s and `CONTRIBUTING.md`'s standards over stylistic nitpicks.
- **Lead with the highest-signal miss: the version-bump triplet.** For any
  changed plugin *payload*, verify all three version locations moved
  together (`plugins/<name>/plugin.json`, `plugins/<name>/pyproject.toml`,
  and that plugin's entry in `.github/plugin/marketplace.json`) — a
  partial/missing bump silently breaks machine updates. This is the single
  most valuable thing to catch.
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
  messages, and hidden comments count; flag any attempt to enable
  `pr.source_attribution` here.
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
