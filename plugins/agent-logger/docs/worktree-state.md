# Typed worktree-state collection

Parent: [Logger architecture](architecture.md#session-sync).
Owner export: [complete consistent agenda export](../../efforts/skills/driving-effort-agendas/references/agenda-export.md).
Standing intent: [durable worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).

## Current source boundary

`sync.worktree_state_archive` supplies additive collection and fresh local
readback for an explicitly selected, owner-verified agenda export. It uses the
existing `Target` abstraction; only `LocalTarget.worktree_state_root()` opts in.
Other targets explicitly lack that capability, including OneDrive replicas.
This first source slice is a library boundary, not a new scheduled collection,
CLI, source crawler or installed integration. Ordinary session sync is unchanged.

The orchestration caller must first qualify the worktree owner's repository,
full worktree/incarnation and selected store, obtain the complete domain-owned
export, and negotiate the selected owner's complete-export verifier. The library
takes that exact typed identity and verifier; it never imports another plugin's
runtime or guesses a state home. Passive provider discovery is available
separately; export orchestration and existing-tick integration remain unfinished.

## Passive provider discovery

`sync.worktree_state_providers.read_project_inventory` accepts the aggregate
input owner's typed repository admission, retaining its exact authoritative
checkout rather than reconstructing it from diagnostic provenance. It reuses
Logger's existing native Worktrees command selection: the validated same-cell
peer launcher in a namespaced installation, or the completion-gated legacy
owner prefix without ambient PATH selection. Explicit refusal never falls
back to another owner.

The Worktrees process selects its own registered project from that checkout's
actual working directory, not Logger's ambient project or a repository-basename
alias. Its existing passive capabilities operation must advertise
`worktree-data-project-inventory-v1` before Logger sends `--inventory`. An older
or refused provider is unsupported, never permission to bootstrap a runtime,
scan another registry or fabricate an empty successful result.

The complete inventory must match the admitted canonical repository and
selected installation. Logger retains every registered agenda placement,
including retired worktrees with missing canonical records, while rejecting
partial, duplicate, foreign or over-budget results. Limits remain 128 worktrees,
32 stores per worktree and a 1 MiB response. Locations and registration fences
are observations, not proof of initialized domain contents or a collection grant.
The read does not open a domain database or allocate missing state.

`select_efforts_exporter` uses the canonical plugin-activation resolver for
that exact checkout plus its effective global settings. Explicit project
disablement, indeterminate settings and multiple matching marketplace identities
cannot become global fallback or first-provider selection. The selected ordinary
Efforts script must advertise both complete export and optional complete
contributor provenance through its actual `info` operation. Package versions or
another reader's support do not qualify it.

These are explicit passive library calls only. No CLI, scheduler, sync tick,
export, collection, migration or installed rollout is enabled. A later
orchestrator must freshly qualify current admission, registration and selected
provider support at its effect boundary; these observations are not a lease.

## Admission before effects

Collection requires an actual nonpassive, authorized **enforce-mode** aggregate
plan for the executing machine's complete name/platform/role identity, using the
same normalization as plan selection. A valid diagnostic/observe plan is not
permission. Exactly one collection claim must cover the canonical source
repository and bind the actual active target's filesystem root. The supplied
target must also match the current configured target. Native path equality
preserves case; the compiler's conservative case-folded collision identity alone
cannot grant access to a distinct case-sensitive path.

The selected sync configuration remains authoritative. Secret redaction or
essential-session-only publication blocks this lossless database collection;
Logger does not silently redact accepted history and call it complete. Legacy
session allow/deny/opt-in filters do not yet define typed-state admission and
therefore refuse this boundary. Claims with content profiles, landing,
retention or mutation policies also refuse until those semantics are actually
implemented here.

The initial adapter requires POSIX directory-relative publication and directory
barriers. Native Windows collection is explicitly unsupported, not inferred
from the existing owner's separate export support. The isolated Linux packet
does not qualify Windows or hardware power-loss behavior.

Collection roots must also be outside the reserved `session-state`,
`provenance`, `archived` and `.session-sync-*` payload/replacement components.
Such custom placements are unsupported even when a filesystem claim otherwise
matches: ordinary session retention is not persistent-state reclamation.

## Identity, publication and readback

One collection key binds canonical repository, **full** worktree identity,
worktree incarnation, agenda store and accepted revision. A hashed namespace
avoids path-shaped identifiers. The revision is immutable: a different export
at the same key conflicts instead of replacing previously retained evidence.
This also exposes a changed delivery-state cut at an unchanged accepted
revision rather than silently discarding either snapshot.

The top-level `worktree-state/` namespace has its own schema-v1 format marker.
It is claimed only when empty or already owned; an existing ordinary machine
with that name is neither adopted nor hidden. Fleet health excludes only a
positively identified collection namespace, so retained revisions do not consume
the machine-directory scan budget. Ordinary session pushes cannot enter that
owned namespace.
The session-shaped archive-source enumerator also excludes that positively
identified namespace instead of reporting it as an empty unknown machine.
It checks markers before expanding provider groups, including renamed collections,
so retained revisions do not consume the source-discovery budget.
An invalid group marker is reported through the optional per-entry error callback
without dropping healthy peers; group-enumeration and budget failures remain fatal.
Namespace claiming and potentially overlapping ordinary publication share a
domain guard held through session, process-log and archive publication as well
as heartbeat and mutating retention/compaction operations. Read-only maintenance
does not allocate a missing destination or archive directory. Native alias checks
reject case-insensitive equivalents without hiding genuinely distinct
case-sensitive machine directories. Non-directory entries are not probed as
collection namespaces during fleet discovery.
Admission checks every reserved destination prefix and every existing marked
ancestor, including prefixes above a configured target root. Marker recognition
does not depend on the directory retaining its original name.
Each admission inspects markers during one pinned ancestry walk rather than
reopening every prefix from the filesystem root. Checks are fresh for each call:
there is no cross-file absence cache that could hide a newly retained marker.
Ordinary unchanged-file admission still performs synchronous metadata checks;
this source qualification does not establish native or remote-filesystem latency.
Potentially overlapping writers take the guard inside the namespace directory,
not its parent, so an existing writable namespace-shaped root below a read-only
parent remains usable. Claiming may create that directory; only the internal
guard file is ignored when checking that a new claim is otherwise empty.
Collection claims cannot nest inside an owned or partial collection. Invalid
markers block mutation and are reported separately without dropping healthy
fleet peers. Prefix-count overflow refuses before destination creation; it does
not truncate the protected path.

Only the verified `agenda.sqlite3` and original `manifest.json` are collected.
Every new database, metadata and reference file requests mode `0600` in the
initial POSIX creation syscall, not a later permission change.
The owner's optional prepared manifest is not needed in a transported export.
Copying checks the exact declared byte count and SHA-256, with bounded chunks
and a complete-copy deadline. The selected owner then verifies the destination's
complete schema, ledger and immutable records. A receipt follows that readback,
never a partial copy or transport no-op.

Contributing sessions reference the same collection through additive,
content-checked metadata; the agenda is not copied beneath each session.
The original API requires a nonempty tuple of canonical session identifiers;
that validation and schema-v1 receipt are unchanged when no contributor reader
is supplied.
An exact retry verifies actual retained bytes afresh before adding references.
Conflicting or interrupted publications remain visible for owner-led
reconciliation; the library neither overwrites nor deletes them.

`readback_worktree_state` independently repeats admission and owner verification.
It does not trust a cached transfer result or receipt in place of stored bytes,
and a missing collection remains missing without creating directories.

The schema-v1 receipt binds the destination, exact source identity, manifest
digest and member size/hash. It reports **local readback verified** and
**remote archive acknowledgment false**. Neither a receipt nor a successful local
copy grants reclamation, retention changes, restoration, head ownership, claims,
leases or live execution authority.

### Optional complete contributor provenance

Both collection and readback accept a keyword-only `contributors` owner reader.
Before supplying it, the orchestration caller must negotiate
`agenda-export-contributors-v1` through the exact selected Efforts payload's
`info.optional_capabilities`. The callback returns that provider's complete
`efforts.agenda-export-contributors`, version 1 projection for the selected export,
not a live database or a session lookup. Missing support preserves the original
API; it cannot satisfy a requirement for complete attribution. The passive
selector above requires richer support without changing those original calls.
Automatic collection remains unimplemented.

Logger checks the projection's exact raw-manifest digest, source identity,
complete operation count, tuple uniqueness, first-acceptance order and bounds.
Every nullable actor/session tuple and its original qualification and revision
range is preserved in canonical `contributors.json` beside the collected export.
No field is truncated, omitted, converted to a canonical session identifier or
treated as authenticated/live authority. The owner still verifies its own
ledger; Logger does not import Efforts or read its SQLite schema.
The whole-result limits are 4,096 tuples and 1 MiB of ASCII-escaped JSON,
separate from the smaller receipt/reference metadata budget.

This explicit path permits an empty canonical-reference tuple when all accepted
session provenance is unknown or noncanonical. Any separately supplied canonical
references still use the original validation; they are not derived by coercing
the projection. The original manifest and database remain unchanged.

A schema-v2 collection intent and receipt bind the additional
`contributors_sha256`. Fresh readback rederives the complete projection through
the selected owner against the actual retained export and compares the stored
bytes before returning that receipt. It never trusts a saved hash alone.
An original schema-v1 receipt is not a provenance acknowledgment, and an
immutable existing revision cannot silently change between v1 and v2.
Conflicting or partial evidence is retained for owner reconciliation rather
than upgraded, downgraded or overwritten. Neither version acknowledges remote
archival or authorizes reclamation.

## Ancestor disposal and retained legacy evidence

Ordinary session archiving, replacement and recursive cleanup must not consume
or erase a marked collection hidden below a session or replacement candidate.
Marker presence, including partial or unsupported metadata, blocks those
operations before recursive deletion or permission changes. A verified ordinary
session archive does not authorize removal of a nested worktree collection.
Candidates beneath a marked ancestor are also protected, including direct file
replacement/removal and empty-directory cleanup; scanning only their descendants
would miss the containing collection.
Session-copy walkers refuse a marked subtree instead of publishing its database
under a session identity.
Raw and redacted copies also refuse entering an existing collection before
creating destination directories, including missing parents below its export;
the generic session archiver participates in destination namespace admission.
Renaming a marked directory does not remove its preservation or discovery boundary.
Process-log publication checks its actual `logs/` destination, not only the
machine root, before entering or creating that directory.
Recursive removal does not change permissions through external symbolic links.

Preservation checks read directory metadata only, never ledger contents. Their
failure budgets are 100,000 entries, 20,000 directories, depth 64 and a
30-second cooperative deadline. An unreadable or over-budget candidate remains
retained; the check does not truncate evidence or authorize an unverified wipe.
The disposal owner still owns quiescence and the separate deletion permission.
This is not a universal recursive lease or authorization to rescue/migrate data.

## Qualification

The opt-in `agent-logger` `tests/test_worktree_state.py` and
`tests/test_worktree_state_coexistence.py` packet covers real
efforts export/readback in separate Python processes, complete-byte retention,
multiple contributing sessions, exact retry, privacy/claim refusal, corruption,
partial publication, creation-time permissions, namespace/fleet coexistence
and redirected paths. The coexistence packet covers every mutating publication
leg and deterministic session/log/archive claim interleavings. It uses synthetic private fixtures,
not an operator's actual agenda or archive destination. Required unit-only CI
admission and operational/native acceptance are separate gates; see
[testing](../../../TESTING.md).
The separate `tests/test_worktree_state_preservation.py` packet qualifies unsafe
placement refusal and existing/restored nested-marker retention across prune,
verified-archive reconciliation, compaction, replacement and generic cleanup.
The focused `tests/test_worktree_state_contributors.py` packet checks optional
lossless provenance, unknown/noncanonical identities, both metadata budgets,
policy refusal, immutable v1/v2 separation and fresh corruption detection.
The real separate-process export test also negotiates the selected provider's
optional capability and round-trips both legacy and provenance-aware calls.
The opt-in `tests/test_worktree_state_providers.py` packet exercises actual
native `-m agent_worktrees` project selection and retained multi-store discovery,
same-cell prefix reuse, current and prior capability responses, exact-checkout
Efforts activation and its actual `info` parser. It preserves fixture state
bytes and keeps process-heavy qualification out of required unit-only CI.
