# Native-aware continuity design

Parent plan: [README.md](README.md).

**Status:** proposed implementation contract.

## Selection is not execution

The operator requests native-compatible behavior when context management is
enabled in effective settings **or otherwise detected**. Preserve both signals:

| Evidence | Behavior to design and prove |
|----------|-----------------------------|
| Effective enabled setting | Native-oriented guidance; verify usable tool/host before invocation |
| Native capability detected with no explicit opt-out | Native-oriented guidance and compatible pressure backoff |
| Explicit policy/setting denial | Respect denial; detect/report conflicting stale evidence |
| Missing implementation or unsupported host | Mode-governed custom fallback with a clear reason |
| Unknown/stale observation | Bounded re-evaluation; no silent suppression of all recovery |
| Native transition not viable | Repair checkpoint/static load or report configured limit; fallback only when authorized |

Exact precedence and supported query APIs are Phase 1 decisions, not invented
flags in this design. Do not use "ACP" as an automatic fallback classifier.

## Detection acceptance boundary

Before operational suppression, require a supported, read-only session-scoped
signal for effective native admission and currently offered native tools. A
user-settings snapshot that excludes repository/managed overrides is not an
effective-session policy snapshot. Likewise, matching tool names or schemas
without provider/override provenance is not proof of native implementation.

Uninitialized metadata is unknown, not unavailable. A descriptor-building API
may perform initialization; do not call it merely to observe capability. An
in-process-only API is not an extension contract even if generated declarations
exist. Do not use private SDK members, raw internal RPC, or guessed flags to
bridge those gaps.

If the installed host does not expose sufficient admission/provenance, preserve
conditional native-first agent guidance and explicit recovery, but do not claim
operational backoff has been implemented or proved. Record the missing host
contract and resolve it before enabling automatic suppression.

Observe a host-confirmed root `session.context_cleared` event for window
bookkeeping where supported. A cwd-change event is not a model-window rollover.
Ignore subagent clears for root pressure state; do not parse checkpoint-looking
prompt text as proof of a native transition.

## Native checkpoint semantics

Ambient system/developer instructions teach native-first continuity. Targeted
skills/templates teach compact checkpoint generation. Carry the original
objective, parent completion gate, remaining roster, current slice, established
decisions/evidence, unresolved instructions, external obligations, and one next
action. Keep canonical effort state in its versioned home and checkpoint only
the recovery pointers/delta needed by the next context.

Do not replace native checkpoint ownership, expected-revision enforcement,
snapshot binding, or terminal-tool ordering. The initial recovery prompt may
instruct a bound artifact read rather than embed the whole checkpoint; preserve
that distinction in guidance and tests.

## Operational backoff

Guidance changes alone cannot stop an existing force-tier automatic trigger.
Reconcile pressure signals, tool gates, forced save/trigger, and confirmed
context-clear events. A context-only transition keeps the session owner; it is
not missing-successor evidence or permission for a process host to cut over.

Do not globally set mode off or remove explicit handoff. Actual session/owner
transfer, unavailable native tools, and process/machine recovery are different
problems from refreshing a model window.

## Failure handling

Missing/stale checkpoint can be repaired in place. A configured cap or policy
denial must not be bypassed through an automatic alternate session. A clear can
succeed in memory while persistence reports failure: inspect the actual window
before retrying or falling back. Preserve diagnostics and continuity evidence.

Keep emergency compaction and runtime budget controls; do not rely on arbitrary
timer calls to a context-clear API that requires tool-in-flight/continuation
ordering. Prefer native agent/runtime scheduling initially.

## Non-goals

- Workspace creation/preparation or disposal hooks.
- Cleanup finalization or worktree claim-transfer redesign.
- Removing the plugin or explicit handoff.
- Automatic subagent opt-in, policy bypass, or unrequested live session reset.
- A custom second checkpoint store or cross-agent adoption of private artifacts.
