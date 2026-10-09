# Archive source discovery

`agent_logger.source_roots.iter_archive_sources(root)` is the common,
non-recursive entry point for an archive corpus. It recognizes source leaves
under these layouts:

```text
<flat-source>/
<host>.containers/<container-name>/
<short-repo>.codespaces/<codespace-name>/
.codespaces/<legacy-codespace-name>/
.codespaces-live/<live-mirror-codespace-name>/
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

## Session archive formats

The shared `agent_logger.sessions` registry supports `<id>.tar.gz` and
`<id>.zip`. Tar+gzip remains the default; ZIP is explicitly selected with
`archive_session(..., codec="zip")`. Existing selector sidecars remain
uncompressed and use the same session ID. A session ID must be one safe path
component; invalid archive stems fail instead of escaping sidecar, lookup, or
materialization paths. Compression formats never create extra logical sessions.

Library extensions register codecs in `sessions.CODECS`.
`archive_suffixes()` returns a unique, longest-first snapshot of the current
nonempty suffixes; `codec_for_archive(Path)` selects the longest matching
registered suffix, and `archive_stem(Path)` strips it or returns `None` for an
unrecognized filename. Registration, replacement, and removal affect subsequent
discovery and reads without rebuilding an import-time cache. Equal-length
matches are ordered by suffix and codec name. These helpers inspect filenames
only and never decode an archive.

A read-only `sessions.Codec` subclass implements `read_member`, `extract_all`,
and `list_members`; its inherited `archive_dir` rejects writes explicitly.
Comparing overlapping representations still requires `member_digests`:
an extension without content-proof support cannot authorize de-duplication or
retirement of an overlapping archive. A longer suffix cannot also resolve as
a second session ID by treating part of that suffix as the ID.

Both containers hold files relative to the session contents, without a leading
session-ID directory. ZIP accepts stored and deflated files, benign root
directory entries, and standard single-volume ZIP64 end records. Multi-volume,
encrypted, unsupported compression, special/link members, unsafe paths,
normalized duplicate names, and Windows case-fold collisions fail explicitly.
ZIP extraction creates new files only, never overwriting existing destination
evidence. Each member is decoded and verified in an exclusively owned, private
staging directory on the destination filesystem, then published with an atomic,
non-overwriting hard link. Failures remove only the staging directory, never
unlinking a caller-owned destination path. A concurrently created destination
is retained and reported as a conflict. Filesystems without hard-link support
fail explicitly; extraction does not fall back to unsafe pathname cleanup.
Previously completed members remain, so extraction is not an all-or-nothing
restore transaction. New ZIP creation uses a unique temporary file, verifies
file content and CRCs before replacement, rejects observed source changes, and leaves the
source directory intact. Settled-source selection and any source retirement
remain the caller's separately authorized responsibilities.

The shared archive-member and session-ID validator rejects Windows-invalid
characters, control characters, device basenames, and trailing dots/spaces on
every platform, including `CONIN$`, `CONOUT$`, and superscript-digit `COM`/`LPT`
devices. ZIP validates the original member name before the standard
library can truncate a NUL-containing name.
Both archive writers validate generated member names before publication;
generated names must already be canonical, so literal POSIX backslashes cannot
silently become path separators. Unsupported source filenames leave the source
and any prior archive intact.
ZIP readers still normalize benign names such as `./events.jsonl`; writer
canonicality does not prohibit content-equal reader-compatible representations.

Session archive publication checks every existing same-ID format before
updating selector sidecars or returning a reference to reclamation callers.
Conflicting or unprovable representations remain on disk and the live session
is retained. Verification also checks overlaps, so hub reconciliation cannot
retire a live directory based on one valid archive beside a divergent sibling.
Ordinary single-format tar verification remains unchanged.
Hub reconciliation counts logical sessions once in both dry-run and actual
removal, not once per format; failed removals do not count.
Removing one archive representation retains the ID's shared selector sidecars
while any other registered representation remains, even if that sibling is
unreadable. Removing the last representation also removes its sidecars.

ZIP descriptor mutation checks include size, mtime, and ctime. POSIX ctime
detects same-size in-place rewrites even if the writer restores mtime; Windows
ctime is creation time and does not provide that same guarantee. These checks
do not freeze concurrent writers or continuously pin mutable directory ancestors.

ZIP reads/writes allow at most 10,000 entries, 512 MiB per file, and 2 GiB total
decoded file bytes. ZIP readers and tar representation comparison also limit
physical compressed input to 2 GiB + 64 MiB before parsing. Tar comparison caps
bytes consumed by its sequential compressed reader even if the file grows
after the size check; ZIP admission caps each member's declared compressed size
and their total before content decoding. This bounds input work even for streams
that produce no decoded bytes. Ordinary single-format tar reads remain unchanged.
Tar comparison also consumes through gzip EOF to validate its trailer before
returning digests, with a separate 2 GiB + 64 MiB decoded-container budget for
members, metadata, and trailing padding.
Creation also bounds inspected source entries and excludes
linked/name-surrogate directories without descending into them. Source entries
are admitted incrementally before retention, rather than allocating an entire
directory listing before checking the limit; admitted batches are sorted for
deterministic traversal. The central
directory has a 16 MiB budget checked before the standard ZIP parser allocates
its index. Preflight scans and bounds the actual directory records, rejects
malformed framing and inconsistent counts, and does not trust the end record's
advertised entry count as an allocation limit.
File/directory conflict checks search sorted names by descendant
prefix rather than rebuilding every ancestor of deeply nested names; cost is
linear in name length and logarithmic in the bounded member count.
Equality comparison uses the same decoded-content budgets.
Tar comparison streams at most 10,000 raw headers,
including directories and extended headers; extended metadata has a cumulative
16 MiB budget enforced before its payload is decoded. Old GNU and PAX GNU sparse
encodings are rejected before their extent parsers can read or allocate
unbudgeted metadata. This restriction applies to representation comparison,
not ordinary single-format tar reads. ZIP verification derives
membership and integrity from one descriptor snapshot and returns false for
CRC, decompression, or truncation failures. Other content-read errors
and permission failures are not empty/missing evidence. These checks do not
claim a continuous descriptor-pinned transaction over mutable ancestor paths.

When two archive formats exist for one ID in one store, the reader compares
every regular member's size and SHA-256 before yielding an archive observation.
Identical readable contents produce one reference, preferring the legacy
tar.gz representation; divergent, corrupt, unsafe, or unprovable contents
raise while retaining both files. A valid live session still takes precedence,
and explicitly ordered archive stores retain their existing precedence.
Archive-store discovery uses two incremental directory scans, retaining only
the current ID's format references rather than materializing the store. The
first pass prevalidates observed overlaps before archive output; the second
revalidates each observation before yielding. There is no new store-entry limit
or global archive ordering guarantee. These scans do not freeze directory
changes between observations.
Across different source roots, provenance-based reconciliation and accounting
remain the consumer/backend's responsibility.

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

## Producer publication handshake

`agent_logger.source_publication` supplies the producer/destination handshake:

- `load_source_identity_file(path)` reads required metadata with the same
  bounded no-link loader as source discovery.
- `validate_publication_key(key, identity)` admits only the identity's canonical
  namespace. An explicit read alias is not authorization for a new write.

These are read-only producer helpers, not destination ownership admission. The
destination target must compare full source identity, reject collisions and
unowned nonempty leaves, and preserve aliases **under its existing sync lock
through all subsequent session/provenance writes**. This module deliberately
does not duplicate that target-owned write path or introduce a second lock.
Its availability alone does not wire namespace writers, claim destination
ownership, authorize a remote receiver, or authorize live publication.

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
and `.codespaces-live` and groups for dot-prefixed repositories such as
`.github.codespaces`. Live mirrors remain distinct physical observations from
close-out captures; source discovery does not silently merge them.

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
