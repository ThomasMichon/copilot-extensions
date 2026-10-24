# Chat container contribution

This directory supplies a **data-only loadout contribution**, not a delivered
image, container installer or deployment. It realizes the template portion of
the reviewed [container contract](../../../efforts/active/agent-chat-graduation/container-service.md).
The existing foreground Chat service remains independently usable; installing
its wheel does not select this optional fleet profile.

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
container specification. Still open are the independently admitted image and
artifact/lock inputs, actual configuration/secret/volume resolution, supported
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
