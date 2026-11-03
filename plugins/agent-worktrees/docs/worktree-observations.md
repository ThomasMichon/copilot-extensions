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

## Qualified resident native saves

The resident can additionally ingest complete committed-save receipts from
canonical transactions executed through its existing `tracking_write` server.
The producer registers explicitly at receiver activation. Its callback only
queues immutable evidence after all physical/logical record and pair holds;
the existing publication/wake path performs materialization. There is no public
receipt-publication RPC, new listener, spool or per-panel producer.

Supported transactions are ordinary claim add/settle/release, disposition and
the shared effort bind/release transactions. Effort writes retain their global
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
