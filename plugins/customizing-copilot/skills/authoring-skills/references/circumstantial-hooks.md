# Circumstantial Hook Contracts

Portable authoring and review contract for command hooks. Read this with
[authoring-skills](../SKILL.md) and
[reviewing-customizations](../../reviewing-customizations/SKILL.md).
This defines design obligations, not a new runtime, manifest or cache schema.

## Roles and the three-stage path

Instruction-injection hooks are **circumstance-based skills**: the last line of
defense for problem-specific, just-in-time guidance. Upfront instructions are
general and reactive skill selection is unreliable. Do not replace occurrence
detection and contextual delivery with startup-only guidance or a blanket
once-per-session acknowledgement. A rule already known can meet a new circumstance.
Hooks also enforce explicit policy and perform lifecycle work; identify the role
instead of treating every hook error or reminder as an authorization decision.

Keep three stages distinct:

1. **Acquire event facts.** Identify authoritative inputs, provenance, work cost,
   completeness and freshness. A small serialized result can conceal a costly
   CLI activation, Git/history/tree scan, config merge or repeated hashing.
2. **Evaluate the trigger at event time.** Use cheap essential checks first,
   against this event and current authority. No trigger means no intervention,
   not a reason to run optional comprehensive discovery.
3. **Intervene contextually.** Deliver the smallest actionable rule/procedure
   for this occurrence, make an explicit policy decision, or use the existing
   lifecycle owner. These effects have different failure and retry semantics.

## Five fact/execution exposure mechanisms

Choose the existing mechanism appropriate to each fact; do not build a parallel
snapshot framework, resolver, daemon or background poller just for hooks.

| Mechanism | Appropriate exposure and limits |
|-----------|---------------------------------|
| Original event payload | This occurrence's tool arguments, target, result and session facts actually supplied by the host. Validate shape and size; process cwd is not automatically target cwd. |
| Source/install-bound launch descriptor | Attributable selected installation, interpreter, entry point and stable launch configuration supplied by their existing owner. Record source provenance; use existing supported runtime selection and recovery behavior. |
| Canonical input-bound projection | Reusable computed discovery from the existing authority. Persist it with its complete dependency/input binding and completeness/freshness contract, or precompute it at sessionStart when stable. A TTL alone is not input closure. |
| Live controls, OS and current authority | Facts that can change after startup: ownership, permission, cancellation, operational state and policy controls. Read/revalidate through their owner at the decision boundary; a cached description or acknowledgement is not current authority. |
| Existing owner mutation APIs | Writes, registrations, lifecycle transitions and cleanup use their actual owner's supported operation. A hook is a caller, not a second state authority or a reason to reproduce that owner's implementation. |

Memoize reusable facts, **not final allow/deny decisions or dynamic occurrences**.
Document which changes invalidate reuse: inputs/configuration, target/cwd,
installation/source generation, policy version, relevant ownership and other
actual dependencies. A bounded display/top-N session summary is not a complete
fact set. Preserve a valid complete EMPTY result. Unreadable, malformed,
incomplete, mismatched or stale data is not EMPTY and must not manufacture a
new allow decision. The explicit unavailable-evaluation fail-open default below
is distinct from asserting that evidence authorized the action. Existing runtime
fallback and recovery selection remain unchanged. Reuse must retain required
guards, not weaken them.

## Required behavioral and data contract

Record this alongside each hook's implementation or owner documentation; no
suite-wide JSON declaration is required. Review the actual path and its tests,
not the presence of these words in a document.

| Contract | Required evidence |
|----------|-------------------|
| Purpose and scope | Circumstantial guidance, explicit policy, lifecycle work or a documented combination; applicable event/host/source/target and owning component. |
| Facts and cost | Each fact source, exposure mechanism, work/cardinality/byte bounds, freshness, dependency closure and acquisition cost, including hidden subprocess/transitive work. |
| First call and acknowledgement | Whether guidance-only continuation, first-call bounce or no intervention is intended; exact acknowledgement scope, storage owner, invalidation and bounded retry behavior. |
| Decision and error outcomes | Explicit violation versus unavailable dependency, optional/advisory failure versus required policy failure, operation effects and agent-readable recovery. |
| Time and lifetime | Host/outer deadline, inner work deadline, cancellation propagation, response and owned-descendant cleanup reserve, first-attempt and total retry cost. |
| Transition and verification | One active implementation per event, rollback/activation owner and whole-path policy, delivery, error, lifecycle, cost and cleanup parity. |

### First-call delivery, acknowledgements and retries

An advisory hook need not block or force a bounce. Use the supported delivery
field for the actual event and host. A justified first-call bounce can supply
`additionalContext` **where proven supported**, or another supported agent-readable
field with an explicit next action/retry instruction. Do not promise that
`preToolUse` `additionalContext` reaches the model on every host; emitting a field,
stderr, or a successful process exit does not prove delivery or composition.
See [hook output composition](hook-output-composition.md).

Define acknowledgement scope exactly (for example rule revision, occurrence or
target identity and relevant input generation), when it expires, and what it
suppresses. Acknowledgement means guidance was acknowledged, **not permission**,
proof the condition is resolved, or a universal guard bypass. A scoped disable
acknowledgement never disables unrelated rules or future circumstances. Recheck
the condition on retry; known rule plus changed circumstance needs evaluation
and, where appropriate, renewed contextual guidance.

Prevent prompt/retry loops: bound attempts, define what progress makes a retry
useful, and report a terminal actionable outcome when unchanged. Deduplicate
repeated diagnostics only within their documented occurrence/freshness scope;
do not permanently suppress an unknown dependency or future failure. No generic
once-per-session acknowledgement can replace event-time trigger evaluation.

### Explicit violations and unavailable dependencies

**Default to fail open when the backing suite is uninstalled, missing or broken
and ordinary hook evaluation cannot run.** Inspection, recovery and repair must
remain usable; every tool call must not depend on suite infrastructure merely
to get past the hook. Missing optional advisory guidance is also nonblocking.
Report degraded evaluation without claiming the rule was satisfied.

A fail-closed exception requires all of: an explicitly documented protected
condition or installation prerequisite, positive applicability to the intended
action from available trusted inputs, a bounded actionable error and a reachable
recovery path. Select only the protected action, not every tool. Suite/root
absence or a failed validator is not evidence that the exception applies. The
outer entry path must retain this default without calling the missing helper to
decide whether to fail open. A generic exception, nonzero exit, timeout or cache
miss must not accidentally become blanket denial.

A confirmed policy violation from available authoritative inputs remains a
violation, not an unavailable evaluator. Preserve that explicit policy outcome
while keeping unrelated recovery operations reachable. The backing-absence
default does not fabricate successful
lifecycle mutation or license replay of an unknown outcome.

Agent-readable errors identify all four of:

- **Owner and failed boundary:** which hook/component could not do what.
- **Effect:** operation not executed, known completed, or mutation outcome unknown.
- **Next verified action:** a concrete supported owner inspection/remediation,
  not an invented command or a blanket reinstall/restart/disable recommendation.
- **Retry safety:** whether retry is safe, safe only after a stated change, or
  forbidden until the existing owner reconciles unknown effects.

Use a verified event/host-visible response channel; stderr alone is diagnostic
logging, not guaranteed model-facing remediation. Preserve the existing valid
response shape, output composition and independent hooks. Diagnose the failed
boundary through existing supported recovery. Global disable, reinstall or
restart is never the default. Lifecycle mutation with an unknown outcome is not
replayed merely because a response was lost or the hook timed out.

### Deadlines, bounded work and cleanup

Budget the **whole path**, including interpreter startup, discovery, IPC,
fallback, serialization/response and owned-descendant teardown. Watchdog limits
are not performance targets. Reserve response and cleanup margin below the host
deadline; give inner work a smaller deadline and propagate cancellation. A host
killing the outer hook is not proof its children were reaped. Use existing
ownership-aware process/lifecycle helpers, retain unknown cleanup outcomes and
never terminate unrelated processes.

Bound input/output bytes, object/roster counts, subprocesses and retry counts.
State first-attempt plus total bounded retry cost, including repeated fact
acquisition, rather than reporting only a warm cache hit. Essential cheap
checks precede expensive optional work. Move optional expensive work outside
the hot path through an existing owning lifecycle, not a new daemon or detached
per-event helper. Precompute only facts stable under their declared invalidation;
current controls and the occurrence still need event-time evaluation.

## Review and validation

For changed behavior, exercise the relevant no-trigger, first-call, acknowledged,
changed-circumstance, retry and explicit-violation paths; missing/unreadable facts,
complete EMPTY, stale/incomplete projections, cancellation/timeouts, unknown
mutation effects and cleanup where applicable. A future consumer recovery fixture
must cover backing-suite absence, continued inspection/repair, and a declared
required-installation exception affecting only its intended protected action.
This governance change alone does not implement that consumer behavior.
Verify diagnostic visibility on
the claimed host, not merely stdout bytes. Measure composed first-attempt/retry
cost and policy/error/lifecycle/cleanup parity before replacing an implementation.
Keep one active implementation per event during any later transition.

The customization scanner checks only native command timeout declarations:
explicit `timeoutSec` must be a finite positive number, excluding booleans. Invalid
owned values block **offline review**; external findings are advisory. Omission
warns and preserves the legacy host default. Compatibility matcher groups and
compatibility `timeout` fields are not subject to this native check.
There is no universal ceiling or
new runtime allow/deny policy. Static checks do not prove inner deadline margin,
actual delivery, fact validity, semantic policy, performance or process cleanup;
those require the contract-specific tests and review above.
