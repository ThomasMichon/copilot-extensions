# Windows Qualification Escape Hatch

- **Slug:** `windows-qualification-escape-hatch`
- **Repo:** copilot-extensions
- **Branch(es):** serial managed proposal and implementation PRs
- **Created:** 2026-11-06
- **Status:** Active
- **Vision:** [Test portfolio effectiveness](../../../visions/test-portfolio/README.md)
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
It follows the [architecture principles](../../../docs/patterns/README.md)
of a right-sized surface and attributable invocation, plus
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
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
- [ ] Prove the complete local submit, hosted run, status and result path on a
  real focused Windows consumer, not a synthetic success marker.
- [ ] Publish and merge the complete flow through normal review and required CI.

### Phase 3 - Qualify reuse and close the effort

- [ ] Prove repeated and concurrent submissions do not create per-run branches
  or lose an older requested source when the transport ref advances.
- [ ] Prove denied admission, failed tests, empty selections, interrupted
  submission, unknown dispatch and handled timeout retain truthful outcomes.
- [ ] Document the actual command, source/ref retention and evidence limits in
  TESTING.md and contributor guidance.
- [ ] Confirm the workflow reaches the default branch through normal promotion
  and demonstrate actual hosted execution of that released control.
- [ ] Resolve every Plan/Validation item, mark Done, archive and finalize.
- [ ] Return to the still-open Driver effort's exact-source qualification gate.

## Validation Plan

- [ ] Unit controls reject non-owner/rerun/non-control-ref admission, malformed
  requests, unsafe paths, arbitrary commands and implicit whole-suite requests.
- [ ] Real Git controls preserve exact source identity, local branch/index/tree,
  old requests and unrelated refs across shared-ref contention and bounded retry.
- [ ] The actual request/result client distinguishes accepted, pending, unknown,
  failed and qualified outcomes and never silently resubmits unknown effects.
- [ ] Missing or unsupported hosted control is reported before staging or
  dispatch; supported caller/controller versions retain their declared meaning.
- [ ] Focused test identities, selection, skips, source SHA, workflow control,
  platform and artifact identity remain explicit in results.
- [ ] The worker invokes the existing canonical bounded runner and capture
  paths; preparation stays inside any existing packet aggregate deadline.
- [ ] A real Windows run proves a focused consumer, successful artifact return
  and a handled failing/invalid request without local test execution.
- [ ] Repeated source submissions use only the one reserved transport ref;
  no source feature branch or default branch is rewritten or merged.
- [ ] Required source checks, code review, CI, promotion and final live
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
