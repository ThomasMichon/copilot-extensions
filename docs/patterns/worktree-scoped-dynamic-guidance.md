# Worktree-Scoped Dynamic Guidance

**Serves:** Vision `harness-guidance` (feature `concise-context-kernel`;
behaviors `resilient-safety-boundary`, `ambient-delivery-fails-open`,
`resume-stable-context`).

> **Direct-copy precedence:** enabled plugins' declared files in the managed
> generated namespace are the primary runtime instructions. Read them without
> hash, fingerprint, version, provenance or receipt admission. Reviewed checked-in
> bodies are the offline fallback, not a gate on available managed copies.
> The independent compatibility publisher uses the same raw-copy namespace and
> workspace lease; checked-in authoring and offline maintenance remain separate.

## Problem

[`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
gives every plugin a proven delivery path for genuinely per-session
**computed** facts. Its own §2a rule is explicit about what does *not*
belong there: "the session-folder file carries only the values that had to
be computed this session... everything else... is a second, ordinary static
projection... checked in and reviewed like any other static fail-safe."

Projected static instruction content -- the body of a plugin's own
`instructions/*.instructions.md` template, synced into a consumer repo via
`projection-reflect`/`instruction-projections.json` -- is exactly that
"everything else" case. It is:

- **already authored and reviewed** in its plugin payload, so startup can copy
  the file directly rather than regenerate policy or provenance envelopes; and
- **not session-specific at all** -- the same rendered content applies to
  every session in a given worktree, changing only when the plugin's
  installed payload changes or the consumer repo's enablement changes.

This content belongs in reviewed plugin source and a disposable worktree copy.
Creation and session start replace that copy, including while another session
is running; replacement does not claim the model has reloaded it.
Legacy checked-in delivery separates a concise
`.github/instructions/**/*.instructions.md` selector from its lock-owned,
non-auto-loaded full body in `.github/copilot/context-fallbacks/`. Every newly
maintained full fallback uses this pair, including former inline kernels.
Relying only on the
reviewed body as the source of truth would leave two gaps:

1. **Sync-lag is user-facing, and the fix requires rights an ordinary
   contributor may not have.** The checked-in projection is only as fresh as
   its last merged `projection-reflect` sync PR, and landing that PR requires
   push/merge rights on the consumer repo. A session-start mechanism that
   tries to perform (or force) that sync itself, on behalf of whoever is
   running the session, fails outright for a contributor who lacks those
   rights -- an environment/authorization problem masquerading as a guidance
   bug.
2. **Some launch paths have no hook and no session-state folder at all** (a
   fully headless, sandboxed, or cloud-hosted agent invocation). For those,
   the checked-in selector plus reviewed fallback
   remains the usable offline path without cache writes. Other launch paths
   can acquire current managed copies instead. Plugin enablement and the
   declaration own source selection; a separate content-authentication graph is
   not a prerequisite to copying.

## Standard approach

### Startup publication contract

This is the static startup delivery contract, distinct from the checked-in
authoring renderer's legacy delivery described in section 4. It serves Vision
`harness-guidance`'s `simple-local-static-delivery` and
`generated-output-is-disposable-source-is-not` items.

- Resolve the complete enabled plugin set and its declared static instruction
  sources locally. Finish all applicable worktree allocations, paired knowledge
  composition and effective local settings before publishing one final batch.
  Do not copy an incomplete harness-only set and repeat it after pairing.
- Project every declared instruction file from that resolved set. The declaration
  is the inclusion decision: do not score content, impose content budgets,
  judge suitability, or run a second content/source-admission policy in the
  publisher. Review the harness as a whole and change plugin contributions
  upstream when their content needs adjustment.
- Serialize publications for the same worktree using a lightweight local
  publication gate. Resolve or revalidate the complete current source set
  inside that gate, then retain it through destination checks and copying;
  an overlapping launch or session-start backup must not publish an older
  selection last. This requires no resident coordinator or packaged manager.
- Copy declared static content into a dedicated, Git-ignored generated
  instruction namespace using the `.local.instructions.md` suffix. Preserve
  the file bytes, including frontmatter, and the declared destination mapping.
  Do not discover arbitrary Markdown or execute dynamic guidance providers as
  part of this static copy.
- Replace existing **untracked** destinations inside that generated namespace,
  including files without ownership markers or with changed contents. This
  namespace is generated output, not an editing surface. Keep writes contained
  there; do not infer ownership of unrelated untracked repository files.
- Reconcile generated instruction files to the complete selected set: remove
  untracked generated outputs for disabled or removed sources so they cannot
  remain auto-discovered. Incomplete source discovery is not evidence that an
  old output should be removed. Limit cleanup to this generated namespace.
- Before any copy or removal, check all replacement and stale-removal targets
  against Git's index in one batch. A **tracked** target, including a staged
  addition or a staged deletion/rename out of the namespace, is a hard
  publication failure even if ignored or a forced refresh was requested. An
  inconclusive index check must not authorize an overwrite. Refusing an unsafe
  publication does not turn unrelated ambient-delivery failures into a new
  whole-session startup gate.
- Retain minimal containment and ambiguity checks: no traversal or
  symlink/reparse redirection outside the output namespace, and no two active
  declarations silently winning the same destination by iteration order.
- Perform this operation in process. It needs no generic projection CLI,
  separately packaged engine or runtime-assets manifest. Copy plugin-provided
  files rather than generate provenance envelopes, revalidate their instruction
  content, or invoke the authoring/review workflow during publication.
- Report progress and real failures. Do not add broad startup timeouts or a
  rollback-capable cross-file transaction for ordinary generated-file copying.

The index and path checks protect repository files; they are not judgments
about instruction content. The copy step adds no content-admission gate.
Checked-in maintenance and legacy migration outside the designated generated
namespace remain separate operations, not prerequisites to copying declared
instructions.

`agent_worktrees.static_instructions` implements this copy path. Its dedicated
namespace is `.github/instructions/agent-worktrees-generated/<marketplace>/`,
followed by the declaration's path relative to `.github/instructions/` with
the `.local.instructions.md` suffix. The publisher adds that namespace to
Git's local `info/exclude`; it never edits the consumer's tracked ignore files.
It rejects nonregular exclude inputs before index queries can consult them,
then reads the current exclude contents again immediately before appending its
rule. A FIFO must fail explicitly without waiting for a writer or consuming the
Git timeout, and a refusal leaves the previous generated instructions intact.
It resolves user, repository and local activation settings in process and uses
the existing vendored `plugin_resolve` marketplace/path helpers and host
installed-root resolver. Built-in unqualified plugin settings are outside this
marketplace projection surface. Local catalog identity uses the existing
`source.manifestName` mapping when a registration name differs. Missing enabled payloads and invalid per-plugin declarations report failures
without withholding independently resolved sources. Such a publication is
partial, never complete readiness; it retains all old generated outputs because
incomplete discovery is not removal authority. Invalid effective settings,
cross-source destination ambiguity and inconclusive index/path checks still
refuse the batch before mutation.
Index queries include case aliases of the generated namespace and neutralize
inherited Git pathspec flags, so caller environment cannot narrow the protection.
Settings use the host's leading-comment and exact-duplicate JSON semantics,
without applying filename case-folding rules to opaque built-in settings.
An explicitly malformed marketplace registration cannot fall back to an
otherwise installed payload.

Direct creation copies after paired allocation and composition. Direct resume
composes the selected pair before copying. The outer launchers explicitly defer
that preparation during `resolve`, then call
`knowledge compose-plugins --prepare-projections` after their update/maintenance
barrier, before mux or Copilot startup. SessionStart retains its asynchronous
backup; it shares the same per-worktree publication lock and copy backend.
Known failed paired allocation or composition leaves a pending marker in the
worktree's existing Git-administrative guidance metadata. Both final preparation
and the backup refuse publication while it remains. Successful paired
composition clears it under the same publication gate; an unpaired no-op cannot
clear a failed allocation. Explicit no-pair creation does not create the marker.
Previously healthy pairs also become pending before composition, so a later
settings/configuration failure cannot let the backup publish a partial surface.
Pending-state writes and successful clearing stay inside the existing serialized
overlay-preparation boundary; older successes cannot clear newer failed work.
Foreground preparation has no overall deadline. Explicitly bounded callers,
including the backup, share their remaining budget across discovery and Git
preflights and check it before publication mutations; operation-specific Git
and lock limits remain separate from a broad startup ceiling.
Publication and preparation keys use NFC normalization plus casefolding so
equivalent macOS checkout aliases cannot acquire different locks. Destination
collision keys use the same equivalence without rewriting paths or copied bytes.
Declared payloads/templates cannot overlap their own disposable output tree.
After the complete batch preflight, stale
untracked files and unused empty generated directories are removed before
installing replacements, allowing file/directory destination migrations in
either direction without manual cleanup. Tracked blockers still fail the batch
before any of those removals.

Every creation/resume preparation copies the complete resolved instruction set,
including when the same files were copied earlier. Session start queues another
copy rather than probing a prepared generation. The publisher does not inspect
old instruction bytes, compare content hashes or fingerprints, or reuse a
`prepared.json` receipt. Old readiness receipts are ignored. Generated files may
be replaced while a session is running; restart/new context loads the updated
files through normal discovery.

A small `owners.json` in the existing Git-administrative guidance directory
records destination-to-plugin names only. During partial discovery it keeps an
unresolved provider's destinations from being reassigned, without hashing or
authenticating their content. Unknown legacy slots and structural overlaps
remain untouched with an explicit ownership finding until a complete source
set can reconcile the namespace. Git index and ignore metadata are checked
around preflight to retain tracked-file protection; those checks do not inspect
or authenticate instruction content.

The desired bytes are read once inside the publication lock. A plugin update
during a copy is not an authentication failure: the next serialized publication
copies its current files. Cross-process callers resolve sources after acquiring
the same gate, so an older waiting caller cannot publish a pre-lock selection
over a newer one. Each destination is atomically replaced, without a cross-file
rollback transaction or a content-match prerequisite.

Bridge's local Session-Host path invokes the attributable optional Worktrees
`knowledge compose-plugins --prepare-projections --json` owner after static
plugin preparation and before spawn. It copies into the same managed namespace,
not a competing renderer's output. This foreground call retains async
cancellation/tree custody and operation-specific owner limits, without a broad
five-second render cutoff. Without a Worktrees owner, the independent bounded
compatibility renderer remains available; refused identity or a failed owner
does not authorize switching to another installation.
On POSIX the Bridge-owned command uses the existing enclosing-process-group
contract, so foreground Git stays in the group Bridge retires on cancellation
or owner exit. Windows retains kill-on-close Job custody. Native Linux
qualification explicitly removes test-only detachment suppression.
The finite command scope observes the owner's public process return code
independently of pipe EOF, then retires descendants; inherited stdout/stderr
handles cannot postpone cleanup indefinitely. Its observer is joined or
cancelled before the command returns and is not a resident poller.
If POSIX birth custody is already unavailable when spawn returns, preparation
fails explicitly before communication starts. It closes only its owned local
capture transport and bounds the direct-child wait; it neither signals an
unverified group nor claims that descendant cleanup was verified.
macOS group identity uses the public native birth-time reader from psutil,
already supplied by Bridge's required `agent-procutil[metrics]` dependency.
Readiness of that identity backend is checked before enabling the owner path;
missing custody is an explicit unavailable result, not an unsafe spawn.
Fresh identity reads retain PID-reuse and unreadable-identity refusal.
Native Darwin descendant controls remain distinct from adapter unit tests.

SessionStart queues its bounded backup on every invocation with sufficient hook
dispatch budget, including after a successful foreground copy. It does not probe
instruction fingerprints or prepared readiness before dispatch.
For a late backup, the exact session retains a watch on the checkout-local
completion status. The existing post-tool hook surfaces completion once:
changed/removed files explicitly require restart/new context before discovery;
partial and failed outcomes retain reviewed-fallback guidance. An idle session
is not awakened and no restart is automatic. Written files, a successful hook
result and an acknowledgment never assert that the running model admitted the
new instructions.
The watch captures an initial generated-file publication revision before
dispatch. A prospective output mutation advances that revision under the
publication lock before the first write or removal, including partial
publications. It survives post-copy verification failure or abnormal exit;
failed-publication counts remain explicitly unavailable. This revision is only
a change-notification token, not a content receipt or admission gate. Completion compares revisions,
not only the backup's own copy counts: a foreground publisher that finishes
before the backup is still a late change requiring a restart notice. A backup
with no selected files or removals does not invent a new publication revision. Failure after
possible changes reports repair and restart/new-context requirements while
retaining reviewed fallback, never a successful refresh or model-admission claim.
The watch timestamp precedes dispatch as well, so a worker finishing while
dispatch is being recorded is not discarded as an older completion. Abnormal worker exits
persist a terminal timestamp and failure result for the same post-tool
consumer; existing completed results are preserved.
The consumer also compares the current publication revision, not just the
backup's returned revision, so a later foreground publication is not missed.
It can report a provably exited worker even when the short-lived queueing
process and its reaper thread have already exited. The resident path reuses
the loaded identity-aware observer; the standalone hook uses read-only kernel
exit checks and retains the watch on access denial or other uncertainty.
It neither kills a process nor rewrites another worker's request.

A third delivery tier, **worktree-scoped** rather than session-scoped or
purely checked-in: disposable copies of declared instructions, refreshed at
worktree lifecycle boundaries and session start.

### 1. The ignored auto-discovered copy

Both startup and the independent compatibility publisher use
`.github/instructions/agent-worktrees-generated/<marketplace>/...`, preserving
the declared destination beneath `.github/instructions/` with a
`.local.instructions.md` suffix. Every declared source is copied, including
`skipLocalCache: true`: that legacy authoring flag does not opt out of runtime
delivery. The compatibility publisher requires existing ignore coverage, while
startup owns its Git-local exclude rule. A consumer can cover both current
copies and historical local bodies with:

```gitignore
.github/copilot/context-locals/**/*.local.md
.github/instructions/**/*.local.instructions.md
```

Nested destination subdirectories are preserved. Creation and resume populate
the managed copies before discovery.
Reviewed fallbacks are ordinary Markdown outside instruction roots, referenced
using code spans rather than automatic imports. `managed_destination()` computes
the copy path; `local_sibling_destination()` describes the historical layout.
The copy contains the **currently installed** template's exact bytes, without
network fetching, rendering, instruction hashes or unchanged-content reuse.
Keeping installed payloads current belongs to normal authorized updates, not
this permissionless copy step.

### 2. Selectors, reviewed bodies, and exact authority

New authoring always produces a selector and a non-discovered reviewed body.
Declarations may still contain legacy `deliveryMode: "inline"` or omit the mode;
the maintenance provider accepts these inputs but normalizes their output to
`"selector"`. `skipLocalCache` remains accepted legacy metadata, not a requirement
for an automatically discovered control floor. Canonical policy templates remain
unchanged and startup still copies their complete bodies.
A selector replaces the checked-in full body at the
existing `.github/instructions/<plugin>/...instructions.md` destination.
Its complete reviewed body is owned separately at the derived literal path
`.github/copilot/context-fallbacks/<plugin>/...md`, preserving the destination's
subdirectories. The entire derived path must exclude `.instructions.md`,
case-insensitively, including repeated or embedded forms; its basename must not
be a native `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` or
`copilot-instructions.md` name. This path is outside instruction discovery and
is referenced using code spans rather than auto-expanding links.

The version-2 lock owns both artifacts, their digests and byte counts. Sync
validates legacy version-1 lock/preimages before migrating; fallback, selector
and lock participate in the same compare-before-replace rollback transaction.
An inline declaration cannot turn a selector back into an automatically
discovered full body or retire its fallback. Foreign edits block updates,
and unrelated/orphan files are never swept automatically.
Missing/malformed reviewed content, foreign ownership and unsafe paths block
offline reviewed maintenance, not available managed-copy selection.
Source-update freshness remains an advisory distinct from those
integrity failures. A new enabled source without reviewed artifacts blocks as
`projection-missing` until its complete enabled canonical local body is verified;
partial, forged or absent bodies do not qualify. Complete canonical local
delivery permits advisory `projection-source-update` for pending reviewed
freshness; valid stale reviewed guidance remains advisory even without local
refresh. An unowned existing destination or missing locked artifact still
blocks. Adopted, consented
deterministic maintenance normally refreshes reviewed projections once daily;
ordinary work does not require synchronous checked-in resync. This does not
assert that installed payloads or running systems have been updated.

#### Provider-first upgrade and rollback window

The existing declaration contract remains version 1 and the authored lock
remains version 2; no command or argument is added. Current readers retain
legacy version-1 inline locks and version-2 inline/selector pairs. A valid
legacy inline preimage migrates through the same transaction, with rollback
restoring its bytes and lock if any write fails. A malformed, edited, foreign
or unsafe preimage is not migration authority.

Readers that already support version-2 selector/fallback pairs can read newly
authored pairs without an upgrade. Providers predating that pair format are
outside this window: upgrade them before maintaining or reading these artifacts,
rather than deleting lock fields or treating malformed output as success.
Introduce the normalizing provider before migrating declarations or adopters.
Then explicitly declare selectors and remove obsolete cache-free flags before
allowing a prior selector-capable writer to resume maintenance. An older writer
given an old inline declaration can recreate the duplicate automatic body;
reader compatibility alone does not establish writer rollback safety or enforce
the new startup-only requirement. Keep that writer paused until its supported
declaration inputs are in place, or retain the upgraded provider.

Generated projections are not an authoring surface: change the canonical
template or declaration instead of touching its generated output. Adopters may
commit `overwriteExistingManaged: true` in their existing projection-reflect
consent to repair content edits on already-lock-owned regular files. The default
remains `false`. Ownership/provenance, scope, valid sources and path safety still
must validate; unmarked files, invalid locks and indirection never qualify.
Actual edited selector/inline and fallback bytes are transaction preimages,
not trusted guidance. Live consent is rechecked under the sync lock before each
write; withdrawal or a concurrent edit aborts and rolls back prior writes
when a changed preimage is observed. Authorized managed outputs are
single-authoritative-writer artifacts: unsupported content edits racing after
comparison may be superseded by exact upstream bytes. Put customizations in
repository-owned `AGENTS.md` or separate sidecar instructions, never generated
artifacts. There is no shadow/update-blocking mechanism in this contract.
Absent destinations use exclusive no-clobber publication; a concurrently
created unowned file is never replaced. Consent, lock and output integrity are
rechecked after publication, with best-effort rollback on failure.
The compatibility publisher uses declaration-only discovery and raw atomic
replacement in the managed namespace. It shares startup's per-workspace native
file lease, with discovery inside the lease, and does not compare old bytes.
An earlier copy remains available if a refresh fails, accompanied by the actual
failure rather than a successful-refresh claim. A refresh failure suppresses
stale cleanup without blocking independently resolved existing copies.
Incomplete declaration discovery also prevents reassignment of existing slots.
Complete successful publication removes stale untracked copies inside the
reserved namespace. Case-only aliases of a current destination are reconciled
by filesystem identity, so case-insensitive cleanup cannot unlink a new copy;
Git protection also includes portable spelling aliases. Historical bodies outside
that namespace are retained with visible migration findings, regardless of
their content, version or self-supplied markers. Runtime selection does not use
those old bodies as current copies.
Git worktrees must adopt ignore coverage before compatibility publication:
missing coverage reports `projection-local-cache-ignore`. Plain non-Git
directories remain supported. Index queries are batched and rechecked before
mutation, including cached deletions with rename detection disabled. Staged
deletions and paths renamed out of the namespace remain protected. The cached
deletion query adds one Git operation per tracking probe; it protects files,
not instruction-content authority.
Compatibility publication also checks startup's pending-pair marker under the
shared lease, before discovery. Incomplete pairing cannot publish an interim
selection or remove earlier outputs.
Checked-in fallback retirement remains a separate reviewed integrity operation.

Before dependent/consequential action the reader acquires authoritative
content, or reports a visible blocker without assuming authorization.
`manage-instruction-projections.py resolve-source <repository> <destination>
--from-settings --json` performs read-only declaration/path selection. It first
looks for the enabled declaration's exact managed path under
`.github/instructions/agent-worktrees-generated/<marketplace>/...`, before reading
the checked-in selector, fallback or lock. Plain copied bytes need no envelope,
content equality, version comparison or receipt. A same-version edit or an older
enabled plugin version is not a reason to reject the copy. Stale or damaged
reviewed artifacts cannot block an available declared copy.
Effective discovery includes user and user-local settings, then repository and
repository-local settings in their existing precedence order. User-relative
marketplace paths use the user Copilot directory, matching startup publication.
Repository layers still honor the existing persisted-folder trust boundary.
Malformed selected settings report errors rather than silently dropping a layer.
Native built-in plugin settings are filtered before marketplace-entry validation.
Runtime discovery accepts startup's local/remote source kinds and all four
marketplace-manifest layouts, without a network fetch.
Local catalog identity must match its registration or an explicit valid
`source.manifestName` mapping. Supported plugin manifests identify the enabled
plugin before declarations or their absence are accepted; missing, malformed
or mismatched manifests are incomplete discovery, not authority to delete old
copies. These are configuration identity checks, not instruction attestation.
Runtime destination mapping also accepts top-level declared instruction files;
the stricter subdirectory/suffix rules remain authoring-only.
Source-qualified activation keys must be well formed before discovery can
authorize cleanup. Runtime identity components follow startup's portable-name
rules, including case and underscores; stricter authoring identifiers do not
gate copies. Declaration and plugin-manifest JSON accept an optional UTF-8 BOM,
while copied instruction templates remain byte-identical.
The complete declaration set is checked for equal and ancestor/descendant
portable output collisions before publication or selection. All conflicting
copies are excluded with visible findings; independently safe new paths remain
eligible. Compatibility results retain the declared-source count.
Reviewed offline lock/artifact validation remains available without source
freshness or folder trust. Explicit committed-source maintenance sync is a
separate reviewable consent-owned write path, not authentication evidence.
When no managed copy is available, lookup uses the existing reviewed fallback
and its offline integrity checks. An obsolete sibling's self-supplied marker
does not redirect that lookup. A disabled plugin's leftover generated copy is
not selected. Unsafe paths and ambiguous declarations remain real errors.

For local authority, supply `--from-settings` (and the attributable
`--agent-worktrees-path` where required): the utility validates the enabled
declaration rather than parsing or authenticating the instruction content.
No new attestation replaces the removed checks. Installation and normal harness
review own trust; runtime lookup owns only activation, declaration and path
selection.

The resolver is a script, not a bare executable subcommand. Selectors expose
its exact argv and instruct the agent to invoke `reviewing-customizations`;
the tool-returned skill base locates the owning payload without a global PATH
entry or installed-directory scan. An absolute Python interpreter and checkout
root complete the three explicit argv placeholders. Catalog-based marketplace
resolution may additionally require `--agent-worktrees-path`. A fresh-fixture
test executes the actual rendered argv with an empty PATH, exercising settings
discovery and raw managed-copy selection rather than mocking the parser, including
user, user-local, repository and repository-local activation.

#### Decision kernels use the same delivery path

Safety, publication, completion and recovery kernels retain their complete
canonical policy prose. Startup publishes those templates like other declared
sources; periodic maintenance keeps only their thin selectors automatically
discoverable. Their full offline copies wait outside discovery until needed.
There is no inline allowlist authorizing a second automatic policy body.
Repository-authored invariants remain repository-owned and are not removed by
this plugin-source migration.

### 3. Full reads and late-source recovery

Checked-in authoring renders still carry opening provenance (`deliveryKind: "body"`,
`bodySha256`) and a closing `copilot-guidance-body-end:v1` receipt whose
`bindingSha256` hashes the canonical marketplace/plugin, source ID, version,
template hash, scope and body hash. This compact binding covers every identity
field without repeating the metadata corpus at each boundary. These are authoring/offline-maintenance artifacts, not runtime copy admission.
Compact version-2 selectors retain source identity, scope, version/hash,
destination and rendered size; full template provenance remains in the lock.
Selectors contain no body receipt. The pure envelope validator detects omitted
middle bytes, truncated bodies, receipt-only summaries and quoted envelopes.
It verifies byte completeness, **not model admission or compliance**.

The current generated catch-all inventories managed copies
after startup because a bare launch can discover
instructions before a hook writes local bodies. It covers late, partial and
unpaired sources without treating a global marker as whole-corpus coverage.

A read may be skipped only if the complete selected body is directly visible
in the current applicable context. No provenance envelope or receipt is needed.
Filesystem existence, quoted examples, omitted ranges, truncated tool output,
compacted summaries and earlier-context claims are insufficient. On uncertainty
read the full selected content. Recompute selection/coverage after source
changes, resume or reconstruction; persist no loaded-state. No global coverage
file is required.

The scanner separates automatically discovered selectors, inline kernels and
auto-discovered local bodies from non-discovered compatibility bodies and reviewed fallbacks.
Actual admission and selective
reads remain unknown without runtime evidence; character/4 estimates are only
heuristics. Sandbox fake prompt/CLI tests establish ordering and envelope
contracts, not native CLI/App/no-hook/resume/new-context behavior or provider
credit savings. Those native acceptance cases remain **not verified** by
structural tests.

### 4. Refresh at every worktree and session boundary

#### Current publisher and independent compatibility boundary

`agent-worktrees` copies at **create, resume and local JSON launch planning**,
before discovery, and requests another copy at every session start.
Its in-process publisher is independently usable without the customization
manager. The retired seven-file engine manifest and runtime resolver are not
part of startup. The authoring manager remains in customizing-copilot for
checked-in maintenance and the optional `render-local-cache` compatibility
command; that command now copies raw declared templates rather than rendering
authoring envelopes.

The projection CLI explicitly anchors adjacent imports to its resolved payload
directory. Manager safe-path isolation (`PYTHONSAFEPATH`, `-P`, or `-I`) therefore
does not remove the renderer's own modules, and checkout modules cannot shadow
them. This does not disable the launcher's import isolation or add the working
directory to the import path.

A shipped standalone agent-worktrees installation never discovers, imports
or executes a sibling customizing-copilot payload. The independent compatibility
CLI uses effective user, user-local, repository and repository-local settings,
with the same trust boundary as runtime lookup, rather than shared committed-only
authoring discovery.

Foreground create/resume waits for projection preparation to complete, with
start/end, elapsed-time heartbeats and the actual delivery outcome. The
Manager streams stderr rather than hiding it behind an aggregate plan timeout;
stdout remains the machine-readable plan. The renderer process remains owned
through cancellation. The asynchronous sessionStart backup worker retains
its 120-second process-tree budget; the hook queues that worker without waiting
for delivery. Explicitly bounded callers retain their requested timeout.
`worktree_creation._create_worktree_core` and
`resolve_launch_cli._resolve_resume_context` (skipped on `--dry-run`) both
call it at exactly the point described above. `resolve_cli._resolve_json_mode`
also prepares the selected local base/worktree before returning its launch
command. Dry-run plans never render. Best-effort degradation for
the repo not yet being trusted, an explicitly bounded subprocess
timeout, or any other render failure never gate create/resume itself, but
return attributable outcomes and log degradation rather than disappear.
Outcomes distinguish ready (installed/unchanged), partial safe delivery,
unavailable renderer, timeout, failed render and deliberately skipped work.
Renderer JSON counts generic warnings separately from blocking findings; warnings
include budget audits, ownership handoffs and retained-file protections. An
over-budget installation is still ready.
The renderer's additive `written` and `removed` JSON lists distinguish delivery
from stale-cache cleanup; legacy `changed` remains the complete mutation list.
Consumers count only written/unchanged paths as installed guidance and put
blocking safety findings first when bounding diagnostic details. An older
renderer missing this accounting contract is reported as degraded, never
guessed to have installed files it might have removed.

The plugin's `sessionStart` hook repeats the same render as a backup, to
catch payload drift accrued between a worktree's creation/resume and the
current session's own start. `agent_worktrees.__main__._run_session_lifecycle`
calls `local_cache_refresh.sessionstart_diagnostic()` before registration and
later provisioning diagnostics. It queues a headless, detached, bounded worker
rather than waiting for discovery and rendering inside Copilot's hook deadline.
The launch uses the canonical explicit breakaway primitive; contained tests
retain containment. One worker per canonical checkout owns the kernel-released
reservation, and the parent reaps its child off the hook thread. A nested session cwd
resolves to the checkout root. An expired or nearly exhausted hook budget skips
dispatch with an explicit diagnostic, and spawn failures remain visible.

The hook reports **queued**, not installed or loaded. The worker's actual result,
including publication counts or failures, is written to
`<checkout-git-dir>/agent-worktrees-local-guidance/refresh.log` named by that
diagnostic. These are ephemeral checkout artifacts, not a runtime or service
registration root: a linked worktree uses its own Git administrative directory,
so the normal finalization/GC Git worktree removal reclaims its lease and log.
The log holds the latest attempt rather than an append-only history; dispatch
leaves an active worker's log intact and does not start another worker.
Admission holds a short kernel gate across reservation, process launch, and
publication of the exact request nonce plus PID/start-time owner. The child
claims only that request; another hook observes the live pending owner even
before the child starts rendering. No lease or inherited log handle stays open
in the Git administrative directory during rendering. A request arriving during
an active copy records a pending rerun under that same admission gate. Completion
checks and clears it under the gate, repeating discovery and copying without
starting a second worker. Requests coalesce, but all copies share the original
120-second budget; a pending copy at exhaustion reports failure, never success
for an older snapshot. A caller that cannot acquire the short admission gate
reports that its request was **not queued**, rather than acknowledging work it
did not record. The worker atomically
publishes a latest-result log capped at 8 KiB and its completed receipt.
Snapshot replacement uses a short, bounded retry for transient Windows reader
handles; persistent permission failures still propagate rather than reporting delivery.
Finalization and cleanup's normal liveness checks spare a live queued/running
worker using its workspace-local receipt, without spawning Git per checkout.
Owner birth identity uses native process APIs on Windows and macOS, and
`/proc` start ticks on Linux; macOS admission does not depend on `/proc`.
Creation and resume still perform synchronous primary publication before launch.
The asynchronous backup can race instruction discovery and first-turn reads:
neither a successful spawn nor a completed cache write proves model admission.
Selectors and the catch-all use applicable existing managed copies or
reviewed fallbacks while that backup is pending; they must not assume it completed
merely because the hook returned. Files published later are available for
subsequent explicit source resolution and discovery.

`agent-bridge`'s own `target.type == "local"` spawn path is the one
remaining local-spawn boundary `create`/`resume`/`sessionStart` above don't
already cover -- a dispatched agent launched through the bridge rather than
an interactive worktree session. **Landed**:
`agent_bridge.local_cache_refresh.refresh_local_cache()` is the
`agent-bridge` counterpart, called from `session_host_connection.py`'s
`_connect_via_session_host`, right after `resolve_local_launch` resolves
the authoritative local `work_dir` and before `spawner.spawn()` actually
launches the Copilot CLI process -- not at `session_start.py`'s own
`target.type == "local"` entry, where a project-backed target's real
directory isn't known yet. Translated to asyncio idioms rather than copied
verbatim --
`agent_worktrees.local_cache_refresh` is free to block its own one-shot CLI
process, but this call sits inside `agent-bridge`'s own long-lived event
loop, shared by every concurrent session the daemon serves, so every
subprocess call here is natively async (never a synchronous call or a
background thread a timeout could only abandon, not actually stop).
The bridge awaits completion before spawn and logs unavailable, partial,
failed, timeout and audit-warning outcomes. Discovery and rendering share the
actual remaining elapsed-time budget rather than reserving unused discovery
time.

Remote Session Host providers do not run this host-local renderer against a
remote path. Target-side worktree creation/resolve and installed session-start
hooks own their corresponding refreshes. A provider bypassing those boundaries
needs target-side preparation; host-local success is not evidence of remote
installation.

### 5. Reviewed artifacts preserve the offline acquisition floor

Consented reviewable sync owns the checked-in selector, non-auto-loaded full
fallback and lock as one transaction; permissionless local rendering never
writes those artifacts. A write-incapable or hookless launch can read the
owned reviewed fallback through its selector without installed-source
freshness requirements. Former inline kernels, including the recovery control,
use the same generated-current and on-demand-fallback paths.

A new enabled source can be read from its managed copy without a checked-in
selector, fallback or lock entry. The offline floor remains useful when there
is no copy. Missing or damaged offline artifacts remain maintenance findings,
not reasons to withhold an available current copy. Valid stale reviewed content
remains usable as fallback and freshness is advisory. Native host delivery
acceptance is a separate proof obligation.

### 6. Local delivery and context auditing are separate

The local cache installs complete content from every safely resolved enabled
source even when its template, rendered file, or aggregate exceeds the
configured guidance budget. These excesses are warning findings with source
and byte-count attribution, not admission failures or reasons to erase existing
guidance. Invalid or unreadable budget configuration also warns and retains
default size accounting; it does not prevent a safe local render.

The compatibility publisher retains bounded input reads, safe paths, source
identity, declaration ambiguity and tracked-destination checks. It does not
inspect an old instruction body's provenance or compare its content.
Oversized local guidance remains refreshable and reconcilable within input
safety bounds.
Those protections are not context-budget enforcement.

The checked-in `scan`/`sync` path also reports aggregate excess as an
attributable `projection-aggregate-budget` warning, never as an admission
gate for updated or newly enabled plugin instructions. Its per-file bounds,
configuration validation, ownership checks and reviewed transaction policy
remain enforced. A deterministic sync worker retains these aggregate warnings
for audit without routing them as conflicts or opening repeat no-diff PRs.

Use the existing periodic audit to balance the aggregate: `scan --json`
reports total bytes, the configured budget and the overrun; the customization
scanner's `--from-settings --context-budget` inventory attributes context to
sources and categories. Budget debt is remedied by trimming or moving detailed
guidance on demand, not by withholding an otherwise-safe re-projection.

## Rationale

This does not compete with
[`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md);
it fills the gap that pattern's own §2a rule deliberately leaves open. Both
patterns share the same core insight (a checked-in static pointer plus an
agent-initiated file read beats hook-emitted `additionalContext`
composition), applied at a different granularity: per-session for genuinely
computed facts, per-worktree for large/stable projected content that a
consumer repo cannot always guarantee is freshly checked in.

Separating "keep the canonical, reviewable record accurate" (the sync
worker's job, privileged, PR-gated, unchanged) from "make sure *this*
worktree's operating instructions are current" (this pattern, permissionless,
purely local) also directly fixes the reported failure mode: no session,
regardless of the acting identity's repository permissions, ever needs to
attempt a privileged sync merely to see current guidance.

## Exemplars

`customizing-copilot:reviewing-customizations`'s
`instruction_projections.render_local_cache()` is the independent raw-copy
compatibility implementation. Its historical sibling-render predecessor and
`local_sibling_destination()` landed as part of
`efforts/2026/10/02 ambient-guidance-navigability` Phase 7
([ThomasMichon/copilot-extensions#4674](https://github.com/ThomasMichon/copilot-extensions/issues/4674)).
**Historical delivery/proof:** Phase 7 landed full checked-in projections with
per-file "prefer local" preambles, the repo-wide catch-all, and
`agent-worktrees` create/resume + `sessionStart` wiring
(`agent_worktrees.local_cache_refresh`). Its historical Plan/Validation Plan
completion does not certify the current selector/receipt architecture.
The historical clean-room agent-driven proof (3 tool-forbidden `explore` sub-agents per
scenario, given only a frozen snapshot) confirmed the preamble and the
catch-all each independently drive an agent to the fresher
`.local.instructions.md` content over a stale or absent checked-in file
(see the effort README's own Journal for the scenarios and results).
`efforts/2026/10/03 local-cache-delivery-primacy` Phase 1 later replaced the
existence-only precedence this proof covered with the marker-provenance
comparison used by the former precedence rule, closing the stale-sibling gap that
existence-only check left open. That same effort's Phase 2 landed the
`agent-bridge` local-spawn-path wiring step 4 describes above
(`agent_bridge.local_cache_refresh`), closing the one remaining local-spawn
boundary the Phase 7 wiring didn't already cover.

**Current reference:** `instruction_delivery` renders selectors/full bodies
and offline receipt bindings; `instruction_copies` selects declared managed
copies without content admission; `instruction_delivery_io` validates reviewed
fallback bytes; `instruction_projections` owns lock migration, sync and offline
scanning. The generated catch-all and literal rendered
argv fixture cover recovery and executable resolver discovery. Historical
preamble proofs do not substitute for current native host acceptance.

## See Also

- Vision: `visions/harness-guidance/README.md`
- [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
  -- the sibling pattern for per-session computed facts; read both before
  choosing where new dynamic content belongs.
- `efforts/2026/10/02 ambient-guidance-navigability/README.md` (Phase 2 --
  `projection-reflect`, the sync mechanism this pattern's checked-in floor
  depends on; Phase 7 -- this pattern's own implementation)
