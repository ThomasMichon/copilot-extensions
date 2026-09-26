# Worktree Finality and Obligations - Design

Back to the [effort README](README.md).

## Design invariants

1. **Finalized is resumable until pruned.** A retained checkout is ordinary
   working state. Introducing new work reopens it; no command tells an agent the
   record is permanently locked. `finalizing` and `orphaned` remain hard-reject
   states because neither is a stable retained checkout.
2. **Final is a proof, not a stored wish.** `FINAL` is emitted only from current
   evidence. A historical `finalized_at` timestamp does not override a present
   blocker.
3. **Claims must be released, not merely safe.** An active claim and an at-rest
   claim are both held. Either prevents finality and pruning. `released` and
   `abandoned` claims remain history but do not block; abandonment stays a
   visible audit event and the never-wedge escape valve.
4. **Follow-ups own obligations, not referenced objects.** A follow-up may point
   to a claim, task, issue, pull request, file, effort, or other objective. The
   referenced subsystem remains authoritative for its own lifecycle.
5. **One descriptor, many renderers.** Picker, mux, JSON, cleanup, filters, and
   legends consume one status descriptor. No renderer recreates finality rules.
   The descriptor owns semantic style; each renderer translates that token into
   its native palette.
6. **Unknown is not final.** Missing or ambiguous evidence degrades to a
   provisional/review state, never to prune-safe.
7. **Finality and cleanup categories are distinct.** `FINAL` describes a
   completed worktree whose closure proof is satisfied. UNUSED, CONVO, GONE,
   and system-record reaping retain separate explicit cleanup dispositions.

## Independent record facets

The worktree record and its derived view keep these facts separate:

| Facet | Authority | Examples |
|-------|-----------|----------|
| Lifecycle | agent-worktrees tracking | active, pushed, finalizing, finalized |
| Git settlement | git/prune assessment | dirty, ahead, open PR, upstream-equivalent |
| Outbound ownership | `ResourceClaim` ledger | active, at-rest, released |
| Local obligations | `FollowUpRecord` ledger | open, resolved, dismissed, transferred |
| Inbound task ownership | agent-dispatch | assigned or claimed task references |
| Live activity | session/mux/process derivation | active, resting, awaiting operator |

The closure descriptor reads these authorities. It does not become another
mutable store of the same facts.

## Finalized reopening

A centralized claim/obligation mutation transaction classifies the requested
change before writing:

| Mutation | Reopen finalized owner? |
|----------|-------------------------|
| Add a new held claim | Yes |
| Reactivate a released or abandoned claim | Yes |
| Add or reopen a follow-up | Yes |
| Accept a transferred obligation | Yes |
| Add a previously-unheld claim reference | Yes |
| Idempotent replay with no effective change | No |
| Metadata, note, heartbeat, or lease-renew refresh | No |
| Settle active claim to at-rest | No |
| Release or remove a claim | No |
| Resolve, dismiss, or transfer a follow-up away | No |
| Finalize-time settlement/release | No |

The transition and mutation occur under the record lock. `finalizing` and
`orphaned` reject new held obligations. Reopening sets the current lifecycle
state to `active`, clears current prune eligibility, preserves `finalized_at` as
history, and re-arms the disposition nudge state. It does not erase summaries,
prior PRs, or released claims.

Finalize preserves its freeze invariant: it moves the record to `finalizing`
under lock, rejects any later acquisition, refuses to proceed when any active
claim remains, releases at-rest claims, rechecks the claim and follow-up
revisions, and only then commits `finalized`. It never auto-settles or silently
releases an active claim. A failed finalization explicitly rolls back to the
prior stable state rather than leaving a half-frozen record. A stale-finalizing
recovery verb uses the finalization lock/owner liveness and age evidence to
restore the prior state or require operator review; it never guesses while an
owner may still be live.

Reopening restores the worktree's ability to work; it does not resurrect
resources already released or children already re-homed by the earlier finalize
cascade. The claim history is surfaced so guidance can state that distinction
without promising impossible resource restoration.

## Follow-up record

Proposed migration-free shape:

```yaml
follow_ups:
  - id: fu-<stable-id>
    summary: Deploy the merged runtime
    state: open
    revision: 4
    created_at: 2026-08-28T21:00:00Z
    updated_at: 2026-08-28T21:00:00Z
    refs:
      - kind: pull-request
        ref: owner/repo#123
      - kind: resource-claim
        ref: worktree:machine/project/id
    result_ref: null
```

States:

- `open` - this worktree still owns actionable work; blocks finality.
- `resolved` - completed with an optional result reference.
- `dismissed` - explicitly determined not to require action.
- `pending-transfer` - the source still owns the obligation while an atomic
  handoff offer awaits acceptance.
- `transferred` - responsibility moved only after acceptance committed; records
  the destination reference.

`open` and `pending-transfer` items count against the source worktree. Linking a
dispatch task without a committed acceptance leaves the item open.

Compatibility:

- `follow_up` remains emitted as `effective_open_follow_up_count > 0`.
- `status --follow-up --summary "..."` creates or updates one compatibility
  item rather than toggling a bare boolean.
- a legacy `follow_up=true` record with no list contributes one synthetic open
  item in derived views and materializes that item on its next write.
- active effort binding contributes a derived open obligation until the local
  binding is explicitly cleared or transferred. The worktree does not pretend
  to observe completion in another repository.
- every item carries a monotonic revision, and deletion uses a tombstone rather
  than list omission. All explicit mutations occur under `_RecordLock`; the
  record save/merge path merges by item ID and highest revision so stale
  background stamp writes cannot drop, resurrect, or overwrite unrelated
  concurrent items. The ledger revision summarizes the latest item mutation.

The explicit command family is:

```text
agent-worktrees follow-ups [<worktree>] [--json]
agent-worktrees follow-ups add <summary> [--ref <kind>:<value>]...
agent-worktrees follow-ups resolve <id> [--result-ref <ref>]
agent-worktrees follow-ups dismiss <id> [--reason <text>]
agent-worktrees follow-ups offer <id> --to <qualified-worktree-or-task>
agent-worktrees follow-ups accept <offer-id>
agent-worktrees follow-ups decline <offer-id> [--reason <text>]
```

## Canonical closure descriptor

One pure ground-layer producer assembles the descriptor from supplied evidence.
`list --json --classify`, the status monitor/list cache, the mux segment, and
the Picker all call or consume that producer; they do not duplicate its rules.
The descriptor carries provenance so consumers know what was actually proved:

```json
{
  "version": 1,
  "computed_at": "2026-08-28T21:00:00Z",
  "evidence_mode": "cached",
  "evidence_complete": true,
  "base_state": "completed",
  "label": "MERGED",
  "style": "complete-blocked",
  "compact": "MERGED C2 F3",
  "git": {
    "upstream_complete": true,
    "dirty": 0,
    "ahead": 0,
    "open_prs": 0
  },
  "claims": {
    "held": 2,
    "unsettled": 1
  },
  "follow_ups": {
    "open": 3
  },
  "blockers": [
    {"code": "held-claims", "count": 2},
    {"code": "open-follow-ups", "count": 3}
  ],
  "closure": {
    "final": false
  },
  "action": {
    "disposition": "blocked",
    "bucket": "held-claims"
  }
}
```

Evidence modes distinguish:

- `refreshed` - current Git/PR/session evidence was collected.
- `cached` - a bounded-fresh descriptor from the list cache/status monitor.
- `fetch-free` - local no-network evidence only.
- `not-computed` - the caller did not request classification.
- `unsupported` - an absent, newer, or unknown descriptor version.

Only `evidence_mode == refreshed && evidence_complete == true` may produce a
current `FINAL` or `action.disposition == safe`. Cached and fetch-free
descriptors may display a previously-proved `FINAL` only as explicitly
provisional; they never authorize cleanup and may not promote a non-final row.

Presentation rules:

- Preserve the existing base labels for live and Git states:
  `ACTIVE`, `DIRTY`, `WIP`, `UNUSED`, `CONVO`, `GONE`, `ORPHAN`, `UNKNOWN`.
- Use `MERGED` when Git is upstream-complete but closure blockers remain.
- Use `FINAL` only when every finality predicate is true.
- Append ASCII-stable `C<N>` for held claims and `F<N>` for open follow-ups to
  the compact token. Existing Picker-only follow-up glyphs retire so both
  surfaces show the same marker language.
- Descriptor metadata owns the exact label, markers, semantic style, and compact
  text. Picker and mux render that metadata rather than maintaining semantic
  state mappings. Their platform-native palette adapters map the shared style
  token to tmux colors or Picker hex colors.
- One shared formatter assembles and truncates compact text. Cross-surface
  parity is byte-identical before truncation and remains identical at the same
  width budget.

## Closure and cleanup disposition

The descriptor sets `closure.final=true` for a completed worktree only when all
are true:

1. the checkout exists and is not live;
2. no dirty files or local-only Git content remain;
3. all pull-request content is verified upstream, including squash equivalence;
4. held claim count is zero;
5. effective open follow-up count is zero;
6. no active effort or accepted inbound obligation remains;
7. prune assessment has no unsafe or review-only reason.

For the completed-worktree category, `label == FINAL` if and only if
`closure.final == true`. Live activity has display precedence and therefore
produces `ACTIVE`, not `FINAL`; it remains a separate derived facet rather than
rewriting durable lifecycle state.

Cleanup and GC consume a separate graded action disposition:

- `safe` - completed closure is final and may be pruned.
- `opt-in` - UNUSED or CONVO requires the existing explicit include policy.
- `record-reap` - GONE/system records use their existing branch/ownership proof.
- `blocked` - actionable blockers can be listed and resolved.
- `unsafe` - dirty, live, unmerged, or ambiguous evidence.

This preserves existing cleanup categories without allowing a blocked completed
worktree to masquerade as `FINAL`.

Disposition precedence applies to every base state:

`unsafe > blocked > opt-in/record-reap > safe`

Held claims, open follow-ups, inbound obligations, and incomplete evidence are
computed before UNUSED/CONVO/GONE policy. They therefore turn any base state
into `blocked` or `unsafe`; an opt-in or record-reap category is available only
after those blockers are absent. Immediately before a destructive action,
cleanup/GC recomputes refreshed, complete evidence under the record/finalization
lock and proceeds only if the resulting disposition still authorizes that exact
action.

At-rest claims do not strand old worktrees. Explicit finalize releases them
under the finalizing freeze after every active claim is settled. Existing
records receive a dedicated reconciliation command that previews exact at-rest
releases and requires `--apply`; cleanup/GC does not auto-release an at-rest
claim from a current-version record. Active claims are never silently released.
Provably dead and safe active claims may become `abandoned` only through the
existing reclaim path.

The descriptor version 1 blocker-code set is closed:

`held-claims`, `open-follow-ups`, `open-pr`, `closed-unmerged`, `unmerged`,
`dirty`, `wip`, `claimed-live`, `paired-pending`, `active-effort`,
`inbound-obligation`, `unverified-squash`, `live-session`, `finalizing`,
`checkout-missing`, `prune-review-required`, `incomplete-evidence`, and
`unsupported-descriptor`.

Every predicate that produces `blocked` or `unsafe` emits at least one code from
that set. A future code requires a descriptor version bump; older consumers
treat an unsupported version as provisional instead of guessing.
`ORPHAN` and generic no-merge-base cases emit `unmerged`; a record frozen in
`finalizing` emits `finalizing`; `UNKNOWN` emits `incomplete-evidence`.

## Surface contract

One fixture must produce the same descriptor everywhere:

| Surface | Contract |
|---------|----------|
| `list --json` | Full descriptor plus compatibility fields |
| mux segment | `compact` text and descriptor semantic style |
| Picker row | `compact` text and descriptor semantic style, full blockers on detail |
| Picker legend/filter | Generated from descriptor definitions |
| cleanup preview | Exact blocking claims and follow-up items |
| worktree/cleanup skills | Read the itemized blockers and act on explicit IDs |
| agent-bridge worktrees API | Pass through the descriptor and version metadata |
| downstream cockpit | Render or explicitly mark unsupported descriptor versions |

The status monitor is the ordinary cache warmer. Remote/cache-first Picker rows
may render bounded-fresh descriptor metadata, including a previously-proved
`FINAL`, but they mark it provisional and never expose a safe destructive
disposition. A not-computed descriptor is distinct from an absent, newer, or
unsupported descriptor version; neither recreates finality from partial legacy
fields.

The shared compact formatter preserves the base label first, then `C<N>` and
`F<N>` markers, then optional title/detail. Every surface uses the same function
and width budget before applying its native terminal escape sequences.

## Sequencing

1. Land failing cross-surface fixtures.
2. Centralize mutation and reopening.
3. Add the obligation ledger and compatibility adapter.
4. Add the descriptor and switch cleanup truth to it.
5. Move mux and Picker onto descriptor metadata.
6. Pass descriptors through agent-bridge and mixed-version fallbacks.
7. Inventory and preview legacy fleet reclassification/backfill.
8. Update guidance and run the live lifecycle proof.

This order keeps behavior observable during migration and avoids a renderer
claiming finality before the underlying lifecycle is correct.
