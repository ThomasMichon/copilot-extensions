# Explicit local owner-label migration

Alias compatibility does not rewrite ownership. A registered execution-space
key, hostname or transport alias is not permission to take a foreign ledger.
The dedicated doctor scope below is an explicit repair for **one unlinked local
record**, not a fleet rename, record relocation or general ownership transfer.

Use the attributable `agent-worktrees` command from the session command catalog.
Select the receiving registered project and exact historical record ID:

```text
agent-worktrees -p example doctor --migrate-owner historical-id --from-machine old-host --to-machine local-space --json
```

Review the JSON report. `eligible` means the selected record passed provenance
and dependency checks; nothing was written. Repeat the identical command with
`--fix` to apply explicitly:

```text
agent-worktrees -p example doctor --migrate-owner historical-id --from-machine old-host --to-machine local-space --fix --json
```

Neither ordinary `doctor --fix` nor a label flag without this selector can
perform an owner migration. There is no wildcard, bulk default or implicit
project selection across records. A separate exact selection is required for
each record. Other doctor repair scopes cannot be combined.

## Admission and preserved identity

- The new label must exactly equal the receiving configuration's canonical,
  explicitly declared execution-space key. Its platform and the record's
  platform must equal the actual execution platform.
- The old label must be a registered key, hostname, identity/compatibility alias
  or SSH alias belonging **only** to that same entry. Key-versus-alias conflicts,
  foreign entries, legacy combined projections and display-name-only evidence
  are refused. Physical grouping is never authority.
- The receiving repository must be canonically registered as worktree or
  knowledge, and its configured anchor must match the registered local root.
  Record identity and `repo` must match the selected tracking store.
- The absolute checkout must equal the registered worktree namespace plus the
  unchanged record ID, with no symlink checkout or redirected descendant.
  A removed checkout is eligible with this same registry, platform, exact-record
  and namespace proof; directory existence alone is neither required nor proof.

Only the `machine` scalar changes. IDs, historical name prefixes, checkout paths,
branches, sessions, head, lifecycle, PRs, titles, flags, unknown fields, comments
and line endings remain untouched. Reapplying normally reports `unchanged`.

## Deliberate relationship fence

The report scans tracking stores from the canonical adopted-project registry
**and** all repositories registered locally for the current platform. This
includes knowledge-only paired repositories without a projects entry or local
config file. Malformed/unreadable peer ledgers refuse the operation rather than
pretending there are no references.

Owner, pair, controller, caller, service-owner, dispatch-attempt and external
execution-provider bindings are refused. Resource claims other than PR, issue,
bug and effort references are refused, including released historical resources.
Embedded historical qualified references and inbound references to either owner
spelling are refused, including canonical claim refs and references in another
project. Structured owner tuples and nested historical machine labels are
fenced too; they are not treated as opaque harmless text. Duplicate or misplaced
copies of the selected record also refuse.
Known caller/controller/owner/pair and typed resource references also fence matching bare IDs,
even without a machine qualifier; passive titles and notes do not. Producer-owned
claims and `handoff_bundle` reservations refuse. The separate claim-handoff intent
registry is inspected for source, consumer and claim endpoints, including retained
terminal bundles, so a record without ledger links is not assumed independent.
Nothing is relocated, pruned, settled, renamed or silently skipped.

These refusals require a deliberate relationship/provider-owned migration, not
hand-edited YAML or repeated force flags. This first scope does not implement
multi-record graph transfer or remote-provider ownership changes.

## Writes, receipts and interruption

Apply uses the existing installation publication authority and nonblocking
claim-handoff-registry and tracking sidecar locks, then repeats the complete inspection and compares its
input bytes before writing. A busy lock, changed inventory or lost provenance
refuses without changing the selected record. Current tracking writers fence
new ownership references and reject stale full-record saves that would restore
the old machine. Canonical repository-registration publication uses the same
authority, preventing a newly visible knowledge ledger from entering mid-repair.

**Quiesce writers running older code before explicit offline recovery.** Older
processes do not acquire newly introduced ownership fences; a source-only
invocation does not upgrade or stop them. This command never installs a runtime,
changes topology/configuration, starts/stops services or migrates a session.
Normal rollout remains the owning installation's responsibility.

The selected record receives an original-byte `.repair-<digest>.bak` backup and
an `.owner-migration-<digest>.json` audit alongside it, using a digest of the
observed original bytes. The audit retains exact old/new labels, checksums and
receiving configuration/namespace evidence. It is prepared before the one
atomic record replacement and marked `committed` afterward. No multi-file
transaction or cross-record atomicity is claimed.

Exit 0 reports `eligible`, `migrated` or `unchanged`; exit 1 includes an explicit
error. If final audit publication fails after replacement, JSON reports
`migrated-audit-failed` and `changed: true`, not a harmless refusal. An interrupted
`prepared` receipt is not proof that the record changed. Inspect its before/after
hashes and preserved backup; the command refuses an incomplete prior audit
rather than silently certifying or repeating uncertain recovery. Never discard
receipts, overwrite backups or restore a record with a text editor.
