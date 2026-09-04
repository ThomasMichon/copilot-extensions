---
name: setting-up-machines
description: >
  Set up or verify a development machine by deriving the applicable plan from
  declared machine topology, enabled Copilot plugins, and agent-machines
  requirement packages, then composing the setup skills owned by those
  capabilities. Use for new-machine bootstrap, onboarding an existing machine,
  repairing incomplete setup, or checking setup parity. Desired-state changes
  route to agent-machines-setup; applying already-declared state routes to
  restore-machinestate.
  Trigger phrases include:
  - 'set up this machine'
  - 'bootstrap a development machine'
  - 'onboard an existing machine'
  - 'verify machine setup'
  - 'repair incomplete machine setup'
  - 'make this machine match the others'
---

# Setting Up Machines

This skill is a **portable conductor**, not a master installation runbook. It
derives what the target machine needs, invokes the capability owner for each
applicable phase, and verifies the resulting composition. Repository,
organization, and operator overlays supply policy and values; this skill does
not invent them.

## Route Before Acting

Classify the request first:

| Intent | Owner |
|--------|-------|
| Add, remove, or change desired configuration across machines | `agent-machines-setup` |
| Inspect or apply desired state already declared in requirement packages | `restore-machinestate` |
| Bootstrap or verify the complete applicable machine composition | This skill |
| Repair one known subsystem | That subsystem's setup or troubleshooting skill |
| Create, delete, start, stop, or repair a hosted machine resource | The provider-owned lifecycle agent or skill |

Do not turn a desired-state authoring request into a sequence of local shell
edits. Update the owning requirement package first, then converge it.

## Derive the Setup Specification

Build the plan from declared and live sources in this order:

1. **Resolve the target identity.**
   Use the active repository's machine topology when it declares one. Resolve
   the canonical machine key through the topology's supported command or
   runtime; do not guess from hostname substrings. Read declared environment,
   role, capabilities, and connection environments.

2. **Inspect declared machine state.**
   Invoke the exact agent-machines command from the session command catalog:

   ```text
   <agent-machines catalog argv[0]> doctor
   <agent-machines catalog argv[0]> discover
   <agent-machines catalog argv[0]> plan
   <agent-machines catalog argv[0]> validate
   ```

   The discovered packages, surfaces, resources, and modules are the desired
   state specification. A missing requirement is an authoring gap for
   `agent-machines-setup`, not permission to improvise it locally.

3. **Inspect enabled capabilities.**
   Read effective Copilot plugin configuration through the host's settings or
   plugin-management surface. An enabled plugin may contribute a runtime,
   configuration, service, skill, agent, hook, or extension. Do not infer an
   obligation merely because a plugin happens to be installed but disabled.

4. **Read capability-owned configuration.**
   Use each enabled capability's documented config and status commands. Preserve
   its precedence rules and never duplicate its values into a new setup file.
   Treat credentials and authentication caches as opaque prerequisites.

5. **Inspect live readiness.**
   Prefer published `doctor`, `status`, `validate`, or readiness commands over
   checking implementation files. A successful installer exit is evidence, not
   the full postcondition.

## Compose Capability Owners

Load only the skills that apply to the derived plan:

| Signal | Owning skill |
|--------|--------------|
| Core worktree, bridge, Codespaces, container, or MCP runtimes need installation/adoption | `agent-worktrees:copilot-extensions-setup` |
| Inbound or outbound SSH is declared | `agent-ssh:setting-up-ssh-host` or `agent-ssh:setting-up-ssh-client` |
| SSH transport is present but unhealthy | The applicable `agent-ssh` troubleshooting skill |
| Codespaces are part of the machine's control surface | `agent-codespaces:codespaces-setup` |
| A local container fleet is declared | `agent-containers:containers-fleet` |
| Session synchronization is enabled or configured | `agent-logger:session-sync-setup` |
| WSL is an intended environment | `wsl-setup:setting-up-wsl` |
| Context handoff needs user-level installation | `context-handoff:context-handoff-setup` |
| Secret storage is configured | `agent-vault:agent-vault-setup` |

Provider-specific lifecycle, organization policy, and operator preferences are
downstream overlays. Invoke those owners when their declared config proves they
apply; never copy their procedures into this skill.

## Execute in Dependency Order

1. **Satisfy the irreducible seed.**
   A repository may publish a bootstrap entry point for prerequisites needed
   before its plugin runtimes or private configuration can be read. Use that
   entry point as documented. Keep the seed minimal and idempotent.

2. **Apply environment policy before dependency installation.**
   If an organization or repository supplies package-source, signing, network,
   or device-governance guidance, load it before running package managers.

3. **Install and adopt runtime capabilities.**
   Delegate to the applicable owner skills. Use published command-catalog
   entries and unified update flows rather than scanning installed payloads or
   running internal installers piecemeal.

4. **Converge declared state.**
   Re-run validation, preview restore, preserve confirmation gates, then apply:

   ```text
   <agent-machines catalog argv[0]> restore
   <agent-machines catalog argv[0]> restore --apply
   ```

   Scope with `--only` only when the operator requested a bounded subsystem or
   the remaining plan is intentionally staged.

5. **Configure interactive or provider-owned capabilities.**
   Complete authentication, hosted-resource lifecycle, SSH, synchronization, or
   other phases through their owners after the underlying runtime exists.

## Human Gates

Stop for explicit operator action or confirmation when required by the owning
system, especially for:

- interactive browser, WAM, device, or SSO authentication;
- elevation or privileged system tasks;
- restart or reboot;
- billable resource creation;
- destructive lifecycle actions;
- security-control, firewall, identity, or credential changes.

Do not reinterpret a broad setup request as blanket approval for these actions.

## Capability-Derived Verification

Verify only what the derived plan says should exist:

1. `agent-machines validate` succeeds.
2. A final restore preview reports no unexpected drift.
3. Every selected capability's published readiness command succeeds.
4. Declared services are healthy and use their configured lifecycle.
5. Declared connection environments are reachable through their owning
   transport, when the target is expected to be online.
6. Effective plugin configuration contains the required enabled capabilities.

Report unavailable optional capabilities separately from failed required ones.
Do not maintain a static universal checklist: the topology, enabled plugins,
requirement packages, and capability-owned config are the checklist.

## Overlay Contract

Downstream overlays may:

- select defaults and preferences using existing configuration surfaces;
- add organization policy that applies before a shared phase;
- bind hosted-resource providers and machine topology;
- publish an irreducible bootstrap for private or authenticated repositories;
- add verification for downstream-only capabilities.

They must not copy shared procedures, hardcode another operator's topology, or
replace a capability's native configuration with prose. Prefer a configuration
value or requirement package over an overlay skill rule whenever the owning
system can express it.
