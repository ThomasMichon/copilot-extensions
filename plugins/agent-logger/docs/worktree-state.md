# Typed worktree-state collection

Parent: [Logger architecture](architecture.md#session-sync).
Owner export: [complete consistent agenda export](../../efforts/skills/driving-effort-agendas/references/agenda-export.md).
Standing intent: [durable worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).

## Current source boundary

`sync.worktree_state_archive` supplies additive collection and fresh local
readback for an explicitly selected, owner-verified agenda export. It uses the
existing `Target` abstraction; only `LocalTarget.worktree_state_root()` opts in.
Other targets explicitly lack that capability, including OneDrive replicas.
The existing sync tick can explicitly opt into this boundary; there is no new
CLI, source crawler or service. Ordinary session sync remains the default.

The orchestration caller must first qualify the worktree owner's repository,
full worktree/incarnation and selected store, obtain the complete domain-owned
export, and negotiate the selected owner's complete-export verifier. The library
takes that exact typed identity and verifier; it never imports another plugin's
runtime or guesses a state home. Explicit managed-placement composition is also
available as a library and through the default-off existing-tick leg below.

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

The complete inventory must match the admitted repository's actual Git origin
and selected installation. The existing aggregate policy identity is checked
separately; it cannot reconstruct a lossless owner identity because it folds
path case and omits ports. A fresh caller-owned Git read shares the same execution
budget and cleanup custody, strips ambient Git repository-routing variables and
bounds its stdout/stderr. The existing local repository-trust normalizer preserves
non-GitHub path case and non-default
ports, folds GitHub path case and removes only protocol-default ports.
`ProjectInventory.owner_repository_identity` retains that exact owner identity
separately from the unchanged aggregate-policy identity. A case-distinct path
or different non-default port is foreign, not a compatible alias.
Logger retains every registered agenda placement,
including retired worktrees with missing canonical records, while rejecting
partial, duplicate, foreign or over-budget results. Limits remain 128 worktrees,
32 stores per worktree and a 1 MiB response. Locations and registration fences
are observations, not proof of initialized domain contents or a collection grant.
The read does not open a domain database or allocate missing state.
Inventory completion also checks the execution cutoff after response validation;
completed commands cannot turn expired caller time into a successful observation.

`select_efforts_exporter` uses the canonical plugin-activation resolver for
that exact checkout plus its effective global settings. Explicit project
disablement, indeterminate settings and multiple matching marketplace identities
cannot become global fallback or first-provider selection. The selected ordinary
Efforts script must advertise both complete export and optional complete
contributor provenance through its actual `info` operation. Package versions or
another reader's support do not qualify it.

These discovery calls remain passive. Their process commands use the shared
finite caller-owned boundary with bounded stdout/stderr and descendant
settlement. An optional caller-supplied absolute budget is shared rather than
renewed by each command. These passive calls do not themselves enable a CLI,
scheduler, migration or installed rollout. Inventory retains its typed project-root/installation scope
for fresh effect-side requalification; observations are not a lease.

## Explicit managed export composition

`sync.worktree_state_pipeline.collect_managed_worktree_state` composes one typed
admission, inventory and placement with export and local collection. Repository
policy is checked **before** provider discovery or staging creation through
`admit_repository_collection`; no invented accepted revision is needed to invoke
that gate. The original revision-bound admission API delegates to this same gate.

The pipeline re-reads the exact Worktrees inventory before export and after the
consistent cut, checking owner repository, project-root/installation scope and
the complete selected placement. A removed or changed store cannot be replaced
by an inferred path. The selected Efforts client re-resolves activation and
negotiates the actual provider before every command. A changed provider selection
refuses; an updated provider at the same selection must still advertise support.

Export captures the owner's actual accepted revision without an expected-revision
placeholder. Verification and complete contributor reads pin that returned
revision against the same exported snapshot. Commands share one caller-owned
absolute process budget, bounded response streams and exact cleanup custody;
unconfirmed native cleanup propagates without becoming an ordinary empty result.
The collection copy also respects that budget's execution cutoff.

Only after admission does the pipeline create an opaque private export directory
under Logger's local home, disjoint from source and collection. Successful local
readback permits removal of those exact temporary export members and the empty
staging directory. This includes the owner's optional, byte-identical prepared
manifest. Unexpected members, changed manifests, failed publication or uncertain
process cleanup retain staging and report its path; no recursive sweep, source
retirement or persistent-collection deletion occurs.

This is an explicit source-library operation, not automatic collection or a new
command/service. Ordinary sync defaults, accounting and scheduled ticks are
unchanged. Real-data collection and installed/native qualification remain
separate effect and validation gates.

## Existing sync tick: explicit machine opt-in

Machine-local `sync.worktree_state: {enabled: true}` enables the typed leg in
`session-sync run`. The default is false; malformed mappings, unknown options
and nonboolean values refuse before sync effects. Ordinary users do not load
aggregate policy, inventory owners or export stores. Dry runs never invoke the
typed leg or create its staging, cursor or destination.
For multi-tenant sync, set the switch in the existing machine-local
`<home>/tenants/<tenant>.yaml` supplement. Committed `tenant.sync` and
`tenant.machines.<machine>.sync` blocks cannot set it; their setting is ignored
with an advisory before the local supplement is applied. The ordinary tenant
layering remains unchanged for other sync fields.

The leg runs under the existing sync lock, before ordinary session accounting
and transfer. It loads the actual filesystem aggregate inputs once, compiles
that same snapshot, and retains each typed admission's authoritative checkout.
The opt-in is not a collection grant: enforce-mode aggregate admission, privacy,
exact repository/target policy and provider capability checks still apply before
inventory or export staging. Native Windows and unsupported targets refuse.

The typed leg shares a 120-second caller-owned budget, including a five-second
cleanup reserve, across policy Git observations, inventory, export, verification
and collection copying. Budgeted policy loading caps each config/Git response
at 1 MiB and the admission list at 128 repositories. This is not a hard bound
on synchronous filesystem operations or the whole existing sync pass, and does
not qualify installed or remote-filesystem latency.

At most eight eligible repositories are visited per pass, with one selected
store per repository. Durable, private `worktree-state-sync-cursor-<key>.json`
files under Logger's home rotate repositories and stores, advancing before owner
reads and exports so failed early attempts cannot starve independent sources.
The key hashes the admitted native destination, session source, sync lock name
and complete executing-machine identity. Each collection context retains both
positions independently; alternating tenants cannot prune or advance each other's
progress. The former unscoped cursor is left untouched, not reused or deleted.
Repositories with otherwise supported claims for another filesystem destination
are outside this pass, not failed attempts. Global admission and privacy failures,
missing/ambiguous claims and unsupported claim policies still refuse.
The cursor records
scheduling position, never authority, success, a cached revision or remote
acknowledgment. Missing positions resume at the next sorted candidate; malformed
cursor state refuses without overwriting it. Failed cursor publication retains
its temporary evidence.

Results distinguish local readbacks, known unattempted stores, repositories
whose inventory was not evaluated, and failed attempts. An inventory/export
failure does not drop healthy independent repositories; unconfirmed native
cleanup still propagates exceptionally. Typed failures make the combined pass
fail even when ordinary sessions transfer successfully. A missing ordinary
session source still returns failure, but no longer prevents this independently
admitted retained-state leg from running. Accounting, compaction gates,
notification, scheduler ownership and ordinary missing-source semantics remain
unchanged. No installed configuration is enabled by this source change.

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
The explicit composition and opted-in existing tick supply that negotiated reader.

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
The opt-in `tests/test_worktree_state_exporter.py` and
`tests/test_worktree_state_pipeline.py` packet exercises actual selected-owner
export/readback from a retired managed placement, unchanged source bytes, exact
temporary cleanup, retained failure evidence, changed-owner refusal and
revision-pinned lossless provenance. A Linux pidfd witness checks that an export
timeout settles its console descendant. These are isolated synthetic fixtures,
not native Windows visibility, installed collection or remote durability proof.
The existing-tick cases use real filesystem policy loading and selected owners
to collect both retained stores across successive passes with no ordinary session
source, while preserving every source byte. `tests/test_worktree_state_tick.py`
adds bounded rotation, independent failure, exhausted-budget, corrupt-cursor and
unconfirmed-cleanup controls; the focused existing sync tests cover default-off,
dry-run and combined failure semantics.
