# Owner-qualified PR state in the Companion

Canonical parent: [complete effort](README.md), Step 6 topology and Step 7
source coverage. Original request: [purpose-first continuation](purpose-first.md).
Coordination: #8811.

**Status: source plan reviewed in #10920; R1 source qualified and awaiting
independent review; R2 implementation pending.**

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

- [x] Land this proposal through normal independent review and required CI.
      Read the entire parent and its original contracts after actual merge.
      #10920 merged as `b8bb77b2af6c32e06848917c9b6fbc56036483e6` after
      current-head approval and all required checks; full readback followed
      immediately before bookkeeping or source execution.

### R1 - Passive owner association and runtime declaration

- [ ] Extend the existing selected `worktree-status-bundle` response with an
      optional versioned PR-association fact and declaration for the existing
      snapshot operation. Keep all current facts, defaults and old readers
      compatible; no new standalone command or snapshot acquisition at opening.
      The new fact's construction is passive; the status command's existing
      Git/liveness/bootstrap behavior is unchanged, not newly declared passive.
- [ ] Read exact canonical PR associations from the selected worktree record.
      Preserve their stable association ID/revision, provider, target repository,
      number, URL and recorded lifecycle state. A claim URL alone cannot supply
      a target, provider, endpoint, credentials or command.
- [ ] Resolve each supported association against the existing read-only target
      binding without acquiring credentials, reading the forge, starting a
      service, fetching Git or altering a record. Unregistered, contradictory,
      incomplete and legacy targets remain explicitly unavailable/unknown.
      Keep missing binding evidence distinct from an empty canonical list.
      Make this association/binding acquisition explicitly on demand through
      an optional declared `--pr-associations` form of the existing operation.
      Normal status reads advertise support but do not acquire the new fact
      or re-enable their deliberately omitted related-PR configuration map.
      Reuse one read-configuration acquisition scope for the bounded selection;
      retain the original cache/core observation without repeating Git work.
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
      Request the optional association form only after the original response
      declares support; absence retains the current claim-only view.
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

## Journal

### 2026-11-05 - Reviewed PR-source admission

#10920 merged at 23:09:14 PST after independent approval of the amended
`370ba944a7795e98b80599f9c51950c711ed6222` head, no inline findings and all
three required checks passed. The complete parent, five original linked
contracts and this plan were read immediately after actual merge, before
provider readback, claim settlement or source realignment.

The amendment keeps related-PR binding acquisition out of ordinary status
calculation, which deliberately omits that expensive configuration work.
Only the new fact construction is passive; existing status Git/liveness/
bootstrap behavior stays unchanged. Managed reconciliation preserved all seven
contracts exactly. R1's declared on-demand association fact is the next source
slice; R2, full live topology and every original parent gate remain open.
No runtime, source provider, configuration, real daemon, model, data or
head/baton operation was performed by this planning admission.

### 2026-11-05 - R1 provider source qualification

The selected CLI now advertises `companion-pr-observations-v1` and accepts the
optional `--pr-associations` read. It projects exact stored association IDs,
revisions, target identity and record state through the existing read-only
configuration owner. Default status reads do not acquire related-PR bindings,
and the optional read preserves the original core/cache without repeating its
Git work. Missing incarnation/association/URL evidence, duplicates, malformed
data, conflicting scopes and unavailable configuration do not manufacture
provider observations. The 24-association and 1 MiB output limits remain
explicit, including a bounded unavailable response when the combined core
cannot fit.

The existing passive snapshot additionally exports its effective `endpoint`,
without another provider request. This is needed to compare the eventual
point-read scope when configured `api_base` is empty and the provider selects
an ambient host; prior snapshots without that field remain unknown.

The nine-file isolated packet passed all 239 controls without skips and with
verified cleanup. It includes actual private YAML reads, strict current and
frozen prior status-parser processes, old caller/new owner, missing support,
one shared fresh binding discovery, reassignment/incarnation changes,
three-provider binding, explicit budgets and independent cached facts.
These are source/private-process controls, not installed/native input-ready
performance or a real provider read. Required unit-only CI is unchanged.
The R1 and R2 completion boxes remain open pending independent current-head
review, required checks, actual merge and immediate whole-parent readback.
No rollout, real daemon wake, live Mux binding, model/data or head/baton
operation was performed.

### 2026-11-06 - R1 evidence-boundary corrections

The first current-head review of #10935 identified six concrete gaps: duplicate
YAML keys could erase record/registry evidence, endpoint formatting could
reselect an ambient host rather than retain the fetch's actual host, supported
whitespace-padded bindings could be rejected, and compact JSON size checks or
unbounded identity fields could exceed the emitted body limit.

Record decoding now uses the existing strict duplicate-rejecting YAML owner;
passive repository lookup uses strict registry acquisition. Each real provider
retains its selected endpoint in the snapshot, including GitHub's distinct
selected-credential mode. The formatter no longer selects an endpoint.
Association binding normalizes only surrounding API whitespace, preserving the
configured text separately. Both output checks reuse the actual versioned,
indented stdout serialization, and metadata overflow produces a small unavailable
response without exposing or truncating another identity.

The expanded source packet passed 581 controls without skips, with verified
cleanup. Four unrelated publication-factory fixture failures are explicitly
deselected under existing open tracker #10408; no unfiltered provider-suite pass
is claimed and no unrelated publication behavior was changed. The new controls
exercise actual duplicate-key files, fetch construction with synthetic native
responses and emitted stdout rather than mocking beyond those boundaries.
R1 still awaits independent review of the revised head, required checks and
actual merge/readback; R2 and every original parent acceptance remain open.

### 2026-11-06 - R1 scope and configuration follow-through

The subsequent review resolved the original six findings and identified three
additional concrete gaps in unchanged source: deployment-prefix case folding,
encoded ADO repository components and configuration failures hidden by usable
defaults. The review's summary said no open issues, but its three
Medium-severity recommendations still needed substantive resolution.

Deployment prefixes now compare exactly; repository components decode
independently and cannot introduce encoded path separators. Encoded and legacy
unescaped names retain their original component identity. The association
consumer captures the configuration owner's existing acquisition outcomes for
both initial and foreign reads. Concrete value failures keep target bindings
unavailable, including later associations that would otherwise reuse cached
defaults; opaque owner-provenance uncertainty is not falsely treated as a
malformed value.

The affected four-file packet passed 122 controls without skips, with verified
cleanup, including the eight added scope/configuration controls. Earlier
581-control source qualification and the exact 4175-node actor-free unit
qualification remain separately recorded, not summed or relabelled as a new
unfiltered packet. The unit source admission correction changes only the
existing stdout source binding, not its nodes, targets or deadline.
R1 still needs a fresh independent verdict and green checks on the revised
head, actual merge and immediate whole-parent readback. R2 and all original
acceptance remain open; no installed, daemon, model or head action occurred.

### 2026-11-06 - Concrete drop-in admission assessment

The next review had no inline issues but asked for assessment of malformed or
indeterminate configuration inputs. A bounded check reproduced two real
`config.d` paths where binding availability survived a malformed override or
an indeterminate registry retaining old values. Both controls failed before the
correction; the genuinely absent registry control already passed.

The consumer now includes the canonical drop-in value/entry stages in its
failure assessment, without changing the owning configuration loader or its
valid-peer isolation. The five-file packet passed 163 controls without skips
and with verified cleanup, including the three actual drop-in regressions.
This is another explicit affected selection, not a sum of prior qualification
counts. Opaque owner-provenance diagnostics alone still cannot make otherwise
known binding data malformed. R1 remains pending a fresh independent verdict,
required checks and actual merge/readback; every original parent gate remains.

### 2026-11-06 - Class-level fail-closed admission

Successive reviews each found one more acquisition failure reason that the
consumer's denylist did not recognize, most recently a malformed installation
context that silently omitted the related overlay while the baseline still
qualified. Rather than add another reason, the assessment is now fail-closed:
any indeterminate acquisition outcome, including an unrecognized reason,
makes the binding unavailable. Only an explicit allowlist of owner-provenance
gaps, where a value was acquired but its complete provenance is unproven,
remains tolerated. The overlay owner also records its installation-context
failures into the active acquisition collector when no explicit list is
passed; outside a collection scope behavior is unchanged.

Five new controls (four malformed or unreadable contexts and one synthetic
unclassified failure) failed before the correction and pass after it; valid,
absent and provenance-only controls passed throughout. The affected six-file
plugin selection passed 647 controls and the existing related-acquisition
promotion unit passed 152, both with verified cleanup. Only that unit's
source binding was refreshed. R1 remains pending a fresh independent verdict,
required checks and actual merge/readback; every original parent gate remains.
