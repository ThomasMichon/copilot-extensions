# Transport-provider contract

How a transport plugs into agent-ssh. A transport is its own plugin (in its
audience's marketplace) that ships a `module.yaml` conforming to
`contract/module.schema.json`. The agent-ssh core consumes it; the transport
never re-implements profile rendering, coexistence, or verification.

## Division of labor

- **Core owns the mechanism:** `Host <name>` block rendering, deterministic
  option ordering, the `~/.ssh/config.d` managed-`Include` coexistence layout,
  atomic per-transport fragment writes, and the reachability probe.
- **Transport owns the recipe:** a single `proxy_command` template describing
  how to dial a host (or nothing, for plain SSH), a `proxy_binary_default`, an
  optional `install-client` script, and its own config schema extensions.

## The `proxy_command` template

Placeholders filled by the core per host:

| Placeholder | Value |
|---|---|
| `{hostname}` | the machine's transport-resolved hostname (`%h` for a jumpbox gate) |
| `{proxy_binary}` | registry `proxy_command_binary` override, else `proxy_binary_default` |
| `{name}` `{user}` `{port}` | the machine's registry fields |

Examples:
- Cloudflare: `"{proxy_binary} access ssh --hostname {hostname}"`
- (a dev-tunnel transport supplies its own equivalent)
- `direct`: omit `proxy_command` entirely -> plain SSH.

## Topology

- `per-machine` (default) -- each host dials its own hostname via the recipe.
- `jumpbox` -- one `gate` host carries the recipe; other hosts `ProxyJump` it.

## Coexistence rules (binding)

1. Write **only** `~/.ssh/config.d/50-agent-ssh-<module>.conf`.
2. Add **only** the single managed `Include ~/.ssh/config.d/*` line to
   `~/.ssh/config`; never rewrite existing content.
3. A machine belongs to exactly one transport's fragment (its `transport:` key).
4. Never read, write, or assume the layout of a peer transport's fragment.

## Verbs the core satisfies vs. the transport owns

| Verb | Owner |
|---|---|
| `emit-profile` | core (`agent-ssh emit-profile`, `core/ssh_profile.py` compatibility wrapper) |
| `verify` | core (`agent-ssh verify`, `scripts/verify.*`) |
| `install-client` | transport (if it needs a client binary) |
| `provision-server` | transport (optional; may be operator-manual) |
