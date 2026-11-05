# Copilot Driver - Vision

- **Subject:** The minimal, independently distributed Copilot execution app in
  the `agent-remote-driver` namespace: Session Host, mux wrapping and a common
  ACP-compatible broker for headless and headed sessions.
- **Scope:** leaf (a concrete provider within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-11-05
- **Reality docs:** [Remote Driver](../../../plugins/agent-remote-driver/README.md) ·
  [Bridge hosting](../../../plugins/agent-bridge/docs/architecture.md) ·
  [Worktrees mux support](../../../plugins/agent-worktrees/docs/mux.md)

## Purpose & Intent

A caller should be able to launch durable Copilot execution in a local or
remote location without installing a coordination suite there. The Copilot
Driver is the small execution substrate that makes this possible. It owns
Session Host and the Copilot mux wrapper rather than leaving those mechanics
inside Bridge, Worktrees or Worktree Manager.

The app supports ordinary host installation, but its primary distribution
objective is a compact, quickly installable redistributable. Bridge, Worktrees
or another authorized caller can carry that same selected package to a machine,
container or CodeSpace and obtain a usable session endpoint. Existing guest
coordination plugins and mutable global plugin settings are not prerequisites.

Headless and headed launches share one Session Host broker. Callers receive
the same ACP-compatible control boundary and attributable endpoint, process
and native-session information regardless of presentation.

## Concepts & Components

### Independently installed application and thin plugin

The core app is independently installable and usable without loading a Copilot
plugin. The plugin is its connection and guidance surface, not its installer
or lifetime prerequisite. Its selected redistributable supplies the receiving
support needed for the chosen mode without importing a caller's whole runtime.

### Session Host broker

Session Host owns the session's broker endpoint, reservation and execution
record. It mediates one authoritative Copilot conversation with compatible
callers while preserving native identity, input ordering and reconnectable
observation. Broker attachment is distinct from control ownership.

### Headless execution

For a purely headless launch, Session Host directly owns the Copilot process
and serves its ACP interaction through a reachable HTTP/TCP endpoint. The
target owns the child lifetime; a caller disconnect does not end its work.

### Headed execution

For a headed launch, the Driver owns a mux wrapping Copilot in interactive
mode, with the requested opening prompt and selected extension present and
trusted from launch. The extension connects to Session Host rather than making
the coordination service its required broker. The mux supplies terminal
presentation and detach/reattach; Session Host supplies the common ACP-facing
session boundary. These are parts of one execution provider, not rival hosts
or separate conversations.

### Reserved execution identity

The endpoint reservation, host and child process identities, native session,
presentation and current control relationship are discoverable by the caller.
Reservation and readiness are distinct: a reserved port or running process
does not yet establish a bound native session or accepted opening prompt.

### Clients and venue transports

Bridge owns coordination, Worktrees owns durable workspace and agency state,
and Manager owns presentation and installation choices. They consume the
Driver rather than carrying independent Session Host or mux implementations.
Venue providers prepare and reach the selected environment; they do not
duplicate the Driver or acquire its execution custody merely by transporting it.

## Features

### quickly-installable-redistributable

The selected package can be delivered and made ready promptly on an eligible
target without source builds, a repository checkout or a full coordination
suite. Repeated use reuses compatible receiving support; missing prerequisites
and unsupported platforms are explicit rather than disguised as readiness.

### standalone-and-hoisted-use

Normal installation and caller-hoisted execution use the same core app and
hosting semantics. Either Bridge or Worktrees can request the minimal target
support directly, including when the target has neither plugin installed.

### common-acp-broker-endpoint

Both launch modes expose Session Host through an ACP-compatible HTTP/TCP
boundary. Mode changes presentation and child integration, not the identity or
basic control model a caller must understand.

### launch-scoped-extension-delivery

A headed launch carries its selected extension from session creation without
rewriting guest-global registrations. Trust is scoped to that supplied
extension; it is not blanket native tool permission or repository exposure
approval.

### attributable-launch-result

The caller can obtain endpoint/port reservation, process and native-session
information, and distinguish reservation, process creation, broker readiness,
native session/process binding, input readiness, opening-prompt acceptance and
completed restoration. Uncertain launch or delivery
outcomes retain the original identities for reconciliation.

### durable-control-and-terminal-reconnection

The same conversation and execution remain reachable after a caller or
presentation disconnect. Headed sessions can be observed and reattached at
the terminal while programmatic control continues through the broker.

### canonical-caller-convergence

Manager's session launch, Worktrees' embodiment and Copilot entrypoints, and
Bridge's creation and resume flows all consume this core. They do not retain
parallel Session Host or Copilot mux implementations behind those operations.
Standalone and remote callers use the same execution authority.

### mode-appropriate-session-lifetime

Driver and Session Host processes remain alive for as long as their Copilot
execution needs them. Interactive CLI sessions remain live until closed by
the user; an idle foreground or detached terminal does not expire them.
Explicitly authorized owner retirement remains a distinct lifecycle action.

ACP execution can end when stopped by its caller, or after a configured idle
duration with no pending background tasks, agents or schedules. Ordinary
caller disconnect does not itself mean stopped, and foreground idle alone
cannot trigger this automatic retirement while background work remains.

### preserved-native-restoration

An explicitly selected stopped conversation can be restored without replacing
its native history or submitting its original seed again. Live incumbents and
pending handoffs remain subject to their existing custody.

## Behaviors

### one-core-owner-two-presentations

Session Host and mux wrapping belong to this app, not internal branches or
copied launch implementations in Bridge, Worktrees or Manager. Headed
extension integration and headless child control converge at the same broker.
Other native or third-party hosting providers remain independent peers.

### target-execution-outlives-the-caller

The target-owned execution survives loss of the installing/launching caller
and closure of that caller's cleanup boundary. A running process observed
before caller cleanup is not proof of durable launch.

### idle-expiration-preserves-pending-work

ACP idle expiration observes the hosted runtime's pending-work state, including
background tasks, agents and schedules. The idle allowance does not silently
cancel that work or invent an empty pending set from unavailable evidence.
Interactive CLI lifetime is not governed by the ACP idle-expiration policy.

### one-conversation-one-controller

Both modes preserve one ordered input stream and exclusive mutation custody,
with observation available independently. Human takeover, a driver change or
a frontend upgrade cannot silently fork the conversation or regain a revoked
control relationship.

### preserve-state-through-package-replacement

Replacing or removing a caller's coordination tooling does not retire the
Driver's live sessions. App replacement preserves durable records and
configuration, and treats incoming readiness and current selection separately
from the safe retirement of old session keepers.

### compatible-runtime-not-package-equality

Callers use support advertised by the selected app and running broker.
Extraction and rollout preserve supported upgrade/rollback paths and native
session custody; an incompatible requirement is refused before effects.

### prompt-and-permission-honesty

Initial prompts retain their exact content and acceptance identity. Uncertain
acceptance is never solved by automatic replay. Scoped extension trust and
broker access do not waive native permissions, repository hosting policy or
the authority required for consequential actions.

## Non-Goals / Boundaries

- **Not a coordination suite.** No task queue, machine topology, fleet
  scheduler, worktree store, PR authority or maintenance-window policy lives
  in the core.
- **Not a venue provisioner.** Creating machines, containers or CodeSpaces
  and obtaining remote access remain with their existing owners.
- **Not a universal host replacement.** SDK, AHP, application and third-party
  providers can compose or coexist under the provider-neutral hosting model.
- **Not a default-mode change.** Common broker ownership does not promote
  headed execution over headless execution or bypass the separate default
  validation decision.
- **Not a specification.** Wire schemas, package formats, command grammar,
  fixed ports and launch flags belong to implementation contracts.

## See Also

- Parent: [agent-fabric](../../agent-fabric/README.md)
- Hosting boundary: [session-hosting](../../session-hosting/README.md)
- Coordination client: [agent-bridge](../agent-bridge/README.md)
- Agency client: [agent-worktrees](../agent-worktrees/README.md)
- Presentation client: [installer](../../installer/README.md)
- Remote presentation: [remote-interactive-sessions](../../remote-interactive-sessions/README.md)
- Default-presentation question: [cli-default-bridging](../../cli-default-bridging/README.md)
- Distribution: [runtime assembly](../../plugin-services/runtime-assembly/README.md)
- Replacement: [authoritative runtime replacement](../../installer/README.md#authoritative-runtime-replacement)

## Provenance

- **2026-11-05** - Factored the shared Copilot execution substrate into an
  independently distributed app, with Session Host brokering both direct
  headless children and extension-connected interactive mux sessions.
