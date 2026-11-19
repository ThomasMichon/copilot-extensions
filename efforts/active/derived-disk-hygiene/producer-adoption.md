# Owned producer adoption

Parent and completion authority: [Derived Disk Hygiene](README.md).
Coordination: #10968; the existing test pilot is [scratch-pilot.md](scratch-pilot.md).

## Goal and scope

Extend the existing allocation and process-settlement contract to selected
repository task producers. A completed invocation must leave either a verified
consumed allocation or a discoverable, bounded disposition. An uncertain child
process result must leave the allocation and its responsibility open.

This is a proposed source continuation. Review and merge this plan before
implementation. It does not complete the parent effort, enable a default,
install a runtime or authorize cleanup of existing data.

## Selected consumers

| Producer | Selected real consumer contract |
|---|---|
| Test | Preserve the opt-in `run-plugin-tests.py --owned-scratch-owner` flow and its existing regression packet. |
| Spike | Execute a bounded, one-off analysis command in claimed scratch and consume its result through standard output. |
| Build | Execute a build-and-check command with its temporary intermediates inside the allocation. Explicit durable build output keeps its existing caller/publication owner. |
| Archive extraction | Extract an explicitly selected input into the allocation, validate and consume its contents before disposition. Preserve the extractor's input/path validation. |
| Report | Produce and consume a transient report inside the allocation. Durable reports use an independently admitted destination; terminal output is not proof of user delivery. |
| Source worker | Create and consume a transient qualification image inside the allocation. Ongoing or editable source work remains in a registered source worktree. |

These are explicit, selected integrations, not universal interception of tool
writes. The implementation and completion record must name the actual commands
and revisions exercised for each row. A synthetic file labelled "build" is not
qualification of the build consumer.

## Architecture

Use the existing Worktrees artifact owner and outbound ledger. Reuse its
capability negotiation, configuration/root admission, identity binding,
interruption behavior, retained disposition and independent cleanup. Do not
add a second allocation inventory or accept caller-supplied ledger paths.

Add one opt-in repository command for bounded disposable producers, provisionally
`tools/run-owned-producer.py`. It runs a literal argument vector, not a shell
expression, through the existing process containment owner. A command receives
its selected allocation as its working directory and through an explicit
scratch argument/environment contract. Child temporary roots stay inside that
allocation. Inherited installation, session and credential state follows the
existing test containment policy rather than reaching the operator's services.

Factor only the shared allocation/disposition portion of the test pilot as
needed. Keep its old entry point and ordinary runner behavior compatible.
The new consumer must not change the test pilot's interpretation of success,
timeout, failure, teardown or unknown descendant cleanup.

Only the integrated containment boundary may settle work after descendants and
cleanup workers join. Do not expose a `work_settled` assertion flag or treat a
zero parent exit, released lock, process scan or elapsed time as settlement.
Missing Worktrees support or coordination authority refuses before child
execution; no bootstrap or unclaimed-scratch fallback is permitted.

Successful disposable output is consumed before exact removal. Failed known
cleanup keeps only the configured bounded producer-owned diagnostic summary,
not arbitrary subprocess output, extracted inputs or recursive log guesses.
Unknown process cleanup retains the complete allocation and open obligation.
Partial physical cleanup remains an explicit retained or unresolved result.

The wrapper owns only its disposable allocation. It does not make arbitrary
external output paths safe, transfer a user file, evict a reusable cache or
preserve execution history. Explicit external outputs need their existing
owner and verified disposition. History and durable results must not be put
in the allocation merely to make finalization pass.

## Contract and compatibility

- Invocation is opt-in. Existing commands without it retain their current
  behavior. Required unit CI must not start a live Worktrees runtime.
- Negotiate the actual loaded artifact capabilities, not a package version.
  An unavailable owner, malformed policy or unsafe root fails visibly.
- Preserve literal command arguments and exit results; define scratch
  substitution explicitly and reject malformed or ambiguous invocation.
- Commands must use foreground child work under the containment owner.
  No detached services, OS tasks, daemon escape or host-state override is added.
- Use no-window Windows creation and the existing POSIX process boundary.
  Preserve identity-qualified cleanup and containment resource limits.
- Finalization accepts a valid retained disposition. A later cleanup is
  separate and revalidates the exact allocation; no automatic sweep is added.

## Vision and pattern reconciliation

This closes the artifact ownership and guided-reclamation intent in the parent
effort and the
[checkout-retirement boundary](../../../visions/plugins/agent-worktrees/worktree-data/README.md#checkout-retirement-is-not-data-reclamation).
It does not change those lifetimes or the domain-owned history contract.
The existing artifact and process owners remain the authorities; this is a
consumer expansion, not a new storage service. Honor independent plugin
availability and the existing service-contract evolution and Windows launch
patterns. No runtime installer or dependency change is proposed.

## Implementation and validation

- [ ] Preserve the test pilot through a focused shared-context regression packet.
- [ ] Implement the opt-in literal-command consumer with capability/root refusal,
      bounded diagnostics, normalized exit handling and joined cleanup.
- [ ] Prove the selected spike, build, extraction, report and qualification-image
      flows through real process entry points; record their exact commands.
- [ ] Add focused unit coverage for argument handling and effect ordering.
      Keep actual process, filesystem and native qualification in the explicit
      integration portfolio, not required unit CI.
- [ ] Prove native Windows and available POSIX success, command failure, timeout,
      unknown cleanup, partial removal and unsupported-owner cases. Include
      paths with spaces and the applicable link/permission/path-length negatives.
- [ ] Run a first-touch fixture with no prior allocation, then a refusal case
      with an incompatible owner. Do not repair or adopt foreign resources.
- [ ] Verify ledger disposition and exact remaining bytes after each native
      result. Preserve a reusable-cache sentinel and unrelated sibling identity.
- [ ] Update the canonical artifact/test documentation and entry points; run
      focused tests, source guards and the required publication checks.
- [ ] Obtain current-head review and required CI, merge the implementation,
      then publish the bounded completion record. Report release and installed
      adoption separately; preserve every remaining parent item and owner.

## Boundaries

No automatic enabling, installed rollout, real history rescue, shared-cache
retirement, unknown-file deletion, new service, parallel claims registry or
whole-machine scan is included. This source plan does not supersede other
Derived Disk Hygiene tracks or assign their remaining work.
