# Aggregate Resolution Contract

Back to the [agent-logger Aggregate Configuration effort](README.md).

This document defines the implementation contract for Phase 1. It is more
specific than the standing vision but remains independent of Python class names
and command-line parser structure.

## Goals

- Let several repositories publish agent-logger intent without granting one
  repository implicit precedence over another.
- Let a machine admit only repositories it trusts to participate.
- Select one deterministic policy per admitted repository.
- Normalize collection and rendering ownership separately.
- Reject ambiguous or overlapping ownership before side effects.
- Produce one stable, provenance-rich machine plan consumed by diagnostics and
  execution.
- Roll out through observation before enforcement.

## Configuration homes

### Repository declaration

The committed declaration lives at:

```text
<authoritative-checkout>/.copilot-extensions/agent-logger/config.yaml
```

It contains portable policy only: source claims, logical collection targets,
logical rendered-log sinks, relative repository paths, profiles, landing
intent, defaults, and machine selectors. It must not contain credentials or
assume a machine-specific absolute checkout path.

The existing `.agent-logger.yaml` remains the repository's log-presentation
surface. It is not promoted into an operational declaration and cannot gain
collection authority through backward compatibility.

### Machine admission

`$AGENT_LOGGER_HOME/config.yaml` owns a machine-local admission registry. Each
entry binds:

- a stable canonical repository identity;
- one authoritative checkout path;
- whether the repository is enabled for aggregate resolution;
- an optional repository-scoped override path;
- bindings from logical target or sink names to machine-local resources;
- an optional quarantine reason.

Admission is explicit. Merely cloning a repository or discovering a declaration
file does not authorize it.

The authoritative checkout must resolve to the admitted identity. Secondary
worktrees and other checkouts of the same identity are ignored and reported;
their unreviewed branch contents cannot alter machine policy.

The override path is explicit rather than inferred from a basename. This permits
homes such as `~/.<project>/agent-logger/config.yaml` while avoiding collisions
between repositories that share a directory name.

### Repository-scoped machine override

An override may specialize or disable only its admitted repository's
declaration. It cannot:

- alter another repository's admission;
- replace another repository's claims;
- create cross-repository precedence;
- change the canonical identity of its owner;
- bind a logical target owned by another admission entry.

Secrets and absolute machine paths belong in admission bindings or the
repository-scoped override, never in the committed declaration or resolved
diagnostic output.

## Stable identities

Repository identity is a normalized source-host identity, such as
`github.com/owner/repository`, derived from and checked against the authoritative
checkout's configured remote. A checkout basename is display metadata only.

Source claims use canonical repository identities. Sessions without a
resolvable repository identity form a separate `unclassified` population; they
are never silently treated as belonging to the current machine or repository.

Target identity is canonical within its kind:

- repository sinks use canonical repository identity plus a normalized
  repository-relative output root;
- filesystem corpus targets use their resolved machine binding and normalized
  destination;
- remote targets use a stable admitted endpoint identity, not a credential or
  display alias.

Two spellings that identify the same destination normalize to one target and
participate in collision checks as the same resource.

## Declaration model

A versioned declaration contains:

- one default policy;
- zero or more machine-specific policy overlays;
- named logical collection targets;
- named logical rendered-log sinks;
- one or more source claims.

Each claim has a stable declaration-local ID and a source set composed from:

- a finite set of canonical repository identities; or
- the wildcard set of all classified repositories;
- a finite set of excluded canonical repository identities;
- an explicit decision to include or exclude the `unclassified` population.

Ownership matching is exact against canonical identity. Substring matching is
not part of the aggregate claim grammar. This keeps overlap decidable and
prevents identities such as `example/tool` and `example/tool-harness` from
cross-capturing.

Each claim may independently name:

- one collection target; and
- one rendered-log sink.

Omitting one dimension makes no claim in that dimension. Collection and
rendering are analyzed separately, so a source population may have one
collection owner and a different rendering owner.

The machine-default fallback is represented as an ordinary claim over the
explicit `unclassified` population or a wildcard with exclusions. It receives
no special precedence.

## Machine selection

Machine selectors use exact values from a bounded identity document supplied by
the runtime, initially:

- machine name;
- platform;
- optional declared role.

A selector is a conjunction of its fields. An omitted field is unconstrained.
String patterns, regular expressions, environment-variable expansion, and
filesystem-derived matching are not allowed.

Resolution within one repository is:

1. Start with its committed default policy.
2. Find matching machine clauses.
3. Select the matching clause with the greatest number of constrained fields.
4. Reject the repository if more than one best match remains.
5. Apply its repository-scoped machine override.

No discovery order, YAML order, checkout timestamp, or cross-repository
priority participates in selection.

## Resolution algorithm

The compiler performs these steps without side effects:

1. Load machine identity and machine admission.
2. For every admission entry, verify canonical identity and authoritative
   checkout.
3. Report but ignore unadmitted declarations and secondary worktrees.
4. Load and schema-check each admitted committed declaration.
5. Apply same-repository machine selection and the repository-scoped override.
6. Resolve logical targets and sinks through that repository's admission
   bindings.
7. Normalize claims into collection and rendering ownership dimensions.
8. Sort all normalized records by canonical identity and declaration-local ID.
9. Detect source, destination, and policy conflicts.
10. Validate target and sink readiness without writing to them.
11. Emit the complete resolved plan and authorization decision.

The same admitted inputs and machine identity must produce byte-stable
canonical JSON regardless of cwd, process environment, discovery order,
checkout enumeration order, or operating system path spelling.

Ordinary `AGENT_LOGGER_*` compatibility overrides may continue to affect legacy
configuration during observation, but they cannot inject, suppress, or mutate
aggregate repository claims.

## Collision rules

Within each ownership dimension, two repositories collide when their normalized
source sets intersect. This includes:

- the same exact repository identity;
- a wildcard and an exact identity not excluded by the wildcard;
- two wildcards whose remaining sets can intersect;
- two claims over `unclassified`.

There is no cross-repository precedence and no "first match wins". An overlap is
valid only after declarations make the source sets disjoint.

Within one repository, overlapping claims are deduplicated only when their
normalized ownership, target, profile, and landing intent are identical.
Otherwise they are an internal ambiguity and invalidate that repository.

Two claims may also conflict when they resolve to the same canonical
destination with incompatible retention, profile, landing, or mutation policy,
even if their source sets are disjoint.

Every conflict records:

- both repository identities;
- both declaration and claim IDs;
- both source locations;
- the ownership dimension;
- the smallest useful overlap witness;
- the incompatible destination or policy fields, when applicable.

Any unresolved conflict makes the whole aggregate unauthorized.

## Schema compatibility and quarantine

An unreadable declaration, unsupported schema version, invalid override, or
identity mismatch is a repository-scoped error that makes the aggregate
unauthorized. It is not silently skipped.

Machine admission may explicitly disable or quarantine that repository to
restore an explainable plan while it is repaired. Quarantine removes the
repository's claims; it does not transfer them to another repository. If a
wildcard would then capture the abandoned population, that behavior must be
visible in the normalized plan and observe-only comparison before enforcement.

## Resolved plan

The machine-readable result is schema-versioned and contains:

- machine identity;
- execution mode: `observe` or `enforce`;
- every admission entry and verification result;
- every discovered declaration and provenance path;
- selected, shadowed, disabled, quarantined, and rejected clauses with reasons;
- applied override provenance;
- normalized collection claims;
- normalized rendering claims;
- canonical targets and sinks with readiness results;
- compatibility claims derived from legacy configuration;
- collision and validation findings;
- legacy-versus-aggregate behavior differences during observation;
- final `authorized` and `passive` decisions.

The output contains no credentials, bearer values, private key paths, or raw
secret-bearing target options. Sensitive values are represented by bounded
presence or readiness facts.

`agent-logger config --resolved --json`, `agent-logger doctor`, and
`agent-logger chronicle status` consume this same result.

## Authorization and side effects

`observe` mode never changes existing sync or chronicler execution. It computes
the aggregate plan, reports whether enforcement would authorize it, and
compares aggregate routing with legacy behavior.

`enforce` mode permits work only when the complete plan is authorized. Failure
occurs before:

- scheduling or dispatch;
- filesystem or network copying;
- destination pruning;
- reservation or journal mutation;
- manifest or rendered-log writes;
- git commits, pushes, or pull requests.

Force, recovery, and catch-up flags cannot bypass aggregate authorization.
Read-only status, configuration, and doctor commands remain available.

An empty enforced plan is successfully passive: it performs no collection or
rendering and explains that no admitted claim applies.

## Legacy transition

Legacy sync allowlists, denylists, chronicle routes, skip lists, and default
sinks are translated into explicit compatibility claims during observation.
The translation preserves their current behavior for comparison but labels
substring-derived or otherwise non-decidable ownership as legacy and
unenforceable until replaced with canonical claims.

Enforcement is an explicit machine-local choice. Removing that choice returns
the runtime to legacy execution without deleting declarations, admission,
overrides, or diagnostic evidence.

No installation or update command writes committed repository declarations.
Adoption and migration are explicit operations after repository intent has
landed through that repository's contribution flow.

## Ownership changes

Changing a population's owner affects newly due work after the new plan becomes
active. Existing landed logs remain in their recorded destinations, and
existing completed reservation or journal evidence is not reset implicitly.

Moving or re-rendering historical records requires a separate explicit
migration operation with its own idempotency and collision checks.

## Initial implementation boundary

The first implementation may support one explicit machine-local admission
provider and exact repository/wildcard source claims. The discovery seam and
resolved-plan schema must permit additional providers and selector fields
without changing the no-implicit-precedence, deterministic-resolution, and
fail-closed invariants above.
