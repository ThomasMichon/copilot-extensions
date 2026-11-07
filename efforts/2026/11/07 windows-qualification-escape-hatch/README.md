# Windows Qualification Escape Hatch

- **Slug:** `windows-qualification-escape-hatch`
- **Repo:** copilot-extensions
- **Branch(es):** serial managed proposal and implementation PRs
- **Created:** 2026-11-06
- **Status:** Done
- **Vision:** [Test portfolio effectiveness](../../../../visions/test-portfolio/README.md)
- **Umbrella issue:** #11169
- **Related campaigns:** #10765, #6237, #7122

## Guiding Intent

Give local contributors an explicit, source-bound way to obtain meaningful
Windows test evidence without saturating their development machine. Use a
manually invoked GitHub-hosted worker, not a second persistent runner service.
Focused selections are the default; a broad suite is an explicit exception.

The completion gate is a usable local submission/result flow through the
reviewed default-branch workflow and a real disposable Windows worker.
Source files, a successful dispatch, or a merged Dev PR alone are not completion.

This closes the test-portfolio vision's bounded feedback, cheapest faithful
tier, containment and attributable-failure intent. It preserves existing
required CI, promotion, native qualification and source-publication gates.
It follows the [architecture principles](../../../../docs/patterns/README.md)
of a right-sized surface and attributable invocation, plus
[version-skew-safe contracts](../../../../docs/patterns/service-contract-evolution.md)
for the independently published local caller and hosted controller.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Qualification implementer | Plan, source, tests, hosted acceptance and PR stewardship | #11169 and managed source worktree |
| Repository owner | Only admitted submitter and rerun principal | Repository-scoped GitHub identity |
| Automated reviewer | Independent plan and implementation review | Repository Copilot review flow |
| Existing CI and release owners | Retain required CI, promotion and runner-routing authority | #6237 and #7122 |

## Coordination

- **Topology:** tightly coupled source work with one owner, one checkout and
  dependency-ordered functional outcomes.
- **Host (owns PRs):** Qualification implementer.
- **Delegates:** none.
- **Handoff:** continue this canonical effort, preserving current source,
  live requests and the outstanding hosted acceptance gate.
- The Driver effort #10765 remains Active. This is its reusable qualification
  enabler, not completion of its caller/lifetime/receiving requirements.
- Do not edit the existing Linux offload selector, required CI graph, release
  writer or native rotation to implement this additive manual lane.

## Context

The repository already has bounded test runners, source-bound capture tooling,
manual workflows and a protected default-branch control convention. The missing
piece is a general local request/result path for a focused Windows escape hatch.

The [implementation design](design.md) defines admission, immutable source
transport, selectors, results and failure handling. It is an agent-recommended
realization, not a statement that these interfaces already exist.

## Request

Operator request, verbatim:

> That works. Create the maintainer-only (heck, even me-only) workflow for this.
> We'll need a way to ensure we don't pollute lots of branches into the upstream
> remote. This whole flow is so agents running locally can "feel satisfied"
> about Windows qualification for their various flows, without contributing to
> CPU saturation. Ideally, agents will scope their test runs to only what they
> need, rather than running whole suites every time, but this is an "escape hatch".

The agreed workflow home is this repository. The public source, not a private
control repository or machine configuration, is the test input.

## Scope Approvals

The 2026-11-06 operator request above authorizes implementing the owner-only
hosted Windows workflow and its local request/result path, normal source
review/publication, and bounded real hosted qualification.

The proposed single reserved transport ref is the implementation of the
explicit branch-hygiene constraint. It does not authorize default-branch
pushes, source merges through staging, force-updating shared history,
unrelated ref cleanup, new repositories, private credential export,
self-hosted runner provisioning or runtime/fleet rollout.

Broader suite selection does not authorize larger existing packet limits or a
claim that skipped, unsupported, partial or mismatched runs qualified a flow.
Unknown publication or dispatch effects remain owned and must be reconciled,
not replayed under a new request identifier.

## Plan

### Phase 1 - Review the execution and transport boundary

- [x] Resolve the source venue, verify effort adoption and inspect existing
  offload/control ownership before claiming #11169.
- [x] Capture the complete request and distinguish the proposed design from
  execution authority.
- [x] Land this proposal after required review and CI before implementation.

### Phase 2 - Deliver one complete focused Windows flow

- [x] Implement owner-only protected-control admission, including reruns,
  without granting write credentials to candidate execution.
- [x] Implement source submission using one reserved append-only transport ref,
  preserving exact candidate identity and the caller's branch/index/worktree.
- [x] Implement typed focused plugin/tool selectors and existing named capture
  packets; make broad suite selection explicit and keep canonical bounds.
- [x] Return source/request/selector/platform-bound results, named progress,
  available JUnit, logs and generated evidence through ordinary Actions artifacts.
- [x] Prove the complete local submit, hosted run, status and result path on a
  real focused Windows consumer, not a synthetic success marker.
- [x] Publish and merge the complete flow through normal review and required CI.

### Phase 3 - Qualify reuse and close the effort

- [x] Prove repeated and concurrent submissions do not create per-run branches
  or lose an older requested source when the transport ref advances.
- [x] Prove denied admission, failed tests, empty selections, interrupted
  submission, unknown dispatch and handled timeout retain truthful outcomes.
- [x] Document the actual command, source/ref retention and evidence limits in
  TESTING.md and contributor guidance.
- [x] Confirm the workflow reaches the default branch through normal promotion
  and demonstrate actual hosted execution of that released control.
- [x] Resolve every Plan/Validation item, mark Done and archive.
- [x] Deferred to `#10765`: Finalize the retained source workspace after its
  source-owned client is no longer needed by the Driver qualification request.
- [x] Return to the still-open Driver effort's exact-source qualification gate.

## Validation Plan

- [x] Unit controls reject non-owner/rerun/non-control-ref admission, malformed
  requests, unsafe paths, arbitrary commands and implicit whole-suite requests.
- [x] Real Git controls preserve exact source identity, local branch/index/tree,
  old requests and unrelated refs across shared-ref contention and bounded retry.
- [x] The actual request/result client distinguishes accepted, pending, unknown,
  failed and qualified outcomes and never silently resubmits unknown effects.
- [x] Missing or unsupported hosted control is reported before staging or
  dispatch; supported caller/controller versions retain their declared meaning.
- [x] Focused test identities, selection, skips, source SHA, workflow control,
  platform and artifact identity remain explicit in results.
- [x] The worker invokes the existing canonical bounded tool runner; controlled
  capture-path tests preserve preparation inside the packet aggregate deadline.
- [x] Deferred to `#10765`: Complete the actual named capture packet's writer,
  independent reproduction and artifact-materialization acceptance.
- [x] A real Windows run proves a focused consumer, successful artifact return
  and a handled failing/invalid request without local test execution.
- [x] Repeated source submissions use only the one reserved transport ref;
  no source feature branch or default branch is rewritten or merged.
- [x] Required source checks, code review, CI, promotion and final live
  acceptance pass at their actual respective source revisions.

## Proposal

Review [design.md](design.md) before implementation. The workflow is an
on-demand validation capability, not a new required check or release authority.

## Journal

### 2026-11-06 - Proposal

Resolved the source-only local venue and compatible effort adoption, inspected
the existing test-portfolio/control/Linux-offload campaigns, and claimed
#11169. The proposed source/result path is additive; existing owners, the
Driver parent and their independent completion gates remain intact.

### 2026-11-06 - Plan accepted

The plan in #11172 received Copilot approval at its actual head and green
required checks, then merged as
`f8c9820f67565402fe4b1b9de06c9bd4686f8d8c`. Pulled the same source worktree
forward to that commit before implementation. The next slice is the complete
focused request, Windows execution and result-return flow; no workflow,
transport ref or qualification result is implemented or activated yet.

### 2026-11-07 - Complete implementation candidate

Implemented the manual owner/original-rerun/protected-control workflow and
the source-owned submit/status/wait/resume/result client. The preferred shared
transport is the non-branch `refs/qualification/windows-inputs`; actual GitHub
namespace support remains a live acceptance gate, not a local-Git inference.
Requests discover the protected provider's versioned support before effects.
Unknown dispatches retain the same request; original transport recovery never
replays an unobserved remote effect.
Per-request crash-releasing operation custody excludes simultaneous local
resumes; independent requests still contend through the transport's ordinary
fast-forward update rather than a repository-wide lock.

The worker reuses canonical plugin/library selection, tool containment and
the complete HTTP-52 writer plus separate same-source no-write verifier and
artifact exporter. Results distinguish actual test outcomes, skips, empty
collection, failure and timeout, and record the managed test interpreter
separately from controller Python.

Validation: 54 focused controls passed, including actual Git append/reuse,
contention, lost-response reconciliation and branch/index/tree preservation.
Python F/E9 lint, headless-launch, documentation consistency and module-size
guards passed. First-touch local Git transport is covered; the disposable
hosted first-touch path, normal promotion, real result download and shared-ref
reuse still need proof after implementation review/CI. No hosted request or
remote transport ref has been created yet. This effort and its Driver parent
remain Active.
The actual source-owned CLI also refused the current unpublished protected
controller explicitly before creating its request store, staging source or
dispatching a workflow.
Eight targeted CLI controls also passed after tightening invalid-workspace
error handling; these supplement the 54-control run above rather than
claiming another full transport run.

### 2026-11-07 - Implementation review

Opened #11187 through the normal Dev PR flow. Required checks passed at its
first head; Copilot identified inherited Git selectors escaping the selected
root, unconfirmed reused transport tips after contention, and missing tool
tier enforcement. Focused real Git/pytest consumers reproduced all three.

Reused the existing repository-identity environment scrubber for command
boundaries, persisted reused tips as confirmed, and loaded the portfolio
guard for tool execution with private pytest scratch. All 59 focused controls
now pass, including the new cross-worktree, descendant-contention and actual
T3/T4 opt-in regressions. Review, refreshed CI, promotion and live hosted
acceptance remain open; no qualification ref or run has been created.

### 2026-11-07 - Implementation merged; hosted acceptance next

#11187 merged as `88e91b14daafe9191c3e21f49eff68e7cb3a831d` after a
genuine current-head Copilot review with no open issues and green required
checks. The ordinary owner-authored clean-Comment policy and configured
review-rule exception applied; no CI gate was bypassed.

Pulled the same source worktree forward after retaining the merged source.
The next slice is Phase 3: normal default-branch promotion, real focused
Windows submit/status/result execution, shared-ref reuse and truthful failure
acceptance. The complete hosted flow remains unchecked, and the effort stays
Active. No hosted qualification request or transport ref exists yet.

The exact merged Dev CI passed on its second attempt after the original
queued run was cancelled before executing jobs. Normal promotion run
`42325551793` then passed source units, native Windows Resume startup,
artifact build and attestation, but failed in the promotion job while
initializing the Sigstore verifier. This matches the recurring symptom
tracked in #10651; it is not evidence of an artifact mismatch, and its
underlying transient classification remains unconfirmed. No candidate PR
was created. The already-running normal successor `42326592274` is being
observed without weakening verification or force-retrying the failed lane.

### 2026-11-07 - Released focused flow accepted; transport integration correction

Normal successor `42326592274` passed all promotion stages. Protected Main
advertises request/result version 1 at controller
`7e4f306d32c4ae2ed53d4ee846dc0e32ec9c0c5d`.

The actual source-owned submit/status/result flow accepted published source
`88e91b14daafe9191c3e21f49eff68e7cb3a831d`, request
`362a52b5c83f4f1189799fa50061c332`, run
[42328654960](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42328654960),
attempt 1. Downloaded artifact `13556895146` passed the client's request,
source, controller, run/attempt, selection and Windows identity checks.
All 53 selected `tools/test_windows_qualification.py` cases passed in 6.359
seconds of worker execution on image `win25-vs2026` / `20261103.298.1`,
Windows Server 2025, AMD64, with controller and test Python 3.12.10.
The validated manifest SHA-256 is
`52954b177ae108930a08837ff28caf08fcbe6db73f79a40a778fd5abd779791b`.
This is genuine hosted qualification, not evidence that local-only transport
or the separate Driver packet has passed.

Two concurrent local-only candidate submissions stopped before dispatch.
Read-only remote reconciliation found no reserved ref, and a dry run of the
original push exposed the local legacy worktree PR guard's blanket refusal.
This is not evidence that GitHub rejects the non-branch namespace. Both
original request records and envelope identities remain retained; their
unconfirmed state is not silently retried or rewritten.

The compatibility correction scopes the existing native mediated-push marker
to the exact fixed-ref envelope push's private child environment. The boundary
refuses branch, force and deletion refspecs and suppresses automatic tag
following; it does not change installed hooks, shared Git configuration,
ordinary publication rules or chained local hooks. Bounded rejection
diagnostics stay with the original request. All 64 focused controls and
touched Python F/E9 lint pass, including the actual native legacy guard,
local-hook chaining and unrelated-tag preservation. Review, required CI and
live shared-ref/reuse/failure acceptance remain open.

### 2026-11-07 - Hosted schema skew and result precision

The explicit unmatched-selector request returned a bound failed result with
zero tests and client exit 1 in run
[42329653508](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42329653508).
It never qualified. Refined the tool adapter to distinguish pytest's known
empty-collection exit 5 with actual empty JUnit as `incomplete`; missing
reports and genuine failures still fail.

Hosted request `54a2668253d2414aba3b55033c2330ea`, run
[42330310109](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42330310109),
tested correction head `dbea2f3c48cb848dd9caa31227069f35cc789d43`.
Its older protected controller's containment helper omitted sandbox roots
required by the newer candidate's pytest policy. Collection refused with
exit 4 before tests ran; this is not a test pass or a provider admission
failure. The tool subprocess now selects the candidate's canonical
containment/environment helper source before execution, as the plugin
runner already does. A future-root schema regression proves the candidate
owner supplies its own private environment.

Review of #11213 also identified Windows shell-hook newline translation and
porcelain push rejection details living on stdout. The fixture writes LF
explicitly; bounded stdout and stderr are both retained and surfaced.
All 11 targeted classification/schema/hook/diagnostic controls and touched
Python F/E9 lint pass after these changes. Required review/CI, promotion of
the updated controller, and hosted revalidation remain open.

### 2026-11-07 - Compatibility correction merged

#11213 received Copilot approval at
`e190d465b24f5d903d365d51e8645560f067b6af`, with both findings resolved,
and merged as `eb41736e6aead56fef28aa62f92035d8f45f3852`.
The latest pull-request CI run `42331177626` passed before the successful
merge. An earlier attempt correctly refused while that run's required gate
was still expected; no CI gate was bypassed. The ordinary authorized
Maintainer review-rule exception applied separately.

The malformed version-99 request
`ec2a0efe993e40ecaed31733fbfa3d19`, run
[42331390558](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42331390558),
failed the Ubuntu admission step with `unsupported request contract`.
The Windows job was skipped, proving denied input does not start candidate
execution. This is expected rejection evidence, not a workflow regression.

Pulled this same source workspace to the verified squash merge after
retaining its reviewed source. The next slice is the updated controller's
normal Main promotion and hosted acceptance of source/schema compatibility,
shared-ref contention/reuse, deliberate failure and explicit incomplete
collection. The effort and Driver parent remain Active.

### 2026-11-07 - Hosted reuse and failure acceptance complete

Normal promotion
[42332070731](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42332070731)
passed. Protected Main controller
`ccea6c44019c7e9be7bafa5064e7fe6aad1340a8` advertises version 1 support;
the released worker, transport, client, contract and workflow match the
reviewed correction's source bytes.

Two concurrent local-only candidates were accepted through
`refs/qualification/windows-inputs`. The first created envelope
`158a452b1aa64431f25a41bcb9ef61eec391e977`. The second encountered an actual
remote ref-creation conflict, then the bounded normal fast-forward retry
retained that envelope in
`1f56ed1a0cf0a9acb1b61b177bba2b3428b22319`. Readback proved both exact
candidates remained ancestors, with only one qualification ref. The caller's
HEAD, clean index and existing README-only working change were preserved.
No feature branch, forced update, shared configuration or installed hook
change was needed.

| Request | Exact source | Run / artifact | Validated result |
|---------|--------------|----------------|------------------|
| `2ca610d4145a42e8a613cd83898bc993` | `8a331d20f0beca0d8e3922d737fa1e8007c0dddc` | [42333991936](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42333991936) / `13558503400` | Qualified, all 68 focused tests passed; client exit 0 |
| `2dee8d81c7684f29b1978585033c43fb` | `8a6403fc71609c77903a98c696cb61bff42c3a9f` | [42334008689](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42334008689) / `13558528089` | One intentional failure, client exit 1 |
| `b7fbce0376eb4ebaa1d4f0c5120393cd` | `8a331d20f0beca0d8e3922d737fa1e8007c0dddc` | [42334120429](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42334120429) / `13559175726` | Incomplete, explicit empty selection, client exit 1 |

All are attempt 1 on that controller, Windows Server 2025 AMD64 image
`win25-vs2026` / `20261103.298.1`, controller Python 3.12.10.
The successful and empty-selection manifests also identify the actual test
runtime as Python 3.12.10. Worker elapsed times were 24.672, 4.281 and 3.969
seconds respectively, not workflow queue/setup latency.
The final request reused the older readable candidate after ref advancement,
without another transport write, and actually checked it out on Windows.

The client validated the complete request/source/selection/control/run/attempt
bindings before returning these results. Manifest SHA-256 values, in table
order:

- `056b0df0738dbf2307b9208a4fa89bf67588928e10a91b15f4dc96f36524b29a`
- `5db104168939ef2538155ab3b19e6dbc87a97fd0cc80be060b307155c7bc7176`
- `ae1e76241d7aa6339fa00478a8e5b79d6fd31b5c074f56a40f76a4907d327d81`

The 68-test Windows run includes controlled lost-response, interrupted
transport, per-request operation exclusion, unavailable support and timeout
classification cases, plus real Git/native-hook boundaries. These controlled
cases are not claims of deliberately losing a live provider response.
The earlier malformed request separately proved admission refusal without
starting Windows. The original legacy-hook-rejected requests remain retained
and unreplayed, rather than relabelled as successful submissions.

Validation tiers: focused units, real local Git/native-hook integration,
fresh disposable hosted Windows execution, and the actual external
submit/status/wait/result flow all ran. No shared runtime rollout or
productive-session test was needed or performed. TESTING.md remains the
authoritative operating guide; this archive records acceptance, not another
command surface.

The escape hatch's completion gate is met. Returned to #10765 by submitting
its exact retained source to the named capture packet, request
`f228a2d79c9740a183ab6d5d5b02e076`. That request and the parent remain open;
packet acceptance and eventual source-workspace retirement stay with #10765.
