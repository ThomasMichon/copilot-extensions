# Pattern: standalone-service-layout

**Serves:** *Vision installer* §Behaviors/`out-of-plugin-delivery`; the
`agent-dispatch-standalone-container` and `agent-index-standalone-service`
efforts' own convention surveys.
**Exemplars:** `agent-dispatch-service/`, `agent-index-service/`,
`worktree-manager/`, `fleet-manager/` (pre-existing, grandfathered — see
Status below).

## Problem

A standalone, out-of-plugin package (one delivered as its own fetch-and-run
payload rather than through the Copilot plugin pipe — see
[`out-of-plugin-delivery`](../../visions/installer/README.md#out-of-plugin-delivery))
needs a top-level repo directory distinct from `plugins/<name>/`. The
convention that emerged organically (`agent-dispatch-service/`,
`agent-index-service/`) stutters: `agent-<topic>` is already the plugin's own
name (`plugins/agent-<topic>/`), so prefixing the standalone twin with the
same `agent-` and suffixing `-service` produces a name that differs from its
plugin sibling only by an easily-missed suffix, while adding no information a
dedicated top-level location wouldn't already convey.

A second, related problem: the **public, durable install entry point** for a
standalone service (the one-line `curl … | bash` / `iex …` a user runs, or
any URL a plugin's own installer hardcodes to fetch it, as
`plugins/agent-dispatch/scripts/install.{ps1,sh}`'s first-use provisioning
now does for `agent-dispatch-service/bootstrap.{ps1,sh}`) is easy to
couple directly to the package's current repo path. Once published, that URL
is a compatibility surface future reorganizations must not silently break.

## Standard approach

**Reserve `agent-<topic>` for plugins.** A name of the shape `agent-<topic>`
identifies exactly one thing in this repo: the Copilot plugin at
`plugins/agent-<topic>/`. No standalone/out-of-plugin package should reuse
that shape.

**New standalone services live at `services/<topic>`.** A bare topic name
(no `agent-` prefix, no `-service` suffix) under a dedicated top-level
`services/` directory — e.g. a future standalone twin of `agent-vault` would
be `services/vault/`, not `agent-vault-service/` or `vault-service/` at the
repo root. The `services/` root itself already establishes "this is a
standalone, out-of-plugin service package"; repeating that fact in the leaf
name is redundant once it has a dedicated home.

**Decouple the durable public install surface from internal layout.** A
standalone service's actual content directory is free to move as the repo
reorganizes (e.g. the migration this pattern itself motivates) only if
nothing external depends on its exact path. Two candidate mechanisms achieve
that decoupling; the follow-up migration effort (tracked below) picks one
rather than this pattern mandating it up front:

- **A dedicated top-level `install/` proxy directory.** Each service gets a
  thin, stable entry point (e.g. `install/dispatch/bootstrap.ps1`) that does
  nothing but resolve and exec the real
  `services/<topic>/bootstrap.{sh,ps1}`. The published URL
  (`raw.githubusercontent.com/.../install/dispatch/bootstrap.ps1`) never
  changes even if `services/<topic>/` itself is later renamed or
  restructured, because the proxy is the only thing a human or another
  plugin's installer ever hardcodes.
- **A unified dispatcher command** (e.g. `copilot-extensions install
  <name>`), potentially hosted by `worktree-manager` — already the one
  durable, self-updating, out-of-plugin app this repo ships (see the
  [installer vision](../../visions/installer/README.md)) — that resolves
  `<name>` to its current service location and execs its bootstrap script.
  This folds service discovery into the one app users already reach for
  turnkey setup, at the cost of depending on that app being installed first
  (a tradeoff the dedicated-`install/`-directory option avoids).

Either mechanism satisfies the same invariant: **the published, durable
install command never encodes a path a future reorganization would need to
keep stable.**

## Status

This is a **convention for new standalone service packages going forward**,
not a mandate to move anything that already exists. `agent-dispatch-service/`,
`agent-index-service/`, `worktree-manager/`, and `fleet-manager/` are
grandfathered under their current top-level names. Migrating
`agent-dispatch-service/` and `agent-index-service/` to `services/dispatch/`
and `services/index/` respectively — including picking and implementing one
of the two indirection mechanisms above so the move doesn't break the
bootstrap URL `plugins/agent-dispatch/scripts/install.{ps1,sh}` already
hardcodes — is tracked as a separate, explicit follow-up:
[#6419](https://github.com/ThomasMichon/copilot-extensions/issues/6419).
`worktree-manager/` and `fleet-manager/` predate even the
`agent-*-service` convention this pattern retires and are a distinct,
unscoped case; this pattern does not decide whether they move.

## Rationale

A single top-level `services/` root makes "is this a plugin or a standalone
package" discoverable from the directory listing alone, without parsing a
name for a stutter or a suffix. Decoupling the public install surface from
internal layout means a future repo reorganization is a purely internal
refactor — exactly the property the harness's other installer-engine work
(vendor pointers, versioned runtimes) already optimizes for, applied one
level up at the directory-layout boundary instead of the file-content
boundary.

## See Also

- Intent: [`visions/installer/`](../../visions/installer/README.md)
  (`out-of-plugin-delivery`, the one-line bootstrap, self-update)
- Registry: [`tools/standalone_consumers.py`](../../tools/standalone_consumers.py)
- Related efforts:
  [`agent-dispatch-standalone-container`](../../efforts/active/agent-dispatch-standalone-container/README.md),
  [`agent-index-standalone-service`](../../efforts/active/agent-index-standalone-service/README.md)
