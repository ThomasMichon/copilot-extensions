# Published loadout preparation and review

The optional `fleet_manager.loadout_gateway` SDK connects the
[loadout designer](loadouts.md) to the existing caller-aware
`ServiceConnector`/`ServiceCaller` Gateway profile. It acquires published
configuration on the controller, retains a review proposal and exposes an
exact-generation decision. It is **not** a deployment executor, new resident
service installer, credential issuer or graphical designer.

`prepared` means the published composition passed the current designer against
the supplied admitted inventory. It does not mean its artifacts are authenticated,
its adapters are available, a deployment was accepted or any capability is ready.
Every current candidate explicitly reports `execution_admission: not-implemented`.
Every decision reports `activation_authorized: false`.

## Existing-owner embedding

The adopted controller's existing lifecycle owner constructs
`PreparedLoadoutService` with these explicit inputs:

| Input | Owner and boundary |
| --- | --- |
| `fleet`, `gateway`, `slot` | The already admitted service assignment; incoming identity cannot select them. |
| `config`, optional `catalog` | `GitSource` objects selected by the existing repository adoption owner. Bundled templates remain available without a catalog. |
| `store` | One `PreparationStore` SQLite path bound to the fleet and preparation installation. |
| `observations`, `origin`, `project` | An existing `ObservationStore` and explicitly bound producer generation; use a dedicated origin/project stream for this installation. |
| `authorize` | Current adopter policy maps channel-derived `CallerContext` to `ReviewScope(account, policy_revision, can_confirm)`, or raises `PermissionError`. Missing or withdrawn scope refuses. |
| `inventory` | An async read of the existing admitted target/provider snapshot, including baseline exclusions and actual freshness. It never provisions a host. |
| `acquisition_seconds` | A finite source/inventory/design budget, default 15 seconds, maximum 30. It is not an outfitting-duration promise. |

Pass `service.binding()` as the existing `ServiceConnector`'s `handler`.
That connector's embedding still supplies its current token callback, proof
key, TLS trust and resident lifetime. The preparation SDK neither reads
credential files nor provides a new login or background reconnect loop.
It does not modify the credential/static-index Gateway profile.

The thin embedding constructs `PreparedLoadoutClient(existing_service_caller,
prefix="/selected-route")`. That finite client supports `refresh()`,
`inspect()`, `plan(prepared)`, `notifications(cursor)` and
`confirm(generation=..., selected_digest=...)`. It does not fetch Git, upload
configuration, hold a manager credential or execute a deployment.
There is no new implicit CLI configuration, daemon or ambient source discovery.

## Source custody

`GitSource` is trusted deployment input, not a schema accepted over HTTP. The
repository adoption owner supplies:

- A bounded source ID and adoption-policy revision, an absolute adopted
  repository path and absolute Git executable.
- An existing remote name, its exact non-secret expected fetch URL, one
  `refs/heads/...` selection and one portable repository-relative `.json` path.
- Its existing `agent_procutil.owned_process.run_caller_owned` adapter, with
  finite native process-tree custody, headless spawning, separate output
  budgets, `on_settled` notification and explicit environment/cwd. This is the
  repository owner's existing runtime facility, not a second process supervisor
  installed by Fleet.

The controller verifies the remote, resolves the exact advertised branch
commit and fetches that immutable object without using a mutable local snapshot
ref or writing `FETCH_HEAD`. It reads only an ordinary `100644` JSON blob.
Concurrent local ref changes cannot redirect that captured commit.
It does not check out files, execute templates,
run repository hooks, fetch submodules or start Git maintenance. Dirty local
files and local Git replacement objects are not configuration inputs: every
invocation disables replacement refs, preserving the reported commit's actual
content. Git environment overrides are removed;
interactive prompts are disabled. Embedded HTTP credentials, URL passwords,
query strings and fragments refuse. The normal adopted Git credential/SSH/TLS
owner remains responsible for authentication; no token is accepted in a
refresh or confirmation request.

Each selected JSON document is bounded to 1 MiB before reading. Native
stdout/stderr and elapsed acquisition are separately bounded. Git object
storage, filesystem quotas and transport/credential policy remain with the
already adopted repository owner: the JSON limit is not a claim that a fetched
Git pack is limited to 1 MiB. No clone or new source registration occurs.
The adapter's settlement notification distinguishes a confirmed-settled refusal
(including stdout/stderr overflow) from unresolved native custody. A settled
failure publishes an inspectable blocked candidate; a later explicit refresh
can recover. An unsettled failure remains the process owner's responsibility.
The service retains it in `repository_failure` and refuses further requests
until that owner has inspected and settled it; it does not discard process/Job
custody or automatically retry through the failure.

Configuration and an optional contributed catalog can have independently
adopted selections. The proposal records both source IDs, adoption revisions,
exact commits and selected paths. Template provenance must still match the
registrar, and source digests include the actual inventory and expanded plan.
No path, ref, URL, account, credential or replacement JSON document can be
selected through the routine control request.

## Durable proposal and exact review

`PreparationStore` owns one current candidate and one retained last valid plan.
It is not a task queue, deployment journal, grant store or replacement for
`NodeJournal`. Transactions bind the store identity, compare the expected
generation, and commit a new candidate independently of a client connection.
An identical candidate preserves its generation and existing decision.
Material source, inventory or plan changes create a new generation and clear
the prior decision.

Failed acquisition/invalid input publishes a blocked candidate without erasing
the last valid plan. The blocked candidate cannot reuse earlier approval.
Candidate metadata and persisted review scope are checked on readback. Full
plan reads and admission validate content, including the composition and
suggested configuration's fleet IDs against the store's fleet. The suggested
configuration must carry the same composition; matching foreign config and
inventory cannot be relabeled by an owned candidate envelope.
Paged inspection retains one bounded immutable encoded plan per validated
generation/digest. A cold or changed generation validates that content once;
warm metadata/status and range reads avoid repeating that work. Each range
transaction rechecks the current generation/digest. A changed concurrent
generation refuses rather than overwriting another controller's proposal.

Confirmation accepts exactly `{"generation": ..., "digest": ...}`. It pulls
the adopted sources again, reads current inventory, reruns the designer and
revalidates current caller policy before recording the decision. Stale,
blocked, expired, changed or unauthorized selections refuse. A repeated
decision in the same scope returns the same retained record. A different
account/policy/principal cannot reuse it as its own decision.

The digest binds composition integrity; it does **not** confer authority.
`ReviewScope` is a current embedding policy result, not a client-supplied
account label. Request expiry/cancellation and policy checks are not atomic
external-effect fencing. Communication loss can make a review write uncertain:
inspect the retained generation/decision rather than inventing another
operation. No deployment effect exists in this slice to retry.

## Gateway routes and notifications

The selected service route forwards only these application paths:

| Method/path | Result |
| --- | --- |
| `POST /refresh` with an empty body | Acquire and compile the selected published sources. |
| `GET /prepared` | Current metadata and decision; no source pull. Freshness is explicit and source revalidation remains required. |
| `GET /plan/<generation>/<digest>/<offset>` | One aligned, at-most-16-KiB chunk of the exact current plan. |
| `POST /confirm` | Exact-generation review decision, with no replacement config. |
| `GET /notifications` | Current scoped preparation notice and an observation cursor. |
| `GET /notifications/<store>/<origin>/<project>/<position>` | A bounded page of notices from the existing observation replay store. |

All routes require the existing authenticated channel and current adopter
scope. A busy preparation refuses with 429 instead of building a work queue.
Invalid/stale application state returns a value-free conflict; missing caller
scope returns 403. Source failures are visible blocked state with stable reason
codes. Native diagnostics, source URLs and credentials are not response bodies.

Plan paging fits the existing handler response bound even for a large resource
grid. The thin client verifies exact byte count and plan hash; every range is
bound to the same generation/digest. A proposal change mid-read refuses.
Cold range access validates the full encoded plan once; subsequent pages copy
only their range, without re-encoding the full plan on the Gateway event loop.
The maximum 4-MiB plan needs 256 pages, not 256 full-plan validations.
The client paces a download below the existing Gateway control/public request
rates without changing those limits. Only a pre-dispatch challenge 429 can
repeat a read-only page admission, at most three attempts for the same selected
generation, digest and offset. Authentication failures, network/unknown outcomes
and stale selections are not retried; refresh/confirmation are never replayed
by this mechanism. Persistent admission pressure remains an explicit failure.
Notifications contain only bounded proposal identity/status, not raw configuration.
Foreign/expired cursors refuse; a new producer generation requires resync.

The observation is advisory, never deployment acceptance. Its source revision
comes from the durable proposal's transition sequence. Inspect/notification
reads repair a missed latest notice after restart; this is current-view replay,
not a guarantee to deliver every intermediate transition exactly once.
The existing owner must admit/bind the notification origin; incoming requests
cannot enroll or replace it.

## Qualification and remaining boundary

`test_loadout_preparation.py` exercises actual Git publication, controller
fetch, dirty-file exclusion, remote/mode refusal, disabled transaction hooks,
durable restart, exact decision/repeat/CAS, expiry and plan corruption.
Published-content controls include local blob replacement and fixture execution
with inherited repository/index/config overrides, without touching the selected
external paths.
Cross-fleet controls cover every composition/config projection, persisted
readback/confirmation and matching foreign source/inventory through the real
Gateway. Exact-limit paging and real settled native-output refusal controls
cover bounded work and recoverable source failure. The large client control
uses the actual Gateway budget functions with a fast deterministic clock;
separate real TLS controls preserve 429 before credentials or dispatch.
`test_loadout_gateway.py` carries that real Git source through actual TLS/PoP/
WebSocket transport and the thin client, including chunked inspection, scoped
notification/replay, confirmation, changed inventory/source, current-policy
withdrawal, failed-source preservation and joined source-worker cancellation.
All identities and repositories are disposable test fixtures.

Both Gateway backend profiles check disconnect custody after an awaited chunk
send and before waiting for its ACK. The HTTP profile also checks after native
response-start/body reads and header writes, before entering another body read.
A native read/write that consumes task cancellation
must not leave the connector reader waiting on an exchange that itself needs
that reader to deliver an ACK. Deterministic start/read/send race controls and
the unchanged real caller deadline/recovery tests cover this boundary; no
client timeout or automatic retry policy is relaxed.

These are source/SDK integration controls, not live deployment evidence.
The existing release, credential, target, command/receipt and deployment
owners must still admit and execute the selected artifacts and complete support
closure, qualify ARC's lifecycle and prove an authorized Actions job.
Outfitting deadlines, graphical interaction and production controller/client
lifecycle integration remain separate open acceptance items. This SDK does not
authorize live enrollment, new capacity, pool migration, credential transfer,
workflow routing or config-deletion teardown.
