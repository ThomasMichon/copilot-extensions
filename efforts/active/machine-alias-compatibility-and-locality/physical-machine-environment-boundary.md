# Physical Machine and Environment Boundary - Phase 3d

Parent: [Machine Alias Compatibility and Locality](README.md).
Tracking: [#5674](https://github.com/ThomasMichon/copilot-extensions/issues/5674);
retains [#6945](https://github.com/ThomasMichon/copilot-extensions/issues/6945)
installation/entry-path ownership.

## Governing model

The operator's clarification is captured in the parent Request. One physical
machine has a configured name, an independently observed OS hostname and declared
execution environments:

```yaml
machines:
  example-host:
    hostname: Example-Host
    environments:
      - windows
      - wsl
```

This illustrates the identity dimensions, not a replacement registry schema.
`example-host` remains the physical machine name in both environments.
`example-host-wsl` may be a compact execution address where an interface accepts
only one machine/environment identifier. It is not another physical machine.
An SSH alias, default distro and actual distribution name are separate inputs.

This closes the existing
[host-qualified guest identity vision](../../../visions/agent-fabric/README.md#host-qualified-guest-identity).
It does not reverse the requirement to distinguish native and guest execution,
or replace the existing shared map and installation identity authority.

## Observed mismatch

Current `config.detect_machine` deliberately returns an execution name. Runtime
update passes that value to the same literal `runtime_allowed` predicate used
by launch reconciliation. A physical-machine allowlist containing `example-host`
therefore denies an actual guest represented as `example-host-wsl`. Copying that
compact value into an adopter's physical-machine policy would mask the consumer
error and make the model less coherent.

Machine guidance also labels an execution entry's key as its machine name.
Prospective worktree naming receives machine and environment as separate inputs,
but the machine input can already contain the environment. Modern naming
suppresses a repeated suffix; allocation replay deliberately retains historical
spelling. Correctness must derive from the structured identity, not infer the
physical machine by trimming arbitrary strings.

A reported post-update Manager resolution failure currently has no retained
reproducer. One successful legacy preview and unchanged roster totals do not
prove all records resolve, and do not establish that this mismatch caused the
transient failure.

## Boundary inventory

| Consumer | Required representation |
|---|---|
| Physical deployment policy and machine-facing guidance | Canonical physical machine name; hostname and environment remain separate |
| Structured creation metadata and new worktree naming | Physical machine plus explicit environment; include the environment once |
| A channel, installation identity or qualified ref with one execution-ID slot | Existing authoritative compact/registered execution ID |
| Historical worktree IDs, paths and refs | Preserve exact bytes and resolve through existing compatibility/provenance rules |
| Locality and mutation authority | Exact receiving execution context, never physical-host equality alone |

The inventory is an **agent-recommended implementation decomposition**. Audit
ambiguous `machine` call sites before changing their semantics; a globally renamed
helper must not silently change an external identifier contract.

## Implementation sequence

The sequence below is **agent-recommended** and requires this proposal's review.

1. Reuse `MachineEnvironment`, `MachineSelection` and declared physical-host
   relationships in `machine_transport`. Resolve canonical physical name,
   environment and selected execution key from the same evidence. Exact keys,
   custom guest names, ambiguity and foreign-space refusals remain authoritative.
   Do not add another registry or a heuristic suffix-removal resolver.
2. Make the physical-name versus execution-ID API boundary explicit. Preserve
   compatible one-ID callers and identify tuple-facing consumers rather than
   globally changing every `Config.machine` or `detect_machine` result blindly.
3. Wire runtime update and launch reconciliation through the same structured
   deployment decision. A declared physical host remains that host in Windows
   and WSL; installer platform and execution authority remain separate. Existing
   universal, payload-only and missing-manifest behavior stays unchanged.
4. Render machine/hostname/environment and any execution identifier as distinct
   facts. Use physical name plus environment for prospective worktree metadata
   and naming. Do not append an environment to an already-composed value and
   treat a suffix match as proof of identity.
5. Keep historical allocations and records readable, including repeated
   qualifiers and replayed reservations. Do not rename IDs/paths, rebind owners,
   rewrite refs, prune records or change live sessions as an automatic repair.
6. Diagnose any reproducible Manager failure from exact request, source,
   record and resolver evidence, separately from cached roster freshness. Stop
   at an explicit data-repair or rollout approval gate when evidence requires it.
7. Land through normal Source review/required CI, qualify via the unit-only
   release path, then perform only authorized installed updates and acceptance.
   No integration suite becomes a promotion prerequisite.

## Validation Plan

- [ ] The same configured physical machine is reported in Windows and WSL;
  environment and compact execution ID differ explicitly and OS hostname is
  independent metadata.
- [ ] Physical deployment selectors admit the already-declared host in either
  permitted environment without adding duplicate physical machines. An omitted
  or foreign physical host still refuses; update and launch decisions agree.
- [ ] Native and guest locality/mutation fences stay distinct. A physical-host
  match does not authorize opening or modifying a sibling's records.
- [ ] Exact custom execution keys and configured transport aliases remain
  valid. Ambiguous registry names, conflicting environments and guest/native
  collisions refuse instead of falling back to hostname or suffix guesses.
- [ ] Newly generated IDs include the selected environment once for the
  declared physical name. A physical name that itself ends in a platform-like
  suffix is not silently truncated.
- [ ] Existing physical/environment tuples, compact legacy labels, repeated
  historical ID prefixes and admitted allocation replay resolve without
  changing their stored bytes.
- [ ] Bounded in-process unit fixtures cover the decision rules; actual Git,
  installed runtime, cross-process and native acceptance stay outside promotion.
- [ ] Installed Windows and WSL creation, lookup and resume work across all
  registered projects; current Manager sources and rows agree with the direct
  resolver. Removed paths and any unavailable or unreproduced case are reported
  separately, not counted as passing proof.
- [ ] Queue, session, head, title, flags, paths, claims and owner/ref bytes are
  preserved through the authorized rollout. Any necessary data migration is a
  separately reviewed, explicitly approved operation.
