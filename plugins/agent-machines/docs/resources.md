# Declarative resources

Resources are typed, identity-bearing declarations of *common* machine state
that `agent-machines` converges itself -- the reviewable-data alternative to
per-repo scripts. They sit between the Copilot **surfaces** (which converge
`~/.copilot/`) and repo-local **modules** (the arbitrary-mutation escape hatch).

A requirement package declares them under a top-level `resources:` list:

```yaml
schema_version: 1
package: your-repo/machine-defaults
gate: ["your-box"]
resources:
  - type: package
    id: marlocarlo.psmux        # identity within (type, manager)
    manager: winget             # winget | apt | pipx | uv-tool | pip
    version: "3.3.5"           # exact pin (optional)
    state: present              # present (default) | absent
    pin: true                   # hold at version where the manager supports it
  - type: file
    id: psmux-settings          # display id (optional; defaults to path)
    path: "$HOME/.psmux.conf"  # $HOME / $REPO(<name>) anchored, or absolute
    format: text                # text (default) | json
    strategy: ensure-present    # enforce | ensure-present
    content: |
      set -g mouse on
```

## Resource types

### `package`

Converge a package-manager package. Identity is `(manager, id)`, so the same
`id` under two managers is two distinct resources.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `package` |
| `id` | yes | Manager-native package id (e.g. a winget `--id`). |
| `manager` | yes | `winget`, `apt`, `pipx`, `uv-tool`, or `pip`. |
| `version` | no | Exact version pin. |
| `state` | no | `present` (default) or `absent`. |
| `pin` | no | Hold at `version` where the manager supports pinning (`winget pin`, `apt-mark hold`). |
| `platforms` | no | Restrict to a subset of `windows` / `linux` / `wsl`. |
| `gate` | no | Restrict to specific machines (defaults to the package gate). |
| `owner` | no | Override the collision owner label (defaults to the package name). |

Manager support matrix:

| Manager | Platforms | Pin |
| --- | --- | --- |
| `winget` | windows | yes (`winget pin add`) |
| `apt` | linux, wsl | yes (`apt-mark hold`) |
| `pipx` | windows, linux, wsl | no |
| `uv-tool` | windows, linux, wsl | no |
| `pip` | windows, linux, wsl | no |

Apply detects current state first, then installs/upgrades only when needed, and
pins when asked. On an unsupported platform, an unknown manager, or a missing
manager binary, the resource is skipped with a reason (never a hard failure).

### `file`

Converge a canonical config file. Identity is the normalized `path`.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `file` |
| `path` | yes | `$HOME/...`, `$REPO(<name>)/...`, or an absolute path. |
| `id` | no | Display id (defaults to the path). |
| `format` | no | `text` (default) or `json`. |
| `strategy` | no | `enforce` (default) or `ensure-present`. |
| `content` | no | The desired content (a JSON object literal string for `format: json`). |

Strategy semantics:

- **text / enforce** -- the file content is made exactly `content`.
- **text / ensure-present** -- the file is created with `content` only if it is
  missing; an existing file is left untouched.
- **json / enforce** -- `content` is deep-merged authoritatively over the live
  JSON (declared keys win, siblings preserved).
- **json / ensure-present** -- `content` is applied as a floor (only fills in
  keys that are absent).

Writes are atomic and back up any existing file under
`~/.agent-machines/backups/` before mutating. A `$REPO(<name>)` anchor that does
not resolve to a known repo is skipped with a reason rather than guessed.

## Path anchors

| Anchor | Resolves to |
| --- | --- |
| `$HOME/<rest>` | The target user's home directory. |
| `$REPO(<name>)/<rest>` | The checkout root of the contributing repo `<name>`. |
| `/absolute/path` | Used as-is. |

## Collision handling

When two packages target the same resource identity, the resolver mirrors the
validator's stance -- **detect-and-report, resolve only the unambiguously
compatible**:

| Situation | Result |
| --- | --- |
| package `present` + `absent` | error |
| package two different `version` pins | error |
| package `pin` flags differ | OR'd to pinned (compatible) |
| file two `enforce` with different `content` | error |
| file conflicting `format` | error |
| file `enforce` + `ensure-present` | enforce wins (advisory) |
| file two `ensure-present` with different content | deterministic pick (advisory) |

The deterministic pick is stable regardless of package order, so plans and drift
keys are reproducible. Errors block `restore`; advisories do not.

## CLI

Resources appear in every verb:

- `agent-machines plan` lists each resolved resource with a one-word summary and
  its contributors.
- `agent-machines validate` reports resource collisions alongside surface and
  bootstrap findings.
- `agent-machines restore` applies resources between surfaces and modules;
  `--dry-run` (the default) previews the exact commands / writes, `--apply`
  performs them, and `--only <id|type|type:id>` restricts the run to a resource
  (and skips modules when nothing else is selected).
- `agent-machines restore --json` includes a `resources` list and a
  `plan.resources` list.

## Adopter guide

To move a common fact out of a per-repo restore script and into resources:

1. Identify the fact's *identity* -- a package `(manager, id)` or a config file
   `path`. If two repos already manage it, they will now collide-check.
2. Add a `resources:` entry to the requirement package that should own it, gated
   to the right machines.
3. Delete the imperative step from the repo-local module (or leave the module
   for the parts that are genuinely bespoke -- resources and modules coexist).
4. `agent-machines validate` to confirm no cross-package collision, then
   `agent-machines restore` (dry-run) to preview, and `--apply` to converge.

Backward compatibility: a package with no `resources:` key resolves to an empty
list, and existing `manage` / `modules` behavior is unchanged.

### PSMux acceptance case

PSMux (the `psmux` terminal multiplexer) is the canonical first adopter. Its
desired state on a Windows box is exactly two resources:

```yaml
resources:
  - type: package
    id: marlocarlo.psmux
    manager: winget
    version: "3.3.5"           # pinned: a later build regressed session attach
    state: present
    pin: true
  - type: file
    id: psmux-settings
    path: "$HOME/.psmux.conf"
    format: text
    strategy: ensure-present   # seed the user's config without clobbering edits
    content: |
      set -g mouse on
```

This is the exact schema a downstream repo should add to the requirement package
that owns PSMux provisioning. `ensure-present` is deliberate: the per-session
model treats `~/.psmux.conf` as user-owned, so the resource seeds it once and
never overwrites later hand edits. A future `managed-block` file strategy (write
only a marked, engine-owned block within an otherwise user-owned file) is the
natural next step for opt-in keybind blocks; it is not implemented yet.

## Reserved types (roadmap)

`registry` and `feature` are recognized and validated by the schema but have no
handler yet -- apply reports "no handler" and skips, so a package can declare
them ahead of the engine. They are the clean next extension points:

- **`registry`** -- Windows registry values (identity `(type, path)`), for
  machine settings currently poked by `reg`/`Set-ItemProperty` in scripts.
- **`feature`** -- Windows optional features / capabilities and Linux
  distro features (identity `(type, id)`).

Adding either is a new `ResourceHandler` subclass registered in `HANDLERS`; the
engine, CLI, and validator wiring already carry them through.
