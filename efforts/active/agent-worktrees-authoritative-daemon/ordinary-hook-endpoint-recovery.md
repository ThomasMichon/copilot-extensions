# Ordinary-hook shell facts and asynchronous recovery - proposed source slice

Back to the [Active parent](README.md). Coordination is the finite P8388 case
retained at #3761, including the
[bounded source claim](https://github.com/ThomasMichon/copilot-extensions/issues/3761#issuecomment-6649260548).
This proposal is not the whole authoritative-daemon migration, a receiving
receipt for other trackers, or permission to activate an installed service.
The original synchronous proposal cleared review in #10585. This amendment
changes that design and must clear review before replacement implementation.

## Amended direction

Ordinary hooks execute in their shell. Live computation and bounded local reads
are permitted; blocking heavy Python/CLI discovery and policy workers are not
the replacement design. The existing daemon/lifecycle producers prepare
hook-readable facts for running worktrees. A boot nudge is optional and
asynchronous: the current event does not wait for startup, readiness or a
publication, and is not sent again when the daemon becomes available.

The unpublished synchronous prototype is retained as historical work, not the
shell solution. Its fixed-budget native cold-start controls failed before the
required launch/descendant observations. Import reduction and unit controls do
not qualify that flow. This amendment does not widen those failed test limits,
discard source, claim deployment or acquire another contributor's work.

## Context and observed gap

The current Worktrees pre/post entries still depend on Python for policy,
admission or fallback. Windows' post-tool entry has a shell IPC fast path, but
its local fallback invokes Python. POSIX enters the Python client directly.
Recovering a missing endpoint synchronously adds process preparation before
ordinary evaluation; its result is startup status, not data needed to evaluate
the current event.

The current source already has two relevant owners:

- Managed-session admission from #10589: automatic behavior applies only to
  adopted harness anchors and owned Git-linked worktrees, not every repository
  near a global installation.
- The [precomputed guard map](../precomputed-hook-guard-map/README.md):
  `guarded_anchors.py` publishes caller-scoped managed observations and
  `guarded_anchor_map.py` reads them without hook-triggered topology acquisition.
  Managed mutations withdraw old views; out-of-band edits use explicit
  preparation/refresh, not watchers or TTL recomputation.

Compose those owners rather than recreating their caches or weakening their
admission. Preserve event-time guards, advisory behavior, the exclusive active
implementation selector and Windows' no-resend behavior after uncertain IPC.
An absent endpoint is not proof its owner died; a started process is not ready.

This realizes part of the parent
[resident availability intent](../../../visions/plugins/agent-worktrees/README.md)
and follows
[singleton correctness](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md),
[circumstantial hook contracts](../../../docs/patterns/circumstantial-hook-contracts.md)
and [version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md).
The existing lifecycle, singleton/cutover, source/installation, current-row and
process-custody owners remain authoritative. No new daemon, generic cache,
poller, installation authority or process reaper is proposed.

## Required state and original publishers

Paths are under `plugins/agent-worktrees`. This map distinguishes present
interfaces from proposed shell exposure; it is not proof of a complete input
witness or installed invocation.

| Hook need | Original owner / existing exposure | Shell consumer responsibility |
|---|---|---|
| Applicable managed workspace/session | `scripts/managed_session.py`, project identity and selected project registry; current positive cache with source/platform/input invalidation | Consume owner-prepared membership facts and retain current event/cwd/input checks; unadmitted sessions are inert. Never replace unknown ownership with automatic enforcement |
| Selected installation/source/contract | Installation-context, runtime-selection and command-binding owners | Use their passive selected-generation locator; no per-event full runtime import, tree hashing or provisioning |
| Current tool, arguments, targets and rule occurrence | Host event and original guard parsers | Bounded shell decoding and target extraction; preserve aliases, quoting, Git options, multiple targets and unsupported-shape semantics |
| Related guarded roots/delegation | `guarded_anchors.py`; `registry_root/guarded-anchors/<caller-key>.json` | Port the existing bounded schema1 reader and metadata-only Git identity lookup; retain complete-empty/unavailable and managed withdrawal semantics |
| Anchor/base and stateless root/project facts | Original anchor/stateless guard and registry/config producers | Extend their purpose-specific original publication; delegation rows do not prove anchor/base classification |
| Guard modes, kill switches, grants and expiry | Live environment and original switch/allow-edits files | Read/revalidate live applicable controls and current clock; never cache a permission or expiry result |
| Disposition, last note, exact worktree and current head | Tracking/status/binding owners and existing bounded current-row/session projections | Read a small original-owner file view, not tracking YAML/history, home-wide discovery or a SQLite/Python reader |
| Reminder count/window/cooldown | Existing nudge accounting and status-write/finalize reset boundaries | Keep one advisory owner/reset path; no canonical tracking writes or cached acknowledgement/permission |
| Late guidance result/revision | Existing correlated watch/request/publication owners | Read/publish their bounded derived observation; retain partial/failure/possible-change and model-admission uncertainty |
| Mutation invalidation | Original status segment/current-row producer | Notify the original owner without waiting for recomputation; a missed notification does not establish fresh status |
| PR-close author/state/current identity | Original PR/provider execution owner | Requires event-bound observation/recheck, not background PR-state/login caching; preserve the existing guard until a reviewed equivalent consumer is qualified |

The guard map is a managed observation, not a finite dependency fingerprint
for arbitrary unmanaged edits. Do not claim the canonical acquisition frontier
closed merely because the published JSON is complete and bounded.

Startup/catalog writers and source-bound SessionEnd need their own existing
registration, guidance and closure owners. This finite slice inventories those
dependencies but does not transfer their implementation or remove lifecycle
work. Other plugins' bootstrap/provider/retirement entries remain independently
owned; ordinary-hook changes do not silently replace their installers.

## Proposed functional flow

These are agent-recommended implementation choices, not new effect grants.

1. **Prepare facts through existing owners.** Compose original managed admission,
   guard-map and bounded current-row publishers. Use their resolved state homes
   and mutation/preparation boundaries. Publish only the fields the current hook
   needs, with exact caller/worktree incarnation, selected source/installation,
   producer generation/revision and completeness. No raw tool payloads.
2. **Read and evaluate in shell.** Process the current bounded event once.
   Select one coherent fact view and perform event-local checks plus live
   controls. The real entry must not block on Python policy, CLI discovery,
   synchronous daemon response or a fallback interpreter. Missing advisory
   data remains a truthful no-op/degraded observation.
3. **Notify without a data dependency.** Where an existing owner accepts a
   supported bounded notification, send mutation/demand information without
   awaiting recomputation. Any operation with uncertain acceptance retains
   its original correlation and is not blindly replayed.
4. **Optionally nudge the original boot owner.** Qualify its selected-generation
   launch/notification contract before using it. The owner coalesces demand,
   applies original cutover/identity/ownership admission and uses its existing
   launcher. Do not create a detached helper per event. If no supported nudge
   exists, skip it; missing optional recovery cannot become a tool-call gate.
5. **Let later events see a publication.** Current evaluation never waits for
   startup or facts and is never replayed. Later events read whichever coherent
   eligible view the owner actually published. Starting, present, unavailable
   and unknown remain different; none establishes this event's success.

Read bytes, object depth, target/path counts, notifications and response time
must have explicit finite bounds before implementation. Preserve response and
owned-resource cleanup margin below actual host deadlines. The failed
0.5-second synchronous-helper measurements remain historical failed evidence;
they are not a selected async lifecycle budget or permission to widen any
caller/test/parent bound. Whole-event and composed throughput gates remain.

## Publication, failure and compatibility

Use the existing producer's file home and atomic replacement/withdrawal
primitives, including Windows sharing and safe regular-file/path handling.
Do not add a coequal tracking store, copy a growing record, mix independent
generations or make publication/readers enumerate the fleet on every event.
Refresh only through existing managed changes/preparation and explicit
out-of-band refresh; no TTL-based topology polling.

Available applicable facts can establish a confirmed violation. Missing,
malformed, wrong-scope, stale or partial data is not complete-empty or permission.
Retain the established unavailable-evaluation fail-open behavior and reachable
inspection/recovery. Any installation-required exception remains positively
scoped and actionable, not all tool calls. A description of a head/PID does not
prove process liveness or authorize takeover.

Publish optional support from the selected producer generation before new
consumers emit new notification/launch syntax. New/new uses the qualified file
contract; old/new retains supported prior semantics; new/old or supported
rollback uses a documented shell-safe baseline or reports the actual unavailable
boundary. Do not silently fall back to blocking Python or claim that a legacy
provider supplies missing mandatory facts. Keep one active event implementation,
original command output composition and no uncertain effect replay.

The POSIX entry needs a proven bounded decoder for actual nested/escaped event
JSON. PowerShell's in-process JSON/.NET path is not POSIX qualification. Do not
replace a real parser with regex/`eval`, assume an installed `jq`, provision a
decoder in a hook, or retain Python as an undocumented cold path.

PR-close checks need an original-owner operation-bound observation and retry
contract. A generic cached PR author/open-state or login is insufficient.
Resolve this boundary before replacing its existing guard; a scoped first-call
intervention, if proposed, must be reviewed and preserve independent permission
and event-time rechecks. No broad acknowledgement grants permission.

## Implementation plan

### Prepared Windows membership prerequisite

The existing PowerShell admission entry can consume the original owner's
positive exact-session association without launching its interpreter again.
Reuse requires current session/workspace/legacy-registry/platform scope,
matching original project/repository source stamps, the original Git marker
witness and unchanged recorded filesystem inputs. Bounds are 1 MiB, 256
witnesses, 4,096 characters per path and 128 path components. The eligible
subset is native legacy Windows with canonical local, unlinked paths.
Other scopes or unavailable evidence retain the original admission owner.

This incremental consumer preserves cold and installation-cell behavior;
it does not advertise complete prepared admission or satisfy the no-Python
cold-path gate. The original source publication remains unchanged. Real
registered-entry controls must compose that publication, not replace
admission with a true-returning stub, and must trap interpreter lookup while
retaining changed-input refusal. See the
[current reader boundary](../../../plugins/agent-worktrees/docs/worktree-observations.md#prepared-windows-membership-reuse).

### Prepared Windows installation-cell prerequisite

The same original producer can expose the already-validated selected cell's
minimum path observation within its existing association, with the original
receipt/root/manifest witnesses. Content digests come from the original
SDK/payload reads that validated those inputs, without observation replay;
the shell's three content reads are each capped at 1 MiB plus one overflow
byte. Restored timestamps must not hide changed receipt or manifest bytes.
The native reader must require exact current
context/payload and complete canonical source/layout checks, preserving all
existing ownership witnesses and bounds. It must not clone the receipt parser,
infer support from an old association or replace the original cold resolver.
Old-reader/new-producer behavior remains unchanged; new-reader/old-producer
and rollback retain the original owner path until this additive view is present.
Real owner-stamped linked-worktree fixtures and interpreter traps must prove
both current reuse and changed/incomplete-context refusal before acceptance.
The observation must retain all seven declared lexical/resolved root pairs
and explicit original validated source-kind eligibility. GitHub, Git URL and
opaque identities can qualify; directory and unknown source kinds retain
original admission rather than duplicating namespace source normalization.
Native regressions must retarget both the directory source and an ancestor,
not only roots underneath the selected plugin.
The three receipt-declared canonical comparisons (`payload.root`, `pluginRoot`
and `namespaceReceipt`) also require original SDK string-read capture during
that same resolution. Singleton values must directly match validated canonical
results without another normalization/resolution. Aliased or incomplete capture
retains original admission; old views missing the complete declaration set
cannot qualify. Native controls must independently retarget all three aliases
and preserve valid original fallback within its existing deadline.
This prerequisite is not full cell post-tool migration or shell-only cold
reconstruction; those consumers and their remaining contracts stay open.

### Prepared Windows installation-cell post-tool migration

The next finite consumer composes that original prepared-cell association
with the existing registered read/native/batch/single-shell path. It reads
only the selected cell's passive discovery and original current-row views.
Discovery must match the marketplace identity from the already-validated
cell layout, exact receipt and plugin root before sending a notification
or using advisory facts. Do not parse the receipts again, reconstruct
membership, select a different endpoint or relax existing input witnesses.

Retain the original capabilities, process generation, receiver admission,
event/target normalization, live switches, once-per-event reminder/late
accounting, wire bounds and no-reply/no-retry semantics. Incompatible
membership retains original admission; a qualified warm cell with missing
or old discovery stays shell-only. Native controls must traverse the actual
registered entry and original publishers/receiver, trap warm interpreter
and synchronous calls, check read/native/batch/shell effects and distinguish
foreign discovery, stale receipts, missing rows, live controls and a
non-replying peer. This closes only a warm source consumer checkpoint;
cold/directory/POSIX/recovery, installed, composed and throughput gates remain.

### Prepared Windows mixed-batch post-tool migration

The same bounded prepared legacy/cell entry now admits complete batches of
original shell aliases, native writes and read tools. It reuses original strict
event normalization, native targets and single-shell classification behind
the separately advertised `mixed-hook-batch-mutation-v1` capability. The receiver
validates every call before effects; there is no supported-prefix invalidation.
Native-only batch and single-shell support cannot imply this new capability.
Read-only batches renew demand without mutation wake; shell mutations and
aggregate overflow remain conservative. Reminders and late guidance stay once
per outer event. See the [mixed-batch contract](../../../plugins/agent-worktrees/docs/worktree-observations.md#windows-mixed-batch-consumer).

Real registered Windows controls must compose original publishers and receiver
for both prepared modes, including targeted writes beside read-only shells,
conservative shell writes, missing/old/wrong-generation/closed discovery,
wire bounds and a non-replying peer. Malformed/unknown shapes retain the
original route, with no silent partial evaluation. The original notification
deadlines, no-reply/no-retry and refresh backstop are unchanged. This finite
checkpoint does not close cold/directory/POSIX, pre/start/end, optional boot,
installed composition or whole-hook throughput acceptance.

### First additive producer contract

The next finite consumer is the Windows read-event path (`view`, `rg`, `glob`).
These events need no mutation invalidation. It consumes original prepared
membership, the binding owner's guarded project/incarnation/workspace locator
and a version-2 advisory row; the canonical recorded head is separate from
the listing/resume target. The original reminder window/reset and bind cooldown
remain in their existing sidecars. Missing observations are a shell-safe
advisory no-op, not a blocking interpreter or resident recovery dependency.
Mutations, installation cells and POSIX retain their
previous paths pending their complete consumer checkpoints. This does not close
the whole-hook, cold admission, notification or throughput gates.
See the [consumer contract](../../../plugins/agent-worktrees/docs/worktree-observations.md#windows-read-event-consumer).

The Windows read-event consumer also evaluates the original late-guidance
watch/request/publication contract after prepared admission, independently of
row-fact availability. Pending/unknown owners retain the watch; correlated
terminal or definitely absent-worker results preserve the original advisory
wording. Shell and retained Python consumers share atomic snapshot-checked
one-time consumption, including replacement preservation. A notice suppresses
only disposition accounting, not independent binding advice. Native tests
exercise the registered entry and competing consumers in both Windows shells.
This does not qualify installed behavior, complete hook composition or throughput.

The next additive read-event checkpoint separates idle demand from advisory
evaluation. The existing IPC owner advertises `read-hook-demand-v1` and its own
process generation before a prepared Windows reader sends a notification.
That compact message renews only the original recent-tool idle grace; it
cannot classify mutations, run policy/advice, wake recomputation or start a
resident. The shell reads no reply and shares a 100 ms allocation/connect/write wait;
unsupported discovery skips it, and uncertain delivery never causes replay.
The [optional read-demand contract](../../../plugins/agent-worktrees/docs/worktree-observations.md#optional-read-demand)
defines wire bounds, source selection, live admission and upgrade/rollback.
Real registered-entry, non-replying-peer, original-receiver and idle-expiry
controls must prove that composition. Mutation invalidation, optional boot,
other scopes and whole-hook/throughput acceptance remain separate open gates.

The next finite consumer extends that same prepared Windows shell evaluation
to recognized single native writes. A separate `native-hook-mutation-v1`
capability on the original IPC owner admits a bounded send-only event, not an
advisory/policy request or another lifecycle. The original native target
extractor supplies every structured-edit and patch-move destination; unknown
targets and overflow preserve conservative invalidation. The receiver retains
current-generation, managed-session, publication, governance and drain
checks, while the shell never waits for a reply or retries uncertain delivery.
The existing sweep/TTL remains the delivery-loss backstop. Unsupported batch
and shell classification remain on their prior path, not silently declared complete.
See the [native-write consumer contract](../../../plugins/agent-worktrees/docs/worktree-observations.md#windows-native-write-consumer).

The next finite consumer accepts a complete bounded batch containing only
recognized native writes and the already qualified read tools. It reuses the
original `tool_events` normalization with explicit strict shape admission,
then collects original native targets before any invalidation. An additive
`native-hook-batch-mutation-v1` capability prevents an old single-write
receiver from being mistaken for batch support. Reminders and late guidance
remain once per outer event; malformed, unknown and mixed-shell batches keep
their prior path. The original process-generation, admission, governance,
wire/deadline and no-reply/no-replay bounds remain unchanged. See the
[native-batch consumer contract](../../../plugins/agent-worktrees/docs/worktree-observations.md#windows-native-batch-consumer).
This is not shell-command, cold/cell/POSIX, installed or whole-hook acceptance.

The existing `WorktreesRuntime` publishes a small advisory view only after its
current-roster store accepts a publication. The original project-state owner
resolves the home: `project_dir(project)/worktrees/<id>/hook-facts/`.
Only rows with sampled live-session membership/flags are eligible; inactive rows
do not create file homes. That sample is not a new process-liveness authority.
The filename is `<hook_facts_generation>.json`. The monitor's passive discovery
record separately advertises `hook_facts_version` and this publisher's process-local
generation; its existing `runtime_generation` is a build identity, not a process
identity. No hook enumerates that directory or picks the newest file.
An old process only owns its own
generation's files, including during withdrawal and shutdown.

Version 1 contains exact project/worktree/incarnation/workspace scope, selected
runtime root, build and publisher generations, the existing installation-context fields,
projection generation and row semantic revision. Its only row facts are
disposition status, last disposition-note timestamp, asserted listing session
and yielded-head flag. Record provenance must be current. The whole view remains
**advisory and partial**: it is neither managed-session admission nor permission,
head custody, daemon liveness, a complete record or a lifecycle result.
Missing fields stay null; no unknown head becomes an asserted binding.

Bounds are 16 KiB UTF-8 per view, 4 KiB per string, depth 4 and at most 4096
selected views per project publication. Producer paths use safe single
segments; the admitted project root may be a configured alias, but derived
children and output files must not be links/reparse points or hard-linked files.
Replacement is atomic in the same directory, with no wait/retry loop.
Timestamp-only observations and changes to unsampled row fields do not rewrite
an unchanged view. Its revision identifies the accepted row that last changed
the sampled view, not a claim to have observed the latest whole roster.
Publication failure, removed/unqualified rows, admission closure and shutdown
withdraw affected files. Failure of this optional projection does not invalidate
the existing roster or advertise a successful hook transition.

The corresponding reader selects one exact generation from passive original-owner
discovery and reads one bounded regular file. It validates the entire envelope
and exact current caller/installation/workspace scope before using any advisory
field. It never joins generations, treats missing as complete-empty, waits for a
file, refreshes via Python or infers current authority after a crash.
PowerShell's in-process JSON decoder is available; the POSIX decoder and
managed-admission file remain separate unresolved gates. This producer expansion
adds no hook registration, notification syntax, boot request or active selector.
The post-tool transition still requires those complete consumer contracts.

- [ ] Review this shell-facts/async amendment, preserving the original reviewed
  proposal and failed prototype as history, not implementation acceptance.
- [ ] Finalize bounded per-fact producer/reader interfaces and actual shell
  decoder/owner-notification capabilities before emitting new syntax.
- [ ] Extend original admission/current-row publications; reuse the existing
  guard map and retain its original managed freshness/withdrawal contract.
- [ ] Implement and prove the complete post-tool shell flow first: no-op,
  missing daemon, reminders/binding/late guidance and nonblocking invalidation.
- [ ] Implement pre-tool shell lookup/guards only after current control,
  target/parser, managed-admission and PR-observation parity is qualified.
- [ ] Add the optional coalesced boot nudge only through the original selected
  lifecycle owner, with actual caller-exit/custody and no-event-replay proof.
- [ ] Publish the implementation with exact-source review, required CI and
  applicable contract/documentation checks. Record source, release and installed
  acceptance separately; do not close #3761 or the full P8388 case.

## Paired validation

- [ ] Exercise the real registered shell entry with Python/CLI execution traps.
  Cold/no-op/missing-provider paths must not hide an interpreter fallback,
  readiness wait, request-time discovery or duplicate event evaluation.
- [ ] Prove producer -> atomic bounded file -> actual shell consumer, including
  complete-empty, unavailable, wrong source/caller/incarnation, partial,
  malformed, concurrent replace/withdraw and managed mutation/preparation.
- [ ] Preserve managed-session admission and ordinary unadmitted/recovery access.
  Prove live mode/switch/grant-expiry changes, multiple/literal targets, relevant
  first-call/retry cases and every confirmed guard outcome.
- [ ] Qualify post-tool count/reset/cooldown, current head, late publication,
  targeted invalidation and terminal no-op without history/fleet scans.
- [ ] Qualify the PR event-bound owner/retry and POSIX decoder contracts; report
  unqualified boundaries rather than weakening or silently dropping them.
- [ ] Cover old/new providers, supported rollback, missing declarations and
  unknown arguments before effects; no blocking Python compatibility fallback.
- [ ] Exercise original boot-owner coalescing, live/foreign/unknown/reused
  identity, cutover/fences, start pending publication and interrupted admission.
  No metadata deletion/reaping, cooldown-based takeover or uncertain replay.
- [ ] A delayed/hanging boot owner must not extend the current shell response.
  Prove actual resource custody and survival after caller-owned cleanup where
  intended, plus exact contained-test suppression/settlement.
- [ ] Run focused units, real shell/file consumers, fresh first-touch and native
  Windows/POSIX qualification, then exact-source review/required CI. Record
  skipped tiers with reasons and no installed/live/model/ACP inference.
- [ ] Measure matched individual/composed first-attempt/retry latency, process
  work, error/tails and throughput at exact source/serving identities. Timeout
  settings, import timings and fewer processes are not whole-flow acceptance.

## Completion and retained ownership

This slice completes only after its real consumer flow, exact-source checks,
review and source merge are verified. Preserve #918/#2619 scheduling,
#6748 publisher recovery, #7937 acknowledgements, #7946 publication,
#5664/singleton replacement and #6832 roster-client recovery separately.
It neither supersedes their open PRs nor acquires their files or resources.
Installed activation, live ordinary-session acceptance and composed performance
remain their explicit receiving gates; this proposal executes none of them.
