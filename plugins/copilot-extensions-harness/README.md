# copilot-extensions-harness

> Consumer awareness and bounded diagnosis, not automatic source maintenance.

Enable this payload-only plugin when using the suite's commands and behaviors.
It identifies their owners and distinguishes authorized installer-equivalent
repair from prohibited edits to installed package logic. During another
product's task, treat this suite's source as frozen.

Existing workspaces must explicitly retire previously projected maintenance
guidance; updating this payload alone does not remove it. Follow the
[consumer migration guide](references/consumer-migration.md) with workspace
configuration authority. No migration or rollout runs automatically.

## What it does (and how to use it)

| Entry point | Use |
|---|---|
| `diagnosing-copilot-extensions` | Diagnose a missing command, failed update, damaged generated binding or unhealthy service. |
| `checking-copilot-extensions-release` | Check whether a source change has reached a release; this does not install it. |
| `consumer-boundary` instruction | Keep diagnosis, local repair and source publication as separate authorities. |

For example, ask to diagnose a missing command. Use the supported repair owner
within existing approval, then verify the original operation. If safe recovery
is unavailable, report the exact blocker and offer a bug report at the
established destination. Do not start a source PR or wait for an upstream fix.

## What this plugin provides -- and what it doesn't

**Provides:** artifact ownership, read-only diagnosis, release awareness,
no-monkey-patching guidance and portable source provenance.

**Does not provide:** automatic issue filing, source fixes, PR publication,
release stewardship, service activation or installation approval. Local repair
may restore only the authentic selected installer's canonical result while
preserving operator state. It must not change shipped logic or templates.

An explicit request to change this repository is still valid without a
maintainer plugin: follow its own `CONTRIBUTING.md` and repository contribution
skill directly from a resolved source checkout. Do not install a plugin merely
to read that procedure. The optional `copilot-extensions-maintainer` package
provides contribution and review procedures for ongoing source work; enabling
it does not expand the current assignment.

## Dependencies & assumptions

This package has no runtime or hook. Diagnosis assumes the selected
installation's existing commands and authentic repair inputs are available.
Their owners retain permission, custody, privilege and preservation checks.
Read `docs/installation-footprint.md` for the canonical repair boundary.

## What's in this plugin

`skills/` contains diagnosis and release checks. `instructions/` and
`instruction-projections.json` declare the static consumer boundary.
`references/external-service-outages.md` covers evidence for outage claims.
`.agent-worktrees/related.yaml` supplies lowest-precedence source provenance.
Skill files are the authoritative procedures.

## Troubleshooting, contributing & issues

If a command is unavailable, diagnose its selected installation; do not patch
the payload. If canonical repair is unavailable, preserve state and report the
blocked operation. A missing maintainer plugin does not prevent an explicitly
requested contribution through the repository's own instructions.

Offer to report incidental defects at the already-established tracker. Obtain
publication authority before filing, and follow `CONTRIBUTING.md` for an
explicitly requested source contribution.
