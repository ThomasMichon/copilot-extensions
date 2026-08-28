# Test Portfolio Triage Framework

Back to the
[`Test Portfolio Rationalization` effort](README.md).

This framework turns "is this test useful?" into a reviewable evidence question.
It is intentionally family-based, contract-first, and safety-gated. A numeric
score organizes evidence; it never deletes a test automatically.

## Baseline

A provisional static scan indicates well over 8,000 source-level test functions
across more than 500 files, before pytest parameter expansion. It is sufficient
to establish scale, but it is not authoritative: a reliable census must
understand module-, class-, and function-level markers, generated cases, and
family grouping.

Phase 2 creates the reproducible inventory command and records its invocation,
schema version, commit, and output. No disposition decision cites the
provisional estimate.

## Unit of triage: the test family

A **test family** is the smallest coherent group that shares:

- one behavioral contract or regression;
- one fixture and execution tier;
- one assertion shape;
- one side-effect profile.

Parameter values remain individual cases for diagnosis, but they receive one
portfolio decision unless a value represents a distinct platform, boundary, or
failure mode. This prevents a large parameter table from receiving artificial
weight merely because it expands into many node IDs.

Split a family when cases protect genuinely different contracts. Merge families
when their only distinction is setup duplication or an implementation detail.

## Execution cost/fidelity tiers

| Tier | Name | Allowed shape | Default status |
|------|------|---------------|----------------|
| T0 | Guard/static | schema, generated-file, manifest, AST, and repository invariants without importing runtime code | routine default |
| T1 | Hermetic unit | in-process logic with memory or temporary-filesystem collaborators; no child process or network | routine default |
| T2 | Contained component | subprocess, shell, executable-resolution, IPC, or service-boundary behavior inside runner-owned limits | default only after containment |
| T3 | Clean room | install, bootstrap, provision, and assembled behavior on a disposable fresh system | explicit scenario |
| T4 | End to end | real external provider or operator-supplied target | opt-in only |

A higher tier is justified only by a failure mode that a lower tier cannot
faithfully falsify. The same contract may appear at multiple tiers when each
detects a distinct integration boundary.

These execution tiers are separate from clean-room **Tier P** (programmatic)
and **Tier E** (agent-evaluated), which describe how a clean-room scenario is
judged. T3 can contain either clean-room tier.

## Effect declarations

Every family declares zero or more effects:

| Effect | Meaning |
|--------|---------|
| `filesystem` | creates, modifies, links, locks, or deletes files |
| `process` | launches or signals child processes |
| `network` | opens sockets or reaches a network endpoint |
| `service` | starts, stops, installs, or queries a durable service |
| `host-state` | changes registry, scheduler, shell profile, package state, or other machine configuration |
| `external-system` | reaches a provider, account, repository host, or caller-supplied target |

T0 and T1 reject every effect except temporary-filesystem use explicitly owned
by the test. T2 requires runner containment and denies undeclared effects. T3
and T4 require explicit invocation and venue/target declarations.

## Safety gate

No broad suite measurement begins until the runner provides:

1. **Process ownership:** a Windows Job Object or equivalent POSIX process
   group/cgroup owns every descendant and terminates it when the runner exits.
   Under test containment, the shared spawn helper suppresses deliberate
   production breakaway flags so descendants cannot opt out of runner
   ownership; adversarial tests prove that attempted breakaway remains reaped.
2. **Bounded execution:** wall-clock, descendant-count, memory, and temporary
   storage limits fail closed with diagnostics.
3. **Executable hygiene:** fixture executables cannot accidentally resolve
   themselves as the "real" command; delegation targets are explicit and
   recursion depth is bounded.
4. **Effect enforcement:** declared tiers and effects are checked at collection
   and runtime.
5. **Interruption cleanup:** Ctrl+C, test failure, timeout, and runner crash
   converge on the same cleanup path.
6. **State-root isolation:** user home, XDG, Copilot, installed-plugin, and
   plugin-runtime state resolve under runner-owned temporary roots for every
   default-tier test. Resolving a real host root fails closed.
7. **Primitive reuse:** containment extends the repository's shared process
   helpers and process-group adapters rather than introducing a parallel reaper.
8. **Isolated proof:** adversarial containment tests run in a disposable venue
   before the protected runner is trusted on a developer host.

## Scorecard

Score the family with cited evidence. Unknown values remain unknown; they are
not silently treated as zero.

**Unknown defaults to Keep, flagged for evidence.** Absence of provenance,
mutation data, or timing history is never positive evidence for deletion.

### Assurance value

| Factor | Scale | Evidence |
|--------|------:|----------|
| Contract criticality | 0–4 | vision behavior, design invariant, compatibility promise, data-loss/security/process boundary |
| Unique contract coverage | 0–4 | contract-map comparison showing behavior not protected elsewhere |
| Defect-detection evidence | 0–4 | historical regression, mutation killed, fault injection, or demonstrated failing implementation |
| Fidelity and observability | 0–3 | test exercises the right boundary and fails with an attributable signal |
| Platform/compatibility value | 0–2 | distinct operating-system, shell, version, or provider behavior |

### Portfolio burden

| Factor | Scale | Evidence |
|--------|------:|----------|
| Runtime/setup cost | 0–4 | contained repeated timing, environment construction, cache sensitivity |
| Redundancy | 0–4 | overlapping coverage vectors, fixtures, assertions, and failure modes |
| Side-effect hazard | 0–5 | declared effects, escape potential, cleanup complexity, host collision risk |
| Reliability/maintenance cost | 0–4 | flake history, brittle snapshots, implementation coupling, review churn |

Do not reduce the two sections to one automatic cutoff. A high-value,
high-burden family normally moves tier or is rewritten; it is not deleted
because the arithmetic is inconvenient.

## Evidence sources

Use the cheapest safe evidence first:

1. Static AST and fixture analysis.
2. Contract and invariant mapping from visions, patterns, architecture, and
   regression history.
3. Contained collection to enumerate node IDs and parameter expansion.
4. Repeated contained timings, peak process count, peak memory, and cleanup
   observations.
5. Focused coverage vectors to find families that exercise the same code and
   branches.
6. Focused mutation or fault-injection sampling on critical modules.
7. Historical failures, reverted defects, and bug-fix provenance.
8. Flake and maintenance history from repeated or CI runs where available. In
   the absence of CI history, record at least three contained repetitions or
   leave reliability unknown.

Coverage overlap is a lead, not proof of redundancy. Two tests that cover the
same lines may protect different contracts or failure boundaries.

## Dispositions

| Disposition | Use when |
|-------------|----------|
| **Keep** | unique assurance is clear and the tier/cost is proportionate |
| **Consolidate** | several cases protect the same contract and can become one table, fixture, or stronger assertion |
| **Rewrite** | the contract matters but the test is brittle, opaque, implementation-coupled, or weaker than a feasible alternative |
| **Move tier** | the test has unique value but its effects or cost do not belong in the current execution path |
| **Delete** | no unique contract, regression, platform boundary, or falsification value remains after stronger coverage is identified |

## Removal gate

A family may be deleted only when its review record contains:

- the contract or regression it claimed to protect;
- the retained family or other evidence that subsumes it, or a reason the
  claimed behavior is not a repository contract;
- a critical-module list and before/after focused mutation or fault-injection
  evidence established within the same plugin wave;
- confirmation that platform and negative/boundary cases remain represented;
- the runtime, reliability, or hazard improvement gained.

Deleting an obsolete contract requires a separate product/vision decision. Test
triage cannot silently redefine behavior by removing its only assertion.

## Per-wave outputs

Each plugin wave produces:

- a contract map;
- a machine-readable family inventory;
- a disposition ledger with evidence;
- before/after test cases, runtime, peak processes, peak memory, and reliability;
- focused mutation/fault-injection results for critical modules;
- updated testing documentation and budget.

The portfolio is complete when every test-bearing plugin has these outputs and
the repository can enforce the same declarations for new contributions.
