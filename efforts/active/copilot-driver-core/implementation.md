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
JavaScript extension. The Python implementation is the canonical
`libs/copilot-driver-core` distribution, consumed by Bridge and the Driver
application connector through ordinary first-party library references. This
preserves the existing materializer and build owner's library attribution,
rather than introducing sibling-plugin source dependencies.
Reuse the repository's wheel/application-project model
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
while expanding the core's ACP-compatible WebSocket/TCP support provider-first.
Use the existing public `acp.v1` WebSocket JSON-RPC convention. HTTP/SSE alone
is not ACP interoperability; do not introduce a parallel envelope protocol.
The ordinary locked application carries the WS dependency and its metadata;
the compact dependency-free receiving archive remains TCP-only and advertises
that limitation. Neither artifact may claim the other's transport support.
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

### Inventory-capable SDK backend proposal

This refinement closes the vision's `mode-appropriate-session-lifetime` and
`idle-expiration-preserves-pending-work` requirements without changing mode
defaults. It remains a proposal until reviewed; inventory qualification is not
production adoption or automatic-retirement qualification.

The native controls in #11994, #12005 and #12008 establish that the public
Python SDK can list session-scoped client tasks, agents, shells and future
schedules. A root-idle event and an empty task list can coexist with a future
schedule. Native ACP currently does not expose that inventory: a missing method
is unavailable evidence, never an empty set. These are distinct provider
capabilities, not a reason to send SDK RPC methods over the ACP wire.

**Recommended boundary.** Preserve direct native ACP as the default headless
backend. Add an explicitly selected, optional public-SDK headless provider at
the existing core backend edge; it owns one native process and one selected
session behind the same Session Host. The SDK owns its native protocol, while
the adapter translates the supported public ACP operations and native events.
It must not implement an internal SDK wire client, copy generated SDK code,
create a parallel broker or join a second conversation merely for inventory.
Prove new/load/prompt/cancel, restoration and reconnect semantics before
migrating a caller. Unknown acceptance cannot trigger another backend or seed
replay.

The existing headed `createHeadedAdapter` remains the canonical adapter for a
launch-joined extension. Its current text-input, control-loss and native-input
readiness semantics are not interchangeable with headless new/load semantics.
Share backend-independent activity interpretation and Host lifecycle policy,
not an invented cross-language SDK transport. Python SDK qualification does
not prove that a joined JavaScript extension exposes the same inventory or
observer-drain operations; headed support needs its own native controls.
Coordinate changes in the receiver-owned auth seams through their existing
owner rather than folding them into this provider work.

**Distribution.** Keep the base core and native-ACP path usable on Python
3.10. The measured Python SDK requires 3.11 or newer; carry it only in an
explicitly selected optional application dependency closure using the existing
locked build/install owner and dependency-age rules. Selection on an
ineligible interpreter or artifact refuses before process/session creation;
there is no automatic interpreter download, global install or silent floor
bump. Declare support from the executing artifact and running backend, not
from an installed SDK version. The dependency-free compact receiver stays
TCP-only and native-ACP-only unless a separately reviewed receiving closure
qualifies SDK delivery. Ordinary base-only installation and supported rollback
must remain usable without this optional dependency. A Node SDK provider is
an alternative only after equivalent public-API and delivery qualification,
not an assumed substitute for the measured Python path.

**Observation and retirement.** Represent foreground state, session-scoped
tasks and future schedules separately, each with explicit known/unknown state.
Install observation before the initial inventory and reconcile concurrent
changes without treating a locally synthesized counter as native completeness.
Coalesce callbacks into bounded work; no synchronous SDK calls, per-event
processes or unbounded polling in event handlers. Terminal task retention is
not pending work, but idle-yet-live agents and future schedules are.
Inventory loss, session mismatch, overflow, reconnect gaps and SDK failure
invalidate the retirement decision while preserving execution and custody.

Inventory availability and automatic idle retirement are separate capabilities.
Keep `automaticIdleRetirement: unavailable` until the provider qualifies
complete initial/ongoing evidence and the final admission/retirement race,
including internally scheduled work. Two empty lists or freezing only Driver
inputs cannot establish an atomic native idle boundary. If the public native
contract cannot supply a safe boundary, retain unavailable status rather than
substituting timed polling, tool-output parsing or `wait_for_pending`.
That SDK wait covers agent/shell follow-ups, not client tasks or future
schedules.

Explicit caller stop remains distinct from idle expiry and observer retirement.
A public SDK `disconnect`/`stop` return is not proof that an old joined
observer drained or that every native descendant settled. Preserve the
independently owned observer-close/drain and whole-session-settlement gates
before auth recovery can be enabled; this proposal does not lift those holds.
CLI foreground lifetime remains independent of all ACP idle policy.

**Promotion order and validation.** First review this boundary, then add a
provider-first inventory adapter with truthful capabilities and automatic
retirement still unavailable. Qualify through the real broker and selected
artifact: native session identity, initial nonempty client/agent/shell/schedule
inventory, future-schedule retention after root idle, isolation from a second
session, cancellation/completion/removal, callback races and unknown outcomes.
Exercise missing optional dependency/interpreter, base-only 3.10 installation,
compact-receiver refusal and supported older consumer/provider rollback.
Keep process/native/clean-room lanes explicit and bounded, not in required
unit-only CI. Only a later qualified lifecycle slice can enable idle grace;
neither this plan nor an inventory-only merge completes the parent's lifetime,
caller-convergence, receiving or observer-retirement gates.

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
