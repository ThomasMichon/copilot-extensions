# Artifact storage policy

The Worktrees storage adapter composes a narrow version-1 policy through the
existing configuration-context and configuration-layers owners. Resolution is
read-only: it neither allocates a directory nor admits its privacy, filesystem
identity or cleanup. A selected path is not a writable allocation.

## Selected documents

Read only `.copilot-extensions/artifact-storage.json` in the owner-selected
base and, when explicitly bound, knowledge roots. The base document accepts:

```json
{"version":1,"portable":{"failure_evidence":{"max_files":16,"max_bytes":8388608}}}
```

The knowledge document additionally accepts a `machines` mapping. Each key is
the exact execution-machine identity supplied by the configuration owner; its
value can override `failure_evidence` and supply an absolute native
`scratch_root`. Physical paths are not portable fields. Other machine entries receive
structural validation but are never selected for this execution. Native absolute
path syntax is checked only for the executing machine, so one document may
contain both Windows and POSIX machine paths.

An existing-owner `repository_machine_root` may contain `artifact-storage.json`:

```json
{"version":1,"values":{"failure_evidence":{"max_files":8}}}
```

Its `values` may also supply `scratch_root`. This is machine-local state, not a
new binding, machine registry or portable repository setting.

Unknown fields, duplicate JSON keys, unsupported versions, malformed selected
documents and an unresolved named knowledge binding are errors. An absent
optional knowledge binding/document is not an error. Each contributing value
is validated before composition; a higher-precedence valid setting cannot hide
a malformed lower layer. Evidence limits are positive, non-boolean integers
within JSON's portable exact-integer range.

## Precedence and outputs

Low to high: private per-user OS-temp defaults, base policy, knowledge portable
policy, exact named-machine policy, repository-machine override, supported
`AGENT_SCRATCH_ROOT`, explicit operation root. An invalid explicit/environment
destination fails rather than falling back.

The OS-temp fallback is a private per-user scratch root; filesystem admission
belongs to the allocator. The current storage API reports `root_admitted: false`
and provenance for composed fields, plus the winning root origin. It rejects
roots inside the selected base, knowledge or supplied session-state home, but
does not replace the allocator's checks for other checkouts, Git metadata,
links/reparse points, privacy, ownership or concurrent replacement.

Defaults bound selected failure evidence to 16 files and 8 MiB per allocation.
These are engineering defaults, not automatic collection or permission to
retain arbitrary sandbox contents. The integrated producer must declare a
diagnostic allowlist, omissions/truncation and its process-settlement proof.
Reusable environments and caches are not disposable diagnostic bundles.

This source API does not change installed preferences, move old allocations or
authorize cleanup. Allocation custody and retained disposition use the
[resource-producer contract](resource-producer.md#retained-artifact-disposition).
The explicitly selected [runner pilot](artifact-scratch.md) adds private root
admission and native allocation without changing ordinary runner defaults.

## User-output policy

The same selected documents accept an optional `user_output` object. Portable
fields are `target` (`auto`, `onedrive` or `downloads`) and `templates`, keyed by
`onedrive` and/or `downloads`. Templates use relative `/`-separated portable
components and only `{date}` and `{topic}` substitutions, without formatting,
attribute access, traversal, absolute paths or device names. For example:

```json
{"version":1,"portable":{"user_output":{"target":"onedrive","templates":{"onedrive":"Deliverables/{date}/{topic}"}}}}
```

Only exact machine-scoped values may add `user_output.roots`, a mapping of target
to absolute native directory. The roots come from the existing machine/storage
owner, not from account discovery or environment inference:

```json
{"version":1,"values":{"user_output":{"roots":{"downloads":"/home/example/Downloads"}}}}
```

Use native Windows paths in a Windows machine entry. Other machine entries stay
inert, with the same foreign-platform structural validation as scratch roots.
An explicit operation destination wins without rewriting its basename or
appending organization directories. Otherwise the exact composed machine root
wins over a storage-provider root for the selected target. A portable preference
chooses the target and layout; absent preference defaults to an available
owner-resolved OneDrive, then owner-resolved Downloads.
An explicit or configured required target must resolve or fail visibly.

Default layouts are `Apps/Copilot/Artifacts/{date} {topic}` for OneDrive and
`Copilot/Artifacts/{date} {topic}` for Downloads. Changing policy does not move
old allocations. `resolve_user_output` performs no filesystem access or writes.
Native admission, exclusive allocation and verified handover belong to the
[user-output producer](artifact-output.md), not configuration composition.
