# Copilot Extensions contribution boundary

`copilot-extensions` is the public, general-purpose plugin marketplace.

## Welcome here

- Reusable engines, protocols, lifecycle tools, and authoring patterns that do
  not require a particular person or organization.
- Portable behavior with explicit extension/configuration seams for downstream
  organization-specific policy.
- Capabilities whose examples and tests can be public and scrubbed.

## Not welcome here

- Personal workflows, machine inventory, private state, identity assumptions,
  or one-operator experiments. Keep those in the private control/knowledge repo.
- Organization-specific internal systems, process, and policy. Contribute those
  to the adopter's organization-owned internal marketplace.
- Secrets, internal endpoints/data, customer information, or examples that
  cannot be made organization-neutral.

## Composition rule

Put the generic mechanism here and the personal or organizational policy in its
own marketplace plugin. If the capability has no useful identity after those
assumptions are removed, it does not belong here.

The repository root `CONTRIBUTING.md`, `AGENTS.md`, and `TESTING.md` remain
authoritative for current mechanics and gates.
