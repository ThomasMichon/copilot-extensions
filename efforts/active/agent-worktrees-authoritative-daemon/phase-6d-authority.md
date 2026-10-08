# Phase 6d — Authoritative Worktrees snapshot and live updates

Parent: [agent-worktrees-authoritative-daemon](README.md), Phase 6.
Coordination: #5555. This is an **agent-recommended execution plan** for the
parent's already-requested authority/stream contract, not a narrower substitute
for it. Review this plan before implementing the new protocol and consumer path.

## Required outcome

With a live resident status-monitor, the Worktree Manager renders the engine's
daemon-owned Worktrees projection. Neither the Manager nor its short-lived
engine CLI computes a competing git/liveness answer. Direct computation is
allowed only when daemon launch genuinely fails; a request timeout, malformed
reply, incomplete endpoint publication, or unsupported live-daemon capability
is not that failure.

Initial rows and subsequent incremental updates come from one coherent
projection generation. With unchanged underlying facts, alternating refresh
paths cannot visibly replace a row's state with a different answer. Shared
wrapper #5743 and stamp repair #5700 are prerequisites/progress, not evidence
that these requirements are already satisfied.

## Verified seams and gaps

- `list_cli.cmd_list` resolves tracking records before daemon classification,
  can return whole-payload `list_cache` JSON without computing/stamping, and has
  a separate cache-only path reading record hints. Resident cache writes can
  grant an extended freshness lease beyond the ordinary 4s TTL.
- `_build_list_json_payload` still scans sessions/mux/bare-orphan/bridge hints
  in the CLI, outside `_classify_records`' daemon request. Moving only the git
  fallback would therefore leave competing liveness observations.
- `_cmd_list_stream` is one-shot fast/classified emission, with its own
  caller-side `_classify_one_record` loop. It does not currently relay a
  daemon-owned snapshot or held update feed.
- The production local `data_local.load` runs Group C reconcile separately,
  calls `engine_client.list_worktree_rows`, and overlays that batch onto the
  returned rows. `refresh_one` also follows a separate refresh/reconcile path.
  Those overlays must not overrule an authoritative projection.
- The shared coalescing library's `subscribe` is subscriber-lifetime accounting,
  not a change stream: `_send_recv` sends one request and reads one response.
  Do not mistake that acknowledgment for a snapshot/subscription barrier.
- Both existing compute servers run in the one resident monitor. Retain their
  distinct batch and per-worktree bundle contracts; do not create a competing
  daemon process or force batch lists to assemble bundle-only facts.

These are source-confirmed gaps. They are not a claim that whole-payload caching
alone reproduces the operator's actual live oscillation cadence.

## Execution slices

### 6d.1 — Resident projection and explicit availability policy

- [ ] Define one project/filter-scoped resident list projection, including the
      row state and every git/session/mux/Group C observation used to choose it.
      Reuse `worktree_git_facts` and existing engine helpers; hoist their work
      into a daemon-owned component rather than copying reductions into a new
      caller helper.
- [ ] Serve list reads from that projection in the existing monitor process,
      with attributable endpoint discovery and owner-scoped authentication.
      Preserve project/platform/status/per-row request semantics, including
      exact versus ambiguous suffix resolution and deletion of removed rows.
- [ ] Distinguish confirmed launch failure from a live but unavailable,
      upgrading, slow, or incompatible daemon. Only the former enables the
      documented direct-compute degrade. The latter preserves last-good rows
      with explicit stale/error provenance or reports an actionable error;
      never return a success-shaped fabricated empty roster.
- [ ] Route classified, cache-only, fresh, per-row refresh, and streaming
      Worktrees reads through the same authority when the daemon is alive.
      Retire the peer filesystem-cache/record-hint reductions on that path.
      Persistence/warm restore must not become a second live authority.
- [ ] Add contract tests that make caller git/session/mux reductions fail if
      invoked with a live daemon, including timeout, malformed result,
      missing endpoint, valid concurrent roster change, and capability skew.
      Separately prove the allowed launch-failure degrade.

This slice can land independently, but it does not close 6d without the held
feed and real consumer stability checks below.

### 6d.2 — Coherent snapshot plus held incremental feed

- [ ] Choose and implement an engine-owned feed in the same resident process.
      The engine CLI relays it over stdout; the Manager does not connect
      directly to daemon transport. Preserve existing one-shot consumers.
- [ ] Specify the generation/cursor and snapshot-to-subscription barrier so a
      mutation between initial rows and listener admission is replayed or
      reflected in an authoritative replacement snapshot, never silently lost.
- [ ] Bound queued updates and define overflow/reconnect/reset behavior.
      Reject stale-generation/out-of-order updates; retain last-good rows
      during reconnect and atomically replace them on a complete reset.
      Subscription release, demand/linger accounting, and cancellation must
      drain cleanly without preventing normal monitor cutover.
- [ ] Wire the actual local and SSH Worktrees loaders to that feed. Remove
      independent fast/classified/reconcile overwrites for authoritative rows;
      source labeling and presentation normalization may not re-derive domain
      state. Preserve the Manager's background-producer/Inbox boundary.
- [ ] Negotiate supported capabilities explicitly. An old live daemon lacking
      this contract is an actionable upgrade/error state, not permission to
      drop an authority flag and silently recompute in the CLI.

### 6d.3 — Consumer acceptance, rollout, and closure

- [ ] Reproduce stale whole-payload cache versus record-hint behavior in an
      isolated contract test, including the extended resident lease, without
      implying that this synthetic test proves the live report's cadence.
- [ ] Test snapshot/subscription races, deletes, filter changes, reconnect,
      overflow, and graceful generation cutover through real transport and the
      production loader/derive path, not just shared leaf functions.
- [ ] Observe a real sampled Worktrees row across repeated refreshes and a
      daemon generation change with no underlying git/liveness mutation.
      Record daemon generation/cursor, emitted fact provenance, and the actual
      rendered state; prove stable state and that caller reductions did not
      execute. Also verify a real fact change is reflected, not hidden by
      indefinite last-good caching.
- [ ] Run affected engine and Manager suites, document Windows/Linux parity,
      update the engine/Picker contract and graceful-cutover impact statement,
      add changefiles for every changed payload, and drive each PR to merge.
- [ ] Verify normal promotion, unified deployment, and activated daemon
      generation before claiming the new authority/feed shipped. Reconcile
      consumer projections after updating. Close only the Worktrees scope of
      #5555; other pivots and this effort's separately owned Phases 1–5 remain
      explicitly accounted for.

## Vision and pattern reconciliation

This realizes the existing agent-worktrees authoritative-daemon intent and the
Manager's engine-only, live-not-snapshot boundary; it does not change those
visions. Implementation must retain owner-scoped discovery, one resident
process, per-fact freshness, background-only UI I/O, and installer-owned graceful
cutover. A held feed adds an admitted work unit: its cancellation/reconnect
boundary must be included in drain/adoption tests, not treated as a later fix.
