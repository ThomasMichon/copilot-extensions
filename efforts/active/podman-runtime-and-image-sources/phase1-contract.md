# Consumer Trace and Proposed Realization

Parent: [Podman Runtime and Image Sources](README.md)

This records the agent-recommended design under the reviewed #11388 plan.
The version-1 driver has source and native consumer evidence; the Windows
broker extension below remains a proposal requiring review before code.
Neither design nor validation grants deployment or execution authority.

## Existing real consumer

`fleet.up` resolves `runtime_drivers.driver_for_fleet`, inventories members and
checks lifecycle blockers before provisioning. Driver fleets bypass Docker
toolchain checks, but retain fleet sizing, retained-member and lease ownership.
`ExternalDriver` carries the version-1 JSON request over a caller-owned bounded
child process and validates the whole response before binding routing.

Discovered and provisioned members enter the same fleet-qualified,
incarnation-pinned route table. `routed_exec` translates the existing
Docker-shaped exec/copy boundary into the selected driver's operations.
`driver_cli.runtime_proxy` supplies the existing fleet-qualified hosting-policy
consumer; a driver handle cannot silently become a Docker target.

`runtime_drivers.launch_plan` retains required relay policy. A driver without
`owns_relay` needs `ssh_transport` for the provider's reverse forward. Interactive
Copilot and Session Host also require that transport; direct streaming ACP is
not equivalent. A Podman driver must not claim `owns_relay` merely to make
readiness green.

For SSH, `ssh_transport.prepare_ssh_config` stages the normal host key through
the routed exec owner, obtains the selected incarnation and constructs the
driver's pinned `exec-stream` request. POSIX can use that request as its
ProxyCommand. Windows explicitly refuses it: the existing windowless
`docker_proxy` broker still constructs a literal Docker command and its
version-1 endpoint has no driver/registration binding. Removing the refusal
alone would select the wrong runtime, not enable Podman.

## First complete supported flow

Use the existing version-1 external-driver protocol for a shipped Podman
implementation; do not broaden restricted containment or reinterpret Docker
fleet keys. Existing explicit manifests and PATH drivers retain their
precedence. A bundled Podman command is a fallback only for the selected
`podman` name, not a fallback from another failing driver. A malformed explicit
Podman registration must not be hidden by that fallback.

Recommended non-secret `driver_options` are `image`, `executable`, `connection`
and `pull_policy`. Validate supported keys/types rather than accepting a
generic shell template. The selected executable is Podman, never Docker;
an explicit Windows connection names an already-configured machine/service.
Do not initialize, start, install or log in as a readiness side effect.

Resolve the image through Podman's own configured registry/alias/authentication
owners. Use a bounded non-interactive pull with the selected policy, then create
from the resolved immutable local image ID without a second implicit pull.
Do not read credential files, pass inline credentials, change TLS policy or
invent a public-feed fallback. Unknown image resolution/pull outcomes are not
successful provision and do not authorize replacing an existing member.

Native Podman documentation confirms that unresolved short names can prompt,
and that remote clients support only registry transport. Preserve those
limitations explicitly; a missing alias or unsupported source is an actionable
failure, not permission to guess a registry or change configuration.

The first supported authenticated consumer is the existing POSIX SSH path,
using the provider-owned relay and policy checks. Windows remains explicitly
unsupported until the existing broker can carry an exactly selected,
incarnation-pinned driver command with correct reconfiguration and lifetime
behavior. That remaining lane is an open requirement, not a parity claim or
completion transfer.

The initial driver uses version-1 verbs, requests and capability semantics
unchanged. It does not emit new mandatory syntax to existing providers or
pretend an old provider implements the bundled fallback. Driver discovery's
additive `podman` fallback and its precedence need focused regression tests.
Keep CLI parsing strict. The separate Windows broker extension below is not
implemented or enabled by that initial driver.

## Windows boundary before caller enablement

Extend the existing broker owner rather than introducing another service.
Preserve legacy Docker startup, endpoint semantics and active connections.
New endpoint reuse must bind the actual selected runtime registration and
member incarnation; it cannot accept an old Docker endpoint because a port
answers ping. Reconfiguration and unsupported/rolled-back receivers refuse
before key staging or driver effects; no effectful new-form/old-form replay.

Name and test the concrete receiver declaration and compatible rollout before
emitting new helper syntax. Use selected-runtime or live-peer discovery, not
payload/package-version equality. Existing cleanup/child-settlement ownership,
headlessness and protected connections remain in force. Full Windows
consumer acceptance must precede removal of its current readiness refusal.

## Native references

- [Podman pull](https://docs.podman.io/en/latest/markdown/podman-pull.1.html): configured aliases, registry authentication, pull policy and remote-source limitations.
- [Podman connection list](https://docs.podman.io/en/latest/markdown/podman-system-connection-list.1.html): existing named connection selection.
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md): receiver-first support, selected-runtime discovery and no replay.
