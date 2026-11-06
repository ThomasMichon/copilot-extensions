# Copilot Driver implementation map

Parent and completion gate: [main effort](README.md).
This is the reviewed-before-code implementation proposal, not runtime support.

## Extend the existing owners

| Current source seam | Refactor responsibility |
|---------------------|-------------------------|
| `plugins/agent-bridge/src/agent_bridge/session_host/` | Move the real Host/protocol/local lifecycle into the core. Keep Bridge's venue transport, relay and coordination adapters outside it. The spike is not the production implementation. |
| `session_host/host.py`, `launcher.py`, `private_attachment.py`, `private_commit.py`, `acp_boundary.py` | Preserve frame/ack identity, custody, bounded bootstrap, private acquisition and native process containment rather than rewriting the proven Host. |
| `session_host/bundle.py`, `receiver_policy.py`, portable receiving support | Use the new core's selected receiving closure, canonical repository-policy reader, resources and notices. No target-suite command becomes a bootstrap requirement. |
| `worktree-manager/bin/launch-session.{ps1,sh,cmd}`, `pane-launch.ps1`, `relocated_launch.py` | Move Copilot mux/pane execution to the core; Manager retains UI/window selection and calls the core. |
| `agent_worktrees/sessions.py`, `mux_creation.py`, pane/seed composition | Worktrees resolves workspace/agency and delegates execution. Preserve nesting cleanup, create/reuse gates, seed ownership and receipt semantics. |
| `agent-remote-driver` extension/control/registry modules | Preserve standalone use and mutation fencing; add launch-bound broker integration without copying another SDK driver. |
| `tools/runtime_application_project.py` and existing artifact/installer inventory | Declare the independent app and its reusable receiving export through these owners, not a bespoke installer or service. |

## App and receiving package

Agent recommendation: retain the `agent-remote-driver` namespace for this
refactor, with an independently buildable Python core alongside the existing
JavaScript extension. Reuse the repository's wheel/application-project model
for ordinary installation and its compact receiving-archive mechanism for
hoisting the same canonical code and assets. The plugin remains a connector;
the app must run without plugin enablement.

The core must not import Bridge, Worktrees or Manager to launch or survive.
Small client/compatibility surfaces may compose the app, but its process must
run from the selected core environment/artifact, not a caller-owned venv or
checkout that disappears on caller update. Retain the necessary app generation
and extension/pane assets until their execution leg retires.

Use the existing first-party dependency/resource closure and admission rules.
Do not introduce a second resolver, mutable source install, third-party vendor
copy, whole-suite receiver or global startup daemon. Copilot and mux/native
prerequisites remain explicit, with their existing setup owners. Measure the
actual cold/repeat delivery-to-ready flow, not only archive creation.

## One broker, two backends

Headless mode preserves direct ownership of Copilot and its native ACP
connection. Headed mode owns the canonical mux execution and receives a
launch-bound connection from its selected extension. Both reach one Session
Host broker; SDK adaptation is a backend edge, not another conversation.

Preserve the existing TCP/frame contract for supported retained participants
while expanding the core's ACP-compatible HTTP/TCP support provider-first.
Expose actual support from the executing app and running broker, separately
from package versions. New consumers select support before launch effects.
Strict unknown-argument and required-policy refusal remain.

The result relates a reserved endpoint, host/child or mux incarnation, native
session and current controller. A listener, PID or mux alone is not native
binding, input readiness, prompt acceptance or completed restoration. Credential
material stays private and out of argv, public descriptors and diagnostic output.
Reuse existing nonce/control and OS-user trust boundaries; no new universal
identity ceremony is needed.

The extension is supplied at process creation through the existing
`--plugin-dir` mechanism and scoped trust. Do not rewrite global settings,
broaden native tool permission, or assume warm join has creation-time
permission/input parity. Unsupported ACP/SDK interactions report honest
capability limits rather than silently accepting a weaker operation.

## Consumer convergence and compatibility

Workspace allocation, repository eligibility, staged prompts, original attempt
identity, preferences, auth/relay and durable agency remain with their current
owners. The core receives their selected execution request and owns its process
and broker result. It must not create another worktree, launch journal, task
queue or credential store.

Expand core providers and declare support before migrating callers. Keep
supported legacy imports/wire behavior through thin adapters where needed,
without retaining parallel launch implementations. Existing old hosts keep
their selected custody and recovery; this source refactor does not migrate or
terminate them live. Failure after possible admission never selects an old
launcher or replays a seed.

Migration is complete only after Manager launch-session, Worktrees
embody/copilot and Bridge create/resume, including their ordinary local and
remote paths, consume the same core. Native AHP/SDK/application peers remain
provider-honest; the refactor does not silently change mode defaults.

## Lifetime and activity evidence

Interactive execution stays alive until Copilot is closed by the user or an
explicitly authorized owner retires it. Detach, a quiet foreground, caller loss
and caller cleanup Jobs do not supply automatic CLI expiry.

ACP can stop at caller request or after its configured idle allowance only
with no pending background tasks, agents or schedules. Replace the old
disconnect-driven interpretation for new core sessions; retain supported old
host contracts explicitly rather than reinterpreting their records.

Use native structured idle/task/schedule observations or a qualified
runtime-owned snapshot. Establish initial state as well as future changes;
a reconnect with missing events cannot invent a known-empty registry.
Coalesce observation off SDK callbacks and revalidate before expiry. Work
arrival cancels the grace decision. Unsupported or incomplete activity evidence
retains the session and reports that automatic expiry is unavailable.
Textual tool-result heuristics alone are not complete pending-work evidence.
This is distinct from an explicitly requested maintenance broadcast followed
by subsequent idle; no extra cleanup-audit gate is added to that operation.

## Evidence before each functional promotion

Retain focused tests that fail without the extraction or adapter behavior.
Use real consumer seams, not tests that merely assert a new flag or import.

1. **Headless core:** build and relocate the selected artifact, launch an owned
   mock ACP child through the actual CLI/broker, exercise init/new/load/prompt/
   cancellation/reconnect and exact child retirement, then prove Bridge
   create/resume consume that core with preserved policy and original custody.
2. **Headed core:** actual mux/pane transport and native SDK extension loading,
   one literal multiline prompt accepted once, stable native identity on
   reuse/resume, shared broker access and independent terminal attachment.
3. **Caller matrix:** Manager attached/detached/new-window, Worktrees
   embody/copilot fresh/resume/reuse, Bridge headless/headed create/resume;
   preserve no-mux and unavailable/unsupported behavior explicitly.
4. **Lifetime:** CLI stays live through idle/disconnect; ACP stop and idle grace
   with pending shell/agent/schedule controls, activity races and unknown
   snapshots. Close every fixture-owned caller cleanup Job in durability
   controls and retain a restrictive shared-outer negative control.
5. **Distribution/skew:** no guest coordination suite or source checkout,
   complete wheel/resources/notices, ordinary install and repeat/update/
   rollback, selected older peers and retained-host compatibility, no
   bootstrap resurrection or installed-state mutation during stage-only work.

Unit CI remains positively admitted and bounded. Real-process, clean-room,
Windows/native and available POSIX/external evidence remain distinct lanes;
mock ACP or fake SDK success is not native Copilot acceptance. Use admitted
test leases/containment, exact cleanup and headless spawning. Never use a
production prompt, retained worker, live shared mux or installed service as
an experimental fixture.
