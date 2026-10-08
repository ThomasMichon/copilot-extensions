# Archive source discovery

`agent_logger.source_roots.iter_archive_sources(root)` is the common,
non-recursive entry point for an archive corpus. It recognizes source leaves
under these layouts:

```text
<flat-source>/
<host>.containers/<container-name>/
<short-repo>.codespaces/<codespace-name>/
.codespaces/<legacy-codespace-name>/
```

Each source leaf has the same optional `session-state/`, `archived/`,
`provenance/`, and `logs/` children. Existing flat provider namespaces are
retained as flat observations; a name alone never proves their host, provider,
or repository.

## Reader contract

The iterator yields `ArchiveSource` records:

- `key` is the corpus-relative source path, including the group and leaf for
  provider namespaces. It is a presentation/location key, not a billing or
  logical-session identity.
- `path` is absolute and anchored at discovery, independent of later changes
  to the working directory.
- `layout` describes the flat/container/CodeSpace layout.
- `identity` is a producer-recorded `SourceIdentity`, or `None` when there is
  no source metadata. Unknown identity is not guessed from a legacy name.
- `legacy_aliases` retains explicit producer alias declarations. Discovery
  neither moves aliases nor merges their contents.
- `iter_sessions()` delegates to the existing live-preferred session/archive
  reader. It validates live refs before they can shadow archived evidence and
  rejects linked or non-regular events, archives, and optional filesystem
  sidecars. It preserves the existing registered session codecs.
- `iter_process_logs()` delegates to `iter_process_log_refs`, retaining raw,
  gzip, and ZIP observations without silently merging representations.

Optional missing stores contribute no physical observations. This is not
evidence of zero usage, complete coverage, or a final session. A corrupt or
unsupported archive fails when its existing codec reader consumes it.

Two physical roots with the same recorded source identity remain two
observations. Consumers must reconcile compatible session/event evidence,
preserve divergent versions, and retain their own workflow state; the iterator
does not implement accounting or catalog migration.

The chronicler uses this enumerator and retains full relative source keys,
including both the host group and container leaf. Its settle, journaled,
replacement-generation, and rescue-snapshot gates remain in the existing
session-source seam. Unsafe live or archived observations now raise explicitly
rather than disappearing from the scan; in particular, a linked live session
cannot silently hide an archived session with the same ID.

## Optional identity metadata

A source leaf may contain `.archive-source.json`:

```json
{
  "schema_version": 1,
  "venue_kind": "codespace",
  "provider": "github",
  "host": null,
  "repository": "owner/repository",
  "venue_name": "example-box",
  "legacy_aliases": [".codespaces/example-box"]
}
```

`SourceIdentity` requires a provider plus:

- `machine`: a host, without a repository or venue name.
- `container`: a host and container venue name, without a repository.
- `codespace`: a full provisioning `owner/repository` and venue name, without
  a host. The directory group uses only the repository's short name.

`identity.namespace` returns the canonical presentation key.
`identity.source_id` hashes the complete structured identity, independently of
the source's current physical location. Same-short-name repositories from
different owners retain different identities; distinct CodeSpace leaves can
share the short-repository group. The provisioning repository is not a
session's subsequently selected work repository.

The loader validates metadata against the provider layout and requires the
physical key to be either the canonical namespace or an explicitly declared
alias. Legacy flat locations can carry the same identity without changing their
path. Metadata and
alias declarations are attribution evidence, not usage meters or proof that two
session versions are compatible. A producer must establish source ownership and
collision safety before writing or changing metadata.

## Failure and platform boundaries

Discovery rejects traversal, unsupported path components, symlink/name-surrogate
reparse directory chains, non-regular identity metadata, malformed metadata,
inconsistent qualified identity, and case-fold collisions on Windows.
Portable components are ASCII letters/digits/underscore/hyphen/dot, with no
trailing dot or Windows device basename. Names are rejected rather than lossy
slugged. Metadata reads and directory/source counts have explicit budgets.
Permission errors propagate; unsafe evidence is not a successful empty result.
Ordinary top-level hidden housekeeping directories are not source roots;
declared provider groups are the exception, including legacy `.codespaces`
and groups for dot-prefixed repositories such as `.github.codespaces`.

A discovered source's directory identity and metadata file stamp are checked again before leaf reading,
and the chronicler checks it again before retaining a scanned batch. These
checks do not provide a continuous, descriptor-pinned transaction across all
mutable ancestor directories. The leaf readers retain their own documented
platform guarantees; see [process-log evidence](process-log-evidence.md).

This reader change does not roll out new namespace writers or migrate legacy
archives. Writer publication, compatible consumer adoption, provenance-based
alias reconciliation, and explicit live-change authorization are separate gates.

## See also

- [Architecture](architecture.md)
- [Process-log evidence](process-log-evidence.md)
