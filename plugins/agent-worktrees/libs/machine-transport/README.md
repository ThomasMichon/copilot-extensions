# agent-machine-transport

Shared ``machines.yaml`` registry model, canonicalized local-machine
identity, and local-vs-SSH transport resolution, vendored (shared, not
duplicated) across every Copilot CLI plugin that dispatches a command to a
named machine.

Registry file reads use PyYAML's safe C loader when available, with its safe
Python loader as the fallback. Every call opens and parses the current file;
no registry, identity or authority result is cached by this parser. Missing,
malformed and unsafe-tagged documents retain their normal failures.

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

- **`control_plane`** -- immutable `ControlPlaneRepositories`,
  `parse_control_plane()` and `load_control_plane()` read an explicitly selected
  harness/knowledge declaration. They preserve legacy self-hosted project
  declarations, reject conflicting launch projections, and report whether a
  pair includes the scalar projection that older daemons consume. They do not
  discover, register, bind or launch repositories.
- **`registry`** -- `MachineEntry`/`SSHEnvironment` dataclasses,
  `parse_machines_yaml()` (resolved raw data), `parse_machines_yaml_file()` (one file),
  `merge_machines_yaml()` (additive
  legacy+canonical merge), and `find_machine_entry()` (key/alias/hostname/
  display_name matching, case-insensitive).
- **`identity`** -- `is_local_machine()`: the canonicalized "is this the
  local machine?" check. Native callers retain the direct `config_machine`
  match before registry loading; guest callers first validate guest authority.
  A registered WSL key need not have a suffix. Qualified guest self names retain
  the absent/unscoped-registry fallback, never an explicit native entry.
  Every comparison is gated on a non-empty value.
- **`machine_environment`** -- `split_machine_environment()`: converts
  `<host>-wsl` and `<host>` plus `WSL` into the same physical-host/environment
  pair. It normalizes environment labels and rejects conflicting requests.
  This is a compatibility/presentation adapter, not ownership or filesystem
  authority: Windows and WSL still have distinct logical execution keys.
- **`execution_identity`** -- `resolve_identity()` /
  `resolve_machine_identity()`: ambiguity-checked execution-key resolution
  that qualifies WSL guests as `<host>-wsl`, treats `display_name` as
  metadata rather than identity authority, and warns when a guest falls back
  to a qualified explicit identity because topology has no dedicated guest
  entry. Its platform detector also treats container roots identified by
  Docker/Podman markers or `/proc/1/cgroup` as plain `linux` even when the
  shared kernel string mentions Microsoft/WSL, so containerized work on a
  WSL host never acquires a spurious guest identity.
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

### Current-machine context without enrollment

`resolve_local_execution_context(entries, *, hostname, environment,
configured_machine=None)` returns immutable `LocalExecutionContext` naming
facts: observed `hostname`, physical `machine`, actual `environment`, one-term
`execution_machine` and optional `customization_key`. The caller supplies the
current OS/substrate observations and, when applicable, its existing configured
binding. Missing-file handling and configuration provenance remain caller-owned.
An empty map or a map containing only other machines does not deny this local
context. A matching map can customize physical and execution names. An observed
physical hostname resembling `-wsl`, `-win` or a parenthesized environment label
is literal, not address syntax.
Physical grouping names are structured clean strings, not execution-reference
tokens. When a mapped physical label cannot form an execution token and the
current platform has no declared execution key, the observed hostname supplies
the fallback execution name (with `-wsl` for a guest), without changing the
physical label. This fallback uses the same collision checks as other local
execution names. Allocation callers still own portable path/Git-ref validation.

This is an additive API for the reviewed local-first correction, not an
activation of tuple producers or an automatic migration of existing consumers.
The existing target selectors and ownership fences retain their contracts.
In particular, a missing guest customization can yield local guest naming
facts here without making its native registry entry a guest target. Explicit
configured conflicts, ambiguous customizations and invalid declarations still
raise. Inferred binding qualifiers must agree with the actual platform; exact
registered keys, aliases and applicable display selectors retain precedence.
Do not supply a requested target or a record's stored owner as the
current configured binding.

An established guest binding remains usable when only its native customization
exists. This local adapter handles the registry's specific
`MissingExecutionSpaceError` (`ValueError` subtype); the target selector still
refuses the missing logical target. The exception retains the resolved
`physical_host`, so guest-qualified key, alias and display bindings keep the
mapped family rather than reverting to their input spelling. Ambiguous physical
families remain errors. Both configured and observed results check
execution-key collisions against keys, compatibility names and profile aliases.
Execution names are normalized before checking every colliding entry; trimming
or presentation conversion cannot bypass that check. The scan includes other
entries' projected execution keys, not just their original presentation labels.
Registry-key whitespace does not change a literal OS observation.
Configured native bindings also match trimmed legacy keys while retaining the
original customization key and key precedence over SSH profile aliases.
Multiple normalized keys remain ambiguous; foreign execution-key collisions
still fail. Contextual legacy display selection does not change
when unrelated scoped entries are present, and a selected physical family
constrains guest candidates even when another key resembles its shorthand.
An unnamed legacy SSH
profile does not assert a contradictory environment. Legacy null profile names
and aliases remain absent metadata, not fabricated identity labels or foreign
environment assertions; invalid selected non-string names raise `ValueError`.
Aliases of a literal observed host retain the same naming facts; an explicit
profile/environment conflict is still a refusal. Actual-platform entries take precedence over
unscoped native fallbacks, and a selected entry keeps its registry key rather
than regenerating it from aliases or physical metadata.
Locally associated legacy display-name bindings use the shared selector before
literal-host format handling; keys and aliases retain precedence, and ambiguous
local display lookups remain errors. Unrelated display metadata cannot replace
an established one-term configured execution identity. An explicitly multi-term
legacy display selector may resolve uniquely without hostname metadata.
A derived guest with no guest
declaration has no `customization_key`: borrowing a physical name does not
declare guest roles or loadouts.
Legacy parenthesized presentation keys are projected to one-term execution
names while retaining their original `customization_key`; explicit canonical
keys are not reformatted. Contextual normalization receives the literal observed
hostname so native/guest sibling labels cannot trim it before selection.
The optional `observed_hostname` keyword is current-OS context, not target
authority. Logical projection canonicalizes recognized profile environment
labels in copies; original raw inputs, machine keys, aliases and other
transport fields are retained. `known_environment_label()` classifies legacy
metadata without validating it; `normalize_environment()` remains the strict
requested-environment validator.
The same context is used for legacy hostname-address collision checks.
Mixed scoped/unscoped registries resolve an existing legacy guest before
generating a fallback, while named native profiles retain their environment
refusals even through qualified aliases. Profile environment labels use the
shared environment normalizer.
The Bridge adapter correlates missing-alias defaults with their exact source
profile, so canonical role names cannot fill an explicitly empty/null alias.

Consumers must separately prove the receiving project, namespace, ledger and
retained allocation before writing or replaying. New naming facts do not
authorize relabeling an admitted allocation, changing its references or
transferring an incumbent owner. Consumer integration and installed acceptance
remain tracked by the
[local-first amendment](../../efforts/active/machine-alias-compatibility-and-locality/manifest-independent-local-identity.md).

### Contextual historical aliases

`resolve_machine_selection()` is the shared address map; format parsing belongs
only to `machine_environment`. Case-insensitive keys, registered aliases and SSH
profiles are checked before legacy inference. Fallback forms include `-win`,
`-wsl`, `(Windows)`, `(Win)` and `(WSL)`, including mixed case and whitespace.
Hostname/display families require a supplied execution context when multiple
logical environments are possible. Conflicting inline and supplied contexts
refuse; an exact registered identity is never renamed because its spelling
resembles a legacy form.

| Slot | Context and authority | Regression grid |
|---|---|---|
| Config/detection and Machines discovery | Actual platform; configured selection remains distinct from automatic independent-space admission | `test_alias_matrix.py`, `test_machine_alias_matrix.py` |
| JSON/non-JSON launch, local creation/resume | Explicit environment or selected logical entry; exact SSH profile | `test_machine_alias_slots.py`, `test_resolve_seed_delivery.py` |
| Stored record, owner/claim ref, liveness, handoff and settlement | Record platform, actual execution and named receiving-project record/namespace proof | `test_machine_alias_slots.py` |
| Manager local roster and physical grouping | Same selected entry and explicit physical association; logical IDs and routes unchanged | `test_machine_environment_grouping.py` |

The grids cover native Windows and WSL contexts, key/hostname/alias/display
families, mixed case, legacy projected and explicitly registered records.
Stored machine/config/reference strings are not rewritten. A historical
projected host label can identify its WSL record only with full record proof;
an explicitly foreign registered space cannot borrow that exception, even on
the same physical host and platform. Unqualified ambiguous metadata without
an environment remains a refusal, not an implicit preference for Windows/WSL.

### Independent execution spaces

An entry may opt into execution-space identity with
`execution_platform: windows|wsl|linux`. Its registry **key**, not its alias,
hostname or display name, is then canonical. `physical_host` is optional
grouping metadata and never implies shared registration, settings or locality.
An explicit space cannot contain SSH environments for another platform.
Its key cannot contain whitespace, path separators or reference delimiters;
keys must be unique without regard to case.

Declare native Windows and WSL as separate entries, for example
`workstation` and `workstation-wsl`. Consumers select the current
registered key explicitly. Distinct entries do not become local merely because
their OS hostname matches. Physical-host metadata cannot resolve an explicit
space; ambiguous legacy names are rejected rather than bound to the first entry.

Existing qualified references retain their three-part syntax:
`registered-space-key/project/worktree-id`. Legacy registrations and records
remain readable; this does not rewrite or migrate their identities.
Registries without an explicit execution-space declaration retain their legacy
lifecycle behavior. Opted-in consumers reject invalid selection rather than
guessing from shared hostname metadata.

### Logical-machine owner projection

`normalize_machine_entries(entries: Mapping[str, MachineEntry]) -> dict[str, MachineEntry]`
is the shared, read-only owner projection. Apply it after parsing and merging
the resolved topology. A legacy Windows/WSL entry keyed `desktop` expands to
`desktop` (`execution_platform="windows"`) and `desktop-wsl`
(`execution_platform="wsl"`), both associated through `physical_host="desktop"`.
The native key stays canonical; each logical entry retains only its own exact
SSH profile. Explicit independent entries retain their configured keys and
physical association, even when they do not use the conventional suffix.
No arbitrary suffix is stripped or inferred. The projection is idempotent
and does not write topology or change persisted registrations.
Generated entries retain the original registry key in read-only `legacy_key`
metadata. Owners use that provenance to distinguish platform-aware legacy
projection from an operator's explicit execution-space selection; the latter's
configuration/platform safety gates remain unchanged.
Projecting a remote Windows/WSL host does not opt an unrelated legacy native
owner into operator-declared execution-space policy. Consumers still fence
operations targeting a projected space, and explicit declarations retain
their existing registry-wide source-selection requirement.

`machine_identity_names(entry)` exposes keys, aliases, explicit
`compatibility_aliases`, and profile aliases. Hostnames are selection identities
only for unscoped legacy entries; a WSL key qualifies its hostname for guest
lookup. Display labels are a unique-lookup compatibility fallback, never a
collision with a key or an alias. Logical-machine
physical metadata is for grouping, not an alternate canonical source/target.
Passing `entries=registry` includes a unique, unshadowed display label for an
unscoped legacy entry's package gates; scoped entries never acquire display-name
gate authority.
Duplicate logical keys, profile aliases crossing entries, repeated same-platform
profiles, and legacy WSL mixed with a non-Windows native platform fail closed.
Collisions with an explicitly declared generated key require repository-owned
topology repair, not an automatic merge.

`find_machine_entry(entries, name, *, reject_ambiguous=False,
execution_platform=None)` resolves independent profile aliases as well as keys.
An owner that knows its local platform may supply `execution_platform` to resolve
a physical hostname/association or select a sibling of another platform.
An explicit `physical_host` wins; otherwise conventional `<host>-wsl` keys
derive the host association for this context-aware lookup only.
Multiple eligible same-platform siblings remain ambiguous; an exact logical
key/profile of the requested platform stays independently selectable.
Without that context a physical hostname alone never selects a logical entry.
`local_execution_platform()` detects Windows, WSL, or Linux without launching
subprocesses or trusting inherited WSL environment variables. It uses the same
container-aware detector as execution identity. Downstream address syntax remains a logical machine name, not a
machine/environment pair.

The parser's compatibility defaults are unchanged: normalization is an explicit
owner step. `agent-machines.resolve_machine` uses this projection and exposes
`execution_platform` and `physical_host`; UI consumers can group by association
without collapsing the independently addressable entries.

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
without parsing diagnostic text. Exact registry keys retain precedence; strict
matching also prefers a unique case-insensitive registry key over aliases,
hostnames and display names. Colliding case-insensitive keys remain ambiguous
unless the input exactly spells one key. The default
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
## Observed local hostname fallback

`is_observed_local_name` recognizes this process's observed hostname when it
differs from its configured preferred identity and the registry does not map
that name. It is restricted to the actual execution context; registered keys,
profiles and physical associations always take precedence. This is a local
lookup adapter, not a new registry alias or a Windows/WSL equivalence. Persisted
record callers must still prove the receiving project, exact ledger, platform
and absolute namespace before applying that local spelling.
