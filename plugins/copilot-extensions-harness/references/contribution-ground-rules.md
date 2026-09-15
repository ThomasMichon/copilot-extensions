# Copilot Extensions contribution boundary

`copilot-extensions` is the public, general-purpose plugin marketplace.

## Public-first: the default, not a stub

When a change is requested to an `agent-*` plugin or another portable
component and the concern can be stated and validated without private
context, **propose or offer to plan and file it here by default** — a
generalized public issue and, for a coordinated stretch, a public
`efforts/active/<slug>/` plan — rather than defaulting to a private-only
tracker. This repository is the **canonical planning and issue surface** for
that portable work, not merely a claim/debounce stub for it.

Use the **canonical-public / private-elaboration** model:

- A public GitHub issue here is the canonical work item for a portable bug or
  enhancement.
- A public `efforts/active/<slug>/` effort here is the canonical plan for a
  coordinated portable stretch.
- A private issue or effort in a downstream control repository points
  **one-way** to the public artifact and carries only the additional private
  deployment/evidence context (topology, credentials, adoption rationale).
  Public artifacts never point to or name a private repository, machine,
  account, or record identifier.
- A concern that cannot be stated or validated without private context stays
  private until a generic reproduction or contract can be extracted from it.

This reverses an earlier, more defensive posture that treated a public issue
as only a claim-stub and kept the substantive planning private. Once a
concern has sufficient public surface, plan and track it here directly.

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

## Evidence: minimize and anonymize, don't just scrub secrets

Every issue, PR, commit, and public effort document is **world-readable**.
Removing literal secrets is not sufficient — aggressively **minimize and
anonymize** every attached example, log, trace, or reference so it stays
useful to a stranger without exposing a private system:

- **Logs and stack traces.** Never paste a raw log. Reduce it to the smallest
  excerpt that demonstrates the defect, then replace every internal path,
  hostname, username, account, and IP with a neutral placeholder
  (`/path/to/repo`, `HOST`, `user`, `example.com`, `192.0.2.10`). Drop lines
  that carry no diagnostic value — they only add surface area to leak.
- **Reproductions.** Reduce to the minimal, generic steps that reproduce on a
  bare public checkout — never "run it inside `<private system>` with
  `<private config>`." Strip any dependency on private setup; keep only what
  an unaffiliated contributor needs.
- **Example and sample data.** Always synthetic — never a real record ID,
  token, private URL, or topic/queue name. Use `example.com` and obviously
  fake values (`user-123`, not a real account).
- **References.** Only public anchors — an issue/PR/commit in this repo or
  another public repository. Never an internal tracker ID, private doc link,
  session ID, task ID, or downstream machine/worktree name. Keep the private
  anchor attached to the driver's own private effort/plan, which links
  *to* the public issue — never the reverse.
- **Rationale.** State the generalized "what" and "why it's useful upstream."
  The organization-specific "why we need it" belongs in the private
  elaboration, not here.

When in doubt, write the artifact as if you were an unaffiliated open-source
contributor with no access to any private system — because to every other
reader, you are.

The repository root `CONTRIBUTING.md`, `AGENTS.md`, and `TESTING.md` remain
authoritative for current mechanics and gates.
