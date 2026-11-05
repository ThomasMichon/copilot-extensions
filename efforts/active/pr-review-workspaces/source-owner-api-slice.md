# Prepared source and owned asynchronous execution

Parent: [PR Review Workspaces](README.md). Extend the existing Worktrees Git,
selected source adapter and caller lifetime owners. This is source admission,
not resource allocation, installed adoption or permission for live fetches.

**Current implementation scope:** prepared-only implementation scope; acquisition
deferred pending resource-owner capacity admission. #9907 admitted the broader
proposal, but #9928 is narrowed to prepared source and owned async execution.
No acquisition callable, binding or tool is shipped. Acquisition remains OPEN
under #9535 at the existing Worktrees Git/resource ownership boundary.

## Boundary and reconciliation

The existing worktree preparation helper chooses a default branch and the Git
fetch helper fetches a whole remote. Neither satisfies review acquisition.
The existing source inspector requires already prepared objects. The PR tool
facade consequently refuses acquisition and async source execution today.
This slice closes those specified source gaps below the existing owning visions;
it does not revise the desired architecture or establish another service.

The normal registered resource pipeline must first select an existing,
exclusively owned writable object repository and its configured remote,
repository/account/scope, opaque workspace reference and current checkpoint.
It must exclude reference, agent-guarded, foreign, unbound, occupied and unknown
members using its actual registration, claim and live-member authorities.
No request path, boolean, class label, manifest or receipt authenticates selection.
This library does not allocate/adopt a member or claim that a real selector is
available. Missing selection remains unsupported. Live owner adoption is separate.

## Deferred acquisition in the existing Git/resource owner (OPEN)

The following are future requirements, not available APIs. Before realization,
the existing resource owner must admit actual pre-write transfer/storage bounds.
Depth-one Git acquisition still writes unchanged blobs before inspection limits
apply; stream-output bounds and an execution deadline do not bound that storage.
A new issuer, guessed quota or replacement transfer client is not a solution.

Future work may add a bounded exact-object fetch operation to `git_ops`, retaining its current
repository-scoped credential selection and transport. A selected source adapter
may bind that callable and one existing remote in its constructor; neither
remote nor credentials can come from a workspace tool request.

Inputs are the complete selected metadata generation's immutable full head and
authoritative common object IDs, not branch names, target tips, merge bases or
today's remote refs. Fetch only those two objects with depth one, no tags,
submodules, FETCH_HEAD/ref updates or configured refspec expansion. Do not merge,
checkout, rebase, deepen history, run hooks or use a local clone optimization.
Missing authoritative common/head evidence refuses before fetch.

Depth-limited fetch mutates shallow metadata. Therefore acquisition is supported
only in an already selected dedicated shallow object repository, never a shared
linked-worktree/common directory, alternates-backed store or an ordinary full
history checkout. Validate that physical shape before credential acquisition,
and again before the fetch. This restriction protects real Git storage, not a
delegate permission or quota. Existing populated objects and all prior retained
refs remain untouched. Existing credentials and owner checkpoint govern every
operation; no ambient fallback is invented.

After physical fetch completion, verify both exact commits through the existing
source inspector, full native path/blob mapping and bounded content checks.
Only that inspector publishes the same immutable head/common ref transaction.
Its separate attachment remains bound to the original metadata generation.
Failed or lost fetch/ref replies cannot claim a ready attachment, clean rollback
or completed review effect. Objects may have arrived without ready publication;
retry rechecks exact identities and never infers success from buffered stdout.
No source/metadata clocks are rewritten.

## Owned async execution and drain

Add an optional asynchronous execution adapter under the existing source owner,
using the same tracked-work, shielded blocking-work pattern already used by the
PR Gateway. It wraps a selected `LocalReviewSource`, not a new resource owner,
service, queue, process supervisor or ledger. The existing embedding lifetime
must stop admission and await its terminal drain before releasing source custody.
It supplies an already owned thread executor; this adapter neither constructs
nor shuts down an executor. Actual native executor futures distinguish queued
cancellation before launch from cancellation of an asyncio wrapper. Missing
owner binding refuses rather than selecting a default producer.
One physical selected-source operation is admitted at a time, refusing busy
requests before copying rather than adding an internal request queue. The
absolute deadline starts at admission and bounds queued work and actual child
IO. Native completion and original cleanup handles are retained independently
of cancellable async wrappers, including normal executor shutdown.
The same selected-source reservation begins before metadata materialization.
Client IO stays on its owning loop, while heavy preparation is tracked in the
existing executor; cancellation cannot release pending CPU/native work. Public
synchronous use is fenced after async binding, and binding refuses active sync
work. Retained source identity/revision is pinned before any queued snapshot.
An ended operation cannot register new native work or client tasks. Native
metadata lookup/chunks check the same operation deadline and withdrawal state,
without replacing the client's own lifetime checks. Preparation rechecks that
state after argument copying and before invoking its callback.
Client invocation is registered before it can start a task. Withdrawal cancels
each client at most once and does not interrupt an already cancelling client's
cleanup. Already-started rejected futures remain in the same owner's terminal
work accounting. Successful results are checked against withdrawal/deadline
before acceptance, and direct inspection pins its selected scope/revision
before executor submission just as retained reads do.
Nested scheduled clients use that same tracked wait path rather than forwarding
another cancellation into their cleanup. Drain rechecks work registered during
withdrawal under its original absolute deadline and fences terminal settlement.
Opaque asyncio futures do not prove native producer completion: clients must
use loop-owned coroutines/tasks or actual native futures from their existing
producer, including the supplied owned preparation executor.
An unsupported future stops admission and retains an explicit unconfirmed
settlement error and wrapper; cancelling it cannot make drain succeed.
Actual native futures are retained to terminal settlement even when returned by
a worker or client. Worker preparation returns synchronous values or observable
native futures; unstarted coroutine results close without IO. Unexpected
loop-awaitable worker results retain unconfirmed settlement and their original
handle rather than depending on callback delivery to a stopped loop.
Operation-created tasks track their own cancellation requests on all supported
runtimes. External tasks without cancellation-state inspection remain retained
rather than risking another cancellation during cleanup.
Selection headers are structurally bounded to the selected owner's scope keys
and normalized revision fields before any on-loop identity pin is copied.
Supported nested loop results transfer to the active waiter through actual
tracked child execution before parent settlement. Withdrawn or unsupported
results retain unconfirmed settlement and their producer handle. Late original
cleanup failures remain available to the same custodian; neither cancelled
waiters nor completion DTOs release unknown custody.
The existing ServiceCaller request owns actual registered native completions.
WorkspaceClient forwards the current source's native admission internally;
the existing credential-reference reader uses that already owned executor in
source scope. Native producers register their real completion handle before
awaiting a cancellable wrapper, and request cancellation joins that handle.
Opaque producers that hide native work are not qualified by a declaration.
Internal operation call/wait helpers compose an already qualified cooperative
binding; accepting a coroutine is not qualification of native work it hides.
The tool facade admits only the stock preparation/request bindings above.
An embedding that supplies another producer still owes actual native completion
custody and owner adoption; synthetic helper callbacks do not establish it.
The new async inspection lane requires actual immutable generation bytes from
the existing verified metadata materialization/read cache. Raw async requests and
unqualified credential producers refuse before source work, without a legacy
flag or unsafe fallback. The stock ServiceCaller source binding executes its
actual cooperative credential reader and joins registered native completion.
Preparation qualifies actual stock exchange/discovery/materialization methods
and rechecks them before client IO. Overridden methods and configured custom
read-admission callbacks stay metadata-only; they cannot qualify hidden native
producers for this source lane. Source-owner permission denials remain original
through native credential settlement. Suspended rejected coroutines retain
unconfirmed custody without driving their cleanup, and completion reconciliation
is safe when native callback registration settles work reentrantly.
Ordinary metadata/callback APIs and released native/synchronous raw inspection
remain unchanged. Git path/output limits retain their original meanings; there
is no new metadata quota, issuer or atomic on-loop copy allowance.
The caller's wait expires on that same deadline without dropping native custody,
and potentially blocking owner checkpoints execute inside the worker.

Run inspection and retained reads off the event loop, with an
operation-specific cancellation checkpoint combined with the original selected
owner checkpoint. A caller cancellation/disconnect withdraws that operation;
already-entered bounded Git IO may finish cleanup before its worker terminates.
Track that worker until actual terminal completion, even after the caller
returned. A drain timeout/cancellation retains pending work and custody; it is
not success or authority to release/reuse the member. Stop admission is
irreversible for that adapter instance. Explicit original owner denials remain
denials; native response completion and transport/cleanup disposition remain
distinct. No review operation is replayed or inferred complete.

Capabilities explicitly distinguish synchronous prepared-only sources,
bound exact acquisition, owned async execution and missing bindings. Keep the
existing strict source/read contract shapes intact through separate additive
descriptors. The existing tool facade exposes source/read for a bound async adapter, but the
one-shot CLI refuses that async source binding because it cannot own its original
loop through later reads and terminal drain. Metadata/native CLI behavior remains
unchanged. Exact acquisition is unsupported and has no command or advertised
capability. A future acquisition wire shape requires its own reviewed admission;
requests cannot choose a remote/workspace/account or introduce another permission.
Old clients/parsers and GitHub/provider-neutral metadata stay compatible.

## Deferred acquisition acceptance (OPEN under #9535)

- [ ] Contained real Git fixtures prove exact head/common depth-one acquisition,
  unchanged target/working state and prior refs, no broad refspec/tags/submodules,
  and rejection of full/shared/alternate storage before transport.
- [ ] Existing Worktrees Git/resource owner admits actual pre-write capacity and
  realizes the deferred acquisition API under #9535; no such owner is fabricated.

## Prepared/async source acceptance

- [ ] Actual synthetic source owner/client/tool consumers prove prepared-source
  immutable attachment coherence, acquisition refusal, changed scope/revision refusal and exact
  path/blob/content verification.
- [ ] Async consumers prove nonblocking execution, cancellation/owner loss,
  stop-admission, failed/timeout drain retaining custody and later terminal drain.
- [ ] Preserve old prepared-only/native/client compatibility and original IO
  budgets. Run focused containment, lint/install/docs/changefile gates, current
  source review, required CI and managed merge.

Actual registered resource selection, current credentials, eligible live
provider review, concurrent real reviewers, tool-host/v2-reader adoption,
installed first touch, serving/release and authorized rollout remain open.
Synthetic callbacks prove callable contracts, not actual operational ownership.

The additive library preparation descriptor is version 2: cooperating clients
accept the supplied operation checkpoint for capability/cache reads and
materialization, including after pacing/read admission and before each request.
Version 1 peers retain metadata compatibility but cannot advertise owned async
source tools. This changes no transport frame, credential owner or IO limit.
