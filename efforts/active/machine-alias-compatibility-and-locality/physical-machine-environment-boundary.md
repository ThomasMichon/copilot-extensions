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

## Original observed mismatch

`config.detect_machine` deliberately returns an execution name and remains a
one-ID API. At inception, runtime update passed that value to the same literal
`runtime_allowed` predicate used
by launch reconciliation. A physical-machine allowlist containing `example-host`
therefore denies an actual guest represented as `example-host-wsl`. Copying that
compact value into an adopter's physical-machine policy would mask the consumer
error and make the model less coherent.

Machine guidance also labeled an execution entry's key as its machine name.
Prospective worktree naming receives machine and environment as separate inputs,
but the machine input can already contain the environment. Modern naming
suppresses a repeated suffix; allocation replay deliberately retains historical
spelling. Correctness must derive from the structured identity, not infer the
physical machine by trimming arbitrary strings.

A reported post-update Manager resolution failure currently has no retained
reproducer. One successful legacy preview and unchanged roster totals do not
prove all records resolve, and do not establish that this mismatch caused the
transient failure.

The admission and guidance source repairs subsequently merged as #7321 and
#7334. Prospective creation semantics, qualified delivery and installed
acceptance remain open; these source merges do not prove the containing
rollout complete.

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

## Tuple-first new-record contract

The operator selected **tuple-first** for newly created durable records and
explicitly retained `<machine>-wsl` as a fast one-term reference. Those are
complementary requirements: the physical identity has separate dimensions,
while an execution address may compose them for interfaces accepting one term.
Existing records, IDs, paths and references are not migration targets.

### New-record representation

The following field names and realization are **agent-recommended** and require
review before implementation; the tuple-first role separation is the operator's
decision:

| Field or boundary | Required meaning |
|---|---|
| New record `machine` | Canonical physical machine from the existing shared selection |
| New record `environment` | Explicit selected execution environment, independent of hostname |
| New record `execution_machine` | Authoritative registered one-term execution-owner identity |
| Existing `platform` compatibility field | Retain its platform contract; reject conflicts with an explicitly stored environment |
| Qualified owner, pair, controller, claim and resource reference slots | Use the execution-owner identity where the slot accepts one term, never physical-host equality alone |

Use typed, explicitly versioned identity metadata and one shared accessor/codec
boundary for new and supported historical representations. Do not globally
change `Config.machine` or `detect_machine`, introduce another registry, or let
ordinary reads/saves silently upgrade a historical record's identity format.
Unknown or internally conflicting identity representations must remain explicit
errors or opaque read-only data, never become authority by a guessed default.

The implementation inventory must include authoritative creation producers,
paired records, serialization and JSON projections, ownership/locality,
qualified relationships, session/head registration and retained creation repair.
Audit consumers outside the record module before repurposing its `machine`
field. This contract does not authorize rewriting any existing relationship.

### Fast one-term addressing

Keep `<machine>-wsl` accepted wherever a one-term machine/environment reference
is appropriate. It must select the same declared guest as the physical machine
plus an explicit WSL environment, without requiring the longer form at every
call site. Exact custom execution keys and configured transport aliases remain
valid under the existing map; the shorthand is not an SSH alias or another
physical machine.

Resolve shorthand through declared shared relationships, preserving exact-key
precedence, ambiguity and conflicting-environment refusals. A suffix alone is
not proof of locality or ownership. A native/guest name collision must not
silently choose the other execution space.

### Prospective naming and retained allocations

Generate a new session ID from the selected physical machine and explicit
environment, including that environment once. A physical name that itself ends
in a platform-like suffix must remain intact; do not trim it as a cosmetic fix.
Worktree names are not transport or ownership authority.

Creation-key replay and interrupted-creation repair must preserve the exact
admitted ID, checkout, branch, creation nonce and relationship identity. Do not
reformat a reserved ID using the new naming rule. Historical tuple, compact-key
and repeated-qualifier records remain supported through existing compatibility
and provenance checks, without automatic relabeling or reference rewrites.

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

## Shared raw-JSON codec integration

The operator selected a shared dependency-free codec for the next consumer
boundary. The package name and sequence below are **agent-recommended
implementation details**, proposed for review before execution.

### Bounded implementation

- [x] Extract the existing pure decoder into `libs/worktree-identity`
  (distribution `agent-worktree-identity`, import `worktree_identity`) using
  ordinary first-party library packaging, with no runtime dependencies.
  Preserve the existing version-1 fields, supported canonical environments,
  exception behavior and absent-version legacy interpretation. Do not add a
  second machine map, environment inference or a broader platform contract.
- [x] Retain `agent_worktrees.worktree_machine_identity` as a compatibility
  import of the same implementation and types. Keep tracking serialization,
  identity fences and every already-merged Worktrees guard unchanged.
- [x] Declare the shared package through the repository's canonical local
  dependency, materialization and artifact-attribution mechanisms. Never
  require an installed sibling plugin or copy the decoder into each consumer.
  Preserve package sources, dependency seasoning and standalone installation.
- [ ] Adapt Dispatch's raw Worktrees row filtering and focus presentation,
  Bridge's venue-plugin owner checks, and Manager's raw-row source/action
  routing. Use the shared execution owner at one-term boundaries, retain
  physical/environment metadata separately, and refuse malformed explicit
  tuples before any ownership or routing effect.
  Dispatch's reader/focus boundary landed in #7484 and Bridge's controller
  boundary in #7502; Manager remains open.
- [ ] Keep task queues, sessions, relationships, transport profiles and
  historical rows untouched. This is tuple-wire adaptation, not a takeover of
  existing transport, task lifecycle, installer or Manager runtime work.
- [ ] Land the extraction and adapters in reviewable source slices, then
  continue the existing prospective-producer and retained-replay gates.
  Ordinary create, anchor and external-adoption producers remain legacy here.

### Required validation

- [x] Exercise the extracted package using only the standard library at import
  time, without Worktrees, machine-transport, configuration, I/O or subprocess
  discovery. Verify the compatibility module exports the same classes and
  functions, not a parallel implementation.
- [x] Preserve valid version-1 and legacy behavior; cover boolean/unknown
  versions, missing dimensions, conflicting platform/environment and invalid
  owner fields. A physical name shared by native and guest records cannot
  substitute for the execution owner.
- [ ] Test each actual consumer's raw JSON boundary with legacy, valid tuple
  and malformed tuple rows. Preserve full metadata without putting physical
  identity into one-term owner fields; filtering must not hide uncertainty.
  Dispatch's raw reader and actual focus CLI have this coverage in #7484.
  Bridge's controller and provider/transport refusal boundary is covered in #7502.
- [x] Verify real first-party package construction, materialization and
  installed import boundaries through existing bounded tools, without adding
  networked or native integration work to the unit-only promotion packet.
- [ ] Pass affected bounded suites, dependency/install-contract and module
  guards, documentation review, current-head review and required Source CI.
  Source proof does not satisfy qualified delivery or installed acceptance.

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
- [ ] New records store the physical tuple and separate execution-owner
  identity; serialization, JSON, paired creation and relationship consumers
  agree without changing the identity representation of existing records.
- [ ] `<machine>-wsl` and physical machine plus explicit WSL environment select
  the same declared guest across appropriate one-term creation, lookup and
  resume interfaces. Custom keys remain usable, conflicting contexts refuse,
  and native/guest mutation authority stays distinct.
- [ ] Admitted creation-key replay and interrupted-creation repair retain exact
  historical allocations under the new producer, including changed current
  registry spelling, or report a demonstrated identity conflict without
  allocating a replacement.
- [ ] Bounded in-process unit fixtures cover the decision rules; actual Git,
  installed runtime, cross-process and native acceptance stay outside promotion.
- [ ] Installed Windows and WSL creation, lookup and resume work across all
  registered projects; current Manager sources and rows agree with the direct
  resolver. Removed paths and any unavailable or unreproduced case are reported
  separately, not counted as passing proof.
- [ ] Queue, session, head, title, flags, paths, claims and owner/ref bytes are
  preserved through the authorized rollout. Any necessary data migration is a
  separately reviewed, explicitly approved operation.
