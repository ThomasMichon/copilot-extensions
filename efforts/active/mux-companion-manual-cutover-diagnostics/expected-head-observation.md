# Independent expected-head observation

This is the bounded source realization of the [parent's retained P3/P4 and
V3/V4 acceptance](README.md#direct-retained-4369-manual-diagnostic-and-independent-expected-head-acceptance),
not a replacement effort or a new head authority. Coordination remains
[#8811](https://github.com/ThomasMichon/copilot-extensions/issues/8811).
Plan #10865 merged as `5c8cf8466cbaca830af80ebe7347efad83d3111f`.
The source captures optional identity in the existing binding and consumption
records. The passive comparison reader and optional Companion consumer are
source-qualified. H1 merged in #10870 as
`0c51500c9284d4aeaaa6b4706ef969c8c49b4111`; H2 merged in #10884 as
`23780cb6d5fa0b1d3147408ee8d0012b322b061b`; H3 merged in #10893 as
`81b5a7d52efed11afde83d0f89f49b90509cc204`.

## Intent and existing evidence

This closes the standing
[post-cutover-head-verification](../../../visions/mux-companion/README.md#post-cutover-head-verification)
intent without extending the allowed actions. Compare the session that actually
consumed the most recent admitted handoff with the independently resolved durable
head. Keep the existing previous/new lineage and explicit Cut over/Refresh
actions. The classic manual clear/paste-seed path and the explicit Cut over path
must use the same observation, including after closing and reopening the app.

The current Companion loads `list-sessions`, then displays its `is_head` marker.
Its immediate Cut over result reports head-before/head-after. Neither establishes
an independent expected identity.

Existing Context Handoff storage already supplies the independent side:

- A consumed file-backed baton retains `consumedBySession` and `consumedAt`.
- A task-backed delivery checkpoint retains `consumerSession`, but is created
  **before** task consumption. Its `steps.taskConsumed` is required; preparation,
  `consumeAttempted`, a prompt receipt or a spawned candidate is not consumption.
- The predecessor's `handoff-request.json` also retains its actual consumer.
  Consumption is recorded before best-effort head promotion. A refused or failed
  promotion does not erase the successful consume.

These are owned delivery records, not proof that the head changed or that a
process/pane retired. Legacy records do not capture their original
worktree-creation nonce or consumer activation. H1 enriches fresh records without
retroactively supplying those facts from a mutable binding read later.
Legacy records therefore remain unqualified rather than being rewritten or
upgraded by observation.

Worktrees already stamps an exact session's `worktree-binding.json`, optionally
including project and creation nonce after a locked, uncached source recheck.
Reuse that seam for an optional activation observation. Context Handoff can
capture it within its existing durable delivery records, without another
binding-lookup CLI or another registry. Task creation reuses returned payload
metadata or one existing read-only payload lookup to preserve the original
author identity on deduplication, including unknown legacy evidence.

The current durable head and the Picker's read-time resume choice are distinct:
`WorktreeRecord.resolved_head_session` replays the head ledger, while
`sessions.resolve_resume_target` can choose a yielded head or a known
filesystem-latest session when conversation data is missing. Display that
distinction; a durable-head match alone cannot promise the same resume target.

## Source sequence

### H0 - Reviewed design admission

- [x] Land this source/validation plan through normal independent review and
      required CI, then read the complete parent before implementation.
      #10865 received current-head independent approval and every required
      green check. The complete parent and all four linked Sidecar contracts,
      plus this plan, were read immediately after actual merge.

### H1 - Capture identity in the existing owner records

- [x] Extend the existing qualified Worktrees binding stamp with an optional
      exact activation observation from the same rechecked record. Preserve its
      current nonblocking ownership, atomic publication and legacy behavior.
      Missing, ambiguous, pruned or unavailable activation evidence is not a
      qualified incarnation.
- [x] Context Handoff captures the original project, exact worktree, creation
      nonce and predecessor identity from that existing binding when authoring
      metadata/request state. Capture the consumer's session/activation identity
      when actual consumption succeeds, not merely when delivery is prepared.
      Keep this optional enrichment separate from existing consume authority.
- [x] Preserve the file consume lock, task-consumption checkpoint, same-session
      replay and failed/unknown outcome semantics. A retry must not silently
      replace an already-recorded consumer incarnation with a later one.
      Missing optional identity must not break ordinary handoff consumption,
      arm a baton or change the head-promotion algorithm.
- [x] Retain identities in the existing file/task/predecessor records needed by
      their respective recovery paths. Do not copy prompts into another ledger,
      create a Companion state file or repair historical records.

### H2 - Passive owning read and separate comparison

- [x] Add one optional, versioned Worktrees read over an exact current record and
      the existing delivery schemas. Use current record ownership/incarnation
      and bounded exact-ID reads, never a session-state-root sweep. Candidate
      discovery is confined to the exact worktree's own existing handoff
      directory: actual consumption can precede recording a token in the head
      ledger. A missing state directory is not created by this read.
- [x] Return the independently admitted expected consumer/predecessor/token,
      the durable head and the existing Picker resolver's read-time choice as
      separate facts. Retain the source kind, exact subject/activation and
      observation limitations. Exclude prompt text and unrelated session data.
- [x] Select the most recent actual qualified consumption using its owning
      receipt evidence. An incomplete newer delivery, ambiguous ordering,
      conflicting records, invalid identity or unreadable/budget-limited
      candidate cannot fall back to an older matching receipt and report a pass.
      A source timestamp helps order observations; it is not permission or an
      expiry/repair rule.
- [x] Recheck current record incarnation and relevant lifecycle/head revisions
      after the bounded read. Changed source state, a replaced worktree, a
      superseded consumer activation, legacy identity or an unsupported schema
      remains explicitly stale/unknown/unavailable. Do not infer identity from
      `is_head`, returned head values, file mtime or whichever process is alive.
- [x] Keep qualified match, qualified mismatch, unknown and unavailable distinct.
      A known empty durable head is different from a failed head read.
      A different read-time Picker choice cannot be hidden behind a matching
      durable-head marker. This is sampled comparison, not a future-launch,
      running-process, prompt-delivery or retirement guarantee.

### H3 - Optional Companion consumer

- [x] Read the declared optional contract through the existing cancellable,
      selected-engine process boundary, off the UI/render path at opening and
      explicit Refresh. Publish only to the same current selection/generation.
      No new periodic poller, daemon or persistence is needed.
- [x] Show the comparison in Recovery beside previous/new lineage, including
      expected consumer, durable head, read-time Picker choice and a short
      uncertainty reason. Detailed source identity stays secondary. Reopening
      derives the result again from the owners, not from an earlier popup.
- [x] Preserve Summary, Claims, Timeline, input/focus, popup dismissal and all
      existing optional-source behavior when this feature is unsupported or
      fails. Detection never repairs head, consumes/creates a baton, launches or
      switches a session, retires a process, files a report or supplies input.
      Cut over remains a direct-human, existing-baton, one-worktree action.

## Contract and compatibility

Follow [service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and [exact-ID session-state access](../../../docs/patterns/session-state-access.md).
The provider-first sequence is H1 identity enrichment, H2 owning read, then H3
conditional consumption; no package-version equality or synchronized rollout.

The proposed read contract is `agent-worktrees.handoff-head-observation`, version
1. Advertise support additively through the selected executable's existing
`list-sessions --json` response, which the Companion already requests. Existing
callers ignore the declaration without incurring receipt reads. A later
Companion retains the declaration instead of discarding the envelope and invokes
the new read only when that selected runtime declares support. The exact parser,
envelope and resource limits are documented with H2 before consumer adoption
in the [provider contract](../../../plugins/agent-worktrees/docs/handoff-head-observation.md).

Old Context Handoff records and old binding writers remain usable for their
existing purposes but cannot qualify the new comparison. New Context Handoff
with an old Worktrees binding similarly reports missing optional identity.
New Manager with an old or rolled-back provider reports the feature unavailable
and retains all other views. A current read rejects malformed/unsupported
declarations or responses without issuing mutation commands or a weaker
identity-based fallback. No existing command or schema is retired.

This separate on-demand read avoids adding receipt scans, native probes or
processes to every existing head, lineage, status-bar or resident-monitor read.
One popup opening/Refresh may perform one bounded owning read after the existing
session-list declaration, through the same cleanup owner. Record concrete byte,
file, session and elapsed-work limits and measured source work before claiming
latency; unit duration is not native input-ready performance.

## Validation Plan

- [x] H1: exercise the actual binding stamp and file/task consume functions.
      Cover non-head registration, exact source/incarnation, failed promotion
      after successful consume, preparation without consumption, optional legacy
      absence, same-session retry and unchanged refusal/unknown outcomes.
- [x] H2: actual private files/current tracking records prove independent
      matching and mismatched identities, both receipt kinds, predecessor and
      token correlation, reused worktree identity, superseded activation,
      ambiguous ordering, newer incomplete delivery, missing/legacy/malformed
      records and changed-during-read results.
- [x] H2: verify exact reads do not enumerate unrelated sessions, create state,
      acquire mutation ownership, wake a daemon, fetch Git or invoke handoff,
      head, process or input actions. Oversized or interrupted reads remain
      unavailable, not absence or an older successful comparison.
- [x] H2/H3: exercise the actual parser/process and Manager codec boundaries for
      new/new, old caller/new provider, new caller/old provider and supported
      rollback. Unknown CLI syntax remains strict; optional additive JSON is not
      support proof.
      H2's real parser/dispatch, actual consume writers and passive declaration
      controls pass. H3's actual process/codec controls retain the declaring
      runtime across activation/rollback and preserve the old row-only API.
- [x] H3: real Textual controls verify Refresh and reopen, separate previous/new
      lineage and comparison, narrow/literal rendering, partial errors,
      selection replacement, late-result fencing and blocked-read dismissal with
      verified owned cleanup. A mismatch issues no repair/report/cutover/input.
      #10893 qualifies 200 isolated controls with no skips, including an exact
      native Linux blocked-child exit witness. This is not installed or
      Windows/macOS qualification.
- [ ] Record containing-source, release and selected installed/manual/native
      evidence separately. The original direct-human Cut over and classic
      clear/paste-seed live tests remain under their existing owners and
      authorization; source fixtures cannot complete them.

## Retained parent gate

This plan does not authorize installed rollout, a real daemon wake/restart,
live Mux binding, a real handoff/head/session/process action, raw override,
automatic answers, SDK/model actors or real-data operations. Existing source,
native, host/provider, claim/reservation and complementary Relay custody stays
unchanged. No TTL, silence, old timestamp or completed local test expires an
owner or grants repair authority.

Finite H1-H3 delivery is not completion of the parent. Every original manual,
popup/app-input, permanent Claims/mode, Relay, source-coverage/PRE,
concurrency/lifetime, cold-read/startup reliability, native performance,
installed and D5 operational/archive gate remains retained.

## See also

- [Canonical parent Plan, Validation and Journal](README.md).
- [Current live Sidecar continuation](sidecar-live-timeline.md).
