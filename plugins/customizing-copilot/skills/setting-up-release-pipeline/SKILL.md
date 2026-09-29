---
name: setting-up-release-pipeline
description: >
  Stand up (or audit) a tiered dev/main promotion pipeline for a
  plugin-marketplace or multi-component harness repo, so it can be checked
  out stably from `main` by passive consumers while accepting continuous
  contributor work on `dev` -- greenfield ("set up a release pipeline like
  copilot-extensions"), brownfield ("my repo needs a stable branch for
  consumers"), or audit ("check our dev/main pipeline for the same
  misconfigurations copilot-extensions hit"). Routes to and drives the
  portable dev-main-promotion-pipeline pattern doc: branch roles, ruleset
  config, the changefile/version-bump/vendoring system, and the generic
  CONTRIBUTING template.
  Trigger phrases include:
  - 'set up a release pipeline'
  - 'dev main pipeline'
  - 'promotion pipeline'
  - 'stable branch for consumers'
  - 'changefile system'
  - 'marketplace repo like this one'
  - 'tiered dev/main branches'
  - 'audit our promotion pipeline'
---

# Setting Up a Release Pipeline

Turn a multi-component repo (a plugin marketplace, a monorepo of packages, a
harness with several installable pieces) into one with a stable, zero-review
`main` for passive consumers and a continuously-updated, reviewed `dev` for
active contributors — connected by a fully mechanical promotion pipeline.

## First: get the pattern doc

The pattern doc is the source of truth; read it before acting.

- **Local checkout present** (you are inside or beside a `copilot-extensions`
  checkout): read
  [`docs/patterns/dev-main-promotion-pipeline.md`](../../../../docs/patterns/dev-main-promotion-pipeline.md).
- **No checkout** (fresh agent in a vanilla folder): fetch it from the repo:
  `https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/docs/patterns/dev-main-promotion-pipeline.md`.

It is written to be **ported wholesale**, not just read for inspiration —
every identifier in it (`RELEASE_AUTOMATION_TOKEN`, `dev`, `main`,
`<maintainer-1>`) is a placeholder for the target repo's own names.

## Detect the mode, then work the doc's sections

| Mode | Operator ask | Behavior |
|------|--------------|----------|
| **Greenfield** | "set up a release pipeline like copilot-extensions" | Work §6's bootstrap checklist top to bottom in a new repo. |
| **Brownfield** | "my repo needs a stable branch, but already has contributors on main" | Introduce `dev` as the contributor branch, migrate branch protection per §2.2, then build the pipeline; expect a short dual-running window while `main` catches up to `dev` for the first time. |
| **Audit** | "check our dev/main pipeline for the same misconfigurations" | Pull every branch ruleset for both branches and check **both** `required_approving_review_count` **and** `require_code_owner_review` independently on each one (§5) — this is the exact bug class that jammed the reference implementation in production, twice, in two different shapes. Also confirm the mechanical changefile-cleanup PR path has the path-verified admin-merge fallback (§2.4), not a bare `--auto`. |

## Do not re-derive the design — port it

This pattern already encodes hard-won, live-incident-confirmed decisions
(why `main` can safely require **zero** reviews, the generated-PR merge
race, the "never admin-merge main" rule, pinning what you validate). Don't
freelance a variant from first principles; follow the doc's ruleset
templates, workflow shape, and changefile system directly, substituting
only the target repo's own names for the placeholders.

## Finish

- Branch rulesets on both `dev` and `main` match §2.2, checked against §5's
  gotcha explicitly.
- The changefile tool, accumulate/consume tool, and version-sync +
  changefile-presence checks exist per §4.
- The promotion workflow (gate → validate at a pinned SHA → promote →
  tag → changefile cleanup) exists per §2.1 and §4.4, with the
  path-verified admin-merge fallback for the cleanup PR per §2.4.
- The target repo's own `CONTRIBUTING.md` carries §3's template, adapted.
- A dry run (§6, step 8) completes both halves — the promotion **and** the
  changefile cleanup — with zero manual intervention.
