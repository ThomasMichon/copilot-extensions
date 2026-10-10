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

### Installed SDK boundary (CLI 1.0.88)

Inspection of the installed extension documentation and exported SDK declarations
confirms the following contract, independently of runtime source:

| Published surface | What it establishes | What it does not establish |
|-------------------|---------------------|----------------------------|
| `joinSession()` and `CopilotSession.capabilities` | Extension attachment; advertised UI capabilities | Native context admission or effective context-management policy |
| `session.rpc.tools.getCurrentMetadata()` | Currently initialized tool metadata; nullable before initialization | Built-in versus external override provenance, or native transition viability |
| `ToolInvocation.availableTools` | Offered metadata accompanying an actual extension invocation | Additional provenance beyond `CurrentToolMetadata` |
| `user.settings.get()` | User values/defaults, explicitly excluding repository/enterprise overrides | Session-effective admission or an explicit policy denial |
| Root `session.context_cleared` event | Confirmed conversation clear and optional initial message | Native terminal-tool execution, checkpoint durability, or permission for another clear |

`CurrentToolMetadata` exports name, optional namespaced/MCP names, description,
input schema, and deferral. None identifies an external override of a built-in
tool. The presence of generated `getBuiltinDescriptors` declarations does not
make an internal-only method callable by an extension.

This is declaration/documentation evidence, not an isolated live CLI/ACP rollover
proof. No live session was cleared, tool list initialized, settings changed, or
private RPC invoked to obtain it. No supported admission/provenance seam has
been established for this installed version; automatic native pressure backoff
remains blocked rather than inferred from names or user settings.

### Required host observation (conceptual, not a proposed RPC name)

The smallest missing contract is a read-only observation bound to the current
session and root/subagent owner, reporting effective admission (including explicit
denial versus default/unknown), implementation support, and the currently offered
native tools after filters/overrides. Native provider provenance must be
host-authored, not metadata supplied by the tool being inspected. Include an
initialization state and revision/invalidation mechanism so policy changes,
tool replacement, owner changes, and reconnects cannot leave suppression active
on stale evidence. A one-time create/resume capability bit alone is insufficient.

The observation need not expose arbitrary settings or initialize services.
Native viability/checkpoint revision errors remain execution-time results; an
admission observation cannot promise a successful transition.

### Selection precedence

This is the acceptance matrix for a future supported observation, not current
plugin behavior. Apply rows top-down; evaluate effective settings, never a
user-only default as an explicit opt-out.

| Current evidence | Selected guidance | Automatic pressure behavior |
|------------------|-------------------|-----------------------------|
| Explicit effective denial, including conflicting advertised tools | Respect denial; diagnose conflict | Never auto-cut over to evade policy; explicit recovery remains policy-governed |
| Effective enablement plus current admitted native implementation/tool set | Native-first checkpoint/terminal rollover | Suppress competing custom soft/hard/force actions while evidence stays current |
| No explicit denial; current admitted native implementation/tool set | Native-first, even without a setting signal | Same native backoff |
| Effective enablement but implementation/availability unknown | Conditional native-first; report missing observation | No claimed native backoff; no native invocation based on the setting alone |
| Confirmed unsupported implementation, without policy denial | Explain unsupported venue; custom recovery | Existing custom mode/consent semantics |
| Absent, uninitialized, failed, stale, or conflicting observations | Explain uncertainty; bounded re-evaluation | Do not silently disable all recovery or treat uncertainty as confirmed native support |
| Admitted native path with checkpoint/static-load/cap failure | Repair or diagnose the precise native failure | No blind alternate-session policy bypass or double clear |

Re-evaluate on supported invalidations and before committing an automatic
pressure action. Bound retries and report failures once per observation revision;
do not poll arbitrary private APIs. Runtime emergency compaction remains intact.

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
