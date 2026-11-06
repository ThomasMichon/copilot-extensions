# Selected worktree observations

The optional owner-observation provider serves one selected worktree through the
existing elected resident. It materializes **accepted current-roster
observations**, not a second worktree ledger or a lossless history of canonical
mutations. Ordinary roster, agenda and canonical-write behavior does not depend
on this optional index.

This describes source support. Independently installed executables and residents
may lack it; a source merge does not install or activate a running receiver.

## Passive commands and discovery

Use the attributed `agent-worktrees` command from the session catalog:

```text
<agent-worktrees catalog argv[0]> --project <project> worktree-observations \
  --worktree <full-worktree-id> --session <selected-head> --contract-version 1 --json

<agent-worktrees catalog argv[0]> --project <project> worktree-observations \
  --worktree <full-worktree-id> --session <selected-head> --follow \
  --after-cursor <owner-position> --contract-version 1 --json

<agent-worktrees catalog argv[0]> --project <project> worktree-observation-history \
  --worktree <full-worktree-id> --session <selected-head> \
  --source-generation <journal-generation> --before-cursor <owner-position> \
  --limit 64 --contract-version 1 --json
```

`--session` may be omitted to request explicitly unknown head qualification.
`--after-cursor` and `--before-cursor` default to no requested boundary; neither accepts a
roster revision, agenda revision or session-event ID in place of its opaque
owner position. History requires its source generation and accepts a limit from
1 through 64. Unknown flags and unsupported CLI contract versions are rejected.
Output is JSON/JSONL; `--json` makes that intent explicit.

The selected executable reads only an existing endpoint and credential. It
negotiates the running receiver's `worktree-observations-feed-v1` or
`worktree-observations-history-v1`, independent observation readiness, published
state and actual owner generation before sending a new action. It does not
ensure a monitor, provision a runtime, adopt a project, start an agent, copy
credentials or fall back to a durable roster cache.

Both payload dispatchers suppress first-use self-provisioning for these reads,
including the `worktree`/`worktrees` namespace aliases and explicit project
prefixes, with or without the leading `agent-worktrees` compatibility prefix.
With an existing runtime but no receiver, the command emits an
unavailable error rather than starting one. Without a project context it emits
a structured `project-unavailable` error. An explicit project name is a
read-only namespace selection, not evidence that adoption occurred.

## Data and coverage

The resident uses its existing SQLite index path and owner election. Selected
facts come only from an accepted current-roster row with current provenance and
a known record incarnation. A yielded head or ambiguous execution-leg resume
target cannot become an asserted canonical session.

Frames declare `history_scope: retained-range` and partial observation coverage.
They retain exact project/worktree, journal and resident generations, head
qualification, position, availability, durability and observation time.
Unavailable or unqualified facts are not an empty observed worktree.

Stable selected fields omit timestamp-only metadata. A timestamp refresh or
heartbeat therefore does not create an event or advance the history cursor.
`observed_at` describes the accepted roster observation; it is not the canonical
record's commit time, proof of activity or a rigid freshness deadline.

Sampled changes are labelled `observed-change`, with compact integrity
references instead of duplicated large before/after facts. Claim-count
differences do not manufacture `claim-added`, effort binding or disposition
events. Changes between samples, before demand or outside supported fields are
not reconstructed.

## Advisory hook files

The current-roster owner also has an optional small file projection for shell
consumers. It is separate from the selected observation journal and does
not replace managed-session admission, canonical write receipts or hook policy.
The Windows read-event consumer below uses it; other ordinary events retain
their existing execution path.

The monitor's original passive discovery record advertises
`hook_facts_version: 2` and `hook_facts_generation`, a process-local publisher
identity. Do not substitute `runtime_generation`: that existing field identifies
a build and can be shared by replacement processes. The original project-state
owner resolves `project_dir(project)/worktrees/<id>/hook-facts/`; read only the
advertised `<hook_facts_generation>.json`, never a directory scan or newest file.
Adopted-anchor admission remains separate; this projection covers tracked
worktree rows with known incarnation, workspace, current record provenance and
sampled live-session membership/flags. Inactive rows do not create file homes.
Those eligibility observations do not certify process liveness or head custody.

Each UTF-8 envelope has exactly `version`, `quality`, `completeness`, `scope`,
`producer`, `revision` and `facts`. Scope is exact project/worktree/incarnation/
workspace. Producer metadata retains selected runtime root, build/publisher/
projection generations and the existing installation-context fields. Facts
contain only `status`, `status_note_at`, `last_session_id` and `head_yielded`.
Version 2 additionally carries `record_head_session`, the canonical record's
resolved head, independently of the listing/resume target. A yielded session or
an execution-leg resume target is not a recorded head. The producer passes this
observation separately from its original in-memory records, matching worktree,
incarnation and workspace against the accepted row. It never extends the legacy
roster wire or durable row schema. Missing or mismatched observations retain
version 1; absence is unknown, not an asserted empty head.
The listing session is an observation, **not head custody or permission to
bind, resume or retire anything**. Missing facts stay null. The whole envelope
is always advisory and partial, even when its source record was current.

Bounds are 16 KiB per file, 4 KiB per string, a fixed four-level admitted shape
and 4096 rows per project publication. Safe regular-file replacement occurs in
the same directory with no retry loop. Configured project-root aliases retain
their original admission; derived children and producer output files cannot
be links/reparse points or hard links. Unrelated row-field/timestamp changes
do not rewrite unchanged facts. The retained revision names the accepted row
that last changed this view, not the latest whole-roster observation.
The original project root resolves once per publication or withdrawal, never
once per row or as a lifetime cache. Derived-path checks still run before each
replacement or removal.

The original runtime withdraws affected files on publication failure,
unqualified/removed rows, admission closure and shutdown. Old publishers only
own their own filenames; closing one cannot remove a successor's view.
Failure of this optional projection does not change canonical writes or the
existing roster. If withdrawal itself is refused, the publisher retains its
owned path and reports the error; it does not delete aliases or claim fresh data.
A crash can leave an old advisory file. Presence is never daemon-liveness or
current-authority proof.

`Read-PostToolHookFacts` in the separately shipped `scripts/hook-facts.ps1` library is a
read-only point reader. Its caller must
already supply original-owner discovery, selected runtime/project roots and
exact expected project/worktree/incarnation/workspace after applicable live
admission/input checks. It validates the full envelope and scope, reads at most
16 KiB plus one overflow byte with replacement-compatible file sharing, and
returns null for absent, malformed, wrong-scope or unsupported data. It performs
no command discovery, Python fallback, daemon call, directory enumeration or
wait for publication. The existing bounded-file input is decoded in process. PowerShell 7 uses its
bundled JSON reader with depth 4, duplicate-field rejection and date coercion
disabled, preserving timestamp strings exactly; PowerShell 5 uses the existing
framework JSON library with a separate depth-4, 16-KiB decoder. Missing parser support is unavailable, not
permission to install one or invoke Python. The ordinary event decoder remains
unchanged. No lifecycle operation follows from a returned advisory view.

Version support expands only this passive file contract. Old hook callers keep
their existing behavior; new readers facing old/rolled-back discovery report
unavailable rather than guessing support. A complete shell-only event transition
still needs managed-admission publication, reminder/reset and late-guidance
consumers, nonblocking invalidation and a qualified POSIX decoder. Those
requirements are not supplied by these files.
### Windows read-event consumer

The ordinary PowerShell entry loads the library in the same shell.
For legacy native Windows `view`, `rg` and `glob` events within the 1 MiB
prepared-event bound, including outstanding late-guidance watches, evaluation
has no synchronous resident request, command
discovery or Python fallback. Read events do not need mutation invalidation.
Other tools, installation cells and oversized events
keep their original entry and behavior; the prepared-event bound must not
consume mutation events. This is not the complete shell-only transition.

The reader checks the original prepared membership and all its input witnesses.
It then reads the binding owner's `worktree-binding.json`. Only the owner's
guarded exact-record recheck adds the `hookFacts` version-1 locator with resolved
`projectRoot` and actual record `workspace`; the caller-supplied display path is
not that locator. Expected project/worktree/incarnation come from this binding,
not the advisory file being checked. Identity/path comparisons are ordinal.
The current event must still be within the exact workspace. No home or
incarnation is inferred from a directory name.

The original discovery selects one publisher file under `AGENT_HOME` when
configured, otherwise `USERPROFILE`, matching prepared membership and the
publisher. This does not relocate reminder accounting: the counter and shared
lock remain under `USERPROFILE/.agent-worktrees/nudge-state` in both shell and
Python. Version-2 facts drive the
existing disposition window/call counter and bind-reminder cooldown at their
original sidecar paths. Shell and Python counter updates and terminal clears
share a byte-range transaction on `nudge-state/<worktree>.lock`, waiting at most
100 ms for contention (or the remaining Python event deadline). A contended
event skips this advisory counter without changing it or replaying the event;
the independent bind reminder remains eligible. The stable lock file is not
deleted on release. Timestamp note identities normalize to UTC microseconds in
both paths, including existing sidecar values, so YAML's equivalent space/T or
offset representations do not reset mixed read/edit accounting.
Note changes reset the window, terminal observations
clear its counter, live environment controls remain live, and a listing target
does not suppress the no-recorded-head reminder. Neither a reminder nor its
absence changes head custody or permission. No current event is sent or replayed.

Missing, changed, malformed, old-contract or unsupported prepared observations
produce no advisory result for these read events, without blocking on cold
reconstruction or a resident. They are unavailable data, not complete-empty
state. The bounds are 1 MiB for event/admission, 16 KiB each for binding,
discovery and facts, and 4 KiB for the existing advisory counter. Binding and
discovery decoding has depth 8; the fact envelope retains depth 4.
Derived paths reject reparse components. Registered-entry tests use the real
membership, binding and current-row publishers, with interpreter/resident
traps, and distinguish hook-entry time from shell-process startup.
These isolated source controls do not establish installed, ACP/model,
POSIX, complete mutation or whole-hook throughput acceptance.

After prepared admission, late-guidance notification is independent of the
binding locator and current-row files. It consumes only the original session
watch (8 KiB), request (256 KiB) and publication revision (4 KiB), with depth-8
decoding and the same canonical local-path restriction. Pending requests retain
their watch unless the native process probe establishes that the recorded PID
is absent/exited. Inaccessible, malformed, out-of-range or live/reused process
identities remain unknown; this probe does not establish incarnation ownership.
Terminal requests must complete at or after the watch's start. Partial/failure,
possible changes and later publication revisions retain the original fallback,
repair, restart/new-context and no-model-admission wording.

Windows shell and retained Python consumers atomically rename an eligible watch
to a unique same-directory consumption path. Only the winner with the evaluated
snapshot emits a notice. The entire claim/check/restore transaction shares a
bounded byte-range guard at `local-guidance-watch.json.lock`, waiting at most
100 ms. Contention skips consumption without replay; the stable guard file is
not removed. This prevents an overlapping stale claimant's restored old watch
from being mistaken for a new publication. A replacement is restored without overwriting a newer
watch; a superseded claimed snapshot is removed. Failed restoration without a
replacement preserves the claimed file rather than discarding it. This is
best-effort advisory consumption, not durable delivery or a new preparation
store; interruption after consumption can still lose a notice. A notice skips
disposition accounting, even when enabled; reminder opt-out never suppresses
late guidance, and independent bind advice still runs when its own facts exist.

### Prepared Windows membership reuse

The existing `managed-session.ps1` admission entry first checks the original
owner's exact-session `agent-worktrees/managed-session.json` association.
For a supported legacy Windows caller it validates the session, workspace,
registry, platform, original project/repository sources and every recorded
filesystem witness in process before returning positive membership. The
reader uses the original size, kind and 100-nanosecond timestamp semantics;
path and scope equality is ordinal, and event extraction preserves JSON array
types instead of treating singleton arrays as scalar identities.
It does not reparse registries, run Git, discover a runtime or start Python.
Membership remains distinct from permission, head custody and liveness.

The read is capped at 1 MiB plus one overflow byte, 256 witnesses, 4,096
characters per path and 128 path components. Only canonical local Windows
paths without reparse components are eligible. Missing, changed, malformed,
oversized, installation-cell, UNC and alias observations retain the existing
owner admission path. Cold reconstruction still uses that original path.
This is validated warm-path reuse, not a complete shell-only hook transition
or a replacement for installation-context validation. Registered post-tool
tests compose the actual owner association, unchanged shell entry and private
resident with interpreter lookup trapped; their synthetic project/provider
does not establish installed, ACP/model or whole-hook throughput acceptance.

## Qualified resident native saves

The resident can additionally ingest complete committed-save receipts from
canonical transactions executed through its existing `tracking_write` server.
The producer registers explicitly at receiver activation. Its callback only
queues immutable evidence after all physical/logical record and pair holds;
the existing publication/wake path performs materialization. There is no public
receipt-publication RPC, new listener, spool or per-panel producer.

Supported transactions are ordinary claim add/settle/release, disposition and
the shared effort bind/release transactions. An admitted `claim_release` with
`--remove` emits `claim-released` with the saved local claim as `before` and
`after: null`, including removal of an already-released ledger entry. This
describes local ledger removal, not destruction of or custody over the referenced
resource. Missing claims outside an admitted release do not imply a native release.
Effort writes retain their global
binding exclusion, conflict checks, completion/transfer rechecks and direct
fallback; they do not start a resident merely to obtain observation coverage.
One compute can save zero, one or several times to its exact admitted record.
A successful record save retains its receipt if the broader command subsequently
fails. Receipt intake and materialization both enforce the admitted normalized
path and full worktree ID; an observer's reentrant direct save of another record
does not inherit that admission. Path comparison is lexical normalization, not
proof that symlink/hardlink aliases are interchangeable with the canonical path.
Nested computes and computes entered with outstanding notification holds,
delivery or queued work have uncertain attribution and remain diagnosed partial
coverage. They still execute their canonical operations normally.

Native qualification requires complete selected before/after evidence, matching
record incarnation and versioned execution identity, the owning canonical path,
and strong sidecar exclusion at capture. Partial, omitted, degraded, legacy-
identity, ambiguous or producer-managed claim evidence does not become an
authoritative native transition. Resource references are relations; save receipt
IDs and transition discriminators form native event identities.

Native entries use the existing v1 kind/evidence vocabulary and
`persisted-mutation`; actors remain unknown. Caller session IDs, tokens and
record heads are not performer identity. The frame still carries the latest
selected snapshot head/facts; each native entry retains its own captured head
and canonical-save fence. A delayed A receipt cannot reinstall A's facts over B.
Index positions order admitted evidence, not a total canonical commit order.
Before appending native evidence, the owner materializes any sampled difference
from the accepted roster. A queued receipt therefore cannot hide an unrelated
direct write in the same sample; that observed change retains current-view
integrity references while the native entry keeps its captured save fence.

The native-specific dual-fence path validates current-view facts separately
from saved-record evidence. Ordinary post-save publication retains its original
fence-equality check. Both paths reuse source/batch/native identity conflict
guards and durable admission intent, with provider/runtime/producer epochs and
writer authority rechecked before materialization and resolution.

Retries reuse notification evidence and native identities only; canonical work
is never replayed to repair a missing acknowledgement. Loss before intake/first
intent, direct-fallback writers, unsupported producers and bounded-queue loss
remain explicit partial/unknown native coverage. The running receiver reports
that limited scope in `worktree_observations.native_receipts`; it does not
advertise complete canonical history. Native observation failure never changes
the canonical write result or disables the ordinary roster.

Ordinary claim/disposition and effort-binding CLI writes retain their normal
resident wake path. When no usable resident exists they check the current user's
maintenance notice and selected installation maintenance flags, wait one brief
100ms discovery backoff, then use the existing bounded ensure/start path if
needed. Maintenance or unqualified notice contents prevent automatic recovery;
they never trigger a restart, installation or marker removal. The normal ensure
also checks maintenance before spawning. Live incompatible publishers retain
their original same-code compatibility fallback without a forced replacement.
User maintenance resolves from the canonical OS profile, not redirected
`HOME` or `AGENT_HOME`; selected installation flags retain their own root.
Held ordinary claim, disposition and effort writes return their normal
text/JSON error rather than a traceback.

A refused connection before any request bytes were sent may use one bounded
recovery/retry; readiness connection probes send no mutation bytes and wait
within the startup budget even if the same address remains published.
A possible post-send outcome never retries. Deliberately no-wake
native paths remain unchanged. Startup failure without a maintenance hold can
still use the existing logged direct fallback, whose native coverage stays
partial/unknown. This reduces avoidable fallback; it neither recovers missing
past receipts nor promises complete history. Observation/history commands remain
passive and never wake the resident.

## Point reads, pages and held feeds

The control plane uses separate `worktree-observations-read`,
`worktree-observations-history` and `worktree-observations-feed` actions. History
cannot fall through to a held roster feed. Authentication and strict request
validation precede dispatch.

Semantic frames are bounded to 35 KiB with 32-KiB bodies; point replies use the
existing version-1 control envelope with at most 64 additional bytes. JSON
duplicates, nonfinite values, invalid UTF-8, boolean versions and unknown
request fields are rejected. A valid but unsupported integer version remains
distinct from malformed input.

A held stream begins with an atomic snapshot, followed by bounded updates,
explicit gaps and connection-only heartbeats. Its 0.5-second heartbeat is inside
the CLI's five-second communication budget. The client rejects wrong selection,
changed owner generation, regressing positions and invalid base boundaries.
Backward pages do not consume a live delivery cursor.
An expired before boundary is returned as the provider's source-qualified
`gap`, retaining its floor, reason and reconciliation metadata rather than
turning an expected retention response into a transport error.

Cold selection returns explicit `unready`, retains bounded demand and wakes
the existing producer. The request itself does not materialize a snapshot or
scan the fleet. Existing owner publication supplies subsequent observations;
there is no per-panel polling producer.

Closing the CLI closes only its observer connection. Retirement closes admission
and accounts for point/history requests, held subscriptions, producers and
publication qualification before releasing owner custody. Failed accounting is
not interpreted as zero active work.

During asynchronous owner handoff, loss of writer election refuses new roster
and observation publications without closing still-admitted cached reads.
Snapshot, history and heartbeat delivery retain their generation and source
fences until read admission ends or the stored owner is replaced. Read
admission never grants activation or write authority.
If election is lost after an observation intent was admitted, `writer-fenced`
refuses that materialization without retiring still-admitted readers. The
affected selection and its unresolved intent remain explicit; unrelated cached
selections remain readable. A changed stored owner or revoked read admission
still supersedes the receiver.
Each request also captures its provider and reusable runtime admission epochs. Stale
requests retire only their own admission, never a receiver activated afterward,
including reactivation with the same stored owner. Held feeds wait and retry
transient publication qualification at initial, before-read and after-read
gates without losing their consumer delivery boundary.

## Failure and recovery boundaries

Optional index failure leaves ordinary roster and canonical operations usable.
Observation errors remain explicit; they are not permission to repair head,
release claims, replay work or restart a service.

The auxiliary admission-intent protocol protects admitted index operations
across failure and takeover. It does not make canonical record persistence and
observation intake one transaction, promise complete native history or recover
events that were never captured. Fresh current facts and retained history
coverage remain separately qualified.

Agenda planning remains advisory: unavailable or rewound agenda state does not
halt otherwise-authorized work. The
[efforts-owned recovery guidance](../../efforts/skills/driving-effort-agendas/SKILL.md#unavailable-stale-or-rewound-agendas)
describes reconstruction from context, current evidence and history. This
provider does not move agenda storage, add logger archival or change cleanup.

Frames may contain private purpose, claim and relationship metadata. Existing
repository, publication and archival policies still apply; receiving a frame
grants no additional data-publication or restoration authority.

## Validation and related contracts

Focused source coverage lives in `test_owner_observation_provider.py`,
`test_owner_observation_provider_races.py`,
`test_owner_observation_provider_control.py`, `test_owner_observation_cli.py`,
`test_owner_observation_cli_process.py` and `test_owner_observation_payload.py`.
The shared CLI transport also retains the existing agenda-relay tests. See
[repository testing](../../../TESTING.md) for bounded execution and explicit-tier
process fixtures; unavailable PowerShell is not Windows qualification.

- [Observation wire implementation](../src/agent_worktrees/owner_observation_contract.py)
- [Current Sidecar plan and remaining acceptance](../../../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-live-timeline.md)
- [Proposed cross-owner observation boundary](../../../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-observation-contract.md)
