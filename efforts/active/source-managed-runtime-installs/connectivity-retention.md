# Critical Connectivity Retention - Source Audit and Contract

Parent: [Source-Managed Runtime Installs](README.md).
Coordination: #9048. Request: [both operator statements](inception-transcript.md#critical-connectivity-exemption).
Intent: [Disposable Supporting Runtimes](../../../visions/plugin-services/disposable-runtimes/README.md#connectivity-survives-supporting-suite-removal).

**Source audit and proposed retention contract, not installed acceptance.**
The code observations below use merged Dev
`649c58cce494f0703c86e580647d7c0ad3b37880`; the accompanying intent edits
change no runtime. No uninstall, service stop, live SSH probe, key/configuration
change or native survival experiment was performed.

## The corrected boundary

Machine-to-machine management connectivity is critical session substrate,
alongside Copilot, Mux and session-host wrappers. Supporting-suite
uninstall/reinstall, including force and purge, must spare its actual
host/client/proxy/tunnel/Gateway dependency closure. Both an established stream
and a fresh authenticated connection matter; one does not prove the other.

Provisioning is a different role. Positioning keys, emitting SSH profiles or
installing OpenSSH need not keep the provisioner on the request path.
Conversely, a helper shipped inside an agent runtime is not disposable if the
selected transport actually invokes it for connection, serving or restart.

**Credential relays are disposable.** Agent/Vault/Bridge credential or token
brokerage may disappear during removal. Native SSH keys, host pins,
authorization files and native authentication agents needed for management
access are different objects, not that brokerage. A mixed-role process must
expose its real transport dependency instead of acquiring a blanket relay
exemption.

An SSH reverse-forwarding relay is a transport role, not a credential relay:
its name does not make an access-bearing listener disposable.

## Per-form source observations

| Form | Actual access-bearing boundary | Provisioner/removal distinction |
|------|--------------------------------|--------------------------------|
| Direct OpenSSH host/client | Native client and target `sshd`, their key/authorization/host-pin material, and selected SSH config/fragment. | The [direct module](../../../plugins/agent-ssh/transports/direct/module.yaml#L1-L17) has no proxy command and does not provision a server. Core profile emission is not the request-serving process. |
| Windows-to-WSL interop SSH | `wsl.exe`, selected distribution/user, WSL `nc`, loopback `sshd`, its configuration/keys and required environment lifetime. | The [WSL module](../../../plugins/agent-ssh/transports/wsl/module.yaml#L1-L31) emits the interop recipe rather than owning those native processes. Removing the emitter is distinct from removing a profile, distribution or its SSH service. |
| Dev Tunnel SSH client/host | Client `dtssh proxy`, external tunnel identity and the actual target `dtssh host`/OpenSSH serving and startup chain. | The [client module](../../../plugins/agent-ssh/transports/dtssh/module.yaml#L1-L25) invokes an external proxy. The [host installer](../../../plugins/agent-ssh/transports/dtssh/scripts/install-host.ps1#L630-L649) copies its launcher/installer to a separate user-local location; its [own uninstall](../../../plugins/agent-ssh/transports/dtssh/scripts/install-host.ps1#L836-L849) stops that launcher and removes startup integration. Do not conflate core removal with this transport-host teardown. |
| Gateway directory/profile projection | Native OpenSSH `ProxyJump`, selected Gateway alias, peer reverse port, SSH identity and host pin. | The [client profile](../../../plugins/agent-ssh/src/agent_ssh/gateway_profile.py#L137-L156) emits `ProxyJump`, not an agent-ssh/Python proxy executable. An already-emitted profile can establish a fresh native connection without running the emitter; the selected Gateway alias still has its own real dependencies. The [directory adapter](../../../fleet-manager/src/fleet_manager/gateway_ssh_provider.py#L101-L161) does not prove their liveness. |
| SSH service native relay | Foreground `agent-ssh-service`, its native `sshd`, dedicated host key/config/authorization, admitted reverse listeners and required target SSH carriers. | The [relay](../../../services/ssh/src/agent_ssh_service/relay.py#L51-L75) generates restricted forwarding authorization; its [native child path](../../../services/ssh/src/agent_ssh_service/relay.py#L163-L249) accepts SSH TCP traffic directly, not through the API WebSocket. This is transport, not credential brokerage. |
| Standalone `agent_ssh.gateway_sshd` role | The selected installed entrypoint, native `sshd`, dedicated loopback config/key/authorization and its actual outer lifecycle owner. | The [adapter](../../../plugins/agent-ssh/src/agent_ssh/gateway_sshd.py#L213-L282) prepares and launches a foreground listener. Its [contract](../../../plugins/agent-ssh/docs/gateway-transport.md#L148-L180) leaves startup/rollback custody with an outer owner; the audit did not establish a production deployment owner. Do not attribute this role to dtssh without a call. |
| External proxy/tunnel provider | The selected proxy executable and route, provider endpoint, required host daemon/startup and native authentication state. | A provider's profile/setup contribution is not proof that its request path survives removal. Audit the actual provider separately; an endpoint or helper outside the core's removal paths is not implicitly authorized for deletion. |
| Gateway service API/control channel | Its actual admitted HTTP/WebSocket role and required connector/service owner. | The [SSH service hosting path](../../../services/ssh/src/agent_ssh_service/hosting.py#L116-L142) uses a service connector. Token/proof authentication does not make the carried API channel a credential relay; retain it only where its role is required for selected machine-management access. |

## Actual removal reach observed

- **Core agent-ssh:** both [POSIX](../../../plugins/agent-ssh/scripts/install.sh#L1106-L1121)
  and [PowerShell](../../../plugins/agent-ssh/scripts/install.ps1#L1104-L1120)
  remove the core command/runtime contents and retain transaction/staging
  coordination. These functions do not remove native OpenSSH, SSH keys/config,
  separately installed dtssh host assets or WSL itself. That does not prove
  safety for a selected route that executes code inside the removed runtime.
- **Dedicated dtssh host uninstall:** its separate stop/startup-removal action
  affects the transport host. The copied launcher does not visibly invoke
  `agent_ssh.gateway_sshd`; core runtime or payload removal alone is not shown
  to remove that copied host installation.
- **SSH service shutdown:** the combined foreground
  [hosting lifetime](../../../services/ssh/src/agent_ssh_service/hosting.py#L45-L86)
  stops/drains the connector and native relay; the relay's `finally` retires its
  owned `sshd` group. Removing that actual owner would remove the management
  listener, not merely an agent credential relay. The declared
  [container entrypoint](../../../services/ssh/container/Dockerfile#L22-L28)
  runs `agent_ssh_service`; source packaging does not identify a particular
  host's selected container or deployment manager.
- **CodeSpaces POSIX uninstall:** [the actual consumer](../../../plugins/agent-codespaces/scripts/install.sh#L1059-L1112)
  stops/removes its Connection Owner, closes sockets below its own install
  directory with `ssh -O exit`, applies a `ControlPath`-directory process
  filter, and removes the install directory including configuration/database.
  This closes plugin-owned channels, not a demonstrated unrelated operator
  `ssh`/`sshd`. If a management route uses that owner, it needs explicit
  retention; not every agent ControlMaster is automatically critical.
- **Bridge POSIX uninstall:** [uninstall](../../../plugins/agent-bridge/scripts/install.sh#L1970-L2005)
  invokes [stop](../../../plugins/agent-bridge/scripts/install.sh#L2163-L2204),
  which targets the configured unit/PID or bridge-port listener and the
  credential-relay listener. Closing agent transports is distinct from killing
  an operator's unrelated native SSH connection. Credential-relay teardown is
  allowed; a shared management carrier still requires classification.
- **Vault POSIX uninstall:** [the consumer](../../../plugins/agent-vault/scripts/install.sh#L947-L1002)
  stops its own unit and removes its command/auth helper. Ordinary removal
  retains state while purge removes runtime/config contents through its own
  root. No native SSH key/config or arbitrary `sshd` target is established by
  that body; its credential/auth relay role is disposable.
- **Containers entrypoint:** [the inspected parser](../../../plugins/agent-containers/scripts/init.sh#L27-L49)
  accepts initialization/provisioning actions, not uninstall/purge. It is not
  evidence of a complete supported container-runtime removal path.
- **Manager complete uninstall:** the [supported-uninstall contract](../installation-independent-sessions/manager-uninstall.md)
  remains a proposed prerequisite at this source baseline. It does not prove a
  functioning complete or forced removal operation.

The inspected call sites do not establish a current critical-channel-aware
force/purge retention gate. Exact native Windows removal consumers for
Bridge/CodeSpaces/Vault were not established in this bounded audit; Bash
call sites and generic root-safety primitives cannot qualify their Windows
behavior. These are explicit remaining proof obligations, not claims that
native removal is impossible or that it necessarily kills SSH.

## Retention and validation gates

- [x] Review this source audit and intent correction before changing removal
  behavior; retain #63/#7206, #8669/#8864 and existing service/installer owners.
- [ ] Enumerate the selected management route and its actual executable,
  process/supervision, config, identity/authorization, endpoint and reconnect
  dependencies. Exclude that closure from supporting-suite removal/reinstall.
- [ ] Trace the actual default, forced and purge removal consumers on each
  supported platform, including generated/shared native entrypoints, and make
  unknown or inseparable critical impact a refusal rather than a force bypass.
- [ ] Separate provisioning/profile emission from live serving and restart.
  Retain a critical helper even if currently packaged beside disposable code,
  or qualify an independently durable transport before removing that package.
- [ ] In isolated fixtures, preserve a real management stream and then make a
  fresh authenticated connection after supporting-runtime/payload removal.
  Include proxy/tunnel failure/restart and mixed-role ownership; a port probe
  or already-open shell alone does not close this gate.
- [ ] Positively remove credential relays and noncritical agent channels while
  management access remains usable. Do not retain every credential broker or
  every CodeSpace/Bridge ControlMaster as a substitute for role classification.
- [ ] Combine these gates with the [YAML maintenance hold](maintenance-banner.md)
  and native session-survival prerequisites. A banner is not authority to stop
  critical connectivity, and no real host action is enabled by this plan.

The parent effort remains Active. No complete forced Windows uninstall is
qualified by this audit, and no current host's selected route was exercised.
