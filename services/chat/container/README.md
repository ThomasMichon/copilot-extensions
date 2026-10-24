# Chat container contribution

This directory supplies an **offline wheel-only image recipe** and a
**data-only loadout contribution**, not an admitted image release, container
installer or deployment. It realizes portions of
the reviewed [container contract](../../../efforts/active/agent-chat-graduation/container-service.md).
The existing foreground Chat service remains independently usable; installing
its wheel does not select this optional fleet profile.

## Independently built foreground image

Use `Dockerfile` with a dedicated build context containing only that file,
`wheels/` and `requirements.lock`. The trusted artifact owner supplies the
approved digest-pinned `PYTHON_BASE`, the exact platform-compatible service
wheel and its complete `service` dependency closure, and exact versions plus
SHA-256 hashes in the lock. Build installation is offline, wheel-only and
hash-required. It never copies a checkout, host configuration, credentials or
another runtime. `host.example.json` is synthetic non-secret configuration,
not a production selection. No credential or private configuration is an
image-build input.

The image runs one foreground host as UID/GID `65532:65532`; SIGTERM drains
and closes it. Keep the application filesystem read-only, drop capabilities,
enable no-new-privileges, and provide bounded writable `/tmp`. Mount the
selected non-secret configuration at `/run/config/chat.json` and its
owner-resolved private key at `/run/secrets/chat-domain`, both read-only.
The key must be readable by that UID, with no group/other POSIX access.
Mount separately provisioned, owner-private retained storage at `/data`.
Provision its ownership and `0700` mode explicitly; the image does not
chown, create or adopt a replacement volume on ordinary startup.

First creation is a distinct explicit operation against an **empty** retained
directory. With the same image, configuration and retained mount selected by
the deployment owner, override only the image command with:

```text
--config /run/config/chat.json --initialize-storage
```

This initializes storage without starting a listener or generating a credential.
It refuses a live local writer, prior history, unknown content or a partial
initialization; it is not a repair, purge or adoption command. Ordinary startup
requires existing database identity, schema and epoch and never creates a
missing database. A configured installation ID must survive replacement.
The canonical writer lease remains on the retained volume; `/tmp/chat-run`
contains only disposable endpoint/socket state. Do not move the lease to
ephemeral storage. Local locking is not cross-host fencing.

The image health check invokes the same installed host's authenticated
`--health` mode through its canonical local endpoint. It checks the actual
serving identity and admission state with bounded I/O; it neither opens the
live database nor treats a status file as application health. It becomes
unready during drain. No backend TCP port is exposed. A supported outbound
Gateway connector remains a separate owner dependency, not a sidecar or
proxy invented by this recipe.

The optional `Chat container qualification` workflow runs
`tools/qualify_chat_container.py` on a pinned source and explicitly selected
digest-pinned Python Linux base. It builds a dedicated source-free context,
starts fresh with read-only application content, one CPU, 128 MiB memory,
64 PIDs and 16 MiB scratch, then checks missing-volume refusal, explicit
initialization, authenticated health, two local participants, singleton
refusal, SIGTERM, restart and stable-ID deduplication. Those are **fixture
limits, not measured production capacity recommendations**. The report records
the actual image/base/platform and timings. The disposable local volume has
no qualified persistent disk quota; Gateway, cross-host fencing, different-code
rollback, Komodo execution and private deployment remain unqualified.

## Select and actualize

Pass `loadout-catalog.json` as an explicitly selected contribution catalog to
Fleet Manager's existing
[`design` interface](../../../fleet-manager/docs/loadouts.md).
It contributes `chat.service`, revision `1`, source `agent-chat-service`.
No plugin import, discovery crawler, setup hook or additional schema is added.
If an adopter already selects other contributions, compose the catalog through
that adopter's normal configuration flow without shadowing template identities.

`allocation.example.json` is one allocation, to be placed under the private
configuration's `extensions.loadouts.allocations.<chosen-name>`. It is not a
complete fleet configuration and intentionally supplies **no support roles or
inventory claiming to be ready**. All example identifiers, machine placement
and resource numbers are synthetic, not production recommendations or measured
capacity. Resolve the actual private configuration repository and use its
dedicated worktree and publication flow; do not infer a repository or filename
from "dotfiles."

Retain the selected template ID, revision and source, private overrides and
custom roles. Every slot is required. Artifact/configuration/storage/credential/
Gateway/manager/resource references remain bounded identifiers, not raw image
tags, paths, tokens, commands, environment or Compose. Each owning resolver must
admit its referenced content before any effect. Resource reservations and limits
are separate positive integer inputs; limits cannot be below reservations.
Update template revision through an explicit private actualization, not by
silently overwriting the selected revision or discarding an override.

## Complete support closure

The `container`, `gateway`, `storage`, `credentials`, `manager` and `network`
slots name existing allocations, not arbitrary configuration references. Their
selected templates must explicitly provide, respectively:

| Capability | Meaning required from its actual owner |
| --- | --- |
| `container.runtime` | Admitted container execution on the selected target |
| `gateway.chat-route` | Predeclared Chat slot, authenticated outbound route and compatible connector |
| `storage.single-writer` | Retained conversation storage, compatibility and current writer fencing |
| `credential.chat-domain` | Owner-resolved domain credential access without embedded secret material |
| `deployment.komodo` | Selected manager instance and independent bootstrap |
| `network.gateway-outbound` | Admitted outbound connectivity and exposure closure |

These capability labels define this template's required composition contract.
They do not assert that another package already supplies a matching template,
connector or executor. Missing allocations/capabilities, cycles, stale capacity
or incompatible provenance block the normal design. Multiple requirements may
share a real support allocation; Fleet Manager accounts for it once.
Support allocations retain their own CPU/memory/disk costs even on another
named machine. A reviewable design is not current support/readiness admission.

## Komodo binding and remaining delivery gates

`role-bindings.example.json` uses the existing reference-only role namespace:
`container.service` through a `komodo` provider, an explicitly named Stack,
artifact/configuration/key references and independent manager bootstrap.
The Chat role is not a copy of the loadout's support graph. Its empty
`depends_on` list means no executable support-role bindings are supplied by this
example, **not** that the service has no dependencies. The final private
composition must supply its actual role ordering and complete support closure.
Do not paste storage mounts, secret bytes, commands or image URLs into this
reference-only namespace; those belong to the existing resolvers.

The current Komodo provider produces a planning projection, not an applied
container specification. Still open are the independently admitted image release
and production artifact/lock inputs, actual configuration/secret/volume resolution, supported
Gateway connector and effect executor, prepared-plan confirmation and private
publication. No implicit backend port, host networking, socket mount, privileged
container or alternate controller is introduced here.

Before reporting deployment, qualify the real image with read-only application
content and a declared non-root identity, resource limits, caller-independent
health, stable-ID exchange through the admitted Gateway route, persistent
single-writer restart/update/rollback and preserving removal. The
[parent effort](../../../efforts/active/agent-chat-graduation/README.md) keeps
those acceptance gates open. Parsing either example, a successful pure design,
a source merge or a named Stack reference establishes none of them.
