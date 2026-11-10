# Owner-qualified PR state in the Companion

Canonical parent: [complete effort](README.md), Step 6 topology and Step 7
source coverage. Original request: [purpose-first continuation](purpose-first.md).
Coordination: #8811.

**Status: source plan reviewed in #10920; R1 provider source merged in
#10935; R2 optional one-PR consumer source merged in #11194.**

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

- [x] Extend the existing selected `worktree-status-bundle` response with an
      optional versioned PR-association fact and declaration for the existing
      snapshot operation. Keep all current facts, defaults and old readers
      compatible; no new standalone command or snapshot acquisition at opening.
      The new fact's construction is passive; the status command's existing
      Git/liveness/bootstrap behavior is unchanged, not newly declared passive.
- [x] Read exact canonical PR associations from the selected worktree record.
      Preserve their stable association ID/revision, provider, target repository,
      number, URL and recorded lifecycle state. A claim URL alone cannot supply
      a target, provider, endpoint, credentials or command.
- [x] Resolve each supported association against the existing read-only target
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
- [x] Bound the projection to 24 associations and the existing body budget,
      retaining explicit omission and completeness. Use the original current
      record/worktree incarnation and recheck selection when needed; never
      borrow a latest session, suffix match or another worktree's PR.
- [x] Declare `companion-pr-observations-v1`, version 1, through this existing
      response only when the executing owner implements the association contract
      and passive `agent-worktrees.pr-snapshot` version-1 read. The declaration
      is support, not observation freshness or merge authority.
      #10935 merged as `38be37c9246321df1fa4230f5b28207b421036f3` after
      current-head independent review and all required checks; the complete
      parent and six linked contracts were read immediately afterward.

### R2 - Optional explicit one-PR consumer

- [x] Present recorded PR associations separately from resource claims in Graph.
      Recorded `open`, `merged` or `closed` remains labelled record state, not
      current openness, review approval, content acceptance or claim release.
      Request the optional association form only after the original response
      declares support; absence retains the current claim-only view.
- [x] On explicit operator selection, inspect exactly one admitted association
      through the same resolved Worktrees executable's existing
      `pr-watch snapshot <repository> <number> --json` operation. Do not pass
      publisher-supplied argv, host overrides, credential commands or tokens.
      Require R1's actual declaration before using the operation.
- [x] Allow one caller-owned finite read in flight. Run it off the UI/render
      path through the existing bounded process/Job owner. Closing, selection
      replacement or cancellation retains cleanup and rejects late replies.
      No additional permanent source worker, poller or subscription is created.
- [x] Validate the returned contract/version, exact provider/endpoint/repository/
      number and typed state/merged/head fields against the admitted association.
      Do not turn malformed, oversized, foreign or unsupported replies into
      empty success. Retain independent Summary, Claims and Timeline.
- [x] Show the actual point-read state, exact source and observation limitation:
      open, closed without merge, merged, or unknown/unavailable. A later explicit
      Refresh can read that same selected association again. Cached display is
      last observed, not continuously confirmed; browsing other evidence never
      refreshes its time. Revalidate the association and source selection first.
- [x] Keep review/check/mergeability fields as observed diagnostics only. This
      view cannot grant consent, satisfy a merge gate, request review, remediate,
      reply, merge, close, release a claim or change a PR/head.

All six R2 rows: source merged in #11194.

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

- [x] Actual private tracking records and binding resolution distinguish empty,
      bounded/omitted, duplicate, incomplete, wrong-owner/incarnation, reassigned,
      creating and terminal associations. Claim state and recorded lifecycle
      cannot qualify provider openness.
- [x] Current/prior real parser and process controls prove old caller/new owner,
      new caller/old owner, declared new/new and supported rollback behavior.
      Association discovery and optional reads perform no canonical writes,
      provider mutations, service startup or credential acquisition at discovery.
- [x] Exercise the actual existing snapshot construction and consumer codec for
      provider-open versus locally landed, closed-unmerged, merged, pending
      mergeability, foreign identity, malformed enums/booleans/object IDs,
      provider errors and bounded output failure. Do not mock beyond construction.
- [x] Real Textual controls cover explicit one-association selection, literal/
      narrow rendering, Refresh, reassignment/head changes, old/unsupported
      providers, late replies and blocked-read dismissal with exact cleanup.
      All independent views remain usable; no action is performed on a PR.
      Covered by #11194: wide-viewport selection, Refresh, reassignment/head
      changes, old/unsupported providers and late replies with fixture point
      reads. The 2026-11-08 controls add 40-column rendering and Escape,
      Refresh and deselection of a blocked real point-read subprocess (POSIX
      source tier; see the journal).
- [x] Record source/private-process/Textual evidence separately from installed,
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

### 2026-11-06 - R1 provider source merged

#10935 merged at 13:00:32 PST as `38be37c9246321df1fa4230f5b28207b421036f3`
after current-head independent review and all three required checks. The
complete parent, the five original linked contracts and this plan were read
immediately after actual merge, before this bookkeeping. Only the five R1
source rows close. The joint Validation Plan rows stay open because they also
require R2's consumer, codec and Textual controls. R2, full live topology and
every original parent gate remain open. No rollout, real daemon wake, live Mux
binding, model/data or head/baton operation was performed.

### 2026-11-06 - R2 consumer source merged

#11194 merged at 18:13:09 PST as `31d87f7750faf69b44955bfa6ed599834b7a2e12`
after four independent review rounds and all required checks. Review fixes
pinned the engine runtime, revalidated the association before the point read,
invalidated a pending refresh before awaiting, kept literal Text labels and
bound the read to the source project and creation nonce. The panel was split
into its own module under the size guard; 305 curated Companion tests passed
with verified cleanup. The six R2 rows and Validation row 5 close. Rows 1-4
stay open: they require actual private tracking records, real cross-version
parser/process controls, the actual snapshot construction rather than fixture
replies, narrow PR rendering and blocked real-subprocess dismissal with exact
cleanup. Full live topology and every original parent gate remain
open. No rollout, real daemon wake, live Mux binding, model/data or head/baton
operation was performed.

### 2026-11-08 - Narrow rendering and blocked-read dismissal controls

`tests/test_companion_pr_panel_ui.py` closes Validation row 4's remainder
without source changes. At a 40-column viewport the association selector and
Re-read button stay fully on screen and side by side, and the wrapped graph
keeps provider markup literal, the point-read state and the record-not-openness
caveat. A selected association's point read runs the real owned read runner
against a child process that blocks; Escape, Refresh and deselection each
retire that child (signal-0 exit witness), leave no engine cleanup failure and,
for the in-app paths, settle the read thread with no displayed state.

The existing expected-head Escape control used `os.pidfd_open`, which the
managed test interpreter lacks, so it always skipped; it now uses the same
portable witness and runs. This is POSIX source/private-process evidence only:
the Windows job-object path, installed behavior and native performance are not
exercised. Rows 1-3, full live topology and every original parent gate remain
open. No rollout, real daemon wake, live Mux binding or head/baton operation
was performed.

### 2026-11-08 - Actual record, cross-version process and snapshot codec controls

Validation rows 1-3 close with tests only; no source changes were needed.
`test_companion_pr_associations.py` adds actual private-record controls that
every recorded lifecycle (creating, open, active, merged, closed, abandoned),
with or without a matching PR claim, yields a bound association carrying only
`recorded_state` and no provider-openness field; a claim without a ledger entry
is no association; and an unrecognized recorded lifecycle is an incomplete
binding rather than a guessed state.

The new Manager `test_companion_pr_compat_process.py` drives the real Companion
caller through the real engine-client subprocess path against the real
Worktrees entry point (only its tracking-file location, status daemon,
configuration source and provider fetch are replaced) and a frozen prior owner.
It proves old caller/new owner keeps the prior form, new caller/old owner is
unsupported with no association read admitted, declared new/new reads, and a
rollback fails revalidation before any snapshot starts, then recovers on
roll-forward. The actual snapshot payload construction feeds the real codec for
provider-open versus recorded-landed, closed-unmerged, merged, pending
mergeability, foreign/unknown endpoint, malformed enums/booleans/object IDs,
contradictory merged state, provider errors and the reply byte bound. The child
forbids canonical writes, Git observation and credential acquisition, and the
private record bytes stay unchanged. A deliberate assertion mutation was
detected.

This is POSIX source/private-process evidence only, not installed, native or
live topology acceptance. No rollout, real daemon wake, live Mux binding or
head/baton operation was performed.
