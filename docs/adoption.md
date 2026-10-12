# Adoption and Deployment Choices

Installing a plugin makes a capability available. **Adopting** it integrates
that capability with a repo, profile, or chosen state home. The toolkit does not
select these identities and policies on your behalf.

Read [installation](installation.md) first, then choose capabilities from
[the selection map](capabilities.md). Exact commands and write scopes stay
with the component owner.

## A practical adoption sequence

1. **Choose a local task.** A customization skill can be useful without
   worktree management. If you need isolated git sessions, use the
   [standalone worktrees guide](../plugins/agent-worktrees/docs/getting-started.md).
2. **Decide repo ownership and policy.** Preserve the repo's contribution,
   branch, review, and state rules. A registry entry does not authorize edits
   or make the repo a special operator harness.
3. **Adopt explicitly.** Follow the selected plugin's `register`, `adopt`, or
   setup skill. Installation/update owns machine-local deployment;
   adoption write scope is [plugin-specific](patterns/install-vs-adopt-boundary.md).
4. **Verify locally.** Check the selected command/service and one harmless
   operation. Verify worktree finalization against the repo's actual PR policy,
   not a hypothetical example's policy.
5. **Grow deliberately.** Add bridge profiles, task supervision, remote venues,
   log sinks, or knowledge binding only when their owners and costs are clear.

For settings and state ownership, use [configuration](configuration.md).
For an opinionated agent-driven assembly/audit procedure, use the
[harness runbook](harness-runbook.md).

## Hypothetical deployment examples

**These are examples, not shipped bundles, tested deployment certifications,
or mandatory architecture.** Names below are generic placeholders. Resolve
actual paths, accounts, and venues from your own configuration.

### Example: one local project

A hypothetical developer works on `sample-app` using customization skills and
optionally agent-worktrees for parallel changes. The product repo can also hold
its harness instructions and plugin selection. No remote machine, bridge,
task service, or private knowledge repo is required for that local slice.

Validation is correspondingly small: loaded skills, a registered project when
worktrees are selected, and one worktree session completed through that repo's
chosen contribution policy.

### Example: portable control repo with private state

A hypothetical team keeps generic instructions and plugin selection in
`control-kit`, with operator-specific notes and activity in a separately chosen
`notes-store`. The optional harness-knowledge binding skill establishes that
relationship; these names are not defaults.

The control repo may enable efforts and visions, but their adopted state
destination follows its declared policy. Verify the selected writable state
home before writing personal material. Do not commit credentials, private
hostnames, machine paths, or real operator activity into the portable repo.

### Example: control repo with an execution venue

A hypothetical maintainer adds agent-bridge and **one** venue provider:
Codespaces for GitHub-hosted execution, or agent-containers for a local Docker
fleet. agent-ssh may supply machine connectivity where needed. This is not a
promise of identical provisioning or authentication on every host.

Before dispatch, verify provider access, transport, resource ownership/leases,
credential policy, and budget. Test a harmless round trip against the actual
selected venue. The selected provider's reference—not this example—defines
how it provisions, authenticates, and cleans up resources.

If durable queued tasks are needed, adopt agent-dispatch explicitly. Installing
a live communication service does not itself create a queue or worker loop.

## Decisions the adopter retains

- Product/repository layout and whether a harness is separate or co-located.
- Forge, tracker, and review policy, within each plugin's actual support.
- Platforms, machines, and execution venues.
- Credentials, account mapping, publication audience, and private-state policy.
- Optional services, schedules, log/index sources and sinks, and resource costs.

Neither visions nor these examples fill an unimplemented provider or workflow.
Use [architecture](architecture.md) to understand current composition and the
[reference index](reference-index.md) to locate exact contracts.
