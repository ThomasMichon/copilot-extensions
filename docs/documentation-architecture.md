# Documentation Architecture

This page maps documentation ownership, not runtime architecture. The suite is
a general-purpose toolkit for assembling a harness; an adopter's repositories,
policy, credentials, and topology are not a supplied operator deployment.
The [documentation vision](../visions/documentation/README.md) records standing
intent. This page records the existing surfaces and the maintenance pattern.

## Page roles and authorities

| Surface | Role | Authority boundary |
|---------|------|--------------------|
| [Root README](../README.md) | Public front door and plugin discovery | Orient and route; plugin manifests and references ground claims |
| [Adopter index](README.md) | Primary reading path | Route by task without requiring every reference |
| [Installation](installation.md), [capabilities](capabilities.md), [adoption](adoption.md) | First-use, selection, and integration waypoints | Summarize supported boundaries; exact mechanics remain component-owned |
| [Reference index](reference-index.md) | Deeper reference discovery | Preserve component and planning links without duplicating mechanics |
| [Architecture](architecture.md) | Current composition, payload/runtime split, invocation and service relationships | Runtime model, not standing future intent |
| [Configuration](configuration.md) | Repository, machine, user, and installation-cell ownership | Route exact schemas and precedence to their plugin owner |
| [Harness runbook](harness-runbook.md) | An opinionated assembly procedure | A chosen workflow, not mandatory installation of every capability |
| [Patterns](patterns/README.md) | Reusable design conventions and invariants | How contributors build; not an adopter command catalog |
| `plugins/<name>/README.md`, `docs/`, `skills/` | Component setup, behavior, commands, recovery | The owning plugin's detailed contract |
| [Visions](../visions/README.md) | Standing should-be | Not evidence that a feature is available |
| [Efforts](../efforts/README.md) and issues | Coordination and implementation evidence | Progress is not a capability guarantee |
| [Contributing](../CONTRIBUTING.md), [testing](../TESTING.md), [pipelines](pipelines.md) | Publication and validation procedure | Contributor workflow, not the initial adopter path |

The marketplace roster is owned by
[`.github/plugin/marketplace.json`](../.github/plugin/marketplace.json).
Plugin manifests and payload/runtime declarations determine what is installed.
Catalog summaries should route to these owners rather than invent independent
version, skill, or platform inventories.

## The reading pattern

Use a short front door, task-oriented waypoints, and deeper owner references.
Installation comes before optional deployment examples or design intent.
Capability selection precedes provisioning additional services or venues.
Configuration explains ownership before exposing component-specific settings.

A waypoint may summarize why a component is useful, but should link to its
installer, command glossary, or skill for exact mechanics. Moving introductory
prose out of the README must preserve reference discovery. The
[adopter index](README.md), [selection map](capabilities.md), and
[adoption waypoint](adoption.md) implement this pattern without moving
plugin-owned references.

Examples must be labeled as hypothetical deployments. They illustrate choices,
not a shipped bundle, a universal platform promise, or a provider integration
that has not been implemented. Future features belong in visions or explicitly
marked planning material, not executable quick-start instructions.

## Maintaining documentation

The required [documentation-impact review](../CONTRIBUTING.md#documentation-impact-required-before-opening-a-pr)
applies to every PR. Assess the final diff, update the affected owner and
waypoint pages, and explain documentation impact in the PR description.
Preserve accurate references and reconcile architectural intent separately.

Existing publication safeguards provide distinct checks:

- `tools/check-docs-consistency.py`: marketplace coverage in the README,
  enumerated plugin skills, recognized catalog-count claims, and local
  paths/anchors plus reading-path edges in the primary adopter pages.
- `tools/check-runbook-references.py`: runbook skill and plugin references.
- `tools/check-effort-vision-structure.py`: canonical planning and vision
  section contracts.
- `tools/check-no-internal-identifiers.py`: public identifier neutrality when
  a private denylist is configured.
- `tools/check-large-files.py`: publication-size limits.

These are mechanical checks, not a claim that all documentation is current.
The navigation check covers the primary pages' inline/reference links, ATX
headings, and HTML link/image and explicit-anchor syntax. Fenced/inline code
and comments are excluded. Images are checked as
assets but cannot satisfy reading-path hyperlinks. Resolved local targets,
including a directory's README anchor target, must remain inside the repo.
The guard is self-contained Python tooling, so existing PR and promotion
callers do not need new dependency bootstrap. It is a bounded conformance check,
not a general Markdown renderer; review rendered navigation as well when
introducing syntax outside the fixture-covered primary-page conventions.
Authors must also verify changed navigation outside the guarded primary path, ground support claims
in the owner implementation, and have semantic claims reviewed. Extend an
existing guard for an objective navigation or ownership invariant rather than
introducing a competing documentation pipeline or a phrase blacklist.

## Related reading

- [Architecture patterns](patterns/README.md)
- [Install contract](install-contract.md)
- [Review rubric](../REVIEW.md)
