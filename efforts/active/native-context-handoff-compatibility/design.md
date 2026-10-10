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

The top-down selection precedence below defines the acceptance ordering.
Supported query APIs remain an unresolved Phase 1 dependency, not invented
flags in this design. Do not use "ACP" as an automatic fallback classifier.

## Detection acceptance boundary

An explicit `contextManagementTools: true` in the applicable `settings.json`
configuration is the operator-selected native-first contract. Read the flag;
do not require a new host RPC or tool-provider provenance for this positive
opt-in. The plugin selects guidance and backs off its own automatic context-only
handoffs; it does not execute native tools or manufacture implementation support.
An absent flag is not enablement, and a higher-precedence explicit false must
override a lower-precedence true.

Distinguish this declared-setting contract from observed effective admission. A
user-settings snapshot that excludes repository/managed overrides is not an
effective-session policy snapshot. Matching tool names or schemas alone is not
proof of native implementation for a separate automatic capability-detection
path. Those limitations must not block the explicit settings-file opt-in.

Uninitialized metadata is unknown, not unavailable. A descriptor-building API
may perform initialization; do not call it merely to observe capability. An
in-process-only API is not an extension contract even if generated declarations
exist. Do not use private SDK members, raw internal RPC, or guessed flags to
bridge those gaps.

If a flag-enabled host does not offer the native tools, report the actual
availability failure and retain explicit custom recovery under its existing
mode/consent rules. Do not automatically invoke a nonexistent tool, clear context,
or switch sessions to evade a policy cap. Backoff from a declared setting is not
proof that the host admitted or completed a native transition.

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
private RPC invoked to obtain it. No supported effective-admission/provenance seam
has been established for this installed version. The later operator clarification
selects direct settings-file opt-in instead; it does not require such a seam.

### Optional future host observation (not an implementation prerequisite)

A separate capability-detection contract could use a read-only observation bound to the current
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

This is the acceptance matrix, not current plugin behavior. Apply rows top-down.
Declared settings-file selection and observed effective policy are distinct;
never label a user-only default as an explicit effective opt-out.

| Current evidence | Selected guidance | Automatic pressure behavior |
|------------------|-------------------|-----------------------------|
| Explicit effective denial, including conflicting advertised tools | Respect denial; diagnose conflict | Never auto-cut over to evade policy; explicit recovery remains policy-governed |
| Selected native path with checkpoint/static-load/cap failure | Repair or diagnose the precise native failure | No blind alternate-session policy bypass or double clear |
| Applicable explicit `contextManagementTools: true` setting | Native-first checkpoint/terminal rollover; agent checks tool availability | Suppress competing custom soft/hard/force actions; preserve explicit handoff |
| Effective enablement plus current admitted native implementation/tool set | Native-first checkpoint/terminal rollover | Suppress competing custom soft/hard/force actions while evidence stays current |
| No explicit denial; current admitted native implementation/tool set | Native-first, even without a setting signal | Same native backoff |
| Effective enablement but implementation/availability unknown | Conditional native-first; report missing observation | No claimed native backoff; no native invocation based on the setting alone |
| Confirmed unsupported implementation, without policy denial | Explain unsupported venue; custom recovery | Existing custom mode/consent semantics |
| Absent, uninitialized, failed, stale, or conflicting observations | Explain uncertainty; bounded re-evaluation | Do not silently disable all recovery or treat uncertainty as confirmed native support |

Re-evaluate on supported invalidations and before committing an automatic
pressure action. Bound retries and report failures once per observation revision;
do not poll arbitrary private APIs. Runtime emergency compaction remains intact.

### 2026-10-10 - Operator clarification

The operator selected reading `settings.json` for the public flag as the contract,
rather than adding a CAR host API. Implementation must resolve applicable
configuration layers and boolean precedence, handle invalid/unreadable input
explicitly, keep load-time I/O non-blocking, and distinguish declared enablement
from live tool/rollover proof. Lifecycle work stays a private proposal awaiting
review, with no product edits or upstream posting.

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
