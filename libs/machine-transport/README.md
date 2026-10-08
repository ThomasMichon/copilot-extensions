# agent-machine-transport

Shared ``machines.yaml`` registry model, canonicalized local-machine
identity, and local-vs-SSH transport resolution, vendored (shared, not
duplicated) across every Copilot CLI plugin that dispatches a command to a
named machine.

## Why this exists

Before this library existed, `agent-worktrees`, `worktree-manager`, and
`agent-bridge` each defined their own `MachineEntry`/`SSHEnvironment`
dataclasses and their own `machines.yaml` parser, and several plugins
independently reimplemented "is this machine name the current one?" as a
bare string comparison against a configured machine identity. That bare
comparison only matches when the two sides happen to be spelled identically
-- a registry key vs. its alias, a raw COMPUTERNAME recorded before the
`hostname:`-field decoupling landed vs. the current canonical alias, or (the
bug that motivated this consolidation) a cloud/devtunnel-provisioned box
whose real OS hostname differs entirely from its configured alias. A
mismatch there silently concludes "remote" and dispatches an unnecessary --
and sometimes self-looping, over the very SSH/devtunnel mesh being
resolved -- network call to reach a host that is actually the caller itself.

## What this library owns

- **`registry`** -- `MachineEntry`/`SSHEnvironment` dataclasses,
  `parse_machines_yaml()` (resolved raw data), `parse_machines_yaml_file()` (one file),
  `merge_machines_yaml()` (additive
  legacy+canonical merge), and `find_machine_entry()` (key/alias/hostname/
  display_name matching, case-insensitive).
- **`identity`** -- `is_local_machine()`: the canonicalized "is this the
  local machine?" check, checking a direct `config_machine` match BEFORE
  ever loading the registry (so a registry outage doesn't mask a known local
  alias), with every comparison gated on a non-empty value.
- **`execution_identity`** -- `resolve_identity()` /
  `resolve_machine_identity()`: ambiguity-checked execution-key resolution
  that qualifies WSL guests as `<host>-wsl`, treats `display_name` as
  metadata rather than identity authority, and warns when a guest falls back
  to a qualified explicit identity because topology has no dedicated guest
  entry.
- **`transport`** -- `resolve_ssh_target()` (pick the best SSH alias/shell
  for an entry), `wrap_remote_command()` (POSIX login-shell wrapping via
  `remote_login_shell`, non-POSIX shells passed through untouched), and
  `get_machine_transport()`, the one-stop local-vs-SSH decision.

## What this library deliberately does NOT own

- **Resolving *which file* `machines.yaml` lives at** for a given repo
  (canonical in-repo path vs. legacy repo-root fallback vs. a bound
  knowledge-repo overlay). That resolution differs enough between consumers
  (agent-worktrees' knowledge-overlay redirect has no equivalent anywhere
  else) that forcing one shared policy would be a behavior change, not a
  pure de-duplication. Each consumer keeps its own path-resolution wrapper
  and calls `parse_machines_yaml_file()`/`merge_machines_yaml()` once it has
  resolved concrete paths.
- **Caller-specific remote-command shapes** -- a picker's own
  `pwsh -NoProfile -WindowStyle Hidden -EncodedCommand` construction, or an
  ACP bridge's breadcrumb-logged launch argv, stay local to their own
  consumer. `wrap_remote_command()` covers the one documented, genuinely
  shared POSIX-login-shell wrap.

## Usage

```python
from machine_transport import get_machine_transport

plan = get_machine_transport(
    "aurora-cloud2",
    config_machine=config.machine,
    load_entries=lambda: load_machines_yaml(config.default_repo.anchor),
)
if plan.local:
    ...  # run in-process
elif plan.resolved and plan.ssh_alias:
    argv = ["ssh", plan.ssh_alias, plan.wrap("agent-worktrees --version")]
else:
    ...  # unknown or unreachable machine
```

`require_alias` on `parse_machines_yaml_file()` controls whether an
`ssh.environments[]` entry with a `name:` but no `alias:` is kept (with
`alias=""`, so a UI can render a disabled/unreachable tab) or dropped
entirely (for a CLI dispatch consumer that always treats "present in
`ssh_environments`" as "has a usable alias"). Picking the wrong one for an
existing call site is a real behavior change -- preserve whichever that call
site already required before switching to this shared parser.

The raw-data parser additionally offers explicit compatibility policies:
`default_ssh_alias_to_key`, `default_ssh_shell`, and
`keep_unnamed_environments`. Bridge opts into key aliases, `bash`, and retaining
unnamed environments to preserve its legacy behavior; file parsing and other
consumers keep their existing defaults. These policies fill missing fields only,
not explicitly empty aliases or shells, and do not change normalization of
unrelated explicit fields. Bridge separately opts into
`preserve_environment_values=True` for its historical raw environment values;
this is independent of the missing-value policies. `SSHEnvironment` also carries optional
`port` and `user` metadata for consumers that need it.

`find_machine_entry(..., reject_ambiguous=True)` rejects a non-exact identity
shared by multiple machines with `AmbiguousMachineError`, a `ValueError`
subclass that lets API consumers distinguish ambiguity from a missing entry
without parsing diagnostic text. Exact registry keys retain precedence. The default
remains first-match for existing consumers. Bridge opts into strict identity
matching and separately binds SSH aliases to their precise environment: that
consumer-specific binding and default environment preference remain outside
this library, as do its authentication hooks and ACP command shapes.

## As a Dependency

In your plugin's `pyproject.toml`:

```toml
dependencies = [
    "agent-machine-transport",
]

[tool.uv.sources]
agent-machine-transport = { path = "../../libs/machine-transport", editable = true }
```

## Vendoring

**In dev**, consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer -- `path = "../../libs/machine-transport",
editable = true` -- so those consumers resolve to this one source tree with
nothing to keep in sync.

**At release**, `tools/materialize_main.py` rewrites every remaining
`uv`-editable pointer into a real, promoted copy at
`plugins/<plugin>/libs/machine-transport/` for that consumer, non-editable,
so a published consumer installs a self-contained source tree with no
cross-plugin `path` reference. `tools/sync-vendored-libs.py --check` verifies
every materialized copy's `src/` tree and version stay byte-identical to this
canonical one and to each other.
