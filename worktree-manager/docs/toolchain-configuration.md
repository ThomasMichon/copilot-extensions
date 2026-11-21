# Adopter toolchain configuration

The standard native `bootstrap.ps1` and `bootstrap.sh` accept an optional
data-only configuration before discovering or acquiring uv or Python. An adopter
can distribute this file independently of its repository, including before
private Git access. No adopter initialization script is needed.

## Input and selection

The file is plain ASCII text (no BOM), at most 16384 bytes, with LF or CRLF
line endings. It contains one unquoted `key=value` per line,
with no whitespace around `=`. Empty lines and lines beginning with `#`
(optionally indented) are ignored. Duplicate keys, unknown keys and unsupported
schema revisions are errors. Values are literal data, never shell expressions,
environment interpolation or executable configuration.

```text
schema=1
python_index_url=https://packages.example.invalid/simple/
npm_registry=https://packages.example.invalid/npm/
```

`schema=1` and at least one source are required. Supported source fields:

| Field | Native consumer | Persistence |
|---|---|---|
| `python_index_url` | `UV_DEFAULT_INDEX` and `PIP_INDEX_URL` | Effective uv default index and pip `[global] index-url` in the user or explicitly selected file |
| `python_install_mirror` | `UV_PYTHON_INSTALL_MIRROR` for managed CPython acquisition | User `uv.toml` `python-install-mirror` |
| `uv_install_mirror` | `UV_INSTALLER_GITHUB_BASE_URL` for the official uv installer | Retained input, reapplied by bootstrap/setup; no global environment edit |
| `npm_registry` | `npm_config_registry` for npm package consumption | User `.npmrc` `registry`, respecting an explicit native user-config path |

URLs must be HTTPS, at most 2048 characters, with an ASCII hostname, optional
numeric port and a path containing only letters, digits and `._~:/+-`.
User information, query strings, fragments, percent escapes and embedded
credentials are not supported. Authentication stays with each native tool's
credential owner. No certificate, trust-host or TLS policy is changed.

Selection order:

1. Leading `--toolchain-config ABSOLUTE_FILE` on the native bootstrap.
2. `COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG`, an absolute local file.
3. `.copilot-extensions/toolchain.conf` in the invocation directory, if present.
4. `toolchain.conf` in the selected `WORKTREE_MANAGER_ROOT`, if retained.

An explicit argument conflicting with the environment selection is refused.
An empty explicit argument is also refused before prerequisite commands; it
does not opt back into public defaults.
There is no parent-directory walk, repository clone, remote config fetch or
execution of adopter code. With no selected file, existing public bootstrap
behavior is unchanged. Normal `worktree-manager setup` uses the environment,
current-directory or retained selection too; its dry run only preflights it.

For example, after downloading the standard bootstrap using the normal trusted
source flow:

```powershell
& .\bootstrap.ps1 --toolchain-config C:\trusted\toolchain.conf setup --apply
```

```bash
bash ./bootstrap.sh --toolchain-config /trusted/toolchain.conf setup --apply
```

## Installed configuration-only application

An already-installed compatible Manager can validate the same selected data
without its original bootstrap or acquisition payload:

```text
worktree-manager setup --toolchain-only
worktree-manager setup --toolchain-only --apply
```

Selection still follows the environment, current directory and retained file
order above. The first command only preflights; `--apply` uses the same native
configuration writer. With no selection, both are successful no-ops.
Neither command discovers/provisions prerequisites, installs/repairs core,
sets up a repository, or changes aliases/knowledge binding. Configuration
success does not report application or engine readiness. Combining this mode
with repository options or `register` is a syntax error before configuration.
Ordinary setup is unchanged.

Version 1 of `copilot-extensions.toolchain-config-only` is advertised as the
standalone line `toolchain-config-only=1` in the exact selected Manager's
`setup --help` output. This existing discovery invocation returns before
reading configuration or provisioning, even with malformed selected data.
Callers must inspect that receiver with launcher self-provisioning disabled
before emitting the new option. A bootstrap declaration, a package version or
another Manager's help is not receiver support. Older ordinary setup help
does not advertise it; direct new syntax on an older receiver remains a
strict parser error, not a compatible configuration-only operation.
Select compatible setup/recovery rather than dropping required configuration
or retrying speculative effects. Existing callers retain their old syntax;
no old form is deprecated by this additive mode.

## Native configuration without Manager installation

A compatible standard bootstrap also accepts the existing configuration-only
setup operation before Manager construction:

```powershell
& .\bootstrap.ps1 --toolchain-config C:\trusted\toolchain.conf setup --toolchain-only --apply
```

```bash
bash ./bootstrap.sh --toolchain-config /trusted/toolchain.conf setup --toolchain-only --apply
```

Omit `--apply` for configuration preflight only. The native seed still performs
its normal uv/managed-Python and source preparation; this is not the installed
Manager's acquisition-free path. Source selection and package admission are
unchanged. Acquisition uses a separate owned directory, not an application's
shared staging path. Successful preparation removes that directory; a failed
invocation retains it for diagnosis.

The standard Python entry dispatches the same setup/configuration owner before
Manager version construction, command activation, core setup or installation
queue work. Only native configuration and its retained selection are applied;
configuration success establishes no application readiness. Strict option
parsing and the installed operation's repository/register restrictions remain.

Before using this first-touch form, require `bootstrap-toolchain-only` in the
exact bootstrap's version-1 `--describe-toolchain-config` capability list.
An older bootstrap can install Manager before forwarding setup, so its support
for the data file or the installed setup mode is insufficient. The compatible
bootstrap also requires `toolchain-bootstrap-only=1` from its fetched Python
entry before invoking it. Unsupported bootstrap/source combinations refuse
instead of constructing Manager, falling back or omitting the selected data.
Ordinary setup and existing installed configuration-only reuse are unchanged.

## Ordering, native configuration and conflicts

The native seed parses the file and preflights affected environment and native
source settings before prerequisite commands. The selected environment reaches
the official uv installer, managed Python acquisition and standard setup's child
processes. Once the standard Python installer entry runs, it fully parses the
affected native configuration before writing any file or installing packages.
It preserves unrelated native options and comments, adds missing defaults,
and retains the input under the Manager root for subsequent bootstrap/setup.
Pip option indentation is retained so existing options are not turned into
continuations of the added index. Repeated application is idempotent.

An existing explicit index, registry or mirror must agree with the corresponding
selected source (a trailing slash is immaterial); it is not silently replaced.
Pip checks include effective global, legacy/user, active virtual-environment and
explicit configuration, including command-specific sections and either INI
option delimiter, plus inherited INI defaults. An existing `PIP_CONFIG_FILE`
suppresses the normal user files; setup persists into that selected file
instead, preserving its options. Darwin
uses pip's native Application Support locations, with its normal user fallback.
A matching legacy uv `index-url` or a single matching default `[[index]]` is
preserved. That default must not be `explicit = true`: uv would then reserve it
for explicitly pinned packages rather than ordinary dependency resolution.
Such a selection requires reconciliation; an explicit `false` is preserved.
Uv checks include `[pip]` selectors and the effective system file
(the first discovered XDG system file, otherwise the platform system file).
System configuration is never modified, and an existing matching system default
does not cause setup to append a second default index.
The no-Python native preflight refuses source-bearing inline/dotted uv pip
forms rather than guessing; use ordinary `[pip]` tables for that path.
Additional/opaque selectors such as extra indexes, find-links, scoped npm
registries, explicit `UV_CONFIG_FILE`, or conflicting uv installer download
overrides require reconciliation and are not cleared. Errors start no package
installation. Native and retained configuration writers take the existing
bounded per-file locks before reading and merging, then publish by atomic
replacement. Cooperating writers therefore preserve unrelated settings written
before lock acquisition; arbitrary external editors do not participate in this
locking protocol. This is not a multi-file transaction or rollback of a later
failed installation.
Setup includes controlled validation reasons in refusals; parser exceptions
remain categorized rather than echoing native file contents.
An explicit pip configuration that disables discovery (`NUL` or `/dev/null`)
is also refused. Relative native pip/npm config paths are anchored before
bootstrap changes directories.
PowerShell resolves those paths through its current filesystem provider
location, not an independently inherited .NET process directory; preflight
reads the same resolved file. Native PowerShell also checks both npm environment
spellings on POSIX, refuses conflicting file aliases and anchors each selected
alias before changing directories.
Checks stay channel-specific: selecting only a managed-Python mirror preserves
independent package-index settings. Explicit uv discovery overrides are still
refused for either Python channel, since they can bypass the persisted file.

This is a small defaults provider, not an organization policy engine. It does
not classify an alternate endpoint as approved, discover credentials, replace a
native source profile, or change artifact qualification and dependency
seasoning. Organizations supply their own values; generic defaults remain
organization-neutral.

## Queued installation and update

When configuration is selected, the existing execution request captures its
selection and the validated effective native routing selectors, including
defaults derived from the data file and the absence of other selectors.
This keeps file-only mirror values intact when a downstream participant
restores the original request rather than trusting its inherited environment.
Windows environment names are normalized before capture, including a registry
derived only from the file; POSIX lowercase npm selectors use the explicit
uppercase request fields described below.
Participants restore those inputs before configuration preflight rather than
inheriting a different draining worker's selection. Relative native config paths
are anchored at submission. Lowercase npm selectors are preserved through
explicit uppercase request fields on POSIX.

This uses the execution owner's existing safe request context; it does not
expand the runner's Python-import-only environment mapping or copy arbitrary
environment/token variables. URL selectors containing embedded credentials,
queries or fragments cannot enter this durable routing context; use the native
credential owner instead. Credential-bearing file contents are not copied.
No-config requests retain their older context baseline. A configured request
requires a product receiver supporting this request-context contract; do not
strip mandatory inputs to make an older strict receiver accept it.
The selected engine's existing `update --inventory --json` response advertises
`toolchain_request_context` with schema
`copilot-extensions.toolchain-request-context` and integer version `1`.
Manager checks that exact engine before handing off a configured update,
including an existing queued participant. Missing or unsupported metadata
refuses the engine update before execution and directs setup/recovery; ordinary
no-config updates do not require the new declaration. Windows `self-install
--apply` participants also apply the selected configuration under installation
custody before constructing a candidate.
Ordinary configured Manager updates apply it under the same queue custody
before project-controller handoff or any update phase, even when Manager is
already current. Configured component-targeted updates enter that Manager
participant too; no-config requests retain their existing direct routing.

## Channel and compatibility limits

A Python **package index does not control Python, uv or Node binary downloads**.
The mirror fields above are separate opt-in channels supported by uv's native
owners. The bootstrap still obtains the official uv installer script through
its existing URL. Node acquisition remains with the existing selected
prerequisite/application owner; this contract does not add a Node binary mirror
or claim that an npm registry controls Node installation.

`--describe-toolchain-config` on the selected native bootstrap describes
`copilot-extensions.toolchain-config` version 1, its supported fields and the
`request-context-v1` capability.
Callers must establish this support before sending the new leading option.
The bootstrap additionally requires the fetched standard Python installer's
`toolchain-config=1` and `toolchain-context=1` capability markers when
configuration is selected; an older source is refused rather than silently
dropping persistence or queued routing. No-config callers
retain their old syntax and do not require this capability.

Release provider support before migrating adopters. A source merge is not a
release or an installed rollout. When rolling back to an older provider that
does not support this contract, do not silently omit mandatory configuration or
retry an effectful unsupported invocation with a different syntax. Select a
compatible release through the existing source owner instead.
