# Configuration Layers

Dependency-free, read-only composition of **ordinary owner-validated preferences**.
It realizes the repository-topology vision's
[`layered-plugin-configuration`](../../visions/agent-fabric/agentic-factory/repository-topology/README.md).
It does not migrate consumers.

## API and boundary

Python 3.10+: import the `configuration_layers` package. Node 18+: import the named exports
from `configuration-layers.mjs`. Both expose the same record fields and functions:

- `plan_layers(context, layers)` validates, scopes, orders and detaches records,
  without filesystem access.
- `compose(context, layers)` returns `{values, provenance, layers}`. `layers` is
  the ordered selected ID list; `provenance` maps JSON pointers to winning layer
  metadata (including scope and optional source), with `values` omitted.
- `read_source(root, relative, ...)` reads exactly one caller-selected document.
  Python accepts keyword `parser=None, optional=True`; Node accepts an options
  object `{parser, optional: true}`. It returns `{values, source}` or `None` /
  `null` for a genuinely absent optional file. `parser(text)` is the owner-native
  parser; the default is strict JSON rejecting duplicate keys.
- `resolve_path(result, pointer)` resolves a winning string leaf relative to its
  declaring document's directory and owning source root. Built-in/source-less
  values cannot supply source-relative paths.
- Invalid data raises `ConfigurationError` in both languages.

Imports do not read configuration, resolve identity, write, spawn, discover,
install or acquire anything. There is no CLI, environment selection, binding
registry, uniform file discovery, cache, state store or service. The SDK does not
grant permission or interpret credentials, protected policy, activation,
installation receipts, registrations, descriptors or resource/state records as
authority. **Owners must select eligible ordinary preferences through their
native parsers and retain every independent veto/admission/custody check.**

## One owner-supplied contract

```json
{
  "context": {
    "repository_id": "repo-qualified-example",
    "execution_machine": "logical-worker",
    "installation_cell": "cell-qualified-example",
    "binding": "unbound"
  },
  "layers": [
    {
      "id": "builtins",
      "tier": "defaults",
      "origin": "defaults",
      "values": {"view": {"compact": false}, "items": []}
    },
    {
      "id": "selected-repository",
      "tier": "repository",
      "origin": "base",
      "repository_id": "repo-qualified-example",
      "values": {"view": {"compact": true}}
    }
  ]
}
```

The executable field validation in both modules is the contract, not a new
user-config schema. Owners adapt their existing documents into these records;
no new user registry is required.

Context requires opaque, nonempty **already-resolved** `repository_id`, logical
`execution_machine`, `installation_cell`, and `binding`. IDs match exactly,
case-sensitively; neither checkout basename, query-target repository, hostname
nor human-readable alias substitutes for identity. The caller owns each
identity namespace. `base_root` and `knowledge_root` are optional absolute source
roots; `bound` requires `knowledge_root`. `unbound` excludes knowledge layers and
forbids a knowledge root. `unresolved` always errors, including with no layers:
an explicitly configured but unresolved binding is not optional absence.
Multiple/converged repository roles require no new pairing engine.

Each layer requires unique `id`, `tier`, `origin`, object `values`. Optional
`source` is exactly `{root: absolute_path, path: safe_relative_document_path}`.
For selected base/knowledge layers, source roots must agree with the corresponding
context root when supplied. Source-less owner-parsed values are supported, but
cannot claim file-relative ownership. A knowledge layer always needs an admitted
bound context. Source paths describe selected native canonical **or legacy**
documents; the native owner chooses between them and handles malformed-present
sources, rather than trying both until one happens to parse.

Low to high:

| Tier | Origin | Required exact scope |
|---|---|---|
| `defaults` | `defaults` | none; file sources and scopes forbidden |
| `repository` | `base`, then `knowledge` | `repository_id` |
| `machine` | `named_machine` or `local_machine` | `execution_machine` |
| `repository_machine` | `repository_machine` | `repository_id` and `execution_machine` |

`installation_cell` is an optional additional exact selector on any non-default
layer. Cell-scoped records cannot bleed into a distinct installation; explicitly
cell-independent ordinary portable preferences can intentionally apply to both.
All other scope fields are forbidden. Duplicate/conflicting IDs, including on
excluded records, error. Multiple legitimate fragments use distinct IDs.

Input order is preserved within each tier/origin; all base repository records
precede all knowledge records at the **same repository tier**. Named portable
machine fragments and machine-local settings retain their separate origins and
explicit owner-supplied order, without assuming one is a registry for the other.
CLI/environment selections remain outside composition: apply them afterward
under native validation, not by manufacturing a fifth configuration tier.

## Merge, provenance and paths

Mappings merge recursively, including empty mappings. `{}` does not clear an
existing mapping; it replaces a non-mapping with an empty mapping. Lists, scalars
and `null` replace the whole prior value. Lists have one atomic winning provenance
entry, not individual element ownership. Empty mappings are terminal provenance
entries; merging an empty map over an existing empty map retains the old owner.
Replacing a subtree removes obsolete descendant provenance. JSON-pointer escaping
preserves keys containing `/` and `~`. Inputs/results are detached JSON data.

No alias resolution or special list policy is invented here. Owners requiring
domain-specific unions or aliases must normalize native layer values before
composition and preserve/document that domain contract. A future narrowly scoped
hook needs a concrete adapter requirement; this foundation adds no speculative
policy engine.

Both implementations reject cyclic data, non-JSON values, non-finite/oversized
numbers (magnitude above `2^53-1`), unpaired surrogates, malformed records, unknown
contract fields and nesting beyond 128. Node also rejects sparse arrays,
accessors, symbol keys and hidden object properties.

Requested document paths reject absolute/drive/UNC paths, empty/dot/traversal
segments, control characters, alternate streams and Windows-reserved aliases.
Source-owned value paths may contain `.` and `..`: normalize them against the
declaring document's directory, then enforce physical owning-root containment.
For example, `config/prefs.json` may reference `../assets/file`, but not
`../../outside/file`. Absolute/drive/UNC paths, empty segments, control characters,
alternate streams and reserved aliases remain invalid even in value paths.
The explicit reader and path helper resolve existing filesystem prefixes and
reject links escaping the owning root, even when the final file is missing.
Broken links, unreadable files, directories, invalid UTF-8 and parser errors are
visible failures. No parent-directory search, filename fallback, expansion of
`~`/environment variables or credential reads are performed. Roots must already
exist for physical resolution. Paths normalize using the local OS; field/value
semantics otherwise agree across languages.

These read helpers are **not a race-proof filesystem sandbox or permission
boundary**: an adversarial concurrent rename can race ordinary filesystem reads.
Owners retain trusted-source custody and any stronger handle-based admission.
Returned provenance is data, not an identity or authorization receipt.

## Existing owner seams (no all-or-nothing Worktrees dependency)

- Worktrees
  [`config_sources.py:15,34,61`](../../plugins/agent-worktrees/src/agent_worktrees/config_sources.py)
  exposes `ConfigSource(anchor, origin)`, `config_source_anchors` and
  `knowledge_config_source`. These are owner adapters, not imports required by
  this library. Its existing base → machine → knowledge order must **not** be
  reused; adapt the resolved values to the ordering above. Its knowledge reader
  is conditional on the launch repository's external-state requirement; an
  adapter must preserve explicit binding failures rather than treating a returned
  `None` as proof no binding was configured.
- [`project_state.py:24,102`](../../plugins/agent-worktrees/src/agent_worktrees/project_state.py)
  owns `RepositoryIdentity` and `_identity`: normalized registered remote digest,
  `repository_id`, marketplace identity and cell-relative state locations.
  It depends on Worktrees registry/installation context. Pass canonical identity
  fields from an owner-validated result; do not copy its digest/registry resolver.
- [`state_root.py:52,499,632`](../../plugins/agent-worktrees/src/agent_worktrees/state_root.py)
  owns `StateRoot`, `resolve_state_root` and `resolve_pair`; failed resolution has
  `path=None` and `error`, distinct from optional unbound preferences.
  [`knowledge_plugins.py:46,863`](../../plugins/agent-worktrees/src/agent_worktrees/knowledge_plugins.py)
  owns `KnowledgePair` / `resolve_pair` and overlay integrity/lifecycle. Consume
  admitted roots as data; do not export overlay lifecycle into the composer.
- [`git_ops.py:94`](../../plugins/agent-worktrees/src/agent_worktrees/git_ops.py)
  owns anchor selection and Git probes, which remain behind the native adapter.
- [`installation-context`](../installation-context/README.md) already owns
  standalone cell/source identity validation. Payload-only consumers can pass
  its admitted context fields without importing the Worktrees runtime.
  [`worktree-identity`](../worktree-identity/README.md) already decodes explicit
  logical execution-machine metadata; it does not establish local authority.

The dependency-light extractable seam is **validated data**, not those entire
Worktrees modules. Payload-only consumers still need an owner-provided resolved
binding/identity adapter; this SDK does not discover one or guess a legacy ID.

## Vendoring and qualification

Python uses static setuptools `src` packages with a dependency-free manifest,
matching the installer's existing editable-source retention policy.
Node is a standalone ES module using only native `fs`/`path`. Source consumers
may use editable references; eventual release materialization must copy the
canonical module appropriate to each consumer, following
[existing vendoring conventions](../worktree-identity/README.md).
No plugin enrollment, runtime build, host adoption or installed payload change
is performed here. Consumers and shared test runners enroll independently.

Run from the repository root:

```text
python -m pytest -q -p no:cacheprovider libs/configuration-layers/tests
ruff check --select F,E9 libs/configuration-layers
```

Tests use synthetic data and owned in-tree scratch directories, cleaned on exit,
with a bounded real Node import/subprocess (`CREATE_NO_WINDOW` on Windows).
Fixtures cover ordering, unbound/unresolved binding, exact repository/machine/cell
scope, machine-origin separation, empty-map/list/null conflicts and provenance.
Native filesystem checks cover source ownership, malformed/absent files, unsafe
paths and symlink escapes. Symlink creation inability is explicitly skipped;
it is not silently treated as qualified. No installer, credentials, services,
remote venue or consumer-migration lane is exercised by this foundation suite.
