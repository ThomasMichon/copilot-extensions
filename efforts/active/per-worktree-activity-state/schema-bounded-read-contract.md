# Schema-bounded current-state reads

**Status:** Proposed contract/validation amendment; implementation remains open.
**Canonical effort:** [Per-worktree activity state](README.md).
**Authority integration:** [Authoritative write-through daemon](../agent-worktrees-authoritative-daemon/README.md).
**Coordination:** #5664 and #3761; storage #229, catalog #1919 and wakeups #2619
remain related, separately scoped work. #6086's reserved exact-selection
enrichment implementation is not taken over by this amendment.

## Operator direction

The [effort Request](README.md#request) captures the schema-bounded read
clarification verbatim. The preceding diagnostic/monitoring direction is also
quoted verbatim, with its original spelling and grammar retained:

> agent-dispatch created a ton of runaway worktrees which are slowing down this machine, to the point where I can to killswitch-disable all the background tasks. While the worktrees are being cleaned up by an anchor-repo agent, take this opportunity to use pyspy and other analysis to see where CPU usage is going. Sweeping the status of all these abandoned worktrees is likely costing us a lot. And we don't even really want to monitoring worktrees that aren't active, or in the claims graph of active worktrees anyway.

That is authorization for diagnosis and a durable goal, not an instruction to
re-enable tasks, compete with the cleanup owner, force a deployment, abandon
claims, or bypass the existing producer-enrollment/migration review.

## Required read boundary

Let **S** be the fixed current-state schema, including its field-size bounds.
Reading one worktree's ordinary daemon-cached status must be O(S),
independent of journal bytes/events, ledger entries, historical lists,
handoff count, claims-graph cardinality, and registered-worktree count.
The bound applies to locating, reading, decoding and rendering the state, not
just to the size of the returned answer.

A slot embedded in a growing YAML/JSON record is insufficient if selecting it
requires loading that whole record. A cached parse followed by a deep copy of
growing arrays is also insufficient. Current state must be independently
readable by worktree identity without discovering it through a fleet list.

Claims graph computation belongs to the owning write/materialization path.
Its results are pushed into affected worktrees' current state, including the
facts needed to report obligations and recurring-monitoring membership.
Ordinary readers consume those results; they neither traverse the ledger nor
reconstruct the graph. A separately inspectable graph may grow, but it is not
an unbounded collection smuggled into the bounded status projection.

Current handoff-head state is selected directly from the worktree's current
slot. The succession sequence and older lifecycle evidence are interesting
when repairing a workflow, not when reporting current state. Older unresolved
retirement duties retain durable custody and automatic retry visibility:
the owning mutation/retry path maintains an independently addressable bounded
next-due duty cursor, separate from the current handoff head. A newer head
does not hide an earlier failed retirement. Routine execution consumes bounded
work per turn with owner-maintained advancement and retry fairness, never by
scanning succession history or returning an all-duties list. Explicit repair
can enumerate every outstanding duty; current status does not do so.

Cold cache, restart, missing/unready projection, and advisory-degrade reads
must retain the same bound. An unavailable projection is explicit, not a reason
to replay a journal, walk a ledger, or build the claims graph inside the read.
Producer recovery or an explicitly requested repair may do that work outside
the ordinary status-serving path.

## Recurring monitoring boundary

The recurring watch set consists of authoritative active worktree/anchor
roots and the transitive claims closure of their unsettled responsibilities.
An inactive but owned child remains relevant. Record existence, a stored
`active` label or a one-time historical listing does not make an unrelated
inactive worktree relevant forever.

The authority pushes changes to membership and graph-derived state.
Unknown observations remain explicit and do not erase responsibility.
Historical inventory and full graph/history inspection remain available on
explicit demand without becoming recurring hot-path work.

## Fresh diagnostic evidence and limits

Read-only profiling of a released `agent-worktrees 1.24.55-dev1` resident
used `py-spy 0.4.2`, 45-second, 50-Hz, nonblocking thread sampling.
The sampled modules were matched by Git blob to released source
`53be0fa787d34aba5a6266dd2709aeca1a347cb8`, not assumed from version metadata.
No installer, service, kill switch, slot or cleanup was changed.

The first accessible-process CPU delta measured the monitor at approximately
0.365 core over 30 seconds. Other consumers, including the terminal and active
agent processes, were also significant; this does not attribute all machine
load to the monitor. The roster contained roughly 1,100 records, with
single-digit candidate live worktree directories. Process CWDs are not an
authoritative head/claims-closure census, and a metadata parse failure leaves
that inventory incomplete.

The monitor's first profile contained 820 samples with 85 nonblocking read
errors. Inclusive stack counts included 320 under
`_find_record_for_path -> tracking.list_records` and 270 under historical
handoff-retirement reconstruction. They are overlapping sample weights, not
CPU-time percentages. A GIL-only profile corroborated record-copy/path lookup
cost and the separate `worktree-status-sweep` refresh thread; its 568 samples
and 134 errors likewise limit precision.

Concrete source paths:

- `status_bar_cli.py:143` resolves one served path by iterating
  `tracking.list_records`; `tracking.py:3108` defaults to copying each record.
- `__main__.py:3378` computes predecessor-retirement history for unserved
  records before its `has_mux_session` guard. That reaches
  `handoff_trace.read_trace` and activity-log lookups even on cold records.
- `worktree_status_daemon.py:234` runs a separate demanded-status sweep.
  `worktree_status_cache.py:414` already has demand expiry, a refresh cap and
  publication fencing: this is not an unlimited refresh of every record
  forever. Its eligibility is recent demand, not active-claims membership.
- `list_cli.py:387` warms recently demanded list shapes. The existing #6086
  reservation addresses shared enrichment there; it is not claimed here.

Parser caching does not make journal reads schema-bounded:
`jsonl_cache.read_jsonl` still opens the file to establish identity, and
`handoff_trace.read_trace` copies the returned history. Neither the old
global log nor a per-worktree journal belongs in the ordinary status path.

Broad host-process polling proved intrusive and was stopped; its results are
not a stationary or causal performance baseline. Concurrent cleanup and
disabled task producers also prevent reconstructing the earlier runaway
workload from these post-disable samples. No full standalone Manager
classification was invoked; that acceptance remains with its existing owner.

## Producer and migration constraints

**Agent-recommended integration constraints, not implemented here:**

Extend the existing write authority and push-through cache rather than add a
second daemon/store owner. Publish revision/freshness/readiness with the
projection, and preserve atomic head transfer, claims custody and uncertain
write outcomes. Graph propagation must not label a stale derived generation
as confirmed current.

The existing staged producer-enrollment/backfill protocol still governs the
handoff cutover. Legacy history reconstruction belongs to that controlled
migration or explicit repair, not to a fallback activated by ordinary reads.
Any pre-cutover legacy reader retained by that protocol is explicitly
transitional and does not satisfy the new read bound; producer enrollment alone
must not be presented as completing this goal.
The older per-token field plan may supply producer/diagnostic data, but reading
the growing `record.handoffs` array is not the new current-head read contract.

## Validation Plan

**Agent-recommended proof details; the O(S) boundary is operator-required.
None of these implementation checks is claimed passed by the source audit.**

- Hold S and the requested worktree fixed while independently increasing other
  worktree records, journal bytes/events, session/handoff histories, ledger
  entries and claims-graph size. Instrument point reads, opened/decoded bytes,
  copying and visited objects; work must not scale with those collections.
- Assert ordinary cold, warm, restart, advisory-degrade and unavailable-state
  paths never call journal readers, full-record/history loaders, fleet
  enumeration or recursive claims resolution. Small returned payloads alone
  are not sufficient evidence.
- Verify schema field-size/collection bounds. An embedded whole ledger,
  graph or handoff list fails the contract even if the number of field names
  stays constant.
- Exercise claim add/settle/transfer and active-root transitions across
  projects, with cycles and inactive owned descendants. Updated membership
  and claim facts arrive through the authority; subsequent reads do no graph
  traversal. Unknown observations cannot silently abandon or falsely finish
  a responsibility.
- Verify one current handoff-head lookup is unaffected by long succession
  history. Late predecessor writes cannot overwrite the new head; existing
  unresolved retirement duties remain explicitly owned, automatically retried
  and repairable. Advance the head repeatedly while older retirements fail,
  defer and later settle; verify the bounded next-due cursor preserves every
  duty and retry fairness, and explicit repair still enumerates them all.
- Increase unrelated inactive records without increasing recurring expensive
  probes at fixed watch membership. Explicit historical inventory remains
  usable, but viewing it does not enroll unrelated cold rows indefinitely.
- Test legacy enrollment, migration interruption, cold restoration and
  publication races with the existing readiness/fencing protocol. Unready
  state stays explicit instead of hiding reconstruction in a read.
- Validate the real consumer API's shape and instrumented access budget, then
  repeat low-impact live profiling after an authorized rollout. Keep CPU,
  observer overhead and concurrent-cleanup conditions separate. Unit counters
  or a faster parser alone do not establish live acceptance.

This amendment records reviewed intent and future acceptance. It does not
complete #5664, #3761 or the broader performance campaign.
