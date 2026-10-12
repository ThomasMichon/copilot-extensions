# Adopter Documentation — Vision

- **Subject:** The toolkit's adopter-facing documentation
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-12
- **Reality docs:** [Documentation architecture](../../docs/documentation-architecture.md)

## Purpose & Intent

Documentation should make copilot-extensions useful to someone assembling
their own harness. It should explain reusable capabilities, their boundaries,
and the decisions an adopter owns, without presenting one operator's assembled
harness as the toolkit itself.

## Concepts & Components

- **Front door:** a short introduction and an actionable current installation
  path, with deliberate routes to deeper material.
- **Adopter waypoints:** capability selection, adoption, configuration ownership,
  architecture, and operating guidance with distinct responsibilities.
- **Owner references:** plugin-local procedures and command contracts that
  remain authoritative for their domain.
- **Intent and examples:** visions describe desired outcomes; explicitly
  hypothetical examples illustrate choices without asserting shipped features.
- **Maintenance contract:** authors and reviewers keep claims and navigation
  consistent with the implementation through existing publication safeguards.

## Features

### Progressive reading

An adopter should reach a first useful capability without reading an
encyclopedia, then find the relevant configuration and operational references
as their deployment grows.

### Capability-honest selection

Capability descriptions should distinguish available behavior, dependencies,
optional integrations, compatibility surfaces, and future intent. Platform
support should be attributable to the component and workflow actually used.

### Portable examples

Examples should show plausible small and composed deployments using generic
identities, while making their hypothetical nature and adoption decisions
explicit.

## Behaviors

### One authoritative home

Shared waypoints should explain and route; detailed mechanics should remain
with their existing owner. Navigation should preserve access to useful
references rather than replacing them with competing copies.

### Evidence before assurance

Present-tense claims should be grounded in shipped implementation and owning
documentation, not inferred from visions, planning milestones, screenshots,
or another component's platform support.

### Change-coupled maintenance

Changes should reconcile affected documentation before publication. Mechanical
checks should enforce objective contracts such as catalog coverage and live
navigation, while reviewers assess semantics and the adequacy of evidence.

## Non-Goals / Boundaries

- Not a preconfigured operator harness, a mandate to install the entire suite,
  or a universal guarantee across platforms and venues.
- Not runtime implementation, deployment automation, or an alternative command
  and configuration authority.
- Not a place for private topology, organization-specific procedures, or
  personal activity.
- Preview assets should illustrate an identified supported surface; they
  should not substitute for capability evidence.

## See Also

- [Vision index](../README.md)
- [Harness guidance vision](../harness-guidance/README.md)
- [Documentation architecture](../../docs/documentation-architecture.md)
- [Contribution and documentation-impact review](../../CONTRIBUTING.md#documentation-impact-required-before-opening-a-pr)
