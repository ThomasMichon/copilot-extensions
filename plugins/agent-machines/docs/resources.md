# Declarative resources

Resources are typed, identity-bearing declarations of *common* machine state
that `agent-machines` converges itself -- the reviewable-data alternative to
per-repo scripts. They sit between the Copilot **surfaces** (which converge
`~/.copilot/`) and repo-local **modules** (the arbitrary-mutation escape hatch).

A requirement package declares them under a top-level `resources:` list:

```yaml
schema_version: 4
package: your-repo/machine-defaults
authority: 0                  # optional, -1000..1000
gate: ["your-box"]
resources:
  - type: package
    id: marlocarlo.psmux        # identity within (type, manager)
    manager: winget             # winget | apt | pipx | uv-tool | pip
    authority: 10               # optional override of package authority
    version: "3.3.5"           # exact pin (optional)
    state: present              # present (default) | absent
    pin: true                   # hold at version where the manager supports it
    process_guard:              # defer replacement/removal while live
      names: ["psmux.exe"]
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
| `process_guard.names` | no | Exact process image names that defer replacement/removal while running. Windows process probing is supported now; an unavailable/unsupported probe defers safely. |
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

Apply detects current state first. An absent package uses the manager's install
operation; a present package at the wrong version uses its native update/upgrade
operation; both verify the exact desired postcondition where supported. Pinning
is reconciled separately. A `process_guard` applies only to replacement/removal,
not first install or pin metadata. A matching process, failed probe, unsupported
probe platform, or missing probe binary returns `status: deferred` without
mutation, and reports the reason plus the command that remains pending. If
installed package state cannot be established, guarded mutation also defers
instead of assuming that the package is absent.

### `file`

Converge a canonical config file. Identity is the normalized `path` plus a
`block` id (empty for whole-file strategies), so distinct managed blocks in one
file are separate, compatible resources.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `file` |
| `path` | yes | `$HOME/...`, `$REPO(<name>)/...`, or an absolute path. |
| `id` | no | Display id (defaults to the path). |
| `format` | no | `text` (default) or `json` (not valid with `managed-block`). |
| `strategy` | no | `enforce` (default), `ensure-present`, or `managed-block`. |
| `block` | managed-block only | Stable block identity; also derives the markers. |
| `begin` / `end` | no | Explicit marker override (default derived from `block`). |
| `state` | managed-block only | `present` (default) or `absent` (remove the block). |
| `content` | no | The desired content (whole file, or just the block body for `managed-block`). |

Strategy semantics:

- **text / enforce** -- the file content is made exactly `content`.
- **text / ensure-present** -- the file is created with `content` only if it is
  missing; an existing file is left untouched.
- **text / managed-block** -- the engine owns *only* a marked block inside an
  otherwise user-owned file. It refreshes the block to `content` (the block
  body), preserves all other lines verbatim, trims trailing blank lines so
  repeats never accumulate them, and re-appends the block after a single blank
  separator. `state: absent` removes the block and leaves the rest untouched.
- **json / enforce** -- `content` is deep-merged authoritatively over the live
  JSON (declared keys win, siblings preserved).
- **json / ensure-present** -- `content` is applied as a floor (only fills in
  keys that are absent).

The `managed-block` markers default to `# >>> <block> >>>` / `# <<< <block> <<<`
(comment-style `#`), matching the convention used by opt-in keybind blocks. Set
`begin`/`end` explicitly to interoperate with an existing block that uses
different markers.

Writes are atomic and back up any existing file under
`~/.agent-machines/backups/` before mutating. A `$REPO(<name>)` anchor that does
not resolve to a known repo is skipped with a reason rather than guessed.

### `registry`

Converge a single Windows registry **value**. Identity is
`(canonical key, value name)`, folded case-insensitively (hive short names like
`HKCU` expand to `HKEY_CURRENT_USER`). Windows-only; filtered out on other
platforms.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `registry` |
| `path` | yes | The key, e.g. `HKCU:\Software\App` or `HKEY_CURRENT_USER\Software\App`. |
| `name` | no | Value name (defaults to `""`, the key's default value). |
| `id` | no | Display id (defaults to the path). |
| `value` | no | Desired data. |
| `value_type` | no | `String` (default), `ExpandString`, `MultiString`, `DWord`, `QWord`, `Binary`. |
| `state` | no | `present` (default) or `absent` (delete the value). |

Apply queries the current value first via `reg.exe`, then writes
(`reg add ... /f`) only when the value or type differs, or deletes
(`reg delete ... /f`) for `state: absent`. If `reg` is not on PATH the resource
is skipped with a reason.

### `feature`

Converge an OS feature via a named `manager`. Identity is `(manager, id)`.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `feature` |
| `manager` | yes | `windows-optional-feature`, `windows-capability`, or `linux-systemd`. |
| `id` | yes | Feature / capability / unit name. |
| `state` | no | `present` (default) or `absent`. |

Manager matrix:

| Manager | Platforms | Backend | present / absent |
| --- | --- | --- | --- |
| `windows-optional-feature` | windows | DISM `/get-featureinfo`, `/enable-feature`, `/disable-feature` | Enabled / Disabled |
| `windows-capability` | windows | DISM `/get-capabilityinfo`, `/add-capability`, `/remove-capability` | Installed / removed |
| `linux-systemd` | linux, wsl | `systemctl is-enabled` / `enable` / `disable` | enabled / disabled |

Apply detects current state first and acts only when it differs. On an
unsupported platform, an unknown manager, or a missing backend binary, the
resource is skipped with a reason (never a hard failure).

### `power-setting`

Converge one setting in a Windows power scheme. Identity is
`(scheme, subgroup, setting)`, case-folded with the documented fixed aliases
(`SCHEME_BALANCED`, `SCHEME_MIN`, `SCHEME_MAX`, `SUB_BUTTONS`, `LIDACTION`, and
`PBUTTONACTION`) canonicalized to GUIDs so alias/GUID declarations collide.
The dynamic `SCHEME_CURRENT` alias remains its own identity because its GUID is
live machine state rather than a manifest constant.

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | `power-setting` |
| `subgroup` | yes | Power subgroup GUID or alias, such as `SUB_BUTTONS`. |
| `setting` | yes | Power-setting GUID or alias, such as `LIDACTION`. |
| `scheme` | no | Scheme GUID or alias; defaults to `SCHEME_CURRENT`. |
| `id` | no | Display id (defaults to `subgroup/setting`). |
| `ac` | one of AC/DC | Desired plugged-in value index. |
| `dc` | one of AC/DC | Desired battery value index. |

Values may be unsigned integers (for arbitrary settings) or the friendly action
names `do-nothing`, `sleep`, `hibernate`, `shut-down`, and
`turn-off-display`. The friendly names map to the standard action indexes 0-4
and should be used only for settings whose documented values are those actions.

Apply reads hidden and visible settings with `powercfg /QH`, changes only the
drifted AC/DC side, reactivates the scheme only when it is active, and queries
again to verify the exact stored postcondition. If a write or activation fails,
the handler restores any indexes it already changed so the next restore still
sees drift and retries. A failed query or post-apply mismatch is an error rather
than a success-shaped fallback. `state` is not supported: power settings are
always declarations of desired AC/DC indexes.

## Path anchors

| Anchor | Resolves to |
| --- | --- |
| `$HOME/<rest>` | The target user's home directory. |
| `$REPO(<name>)/<rest>` | The checkout root of the contributing repo `<name>`. |
| `/absolute/path` | Used as-is. |

## Collision handling

When two packages target the same resource identity, authority is resolved per
semantic field. A unique highest authority selects that field and emits
structured selected/superseded provenance plus an informational
`authority-supersession` finding. Equal-highest disagreement retains the
existing error (or advisory for differing `ensure-present` file content).
Declarations are not filtered wholesale, so unrelated fields and conservative
compatibility data from lower-authority declarations remain effective:

| Situation | Result |
| --- | --- |
| package `present` + `absent` | highest authority wins; equal-highest disagreement errors |
| package two different `version` pins | highest authority wins; equal-highest disagreement errors |
| package `pin` flags differ | OR'd to pinned (compatible) |
| package `process_guard.names` differ | names are case-folded and unioned (conservative, compatible) |
| file two `enforce` with different `content` | highest enforce authority wins; equal-highest disagreement errors |
| file conflicting `format` | highest authority wins; equal-highest disagreement errors |
| file `enforce` + `ensure-present` | enforce wins (advisory) |
| file two `ensure-present` with different content | highest authority wins; equal-highest disagreement keeps the deterministic advisory |
| file same `(path, block)` with different state or content | highest field authority wins; equal-highest disagreement errors |
| file same `(path, block)` with different begin/end markers | error regardless of authority; marker migration is not implicit |
| file distinct `block` ids in one file | compatible (coexist) |
| file whole-file owner + managed block on one path | error |
| registry `present` + `absent` | highest authority wins; equal-highest disagreement errors |
| registry conflicting `value` or `value_type` | highest field authority wins; equal-highest disagreement errors |
| feature `present` + `absent` | highest authority wins; equal-highest disagreement errors |
| power setting conflicting `ac` or `dc` value | highest authority for that power source wins; equal-highest disagreement errors |

File `format` and `content` are selected from declarations participating in the
winning strategy (`enforce` when present, otherwise `ensure-present`), so
resolution never synthesizes a format/content pair that no compatible
declaration supplied. Invalid JSON content is an error result, not a successful
skip.

Package `pin` remains an OR across every declaration, and
`process_guard.names` remains a case-folded union across every declaration.
Whole-file and managed-block ownership of the same path remains a hard error
regardless of authority. The deterministic selection is stable regardless of
package order, so plans and drift keys are reproducible. Errors block
`restore`; advisories and authority information do not.

## CLI

Resources appear in every verb:

- `agent-machines plan` lists each resolved resource with a one-word summary
  and contributors, then renders any source-qualified authority decisions.
- `agent-machines validate` reports resource collisions alongside surface and
  bootstrap findings.
- `agent-machines restore` applies resources between surfaces and modules;
  `--dry-run` (the default) previews the exact commands / writes, `--apply`
  performs them, and `--only <id|type|type:id>` restricts the run to a resource
  (and skips modules when nothing else is selected).
- `agent-machines restore --json` includes a `resources` list (each result has
  `status: ok|changed|deferred|skipped|error`), a `plan.resources` list, and
  stable `authority_decisions`. Any resource error makes the top-level `ok`
  false and the command exit nonzero.

## Adopter guide

To move a common fact out of a per-repo restore script and into resources:

1. Identify the fact's *identity* -- a package `(manager, id)`, a config file
   `path`, or a power setting `(scheme, subgroup, setting)`. If two repos already
   manage it, they will now collision-check.
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
desired state on a Windows box is exactly two resources: the pinned package, and
a `managed-block` file that owns *only* the opt-in keystroke-passthrough keybind
block inside the user-owned `~/.psmux.conf`.

```yaml
resources:
  - type: package
    id: marlocarlo.psmux
    manager: winget
    version: "3.3.5"           # pinned: a later build regressed session attach
    state: present
    pin: true
    process_guard:
      names: ["psmux.exe"]     # defer version replacement while sessions are live
  - type: file
    id: psmux-keybinds
    path: "$HOME/.psmux.conf"
    strategy: managed-block
    block: "agent-worktrees mux keybinds (opt-in)"
    content: |
      # Opt-in intercept: every unprefixed key/mouse event passes straight
      # through to the inner application; only the prefix (Ctrl+B) is intercepted.
      set -g prefix C-b
      unbind-key -a -T root
      # Re-add mouse-wheel passthrough (cleared by the unbind above).
      bind-key -T root WheelUpPane   send-keys -M
      bind-key -T root WheelDownPane send-keys -M
      # Disable Windows Ctrl+V paste interception.
      set -g paste-detection off
```

This is the exact schema a downstream repo adds to the requirement package that
owns PSMux provisioning. The `managed-block` strategy derives its markers from
`block` as `# >>> agent-worktrees mux keybinds (opt-in) >>>` /
`# <<< agent-worktrees mux keybinds (opt-in) <<<`, which match the block a prior
imperative `apply-mux-keybinds` script wrote by hand -- so adopting the resource
takes over the existing block seamlessly and the custom persistence script can
be deleted. The rest of `~/.psmux.conf` (the user's own `set -g mouse on` and
any hand edits) is preserved; only the marked block is engine-owned, and it is
refreshed idempotently on every restore. Setting `state: absent` on the same
resource removes the block cleanly.
