# copilot-extensions-maintainer

> Opt-in source contribution and maintenance procedures, bounded by assignment.

This payload-only companion separates source work from the consumer diagnosis
in `copilot-extensions-harness`. Enable it for ongoing maintenance of this
repository, not to make downstream product tasks repair their own tools.
Activation is not a work assignment, publication approval or a Maintainer role.

## What it does (and how to use it)

| Entry point | Use |
|---|---|
| `contributing-to-copilot-extensions` | Deliver an explicitly assigned source change through tests, changefiles and the required PR flow. |
| `reviewing-copilot-extensions-vision` | Apply an explicitly selected conceptual review lens. |
| `diagnosing-validate-and-promote-failures` | Diagnose the release pipeline within an assigned maintenance charter. |
| `validating-in-clean-room` | Run, evaluate or author a contained clean-room scenario. |
| `clean-room-judge` agent | Evaluate a supplied Tier-E run without repairing its setup. |
| `copilot-extensions-reviewer` agent | Perform the repository's explicitly dispatched review-loop assignment. |

For example, ask to change one plugin's behavior. The assignment includes its
normal tests, rebases, required PR and review fixes without repeated generic
permission requests. It does not include unrelated products or installation
rollout. An operator may separately assign a bounded automatic-maintenance
charter, including reporting and contribution destinations.

## What this plugin provides -- and what it doesn't

**Provides:** contribution, review, release-triage and clean-room procedures,
static maintenance guidance and a scoped publication guard.

**Does not provide:** automatic work discovery, issue/PR permission, an
installation, OS privileges, runtime patching or a deployment grant.
During another product's task, supporting source remains frozen even when this
package is enabled. Report incidental failures, offer a bug report and return
to that task; do not make it wait for an upstream fix.

An explicit contribution does not require this plugin to be enabled. The
repository's `CONTRIBUTING.md` and `.github/skills/` adapters point to these
canonical source procedures directly. Plugin presence controls availability,
not a contributor's repository access or right to request a change.

## Dependencies & assumptions

Enable the consumer companion for installed-artifact diagnosis. Worktree,
publication and validation operations use their existing owners and the
target repository's current policies. Clean-room experiments need their own
bounded resources and effect authority. No runtime is installed by this package.

The `preToolUse` guard refuses publication of an `@copilot` mention to this
repository when the workspace explicitly adopts this maintainer plugin.
Such a mention starts a separate coding agent; it is not a review request.
The guard resolves its target from the manifest, applies only to shell tools,
and retains its existing fail-open availability and fail-closed scoped-policy
behavior. It never publishes the proposed command.

## What's in this plugin

`skills/` contains the canonical source-work procedures. `agents/` contains the
bounded reviewer and clean-room judge. `instructions/` and
`instruction-projections.json` declare maintenance guidance.
`references/` contains contribution, review and sanitization detail.
`scripts/`, `hooks.json` and `tests/` own the scoped publication guard and its
transport contracts. Skill files are the authoritative procedures.

## Troubleshooting, contributing & issues

If installed commands fail, use the consumer package's diagnosis within
existing repair authority. If a contribution procedure is unavailable, read
the repository's canonical source; do not enable maintenance to unblock a
different product. If validation fails, record its evidence and disposition
without weakening required CI or expanding the assignment.

Follow `CONTRIBUTING.md`, `TESTING.md` and the contribution skill for changes
here. Report incidental defects only with applicable publication authority
at the established tracker.
