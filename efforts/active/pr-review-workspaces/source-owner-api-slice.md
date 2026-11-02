# Exact source acquisition and owned asynchronous execution

Parent: [PR Review Workspaces](README.md). Extend the existing Worktrees Git,
selected source adapter and caller lifetime owners. This is source admission,
not resource allocation, installed adoption or permission for live fetches.

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

## Exact acquisition in the existing Git owner

Add a bounded exact-object fetch operation to `git_ops`, retaining its current
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
using the same tracked-task, shielded blocking-work pattern already used by the
PR Gateway. It wraps a selected `LocalReviewSource`, not a new resource owner,
service, queue, process supervisor or ledger. The existing embedding lifetime
must stop admission and await its terminal drain before releasing source custody.

Run inspection/acquisition and retained reads off the event loop, with an
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
descriptors. The existing CLI/tool facade may expose optional `source-prepare`
only for a bound acquisition owner and source/read for a bound async adapter.
The new request carries only number, generation ID and existing IO budget;
it cannot choose a remote/workspace/account or introduce another permission.
Old clients/parsers and GitHub/provider-neutral metadata stay compatible.

## Source acceptance and operational holds

- [ ] Contained real Git fixtures prove exact head/common depth-one acquisition,
  unchanged target/working state and prior refs, no broad refspec/tags/submodules,
  and rejection of full/shared/alternate storage before transport.
- [ ] Actual synthetic source owner/client/tool consumers prove preparation and
  immutable attachment coherence, changed scope/revision refusal and exact
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
