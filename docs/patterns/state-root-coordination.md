# Pattern: state-root-bound coordination

**Serves:** *Vision agent-fabric* resource claims, resource leasing, resource
accountability, and claimed-resource-not-reclaimed.
**Exemplars:** agent-worktrees claims/run/worktree creation and Git-ref leases;
agent-codespaces with agent-bridge CodeSpace dispatch.

## Problem

A shareable harness may require a separately bound private state repository.
Creating a claim, lease, child worktree, or provider resource before that
binding is usable assigns ownership to a transient or shared launch-repository
identity. Rejecting only the final ledger write is too late because the resource
may already exist.

## Standard approach

**Resolve the owner's coordination identity before side effects.** A producer
uses the project named by the qualified owner reference, not its own process
cwd. It evaluates the versioned coordination-readiness contract before ledger
mutation, reservation, subprocess launch, source fetch, Git-ref access, Git
worktree creation, or provider work.

The version-1 result has stable top-level fields:

| Field | Meaning |
|-------|---------|
| `version` | Contract version; currently `1`. |
| `ready` | Whether new shared ownership may begin. |
| `code` | `ready`, `knowledge_binding_required`, or `state_root_resolution_failed`. |
| `state_root` | Bounded identity metadata: path, source, repo, external requirement, and resolution state. |
| `error` | Actionable remediation when `ready` is false. |

The CLI exits `0` for `ready` and `3` for either compatible rejection. An
unready response is valid only when `error` is a non-empty string. Optional
consumers must validate all three signals — version, payload, and exit code —
before treating a response as authoritative.
An embedding producer (`agent-worktrees lease acquire`) carries the same
versioned rejection body on exit `5`; agent-codespaces recognizes that value as
a race-time rejection after its earlier preflight.

`knowledge_binding_required` means an external state root is required but no
knowledge repository is bound. `state_root_resolution_failed` means a binding
or owner project exists conceptually but its checkout/config cannot be resolved.
Self-hosted projects remain backward compatible: state-root unavailability does
not add a new claim-ledger gate when no external root is required.

**Gate acquisition, not teardown.** Operations that create ownership fail
closed before their first side effect. Read-only inspection and release,
settlement, renewal, cancellation, and other teardown of existing ownership
remain available. An existing lease is maintained through the original
explicit/carried store origin even if the current binding later becomes
temporarily unresolvable.

**Overrides identify; they do not bypass.** For a required external state root,
a lease-origin argument or environment value is accepted for new acquisition
only when it identifies the bound state repository. The bound checkout still
supplies the repository/account authentication context.

**Optional peers degrade by compatible contract.** A provider calls the owning
plugin's CLI over a process boundary. Missing commands, absent peers, malformed
JSON, unversioned responses, unknown versions, and incompatible codes behave as
an absent optional integration. Only an explicit rejection from a compatible
contract blocks provider work. This preserves à-la-carte installation while
preventing a known-unready owner from creating an unaccounted resource.
Within the provider chain, exit `78` carries that compatible rejection across
agent-codespaces to agent-bridge so Session-Host dispatch is bounced before its
transport is established.

## Producer checklist

1. Resolve the qualified owner project.
2. Query readiness before every mutation or external operation.
3. Return the structured code and remediation unchanged.
4. Prove rejection leaves bytes, refs, subprocesses, and provider calls untouched.
5. Keep owner-less bootstrap and existing-ownership teardown available.
6. For an optional peer, validate the response version before honoring rejection.

## Rationale

The resolved state root is not merely a file destination; for shared claims it
is the operator-specific identity under which accountability survives sessions,
machines, and provider boundaries. Preflighting at the producer is the only
point that can guarantee no unowned resource exists. Keeping the contract
versioned and optional preserves standalone plugins without converting the
coordination layer into a mandatory central service.

## See also

- Intent: [`visions/agent-fabric/`](../../visions/agent-fabric/README.md)
- Composition: [`a-la-carte-independence.md`](a-la-carte-independence.md)
- Reality: [`architecture.md`](../architecture.md#coordination-readiness--identity-before-ownership)
- Accountability: [`architecture.md`](../architecture.md#resource-obligations--accountability)
