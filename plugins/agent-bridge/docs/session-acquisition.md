# Strict local session acquisition

Phase 6 of `efforts/active/agent-bridge-cli-mode-sessions` consolidates dispatch
session acquisition beneath bridge. `agent-bridge acquire --request-file -`
accepts version-1 or workspace-qualified version-2 JSON on stdin and calls
`POST /api/v1/acquisitions`.
HTTP protocol **34** gates current clients, including post-preparation
retirement/holder revalidation; older daemons refuse capability
instead of falling back to create. Legacy unbound/explicit-fresh `create`
semantics are unchanged. This slice supports **local** CLI and ACP; remote/
fleet acquisition remains outstanding.

## Request and result

The [workspace allocation and session acquisition contract](../../../docs/patterns/workspace-allocation-and-session-acquisition.md)
qualifies every allocation/acquisition by the selected execution project, not
the producing task lane. Bridge creation for a worktree-capable project
composes the ground layer's allocation with embodiment in that same workspace.
An existing workspace with authoritative sessionless history may receive its
first conversation through the ordinary admission path; no takeover is implied.
The optional standalone `create --creation-key` boundary below owns allocation
replay; the delivery-ID journal described here remains task acquisition authority.

## Standalone keyed creation

`agent-bridge create <agent> --creation-key <opaque-key>` composes the ground
owner's `create --creation-key` allocation with ACP embodiment. Protocol **34**
and `POST /api/v1/sessions/workspace-creations` are required; an older daemon
cannot silently ignore the key through the legacy sessions endpoint. Unkeyed
creation keeps its existing fresh-create behavior. Keyed creation currently
supports local worktree-backed agents only; SSH, provider and CLI-mode venues
refuse explicitly rather than dropping the key or changing execution mode.

The ground request uses `--no-owner --interface acp --origin delegate`, plus
an explicitly supplied caller owner reference. A direct ground caller using the
same key must supply the same effective allocation facts. Alternatively,
`--worktree-id <id>` embodies an existing allocation without running create.
That first-conversation path requires an owner-returned creation nonce, an
explicit zero-session history, an authoritative empty head, and no represented,
owned or uncertain execution leg. Missing legacy proof and foreign/live holders
refuse; this path is not takeover or dispatch fresh-recovery authorization.
That complete holder check runs again after awaited owner revalidation, before
native-start registration. The existing SQLite launch gate also fences ordinary
bridge and live-session registrations during that await.

The installation's SQLite embodiment journal binds the key, effective target
and execution options before allocation effects, and reserves its original
session identity before native start. Exact replay retains project, worktree,
path, native conversation and optional first-turn admission. Pending allocation
replays only the ground owner's same key; ambiguous native start requires
reconciliation of the recorded session, never another `session/new`.
Changed facts conflict. Removed/retired workspace or native results refuse,
not fresh-create. Standalone embodiment grants no task/claim authority.
An absent workspace observation does not prove disposal: a resident roster can
precede the ground allocation. Missing rows and advisory, stale or malformed
projection observations explicitly refuse while retaining the same allocation
and creation key for reconciliation. They never permanently terminalize it or
authorize another create. A positively observed terminal workspace or retired
native result still refuses replay. Ordinary same-key replay reconciles a
previously terminalized pre-launch observation only when the ground owner now
returns the same active, sessionless allocation and the original journal has
no native, turn, persisted session or launch-gate history. It retains the
original allocation and reserved session identity, logs the reconciliation,
and runs the normal pre-launch checks again. A terminal workspace, changed
allocation, existing conversation or uncertain holder still refuses; no
separate operator recovery ceremony or replacement allocation is required.
The qualified caller worktree, not its current session fragment, qualifies
ownership facts, matching ground allocation across a caller restart.

An optional creation prompt is admitted with a stable delivery ID through the
existing ACP durable turn skeleton. A lost response or concurrent replay returns
that original turn instead of submitting another one, including while it runs.
Completed replay is read-only with respect to native binding and turn
admission; missing completed-turn evidence refuses instead of resubmitting.
It settles only its own stranded launch gate if interruption occurred after
the completed receipt was persisted; another request's gate is never released.
A typed drain refusal at the session manager's pre-effect guard restores the
retained allocation and releases its own gate, allowing same-key continuation
after cutover. Unknown native-start exceptions remain pending instead.
An established cold native conversation is strictly resumed only to admit
its still-unadmitted original prompt, with recreation disabled.
The journal composes the existing per-worktree async lock and OS executor lease;
it introduces no resident service or independent session host.

## Task acquisition request and result

`acquisition_contract.AcquisitionRequest` version 1 carries explicit `cli` or
`acp` mode, target, task ID and generation, worktree ID, allocation provenance
(`created`, `reused`, or `targeted`), tri-state conversation retirement,
`automatic`/`fresh`/`continue` intent, carried bridge and native conversation
identities, execution options, prompt, delivery ID, project, driver,
verification timeout and the current spawn reservation key. Additive unknown
fields are ignored by request and result readers, following the repository's
wire-compatibility contract; they grant no acquisition authority. Versions,
modes, malformed required facts, and conflicting strict-continuation/retirement
intent still refuse validation. Admission independently verifies the known
task/reservation/native bindings. A new field that changes safety or continuity
semantics requires a new negotiated protocol, not an ignorable extension.
No mode defaults to CLI.

Version 2 additionally requires a nonempty execution project and worktree path.
Admission compares both to dispatch's persisted allocation provenance, then
checks the exact worktree owner's `repo` and `path` before native-head reads.
Conflicting or missing owner facts refuse without searching another registry.
Protocol 34 is required for version 2; the route continues serving version-1
requests from protocol-30/31/32 clients with their existing safety checks.
Version-1 request digests remain byte-compatible with persisted history.

The default local CLI worker target `task-worker` is a stable task-affinity
label, not an ACP registry prerequisite. Its execution target comes from the
owner-verified local worktree, and the worktree CLI retains control of configured
launch defaults. Explicit named CLI targets still resolve normally and must be
local; ACP targets, including `task-worker`, still require a registered agent.
Missing explicit/ACP targets return `target_unavailable` and a prepared refusal
receipt rather than an HTTP 500 with a stranded pending attempt. No dummy agent,
pool activation, changed affinity label or fresh-conversation authorization is
needed for the default CLI body.

`AcquisitionResult` requires the represented or owned session handle **and**
the exact native conversation ID, requested bindings, delivery ID, and
`created`/`reused`/`resumed` outcome. A mux handle, path, successful process
launch, or reused worktree is not a substitute for native identity proof.
Output validation rejects changed bindings and recreated strict continuation.

## Selection policy

`acquisition_policy.select_acquisition` is side-effect free. Its input is a
provider-verified, complete holder snapshot and ground-layer native head.
The adapter, not the consumer, must establish task affinity and native identity.
Unknown authority, unknown liveness, a pending transition, multiple live
holders, busy/foreign ownership, option mismatches, and missing retirement
evidence refuse. A fresh targeted directory inherits no transcript.

Creation is selected only for a verified new allocation or explicit fresh/
retired intent with no live or unknown holder. Fresh intent authorizes neither
takeover nor stopping a process. Retained automatic acquisition requires
retirement exactly false, one exact task/mode-bound conversation,
and a matching native head. Stopped reuse selects resume; live reuse selects
delivery to that same conversation.

The bridge reads admission facts through the existing `dispatch:` namespace
provider: current task generation/status/hold, latest active reservation,
worktree ownership and retirement. Generation is the **current admission
fence**, not immutable conversation lineage: claim/adoption increments it.

Worktree-owner resolution does not require an interactive shell's `PATH`.
A legacy bridge uses its installed canonical versioned-runtime resolver to
select a completed worktree-owner interpreter. Its execution trampoline rechecks
legacy ownership and resolves the completed slot immediately before launch,
retaining the legacy command
compatibility path when no runtime resolves. The Windows trampoline explicitly
forwards stdin, stdout and stderr to the headless owner process, preserving
captured JSON and diagnostics across interpreter boundaries. A native service rooted in an
installation cell derives its own `install.json` context when the launcher
did not supply one; explicit and derived contexts use the existing validated
same-cell peer launcher and never fall back to a global command. Retired legacy
ownership refuses rather than crossing installation cells.
Owner availability is checked before marking dispatch acquisition. A missing
owner reports the existing `worktrees_unavailable` refusal without certifying
`effect: not_started`: neither owner absence nor an empty local journal proves
that an earlier launch did not happen. Historical and unknown effects retain
their existing reconciliation requirement. No acquisition wire capability or
protocol change is introduced by installed-owner resolution.

Worktree identity preflight reads the exact current tracking record with
`list --json --cache-only --worktree-id <id>`, avoiding global transcript scans
and cached full-list results. Only tracking identity, path and terminal status
are used from this row; cached display/session enrichment grants no authority.
The separate `head-session` read still verifies current native-head and
handoff state. Missing, duplicate, mismatched or terminal records refuse, as
do unknown head authority and pending transitions.

For an admitted new delivery, the bridge records the complete request in its
`prepared` journal stage before the worktree-owner preflight. A preflight
refusal therefore leaves a request-digest-bound `refused/prepared` receipt,
including when owner capability is unavailable. Existing pending operations
retain the normal reconciliation path; absence never settles an old attempt.

An automatic reused allocation can create its first native conversation only
when the dispatch provider supplies the complete consecutive same-task
reservation history (bounded to 256 attempts), beginning with `created`
allocation, and every predecessor has a matching positive
`refused/prepared` bridge receipt. Worktree, task, mode, target, project,
retirement and request digests must agree; a predecessor generation may be
older than the current admitted generation, never newer. No predecessor may
have a session handle, a spawned/unknown outcome, a launch-stage receipt,
or a binding; current native head, holder, continuation intent and task
attachments still block first-conversation creation. This preserves allocation
provenance without retiring or discarding a transcript.

Missing/truncated provider history, gaps, missing original receipts, database
replacement and legacy attempts that failed before journaling do not authorize
creation. `acquisition-inspect "<reservation-key>:acquire"` reads individual
stages; `acquisition_not_found` is unknown history, not a no-launch receipt.
No blanket fresh or rearm operation repairs missing proof.

An explicitly authorized operator may instead choose a new conversation via
dispatch's control-only `reservations authorize-fresh` operation. This is
audited fresh-generation/retirement authority, not retroactive proof of
historical absence. It preserves old records and transcripts and authorizes no
takeover. Bridge independently refuses live/unknown holder and head evidence,
then revalidates the exact task/reservation/generation/retirement fence after
target preparation and before launch. Revocation or a changed holder prevents
creation. See the dispatch acquisition guide for the exact permission,
concluded-reservation, liveness-attestation and deployment requirements.
Retirement revalidation requires protocol 31. A protocol-31 daemon continues serving
protocol-30 acquisition requests with its stronger holder/retirement checks.
A bridge binding from an older generation of the same task remains resumable
when current owner facts authorize it. A future/stale admission generation
refuses. Historical expired registrations that are not the current native head
are not live holders; a current head with missing identity/liveness proof refuses.
Cross-task exclusive-key carry alone does not prove task transcript affinity.

A first-attempt targeted task with no owner session, attachments or carried
conversation receives fresh intent from the task owner's authoritative facts.
Directory existence alone never supplies that intent. Creation excludes all
known retired/head native identities even when the consumer dropped its handle.
Request `None` for execution options means **no override**, not “erase configured
defaults”; explicit incompatible overrides refuse. ACP retains registry/bound
charter and preference setup. CLI override requests currently refuse explicitly
because their effective-state proof is not yet implemented.

## Durable retry fence

A fresh-generation authorization is not an instruction to create a second
conversation on every reservation retry. If launch succeeded but delivery did
not begin, reconciliation preserves a validated native receipt before marking
the old attempt `superseded_before_delivery`. Automatic retries may reuse that
specific non-retired, same-task, same-generation conversation only when its
target/mode and native head agree with that receipt. Foreign or unbound
holders, older generations, a newly issued freshness intent, and retired
native IDs still refuse. Older superseded `launching` records without a result
remain recoverable only when their positive creation authorization, exact
native task/generation binding, and recorded launch caller (ACP) or driver (CLI)
all agree. Missing records never establish that proof, and historical stages
are not rewritten into fabricated result receipts.
Durable turn admission consumes that recovery chain
for other delivery IDs, even before completion is journaled. A completed receipt
retains immutable proof only for replay of its own delivery ID; later
reservations cannot use it to deliver the same fresh-generation task again.
CLI recovery from `launching` revalidates the persisted native identity against
its live registration and current head, without requiring the reused process
to have acquired the retry's driver label. Already-admitted turns settle
without a duplicate delivery or a new process.
Retirement recovery requires protocol 32. Current acquisition clients require
protocol 34 and refuse an older daemon before sending a request.

`acquisition_journal.AcquisitionJournal` uses the bridge database's existing
SQLite connection/write machinery, with a separate internal attempt table.
One delivery ID binds the complete request digest. A partial unique index
permits only one pending acquisition per target/worktree. Completed retries
return the original validated result without authorizing another delivery.
Rebinding a delivery ID is a typed refusal.

Only an exact digest-bound `refused/prepared` receipt for a deterministic
workspace/CLI-capability refusal can certify `effect=not_started`. A carried
native identity or earlier pending/launched/completed receipt prevents that
classification. Missing owner capability, busy holders, unknown effects and
launch-stage refusals remain unclassified and require reconciliation. A
refusal code by itself is never a no-launch attestation.

A process interruption leaves a durable pending attempt. It has **no automatic
expiration**: elapsed time does not prove whether launch or delivery happened.
Admission atomically commits the complete request and `prepared` recovery stage
with the delivery claim. Interrupted preflight can therefore be reconciled by
a newer authorized reservation without guessing its prior request.
An adapter may record a pre-effect refusal only when it has proven that no
effect occurred. Recovery stages record the selected outcome, retired identities,
native binding, and delivery admission. ACP turns carry a unique delivery ID;
CLI uses the existing guarded, idempotent durable inbox. A retry reconstructs
receipts from these facts without submitting a second turn. A newer authorized
same-task reservation settles an interrupted predecessor: preserve an already
launched native conversation, abandon an unadmitted stale prompt, or observe
its durable admission. An ambiguous launch/turn refuses rather than guesses.
FAILED status, absent client/native ID, or missing head/record do not establish
process absence: an independently owned Session Host may survive failed attach.
These launching states retain their ambiguity and gate until actual
identity-bound host/process cleanup or recovery evidence exists. An established
native conversation stopped by a bridge restart is strictly resumed before
its unadmitted prompt is delivered; recreation remains disabled.
Recovery rechecks current dispatch admission and worktree/native head after
strict resume and again after acquiring the turn-start lock, immediately before
persisting the turn. A hold, cancellation, generation or reservation change
while waiting refuses the stale prompt without advancing its delivery stage.
`GET /api/v1/acquisitions/{delivery_id}` reports recovery evidence read-only.
Its `delivery_admitted` flag is derived from the exact durable CLI inbox row or
ACP turn (delivery key, session and constructed prompt), not the pre-effect
`delivering` journal stage. Dispatch STARTED admission requires that proof;
a receipt bound before insertion does not suffice.
The CLI inbox's atomic admission statement also requires an idle/completed turn
and no pending non-control message. Competing enqueue/turn transitions refuse
as `session_busy`; an identical delivery-key retry returns its original row.
Unsupported CLI execution
overrides report `effect: not_started` before any journal, gate or launch, so
dispatch fails the capability mismatch instead of deferring it for reconciliation.
Terminal receipt retention and recovery of launches with no surviving native
proof remain outstanding; no timeout-based force-clear is provided.

The journal is **not a live-holder lock**. Acquisition composes it with the
existing worktree async lock, an existing cross-process OS lease primitive,
durable launch gates guarding owned/represented registration, ACP ownership,
and CLI-mode reservations. The OS lease prevents another live executor from
mistaking an active attempt for an interrupted one during daemon cutover.
Pending gates survive process loss for reconciliation. Ordinary ACP resume
respects the gate; failed strict resume never enters legacy create fallback.
Native prompt *completion* is distinct from durable admission.
The delivery includes the exact native `start --owner-session-id` binding,
not a generated launch-worker ID or ambient predecessor identity. The same
constructed prompt is used for durable admission and retry reconciliation.
Before delivery, the registered dispatch provider persists a start receipt after
independently checking bridge's authenticated durable acquisition inspection.
The receipt covers CLI worktree owners as well as explicit ACP workers, and
dispatch compares its exact native identity inside the start transaction.

## Graceful cutover

This adds no resident process or service-management path. The existing bridge
and dispatch installers activate immutable runtimes through their existing
active/passive routing and drain seams. Acquisition refuses new work while the
bridge is draining; started effects stay in the installation's durable database.
The per-worktree OS executor lease prevents a replacement frontend from
reconciling an acquisition still executing in the previous frontend. Native
hosts remain owned by their existing lifecycle providers, not this HTTP request.
Stopped established conversations recover strictly; failed attachment without
actual host-absence evidence retains its gate. No shutdown, forced takeover,
host kill or production rollout is introduced by acquisition.

CLI launch calls the worktree owner's unseeded
`embody --bridge-acquisition <native-id|fresh>`. Its capability preflight uses
`--dry-run`; the real launch rechecks native head/liveness under the existing
worktree lifecycle lock, refuses a live mux, and leaves pending worktree seeds
untouched. Bridge waits for exact native registration/head, then submits once
through the existing inbox. No new CLI PTY/session-host daemon is introduced.

## Compatibility and remaining wiring

Existing unbound/explicit-fresh `create` and legacy resume behavior are
unchanged except respecting an in-flight acquisition launch fence.
Unsupported modes, providers, or strict-continuation capability must refuse,
never switch mode or bypass bridge.
Pre-request protocol refusal is typed as `unsupported_acquisition_protocol`
with `effect=not_started`; it is not an ambiguous launch result.

Local one-shot (`create --spawn`), supervisor CLI/ACP factories, and interactive
embodiment now call this boundary. CLI handles remain native session/worktree
handles for CLI cleanup; ACP handles remain `local-body:<bridge-id>`, separately
returning the native conversation ID. Unknown launch outcomes defer rather than
claim absence or permit destructive cleanup. No CLI-to-ACP fallback remains in
one-shot acquisition.

Remaining implementation includes remote carrier/provider acquisition and
fleet callers, CLI execution-option proof, complete legacy identity adoption,
native process-exit/transcript integration and Windows validation. The current
tests exercise real request/response construction, SQLite admission/fencing,
and SessionManager start/resume, with native process/provider I/O simulated.
They are not proof of a deployed native transcript across process exit.
Worktree allocation/ledger, task leases,
evaluation, provider transport, and CLI-vs-ACP terminal cleanup keep their
existing owners. No additional CLI host/PTY daemon belongs in this boundary.
