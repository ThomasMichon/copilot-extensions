# copilot-extensions Worktree Manager

The standalone, **out-of-plugin** harness control plane (installer, configurator
& worktree launcher) for the copilot-extensions harness. It is deliberately
**not** a Copilot plugin and is **not** delivered through the marketplace/plugin
pipe — it is its own payload, fetched and run directly, because the thing that
must guarantee the plugins' prerequisites cannot itself be one of those inert
plugins. It is the one piece that must work *before* the plugins do.

> **Status: active control-plane extraction.** The Manager bootstraps and updates
> itself, provisions the harness core, reads harness state, shells out to the
> worktree engine, and hosts the first independent Textual Picker. It now also
> owns the versioned contract for plugin-contributed pivots, actions, cards/forms,
> and configuration sections. The Picker now renders contributed pivot snapshots
> asynchronously from that contract; action/streaming parity, repo adoption/config
> editing, mux/profile relocation, and presets remain under umbrella issue
> [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) and the
> contract/render extraction is tracked by
> [#1165](https://github.com/ThomasMichon/copilot-extensions/issues/1165) and
> [#1174](https://github.com/ThomasMichon/copilot-extensions/issues/1174).

## Bare entry and first-run diagnostics

Run `worktree-manager` without arguments in an interactive terminal to open
Worktrees when exactly one launchable project and an engine command are available.
Otherwise the same app opens **Setup / Doctor**, without inventing a project or
loading operational worktree data. Multiple registered projects require an
explicit selection; knowledge-only repositories are not standalone targets.
Unreadable or malformed registries remain visible errors, not an empty first-run
roster. **Reload** re-observes state without installing or adopting anything.

**Review setup plan** runs the real `worktree-manager setup` dry run, and
**Inspect Manager setup** uses the existing read-only Manager diagnostics.
Both preserve stdout, stderr and the actual exit status before returning.
An incomplete setup can return a nonzero status even though no changes were
applied. Diagnostics are explicit, bounded child commands, never startup probes.
The result screen waits for command settlement before allowing Return.
The Manager-owned setup child retains the selected installation context; general
contributed-command identity scrubbing is unchanged. Timeouts keep bounded
partial stdout/stderr and do not invent a normal exit status. Unconfirmed
cleanup retains its original custody and blocks another diagnostic attempt.

This is navigation and diagnostic entry, not a complete onboarding wizard.
Installation, repository trust/adoption, machines, plugins, knowledge binding
and authentication retain their existing owners and explicit commands.
**Install core**, **Add machine**, **Adopt repo** and **Enable plugins** open
read-only guides with prerequisites, effects, owning references and supported
next steps. Install core can open the same real setup-plan command. Applying
setup or adoption still uses the named CLI outside the TUI; retain its result
and follow restart instructions before returning and selecting Reload. Machine
creation and plugin enablement have no in-app writer here. Browsing a guide
does not execute its displayed commands, open links or change readiness.
Redirected/noninteractive bare invocation still prints installation status;
explicit verbs, project launch and launcher JSON output keep their contracts.

## Explicit Sync progress

Local paired Sync reads `agent_worktrees/sync-progress-v1.json` beside the exact
selected engine interpreter. A valid version-1 declaration enables
`sync --paired --json --stream`; missing, invalid or unsupported support retains
the ordinary JSON invocation. The selected command is pinned for that invocation.
Remote Sync remains on its existing attributable JSON path. Neither path retries
an uncertain operation or strips an optional flag after execution starts.

The existing progress screen shows real stage messages while the engine runs
off-thread and retains the complete final result, including both pair members.
A 30-second communication interval reports initial or later silence without
cancelling Sync; received updates, including unfamiliar kinds, renew contact.
A separate one-hour execution budget remains finite despite progress.
Missing or contradictory terminal/process results are unknown outcomes, not
success. Output is bounded; expiry retires the owned Job/group and preserves
diagnostics. Unconfirmed cleanup retains exact custody and blocks another item.
Escape does not dismiss an active Sync or claim cancellation/rollback. Completed
results keep the existing diagnostic browsing controls. This does not install,
update or automatically synchronize a worktree.

Application teardown is different from Escape: idle expiry, owner-lifetime exit
and normal Picker shutdown stop queued items, settle the current owned local
Sync and join its worker/observer. Both streamed and legacy JSON local Sync use
the application-owned lifetime; baseline syntax and its 120-second execution
bound remain unchanged. Interrupted execution is unknown, not rolled back or
safe to replay. If retirement fails after a terminal result, complete member/
plugin results and bounded stderr remain attached to the retained cleanup error.
An in-flight remote SSH Sync retains its prior bounded JSON lifetime: teardown
stops later queued items but neither waits for that remote operation nor claims
it cancelled or settled. Mixed selections join only the currently owned local
Sync; remote streaming and remote-process ownership are unchanged.

## Picker notices and diagnostics

Mux uncertainty is separate from process liveness: `MUX(?)` means last-known
mux hosting cannot be confirmed, and `HOST(?)` means a live process's hosting
is unknown. Neither is a zero-client detached session or proof of a safe cold
launch. Passive PSMux CLI discovery, attachment refresh and status validation
are replaced by Driver-owned finite authenticated socket observations,
including fresh session identity and real client counts. Failed/unsupported
observations preserve existing mappings without claiming fresh proof.
The launcher qualifies the actual selected Worktrees provider's observation
contract; older providers stay explicitly unsupported without a CLI downgrade.
Open still creates or reattaches through the existing launch flow. Foreground
PSMux attach/create and explicit writes retain their startup-cleanup risk. See
[preservation-first recovery](../plugins/agent-worktrees/docs/mux.md#preserve-first-when-discovery-is-unavailable)
for the scope, older-consumer limitation and separately authorized recovery.
Periodic PSMux status-bar writes remain refused: restoring fresh listings
does not reactivate cleanup-bearing background CLI work.

The Picker allocates rows only for real Knowledge setup and cached-source
refresh notices: none when healthy, one for either notice, two when both apply.
Updates resize the list viewport without inserting selectable options or moving
its keyed selection. Cached-source status stays in the notice rather than
repeating a stale prefix in every worktree title. Claim-coverage warnings appear as a compact warning
icon; impacted worktrees offer **Doctor** in Actions for read-only diagnostics
and full claim inspection. Inspection does not repair or release resources.
Re-homed resources without a worktree row retain the compact `o: Doctor`
footer shortcut to their existing inspection screen.

## Worktree list controls

The default list keeps **Active** separate and combines dormant and completed
work in **Recent**, ordered by USED. The always-visible controls row exposes
`s` **Sort** (used/title/state), `g` **Group** (Active/Recent, detailed status,
or none), `f` **Status** (all/live/unfinished/completed), and `/` **Search**.
Unowned and controller-owned workspaces retain their separate safety sections.

Search still preserves live rows. An explicit nonlive status filter may hide
them and visibly says **live hidden**. Escape clears search, then a status
filter, before collapsing selection or asking to quit. Grouping and sorting
remain selected. Live reordering preserves source-qualified focus, range
anchor and multiselection; filtering never changes an action's target identity.

USED and its default sort share the latest real worktree message timestamp
when the selected engine supplies the optional versioned message fact.
Older engines retain resume/completion/start fallback. See the
[engine contract](../plugins/agent-worktrees/docs/engine-picker-contract.md#message-activity-and-native-mutation-wakes)
for message, timezone and compatibility semantics.

## Staged launch prompts

Launch prompts require a freshly confirmed cold target. A confirmed live target
is refused; an unavailable, malformed, or unconfirmed status response is not proof
that a target is cold. Refusals preserve the staged seed and distinguish a live
session from an unverified target, retaining status-probe errors or the pending
observation reason when available. Resolve the reported prerequisite and retry.
An independent successful liveness probe can confirm a cold target even while
Git status observations await cleanup; it does not release that cleanup custody
or mark the other status facts confirmed.

Worktree/session launch does not restore global Copilot settings. Session model,
effort, and context choices remain CLI arguments, and folder pretrust remains
available. Explicit machine setup/restore and configured background maintenance
can still update global settings; ordinary runtime-readiness hooks are unchanged.

## Optional historical cleanup

`worktree-manager cleanup capabilities --json` describes the optional cleanup
contract of that exact executable runtime. A newer marketplace payload does not
establish support in an older selected Manager. This command is not part of
normal update, startup or discovery.

The retained detector archive is finite and owner-scoped:

| Component | Supported historical family | Effect boundary |
|---|---|---|
| `manager` | Two pre-rename prototype commands; sealed payload/application runtime slots | Current command publication custody or canonical runtime construction/retention/publication custody, with pinned native files |
| `agent-worktrees` | Authenticated displaced status-monitor control-v1 peer | Selected installed Worktrees runtime, current route/cutover custody, accepted-work drain, native process pins and positive exit |
| `agent-index` | Explicitly adopted legacy `agent-index` / `agent-index-engine` Windows tasks | Selected namespaced cell with retained-inert legacy disposition, Index transaction mutex, current-user registration ownership and full native revalidation |

Unknown layouts, other daemons, unattributed registrations, systemd/launchd
services and unrelated residue remain unsupported. File retirement is qualified
only on platforms advertised by its native owner; unsupported platforms preserve
findings rather than use a pathname-delete fallback. Inspection reports incomplete
coverage and exits 1 instead of claiming that a host is clean. Broader coverage
remains tracked by [#10180](https://github.com/ThomasMichon/copilot-extensions/issues/10180).

```console
worktree-manager cleanup inspect --component manager --json > cleanup-report.json
worktree-manager cleanup apply --component manager --report cleanup-report.json --select FINDING_ID --json
worktree-manager cleanup inspect --component agent-worktrees --json
worktree-manager cleanup inspect --component agent-index --root ABSOLUTE_LEGACY_ROOT --selection CURRENT_CELL_SELECTION.json --json
```

Inspection does not remove anything. Apply accepts only explicitly selected
candidate IDs from a report for the same component/current scope and delegates
revalidation to the owning adapter. The report's hashes establish consistency,
not permission. Changed, protected, foreign, unsupported or unconfirmed resources
remain preserved, pending or failed. Repeated `--select` chooses multiple candidates;
there is no apply-all or force fallback. `--root ABSOLUTE_PATH` selects a Manager
root while commands stay in the current user's `.local/bin`. Worktrees resolves
its own root and accepts no override, PATH fallback or self-provisioning command.
A Worktrees family selection is sent in one bounded native request, not one
process per PID.

Index requires the adopted legacy root and a current caller-supplied selection
file on both inspect and apply. The file names `payload_root`, `context`,
`durable_home`, `marketplace_id`, `namespace_generation`, `install_generation`
and `activation_generation`; an optional `maintenance_token` remains in that
private input and is never copied into reports. The native installation-context
owner validates this selection, explicit adoption and retained-inert disposition;
paths or task names alone do not qualify. A report never chooses executable
payload code.

A busy or ambiguous daemon drain retains the exact original native identity and
a durable code reference. A later explicit inspection can select settlement of
that reference only after positive original-process exit; a missing census entry
or RPC ACK is insufficient. Neither the front door nor an update silently retries
an unknown retirement, releases another owner's hold or starts a cleanup service.

Exit 0 means the capability query succeeded or every selected apply outcome is
confirmed. Exit 1 means incomplete coverage or an unsettled selected outcome;
inspect JSON remains available. Exit 2 means invalid syntax, report or scope, or
a pre-effect refusal. See the [historical cleanup pattern](../docs/patterns/historical-installation-cleanup.md).

## In-session Mux Companion

The resident mux runtime retains its daemon-instance admission until its
listening/reaper threads and every accepted wire handler have actually retired.
Its existing bounded `shutdown()` and control ACK remain independent of that
settlement: a runtime-owned retirement worker holds custody after the caller
returns, including for in-process embedding and partial startup failures.
This does not change routing, generation selection, the single-active lease,
or production lifetime policy.

`worktree-manager companion` opens the current worktree's Textual companion.
Summary is the initial tab, showing its reported purpose and disposition.
Claims shows the actual engine resource ledger, including the inbound owner,
resource references, states and notes. Each observation retains its own
confirmation marker and timestamp; unavailable data is not an empty ledger.
Reads and Refresh run off the UI loop. `r` refreshes and Escape closes.

The on-demand popup explicitly binds Escape to clean exit. Ctrl+Q uses Textual's
clean quit action; Tab/Shift+Tab followed by Enter/Space can activate **Exit**.
Ctrl+C keeps Textual's quit-hint notification and leaves the Companion open.
Clean exit cancels owned reads and closes `display-popup -E` without a second
key, returning input to the original CLI. It does not invoke **Cut over**,
consume a handoff, or retire the worktree/session. Private attached-client tmux
tests cover ready, blocked and failed source reads; installed clients and
Windows/PSMux retain separate qualification.

Timeline is an alternate view backed by a bounded timeline contract, virtual
line renderer and caller-owned observation controller. Its compaction policy
compares qualified pre-context counts without rounding; target-only capacity
or unqualified measurements remain unknown. Opening Timeline now attaches the
selected worktree's optional owner-observation feed through the attributed
engine command. Summary remains initial and does not wait for timeline history.
An owned passive probe checks actual receiver support and the selected parser
before any new verb; absent/old providers stay explicitly unavailable without
launching or provisioning a resident.

The adapter consumes `worktree-observations` point/follow responses and bounded
`worktree-observation-history` pages. Initial history uses a bounded latest page
and an atomic replay read before following that live barrier. Current purpose
and effort come from qualified selected facts; creation context remains unknown
unless its owner supplies it. Roster differences are generic `observed-change`
records with partial sampled coverage, not fabricated creation, claim or binding
events. A timestamp refresh or heartbeat is not a timeline milestone.
See the [owning source contract](../plugins/agent-worktrees/docs/worktree-observations.md).

Known Bridge tool/contribution occurrences use readable labels and reported
tool titles or agent display names where available, not raw event enums as the
normal work summary. Names remain literal, bounded reported metadata; absent
older-provider fields use generic occurrence labels. Exact native kinds and
source fields remain in expandable Evidence. These labels do not establish
current concurrency, allocated-agent custody, lifetime tool totals or pending
input.
An optional label that cannot be used as literal display text falls back to
the next reported name or generic occurrence label while its source value stays
in Evidence. Invalid wire text remains a source error, not a hidden fallback.

Known lifecycle/tool occurrences with a valid owner-reported subagent ID use
that reported actor's own source-scoped lane. Equal display names do not merge
distinct IDs, and reporter/subagent identities occupy separate namespaces.
Missing, invalid or unknown-event attribution stays with the Bridge reporter.
An observed start/completion is not current running state or allocated custody;
parent tool-call metadata remains in Evidence, not an inferred hierarchy link.
Expanded activity Evidence explicitly keeps current execution and concurrency
unknown, including when a reported tool has no observed terminal update. The
[source coverage](../plugins/agent-bridge/docs/session-observations.md#contribution-and-activity-coverage)
distinguishes live callback reports, retained/replayed records and unsupported
current-state evidence.

Known Bridge `agent_message` and `agent_thought` occurrences collapse into
expandable output groups of at most 64 adjacent loaded records from the same
source generation and reported actor. Work, requests, compaction, gaps and
unknown native kinds remain distinct; summary prose never classifies output.
The count is loaded records, not unique messages, current concurrency or an
owner-qualified lifetime total. Enter or a prefix click opens a group; its
individual rows retain the ordinary exact-time/Evidence expansion. At most
16 output groups and the existing 16 event-detail groups can be open.
Grouping retains every loaded raw record, identity, hash and independent
history/live cursor. It neither copies omitted transcripts nor changes source
history. **Older** or upward navigation requests one bounded page at a time,
even on a short collapsed canvas; repeat to continue and hide to cancel.
There is no automatic fill loop or page-budget renewal on incoming chunks.
Browsing anchors and visible group selection follow prepend, append, split,
merge and eviction. Short browsing canvases may leave space below their anchor;
live-following alone enforces bottom alignment. Automatic resize clamps do not
become user browsing or history demand.

Timeline keeps the purpose, compact source/input qualification and bottom-anchored
work canvas visible. Expand **Evidence** for exact source IDs, qualifications,
effort reference, diagnostics and the Bridge feed selector; **Creation** expands
the owner's supplied creation context there. Preparation failures remain visible
in the compact indicator even before a source token exists. Ordinary membership
pauses retain evidence as inactive, not as invented source errors; actual errors
and head fences remain intact. Navigation failures also remain visible outside
the collapsed evidence area.
Agenda startup waiting remains unknown rather than a failure. A resumed inactive
source needs a fresh snapshot and its exact bounded retained-boundary replay;
head changes, immutable conflicts and exhausted proof budgets still refuse it.

Collapsed rows show local clocks and literal summaries, with a date at each
local-day boundary. A `~` clock is observation time, not an occurrence claim;
`--:--` means the local clock is unavailable. Source timestamps outside the
local zone's representable range remain intact in detail rather than aborting
reflow or acquiring a fabricated clock. Enter expands the exact timestamp, actor, event kind and
evidence qualification. Actor columns do not infer relationships or concurrency.
The stable scrollbar gutter is excluded from line wrapping so narrow scrollable
views do not silently clip text under the scrollbar.

Qualified native entries retain their actual mutation kind, saved-record fence,
captured head and unknown actor; sampled changes do not acquire those semantics.
An owner entry can fit its wire bound while exceeding a display field. The
projection then shows an explicit detail omission with the original canonical
entry hash/byte count and retained source/event/cursor identity, without changing
owner history or enlarging display limits. Its compact native mutation kind
and saved-record fence remain in that envelope. A fence or source reference
that cannot fit retains an explicit field-specific omission with its original
hash and encoded byte count. Actual payload references remain
unchanged; no raw-detail endpoint is inferred when the owner supplied none.

Live and backward-page cursors remain independent. Browsing retains its anchor
and unread updates; retention gaps preserve their source, floor, reason and
reconciliation information without accepting a new live position. Head,
resident, record-incarnation and journal replacement failures remain explicit;
Refresh obtains a new qualified selection. Hiding Timeline retires its optional
reader and pages, and reopening obtains a fresh snapshot. The selected worktree
reader, existing coarse Agenda subscription and optional selected Bridge
reader provide three-source composition without a panel fleet poller. Rich
Agenda reads reuse coarse publication demand and the
[owner-managed placement/reader](../plugins/efforts/skills/driving-effort-agendas/references/managed-placement.md);
they do not create another permanent feed. Source support is not installed/native
qualification or rollout.

The optional Bridge adapter selects the enabled payload through shared plugin
activation, uses the selected worktree's qualified Claims owner reference, and
invokes its passive `observe-owner` operation across a process boundary.
The owning CLI negotiates the running capability and resolves its own validated
installation, without inheriting another plugin's installation receipt or root. A
child-local no-provision fence also covers older payloads. Missing support preserves Summary and the
independent sources. Current membership appears separately from history:
bootstrap facts do not become fabricated creation or activity events.
Owner upserts/tombstones retain their exact observed-change and retirement
qualifications, never claim release. Source head, liveness, freshness and
answerability stay unknown; the independently selected worktree/head fence
controls admission and retires the reader when that selection is invalidated.
Paginated replacement stages bounded intermediate membership without publishing
it as current facts; current members remain unknown until replay completes.
Bootstrap positions must equal the retained head. Delta bases must remain within
retention, and their delivered positions and pagination flags must agree.
Global sequence gaps and empty caught-up reads remain valid because unrelated
owners share the sequence; they do not manufacture local events.
Already-published rich Agenda work is admitted before optional Bridge startup
waits. Worktrees and Bridge preparations start independently, including after
Refresh while Bridge evidence is exhausted. A blocked optional bootstrap cannot
delay the healthy Worktrees reader or its live updates; each attachment retains
its selected-head and revision fences. Unrelated-owner heartbeat progress retains
transport continuity without a productive milestone.
See the [owning membership contract](../plugins/agent-bridge/docs/owner-membership.md).

The keyboard-accessible Bridge feed selector defaults to membership discovery.
An exact owned or represented session from the delivered complete membership
state can instead be selected for the owner's separately negotiated
[`observe-session` and `session-observation-history` reads](../plugins/agent-bridge/docs/session-observations.md).
This replaces the one held Bridge reader; it does not add a permanent worker for
each delegate. Membership discovery is explicitly paused while a session is
selected. Returning to membership obtains fresh facts and resumes its push feed.
Explicit Refresh also returns to membership so a restarted Bridge's current
generation is reacquired before selecting the session again. Reselecting a
same-incarnation exhausted session reconciles its bounded baseline while
retaining the old window's integrity evidence.
Inactive streams retain loaded evidence and immutable proofs, not current
availability. Their old head markers cannot restart an unrelated active reader.

The selected worktree/head remains Manager's independent admission fence. The
session's actual stored project/worktree, registry, durable identity when known,
hosting generation and log incarnation stay separately qualified in its source
facts and wire positions. Represented sources retain a null project rather than
borrowing the parent project; arbitrary federated relationships are not acquired.
The owner's snapshot tail, contiguous live pages and finite backward pages
retain their native identities, content hashes, omission references and partial
coverage. History never advances the live cursor. For an empty log, the selected reader
waits on the owner's existing follow stream before admitting its first real
event continuity. It shows unknown history and remains cancellable during that
wait; Refresh cannot turn a provisional observer scope into a new proof budget.
Replacement or retention gaps require Refresh.
Missing recorded timestamps display as unknown instead of a fabricated clock.
Historical input/resolution events do not prove current pending requests or
answerability, and compaction starts do not qualify active pre-window capacity.
No answer, session start, claim release or installed/native acceptance is implied.

For current input and optional tool statistics, Manager reads the selected
executable's passive `version --contracts-json` declaration once per selection.
Only a supported CLI receives `--current-requests` or `--tool-statistics`; that
CLI independently negotiates the running daemon before sending each optional
query. An exact typed unsupported-capability refusal before any source read
removes only that optional flag, leaving the other feature independent. An
unrecognized invocation, malformed source frame or uncertain transport outcome
is not retried as a baseline request. History requests carry neither option.

The short `N starts (observed range)` label counts owner-reported native
tool-start **record occurrences**, not unique calls, completed/running work or
session-lifetime totals. Evidence retains the exact source scope, interval,
native head, observed-record count, contiguity and partial/unknown durability
qualification. Empty/unqualified or unsupported statistics remain unknown,
not zero. A qualified nonempty range containing no starts may show zero.
Backward pages, duplicate delivery and display eviction cannot recalculate the
count; replaced source continuity requires a fresh scoped observation. The input
label remains a local waiter observation, with full coverage/reason and unknown
answerability in Evidence. Both projections share the existing selected reader
and one bounded UI callback; no extra worker, history scan or rendering I/O is
introduced.

The same reader delivers bounded live waiter state independently of the timeline
window and history pages, including while an empty log still waits for its first
real event. State-only revisions do not advance event positions or append rows.
The selected-session indicator distinguishes observed pending waiters, complete
local absence at the observation, partial coverage and unknown qualification.
It never copies questions or answering selectors, claims the whole actor is
blocked, or submits an answer. Historical request occurrences remain historical.
Selection/head changes, errors, gaps, hiding and reader retirement invalidate
the corresponding live display; late old callbacks cannot restore it. Membership
discovery keeps its existing geometry and does not display a session-specific
waiter indicator.

Integrity-budget exhaustion does not discard evidence on ordinary reconnect or
tab reopening. With Timeline visible, explicit Refresh or Reconcile source first
obtains a fresh owner-qualified snapshot and admits a separate bounded observation
window before replacing the exhausted one. Only the exhausted source receives a
fresh baseline: other providers retain their exact bindings, accepted cursors,
facts, rows and immutable proofs. Proof count, cursor count and integrity-byte
budgets have fixed equal partitions for Worktrees, Agenda and Bridge within the
unchanged total bounds. An exhausted owner cannot consume another owner's
admission capacity, including Agenda commits while its fresh baseline is pending.
Explicit reconciliation also refreshes an exhausted rich Agenda from its actual
latest owner page, retaining the admitted store identity and revision. Simultaneous
owner exhaustion is recovered independently in either completion order.
Failed, unqualified, stale or
conflicting snapshots leave the old proof fence and cached rows intact. A retained
browsing anchor and unread state carry into the new window where available.
This replaces ephemeral viewer evidence, not owner history or work/resource budgets.
Exhausted windows remain separately inspectable on the attachment for the
observer's lifetime, never as active facts. Retention is bounded to 32 windows
and 16 MiB of encoded rows plus integrity-evidence accounting; reaching either
bound refuses another baseline rather than silently evicting evidence. This is
in-memory viewer retention, not durable archival.
Within the timeline canvas, arrow/PageUp/PageDown scrolling selects a visible
event and Enter expands it; mouse selection is highlighted. Home browses the
oldest loaded event and requests bounded older history. End/Follow returns
selection to the live bottom. These controls do not change application focus
or Escape behavior.
Up/PageUp and an unmodified upward mouse wheel also request older history at
the loaded top when content is too short to scroll. Modified horizontal wheel
gestures retain Textual's default handling. While browsing, live appends preserve
the padded event/screen-row anchor rather than shifting it as bottom padding shrinks.
Immutable event references are validated once when their event is constructed,
not rebuilt during paint or repeated index scans. The bounded height index
reuses unchanged heading/detail geometry only when text width and actual text
match. Clock labels, width, lane overflow, expansion and loaded relationship
details therefore invalidate the relevant geometry. Index/merge bookkeeping
still visits the bounded loaded window; only requested display rows become
strips, and no whole logical source history is materialized.
Timeline adapters receive independent request-local stop events. Teardown waits
for their bounded cleanup, including a naturally completed read already being
finalized, without canceling adapter tasks or discarding their handles. Cleanup
errors remain explicit after selection retirement. A terminal cleanup failure
also stops automatic head-change rescheduling and retains its diagnostic.
Disconnects, frame deadlines and invalid source replies are read failures:
after successful resource retirement, Refresh or tab reopening can subscribe
again. Only a typed failure of owned process/pipe retirement latches cleanup
and blocks replacement; ordinary transport failures do not poison that gate.
Adapters normalize read failures into `TimelineSourceError` and tag failed
owned-resource retirement with `TimelineCleanupError`, including during stop.
Controller terminal failures retain their source identity and exception custody
independently of reader/page task slots for the controller's lifetime. They fence
further reads and cooperatively stop the affected source, not healthy providers.
Recording precedes task completion, with synchronous completed-task harvesting
before new page admission; delayed done callbacks cannot lose the fence.
Selection retirement and repeated close still propagate the retained failure.
The same retirement marker covers POSIX group sweep/reaping and portable
process/pipe cleanup; an ordinary read timeout remains distinct. Legacy Agenda
reports terminal retirement failures without retrying its subscription or
disabling Summary/Timeline. Its observer-local terminal gate also prevents
Refresh from starting a replacement while custody is unresolved. Scoped engine
readers retain terminal failures, and the base engine API preserves their type
instead of treating them as compatibility errors or an empty result.
Agenda's terminal gate is separate from Timeline and base-engine read custody.
Base-engine and read-only Picker-review scopes refuse further owned reads after
a retirement failure; compatibility fallback does not reinterpret that failure
as an unsupported flag. POSIX capture-file/descriptor closure and streamed
diagnostic cleanup use the same operation-local retirement classification.
The [implementation and validation plan](../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-live-timeline.md)
retains the provider, joined-app, native-client and installed-adoption gates.

Agenda is an optional, display-only tab subscribed through the selected engine's
`agenda-feed` CLI (`worktree-agendas-v1`). It starts only after the current
worktree, project and exactly one head session are resolved. Complete pushed
frames update the widget without agenda-file reads or an agenda polling timer.
Refresh invalidates and retires the prior subscription before replacing it;
head changes clear the displayed agenda and require Refresh. Unsupported,
unpublished, unavailable and malformed data remain explicit, never an empty
successful agenda. After a supported connection fails, at most three reconnects
use bounded backoff and each requires a new atomic snapshot. Publication is
not acceptance, execution permission or an action.

The provider must expand first: this consumer does not install, provision or
start a resident to obtain agenda support. Before sending any new agenda verb,
an owned process calls the existing `worktrees_client.read_health()` in the
resolved provider interpreter, without entering its legacy main/launch dispatcher.
Missing capability/readiness or an unadmitted wrapper shape refuses the feed.
Independently updating Manager against
an older engine leaves Agenda unavailable without disabling other tabs.
See [`docs/live-agenda.md`](docs/live-agenda.md) for ownership, wire admission,
test commands and the separate provider-integration/rollout gates.

Graph shows bounded recorded disposition history and directed resource
references, with reported subagents kept distinct from resource ownership.
Opening Graph reads the selected head session's optional, versioned
`session-observations` provider off the UI loop; initial Summary does not wait
for retained-history scans. Statistics count recorded starts/completions in the
retained file, not unique calls or lifetime totals. Missing, partial, stale and
unsupported evidence remain visible. Reported completion is not agent retirement.
Refresh reloads the graph; leaving the app cancels its owned reads.

Graph also lists the worktree's recorded PR associations, but only when the
selected engine declares `companion-pr-observations-v1` on its status bundle;
otherwise it shows claim-only text and PR state stays unknown. A recorded
lifecycle is association bookkeeping, not current openness. Selecting one
association runs exactly one finite `pr-watch snapshot <repo> <number> --json`
through that same engine (no host or token overrides); **Re-read PR** repeats
it. The result shows open, closed without merge, merged, or unknown, with its
last-observed time and source. A reply must match the selected provider,
repository, number and endpoint and the version-1 snapshot schema, or it is
shown as unavailable. Review, check and mergeability fields are diagnostics
only and never grant consent or merge authority. Refresh or exit invalidates
in-flight reads. Bounds: 24 associations and a 256 KiB reply.

The optional attention strip shows the selected head CLI's **last-reported**
busy, idle or awaiting-operator state and transition timestamp. Continuity
remains unknown: this is not proof of a currently pending question or of no
pending input. The Return to CLI button may read **CLI (last: input)** or
**CLI (last: idle)**; it only navigates and never answers or approves anything.
The selected engine's versioned `session-attention` contract is optional;
unsupported, malformed or absent evidence leaves attention unknown without
disabling Summary, Claims, Graph or Recovery. Intent/question/transcript text
is neither read nor displayed.

Attention has its own cancellable worker and single-flight reads. A five-second
timer revalidates only the displayed worktree's head, then reads that head's
bounded pulse through at most two engine calls (five-second timeout each).
Failure/unknown evidence backs off for at least 30 seconds. A changed head
clears the nudge and pauses reads until Refresh; refresh/reopen invalidates
the prior selection, and late results cannot replace it. No history scan,
fleet scan, persistent cache or new service is added. In a same-window
observer, periodic engine reads require native evidence that its exact pane
and original incarnation are visible with an attached client; hidden/unknown
visibility pauses them. Native visibility uses one exact-pane query with a
two-second bound. It reads `session_attached` rather than treating a nonempty
client listing as proof: PSMux can synthesize such a listing while detached.
Opening or explicitly refreshing permits one foreground snapshot even if
visibility cannot be established. The popup polls only for its own lifetime.
Leaving either view cancels owned engine reads. Production cold/warm latency
and rendered/native-client attention acceptance are not inferred from these
polling/timeout limits.

Recovery retains session lineage and the explicit, human-gated Cut over action
for an already-prepared handoff. Selecting a tab never triggers cutover.
Opening and explicit Refresh retain the selected engine's existing session-list
declaration. When it supports
[`agent-worktrees.handoff-head-observation` version 1](../plugins/agent-worktrees/docs/handoff-head-observation.md),
one additional passive read compares the actually recorded handoff consumer with
the durable head and the Picker's sampled resume target. Recovery shows all
three separately beside previous/new lineage. Match, mismatch, unknown and
unavailable are distinct; a known empty head is not a failed read. Detailed
baton/incarnation/activation evidence is collapsed by default.

The optional read runs off the UI/render path through the same selected-cwd,
caller-owned process boundary, with a five-second hard deadline and existing
child cleanup. Discovery and its optional read retain one resolved engine argv,
so activation or rollback between requests cannot transfer a declaration to
another runtime. It publishes only to the current load generation. Reopening
derives another observation; there is no timer, cache or persistent Companion
registry. Legacy, unsupported, malformed, failed or rolled-back providers leave
all other views and existing actions usable. Detection performs no head repair,
report filing, handoff consumption, session launch/switch/retirement or input.
This is a sampled source comparison, not a future Picker launch guarantee or
qualification of the original live manual-cutover tests.

The existing Ctrl+K popup also offers **Show Summary pane**. It opens or
reuses an observer in the CLI's existing mux window and zooms it; **Return
to CLI** restores the original CLI pane without restarting its process.
The popup itself and its key remain unchanged. The explicit shell entry is
`worktree-manager companion-pane --action show|return [--cwd PATH]`.

Pane actions require an installed Manager runtime and an unambiguous,
launcher-registered CLI pane. They verify the native session incarnation,
pane directory and window before switching; older sessions without that
identity refuse rather than guessing pane zero. Reattachment preserves a
recorded same-owner CLI while native evidence is unavailable, and removes it
only when native inventory confirms its loss. Snapshot-fenced registration
cannot overwrite a concurrently created CLI identity.
Windows pane targeting keeps the session explicit and checks the selected
client's `-V` and live server's reported version and native identity before
selecting compatible 3.3.x or 3.4.x syntax. Discovery resolves registered bare
names through PATH, is cached by executable/session generation and shares the
operation's deadline. Unknown or incompatible releases refuse before pane
actions. See the [native qualification
packet](../TESTING.md) for the supported syntax and evidence limits.
Replacing a conflicting recorded native incarnation also requires current
native session evidence; a delayed reconnect cannot displace its successor.
The observer uses the constructed runtime's bootstrap, not source or `uv`.
Concurrent callers serialize lookup and creation for that native CLI
incarnation. While open, the observer checks only its registered CLI's native
presence once per second off the UI loop and exits when that owner is
confirmed ended; unavailable evidence never means death. It cannot keep an
otherwise-ended CLI session alive indefinitely or adopt a successor pane.
An already-bound observer continues checking its pinned native owner if
registry or CLI metadata disappears.

Native two-line clickable mux chrome and installed attention qualification
remain tracked by [#8811](https://github.com/ThomasMichon/copilot-extensions/issues/8811).

## Set up the harness

For UI development without installing or promoting a candidate, use the
[read-only source Picker review loop](docs/source-review.md). It renders the
production Picker over existing local owner data while refusing live effects.

Managed authoring launch honors the target repository's
[`expose_agent` hosting veto](../plugins/agent-worktrees/docs/repository-agent-policy.md).
The Manager uses the engine's read-only policy API, never imports the plugin,
and refuses engines without hosting admission instead of dropping the guard.
Shell-delegated launch resolution defers instruction projection publication
using a call-scoped flag while retaining the selected engine provider and
the caller's profile, PATH and installation-selection environment. Parent Python
runtime overrides remain stripped; the flag does not alter later launches.
Recovery resolves its runtime first, then checks the actual setup repository
before running its setup script. Source-only worktree allocation remains
available through `agent-worktrees create --json`; it does not return a launch
plan or adopt the source repository as a harness.
The Bash launcher parses profile environment assignments once into a quoted
array, exports those values and passes the same literal assignments to tmux.
Empty values, embedded quotes and newlines are not forwarded as shell source.
The existing AHP credential exclusions remain intact. The engine does not
classify shell renderers for native eligibility: an explicitly empty
`BASH_ENV` permits the copied native seeded setup, while actual nonempty or
unknown mux initializer state retains the existing owner. This is independent
of marketplace cells and does not establish full launch or removal survival.
Post-update interpreter refresh recognizes the legacy Python seed envelope
only; it does not replace native Bash/PowerShell hosts or a POSIX `env` prefix.
The native prefix applies the selected empty initializer before Bash while
preserving Python-sensitive backend environment values. Older launchers that
overwrite every seeded executable need this correction; no new renderer
handshake or planner-admission fence is introduced.
The shell launchers defer setup-log creation/pruning, trace publication, root
registration and automatic update staging/apply until the actual target passes
the versioned owner check. Early diagnostic messages remain in memory. Recovery
checks its setup directory first; refresh checks the plan's execution directory
or explicitly resolves the selected project's `get repo-dir` before maintenance.
Argument errors retain their original ordering. Direct inspection/source CLI
commands run without implicit maintenance or persistent launch diagnostics.
Agent discovery requires explicit `repos.yaml` `agent: true`, independent
of editing class; a current source directory is never promoted into the project
profile roster. Direct repository eligibility remains opt-out, not default-deny.

A bare `worktree-manager` invocation reports its running and installed versions,
observed core/engine availability, baseline prerequisite gaps, and registered
project count. Next steps are derived from that snapshot: an installed setup
is not repeatedly presented as a first-time Phase 2 install. The summary is
read-only and does not claim a complete health or authentication verdict;
use `doctor` for deeper inspection.

```bash
uv run python -m worktree_manager doctor        # report prerequisites + the core install
uv run python -m worktree_manager setup         # plan provisioning + core install (dry-run)
uv run python -m worktree_manager setup --apply # actually provision + drive the real installer
```

`doctor` is read-only; `setup` is **dry-run by default** and only changes the
machine with `--apply`. Apply rechecks prerequisites after provisioning and
continues in the same invocation when they are available. Failed provisioning
or unresolved prerequisites stop before the core installer; a PATH-changing
install that still leaves a gap requests a shell restart. Re-running is
idempotent and can heal a partial install. Required Python must return a
successful, supported version from the exact discovered executable. Detection
continues through the existing `python3`/`python` aliases and their PATH entries
when an earlier Python is unusable or too old, without executing WindowsApps
Store aliases. If none qualifies, the first candidate remains the diagnostic
path and Python remains a prerequisite gap. Fallback lookup stays within each
PATH directory, tries remaining Windows PATHEXT matches, and retains the
current-directory meaning of empty entries.
Detection does not change PATH,
acquire Python, search private runtimes or add launcher/install-root discovery.
Windows also requires a working PowerShell 7 (`pwsh`) host for the native core
installer. A missing, older or unqualified host stops setup before core or
repository work. As with Git, setup reports a manual system-package command;
it does not run WinGet or request elevation for this prerequisite. Install the
reported package, open a fresh terminal, and retry. POSIX setup does not require
PowerShell.
The core install is never
reimplemented — the Worktree Manager locates and calls agent-worktrees' own
`install.{ps1,sh}`. Core detection uses the same read-only, installation-context-
qualified runtime selection as the engine client. Empty runtime/versions
directories and a leftover global stub cannot satisfy it; a selected complete
runtime does not require a legacy global stub. This is installed-runtime
selection, not a live service, authentication or complete-toolkit health check.
A failed core installer remains a failure even when an
installed-state marker is present; setup success is not a full authentication
or repository-adoption verdict.

### Optional repository setup

`setup` may also select an HTTPS Git source after the existing prerequisite
and core stages:

```text
worktree-manager setup --apply --repository <https-git-url>
  [--repository-target <absolute-path>] [--repository-commit <full-commit-id>]
  [--trust-repository-setup] [--context <declared-name>]
```

Without `--apply`, this remains a plan. The target, commit and trust options require a
repository and are validated before provisioning. Apply requires fresh actual
core readiness, then invokes the engine's
[`repos setup` contract](../plugins/agent-worktrees/docs/repository-setup.md)
with inherited stdio. An older/absent engine refuses this optional stage, not
the completed core installation. Repository failure never uninstalls core.

After installing Manager, the equivalent repository-first entry is:

```text
worktree-manager register <https-git-url> --apply
  [--repository-target <absolute-path>] [--repository-commit <full-commit-id>]
  [--trust-repository-setup] [--context <declared-name>]
```

`register` requires one repository URL and reuses the same prerequisite, core,
acquisition and trusted-setup flow. Without `--apply`, it only previews the plan.
It does not replace the engine's project-name `register` command or grant setup
trust implicitly. The selected repository's explicitly trusted setup owns any
further consented configuration; Manager does not add another setup engine.

`--repository-commit` requires a full 40- or 64-hex commit ID. It is forwarded
as the engine's explicit `immutable-commit` capability request, never replaced
with remote HEAD if that selected engine refuses it. Existing targets must
already match; only a new checkout may select the requested commit. Unavailable
commits and unsupported older receivers retain completed core work and refuse
the repository stage without an unpinned retry.

`--context` selects the trusted harness's optional
[engine-owned continuity context](../plugins/agent-worktrees/docs/repository-setup.md#optional-selected-continuity-context).
The engine/Fleet runtime owns source selection, original-operation enrollment
and authenticated outbound connection; Manager does not implement a private
installer or read grants. Missing/older support retains completed core work.

The existing bootstrap forwards the same `setup` argument array. Selecting a
repository does not change the Manager's installation/update source, enroll
maintenance, launch an agent or imply full machine adoption. Explicit trust is
required before even the repository's check entry runs. A repository without
the supported declaration is unsupported, not inferred from a README or hook.
Native synthetic consumer coverage is not proof of actual attended provider
authentication or a working adopter onboarding command.

### Optional Windows system Python bootstrap

The Windows **bootstrap**, not the installed Manager CLI, accepts
`--system-python` as its first argument:

```powershell
& .\bootstrap.ps1 --system-python setup --apply --repository <https-git-url>
```

It qualifies a physical Python 3.11+ from PATH or the standard Windows Python
registration, requiring a valid Authenticode signature before probing it.
Signature verification loads the security module from the current host's
`PSHOME`, not an inherited module-search path from another PowerShell version.
WindowsApps aliases and virtual environments are not substitutes. If no
qualified interpreter exists, an actual interactive terminal may install the
user-scoped `Python.Python.3.11` package through WinGet. Bootstrap repeats
signature, version and base-interpreter qualification, then exposes only the
qualified directory at the front of its process PATH;
a package-manager success code alone is insufficient. A blocked/failed install
or missing WinGet remains an error, without a managed-Python fallback or device
policy, Defender, registry-policy or execution-policy override.
An unexpected verification error or cancellation is not interpreter absence:
it stops before package discovery/acquisition instead of triggering an install.

The selected interpreter hosts canonical Manager runtime installation; this mode
does not migrate existing runtimes or change their lifecycle owner. UV/Git
provisioning, repository arguments and Manager source/update configuration retain
their existing owners. Without the option, Python acquisition remains UV-based.
Repeated mode flags and repair combinations refuse before acquisition.
The Bash counterpart explicitly refuses this Windows-specific mode before
effects; it does not silently substitute a distribution package route.
This optional mode alone is not a full adopter readiness or attended-auth claim.

Every Windows bootstrap puts its selected Python directory first on the
bootstrap process PATH before invoking Manager and nested installers. Both
bootstrap and the installed update command reconcile PATH after a successful
waited manual update, even when Manager is already current. They qualify the
running interpreter's physical Python 3.11+ base, place its directory first on
the user PATH, read the setting back and notify Windows of the change.
Neither publishes a private runtime's venv as the public prerequisite,
removes an older Python, or rewrites machine PATH. Existing parent terminals
cannot inherit a child's environment changes and must be restarted; machine
PATH precedence remains administrator-owned. Automatic, detached, targeted or
inspection/recovery commands, failed updates and contained tests do not persist
this preference. Only byte-identical duplicate entries are removed; other case,
Unicode and separator aliases are preserved. Verified desired PATH is notified
even when no write is needed, allowing a failed notification to be retried.
Concurrent publishers share the bounded Manager configuration-writer lock
through registry read, modification and verification, preserving each other's
entries. This does not serialize unrelated tools that write user PATH directly.
A physical Python directory containing `%` is refused
before writing an expandable user PATH, rather than publishing a different
directory after Windows variable expansion; literal registry strings retain
their original type.

The Manager is a standalone canonical Python-installer-engine adopter. Bootstrap
selects Python 3.11 or newer without running project resolution, then the engine
retains the source/dependency/configuration inputs and constructs a non-editable
runtime. Governed `uv pip install` selects runtime dependencies for this platform,
not development groups or unrelated platforms, while retaining the configured
indexes and the seven-day minimum external-dependency age.
The Windows bootstrap resolves the selected UV command's filesystem links
before its first Python lookup and retains that physical path for acquisition
and the subsequent lookup. It does not launch a WinGet executable link or
reselect UV if PATH changes during bootstrap; missing or unsupported targets
fail before runtime construction without changing Windows mitigation policy.
Git staging checkout also enables long-path handling only for that invocation,
without changing user or repository Git configuration. Before constructing the
runtime, bootstrap verifies the tracked checkout is complete and unchanged;
a zero checkout exit code alone is insufficient.

Only a candidate passing isolated installed-origin and real CLI-import health
receives canonical completion and becomes `current-version`. Construction failure
leaves the previous selector and mux untouched. A legacy payload-only or
interpreter-only slot is retained; repair constructs a distinct generation rather
than rewriting an active runtime.
A completed matching slot without a nonempty valid selection is not
`already-current`: dry-run plans repair, while apply health-checks/reuses the
canonical ready candidate and restores selection without rebuilding it.

Selector publication uses canonical created-object receipts while holding the
selector lease. Normal bootstrap and self-update start the pinned incoming
generation through its own installed activation entry before publishing commands,
registrations or selectors. Successful V2 startup commits forward selection;
V1 state or a late cancellation cannot restore the predecessor's selectors.
Cancellation after startup finishes owned publication without restarting V2.
Publication I/O failure retains its evidence and reports an incomplete operation.
Selector-only operations that have not committed an application startup retain
their previous-state recovery, including absence. Recovery never copies over
foreign objects.
Historical selectors retain their existence and exact bytes as rollback state,
including empty, malformed UTF-8 and unsafe old names. Undecodable selection is
reported as unknown; unsafe old names are never resolved or retained and cannot
veto a valid incoming target. Valid old slots still receive runtime custody.
Concurrent changes to either raw selector abort publication. Incoming target
names remain strictly validated before selector or retention custody.

On Windows, non-update bootstrap construction and `self-install --apply` enter
the existing installation execution driver. A caller with a live inherited or
scoped witness joins that owner; a cold explicit caller submits a fixed
installation recipe and drives the same finite native executor. Neither path
calls `ensure_worker`, creates another mutation lock, or falls back from a
failed background launch. Application arguments remain outside the bootstrap
request. Its bounded completion receipt binds the root, source digest and
actual installed slot; later selection by another request cannot silently
change which runtime the bootstrap dispatches.

Fresh `update` bootstrap on either platform instead submits one complete
Manager update request: Manager construction, activation and the
subsequent suite update remain under the same execution custody and outcome.
The ordinary caller starts the existing finite worker and waits for that whole
result; losing the observer does not discard accepted work. `--detach` reports
acceptance, not completion, and `--supervised-debug` drains the same queue inline.
No new daemon, mutation lock or foreground fallback is introduced.
Request status, recovery and authentication inspection instead use the current
CLI outside installation submission. Read-only queries never authorize implicit
fence repair; explicit recovery does not enqueue a new job that would hold its
own recovery lock. Structured results remain on stdout, separate from diagnostics.
If worker startup fails after bootstrap admission, detached JSON mode returns the
admitted request ID, log path and admission snapshot with the caller error.
The caller still fails; a pending receipt is not a running worker or completed
installation. Inspect that same request before any further submission. The
receipt does not authorize replay, foreground fallback or a different launch
boundary.
Foreground waits preserve the admitted identity on stderr when a worker exits
between acknowledgment and claiming the request. Foreground `--json` remains
unsupported and is rejected before admission; this receipt does not enable it.
POSIX dispatch also waits when it owns a completion scope but deliberately
forwards no witness. An unowned non-update
launch retains process replacement. Source-only entries resolve their current
controller's declared driver dependencies before either platform branch.

Each update acquisition has its own staging directory. Before submission, the
bootstrap freezes Manager and SDK inputs through the canonical source freezer.
Unmaterialized sources also retain the Manager's declared source-preparation
tools inside that controller, with their bytes included in snapshot identity
and readback validation.
The execution root and retained-input directory use the queue owner's protected
creation helper before any source is copied; existing permissions are preserved.
Snapshot identity is independent of request sharing: unchanged source inputs,
assets and revision reuse retained bytes even for unshared attempts. Those
attempts carry a current-controller-only marker in the queued invocation, not
in selected application arguments. No pending or live-worker inputs are pruned.
Successful callers remove only their acquisition checkout, including detached
callers after acceptance; the worker continues from its retained inputs.
Detached callers first launch the worker through a finite retained controller,
so leased startup dependencies also come from retained source. This helper
inherits the caller's containment; the existing daemon owner governs worker
lifetime and startup acknowledgment.
Failed callers preserve their acquisition checkout for diagnosis.
Equivalent pending callers share a request when its immutable source and
configured marketplace selection agree; distinct arguments or changed source
remain separate. An active suite may still discover later marketplace inputs,
so a new arrival queues a fresh attempt rather than joining it on the strength
of a Manager/SDK snapshot alone. Unresolved selections do not share even
pending work.
Prepared installation sources remain data for the retained current bootstrap
controller, so an older selected payload need not implement new internal flags.
Automatic policy is rechecked against the saved request before construction.
A caller-relative hold-file override is expanded before queue identity is
created, and saved source configuration is checked before construction and
immediately before publication.
A manual update may use the existing explicit repair authorization to release
an interrupted queue fence once; the prior failed outcome stays recorded, and
that authorization does not certify cleanup or permit displacing a live owner.

### Local promoted installation inputs

For recovery independent of an installed Manager or dispatcher, the current
Windows bootstrap also exposes passive-marker-guarded
`--describe-recovery-source` discovery and `--recover-source <request.json>`.
See [Fresh bootstrap source recovery](../docs/fresh-source-recovery.md) for
the exact preview/install request, immutable selection, native new/legacy
compatibility and partial-outcome contract.

The standalone bootstrap and `scripts/runtime_install.py` advertise the
read-only `--describe-source-inputs` descriptor. Its `install-source` capability
accepts `--install-source <absolute-snapshot>` before application arguments;
`--repository` remains the application's repository-adoption argument.
Unknown or ambiguous `--repo` syntax is not a Manager-source selector.

`tools/local_install_source.py` freezes a Git commit and builds the Manager's
first-party wheels, or reuses explicitly selected matching prior inputs.
A source selection with `--main-head` consumes the canonical
Main-seeded, version-preserving local composition and retains its Git association
through the prepared native profile. The selected source's changefiles remain
untouched. Unseeded source preparation retains its historical behavior.
See [the install contract](../docs/install-contract.md#local-wheel-applications-for-manager-and-worktrees)
for the association, explicit first-party reuse, automatic complete-application
output reuse and held-input checks. Existing commit/tree and assembly
identities distinguish deployment generations even when package versions are unchanged.
Use repeated `--project` selections for a bounded core preparation, for example
`--project standalone:worktree-manager --project agent-worktrees`; this avoids
building the rest of the portfolio. The default remains Manager only, and the
separate `--suite` option explicitly selects all declared projects.
Ordinary source installation remains available. Resource-packaging hooks use
the constructor's existing isolated source-build fallback rather than its
static-project wheel cache; an explicitly prepared wheel application does not
run those first-party build backends during installation.
`--finalized-main --main-head <commit>` additionally verifies Main ancestry.
Only real Dev-to-Main promotion accumulates release versions.
This build step produces first-party preparation data, not yet an installable
native runtime profile. An incomplete snapshot explicitly refuses installation.
Wheel files belong in ignored build output, never Git.

The Manager wheel embeds the selected 14-library first-party core under ordinary
import names, with declared resources and LICENSE notices. The standard
`first-party-core` dependency group and existing source mappings describe build
inputs; static runtime metadata retains ordinary third-party dependencies and
their transitive constraints. Manager uses its own application environment.
Combining that embedded wheel with separately installed copies of the libraries
is refused on actual payload-file ownership collision; the shared-core inventory
and profile identity are not silently changed. The complete source directories
remain associated build inputs, and the sdist carries the inputs needed to
rebuild without the original checkout. This does not qualify cold construction,
serving, cutover or rollout.

Installed wheels expose the non-routing
`runtime_source_prepare.prepare_application_inputs` API for completing an
already-admitted first-party snapshot through the canonical native profile
pipeline. Its declared multi-root toolkit is independent of the original
checkout. Callers explicitly supply their prepared tooling interpreter,
absolute native Python/UV, source identity and remaining deadline; no host
dependency bootstrap, runtime selection or activation is implicit. This is
provider reachability, not adoption by default/configured-source update.
See the [installation contract](../docs/install-contract.md#local-wheel-applications).

A complete snapshot additionally carries its prepared native profile and exact
wheel closure. The adapter retains and inspects those bytes through the existing
constructor and real installed-health gate. It does not resolve dependencies,
execute first-party backends, clear automatic-update inhibition, or acquire
permission from a running Manager. Independent native installation custody
still governs publication; foreign or unsettled owners are not bypassed.

Read-only source selection and profile reuse live in
`scripts/runtime_install_selection.py`. Unusable historical selectors, source
bytes or constructor/completion receipts select fresh preparation instead of
vetoing the incoming source. They never authorize reuse or an in-place rewrite
of the incumbent. Incoming source identity, package selection, hashes and wheel
inputs remain strict; failures reading incoming inputs retain their I/O cause.

The programmatic `self_install(stage_only=True, dry_run=False)` boundary also
supports a running nested wheel application. It qualifies that application's
original source/profile and installed inputs under the existing retention
owner, proves passive readiness for its exact generation, then rechecks the
inputs before returning `staged`. It does not treat the installed slot as
source, rebuild it, publish selectors/commands/providers or activate it.
This does not add a high-level `self-install --stage` command or change the
shared application project's environment-only `install --stage` entry.

An explicit publishing install replaces its declared Manager command files
even when their historical contents were edited or no longer match an old
template. Unrelated commands are not removed. Existing path protections,
publication custody and atomic replacement remain; real lock/write failures
retain their I/O error instead of being masked by a historical-byte veto.

Construction and stage-only readiness do not adopt or register a scheduled Task.
Separate launch-broker operations keep their own owner and durable runtime
reference; application selection does not undo those independently qualified
actions. The normal `spawn_daemon` default remains unchanged. This source
integration does not authorize host installation or claim complete cold-install,
desktop or maintenance qualification.

PowerShell, CMD and Bash launch the selected slot through the canonical retention
bootstrap in isolated mode, preserve its exit status, and never resolve or acquire
dependencies during ordinary launch. Mux cutover uses that same constructed
interpreter, not an old interpreter with the new source on `PYTHONPATH`.
The PowerShell entry fails nonzero if that interpreter cannot start and
preserves the actual exit status when it runs.
CMD consumes the runtime invocation and its status-preserving return on the same
line, so an update replacing the launcher during a session cannot make CMD resume
at an obsolete byte offset. PowerShell post-exit finalization displays native
diagnostics separately from its numeric result; successful cleanup text is not a
failure, while a nonzero result or unavailable runtime still reports a warning.
Mux routes and target readiness use the constructed slot's immutable generation,
including repair/source qualifiers. Health preserves the semantic package
`version` and separately reports `runtime_generation` from its own interpreter
prefix, never from the mutable selected marker.
`python -m worktree_manager.mux_serving_observation --root <manager-root>`
emits the version-1 `worktree-manager.mux-serving-observation` result. It
reads the existing authenticated route and native process identity without
starting a daemon or creating a control credential. It reports `ready` with
the actual serving generation, `inactive` only when no route or ownership
markers exist, and otherwise `unknown`. Health binds the routed PID and birth
identity; older health replies are admitted only when the authenticated
rendezvous independently matches the route. An unresolved cutover breadcrumb
is never reported as a ready or inactive owner. `--lock-paths` describes this
runtime's cutover, routing and spawn lock paths without acquiring them.
`--unfenced-absence` ignores those lock files but reports only
`unfenced-absence`, not verified inactivity. It also recognizes a cleared
shutdown route when intact, unsaturated publication history confirms every
recorded owner's original process has exited. This does not account for
unrecorded owners or settle child execution. A coordinating caller must hold
all three corresponding native locks and recheck before relying on it.

The selected, complete physical Manager runtime also provides
`python -m worktree_manager.mux_serving_reconcile --root <manager-root>`.
It leaves an inactive Manager stopped, or uses the existing cutover owner to
replace a verified incumbent with the selected generation. It checks selection,
credentials and native ownership around publication, and never terminates a
candidate whose publication is uncertain. A failed, unpublished candidate is
retired only through an identity-bound OS process owner with confirmed exit;
if that owner is unavailable, the candidate remains and the result is unknown.
Its version-1 result reports
`ready`, `inactive` or `unknown`; an uncertain effect must not be replayed
through another command. The timeout bounds individual waits, not the whole
transaction; each bounded health request and its release share one completion
budget. Neither observation nor reconciliation alone authorizes selector
settlement or rollback; a coordinating caller must hold the appropriate
native ownership and recheck the result.
Normal mux-daemon startup uses the
[platform Python cache policy](../libs/agent-procutil/README.md#platform-cache-provider-contract)
with Manager's durable root as the installation identity, not the cache location.
Unsealed or older providers and explicitly read-only retention/bootstrap controls
keep their separate no-bytecode contract.
Worktrees' direct Manager and fast-Picker handoffs carry the same cache policy
on their frozen engine callback command, using Worktrees' installation identity
rather than Manager's. The callback keeps its original interpreter and arguments.
The retained launch-session registration and pane-wrapper removal callbacks
also use that payload's constructed interpreter and retention bootstrap, without
requiring `uv`. Their bounded metadata work is dispatched through the existing
`agent-procutil` background primitive; only its spawn handshake is synchronous.
Source and legacy project layouts retain their `uv run --project` entry.
The existing Ctrl+K Companion popup uses the same runtime resolver through its
separate interactive entry: it inherits the popup terminal and all standard
streams, rather than using the background metadata dispatcher. Its key, popup
dimensions and close-on-exit behavior are unchanged.
Update availability compares the canonical package version, not the selected
repair/source-qualified slot name; selection and mux identity remain unchanged.

Normal `agent-worktrees update` also repairs an already-adopted Manager whose
registered launcher cannot run. Its new installer settles under the newly
constructed updater interpreter before returning to an older update caller;
the current updater also checks recovery before local profile integration when
its runtime is already current. If an old caller skips installation because a
concurrent update already selected the new runtime, run the same normal update
again: that invocation uses the new caller's recovery check. Copying a payload
alone does not replace code already loaded by an old process.

An explicit `<repo> update` or `worktree-manager update` is manual by default:
the automatic-update KillSwitch does not inhibit it, and manual intent does not
force rebuilding already-current runtimes. Existing `--force`, `--force-runtime`,
recreate and repair selections keep their own construction semantics.
All automatic producers pass `--auto`, including Manager's optional maintenance
before session launch/resume and unattended fleet/self-update sweeps. Independent
schedule opt-ins do not exempt those calls from the hold. Explicit project
updates remain manual. An applicable default-installation hold returns
the visible exit-3 no-op before Manager runtime construction or downstream
installers, while an inactive hold executes normally. The POSIX bootstrap checks
the same canonical admission policy before construction; target-scoped updates
retain their manual/automatic mode through the target parser and engine handoff.
Both native bootstraps anchor a relative hold-file override to the caller's
directory before staging, and queued bootstrap admission retains that exact path.
Independent namespaced cells retain their existing exemption from that default hold.
Interrupted-transaction settlement and live-installer exclusivity remain separate
gates. The existing queued `repair` boolean records manual admission, not rebuild
selection; old records without it remain automatic. Parsers without `--auto`
support refuse the request rather than silently treating it as manual.

Recovery requires the existing provider registration naming the canonical
Manager root and local-bin launcher. Registered launchers may be older or
damaged; their bytes are not historical-version authority. Captured command and
registration bytes must remain unchanged during recovery. It reuses a healthy registered Manager without
source acquisition, never adopts an unregistered installation, and preserves the
Manager's own root, source repository, ref/pin and credential context. The
selected source must contain the canonical recovery adapter; a pinned older
source is an explicit failure, not permission to fall back to `main`. Candidate
construction, selection and mux activation use the canonical installer and the
constructed Manager interpreter, retaining the previous runtime and restoring
selectors on activation failure.

Fresh update bootstrap validates only the Manager package, launchers and provider
registration that it publishes. It does not compare installed Worktrees helper
bytes with the current payload: payload refresh can legitimately leave older or
damaged helpers awaiting repair. Worktrees source, installation-cell and
environment admission remain with the subsequent engine repair and its own
transactional publisher; Manager construction does not modify those helpers.
An existing valid Manager registration owns its fixed local-bin launcher family;
bootstrap snapshots those files and refuses concurrent changes before publishing.
Without that registration, only exact published launcher bytes, including the
current generated CMD, PowerShell and POSIX launchers, are recognized. Edited or
unrelated commands remain protected rather than adopted by filename. Package,
ordinary-path and selected-provider checks remain.
Repair-child launches explicitly forward standard input, output and error to
the existing caller handles while retaining hidden native children and their
completion custody. Redirected diagnostics must reach the durable request log,
not disappear behind an exit code.
Engine repair reasserts and explicitly exports the current driver's scoped
participant witness before entering either publication boundary. Consuming the
incoming environment witness does not authorize an unadmitted replacement
driver; a lost owner refuses before publication or child execution.

Engine repair tolerates transient Windows payload-directory sharing contention
without stopping live sessions or disabling their hooks. A completed native
plugin **update** reporting sharing/lock violation 32 or 33 may retry at most
twice, after its owned process tree is quiescent, within the original shared
acquisition budget (180 seconds by default). Each retry revalidates the
registered source, all marketplace definitions and the requested plugin's ledger
entry after backoff. Installs, POSIX failures, timeouts, uncertain cleanup and
authentication, permission, integrity or policy refusals are not replayed.
Persistent contention remains a failed update; a refreshed payload alone still
does not prove a healthy, selected engine runtime.

On Windows, a positively settled acquisition timeout retains up to 8,192 bytes
from each native output stream before closing its private capture. The registered
origin error includes bounded excerpts from both channels through the existing
credential/URL redactor, without exposing the command representation. Unconfirmed
cleanup retains its custody failure rather than reporting a settled timeout.
This diagnostic recovery does not replay the acquisition or extend its deadline.

For a measured slow-host recovery, set
`WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC` for that update invocation to
a positive finite duration of at most 3,600 seconds, for example `600`. The
durable Manager invocation captures this value, and the worker restores that
budget rather than inheriting a later caller's setting. An absent choice clears
the worker's ambient override. Invalid values refuse submission. The internal
fresh-bootstrap request likewise captures and validates an applicable
environment-selected budget before retention/submission; inspection, recovery
and targeted commands do not inherit that suite-only setting. The internal
Manager argument is consumed before engine handoff: no new shared execution-context
key or engine argument is required, including for older engine participants.
Retries still share one deadline and the same attempt ceiling; timeouts are not
replayed. This does not change runtime-construction, dependency, health, cutover
or rollback contracts. It is separate from the Worktrees installer watchdog.
An older engine that cannot read the current update-driver schema must use the
ordinary fresh-Manager update path; do not downgrade the queue to enable
`--no-manager`.

The **One-line bootstrap** below remains the fresh-install entry, not a required
manual migration step for an adopted Manager. Its explicit
[queue-release mode](#older-client-queue-release) is separate from installation.
Do not rewrite a deployed launcher or install application dependencies into
ambient Python to force recovery.

Bootstrap-owned human progress goes to stderr with UTC correlation time and
explicit running, warning or failed state. PowerShell uses its in-process
monotonic stopwatch for elapsed time and writes text directly, avoiding duplicate
information records in redirected encoded-command capture. Bash reports
`elapsed unavailable` before a portable monotonic provider exists; it does not
claim wall-clock differences are durations. These dependency-free lines do not
load an installed runtime. Native prerequisite diagnostics, exceptions and the
launched Manager's streams and exit status retain their existing ownership;
this is not a claim that those separate producers are all framed.

The fresh Manager's private update-repair prefix then opens its own
`update/manager/engine_repair` human scope before the aggregate update run
exists. Its start, missing-source or caught-refusal diagnostics, and final
exit status use the shared UTC/monotonic stderr formatter. A failed repair
ends that scope as failed and does not start aggregate orchestration.
Native repair children explicitly forward the caller's existing standard
descriptors, retaining redirected Windows streams even when no new console
is allocated. They settle through the existing lifecycle owner; their
diagnostics are not captured or decorated by this scope. The exit-only repair
entry remains unframed when called outside a human scope, and older runtimes
without the formatter retain the original diagnostics.

`doctor` also reports **plugin-catalog alignment**: whether the authored
catalog (`data/plugins.toml`) still matches the discovered plugin membership
(same report as `plugins --reconcile`, folded into one surface). A discovered
plugin with no authored entry (**uncovered**) is expected and non-blocking —
it just runs on inferred defaults. An authored entry no longer discovered
(**phantom/renamed**) or a plugin publishing a prerequisite the catalog
doesn't carry (**published-prereq gap**) is real drift. In **human-readable
mode**, that drift fails `doctor`'s exit status alongside the existing
prerequisite/core-install gate; `--json` mode always exits `0` regardless of
findings (including `plugin_alignment.ok: false`) — inspect the JSON payload's
own `ok` fields for the actual status.

`doctor` also reports **repo registration**: whether every `repos.yaml` entry
with a checkout path registered for this platform actually resolves to a real
git checkout on disk (working tree or bare, confirmed by probing git itself
rather than trusting filesystem markers alone). A repo with no path
registered for *this exact* platform at all is not flagged — it may
legitimately be reference-only here, and another platform's own entry is
never substituted for it. A *registered yet missing or non-git* path is real
drift, surfaced the same way plugin-catalog alignment is: it fails `doctor`'s
exit status in human-readable mode, and `--json` mode always exits `0`
regardless (inspect `repo_registration.ok`). A repo whose checkout couldn't
be verified at all (git itself unavailable or the probe timed out) is
reported separately under `repo_registration.unknown` — visible, but never
treated as confirmed drift.

## Connected native mux status

The engine status monitor discovers connected canonical worktree mux sessions
even when they were created directly or reattached with native `psmux`/`tmux`,
without first using the Manager launcher. Attribution requires an unambiguous
local tracking record and a matching inner-pane directory, not a shell's
current directory or a `wt-` name prefix alone as ownership proof. The server
may start in home while the pane executes in the selected checkout.
Discovery checks at most two
candidates per sweep, round-robin; a larger connected fleet converges over
successive sweeps. Current registered paths with an admitted native incarnation
and fresh managed mappings are reused without another roster scan, mux probe
or registration request. Standalone registrations retain their raw path file
and an atomic hidden identity companion, so monitor restart does not trust a
same-name replacement. Changed or unknown incarnations are excluded from
presentation until their checkout pane is attributed again, including
while detached. A failed identity write cannot authorize presentation.
Admission and persisted reuse require the native `$<decimal>:<decimal>`
session-ID/creation identity, including valid zero values. Malformed or unknown
identities cannot authorize presentation or be mistaken for confirmed absence;
this is a syntax fence, not a new freshness or lease rule.
Unresolved sessions retry no sooner than 20 seconds after an attempt completes;
a new native incarnation has its own retry fence.

The existing authenticated mux-daemon endpoint admits these observations with
`mux-discover-v1`. Manager confirms the native session incarnation, matching
pane directory and positive attachment count in one bounded pane inventory
before atomically creating an absent mapping. Other panes may have different
directories, but conflicting session identities or attachment counts remain
unconfirmed. Reattachment and daemon reconstruction retain its revision.
Checkout-path evidence is required for admission or replacement, not repeated
for an already-admitted incarnation's status and attachment refresh. Changing
directories within a pane does not revoke that mapping. Empty paths from
retained panes are not attribution evidence and do not invalidate other panes.
Same-incarnation tombstones cannot be resurrected. A confirmed distinct native
incarnation for the same canonical worktree can replace an old mapping at a
higher revision, but only if the registry identity and revision still match the
snapshot taken before the native probe. Bare and absolute mux commands must
resolve to the same executable; similar basenames alone are not equivalent.
Unknown prior incarnations, foreign
paths/sessions and concurrent replacements are refused. Failed enumeration is
unknown, not a zero count or permission to take ownership.

The engine remains the status-data authority; Manager still applies rendered
options through `mux-status-v1` and republishes live observations. Status pushes
carry the expected mapping revision and incarnation, and discovered mappings
also recheck the native incarnation before presentation. Their first status
apply initializes session-scoped status formats through that same writer;
standalone admission initializes those formats before registration. Both paths
remain retryable if initialization fails, without changing global mux settings.
A complete valid native listing that confirms disappearance retires the mapping
through the existing revision/incarnation-guarded tombstone path. Failed or
malformed probes and same-name replacements do not authorize retirement.
The existing attachment observer also reconciles discovered session identity
without waiting for a new render, replacing rather than adding an attachment
probe within its two-probe/two-second cycle and completion-based 20-second
cadence. Exact tmux missing-server/missing-socket diagnostics with exit 1 and
empty output qualify terminal absence; other failures, including unqualified
PSMux diagnostics, remain unknown.
When Manager is absent,
the existing standalone engine writer remains available. An unavailable or
refusing configured Manager is not permission to bypass its presentation lane.
An older Manager that does not support discovery remains visibly deferred and
subject to the same retry cadence, without aborting the monitor sweep.
Positive attributable connected-session demand can start a dormant selected
Manager through its registered command's existing `mux-daemon ensure` operation.
The declared installation root is retained for startup and IPC; no PATH search,
installer or updater is used. Existing explicit sink-root overrides still apply
to status delivery; an override conflicting with the selected installation
defers automatic startup instead of silently selecting another root.
Startup attempts share a 20-second completion-based
backoff across sessions. A reachable peer is never restarted because it rejects
the discovery request.
No installation, global mux configuration or extra service is required.

## Launch preparation

### Disable automatic maintenance while auditing

Set this strictly boolean user-local policy in
`~/.worktree-manager/config.toml` (or the existing `WORKTREE_MANAGER_ROOT`):

```toml
[maintenance]
automatic = false
```

Omitting the table/key preserves the enabled default. Invalid or unreadable
configuration blocks automatic work with a diagnostic; strings such as
`"false"` and unknown maintenance keys are errors, not enabled defaults.
Configuration inspection, confirmed edits and update admission expand a
current-user `~` in the selected root consistently. Relative roots are anchored
to the invocation directory before queued execution; missing intermediate
`..` segments cannot redirect a read or edit into a shadow configuration.
The Picker reports a disabled policy without blocking ordinary browsing.
This switch is a veto, not installation consent: `true` does not bypass the
updater's existing opt-in, publication, or ownership checks.
The launch-time policy probe suppresses bytecode writes before importing the
installed payload, preserving its sealed construction inventory.

The policy vetoes Manager-initiated automatic launch update/reconciliation
submissions, background staging, anchor healing, orphan/managed/finished-session
sweeps, Picker monitor-root enrollment/renewal and monitor start/restart
attempts. It is re-read at each admission, including queued worker entry and
successive launch phases. Optional warm-Picker spare creation is also vetoed;
cleanup of already-owned UI processes still follows their normal lifecycle.
The legacy `AGENT_WORKTREES_NO_AUTO_CLEAN` opt-out
still independently vetoes finished-session cleanup.

This is **not a read-only sandbox**: manual actions retain their existing
confirmation/ownership gates, passive discovery and update-availability reads
continue, and work already admitted may finish. It neither cancels an accepted
update request nor stops another owner's resident service or scheduled
maintenance. Set it before launch when auditing; changing it later cannot
undo earlier effects. Reset to `true` or remove the key to allow future
automatic admissions.

For changes to installation or launch, run the ordinary contained Manager tests
and `python tools/run-manager-runtime-acceptance.py --root .validation` from the
repository root. The latter constructs fresh candidates under the existing
bounded native supervisor before running its isolated-import, rollback,
retained-launch and target-interpreter mux assertions through the standard
test runner. It does not install on the host.

Explicit shell maintenance reports each synchronous marketplace installer,
bootstrap update and reconciliation action's start, process exit code and
completed duration. Failures (including commands that
cannot start) are explicitly reported as continuing: maintenance remains
best-effort, not a launch-wide abort policy. PowerShell uses a Stopwatch and
native UTC formatting. Bash, including Bash 3.2, uses one launch-owned
`update_telemetry.shell` renderer during maintenance, not the wall-derived
`SECONDS` variable. It acknowledges each action scope before its
command starts, providing the shared formatter's UTC and monotonic clocks
without a Python or `date` process per line. These durations measure the
containing action, not individual child lines or a verified serving-state
postcondition. Outside those finite scopes, Bash's early/late launcher
diagnostics retain UTC wall-clock correlation at whole-second resolution but
explicitly report `[elapsed unavailable]`: there is no monotonic scope across
the Picker or interactive session. Multiline diagnostics share one clock read
and each physical line carries its severity and envelope. Fallback status
echoes retain the existing terminal gate and use the same envelope as the log.
A failed wall-clock read is labeled `[time unavailable]`, never a fabricated
time or duration. An in-process clock reader observes native completion records
independently of the presentation sink, so draining queued diagnostics does
not inflate a completed action's duration. Child output
continues streaming to the setup log; interactive action summaries use the
existing launch-status gate, leaving quiet/protocol stdout unchanged.
Each pass still obtains a fresh plan and re-resolves the runtime afterward;
an exit of zero does not replace the second reconciliation pass.
Bootstrap updates likewise retain runtime re-resolution and their fresh
staleness check. Child stdout and stderr go through the same setup-log sink,
including an unterminated final line; a nested canonical frame is not prefixed
with another installer label. Unqualified native progress uses the same state
vocabulary: leading `[OK]`, `[SKIP]`, `[WARN]`, `[FAIL]` and `...` become
`done`, `skipped`, `warning`, `failed` and `running`. These remain elapsed
diagnostics, not completed-step durations or changes to a child's actual exit
outcome. Explicit owner states, completion messages, unknown/embedded markers
and canonical records are preserved on both native paths.
PowerShell restores its
native-error preferences even when spawning fails. Bash obtains the request's
exit code from the command, not its output consumer, and decodes staged metadata
with NUL-delimited fields so an empty optional value cannot shift the fields.

Launcher-owned diagnostics record passthrough and Windows pane/direct-handoff
argument counts, not argument values. Bootstrap and reconciliation actions retain
their service labels, timed outcomes and child diagnostics without a duplicate raw command
echo. Execution arguments are unchanged; this does not redact arbitrary output
emitted by a child command.

Windows PSMux creation is scoped to the resolved user home while the pane keeps
its explicit selected worktree directory. The launcher's directory is restored
even when creation fails. This avoids lending a retired checkout to an idle
PSMux standby without changing global warm settings or non-mux launches.
See [mux launch directory](../plugins/agent-worktrees/docs/mux.md#two-backends-one-model).

Ordinary Open and Resume use the worktree's existing setup. Native wrappers do
not submit installation requests, recompose paired plugin settings or run
marketplace preflights before entering it. The receiving engine also preserves
repository HEADs and the overlay across Resume and SessionStart, retaining
folder trust and tool-roster admission. Resume does not start background
runtime provisioning.
New retains its first-launch provisioning intent across relocation without
creating the worktree twice. The handoff uses `--worktree-id <id> --first-launch`
through the existing literal argument transport, including shell-owned Windows
terminal handoff with mux enabled. No first-launch environment hint leaks into
later sessions.

Picker **Sync** is the affirmative repository/local-plugin operation: safely
fast-forward both registered members of a worktree pair, then compose plugin
settings and prepare instructions only if both are current. A failed fetch,
dirty/ahead/active member or failed fast-forward leaves the existing plugin
setup unchanged and reports the blocker. Any Git progress before a later
failure remains visible in the result. Sync never discards local work or runs
a machine-wide software installation; use the separate explicit **Update**
flow for that. Initial creation still prepares a new worktree. Independently
adopted maintenance and explicit refresh/update retain their existing owners,
policy and exclusive installation authority.

The existing `launch-update` compatibility commands remain available for
explicit maintenance. They retain canonical request identities, source and
runtime checks, durable logs and honest outcomes; ordinary session entry no
longer invokes them.

The native maintenance sinks share one physical-line envelope with the
Python updater: `[HH:mm:ss.fffZ] scope state message [elapsed Ns]` or a
completed action's `[duration Ns]`. Canonical nested envelopes are retained,
not wrapped in another timestamp. The Bash renderer reads a private temporary
spool using shell-built-in record writes; it has no update, service or process
retirement authority. Normal completion drains and waits for it, then removes
the spool before Copilot starts. Startup and shutdown waits are bounded;
parent loss ends the renderer. A missing/failed renderer warns explicitly and
uses unavailable timing labels rather than invented durations. Failure
diagnostics remain in the named private spool instead of being discarded.
Parent-loss termination is independent of terminal writes and admission
waits; the dedicated CLI exits nonzero and retains a `ready.failure` reason.
Shutdown resolves the renderer's still-owned Bash job and command before
signalling; a cached PID alone never authorizes termination. A final receipt
is written after output closes and the clock thread joins. Cleanup requires
both that receipt and the absence of a live owned job, even if Bash already
reaped its exit status. Job-table checks run only at lifecycle boundaries.
Each finite Bash runtime preflight also owns one renderer for its setup-log
records, stopped before returning to the launcher. Its scope covers all
attempts and retry delays, not a single child execution. Runtime re-resolution,
three attempts, native argument vectors, captured stdout, optional stderr
passthrough and the existing final continuation warning are unchanged.
Successful preflights do not gain terminal summaries or contaminate protocol
stdout. No renderer is kept alive across the Picker or interactive session.
Other early/late Bash diagnostics outside these bounded scopes retain their
existing behavior. Legacy standalone Worktrees
install/update presentation is owned by its existing admission parent, not
this launcher renderer. Structured-context installers retain their own native
validation and staging owner, including its existing internal output buffering;
the detached Windows observer does not replace that owner with legacy admission.

New waits for initial source and plugin preparation; Resume retains existing
source and plugin setup while resolving trust, session and prompt handoff.
Neither imposes a fixed aggregate plan timeout. Preparation stages and elapsed-time heartbeats stream
to the launch terminal; the final plan remains JSON on stdout. Individual
engine operations keep their own failure bounds, and automation can explicitly
request a bounded plan call.
An explicitly bounded SSH prompt plan allows at least 300 seconds for the
target's 60-second read-only owner-policy check, 180-second admission call and
host overhead; an unbounded preparation
call remains unbounded.
Captured preparation owns a kill-on-close Job on Windows and a dedicated
process group on POSIX. A POSIX group owner stays unreaped until the engine's
completion receipt has been read and the entire group has been cleaned up,
including when an inner render failure degrades and the engine exits normally.
Foreground projection inherits the latter rather than
detaching from its launch owner, so an explicit timeout or cancellation also
terminates renderer and Git descendants. The bounded asynchronous backup
worker keeps its independent lifetime and containment.

Instruction projection is shipped inside agent-worktrees itself. It does not
require customizing-copilot to be installed or globally enabled: promotion
vendors the shared canonical engine into the owning worktree payload.

## Resume prompt

The initial loading tab is labelled `Local` with its environment until the
machine roster resolves its canonical name. It does not present the OS hostname
as a configured machine or add roster/configuration I/O to first paint.

Windows and WSL tabs for `<host>` and `<host>-wsl` share one physical-host
heading. An explicit `physical_host` association takes precedence over suffix
inference. Grouping changes presentation only: environment selection, source
identities, SSH aliases and worktree routing remain distinct.
Physical siblings are presented together even when the roster separates them;
keyboard traversal uses that same stable first-host-appearance order.
The configured local key or alias need not match the OS hostname: source
construction and per-row locality checks resolve it in the running execution
environment. Its current tab loads in-process; sibling environments keep their
own SSH targets, and returned rows retain the selected source's logical identity.

Ordinary Worktrees loads require the engine's resident current-roster
publication, including its observed git/session/mux facts and bounded claim
aggregates. The local and machine-SSH loaders hold the CLI's
`list --current --stream --follow` feed by default. An explicitly advertised
resident Picker handoff may use the same authenticated local feed directly,
without a CLI relay; it never imports engine code or recomputes/overlays those facts. Complete
snapshot/update barriers replace rows atomically. Planned cutover notices and
abrupt disconnects are distinct; reconnect retains explicitly stale last-good
rows until the replacement generation completes. Unsupported authority is an
actionable engine-upgrade error. Explicit `AGENT_WORKTREES_PICKER_STREAM=0`
selects current one-shot reads, not a direct computation fallback.

Cold local loads request a labeled saved-DB bootstrap on the same subscription.
Saved rows remain advisory and non-authoritative while the source is loading;
the first complete current publication replaces them atomically. Retryable
resident startup/unready responses keep the loading state instead of reporting
a failed load. Direct feeds negotiate `worktrees-feed-bootstrap-v1`; CLI feeds
use the backward-compatible `--follow --cache-only` combination. A missing
direct resident uses the existing CLI launch/recovery owner, never caller-side
Git computation or a new daemon owner. Real producer, protocol, authentication
and live-peer failures remain explicit.

Machine-SSH listing and follow-feed calls suppress configured local and remote
forwarding with `ClearAllForwardings=yes`; aliases, routing, identity, host trust
and timeouts remain unchanged. Interactive launches and effectful commands retain
their configured forwarding behavior.

Pressing `r` immediately shows retry progress and preserves the selected source,
including All. Before a first successful list, the list area distinguishes
`Worktrees connection unavailable` from a reachable service whose worktrees
`could not load`; diagnostics and the retry hint appear there, not in a `WT`
error badge. A read failure does not declare the host or its resources offline.
After a successful list, including an empty one, later connection/refresh errors
retain the last loaded data with a muted `~` source marker and scoped
`Updates paused` notice rather than a fresh-connection checkmark.
Refreshing preserves those rows, and successful recovery clears the notice.
Held-feed staleness stays in availability metadata rather than rewriting every row title.

Explicit version-1 machine tuples are validated by the shared dependency-free
identity codec before current rows or stream publications are admitted.
`engine_client.Worktree.machine` is their execution owner; the original physical
host and environment remain unchanged in `raw`. Picker rows additionally expose
the validated tuple as `machine_identity`, while their `machine`, `env` and
`source_id` keep the already-selected logical source. Tuple metadata never
retargets a configured transport or grants native/guest authority.

Contributed action context keeps an explicitly selected source owner; if it
needs a raw-row fallback, it uses the tuple's execution owner, not its physical
host. `{physical_machine}` exposes that original host for versioned rows.
Malformed explicit tuples fail visibly before background/template effects;
unversioned rows keep historical parsing and output behavior. The codec is an
ordinary first-party dependency embedded by the existing payload materializer,
not an import of an installed Worktrees runtime.

The CLAIMS preview retains the engine's ranking and links, with a leading
omitted-count or unresolved-descendant warning when the bounded publication
is partial. Older publications without sufficient evidence show **coverage
unknown**, not a complete subtree. **View details** loads full held claims
on demand and, when child claims are present, uses the engine's existing
transitive inspection to show descendant owner paths and unresolved edges.
The engine supplies resolved detail URLs for shorthand and canonical PR refs;
the card retains the stored ref and owner path. Older engines without those
URLs still show the original ref, linking it only when it is already HTTP(S).
Those inspections run in the background only after opening the detail card;
ordinary row rendering never walks a claim graph or performs network reads.
Descendant claims remain owned by their original worktrees.

Unresolved interactive Picker startup presents a pending project-binding view with
Escape to cancel. Project-default discovery and engine `picker-bootstrap`
resolution run after that view's first refresh, off the input thread. Operational
actions are not available until the engine's project/cwd/live decisions are
accepted. A failed binding stays visible until dismissal and returns failure;
late results after cancellation cannot activate a project or start housekeeping.
Explicit project names go to the engine without enumerating the full Manager
project roster; implicit defaults still reject ambiguity and knowledge-only
companions.

Initial live rows come from one authoritative snapshot-plus-follow subscription,
not a preceding cache-only one-shot process. Pivot discovery remains independent
and remote loading remains lazy. An explicitly labeled advisory bootstrap may
arrive on that same subscription, but cannot overwrite accepted current rows.
Optional bootstrap `project_config`
fields (`config_dir`, `repo_dir`, `machine`) seed the Manager's config cache.
Older engines retain one memoized fallback call per missing scalar, on setup
workers. Bound scalars survive ordinary setup/reload file-cache invalidation;
accepting a new project binding resets them. Environment-enabled mock operations
still resolve implicit project selection and refuse ambiguity. This consumer
support does not assert that every engine produces those optional fields.
Manager requests `picker-bootstrap --json --project-config` to obtain the
configuration directory, effective repository directory and validated machine
in the existing worker request. This uses one layered engine configuration
load without unrelated control-plane PR overlays; a registry anchor is never
substituted for a layered machine-local anchor override. The default engine
bootstrap remains configuration-load-free. Only an explicit unsupported-option
response falls back to the older bootstrap, whose missing fields retain the
memoized worker scalar queries. Configuration, identity and timeout failures
are not retried as compatibility fallbacks.

The optional version-1 resident bootstrap supplies those same scalars and fresh
headless policy before an advertised provider handoff. The Manager consumes a
project/engine-scoped payload, keeps its ordinary command for actions and
nonlocal sources, and uses the existing atomic receiver for the local socket
feed. The socket request preserves the ordinary local CLI's default
current-platform and present-checkout selection. Feed transport and publication
do not perform synchronous launch-trace writes. Missing capability retains the ordinary front door. This avoids repeated
local bootstrap/feed CLI starts; it does not itself keep a cold Textual UI warm
or qualify first-visible/input-ready latency. See the
[resident read contract](../plugins/agent-worktrees/docs/engine-picker-contract.md#optional-resident-picker-bootstrap)
for selection, failure, cancellation and deadline semantics.

The existing Manager mux resident can retain one single-use, initialized
Textual spare and at most four active UI processes. Each has its own mux
terminal, project binding and engine state; applications do not share mutable
UI globals. A matching engine, invocation context and Manager runtime are
required before an atomic handoff consumes the spare. Missing or incompatible
resident support retains the cold path without provisioning another service.
Admission compares a digest of the caller's non-transport environment with the
spare's actual environment, including home/config paths and Picker overrides;
different settings retain the cold path rather than inheriting the resident's.
Session, claim, handoff and authorization-bearing contexts use the cold path and
are stripped before a spare starts. No raw caller environment or credentials
are journaled. Per-launch trace identity is excluded from the reusable
environment fingerprint and carried separately into the admitted UI.
An absent Manager-root environment value uses the selected provider's canonical
default when comparing contexts; an explicit different root remains incompatible.
Spare and completion commands use isolated Python startup and the owning
constructed slot's retention bootstrap, so inherited Python paths cannot select
a different Manager. A consumed UI enters the caller's cwd before project binding;
the engine's explicit switch-to-anchor policy still applies afterward.
Warm admission revalidates the current mux incarnation as well as the worker's
process birth identity. Prewarming is disabled when process-birth identity is
unavailable, preserving the cold path without repeatedly spawning unusable UIs.
The current primitive supports Windows/Linux; macOS retains the cold path
until its process-birth identity is supported.
An already-accepted bootstrap can bind directly rather than repeating project
discovery behind a second pending view.
Automatic update and orphan courtesy checks yield to the bounded initial-local
visible-refresh gate; an explicit forced orphan refresh remains immediate. Exiting
while an automatic check waits prevents its backend request from starting.

Resident bootstrap also binds its authoritative runtime root for diagnostic
paths, avoiding a synchronous `picker-paths` subprocess before local feed
enrollment. Older engines preserve the existing path lookup. UI acquisition,
activation, source preparation and loader start have optional
launch-trace checkpoints; these are diagnostics, not latency qualification.
Drain closes UI admissions and retires idle spares, but active UIs keep the
resident busy until their decisions are consumed or bounded result retention
expires. Results are bounded and retained briefly for
the attaching caller; cancellation and abandoned attachment are owner-cleaned.
Final resident shutdown retires every owned UI session, including attached
ones, rather than abandoning its cleanup deadlines; changed mux incarnations
are refused instead of targeting a replacement.
The UI also checks its owner's process birth while mounted. Abrupt owner loss
ends both pending and bound UIs without publishing a decision, and retires
only its known protocol files and empty slot directories. Unknown files remain
for diagnosis rather than being recursively deleted.
A retirement refusal is isolated to its slot so another completed session can
still expire or be consumed. On POSIX, the selected tmux socket is explicit in
creation, probes, attachment and cleanup commands; attachment does not switch
silently to the default server after clearing nested-terminal environment.
This source facility does not establish a two-second native launch guarantee.

Providers advertising the optional terminal-session contract let the existing
engine front door attach directly to that UI. A second Manager Python process
is needed only afterward to consume the retained decision, not to first open
the screen. Result consumption validates the original project/engine handoff
and is exclusive, so replay cannot execute the action twice. The action process
rebinds that handoff's project, configuration scalars and cwd policy before
dispatching a retained decision, including remote Restore. Native session
incarnation changes refuse stale cleanup. Optional housekeeping, pivots and
background remote pings defer to initial local-feed settlement, with a bounded
escape rather than making a failed local source block the other views forever.
After fresh resident binding is accepted, per-launch data preparation can enroll
the current feed while widgets are constructed. The presentation consumes that
same loader once through its existing Inbox path; cancellation closes it and
late source preparation cannot start a replacement. No project data is loaded
into the idle spare before launch admission. Reloads obtain fresh source
snapshots and loaders; cancelling the initial loader cannot cancel its replacement.

Binding replacement never waits for an older scalar read; late results stay in
their original binding/epoch. Failed scalar lookups may retry after reload,
without discarding accepted bootstrap values.
An event-loop caller with an unprepared scalar receives a configuration-pending
error instead of starting a process or waiting on a setup worker's scalar lock.
Previously worker-prepared local fallbacks remain usable during reload, including
configured local-machine aliases; workers retry failed fields and replace those
snapshots when authoritative values recover.

First pending refresh is not first current rows, and neither alone proves visible
native input acknowledgement. Existing launch tracing records
`picker_pending_binding_first_refresh` and `picker_config_scalar_fallback`;
no new telemetry service or SDK is required. The explicitly selected native
headless qualification uses real interpreter/engine processes and Textual
rendering, but does not claim compositor visibility or installed-host acceptance:

```powershell
$env:WORKTREE_MANAGER_STARTUP_PROBE = "1"
python tools/run-plugin-tests.py worktree-manager --test-file tests/production_picker/test_picker_startup_native.py --test-timeout 90 --timeout 600
```

It samples cold/warm process state at 1, 100 and 1,000 rows, with and without
optional scalars, checks input acknowledgement while bootstrap is gated for two
seconds, and verifies a rendered current row and one held subscription. Host
startup latency is measured separately from that controlled delay; the
under-two-second vision budget remains an independent qualification, not an
assertion inferred from these protocol checks.

For the `config-dir` cases, also prepare the isolated `agent-worktrees` test
environment and set `WORKTREE_MANAGER_STARTUP_ENGINE_PYTHON` to its Python
executable. Select `-k config-dir` with the command above. These cases execute
the real source bootstrap handler in a separate engine process with resolved
fixture inputs; configuration/topology reads are forbidden in that handler.
The actual Manager consumer must retain the configured anchor override, render
current rows and issue exactly the two remaining scalar commands. No installed
runtime is selected or modified by this source fixture.

Held-feed startup uses the source's existing bounded process/SSH budget
(20 seconds by default). Once an initial snapshot or update starts, the
two-second atomic-publication deadline applies; committed feeds retain the
five-second heartbeat deadline. A transient transport watchdog expiry
reconnects through the existing cancellable backoff, showing failure or stale
last-good rows until a complete replacement arrives. Explicit peer timeout,
authentication, malformed-protocol and incompatible-capability refusals remain
terminal; none authorizes a polling fallback or a daemon restart.

Supported local-native New and cold-Resume composers save each nonempty accepted
prompt to the Manager's local `seed-history/history.sqlite3` **before** requesting
an engine launch plan. A timeout or failed launch therefore does not erase the submitted
paste buffer. Reopen the composer and press **Up/Down** to recall older/newer
seeds; multiline cursor movement remains available until the first/last text
line, and **Ctrl+Up/Down** recalls history from anywhere. Browsing back past the
newest entry restores the current unsent draft, including edits made while
browsing. Both composer caches keep each entry's original expiry deadline and
exclude expired entries during navigation without replacing the current draft.

History is per project, survives Manager restarts and runtime updates, and
retains exact submitted text locally under `WORKTREE_MANAGER_ROOT` (or the
normal `~/.worktree-manager` root). It is not synced, logged, or automatically
executed. Consecutive identical submissions do not create duplicates or extend
their original deadline. Entries expire 90 days after submission; reads hide
expired text even before physical maintenance. Treat this database as private
prompt content. Recognized legacy history migrates without changing original
IDs, projects or timestamps. Versioned history accepts only its expected table,
expiry index and application/version markers; extra schema or foreign markers
refuse prompt reads/writes.
The history directory and database are owner-only on POSIX; Windows uses a
protected owner/SYSTEM ACL, including inherited protection for SQLite journals.
Existing history permissions are repaired before access; failure blocks submission.
Blank or cancelled prompts are not recorded. If saving fails, the composer
stays open with its text and an error rather than launching an unprotected
request. This recovery history is separate from the engine's pending launch
intent and never retries an allocation automatically.

History reads open SQLite in read-only mode. A valid uninitialized empty database
is treated as empty history without creating its schema. Corrupt, foreign, or
incompatible stores refuse access instead of being silently repaired or migrated.
Real production Picker use maintains both history and launch-journal input after
first paint when automatic maintenance is enabled. Explicit confirmation also
maintains both off-thread before saving. Offline machines physically expire text
at their next permitted maintenance opportunity. Migration scrubs legacy freelist
copies and safely checkpoints recognized WAL into DELETE mode; busy or unexpected
sidecars report errors rather than being removed by hand. Pending daemon seeds
remain untouched. See [the storage contract](docs/launch-attempt-storage.md#private-readers-and-expiry)
for crash recovery, privacy and automatic-maintenance behavior.

Nonempty prompts with Anchor, Bare, AHP, or Local model selected refuse
confirmation visibly and keep the draft open without recording it. Clear the
prompt or change the options to continue. Remote and cross-environment
(including Windows-to-WSL) New and Resume do not offer a prompt composer; programmatic
prompt requests for those transports or active/uncertain execution
legs refuse before engine staging. Seedless launches are unchanged. No Mux is a
supported prompt launch modifier. History retains the exact paste buffer;
launch submission retains the composer's existing surrounding-whitespace trimming.
Programmatic seeded Resume requests must identify an existing worktree; missing
or empty worktree ids refuse before any execution-leg lookup or launch resolution.

Sessionless workspaces retained by another controller appear separately under
**Owned**, not as ordinary resumable conversations. Use **Go to controller**
when its exact target is loaded, or **Refresh** / **View details** to diagnose
an unresolved owner. Existing live-mux attachment and orphan reclamation remain
available; a genuinely empty, unowned worktree still offers **Open**.
A successful explicit task embodiment may attach its newly launched session
even while the Picker's pre-transaction workspace snapshot is still sessionless.

### Picker navigation and read-only status

Dispatch contributes one **Tasks** pivot. **Include maintenance** is unchecked
by default (work tasks only); checking it includes both work and maintenance
tasks from the same attributed provider, even when only the maintenance
contribution is available. The toggle filters the current scope; it does not
create tasks, change categories or establish cross-machine aggregation.
Provider scope/read errors remain explicit, not an empty successful result.
The provider applies its row limit independently to each category before
combining the board, so maintenance traffic cannot hide older work tasks.
This requires the provider's `--limit-per-category` board support; an older
peer reports an unsupported-option/upgrade error instead of an incomplete
work-only inventory.

Workers Configuration uses its provider's progressive catalog: known templates
appear while configured-worker and source discovery continues. The loading
banner qualifies the partial view; final counts and activity facts come from
the provider, not Manager-side reconstruction. See the
[Workers catalog contract](../plugins/agent-dispatch/docs/worker-instances.md).

**All** gathers machine-selecting registered providers from admitted canonical
source descriptors, including machines whose Worktrees read failed. Repeated
tabs for one logical machine share a request; distinct guest identities are
not collapsed by a common display name. At most four initial reads start
concurrently. Available and cached rows retain their source labels beside
pending/failed scopes; a failed scope is never reported as an empty success.
Account/global providers and commands without a declared `{machine}` selector
run once in their own declared scope, without invented machine flags.
`r` refreshes the active registered provider's selected scope(s), not the
Worktrees loader. Cached failed rows remain inspectable, but executable row
actions refuse until the source has a current read; provider-side ownership
checks and action confirmations remain authoritative.

One-shot registered reads retain the 20-second command budget and now use
caller-owned process-tree cleanup, including wrapper descendants on Windows.
The existing 30-second stream budget also bounds a held stream's first commit:
`begin` alone is not a committed snapshot; a row or completed empty snapshot is.
After that first commit a subscribed channel may remain open normally.
Startup timeouts and worker exceptions settle as cached, source-attributed
errors instead of retrying on every render frame. Explicit refresh and the
normal polling cadence can retry them.

Highlighting the right-docked **Configuration** entry immediately displays its
last selected section. This is browsing only; Apply/confirmation remains
required for changes. Escape returns to the previous operational view.

Worktrees reads the selected engine's knowledge readiness off the input thread
in both local and live modes. The last validated observation and button
identities remain stable while a refresh is pending.
Missing required knowledge shows **Configure knowledge**, which navigates to
the native Repositories section and its confirmed existing-repository binding
action. An explicitly unresolved existing binding instead offers
**Inspect knowledge**. A pending or failed readiness check does not imply
missing configuration and does not offer setup; its neutral notice distinguishes
checking from an unavailable read. Worktrees **Reload (r)** retries the same
bounded asynchronous observation and clears the notice after a successful read,
without polling or changing configuration. Unknown remains unknown, not ready.
An unavailable binding payload shows preparation guidance in the
Repositories banner. Unavailable/failed reads remain explicit; a banner is not
proof of readiness. Completion refreshes the owner observation and reconciles
current and saved Worktrees button focus before returning from Configuration.
The native New gate
still decides admission, while source-only Bare allocation and existing-session
recovery retain their separate behavior. This flow does not create a repository,
authenticate a provider, retrofit a pair or install a missing binding owner.
Caller timeout does not establish rollback. Inspect native readiness before
retrying; the binding worker retains registration serialization independently
of a caller that has lost its result.

One header update action is shown: a Manager update takes precedence when both
Manager and payload report availability (that path also reconciles the payload).
Otherwise the payload's staged-update action remains available.
Rows retain checked selections while buttons hold focus, but the amber cursor
appears only while the list itself has focus.
Filtered live publications remap focus by the source-qualified row key, rather
than reusing a stale row index. The existing safety rule still keeps live/ACTIVE
rows visible even when they do not match the query; newly admitted live rows
must not silently retarget the filtered selection.

`WT✗` qualifies a failed **Worktrees source read**, not whole-machine
reachability; another pivot may still read successfully on that machine.
Claim-coverage and orphanage details are read-only snapshots: inspect their
scope, freshness and incomplete/unknown evidence before considering any cleanup.
Unreachable never establishes that a resource is stopped, absent or safe to
reclaim.

For a stopped local worktree, **Actions > Resume** includes an optional prompt
field directly above the actions and the existing **No Mux** / **AHP** options.
Only eligible prompts load history, asynchronously through the Picker's
existing background/Inbox owner. History completion never replaces a draft or
its cursor, and Escape does not wait for history storage. If late verification
removes local cold-Resume eligibility, a nonempty draft remains visible and
launch refuses rather than silently dropping it. The compact Actions frame
keeps its native option list scrollable at 80×24 and 118×24.
There is no separate prompt dialog or Bare-resume action in Manager. The prompt supplies the first
turn of the cold Copilot start alongside its prior `--resume=<session>` target.
It uses the full `--interactive` argument, including with **No Mux**. A live
session offers **Open**, not Resume with a prompt; Open neither queues nor
injects a turn, even if the live session has not received its first prompt.
If the target becomes live during launch, the prompted resume refuses visibly.
New and Resume prompts are stored with their distinct provenance in the
worktree's external state folder through the daemon's existing write
authority. Failed attempts keep the prompt for the next cold attempt.
Both Manager resolve hops carry the exact staged identity, not another copy
of its text. Successful Copilot handoff discards the prompt; a setup refusal
or backend-start failure leaves it available for retry. Open never submits a
staged Resume prompt. Uncertain handoff acknowledgement keeps the intent
visible but refuses automatic resubmission, avoiding a duplicate turn.
A later explicitly supplied prompt replaces the pending intent; a stale
handoff acknowledgement cannot discard that replacement.

Local and remote New/cold Resume use the same host-rendered prompt composer.
For SSH targets the Manager requires the engine's negotiated native-seed
launch-request boundary. A caller sends a bounded structured
request to the target engine before opening its interactive SSH launch.
The target daemon admits the request, allocates at most one New
worktree, and stages the prompt in that target's worktree state. The launch then
carries only the target worktree and seed identity plus the native capability
fence. Version-2 admission binds the saved Manager attempt ID to the target's
durable receipt; the host verifies that echo before launching. Missing capability
or mismatched identity fails visibly, never by retrying a weaker request.
The attempt ID is audit correlation, not authority to replay an uncertain New.
Journaled local and seedless actions also require durable engine resolution
receipts through `--require-launch-attempt`. The confirmed ID survives the
relocated local launcher and the SSH target hop. Each resolution has its own
operation receipt; a returned plan is not evidence that Copilot started.
Seedless remote planning records forwarding only, while target resolution
records its own machine-local preparation. Missing or mismatched receipts stop
execution without automatically repeating allocation. Full prompts remain in
the protected Manager journal, not in these engine metadata receipts.

#### Read saved launch attempts

Press **Ctrl+Shift+H** in either interactive Picker to open local launch
history. The production Picker's `?` legend and the compact Picker footer
advertise the shortcut. Metadata pages contain at most 25 attempts; project,
action and Manager-outcome filters and Newer/Older controls stay within the local protected
journal. Opening or refreshing this list never queries an engine.

Select a row for bounded local detail (at most 20 Manager events and 10 engine
receipts). Source errors and incomplete-page cursors remain visible; the CLI
below can retrieve further pages. **Reveal private input** is a separate,
explicit action for that exact attempt, subject to its original expiry.
Back to list clears the displayed private detail. Long details are labeled as
truncated previews after 32,768 Unicode characters; use the CLI for the full detail.
Reads run off the UI thread, and Escape/Close remains available while the
local engine responds. The screen never retries a launch, changes retention,
or contacts the selected remote venue.

`worktree-manager launch-attempts --json` reads bounded, local saved-intent
metadata without starting the engine. Filter with `--project`, `--action`,
`--machine`, `--environment`, `--provider`, `--venue`, `--outcome`, `--after` (inclusive Unix
seconds), and `--before` (exclusive). `--limit` is 1-100. When `has_more` is
true, pass both returned `next_before` and `next_before_id` as `--before` and
`--before-id`; the ID tie-breaker preserves equal-timestamp entries.

`--outcome` filters the **latest durable Manager boundary**, not a whole-attempt
or remote-engine verdict. Each row and detail declares
`manager_outcome_scope: "manager-boundary"` and retains `last_manager_event`
with its sequence, timestamp, phase and prompt-free details. This observation
is independent of event pagination; filtering happens before the row limit.
`refused` means the Manager refused a step, `failed` means its launcher returned
nonzero, `interrupted` requires an explicit interruption observation, and
`returned` means only that its launcher returned zero. **None establishes
Copilot launch, prompt acceptance, current liveness or safe replay.** An
incomplete step, absent event or ambiguous engine/transport error remains
`unknown`; elapsed time alone never turns it into a proven crash. Later
Manager observations supersede earlier ones in this boundary projection,
while the event history retains the earlier evidence. Engine, daemon and
native observations cannot override the Manager's own boundary.

To inspect explicitly selected configured SSH machines, use:

```text
worktree-manager launch-attempts --sweep-project example --source-machine worker-linux --source-machine other-wsl --json
```

`--sweep-project` selects **only the configuration source**, not a journal
filter; `--project` still filters saved attempts on each target. The reader
consults that registered project's existing local anchor, additively merges
its root and canonical machine files (canonical entries win), and uses its
configured knowledge supplier only when neither file exists. It never
enumerates every project's files or provider venues. Cell-scoped registry
selection is currently unavailable and refuses rather than substituting the
machine-global registry.

Select 1-8 configured machine/environment identities explicitly. The sweep
refuses legacy multi-profile entries that cannot be normalized to independent
execution spaces, rather than substituting their default SSH profile for a
selected alias. Declare separate execution-space entries for those sources.
It reads **one metadata page per source** through its configured SSH profile and
the target's published absolute `~/.local/bin/worktree-manager` front door
(`worktree-manager.ps1` on Windows), not a PATH-selected engine or a setup
command. Only already-known host keys are accepted; agent, X11 and port
forwarding, persistent
control sockets and SSH local commands are disabled. The shared SSH transport
owns proxy children and cleanup. A source has a 20-second read deadline, the
sweep shares a 120-second deadline, and each source stream is limited to 1 MiB.
Cleanup may add time after a deadline; no read is retried.

The versioned sweep response labels observations non-atomic and retains each
source's configured identity, status and independent pagination. Identity is
based on the configured SSH profile, not a newly attested remote hostname or
service readiness. Re-run with that one source and its `next_before` /
`next_before_id` to continue; pages are not merged into a misleading global
cursor. Unconfigured, ambiguous, duplicate, unavailable, incompatible,
oversized or timed-out sources remain explicit errors, alongside independently
read pages, and produce a nonzero exit. Remote stderr and unexpected response
fields are never echoed. `--source-machine` is separate from the existing
`--machine` journal-venue filter. Without an attempt ID this remains a
metadata-list-only sweep: it refuses `--summary`, `--include-input` and detail
page selectors before opening a transport.

For one exact attempt on one explicitly selected configured SSH source:

```text
worktree-manager launch-attempts <attempt-id> --sweep-project example --source-machine worker-linux --json
worktree-manager launch-attempts <attempt-id> --sweep-project example --source-machine worker-linux --include-input --json
```

This is not a fleet-wide prompt search. Exact detail refuses multiple sources,
list filters, non-default `--limit` and `--summary` before reading configuration.
It invokes the selected target's existing local detail reader through the same
absolute published Manager launcher and SSH process owner; it never falls back
to the host journal, provisions a target runtime or retries a launch.
`--event-limit` / `--after-sequence` and `--receipt-limit` / `--receipt-after`
select one independent target event/engine page. The source retains its
20-second transport and 1 MiB response budgets; oversized detail is refused,
not truncated or silently redacted. Smaller page limits can reduce a response.

The result uses `worktree-manager-launch-history-remote-detail` v1 with one
configured-profile-attributed `source`. `source.detail.attempt` contains
validated metadata and events; only explicit `--include-input` admits its exact
protected prompt/options, and missing, expired or erased input refuses that
request. Default detail never admits a target's unexpected `input` field.
`source.detail.engine` projects the existing prompt-free owner observations,
positive boundaries, sanitized errors and continuation rather than echoing raw
engine receipt bodies. Observations remain non-atomic, absence and prompt
acceptance remain unknown, and the recorded-result/final-wrapper distinction
below is unchanged.

An independently unavailable engine can leave useful target intent:
`source.status: partial` retains validated detail with the actual
`target_exit_code`, a sanitized `incomplete-evidence` code and nonzero caller
exit. A nonzero target exit never becomes clean success. Missing/old launchers,
incompatible or foreign detail, malformed fields, timeouts and unavailable
protected input remain attributed errors without echoing target stderr or
arbitrary error text. Neither a partial nor a complete read is launch or replay
authority.

`worktree-manager launch-attempts <attempt-id> --json` reads one protected
intent and its Manager events, then queries the installed engine once for that
project's **local** receipts. The selected target in the intent is not evidence
that a remote target was contacted by this read. Explicit request IDs in the
current Manager event page and engine resolution page allow matching local
target-admission receipts to be joined;
unavailable target receipts remain missing, not successful.

Use `--event-limit` (1-1000) and `--after-sequence` for Manager event pages.
Use `--receipt-limit` (1-100) and `--receipt-after` with the engine's returned
`next_after` for receipt pages. Each page declares incompleteness independently;
an absent terminal event or an incomplete page is not a whole-attempt verdict.
An unavailable/incompatible engine preserves the saved local detail with an
explicit source error and nonzero exit, without invoking setup or replay.
Per-receipt read errors remain in the engine response.

For an explicit cross-page local projection, use
`worktree-manager launch-attempts <attempt-id> --summary --json`. This reads at
most 1,000 Manager events and ten existing engine pages of 100 receipts each;
the engine calls share a 120-second deadline, rather than each acquiring a
fresh deadline. At most 16 Manager request references are sent per call;
engine-discovered admission references retain the engine's own per-page limit.
The default detail, list and UI read budgets are unchanged. Summary cannot be
combined with event/receipt page selectors.

`summary.boundaries` keeps positive owner evidence separate: `allocation`
requires a New admission's completed creation, `launcher_start` and
`backend_start` require the corresponding actual process-spawned observation,
and `native_session` requires the engine's qualified original native-session
join. Each fact keeps its request or operation identity; process observations
retain resolution/parent/worktree/incarnation, timestamp, PID and birth evidence.
A backend command is not by itself proof of a Copilot session, a seed claim
does not prove handoff, and a returned plan does not prove allocation. Multiple
operations are not collapsed into one winning launch or conversation. The
Manager-only `--outcome` filter retains its existing meaning.

`summary.owner_observations` adds separate `resolution`, `execution` and
`admission` lists from those same existing receipts. Each resolver/execution
operation keeps its last observed phase and timestamp. `terminal_phase: null`
means no terminal was read; the literal `unknown` phase preserves an owner's
explicit uncertainty, not proof of a crash. Resolver `plan-ready`, `forwarded`
and `remote-admission-returned` phases are resolver observations only.
`recorded_exit_code` is the result recorded inside that owner, not proof of the
wrapper's final return. `wrapper_exit_code` stays `null`: later persistence,
output or receipt failures can still change the wrapper's actual exit.
Execution `process_exit_code` and `receipt_uncertain` remain separate, including
a zero child/recorded result followed by an actual wrapper return of 3 after an
earlier receipt-write failure. Execution records retain parent and resolution
identities, never letting a parent observation overwrite a child outcome.
Admission observations retain only the existing creation/acceptance flags:
neither incomplete creation nor missing acceptance proves refusal or failure.
Repeated unchanged admissions are deduplicated; changed observations remain
visible with a non-atomic-source diagnostic. No overall success/failure or
prompt-acceptance verdict is synthesized from these lists.

The projection labels its observations **non-atomic**, leaves absent boundaries
`unknown`, and never claims snapshot completeness, current liveness, safe replay,
or allocated-but-not-launched based on a missing event. A later-page failure
retains independently observed facts. Limits, incompatible/unavailable sources,
invalid receipts and changed admissions remain explicit `summary.errors` and
produce a nonzero exit; page counts and the last continuation remain under
`engine`. The selected remote is explicitly `not-queried`. These facts are a
bounded local view of an attempt, not a remote sweep or a universal terminal
outcome.

Only `--include-input` with an exact attempt ID selects protected prompt/options.
Expired or erased input refuses that request; list and ordinary detail never
select its text. Resolution, launcher exit, seed claim and original native-session
observations remain separate evidence. `prompt_acceptance` stays `unknown`,
including after a zero exit or a qualified native-session join. These commands
do not launch, repair, expire or retry. Only explicit sweep selections contact
other machines. Complete terminal-outcome classification remains separate;
local retention maintenance runs through the Picker/confirmation path, not
these read-only inspection commands.

The explicit cross-runtime source lane is
`python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_launch_history_cli_native.py`.
Set `WORKTREE_MANAGER_TEST_ENGINE_ARGV` to a JSON array naming a prepared
candidate engine interpreter/entry command. The lane runs the real Manager
entry and engine CLI against isolated synthetic intent, checking metadata and
explicit input in separate cases and verifying unchanged fixture state.
Its summary cases additionally cross two real engine pages (105 receipts),
including a backend spawn and the engine's native-session join beyond page
one, while preserving unknown allocation and prompt acceptance. They also
preserve separate parent/child recorded results, unknown final wrapper exits,
explicit resolver uncertainty and missing resolver terminals. A fault-injected
actual execution owner runs a real child, fails an earlier receipt write,
successfully writes the terminal with zero recorded/child exits and uncertainty,
then returns 3; the reader must not call its final wrapper exit zero. They print
source-read elapsed time, not a performance-budget verdict.
It is not a required-unit-CI matrix, a latency qualification, installed
Copilot acceptance or permission to provision a runtime.

The explicit `tests/native/test_launch_history_sweep_native.py` lane exercises
the shared transport process owner, real configured native shell and actual
target Manager list CLI with a synthetic transport executable and isolated
published launcher. It follows two independent pages and checks unchanged
Manager journal/launcher bytes, plus missing and incompatible target refusals.
Native shells may maintain their own OS caches outside Manager state. This
lane does not claim real SSH authentication or installed target compatibility.
Its exact-detail cases also exercise the actual target Manager reader through
that shell and process owner: default privacy, explicit input, event
continuation, expired input, partial engine failure and a projected synthetic
engine process. Manager journal/launcher fixture bytes remain unchanged. The
separate native Manager-to-selected-engine lane above qualifies real engine
receipt consumption; the synthetic remote engine is not installed Copilot
acceptance.

#### SSH admission and pending intent

Arbitrary prompt text is
encoded, not interpolated into the remote shell. Requests are limited to an
7 KiB UTF-8 envelope, including JSON metadata, to stay within remote command
limits after PowerShell's UTF-16LE/Base64 wrapping. Oversized envelopes or shell
commands are rejected before SSH admission.

An old target engine refuses the structured request before creating or staging
anything. Unjournaled seedless CLI launches retain their prior compatibility;
journaled actions require the receipt capability on the receiving engine.
An uncertain response
reports the target and request identity. Do not repeat New: on the target,
`<project> resolve --json --launch-request-status <id>` reports the already
allocated worktree and staged intent without submitting another prompt.
Resume that existing worktree only when its state is `staged`; an incomplete
creation or unconfirmed handoff requires explicit inspection.

Prompted requests require `--stage-launch-seed --require-native-seed` support;
ordinary seedless launches retain older-engine compatibility. The engine
serializes native prompt staging and hosted-provider activation under the same
worktree record lock. A provider that wins first refuses staging; a staged seed
that wins first blocks provider activation until its native handoff finishes.
The Manager's early local execution-leg read is only a preflight, not that fence;
remote prompts rely on the selected target's authoritative checks.
Accepted recovery history survives either refusal and does not itself reserve
an execution leg. Uninstrumented legacy/custom
prompt launchers refuse without consuming the seed; use normalized setup or
adopt the engine's documented launch-seed invocation boundary.
The `gh copilot` intermediary cannot acknowledge actual Copilot startup;
prompted requests using that fallback retain their seed and require a direct
Copilot executable. Ordinary seedless fallback launches are unchanged.

## One-line bootstrap

Global project `update` fetches this Main orchestrator independently of local
runtime-source policy. The [bootstrap transport contract](../docs/install-contract.md)
owns the explicit environment overrides, source precedence and older-caller
compatibility; `[source]` still selects the Manager runtime, not this transport.

**Windows (PowerShell):**

```powershell
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.ps1)
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh | bash
```

Windows retains UV's successful Python selection. If that lookup instead fails
with RedirectionGuard error 448, bootstrap resolves existing `python3`/`python`
application paths to physical executables and verifies a base interpreter at
Python 3.11 or newer. Store aliases, virtual environments and invalid probe
responses are not accepted. With no qualified candidate, the original UV
acquisition path still applies; mitigation and dependency policy are unchanged.

The bootstrap fetches this `worktree-manager/` payload and **version-installs** it
under the same convention as the harness's other installers — an immutable
`~/.worktree-manager/versions/<version>/` slot, a plain-text
`~/.worktree-manager/current-version` marker, and a `~/.local/bin/worktree-manager`
binstub (`.cmd`/`.ps1` on Windows) — then launches it. Re-running the one-liner is
**version-gated** (a no-op when already current). After the first run, invoke
`worktree-manager …` directly (ensure `~/.local/bin` is on `PATH`). The bootstrap
**auto-provisions its prerequisites**: `uv` is installed user-local (no admin) when
missing, and `git` is installed best-effort where a package manager exists —
otherwise the payload is fetched as a GitHub tarball, so a bare machine bootstraps
even without `git`. It amends the current session's `PATH` and prompts for a
restart when it can't. Set
`WORKTREE_MANAGER_ROOT` to relocate the install root. Inspect/repair the versioned
install with `worktree-manager self-install` (dry-run) / `--apply`, and see it in
`worktree-manager doctor`. A successful self-install also refreshes the
`agent-worktrees` front door registration at
`~/.agent-worktrees/control-plane-providers.d/worktree-manager.json`, so a bare
project binstub discovers this Manager through the generic control-plane-provider
registry rather than a literal `worktree-manager` PATH probe.

### Prepare without publication

The trusted prepared Manager's `scripts/runtime_install.py --describe-install`
reports the executing provider's `stage` and `activate` contract generations,
independently of package versions. Its `--root <root> --stage` operation constructs
or reuses the candidate, proves actual installed passive-server readiness and
closed work admission, then exits without selector, command, registration or
serving-route publication. It cannot be combined with removal, recovery or
application commands. Normal installation omits `--stage` and starts V2 before
committing publication.

This is the standalone provider boundary, not a `self-install --stage` option.
Prepared-source support is discovered separately with `--describe-source-inputs`;
use `--install-source` only when that executing entry supports it. A running
app-only installation is not itself a preparation input: library staging without
prepared inputs refuses rather than republishing it. Callers must discover the
exact entry's support before sending new syntax. Older entries may reject
discovery or staging; that names a capability gap, not permission to retry an
effectful operation with a different spelling. Configured-Dev promotion/reuse
orchestration and portfolio-wide staging still require whole-flow qualification.

### Older-client queue release

An older installed Manager or Worktrees command may not understand
`update --recover-for-repair`. An explicitly authorized operator can instead
use an intact, trusted newer source's recovery-only entry. With Python 3.11+
already available, from that source root:

```bash
python3 -I -S -B -X utf8 worktree-manager/scripts/runtime_install.py --recover-for-repair --json
```

On Windows, use the corresponding `python.exe` invocation. This entry imports
only that Manager payload's declared local update-driver dependency closure,
including materialized release payloads. It does not import the installed
application, require its cooperation, construct Manager, publish selectors or
commands, start services, or run pending work.

The paired native bootstrap also accepts this mode:

```bash
bash worktree-manager/bootstrap.sh --recover-for-repair --json
```

```powershell
& .\worktree-manager\bootstrap.ps1 --recover-for-repair --json
```

These bootstrap commands fetch the configured source into a private temporary
directory, not the Manager installer's shared staging directory, and remove
that temporary source after the recovery child finishes, including on failure.
The ordinary bootstrap prerequisite policy still applies. `--json` is optional;
no installation options or application command may accompany recovery.
`WORKTREE_MANAGER_ROOT` selects bootstrap source configuration, not a different
queue: recovery addresses the executing user's canonical installation queue.

The [driver's explicit repair contract](../libs/update-driver/README.md#explicit-operator-authorized-queue-release)
owns authorization, failed-history preservation and refusal semantics. JSON
reports `cleanup_confirmed: false` and the actual released records, never a
successful installation. A current execution owner, malformed queue or separately
fenced native foundation still refuses this bounded entry. Release neither
retires a live installer nor replays its failed request; request the intended
ordinary installation separately after release. Full bounded takeover and
selected-runtime readiness remain distinct from this operation.
Recovery can migrate the queue to schema 4. Subsequent inspection and installation
must use a current, schema-4-capable driver; an older schema-2-only client still
cannot read that queue. Queue release does not upgrade that client. Use the
operator-selected supported source for the subsequent installer, not an implicit
source switch, and verify actual installed readiness separately.

### Slot completeness and recovery

Each `versions/<generation>/` directory is an engine-owned venv with canonical
`.install-complete.json`, `.runtime-input-origins.json` and `.runtime-bootstrap/`
evidence. Its self-contained source payload is retained under `payload/`;
that subtree's legacy `.install-complete` proves only assembly. It does not
authorize runtime selection. Frozen source provenance and isolated installed
health precede canonical completion, and candidate custody is retained through
selection and update-triggered mux cutover.

Installed plugin discovery uses the canonical runtime layout resolver for both
historical direct-slot environments and prepared applications with a slot-local
`.venv`. Root attribution and complete-slot selection still apply; a same-named
command on `PATH` is not an engine selection. Engine bytecode caches remain
outside the selected slot in either layout. Picker monitor reuse compares the
selected interpreter's environment prefix with the monitor's recorded prefix,
so a healthy nested environment does not cause another launch attempt.

Wheel applications retain operational scripts under the installed package's
`_runtime_assets/`, without requiring a source `payload/`. Self-install and
bootstrap activation bind the running application to its physical installation
and package location, accepting equivalent native Windows path spellings while
still refusing a foreign root or package. Launcher consumers obtain resources
from the selected Manager itself; the filesystem spelling is not an ownership
or readiness exemption.

Package versions and runtime generations are distinct: a repair adds the
canonical `+repair.<identity>` qualifier (or `.repair.<identity>` after a local
version). Existing source-qualified identities retain their canonical identity.
This installer does not implicitly claim a mutable `dev` slot.

If you hit `No module named worktree_manager`, the `worktree-manager` binstub
itself is unusable (it runs `python -m worktree_manager` straight out of the
broken slot, so it fails identically) — **re-run the bootstrap one-liner
above**, which fetches a fresh payload and runs its own `self-install --apply`
from that valid copy, independent of the broken slot. Once a working install
is restored, `worktree-manager self-install --apply` is the right command for
any *other* incomplete-slot symptom (e.g. a stale provider-manifest or
binstub) where the binstub itself still runs — it is version-gated and
idempotent, and will detect and rebuild an incomplete slot rather than
skipping it as "already current."

Registered project binstubs enter the interactive front door as
`worktree-manager --project <name>`; that project-only form launches the Picker.
Truly argument-free `worktree-manager` retains the provider-free first-run
banner and onboarding roadmap.

## Durable update execution

Ordinary `worktree-manager update` and `agent-worktrees update` submit a
durable request to the shared, per-user execution-space driver and wait for
its recorded outcome. The calling terminal or agent is an observer, not an
installer. One kernel-backed transaction owner spans Manager self-update,
payload acquisition, runtime construction, activation and local integration;
existing installer publication and per-slot leases remain in force underneath.
Manager and engine retain their process boundary, including the engine's
`--no-manager` re-entry, which does not bypass execution ownership.

Project-controller discovery requires a capable selected engine. A legacy
`Unknown key: 'update-controller'` response is not proof that the repository
has no controller, even when durable authority has not been configured.
Manager refuses that uncertainty with an engine setup/recovery diagnostic
instead of choosing standalone execution. A confirmed no-project response is
neutral only when the caller did not explicitly select a project. Fixed legacy
diagnostics are recognized across UTF-8 stdout, stderr and JSON error responses;
arbitrary stdout is not exposed as an error message.
Only a classified no-project response permits that fallback. Mixed channels,
extended diagnostics and unexpected exit codes remain failures even if their
text contains a no-project phrase.

```text
worktree-manager update --wait
worktree-manager update --detach --json
worktree-manager update --request-status <request-id> --json
worktree-manager update --recovery-status --json
worktree-manager update --recover-after-restart --json
worktree-manager update --recover-for-repair --json
worktree-manager update --supervised-debug
```

`--wait` is the default. `--detach` returns an acceptance receipt, not a
successful-installation claim. The status operation is read-only and does not
create a driver, fetch source or run installers. `--supervised-debug` keeps
supervision in the foreground but uses the same queue and exclusive authority;
it cannot bypass an active transaction. The engine exposes the same options.
Equivalent pending requests can coalesce. Each addressed installation/project
authority retains at most one waiting update: a newer accepted update replaces
the previous waiting selection, preserving the running update. A superseded
request remains inspectable with exit code **125** and an error naming its
successor; it did not run and is not successful installation. Automatic work
cannot displace an explicit waiting update. Independent installation scopes and
required component provisioning are not discarded.
A request arriving after execution starts remains separate from the active
request. These requests may refer to moving source refs, so
identical arguments do not establish the pinned target required for the shared
driver's active-request opt-in. Explicit options, invocation context,
installation scope and pinned runner identity are retained in history, including
superseded requests.
The engine resolves the caller's marketplace context before submission, so a
queued engine-to-Manager-to-engine round trip cannot replace it with anchor
settings. Accepted provider argv and the explicit
`WORKTREE_MANAGER_ENGINE_CMD` override retain their existing precedence and
participate in request identity. Each queued product starts with its own
approved context; absent overrides are cleared rather than inherited from
another caller that launched the shared worker. Scoped Windows update
handoffs remain headless without changing ordinary interactive passthrough.
Updater stdout/stderr bytes are retained in the request log; waiting renders
that combined log as UTF-8 on stderr. Stdout is reserved for requested receipts
and status JSON. Read the log when original non-UTF-8 bytes matter;
the read-only authentication-report protocol remains unchanged.

The finite worker uses the shared headless, caller-independent launch primitive
and exits when its queue drains. It does not enroll recurring maintenance,
write an Autorun entry, require an open Picker or create another scheduler.
Unattended credential prompts are disabled; authentication failures remain
observable. A direct Manager request retains its configured source identity
and refuses a changed source selection at execution rather than silently
substituting it. Relative filesystem sources retain their resolved inherited
source anchor, or otherwise their submitting directory, for validation and
acquisition. This anchor is separate from the runner's working directory.
Relative hold-file overrides remain anchored to the actual submitting directory,
not the source anchor.
Bootstrap workers still run from the retained controller, so the submitting
directory need not remain present unless it is also an explicitly required
invocation context.
Credential-bearing source URLs are refused before journaling;
use the existing source credential helper.

The driver keeps requests, logs and results beneath
`~/.copilot-extensions/update-driver`. Canonical update telemetry below remains
separate: a queued request is not yet an executing update run. A worker
interruption is not success and is not automatically replayed; uncertain
execution fences subsequent installations. Never delete its lock or journal
to override that fence. `--recovery-status` reports settlement availability
without changing state. After an independently confirmed kernel restart,
`--recover-after-restart` can settle old-boot interruptions under the same
exclusive authority. It refuses same-boot, missing, malformed or unsupported
boot evidence; age and PID disappearance are not substitutes. Settlement retains
the interrupted failure and does not replay it or start an update. Submit a new
ordinary update explicitly after recovery.

`--recover-for-repair` instead records explicit operator authorization to release
valid queue fences without historical cleanup or boot evidence. It preserves the
failed outcome and reports `recovery_authority`/`released_at`, not native
settlement. It neither runs nor replays an installation. The
[driver recovery contract](../libs/update-driver/README.md#explicit-operator-authorized-queue-release)
defines its scope, atomic migration, current-owner refusal and remaining
foundation/active-takeover limitations.

The same authority wraps public `stage-update` acquisition and
`reconcile-plugins --apply`, including the session provisioning hook. They
retain their canonical JSON result, exit status and status-file behavior;
stage output remains quiet unless `--json` is requested. Planning, indicator
reads and `--check-update` do not enqueue installation work. Nested canonical
update calls explicitly hand off checked custody rather than queue recursively.
The witness is consumed at entry, so unrelated service/daemon children do not
inherit transaction authority.

Native launch bootstrap, reconciliation and staged self-installs now use this
same authority. Standalone first provisioning and individual plugin installers retain their existing
ownership contracts; their remaining convergence is tracked
under [#7758](https://github.com/ThomasMichon/copilot-extensions/issues/7758),
not inferred complete from these migrated entry points.

## Remove an owned installation

The standalone installer provides **`worktree-manager.uninstall`, generation 1**.
Select an extracted, supported Manager payload and a base Python **outside**
the target installation. Read that payload's
`references/uninstall-contract.json` before invoking its installer; support is
not inferred from package-version equality or an ambient command on `PATH`.

```powershell
$python = '<external-base-python>'
$installer = '<external-manager-payload>\scripts\runtime_install.py'
& $python -I -S -B $installer --root '<manager-root>' --uninstall --json
& $python -I -S -B $installer --root '<manager-root>' --uninstall --apply --json
```

The first invocation previews without construction, fetching, service requests
or creating lock files. `--apply` removes validated runtime slots, their
selectors and attributable generated frontdoors/registration. Configuration,
source preferences, project registrations, mappings, routing/recovery history,
unknown root files and persistent lease files remain. There is no data purge.
Settle any running Manager infrastructure through its existing lifecycle owner
first: removal never stops Copilot, its mux, another installation or a service.
Settled routing history is preserved under the daemon's existing spawn lease,
not mistaken for a running daemon or deleted to make removal succeed.
Under that quiescent custody, an owned finite Windows launch-broker registration
is removed through the canonical broker owner before runtime files. Its action
generation stays retained until external Task removal is confirmed. Pending
publication requires installer recovery; unknown removal retains the runtime
and ownership evidence rather than deleting the action beneath a live Task.
Present installations also require a reliable process census (Windows or
Linux `/proc`); other platforms refuse rather than assuming no process is live.

Every present runtime slot must carry the construction-owned generation-1
artifact inventory. Legacy, development, changed, unexpectedly linked, foreign, busy or
otherwise unsupported targets refuse rather than being rebuilt or broadly
deleted. Added ordinary CPython bytecode caches are removable only beside an
unchanged inventory-owned Python source, with canonical cache names and matching
timestamp/size or source-hash headers. Their observed identities join the
in-memory removal plan; the sealed inventory is never rewritten. Unknown cache
contents, links and changes to sealed entries still refuse. Generic frontdoor bytes alone do not establish ownership: removing
shared commands requires the matching current Manager registration.
An interpreter or installer inside the target refuses apply before effects,
including on Windows; use the external invocation above instead of delayed
self-deletion. Missing capability metadata is unsupported uninstall, not
permission to try a different removal mechanism. Install/update remain available
under their existing contracts.

JSON results use `worktree-manager.uninstall-result`, version 1, with contract
identity/generation and status `planned`, `absent`, `removed`, `refused` or
`incomplete`. Exit codes are 0 for the first three, 1 for refusal/incomplete
removal and 2 for invalid syntax. An absent target is not constructed.
Partial removal is never success; withdrawn selectors are not restored to
partly removed slots, and unresolved artifacts/evidence remain for their owner.
There is no installed convenience command or automatic caller migration in this
provider-first expansion. Older providers and rollback targets without the
declaration/inventory retain their previous operations but cannot perform this
uninstall contract.

## Choose the update source (fork / canary branch)

By default the self-updater pulls the Worktree Manager payload from the canonical
GitHub repo's `main`. To track a **fork** or a **canary / different source
branch** for future updates, set a **user-level source override** — a small config
file at `~/.worktree-manager/config.toml` (`[source]` table), managed with the
`source` command (there is **no** environment variable for this):

```bash
worktree-manager source                                   # show the effective repo + ref
worktree-manager source set --ref canary                  # track a different branch
worktree-manager source set --repo https://github.com/<fork>/copilot-extensions.git
worktree-manager source reset                             # back to the canonical default
```

The override is honored by both `worktree-manager update` (the self-update step)
and the bootstrap one-liner (it reads the same config file on re-run), and shows
up in `worktree-manager doctor`. Resolution is simply **config file → built-in
default**; the file is human-editable:

```toml
[source]
repo = "https://github.com/<fork>/copilot-extensions.git"
ref  = "canary"
```

Git-backed acquisition uses per-command Windows long-path support for cloning,
fetching and checkout, including Main and custom refs. It does not change
user-level or machine-level Git configuration. Routine Main and custom-ref
self-update checkouts materialize only `worktree-manager`, its shared `libs`,
and `.gitattributes`,
including when reusing an existing staging checkout; unrelated plugin paths
are not needed for Manager self-update.
The standalone bootstrap expands retained sparse staging before construction:
fresh engine repair also needs the owning `agent-worktrees` installer and SDK.

The finite explicit-bootstrap installer retains the submitting caller's working
directory, so relative filesystem repositories resolve where they were
configured. Its installer, payload and root arguments remain absolute. This
does not add a request-context field, override the configured repository/ref or
change the separately captured absolute KillSwitch path.

If acquired Manager content differs from an already published slot with the
same package version, installation uses a distinct immutable repair generation
instead of rewriting or rejecting the predecessor. Matching healthy generations
remain reusable. I/O failures still propagate; this fallback does not authorize
recovery of unknown execution effects or relax source-evidence validation.

Manager update and bootstrap activation start the selected `mux-daemon` on a
fresh loopback port without requiring a healthy or even present predecessor.
Only the incoming generation is health-checked before routing is published;
the installer does not recover, drain, shut down or terminate the old daemon.
Each protocol-aware incumbent observes the healthy successor, drains its own
accepted work and exits. Brief overlap is expected. The incoming process admits
exclusive work only after acquiring the resident lease released by its
predecessor. Existing cutover records and old code remain untouched; explicit
legacy recovery is separate from ordinary update and repair. Publication proves
incoming readiness, not that every old process has already converged.

For a prepared source payload, `self_install(..., dry_run=True)` compares the
full retained source identity and assembled bytes, not just the package version.
An explicitly supplied complete wheel profile also participates in dry-run
selection: a different lock or local-package set is planned, not reused merely
because the source identity matches.
It reports the selected source/repair slot and marker on exact reuse, or the
requested immutable generation when a different source is planned. Invalid
source evidence is an explicit error; dry-run does not construct or publish.
Apply still runs installed health before reuse. A selected healthy repair of
the same source generation is reused instead of repeatedly rebuilding repairs
from a damaged base slot. Neither path changes the existing config/data root.
An explicit `[source] ref = "dev"` now acquires that Manager repository's
merged `dev` tip in its own clean, owned checkout and prepares it through the
running Manager's trusted tools. Fetched tools are never executed. Its cache
and immutable prepared payloads live below
`<manager-root>/manager-source/runtime-sources/`, independently of the service
`[runtime_source]` selection. The ordinary versioned installer, global commands,
data root and mux cutover owner are unchanged. Acquisition, preparation or
receipt failures are reported without an unqualified fallback; this mode
requires Git. Main and other refs retain their existing Git/GitHub-tarball
path and are not yet source-qualified by this contract. Dev self-update now
prepares a complete application snapshot once through the packaged canonical
composition/build/profile owners, carrying one Main seed and deadline. Acquisition
revalidates before handoff; the existing native input owner then holds the frozen
source, wheels and application through the ordinary install/publication/cutover
path. Preparation does not select or replace the service-runtime source policy.
The standalone Dev bootstrap uses that same complete-input context after
validating its declared process helper. The canonical provider borrows its held
build toolchain for profile preparation, without requiring Packaging in the
base parent. Frozen inputs remain held through the same Manager installer,
publication and cutover; they settle before dispatching the requested command.
Original source-digest and repair-SDK ownership checks still apply to the
executing bootstrap payload, not the acquired replacement. An attributed Dev
payload without `--install-source` is refused; the adapter never silently
reselects it or treats raw source as a complete application. Explicit complete
inputs retain the existing selection path. Recovery-only and adopted
recovery entries do not acquire another Manager source. Qualifying Manager
does not select the engine's build source; the independent service-source
repair contract below owns that choice.
Source/actor controls cover this handoff; actual cold-bootstrap and installed-host
qualification remain open.

`worktree-manager update` owns the update run's telemetry: it records the
self-update as the `manager_self_update` phase, exports the run identity to the
engine it drives, and writes `~/.copilot-extensions/update/update-status.json`.
When bootstrap supplies an explicit environment prepared before that run
started, Manager overlays its run ID, event path and owner for the engine child;
the original environment and unrelated selection/authentication values remain
unchanged. The engine participates in that run rather than creating a second
owner.
A failed cutover fails the run (`command_failed`, exit 1); the engine's exit
code is mapped through the shared update exit-code table (0 success, 1
`command_failed`, 2 usage error, 3 configuration, 4 auth, 5 blocked by a lock,
6 timeout (never reported as success), 130 interrupted; see the
agent-worktrees CLI reference). The run exists before the engine parses the
forwarded arguments, so an engine usage error is recorded as the run's failure
(error code `usage`) and exits 2. Events go to
`~/.copilot-extensions/update/runs/<run_id>/events.jsonl`.

On Windows, ordinary and passive daemon starts resolve the real base
`python.exe`, preserve the calling venv's import context, and apply the shared
windowless daemon launch flags. Recurring PSMux probes and status writes also
apply the shared captured-child window-suppression flags; redirecting output
alone is not sufficient. Windows status writes and liveness probes own their
descendants through a kill-on-close Job, including on timeout or cancellation.
The launch-kind contract is documented in
[`windows-background-process-launch`](../docs/patterns/windows-background-process-launch.md).
This keeps the spawned process handle's PID equal to the runtime PID instead of
publishing a venv redirector's PID. If the venv's direct base interpreter cannot
be resolved, startup fails explicitly rather than retaining an ambiguous
launcher/child identity. Repair that Python installation before retrying.
Existing launcher/child pairs are not rewritten or stopped by this change;
their identity must be classified before automated retirement.

## Independent runtime source acquisition

`runtime-source` manages a separate `[runtime_source]` policy in the same
Manager config. It does not change `[source]` (Manager self-update), Copilot's
marketplace selection, installation roots, or existing runtime data.
The default is `marketplace`, preserving ordinary update behavior.

```bash
worktree-manager runtime-source show
worktree-manager runtime-source set main
worktree-manager runtime-source set stable --ref v1.0.0
worktree-manager runtime-source set exact --ref <full-main-commit>
worktree-manager runtime-source set dev --allow-dev
worktree-manager runtime-source resolve
worktree-manager runtime-source reset
```

`set` replaces the complete runtime-source policy; `--repo` optionally selects
a different credential-free Git locator (local paths must be absolute).
`dev` requires explicit consent, `stable` names a release tag, and `exact`
requires a full commit object ID merged into fetched `main`. Moving branch
choices resolve once per acquisition; the returned commit and tree remain
fixed even if the upstream branch advances.

`resolve` is an acquisition diagnostic, **not installation or activation**.
It maintains a separately owned standalone clone below
`<manager-root>/runtime-sources/`, holds the existing construction lease,
fetches the selected branch/tag and returns its exact source identity as JSON.
Every acquired result also records `main_head`, the freshly fetched Main commit
available as a composition seed. Dev and Main are fetched in the same acquisition;
the selected Dev commit/tree and Main seed remain separate frozen facts. A later
acquisition refreshes Main even when Dev is unchanged, and unavailable Main is
refused rather than replaced by the clone's stale ref. Legacy five-field
`ResolvedSource` callers retain their prior document shape. This metadata does
not select promoted outputs, construct a complete profile or authorize activation.
Reuse never moves the clone's checked-out files. Dirty, linked, worktree-backed,
unowned, unavailable, substituted or unmerged input fails explicitly; neither
an ambient developer checkout nor a marketplace payload is a fallback. Fetched
code is never executed during acquisition. Repository-routing overrides are
removed from Git's environment, while configured credential helpers and scoped
authentication configuration remain available; acquisition does not replace
the user's authentication mechanism.
First-use cloning pins the `origin` remote name and publishes a validated,
owned checkout atomically. Failed initialization cleans only its private
staging directory, leaving a later acquisition able to retry; an unowned
incumbent is still refused rather than repaired automatically.
Checkout publication retries only explicit Windows access/sharing/lock errors,
using the existing versioned-runtime attempt/backoff bounds and the original
deadline. Before retrying, it revalidates the staged owner, origin, clean
checkout and exact head. An appearing destination, linked path or changed
checkout is refused; retry never overwrites or adopts another checkout.

Ordinary `update` qualifies the **exact executing engine** through its existing
installation inventory before source acquisition or Manager self-update. A
missing or unsupported packet-V2 declaration refuses with an engine
upgrade/repair diagnostic; it never tries an effectful new command and then
replays marketplace delivery. For a capable engine, Manager acquires one exact
service-source commit/tree plus Main seed and prepares every eligible runtime
through the canonical complete-application provider before either update stage.
The inventory's owned profile selections supply each application's exact extras.
Existing `--skip-modules` semantics apply. Prepared
generations persist below `<manager-root>/runtime-sources/applications/<attempt>/`; the
complete immutable packet crosses the existing engine process and durable
request boundary. The capability read and execution use the same frozen engine
argv, including call-scoped ownership rather than a stale inherited handle.
The engine remains authoritative for complete portfolio admission, application
installation and outcomes; no alternate executor or recurring worker
is introduced. Marketplace plugin-content refresh remains independent.
Packet V2 requires complete retained wheel/profile/application snapshots, bound
to that exact acquired source and composition. The engine revalidates the
installed profile owner before planning and execution, then invokes the admitted
source's canonical application entry through its existing executor. It does not
adapt packet V1 or replay a failed application through the old shell installer.
See the [owning packet contract](../plugins/agent-worktrees/docs/cli-reference.md#prepared-source-suite-input).
Older pre-wheel providers require the separate standalone force-fresh migration;
source-only consumer controls do not qualify that migration or live installation.

Fresh suite repair can also honor that independent policy when the installed
engine cannot provide inventory yet. It acquires one service-source selection,
holds its lease through repair and the subsequent suite, and prepares the engine
through the executing Manager's trusted toolkit. The fresh publication SDK
revalidates the incumbent registered command owner and default runtime root;
the prepared native installer supplies code, not a new owner or data root.
Repair uses the installer's existing runtime-only update mode, enforces the
complete transaction pin and requires the actual pinned slot and deployment
receipt to be complete. A corrupt completion marker or failed installed-health
check for an existing source generation enters the engine's canonical distinct
`repair` mode, preserving the damaged generation. An already selected healthy
repair with the same complete source identity is reused; the source pin still
names the original source assembly, not a different commit.
The repaired engine then supplies real inventory, and
the suite prepares all eligible runtimes from the same original commit/tree
without fetching a moving branch again. Manager's own source remains independent.
This is legacy/default-root repair; namespaced source repair refuses before
native payload refresh.

Non-repair bootstrap updates and targeted service-runtime updates still refuse a
non-marketplace policy before their effects. A targeted Manager self-update
continues to follow its independent `[source]` policy. `reset` explicitly
restores ordinary marketplace runtime delivery. Invalid bootstrap arguments
retain their existing failure scope without opening aggregate update telemetry.

Released Manager payloads also carry the trusted canonical
[source-preparation toolkit](../tools/prepare_runtime_source.md) in
`scripts/runtime-source-tools/`. It supports Manager's standalone project as
well as runtime plugins without executing fetched tools. Ordinary source-backed
updates invoke only this executing Manager's toolkit in an isolated Python
process and verify its complete receipt and prepared bytes before forwarding.
Fetched tools remain data; they cannot replace the trusted preparer.
The existing update request's source identity includes this policy, so changing
it after submission rejects the old request rather than running a different
runtime selection under its name.

## Read-only daemon status

`worktree-manager daemons status [--json]` reports resident mux daemons for
the selected Manager root. Windows reads the process census, command line,
token SID and creation time through native APIs, without PowerShell or CIM
probe processes. Current-user ownership, root and process incarnation are
checked before and after health; an unavailable identity remains unverified.
When a daemon selects its port internally, the reader uses the active loopback
route only if its recorded PID and nonempty creation time match the observed
process. An explicit command-line port takes precedence over a conflicting route.
This diagnostic reader does not change daemon cutover or retirement authority.

## Restart evidence and notification transport

The existing active mux daemon maintains a private, bounded recovery-evidence
journal under its runtime root at `restart-recovery/evidence.sqlite3`. This is
not a second session or worktree authority. Each positive observation binds the
canonical native conversation UUID, worktree, native environment and boot epoch,
mux incarnation, native process birth identity, owner/mapping revisions and
observation sequence. A new native occupant of an unchanged mux requires an
advanced owner lifecycle revision; delayed evidence from the prior native
process cannot retire its replacement. The engine
corroborates the current native head, conversation data, process identity and mux
binding over its normal process boundary; stale or uncertain evidence is not
recorded as live. A detached live session remains observable.
On Linux/WSL, positive UUID/process binding additionally requires the exact
native session's `inuse.<pid>.hold` inode to be open in that birth-identified
process. A stale PID-shaped filename is insufficient. The descriptor probe is
bounded; missing, inaccessible or unsupported native ownership evidence stays
unconfirmed, never a new verdict that the process is dead. The journal records
the explicit `inuse-hold-v1` proof capability and rejects weaker metadata.
The live mux incarnation is corroborated alongside the native process binding,
not copied from a possibly stale display mapping. Mapping revisions order
refreshes but are not execution identity: rejoining the same execution cannot
clear intentional closure or shutdown-interruption evidence.

The daemon inspects previous evidence before admitting ordinary status requests,
then samples at most one subject per 20 seconds with a four-second owner-query
deadline. Sampling shares the existing active lease and drain fence; passive
cutover generations do not sample. Last-positive evidence survives missing-mux
display tombstones. The journal retains at most 16 events per worktree and
refuses new subjects after 4,096 worktrees rather than growing without bound.
Corrupt, unavailable or incompatible storage remains an explicit blocker.
The active daemon lease holder allows SQLite to roll back an interrupted
transaction while reading existing storage at startup. This neither creates a
missing database nor resets an incompatible schema. Report-only inspection
keeps a read-only connection: if SQLite needs that rollback, the report exposes
the read-only blocker rather than repairing the database itself.

Sampling includes stored journal subjects as well as live mappings. One owner
query returns current hosting and the exact previously observed conversation's
canonical activation lifecycle, so a tombstone cannot hide its subsequent End.
New qualified observations bind the activation ordinal and opaque original
source in addition to the native/mux generation. Legacy evidence defaults to
unqualified; periodic reads never invent or assign a registration source.
Lifecycle reads accept the owner's full retained budget of 256 intervals;
activation ordinals remain monotonic and may exceed that retained count.
Intentionally closed journal subjects and definitively consumed source-bound
hook Ends without a live mapping stay retained but do not consume polling turns;
a live mapping returns them to the rotation. End consumption is separate from
exit disposition: non-user exit causes remain unknown, and inferred Ends remain
probeable for later hook corroboration. The consumption marker survives
same-generation refresh and resets only for a confirmed new execution generation.

Only a uniquely source-bound native `user_exit` can assert intentional closure.
Other reasons, repeated same-physical/same-UUID activations, missing source proof
and incompatible owner responses stay unknown or unconfirmed. A historical End
cannot retire a replacement. The journal serializes native End with shutdown
metadata: a racing notification takes precedence over an apparent user exit, and
an End after cancelled shutdown remains conservatively uncertain for that
execution generation, including across intermediate inferred or unknown exits.
This does not infer a shutdown cause from an exit code or
wall-clock timestamp.

Native Linux has an explicitly requested, default-off logind notification
transport in this same daemon. It negotiates a real delay-inhibitor FD before
subscribing, authenticates the unique logind owner and reads the initial
`PreparingForShutdown` state. A shutdown signal checkpoints matching
last-positive generations transactionally, then releases the inhibitor;
cancellation durably reconciles the matching notification before reacquiring.
If native End preceded cancellation, its shutdown-interruption claim becomes
unknown with a sticky cancelled-shutdown marker, not a revived execution.
After a same-boot pause or replacement, the adapter recovers persisted
notification identities for this exact environment/boot. An initially cancelled
native state reconciles those identities durably before reporting `watching`;
an ongoing shutdown retains them for its later cancellation. Batch cancellation
is one transaction and cannot affect foreign epochs or explicitly closed
generations.
Checkpoint lock contention fails immediately rather than spending the delay
budget waiting for SQLite, and a progress deadline can roll back an expired
checkpoint. This is not a hard bound on operating-system filesystem stalls.
Owner loss, bus disconnect, malformed messages, denied permissions and failed
writes stay visible; no elevation or busy reconnect loop is attempted.
Permanent failure disconnects the bus and drains pending tasks and received
descriptors before the adapter stops pumping, including a timed-out inhibitor
request whose FD reply arrives late.
One daemon-owned I/O thread constructs, pumps and closes the native adapter.
Its at-most-50-ms I/O waits remain independent of ordinary blocking attachment,
owner and status work; no parallel scheduler or separate daemon is added.
Only explicitly requested native Linux notifications under current active
admission can start it. Default-off, unsupported and blocked paths create no
thread or busy reconnect loop.
Drain closes its admission and signals stop independently of the ordinary loop.
Clean drain includes every admitted callback, journal write and native resource
cleanup; pause/shutdown join that owner before the active lease can end.
Undrain can admit a new owner only after the previous thread has stopped.
I/O/startup failures stay visible, and cleanup failure cannot report clean drain.
Descriptor-release failures remain sticky even when adapter cleanup returns
normally or its event loop has already closed; thread exit is not cleanup proof.
Disposable private-bus coverage proves a shutdown checkpoint and inhibitor
release within a one-second native delay while ordinary work blocks longer.
This does not establish hard real-time filesystem/OS bounds or real host
shutdown/startup coverage. Health retains `qualified: false`; automatic recovery
cannot treat the transport alone as platform qualification.
See [configuration](docs/configuration.md#restart-recovery-requests) for the
strict, default-off preferences and capability reporting.

```bash
worktree-manager mux-daemon recovery-evidence
```

This read-only JSON report creates no missing journal and accepts no apply flag.
It distinguishes same-boot observations, foreign environments, explicit closure
evidence and unconfirmed previous-boot candidates, and reports requested recovery
capabilities without opening a native bus connection. `automatic_eligible` is always
`false`: this evidence foundation does **not** launch or enroll recovery,
configure boot startup, deliver a restart seed, or grant operational permission.
The journal's reasoned exit/shutdown APIs do not independently prove native
notification coverage. Missing hooks, generic tombstones and exit codes alone
never assert intentional closure or recovery permission.
Invalid configuration, including non-UTF-8 input, is a JSON inspection blocker
and disables recovery admission without preventing ordinary mux service.

An explicit `automatic_restore = true` request freezes qualified previous-boot
candidates before ordinary startup reconciliation and observes their exact
owner receipts, under the daemon's existing active lease. It never launches.
The private `restart-recovery/attempts.sqlite3`
retains each original execution, worktree creation nonce, head revision and
target epoch under a deduplicated attempt identity. A same-boot daemon restart
or later evidence refresh does not overwrite that frozen snapshot. Legacy
capture without the owner binding remains `capture_binding_qualified: false`;
unknown/intentional closure, cancelled shutdown, same-boot and foreign evidence
cannot become frozen candidates. No current owner/task/hold admission or native
launch is implemented by freezing.

Attempt inspection is read-only and included in `recovery-evidence`; absent state
is not created and corrupt, foreign or torn state is not silently reset.
The first writer atomically publishes a fully initialized schema without
replacing an existing file. A truncated/empty existing database, non-text SQLite
columns and orphaned transaction sidecars remain blockers; they cannot allocate
a replacement identity. Filesystems without atomic hard-link publication refuse
initialization rather than falling back to an unsafe creation path.
Out-of-range numeric evidence and excessive JSON nesting also remain structured
recovery blockers, without preventing ordinary mux startup.
Transactions preserve prior attempts across a crash before commit and serialize
concurrent freezing. At most 4096 attempts are retained, including acknowledged
history, each bounded to 32 KiB plus one receipt of at most 32 KiB. Capacity
refuses new freezing rather than evicting identities.
`frozen` means **awaiting owner admission**, never launched or seed-acknowledged;
it is not an exactly-once execution guarantee. Default-off and passive daemon
paths do not create attempt state.
Initial activation and passive-generation promotion freeze only after acquiring
the active daemon lease, inside its existing drain-visible loop-mutation
boundary. A draining generation admits no new freeze, and an already admitted
freeze must finish or report its blocker before drain can report idle. The
existing update/cutover owner and notification resource teardown are unchanged.

While this explicit preference remains enabled, the active daemon samples at
most one unacknowledged attempt's owner receipt per twenty seconds, with a
four-second owner-call timeout and the same no-update/no-reconcile read boundary
as `--check-launch`. It rotates through retained same-environment attempts,
preserving each historical target boot. Completed ACKs need no further probe.
Refusing to freeze a new candidate at capacity does not strand existing
receipts: observation requires confirmed startup evidence and epoch, and
independently validates the retained database. The freeze blocker stays visible.
Sampling runs in the ordinary loop's existing mutation fence, not on the native
notification I/O thread or a parallel scheduler. Admission is rechecked after
owner I/O before persistence: observed drain refuses the write, while an
already-admitted write must finish before drain can report idle.

The first valid observation transactionally adds an outcome table to the
existing version-1 attempt database. Read-only inspection accepts either version
without migrating. Frozen payload bytes and attempt IDs never change. Each
retained receipt must match the exact original request, and states only advance
from `not-reserved` through `launch-uncertain`, `mux-created` and
`seed-acknowledged`. Skipped intermediate observations are allowed; regressions
and conflicting ACK execution bindings are blockers, not overwritten evidence.
Unavailable owners and failed writes preserve the last retained outcome.
Malformed, orphaned or oversized outcome rows block recovery without reset.
The authenticated daemon health includes `outcome_observation` with the last
sample's state or explicit blocker; ordinary `recovery-evidence` includes the
retained `outcome` inside its frozen attempt, without contacting an owner.
Neither receipt persistence nor its absence grants retry authority, current
liveness, completed restoration or automatic eligibility.

To request a provisional current-worktree check for **one existing frozen
attempt**, without enabling recovery:

```text
worktree-manager mux-daemon recovery-evidence --check-attempt <attempt-uuid>
```

The target epoch must still match this environment. The Manager passes the
frozen exact head, creation nonce, path, lifecycle and source activation to
agent-worktrees through its attributed process boundary, with a four-second
timeout and `WORKTREE_NO_UPDATE=1` / `WORKTREE_NO_RECONCILE=1`. It checks the
response version and exact typed request binding; old owners, errors and
timeouts remain visible blockers, never a legacy/fresh-launch fallback.
The optional `owner_check` reports provisional current owner facts. A single
active task claim with an explicit local/central writer can additionally carry
`task_policies`, checked through that writer's registered read-only provider.
The current task must match the original native owner and be started, unheld,
unsteered and without a pending resume or waiter. Changed, paused, ended,
pending, externally claimed, opaque/legacy/mixed-task or profile-assigned
worktrees remain unqualified. A matching worktree is still **not admitted**:
action-time task/claim policy and acknowledged automatic restart Resume
remain unresolved, so this inspection returns a blocker/nonzero status
even on a match. Neither command writes an attempt outcome, reserves a launch,
starts a conversation, changes enrollment or contacts the host notification
service. Ordinary inspection makes no owner call; these explicit inspection
commands do not enroll daemon polling or enable the recovery preference.
Failed or timed-out mux listings remain unknown and cannot establish the mux
absence needed for even a provisional worktree match.

`worktree-manager mux-daemon recovery-evidence --check-launch <attempt-uuid>`
additionally offers an explicit read-only owner-outcome inspection. It composes
the frozen attempt UUID and original source epoch/PID/birth with the exact
agent-worktrees `embody --restart-request ... --restart-receipt` API. The
versioned, typed response distinguishes `not-reserved`, `launch-uncertain` and
`mux-created` (version 1), and `seed-acknowledged` (version 2). Only the last
includes the original resumed agent's explicit STATUS FIRST assertion bound to
the fresh native source, target epoch, mux incarnation and owner/activation
revisions. The owner corroborates its actual live native hold and invoking
ancestry under the existing lifecycle/admission/record fences. Registration or
the Manager caller cannot manufacture that acknowledgement. No outcome marks
restoration complete or grants automatic eligibility. Old
owners, mismatched requests, unavailable owners and corrupt receipts remain
blockers. The command returns nonzero because recovery is still unqualified.
It does not rewrite the immutable frozen attempt, acquire a daemon lease,
reserve or launch, or turn an absent/uncertain receipt into retry authority.
Ordinary inspection still performs no owner call.

`--check-launch` can inspect an older frozen target boot in the same
platform/environment; it preserves the attempt's original target rather than
rebinding it to the current boot. The owner returns the exact retained request
from its bounded history, not just the latest outcome. `--check-attempt` remains
strictly current-boot-only. Neither historical read establishes current liveness.

`worktree-manager mux-daemon recovery-evidence --check-survivor <attempt-uuid>`
explicitly requests a **current** original-session hosting snapshot through
agent-worktrees `session-role --restart-survivor`. Like `--check-attempt`, it
requires the frozen target boot to remain current; it never rebinds historical
attempts. The exact request and typed response bind current native UUID,
PID/birth, source, mux incarnation and newer owner/activation revisions to the
frozen original. The owner requires retained original history, a current native
hold, unchanged head/path/incarnation and qualified claim/profile facts.

The optional `survivor_check` stays distinct from `launch_receipt` and retained
`outcome`. A confirmed survivor is not adoption or STATUS FIRST reconciliation;
all reuse, launch, seed-ACK, restored and automatic flags remain false.
Unconfirmed hosting, old owners and timeouts remain explicit blockers, not cold
Resume or retry permission. This command returns nonzero while recovery is
unqualified. It performs no attachment, prompt delivery, launch, attempt write,
configuration change or daemon enrollment; ordinary inspection still makes no
owner call.

For an acknowledged receipt the blocker identifies remaining native recovery
qualification, rather than incorrectly reporting missing seed acknowledgement.
The receipt is durable historical pickup evidence, not a new live status probe:
later exits or policy changes require their current owners' reconciliation.
No Manager attempt transition, retry, receipt reset or automatic launch follows
from this read.

The explicit owner restart-launch increment holds its existing lifecycle,
native-admission and strict record-writer fences through final validation and
mux creation. It durably records uncertainty before invocation and supplies the
original UUID plus full interactive STATUS FIRST seed. It currently admits no
task/producer/profile-bound worktree and has no automatic caller in the Manager;
only an empty claim ledger or the exact canonical original self-session claim
is admitted. A different later-boot request can proceed only from the latest
acknowledged execution, retaining every prior outcome within the owner's
16-entry/32-KiB bounds and reserved ACK/counter capacity. Unacknowledged launches
and exhausted history remain blockers, not permission to reset or evict state.
Persisted owner launch outcomes are independent of Manager process lifetime;
`frozen` in the Manager database is historical candidate state, not evidence
that no native invocation has occurred.

Native Linux and WSL have evidence-epoch readers; WSL includes its init-process
incarnation because a distro can restart without restarting the shared kernel.
Native Windows, macOS and container epochs are explicitly unqualified, not
silently treated as Linux. Qualified native shutdown notification, automatic admission,
qualified exact-conversation Resume and acknowledged restart-seed delivery remain tracked
under [#4564](https://github.com/ThomasMichon/copilot-extensions/issues/4564).
An evidence report is not completed restoration or installed-release proof.

## Manage the harness (state views)

Once set up, the Worktree Manager provides state views read from the harness's
own files, plus explicit local integration commands:

```bash
uv run python -m worktree_manager projects        # harness repos (binstubs + profiles)
uv run python -m worktree_manager projects <name> # config dir, linked knowledge repo, profiles, enabled plugins
uv run python -m worktree_manager projects --json # strict read-only version-1 project snapshot
uv run python -m worktree_manager repos           # every known repo + indicators
uv run python -m worktree_manager repos <name>    # worktree mode · agent mode · pr model · ownership · remote
uv run python -m worktree_manager worktrees       # live worktree counts per project (via the engine)
uv run python -m worktree_manager worktrees <name> # one project's worktrees: state, sync tags, titles
uv run python -m worktree_manager plugins --status  # known plugins vs. what is enabled user-global
uv run python -m worktree_manager contracts --project <name> # contributed pivots/actions/cards/config
uv run python -m worktree_manager refresh-profiles <name> # Windows: reconcile local profiles + shortcuts
```

`projects --json` exposes registry-owned identity as
`{"version":1,"projects":[...]}`. Each record contains only `name`, selected
`anchor`, `config_dir` and Boolean `expose_agent`. It performs strict registry
reads without provisioning, registration or repairs.
An absent profile returns an empty list without creating it; malformed or
unreadable inputs return nonzero with `state: "unavailable"`, never successful
absence. Human-readable project listings retain their existing tolerant behavior.
This optional read form is additive; callers of older Manager versions must
refuse non-JSON/unsupported output before apply, not infer an empty catalogue.
It deliberately omits PR model, plugin enablement, machine rosters and runtime
configuration/readiness. Those unrelated inputs are not inspected or silently
represented by defaults; this ownership view is not a full readiness proof.

`refresh-profiles` is the narrow, JSON-producing local integration command.
It collects saved project selections once, applies the existing fragment and
Terminal state/settings reconciliation, and refreshes native/WSL shortcuts.
Unchanged fragment content and shortcut properties are not rewritten. It does
not stage a plugin payload, build a venv, or probe a remote host. A missing
destination, invalid selection or failed shortcut operation returns a blocked
result and nonzero exit rather than claiming synchronized profiles.
Publishing also refuses malformed or unreadable registries and resolved machine
rosters, invalid registry entries and dangling configuration links before
touching fragment/state/settings outputs. Actual publishing collection uses
strict registry reads too; preliminary validation never authorizes a later
tolerant reload. Genuinely absent optional files remain supported. Read-only
human-readable state views and previews retain their tolerant behavior.
Present registries require their explicit `projects`/`repos` mapping; an empty,
null or missing-key document is not permission to retire profiles. Shortcut
display names are validated before fragment publication, not after it. Engine
callers pass their resolved `--machine` key so global configuration and machine
detection do not depend on a project-local machine setting.
For a selected installation cell, the engine additionally sends
`--input-roots-stdin`: a version-1
`copilot-extensions.terminal-profile-input-roots` JSON object with `context`,
`payloadRoot`, `registryRoot`, and a `projectRoots` name-to-directory mapping.
The engine resolves project identities; Manager validates the owning
agent-worktrees installation receipt and cell containment, then uses those
same explicit roots for preflight and publishing collection. Missing project
mappings or invalid cell inputs block instead of reading legacy selections.
This does not redirect Manager discovery, Terminal output or shortcut paths:
those remain machine-global. Direct legacy invocations need no extra argument.
Publication also validates existing Terminal fragments, state and settings
before writing, retaining the input bytes through reconciliation and refusing
observed drift. Missing optional documents remain valid; malformed or denied
reads never become empty profile inventories. Terminal settings support JSONC
comments and trailing commas. When settings need cleanup, their original bytes
are backed up; unchanged settings are not rewritten.
Strict YAML publishing rejects duplicate mapping keys before migration or
defaulting. Fragment-directory enumeration errors are failures, never evidence
of an empty inventory.
Publishing rejects blank/non-string selection identifiers and unknown kinds,
including when inputs change during collection; it never silently converts a
shell typo into an agent launch.

The **worktrees** views are the first slice of the extracted Picker: they read
**live** worktrees by shelling out to the `agent-worktrees` engine
(`agent-worktrees --project <p> list --json --current`) — the **process
boundary** the Textual Picker uses. The Manager never imports the plugin;
coupling is through the engine's stable JSON verbs or optional resident read
contract (the pinned
[engine ↔ Picker contract](../plugins/agent-worktrees/docs/engine-picker-contract.md)),
and the client ([`engine_client.py`](src/worktree_manager/engine_client.py))
requires the current-roster authority contract. Older or incompatible engines
produce an actionable upgrade error rather than silently dropping authority.
Unreachable residents permit only explicitly labeled, independently indexed
advisory rows or an unavailable error, never caller-side reconstruction.

Plugin-contributed interactive surfaces are discovered independently from each
enabled plugin's resolved active payload, including directory marketplaces.
`worktree-manager contracts` validates the
versioned manifest contract and reports disabled, malformed, duplicate, legacy,
or missing-command contributions without importing plugin code or requiring a
plugin installer to write into Manager-owned state. See
[`docs/plugin-contribution-contract.md`](docs/plugin-contribution-contract.md).
The contract also lets one available contribution designate the initial home
pivot and lets providers declare view-scoped actions alongside row actions, so
the Manager does not need a provider-specific home or "New" path.
Forms may declare read-only request preparation and exact receipt commands.
Manager retains the complete original request before sending it, including
file and directory publication barriers; a failed barrier prevents Submit.
Identical retries re-establish those barriers before invoking Submit; receipt
reads remain available when storage is unavailable.
Recovered submissions refresh the original provider's original scope even
when command output leaves acceptance uncertain; receipt-only reads do not
mutate the display cache.
New parent directories are published write-through on Windows; POSIX writes
sync the containing directory and each newly published parent's entry, not
unrelated existing ancestors. Both platforms stage a local pending marker
before publishing a new directory; a leftover marker requires a successful
parent-directory flush before the directory can be reused.
Directory publication is exclusive, so concurrent writers cannot replace
another writer's new empty directory; unavailable exclusive-rename or flush
primitives fail closed.
Inspection loads observations
off the UI thread and reports unreadable observations without hiding the
original request. **Retained
responses** (`Ctrl+Shift+R` from any top-level view) remains reachable after
restart, card/row removal or provider removal. It supports read-only inspection,
explicit owner receipt checks and a separately confirmed identical retry;
changed/unavailable providers leave recovery read-only. Local records and
command observations are not proof of acceptance or answer consumption.
Available pivot contributions appear beside Worktrees in the Picker. Their list
commands run in background workers, so the UI opens before cross-process reads
finish; each pivot keeps its own cached rows and loading/error/empty state, and
renders either its declared columns or the contract's id/title/badge fallback.
The current internal Worktrees surface now uses that same declarative pivot
model (`entity`, home, list envelope, columns, row actions, and view actions)
while retaining a typed cross-cutting `Worktree` model for launch/session
integration. This is the migration seam for moving the declaration into the
agent-worktrees payload without pretending the Manager cannot understand
worktrees.
Streaming, actions, cards/forms, and configuration sections landed through the
subsequent parity slices, and the bundled Picker was retired once that parity
work completed.

## Production Picker transplant

Explicit source-only performance observations reuse the Picker's existing
frame-health writer. See [source observation](docs/source-observation.md) for
the native headless fixture, optional local export, complete reports and the
boundaries that are **not** headed/installed qualification.

The established production Picker source now lives under
[`src/worktree_manager/production_picker/`](src/worktree_manager/production_picker/)
as a wholesale copy of the still-shipping agent-worktrees implementation.
`worktree-manager picker <project>` runs that transplanted UI.

The production validation and preview surfaces live with it:

```bash
uv run python -m worktree_manager picker mock <project> --local
uv run python -m worktree_manager picker screenshot <project> --format text
uv run python -m worktree_manager picker screenshot <project> --format svg --out picker.svg
pwsh -File scripts/preview-picker.ps1 -Project <project> -Format svg -Out picker.svg
cd scripts/picker-snapshot && python install.py
uv run python render.py picker.png
```

Current-roster readers consume the optional
[live reporting context](../plugins/agent-worktrees/docs/engine-picker-contract.md#live-reporting-context)
from the serving engine, including heartbeat-only renames and unavailable
mapping withdrawal. Reporter naming never rewrites the configured source,
selection key or action route. Providers without the envelope retain the legacy
row-tuple baseline; no local-host lookup substitutes for a remote reporter.

New Worktree options and the Open/Resume action menu expose independent,
default-off `AHP` and `No Mux` checkmarks. `AHP` asks the Manager to create or
verify a same-machine hosted session for the exact engine-resolved worktree;
`No Mux` controls only terminal presentation. Configuration is user-owned in
`~/.worktree-manager/config.toml`; see
[`docs/configuration.md`](docs/configuration.md). Missing or mismatched AHP
configuration, account, host, session, or working directory fails closed and
never falls back to a direct Copilot process.

On resume, an `active` or `unknown` persisted execution leg forces its provider
even when the AHP checkmark is off; only a disposed or absent binding may return
to direct launch. An AHP-owned row exposes **Dispose hosted session** as an
explicit action. It disposes and terminally marks the hosted binding so a later
normal Finalize can proceed; terminal exit never performs that destruction.

### Actions dialog refinement

**Finalize** is available on ordinary machine-backed worktree rows and bulk
selections regardless of cleanup bucket. It opens an explicit confirmation;
cancel runs nothing.
Controller-owned children and unsupported sources retain their action boundary.
Known live sessions, lock residue and unavailable mux observations return a
visible refusal without invoking the engine. Resolve those lifecycle conditions
before retrying.

Otherwise the existing asynchronous executor calls the selected engine's
canonical `finalize <id> --json`, never force cleanup. Content, objectives,
PRs and resource obligations remain engine-owned checks; the menu does not
reimplement them or remove saved conversations. Failed results retain the
engine's diagnostics. Press **v** in completed Finalize progress to read the
full scrollable result, including exact claim references; ask
`help clean up worktree <id>` for the existing claims/obligations procedure.

Actions opens from a private snapshot of the published row. Tracking-path and
locality discovery run on its existing background worker, never before modal
push. Unknown locality does not enable local-only AHP operations.

Each refinement is bound to the original modal, open epoch, source-qualified
row, setup epoch and project binding. Closing/reopening, replacing a source or
rebinding the project discards late results. Accepted results update the header,
verbs, modifiers and dialog-owned dispatch maps together without rewriting the
authoritative row feed. Missing records or failed probes retain cached content
with a `liveness not verified` footer instead of presenting a successful refresh.
The accepted status survives results arriving before composition or mounting;
mounting replays the accepted header, verbs and footer rather than clearing it.

These are functional guarantees; headless modal tests do not establish the
native Actions-under-one-second or input-under-100ms budgets.

### Configuration snapshots and locality

The production Picker's file-cache scopes share a bounded, binding-qualified
generation. Existing background demand revalidates file bytes after 60 seconds;
unchanged bytes reuse parsed YAML, and concurrent consumers share one read per
key and generation. A zero-TTL scope reuses only within that pass. This is
demand-driven freshness, not a new polling service or a claim that an idle view
refreshes on a timer.

Input reads never wait for an in-flight file load or perform cold discovery.
Missing/expired snapshots report not-ready; malformed or unreadable inputs are
not installed as valid empty mappings. Manual reload invalidates before its
worker collects data, preserving newly prepared snapshots and accepted scalar
bindings. Derived machine-name mappings refresh without resetting selection and
reject obsolete source/setup completions.

Supplier fallback uses `harness_state.project_anchor_selection`: at most the
two owner registry files are read, using the same platform-path and anchor
precedence as the full builders, without materializing unrelated projects.
The actual supplier roster is included in the file generation. These YAML
readers do not expand external templates/includes.

Known locality retains its Boolean behavior. Unavailable locality is a visible
refusal, never a remote/SSH classification: ordinary resume, headed launch and
internal embodiment/resume callers stop before exit or launch effects.
A warm project configuration alone does not establish locality: the same file
generation's roster must have been prepared by the existing background source
or machine-mapping worker. Cold roster reads refuse even a configured native
alias until that authority is available. Selected supplier entries retain
absent/null compatibility, but other non-mappings are rejected; explicitly
supplied non-mapping registry containers, including null, are invalid.

### Developing a pivot: render early, render often

Any change touching a pivot's columns, derived fields, row grouping, or
section ordering (Worktrees or a plugin-contributed pivot alike) should be
verified **visually**, not just by reading a diff or trusting a passing test
— column widths, truncation, and drop-priority under narrow terminals are
exactly the kind of regression a unit test's string assertion can miss while
still "passing". Produce a render **before and after** the change, and again
at each meaningful milestone while the change is in progress, not only once
at the end:

```bash
# Fast, no dependencies -- read directly in a terminal or paste into a PR:
uv run python -m worktree_manager picker screenshot --demo --format text

# Shareable PNG (once, to install the Node dependency + cache the font):
cd scripts/picker-snapshot && python install.py
uv run python -m worktree_manager picker screenshot --demo --format svg --out /tmp/pivot.svg
node svg2png.mjs /tmp/pivot.svg /tmp/pivot.png 3
```

`--demo` needs no live engine or real worktrees -- it renders the real
production Picker against the deterministic fixture in
[`src/worktree_manager/demo.py`](src/worktree_manager/demo.py) (see the
`--demo`/`--preview` paragraph below). If the fixture doesn't yet exercise
the field you're adding (a new derived column, a new state, a new claim
shape), extend `demo.py` with representative values first -- a render against
data that never populates the field you're changing verifies nothing. Compare
the before/after renders side by side (or via the `tests/production_picker/`
golden-screenshot harness, `AGENT_WORKTREES_UPDATE_GOLDENS=1` to
intentionally accept a diff) before considering the change done, and keep
each phase's render as evidence in the tracking effort/PR rather than only a
prose description of what changed.

`picker mock` renders real-shaped state but simulates mutations. The screenshot
command exports the production character grid, ANSI grid, or SVG through the
same compositor used by the live app. `scripts/picker-shot.py` adds
identity-obscured/shareable captures and validates the PNG signature and
dimensions after browser rasterization. `picker [screenshot] --demo`/
`--preview` renders the same real production Picker against deterministic
mock data instead of live engine state -- see `preview.py`'s module
docstring for the two injections this composes (a fake-engine worktree data
source, and a manifest-injected mock pivot exercising the same cross-plugin
pivot registry a real contributed pivot uses). The older minimal `picker_app`
(`demo_source`/`capture_svg`/`run_picker`) is no longer used by `--demo`; it
remains only as the still-live `LaunchRequest`/launch-compose machinery
shared with other commands.

The screenshot command's optional `--wait <seconds>` for a contributed pivot
must be finite and non-negative; zero disables that additional wait. Invalid
values return usage error 2 before preview, registry or bootstrap work. Direct
capture APIs reject the same values with `ValueError` before starting capture.

Non-live screenshots wait for the current setup epoch to apply before reading
the compositor. Setup completion, diagnosed failure and teardown wake the
capture without repeated per-widget render barriers. Production captures use
the engine's 120-second operation budget for the whole setup wait, including
plugin verification and source loading; this is not a first-paint speed target.
The low-level fixture capture helper retains its five-second default.
Setup failure, teardown or deadline expiry returns an error, not a successful
capture of an unloaded screen. Interactive startup remains asynchronous.

During the migration, the copied presentation modules reach their existing
engine/data operations through a private compatibility boundary to the active
agent-worktrees runtime. That boundary is intentionally temporary: it preserves
the proven UX first, while later slices replace each dependency with the
Manager-owned CLI/service contracts. The full legacy UX, golden, cache, pivot, streaming, steering, profile, SSH, and
selection corpus now runs from `tests/production_picker/` against the
Manager-owned package. Bare project invocation remains on the bundled
production Picker until remote/base-repo decisions and final live validation
are complete.

**Projects** are the repos promoted to first-class harness projects (worthy of
binstubs + profiles, in `projects.yaml`); **Repos** are everything else in the
registry. The views expose worktree mode, agent mode, PR model, ownership, the
linked knowledge repo, and per-project enabled plugins. Config *editing* (linking
a knowledge repo, per-plugin config, adoption) is being built out under Phase 3/4
([#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
[#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)).

## Plugin-knowledge model (Phase 1)

The Worktree Manager learns **which** plugins exist dynamically from the marketplace
— a nearby checkout when present, otherwise the remote published marketplace ref
(the same ref the bootstrap fetches; set with `worktree-manager source set --ref`). Its
installer-owned catalog
([`src/worktree_manager/data/plugins.toml`](src/worktree_manager/data/plugins.toml), read
by [`catalog.py`](src/worktree_manager/catalog.py) + composed in
[`model.py`](src/worktree_manager/model.py)) is a **knowledge overlay** on that
membership: `kind`, the ordered "what to do" steps, and any prereqs beyond what a
plugin publishes. A discovered plugin with no catalog entry is kept with
**inferred defaults**, so the installer never breaks on a newly-added plugin.

```bash
uv run python -m worktree_manager plugins              # effective model (discovered + overlay)
uv run python -m worktree_manager plugins agent-worktrees   # one plugin's prereqs/config/steps
uv run python -m worktree_manager plugins --prereqs    # de-duplicated union of prerequisites
uv run python -m worktree_manager plugins --reconcile  # coverage: uncovered / phantom / prereq drift
```

This resolves **DQ2** in favor of an installer-side catalog **over dynamic
membership**: it imports no plugin code and requires no plugin to publish
anything installer-specific, yet it reads metadata plugins already publish for
their own reasons (the marketplace entry, `scripts/service.yaml` prereqs). Because
membership is discovered there is no frozen list to police — `--reconcile` is a
*coverage* report (uncovered = discovered but not authored, running on inference;
phantom = authored but not discovered), not a hard gate. A contract test asserts
the one-way boundary holds in both directions.

## Boundary: one-way, dependency-free

The Worktree Manager *knows about* the plugins and their configuration and "what to
do" to make each ready — but **neither depends on the other**. No plugin
requires the Worktree Manager, and the Worktree Manager never joins a plugin's
dependency graph. A plugin installed and run with the Worktree Manager never present
behaves exactly the same.

## Develop

```bash
cd worktree-manager
uv run python -m worktree_manager          # run the app
uv run python -m worktree_manager --version
uv run --extra dev pytest -q              # tests
```

Default pytest collection owns `tests/`, including `tests/production_picker/`,
in both development and materialized release layouts. Releases embed complete
shared libraries under `libs/`, including their test sources; those suites
remain owned by their canonical library lanes, not by Manager's default run.
`tools/test_worktree_manager_collection.py` checks real materialization with a
bounded selection of actual Manager test modules, preserves their collected
IDs, and verifies that embedded library code and tests remain intact. It
reconstructs development references from either checkout layout and compares
canonical bytes using the materializer's exclusions for generated build metadata.

Python + [uv](https://docs.astral.sh/uv/); the Phase 4 visual worktree-manager uses
[Textual](https://textual.textualize.io/).
