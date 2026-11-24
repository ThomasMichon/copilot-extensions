# Target inventory and metadata hydration

Back to [local session acquisition](session-acquisition.md).

The optional `target_inventory: 1` health capability adds
`GET /api/v1/targets` and `BridgeClient.list_targets(refresh=False)`.
The version-1 response contains `targets` and independent `sources` observations.
It projects the union of active managed sessions, registered Session Hosts,
extension registrations and every Worktrees-listed workspace. The legacy
`/api/v1/sessions` managed-session response remains unchanged.

Each target has its actual `kind`, `handle` and source names. Managed targets
carry `session`; Host and extension targets carry `registration`; workspace
targets carry `owner`, `project` and the existing `worktree` view. A workspace
handle is not a fabricated managed session ID. A workspace without a native
head or managed row remains visible. Different owner/project/machine/path
observations are retained, not collapsed by a friendly name or native ID.
Consistent Host and managed observations of the same control record share one
entry without converting a live extension transport into an ACP controller.
Workspace linkage requires the managed controller's owner, project, exact path
and local/SSH transport to match; a bare shared handle never supplies that proof.

Listing starts no native process, sends no prompt and hydrates no metadata.
Workspace discovery uses the existing app-owned cache and cold-crawl behavior;
`refresh=true` joins its bounded refresh. Stale or incomplete workspace evidence
is retained verbatim. Missing or invalid Host inventory is explicitly unavailable,
not an empty source. Local Host liveness is observed; remote liveness remains
unverified until its owning transport qualifies it.

On first startup, the process that creates the Host directory writes an empty
index under the Host index lock unless another writer has already created it.
This lets the first `create <project>` proceed when the other sources are also
complete and no target matches. If the directory already exists, startup does
not replace a missing or invalid index. Listing targets never repairs it.

## Conversation inventory

The optional `bridge_inventory: 1` health capability adds
`GET /api/v1/bridges` and `BridgeClient.list_bridges(...)`.
The new `list` command selects this version-1 response with `bridges`, `sources`
and `complete`. It requires the caller's project, explicit `--project REPO`, or
explicit `--all-projects`. `--machine`, `--namespace`, `--status` and `--live`
filter the same projection. It does not ensure the Bridge service.

The projection includes session-backed dormant worktrees, managed conversations,
extension registrations and standalone Hosts observed by the existing inventory.
Empty worktrees remain potential targets, not conversations. Dormant anchors
stay out of this default view. Assignment, liveness and control remain separate:
a running workspace can have `control: unavailable`, and an unavailable source
does not become a complete empty result. Conflicting native identities make
control unavailable rather than selecting a head by arrival order.
Title, disposition and creator/controller fields use cached owner metadata.
Listing never reads each conversation's transcript or imports native history.
An observed workspace supplies its owning project, even when the native
registration names a member repository. Explicitly conflicting owners remain
separate. Missing liveness, hosting and transport evidence remains unknown.

Select remote Bridge hosts explicitly with repeatable `--host`:

```text
agent-bridge --project example --json list --host host-a --host host-b
agent-bridge live-sessions list --all-sources --host host-a --host host-b --json
```

The latter selects only observed live conversations across all projects,
including managed ACP conversations and standalone Hosts. Existing
`live-sessions list` output and its registration-only JSON array are unchanged.
`--all` and `--supervisor` retain their registration-only meaning and cannot
be combined with the new source-union selection.

Host selection is bounded to 16 entries, with at most four concurrent reads and
a 15-second deadline per host. The remote `bridge.inventory` carrier operation
requests only that host's local contribution; it never recursively enumerates
the fleet. Every result has a host-qualified `route`; repeated observations of
the same scoped conversation retain alternate `routes`. The response preserves
each selected host's source evidence. An old carrier, an old daemon, a timeout
or unavailable metadata sets `complete: false` without dropping other hosts'
positive observations. No host names are inferred from personal conventions.

This is an additive discovery contract, not selector or lifecycle migration.
Existing `sessions`, `targets` and registration APIs retain their shapes and
effects. Listing clients require the executing daemon's `bridge_inventory: 1`
capability. Unsupported new reads refuse explicitly; rollback leaves existing
commands available. A supported carrier that lacks `bridge.inventory` reports
an unsupported operation for that host. There is no fallback to process
launch, receiver enrollment, service recovery or message submission.

### Exact conversation inspection

`show ID` and `BridgeClient.resolve_bridge(ID, ...)` select a single projected
conversation through the existing `bridge_inventory: 2` read. They accept an
exact workspace handle, native session ID, registration ID or current/retained
managed ID exposed on that row. They do not use substring, profile or
potential-placement matching. Project/CWD scope and explicit `--all-projects`,
`--machine`, `--namespace` and repeatable `--host` selectors have the same meaning
as `list`.

Fleet reconciliation retains `identity_aliases` for managed and registration
IDs whose observed native session matches the selected head. Routes retained
from an older head are not identity aliases. When the head itself is
ambiguous, aliases are retained only as matching evidence for a 409 refusal;
they never select either conflicting conversation.

The version-1 JSON response contains `bridge`, `sources` and `complete`.
Multiple matching conversations or conflicting observed native identities
refuse with `ambiguous_bridge_selector` (409). A missing match is
`bridge_not_found` (404) only for complete evidence; otherwise it is
`bridge_inventory_incomplete` (503). A positively observed single conversation
retains incomplete source evidence, unknown liveness or unavailable control.
This is inspection, not authority to execute its cached route. Effectful callers
must still use the owning operation's current identity and admission checks.

Exact inspection requires `bridge_inventory: 2` in the executing provider's
existing handshake. Version 1 remains sufficient for `list`, but cannot
certify alias-preserving inspection. The JSON inventory envelope remains
version 1 and adds `identity_version: 2`. A selected remote host without that
identity contract retains its positive rows, but makes discovery incomplete,
with `identity_complete: false` in its source evidence. A missing alias then
returns 503, not a false 404.

No new RPC is introduced. An older executing provider refuses `show` before
the inventory read; legacy commands remain available on rollback.
`resume`, `stop` and `end` keep their existing selectors.
Ordinary `send` also retains its existing default; opt-in exact sends are described below.
`read` and `wait` also keep their existing defaults; explicit adoption is
described below. No previous form is deprecated or removed.

### Exact local conversation observation

`read ID --bridge` and `wait ID --bridge` use the same exact selector as `show`,
with the same project/CWD scope, `--all-projects`, `--machine` and `--namespace`.
Without `--host`, they require `bridge_inventory: 2` and
`result_native_identity: 1` from the executing daemon and read its local
inventory only. They do not consume cached fleet routes.
A Bridge-managed ACP conversation whose worker is remote can still be observed
through its local owning daemon.

Add one `--host HOST` to select another configured Bridge host explicitly.
Selection reads only that host's inventory and pins its host-qualified route,
result-owner ID and native ID. The local daemon must advertise
`remote_native_result: 1`; the remote owner must support exact native results.
Each snapshot negotiates support again through the existing authenticated HTTP
and carrier adapters below. Missing support refuses explicitly, with no local
fallback or following a different host's merged row. Represented waits recheck
the exact registration on the selected host after each snapshot, not the
observer's local registry.
An empty explicit host refuses before client selection. If a remote `read`
exhausts its finite result deadline, it reports `bridge_observation_timeout`
(504), not a partial result or local fallback.

This is the explicit-adoption step, not a change to ordinary `read`/`wait`.
Their existing event-cursor, historical-session, workspace-following and attention
forms remain available without `--bridge`. Default migration is not complete:
archive lookup and represented attention still need
their own consumer contracts. Unsupported providers refuse the new mode before
reading inventory; there is no fallback to the legacy selector.

The selected row must expose a current managed ID, registered ID or matching
retained managed ID. Current managed observation takes precedence when both
owners represent the same native conversation. A metadata-only conversation
refuses with `bridge_observation_unavailable` (409), rather than hydrate a
control record, load native history or resume the conversation. Selection errors
retain the 404/409/503 distinctions above.

`read --bridge` returns one bounded result snapshot for either hosting mode.
JSON retains the result-snapshot schema and reports the available fidelity.
`--tail` sets the item bound; `--max-items` and `--max-text-chars` keep the existing
snapshot limits. The read is always cursor-neutral and finite, including with
`--no-follow`. ACP event ranges and message receipts are not snapshot positions:
`--range`, `--event`, `--since` and `--message-id` refuse in this mode.

`wait --bridge` polls bounded snapshots of that exact conversation until idle,
terminal state, identity change or command timeout. It never follows a successor.
JSON contains `settled`, `reason` and the final `snapshot`; `settled: true`
means the observation stopped on a reported state, not that a prompt was
delivered or consumed. A timeout reports `settled: false`. Represented waits
also inspect the exact registration after each snapshot, within the same
deadline, so expiry or takeover during the result read is not reported as idle.
An explicitly running current turn also takes precedence over an earlier idle snapshot.
Attention/position options refuse in this
mode rather than silently weaken the existing owned attention contract.
The command deadline starts before client initialization and selection, including
the initial health check. It bounds capability checks,
HTTP reads and retry delays. An expired deadline starts no new poll. A timeout
keeps only the last snapshot observed within the budget, or `null` if none.
Finite repository/provider lookups share that budget and retain their existing
owned-process cleanup. Cleanup can finish after the observation deadline.
HTTP response reads reduce the socket timeout before each raw read, including
headers and error bodies; a slowly streaming response does not renew the budget.
A timeout selected by the command deadline remains observation expiry even if
the socket backend returns just before the monotonic cutoff. An independently
shorter socket timeout retains its own error. HTTP error-body fallback does not
replace a deadline exception with the HTTP status.
Recoverable `wedged` state does not settle a wait; observation continues until
recovery, a terminal state, identity change or timeout.

Both operations connect without ensuring a daemon, use the existing result
owners, and change no delivery cursor, workspace, registration, assignment or
conversation. Result identity adds the optional `native_session_id` field;
legacy callers can ignore it. The new mode pins both the selected result-owner
ID and native ID. A result for a different snapshot or native ID refuses with
`bridge_observation_identity_changed` (409). Errors do not cause profile
fallback, metadata hydration, replacement or replay.
The same refusal applies if native recreation changes identity during snapshot
construction. An older provider without `result_native_identity: 1` refuses the
new mode before reading results; legacy result reads remain available.

Owned exact reads select the additive `native_only=true` option on the existing
result API. Completed text is available only when its original retained turn
record identifies the selected native conversation. Missing or different origin
returns `unsupported_for_target`, not an answer relabelled from an earlier native
conversation. Managed event history, active-work, pending-input and attention
projections have no native content-origin contract, so exact owned observations
omit them and report reduced fidelity. This does not enroll a producer or require
installation custody to read state. A legacy result read without `native_only`
keeps its existing history and fidelity; no stored turn or event is changed.
Represented exact reads also request `native_only=true`. A represented alias can
combine event histories from different native IDs. Such a read refuses with 409
because the combined history has no per-event native content-origin contract,
even when the alias existed before selection. Legacy represented reads keep
their combined history. Exact reads recheck registration and aliases after
projection so an alias created during projection also refuses with 409.
This adds only indexed registration/alias reads to exact represented snapshots;
legacy snapshots do not perform the extra recheck.

The [contained source runner](../../../TESTING.md) can select
`tests/test_bridge_inventory.py`, `tests/test_bridge_listing_cli.py` and
`tests/test_bridge_fleet_inventory.py` with `--allow-explicit-tiers`.
`tests/test_bridge_observation_cli.py` qualifies explicit local and single-host
read/wait selection, bounded output, registration rechecks, refusal and
unchanged legacy-option boundaries.
`tests/test_native_result_snapshot.py` qualifies matching, different and
unconfirmed original content origins without changing legacy reads.
The packet uses synthetic five-host observations and private registry fixtures.
It exercises the actual CLI/client/API serialization and unsupported-capability
refusal without native execution. It does not certify installed fleet adoption.

### Remote native-result provider foundation

The existing authenticated SSH carrier accepts the additive read-only
`bridge.result_snapshot` operation at operation version 1. This expands the
provider before migrating any CLI or HTTP consumer. Existing operations,
versions and defaults are unchanged; no old form is deprecated.

The request supplies the exact `session_id`, its expected `native_session_id`
and a Boolean `represented` owner choice. Optional `position` is the existing
opaque result position, not an event-delivery cursor. Bounds use the native
result limits: 1-100 items (default 20) and 256-20000 text characters (default
6000), with positions limited to 2048 characters. Invalid requests refuse
before acquiring a local client.

The receiver calls only that owner's existing native-only result method. The
serving HTTP generation must advertise `result_native_identity: 1`; package
version is not evidence of support. Both receiving and requesting adapters
validate the result schema, exact snapshot/native identity and requested
bounds. Identity changes refuse with 409; an invalid peer result refuses with
502. A result can report a successor, but the operation never follows it.

Each observation has a 15-second deadline, including carrier acquisition at
the requesting service and HTTP negotiation/read at the receiver. It starts
no Bridge daemon or conversation, enrolls no receiver, writes no delivery
cursor and does not send, resume or expand retained result details. The
existing carrier connection owner still controls connection acquisition and
reconnection.

An older carrier refuses this operation with 501 `unsupported_operation`;
an older serving HTTP generation returns its existing 426 capability refusal.
Neither outcome triggers a history fallback, effectful retry or automatic
upgrade. Ordinary read/wait callers continue using their prior contracts on
provider rollback. `tests/test_remote_result_snapshot.py` qualifies this
foundation; installed remote observation and CLI adoption are not claimed.

### Exact remote HTTP result reads

The carrier provider above is available from Bridge `0.9.540-dev1`.
The additive `remote_native_result: 1` HTTP capability exposes
`GET /api/v1/remote/{host}/sessions/{session_id}/result` and
`BridgeClient.get_remote_result_snapshot(...)`. The caller supplies the
exact host, result-owner ID, expected `native_session_id` and explicit
`represented` Boolean. Position and item/text bounds retain the carrier's
meaning. This is a result read, not inventory selection or permission to
execute a cached route.

The client checks the running HTTP generation's capability on every attempt
before requesting the new endpoint. Health negotiation and result reads share
one 15-second deadline, reduced by any enclosing observation deadline.
The existing service owns the carrier read; both carrier adapters and the
HTTP client validate the result's schema, exact identity and content bounds.
Older HTTP peers refuse with 426 before receiving the new request. An older
carrier's 501 and identity/bounds failures retain their original meaning,
without history fallback, service recovery or message effects.

This expands the HTTP provider and offers an explicitly selected client
method. CLI read/wait defaults are
unchanged. No old endpoint, parameter or response is deprecated or removed.
The real HTTP/carrier/native-result tests in `test_remote_result_snapshot.py`
check both owning modes and unchanged database state. They do not prove
installed remote adoption or model-facing message consumption.

### Exact remote native prompt carrier foundation

The existing authenticated carrier accepts the additive `session.native_prompt`
operation at carrier operation version 2. Its explicit service method is
`submit_native_prompt(host, session_id=..., native_session_id=...,
invocation_key=..., prompt=..., caller_id=...)`. This carrier foundation
does not change ordinary CLI host selection or defaults. The separately
negotiated HTTP expansion is described below.

The request requires an exact managed result-owner ID, expected native ID and
original nonblank invocation key. Prompt and caller bounds are validated before
client acquisition; queue, resume and creation controls are not accepted.
After carrier acquisition, the caller uses its public `encode_request` contract
to validate the complete escaped JSON envelope against that carrier's negotiated
outbound limit. Preflight and actual submission use the same correlation ID and
payload. Oversized ASCII, Unicode or escaped content refuses with 413 before
queueing, retaining the original native binding. Character counts alone do not
define the frame budget. A local carrier without this encoding contract refuses
with 426; it does not guess the peer's limit. The actual request owner rechecks
encoding before queueing if transport state changes.
The receiver reuses the existing keyed native admission owner with `no_resume`
and exact-owner checks. A native alias for another managed record refuses before
the native POST. Existing local callers retain their supported alias behavior.

Admission returns the existing version-1 native send receipt only when the
managed owner, native ID, original key, accepted admission, nonqueued state and
integer turn index match. This proves admission, not prompt completion or
model-facing consumption. An explicit same-key recovery returns the original
turn; a changed prompt conflicts rather than creates another turn.

The service makes one carrier request, without the generic remote request
owner's reconnect/replay loop. Its finite deadline includes lease acquisition
and request observation; the receiver separately bounds its native HTTP
negotiation/admission observation to 20 seconds. Lease cleanup can finish after
the caller deadline. A lost response, disconnect, timeout, unclassified failure
or mismatched receipt reports `native_prompt_admission_unknown`, retaining the
original identity and key. It neither cancels native work nor permits a fresh-key
resend. Known native refusals retain their typed reason under
`native_prompt_refused`; they are not durable non-admission settlements.
Malformed reply envelopes and lease-cleanup failures after submission also
retain unknown admission rather than trigger replay.
Typed native error replies retain their meaning only when managed owner,
native ID and original key all match. Missing or different bindings become
unknown admission with the caller's original binding. Prior-carrier protocol
refusals remain explicit pre-effect outcomes.

Prior carriers refuse the unknown operation with 501 before native client
acquisition; version 1 refuses this mutation with 426. Prior native providers
without keyed no-resume admission refuse before the native POST. No old
operation, syntax or response is retired. Consumers must wait for this carrier
expansion's qualification and release, then negotiate their own serving-runtime
support. The HTTP expansion below follows the released carrier provider.

`tests/test_remote_native_prompt.py` exercises the service/carrier/native
client/API seam, one-turn same-key recovery and conflict, post-commit response
loss, exact-owner refusal, prior peers, deadlines and one-attempt cleanup.
The existing native admission controls remain selected in the same path-gated
Linux/Windows jobs with their original budgets. Synthetic fixture validation
does not authorize installed rollout, receiver repair or retained-message replay.

### Exact remote HTTP native prompt admission

The additive `remote_native_prompt: 1` HTTP capability exposes
`POST /api/v1/remote/{host}/sessions/{session_id}/native-prompt` and
`BridgeClient.submit_remote_native_prompt(...)`. The caller supplies the
exact host, managed owner ID, expected `native_session_id`, original
`invocation_key`, `prompt` and optional `caller_id`. The body accepts no
queue, resume or creation controls. The service reuses the released carrier
provider above; remote session creation is not a fallback.

The client negotiates the serving HTTP generation's capability before the
POST. Prior HTTP providers refuse with 426 without receiving that POST.
Negotiation and admission observation share a 20-second deadline, reduced by
any enclosing deadline. The selected endpoint stays fixed across the check
and POST. The client makes one POST, with both HTTP and outer protocol
retries disabled. A drain response does not trigger endpoint replacement or
resubmission. Preflight can retry an unavailable health read within the same
deadline; it cannot replay an admission attempt.

HTTP replies add the requested `host` to the carrier's original receipt or
typed error binding. Success requires matching host, managed owner, native
ID and original key, plus the carrier receipt's accepted, nonqueued state
and turn index. A bound prior-carrier refusal or native refusal retains its
typed meaning. Missing or changed reply bindings, unclassified HTTP errors,
response loss, post-attempt interruption and post-attempt deadline expiry become
`native_prompt_admission_unknown` with the original host, identity and key.
They do not authorize a fresh-key resend, recovery, cancellation or creation.
A protocol preflight refusal remains distinct from an unbound 426 returned
after an attempted POST.

Interrupting the caller after entering the POST attempt boundary reports
unknown admission with the original binding and key; the CLI exits 70. It
does not cancel the remote turn or authorize another send. Interruption
before the POST retains ordinary local interruption behavior and sends
nothing. Interruption during a later result wait remains a separate
incomplete observation of already-confirmed admission.

This is an additive provider and explicit client method. The opt-in CLI
consumer below negotiates it separately; ordinary defaults do not change. Supported old operations
retain their syntax, response and effects. Provider rollback gives the
declared capability refusal; it does not select an effectful legacy fallback.
The synthetic HTTP/carrier/native-API seam in
`tests/test_remote_native_prompt_http.py` proves one native turn and no
automatic replay after response loss. It does not prove live rollout or
model-facing consumption.

### Exact local ordinary prompt submission

`send ID "prompt" --bridge` adopts the same project-scoped exact selector as
`show`, using only the local daemon's inventory when `--host` is absent.
It accepts `--all-projects`, `--machine` and `--namespace`. Explicit-host
managed sends are described below. Selection requires
`bridge_inventory: 2`; unavailable or ambiguous evidence never falls back to
agent/profile matching. This is opt-in. The legacy `send` selector and defaults
remain supported, with no retirement in this release.
An HTTP or connection failure during selection returns `refused_unavailable`
with reason `bridge_selection_unavailable` and exit code 69, before any admission.
This includes the real client's initial health and compatibility preflight.

The selected row must identify a running conversation and a current admission
owner. Retained managed history and metadata-only rows cannot accept a prompt.
This operation never ensures a daemon, hydrates metadata, resumes a dormant
conversation or creates a replacement. A running inventory observation does not
authorize submission by itself: the existing owner checks again at admission.

For a managed conversation, the existing native-bound turn API checks the native
ID under its turn lock and refuses stopped, replaced or busy targets. Keyed
sends also require the existing `native_send_no_resume: 1` contract. For a
registered conversation, the existing inbox API receives `exact_session: true`
and the identical raw `expected_session_id`; its transactional enqueue refuses
aliases, expiry and supersession. An admitted exact message remains with its
original native session during later rollover, rather than move to a successor.
Inbox admission does not prove SDK delivery or model-facing consumption.
An enrolled inbox's known pre-admission refusal returns the typed
`refused_unavailable` outcome with reason `retained_inbox_refused`, not a
missing JSON result or an unknown-admission claim. Other HTTP admission errors
that have no recognized refusal contract report unknown admission in
`--bridge` mode and retain the original binding; legacy error handling is unchanged.
This also applies to managed HTTP 404/409 errors: the opt-in adapter preserves
the native owner's known refusal messages, not a blanket status-only refusal.
For registered sends, only the inbox owner's `live session not found` message
proves the corresponding HTTP 404 refusal; other 404 responses remain unknown.
Unkeyed native-bound admission makes one POST without automatic retry, including
for the legacy explicit-native selector. A lost response must not start another
turn. In `--bridge` mode, a missing or mismatched native admission receipt is
unknown, not proof that admission was refused.

The new `live_exact_send: 1` capability advertises this exact-input option on the
existing inbox endpoint. The client checks the executing daemon before the POST,
pins that endpoint and does not retry the POST automatically. An older or rolled
back provider without this capability refuses before sending; it is not asked
to ignore an unknown safety field. Old callers retain the same inbox semantics
on the expanded provider. No new RPC, database or receiving-enrollment protocol
is introduced.

This first send consumer accepts ordinary prompts, prompt files/stdin, JSON,
`--no-wait` and an optional original idempotency key. It refuses creation, resume,
force/queue, alternate delivery/kind, explicit delivery/reply waits, startup,
receipt and settlement modes before connecting. Explicit sender/reply-to fields
apply only to a registered inbox; managed targets refuse them. Existing commands
retain those specialized contracts. Managed sends still use their existing
turn/stream result, and registered sends retain their existing message receipt.
This slice does not yet unify those two admission models or migrate defaults.

A lost or invalid admission response reports `outcome: unknown`, not permission
to retry. JSON retains the selected native/owner binding in `bridge` and the
original `idempotency_key` when supplied. Observe the original operation through
its existing receipt owner; do not resend to a new key or successor.
`tests/test_bridge_send_cli.py` exercises the real parser, client and private
API owners, including refusal races, exact input retention, rollback and lost
responses. These contained checks do not certify installed adoption.

### Exact explicit-host managed prompt submission

`send ID "prompt" --bridge --host HOST` selects only the named configured
host's inventory. It pins that row's host, current managed admission owner
and native conversation. A route from another host, a retained-only owner
or a registered-only inbox refuses before admission. Exact remote registered
inbox adoption remains open; the existing qualified-peer commands retain
their separate contract. No selector falls back to a local row, legacy
profile, remote creation, resume or another host's merged owner ID.

`--require-agent-hosting` with `--host` also refuses before contacting either
peer. The remote prompt contract does not propagate that requirement to the
receiving client; an intermediary's hosting capability cannot substitute for
end-to-end enforcement. Existing local enforcement remains available.

The client negotiates `remote_native_prompt: 1` for the one exact keyed
admission described above. When the caller omits `--idempotency-key`, this
invocation generates one key before admission and retains it in the receipt,
text output and unknown-admission result. It never replaces that key after
an error. A matching native refusal retains the existing typed outcome and
exit code, including busy (75), conflict (65), unavailable capability (69)
and invalid options (2). Unknown admission returns exit 70 and the original
host, owner, native ID and key. Neither outcome authorizes a fresh-key resend.

Text unknown-admission output also prints that complete original binding and
the invocation key, including a key generated by this CLI invocation.

JSON returns the native admission receipt with `bridge` and `outcome`;
`outcome: delivered` has the existing managed-send meaning of turn admission,
not completed work or model-facing consumption. Text with `--no-wait` prints
the bound admission and key, then returns. These modes need no result reader.

Default text mode waits for the admitted turn's bounded completed result.
It preflights the existing exact native-result provider before admission.
Missing support refuses before the prompt, rather than silently dropping
the wait. After admission, each cursor-neutral snapshot pins the same host,
owner and native ID. Only a completed result with the receipt's exact
`turn_index` is printed. An older idle/result observation cannot complete
the wait, and a later turn or successor is never printed as this result.
The existing command timeout bounds the wait, while each snapshot retains
its own shorter deadline. Partial and truncated results are marked explicitly.
This mode requests a 6000-character shared snapshot budget. The current
allocator reserves half for the latest result, so the effective completed-text
ceiling is 3000 characters, below the producer's independent 4000-character
field ceiling. This mode is not full transcript delivery or token streaming.

Managed event history has no native content-origin contract. This new
explicit-host mode therefore does not open an unqualified event stream,
advance a delivery cursor, or honor feed expansion: `--expand` refuses
before connecting. Local managed sends retain their existing stream.
After a confirmed admission, result timeout, interruption, source failure
or identity change reports that admission remains accepted and observation
is incomplete. It does not become an admission refusal or trigger another
prompt. A settled legacy producer without the original native content-origin
record reports that result observation is unavailable, rather than waiting
for an origin that cannot be reconstructed. Inspect the original operation
instead of resending.

`tests/test_bridge_remote_send_cli.py` qualifies the actual parser and these
owner/wait boundaries. The CLI controls in
`tests/test_remote_native_prompt_http.py` reach the actual HTTP, carrier and
native admission API and preserve one turn after a lost response. Prior
providers retain explicit capability refusal without an effectful fallback.
No ordinary default is migrated or retired, and no installed rollout is implied.

## Metadata only

The separately negotiated `worktree_metadata_hydration: 1` capability adds
`POST /api/v1/worktrees/{id}/metadata` and
`BridgeClient.hydrate_worktree(id, request_timeout=None)`.
It reads the exact workspace's authoritative native head and incarnation,
then imports dormant metadata through the existing native owner. An original
keyed journal instead uses its
[retained metadata-adoption owner](retained-acquisition-writer.md#metadata-loss-without-native-conversation-loss).
The original native conversation, keyed exclusions and explicit history gap
remain intact.
New imports require the exact record's `execution_leg_live` to be explicitly
false. Active, unknown or omitted custody evidence refuses. The exact record's
validated `bound_agent`, not its cached discovery value, supplies the launch
profile. A member repository's name may differ from the owning project label;
the existing exact-record owner and path qualification retain that boundary.

The response contains the real SessionInfo plus
`metadata_hydration: {version: 1, native_execution: false}`. A new control record
is stopped, with automatic recovery disabled. The operation never loads native
execution, drains a queue, sends a prompt or accepts a task. Repeated calls and
lost replies discover the committed control. Another frontend with stale
in-memory ownership refuses rather than duplicating that persisted record.

Changed or ambiguous heads/workspaces, pending handoffs, live transport custody,
incomplete Host inventory and launch holds retain explicit refusals. A listed
workspace without a usable head is not permission to create another conversation.
Both Python operations qualify the actual receiver on each transport attempt.
Metadata POSTs are not automatically replayed after an unconfirmed response;
unsupported older receivers refuse before mutation. CLI target discovery,
normal read/send selection and Dispatch caller migration remain separate.
