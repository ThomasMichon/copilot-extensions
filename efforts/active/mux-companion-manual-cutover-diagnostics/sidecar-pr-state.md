# Owner-qualified PR state in the Companion

Canonical parent: [complete effort](README.md), Step 6 topology and Step 7
source coverage. Original request: [purpose-first continuation](purpose-first.md).
Coordination: #8811.

**Status: proposed source realization; review before implementation.**

## Existing boundary and intent

The Graph currently displays canonical resource claims but correctly says that
PR openness is unknown. A claim records responsibility, not the provider's
current PR state. The tracked `PRRecord.state` is also an association/lifecycle
record: reconciliation can recognize landed content while the provider PR is
still open. Neither value may be relabelled as a live provider observation.

The Worktrees owner already supplies the passive
[`pr-watch snapshot` read](../../../plugins/agent-worktrees/docs/pr-observation.md).
Its version-1 `agent-worktrees.pr-snapshot` envelope keeps provider, endpoint,
repository, PR, head, state and merge evidence together. It does not reconcile
the tracked record or acquire a durable watch. The subscription facade instead
owns acknowledged Bridge prompt delivery; it is not a passive UI feed and must
not be adopted merely to refresh the Companion.

Realize the original requested PR topology using these owners, not a new forge
client, watcher, sidecar ledger or inferred claim state. This is vision-closing
work toward `worktree-summary-topology` and `summary-is-an-observer`; it adds no
standing intent.

## Bounded source sequence - agent-recommended

### R0 - Review the realization

- [ ] Land this proposal through normal independent review and required CI.
      Read the entire parent and its original contracts after actual merge.

### R1 - Passive owner association and runtime declaration

- [ ] Extend the existing selected `worktree-status-bundle` response with an
      optional versioned PR-association fact and declaration for the existing
      snapshot operation. Keep all current facts, defaults and old readers
      compatible; no new standalone command or snapshot acquisition at opening.
- [ ] Read exact canonical PR associations from the selected worktree record.
      Preserve their stable association ID/revision, provider, target repository,
      number, URL and recorded lifecycle state. A claim URL alone cannot supply
      a target, provider, endpoint, credentials or command.
- [ ] Resolve each supported association against the existing read-only target
      binding without acquiring credentials, reading the forge, starting a
      service, fetching Git or altering a record. Unregistered, contradictory,
      incomplete and legacy targets remain explicitly unavailable/unknown.
      Keep missing binding evidence distinct from an empty canonical list.
- [ ] Bound the projection to 24 associations and the existing body budget,
      retaining explicit omission and completeness. Use the original current
      record/worktree incarnation and recheck selection when needed; never
      borrow a latest session, suffix match or another worktree's PR.
- [ ] Declare `companion-pr-observations-v1`, version 1, through this existing
      response only when the executing owner implements the association contract
      and passive `agent-worktrees.pr-snapshot` version-1 read. The declaration
      is support, not observation freshness or merge authority.

### R2 - Optional explicit one-PR consumer

- [ ] Present recorded PR associations separately from resource claims in Graph.
      Recorded `open`, `merged` or `closed` remains labelled record state, not
      current openness, review approval, content acceptance or claim release.
- [ ] On explicit operator selection, inspect exactly one admitted association
      through the same resolved Worktrees executable's existing
      `pr-watch snapshot <repository> <number> --json` operation. Do not pass
      publisher-supplied argv, host overrides, credential commands or tokens.
      Require R1's actual declaration before using the operation.
- [ ] Allow one caller-owned finite read in flight. Run it off the UI/render
      path through the existing bounded process/Job owner. Closing, selection
      replacement or cancellation retains cleanup and rejects late replies.
      No additional permanent source worker, poller or subscription is created.
- [ ] Validate the returned contract/version, exact provider/endpoint/repository/
      number and typed state/merged/head fields against the admitted association.
      Do not turn malformed, oversized, foreign or unsupported replies into
      empty success. Retain independent Summary, Claims and Timeline.
- [ ] Show the actual point-read state, exact source and observation limitation:
      open, closed without merge, merged, or unknown/unavailable. A later explicit
      Refresh can read that same selected association again. Cached display is
      last observed, not continuously confirmed; browsing other evidence never
      refreshes its time. Revalidate the association and source selection first.
- [ ] Keep review/check/mergeability fields as observed diagnostics only. This
      view cannot grant consent, satisfy a merge gate, request review, remediate,
      reply, merge, close, release a claim or change a PR/head.

## Compatibility and effects

This proposal follows
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
R1 expands an optional fact/declaration before R2 consumes it. Old Managers
ignore the additional fact; new Managers with older/rolled-back providers retain
the current claim-only view and explicit unknown PR state. Missing declaration
does not trigger a speculative command, provisioning or raw forge fallback.
Unknown CLI syntax remains rejected.

The actual passive snapshot keeps its existing protocol and configured
credential owner. Do not add another provider HTTP implementation or persist its
review bodies in Companion history. Preserve existing stdout/frame/time/cleanup
bounds; an oversized source remains unavailable rather than enlarging them.
Document exact association and consumer limits with implementation.

The explicit point-read is not the promised whole live topology or a pushed PR
feed. It is a bounded first supported source path for otherwise unknown PR
state. Unselected, unregistered, child/foreign and unsupported associations keep
their actual coverage limits. Later push integration must use an admitted
observer feed, not prompt-delivery subscriptions or an extra panel poller.

No installed rollout, real daemon wake/restart, live Mux rebinding, model actor,
head/baton action, new credentials or real-data operation is authorized here.
The existing source/owner/review, manual/head, popup/Claims/Relay, PRE, native,
performance, installed and archival gates remain unchanged.

## Validation Plan

- [ ] Actual private tracking records and binding resolution distinguish empty,
      bounded/omitted, duplicate, incomplete, wrong-owner/incarnation, reassigned,
      creating and terminal associations. Claim state and recorded lifecycle
      cannot qualify provider openness.
- [ ] Current/prior real parser and process controls prove old caller/new owner,
      new caller/old owner, declared new/new and supported rollback behavior.
      Association discovery and optional reads perform no canonical writes,
      provider mutations, service startup or credential acquisition at discovery.
- [ ] Exercise the actual existing snapshot construction and consumer codec for
      provider-open versus locally landed, closed-unmerged, merged, pending
      mergeability, foreign identity, malformed enums/booleans/object IDs,
      provider errors and bounded output failure. Do not mock beyond construction.
- [ ] Real Textual controls cover explicit one-association selection, literal/
      narrow rendering, Refresh, reassignment/head changes, old/unsupported
      providers, late replies and blocked-read dismissal with exact cleanup.
      All independent views remain usable; no action is performed on a PR.
- [ ] Record source/private-process/Textual evidence separately from installed,
      native input-ready performance and any future admitted push coverage.
      Required review, green CI, actual merge and immediate whole-parent
      readback precede source completion.

## Completion boundary

R0-R2 close only this finite optional association/point-read realization after
their actual source qualification. They do not complete the original topology,
S2-S5, independent manual/head, permanent Claims/mode, Relay, PRE, native,
installed or D5 operational/archive acceptance. The canonical parent stays
Active until every original Plan and Validation Plan clause is resolved or
genuinely transferred to a named tracked objective under its actual owner.
