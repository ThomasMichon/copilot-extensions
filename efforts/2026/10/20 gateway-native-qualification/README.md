# Gateway Native Qualification

- **Slug:** `gateway-native-qualification`
- **Repo:** copilot-extensions
- **Branch(es):** serial reviewed slices against `dev`
- **Created:** 2026-10-20
- **Status:** Done
- **Vision:** [Declarative Fleet Gateway](../../../../visions/machine-fleet/gateway/README.md), `thin-hosting-wrappers`
- **Umbrella issue:** #7257
- **Parent effort:** [Fleet Control V2](../../../active/fleet-control-v2/README.md), #6236
- **Predecessor:** [Gateway Role Tunnels](../19%20gateway-role-tunnels/README.md)

## Guiding Intent

Close the initial Gateway image's native architecture qualification gap using
the existing exact-source, isolated container witness. A selected architecture,
the native engine, the inspected image and the actual installed process must
agree before a native result is reported. Qualify ARM64 without pretending that
cross-build advertisement, emulation or an amd64 result is native ARM64 evidence.

This is a finite qualification campaign, not live Gateway adoption. The completed
initial effort remains archived; its provider, lifecycle and deployment owners
retain their responsibilities.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Qualification coordinator | Owns interfaces, source, public coordination, PRs and completion | Managed source worktree; #7257 |
| Native amd64 qualifier | Executes the unchanged-default architecture path with synthetic inputs | Existing admitted local Linux engine |
| Native ARM64 qualifier | Executes the same exact-source contract on the admitted native venue | Explicitly authorized native ARM64 execution venue |
| Adopter | Owns provider identity, capacity, credentials, placement and live activation | Separate adopter approval |

## Coordination

- **Topology:** reviewed plan, then one coherent witness/test/documentation
  implementation and actual native proofs, followed by the completion record.
- **Host (owns PRs):** Qualification coordinator. A remote qualifier executes
  only its assigned exact candidate and returns compact evidence; it does not
  edit shared source, open a competing PR or change a deployed runtime.
- **Exclusive source:** `tools/clean-room/gateway-role-tunnels/`, its focused
  runner tests, and directly affected Gateway qualification/readiness docs.
- **Compatibility:** preserve the existing amd64 default, report top-level
  vocabulary, private results custody, source allowlist, deadlines, runtime
  container bounds, exact-ID teardown and unchanged Gateway runtime.
- **Retained owners:** #5789/#6804 authentication/provider profiles;
  #6240/#6424 accepted-revision/install lifecycle; #6030 placement/adoption;
  #6236 the integrated product. No owner transfer is implied.
- **Handoff:** resume the first unresolved Plan/Validation item in this README.
  Actual native execution and complete cleanup, not a mocked guard or merged
  prerequisite, determine qualification completion.

## Context

The initial campaign delivered the image and separate-container routing proof
on native amd64, plus generic dual-TLS ingress evidence. The current witness
hardcodes amd64 in engine admission, build/inspection, installed-process
qualification and reporting. Its deliberate refusal on another engine is the
concrete architecture gate transferred to #7257, not an image failure.

Read-only venue inspection established an available native ARM64 Linux engine
with ordinary non-root Docker access and sufficient available local resources
for the bounded proof. No build, container, service or host configuration was
changed by discovery. The [living readiness record](../../../../fleet-manager/docs/gateway-adoption-readiness.md)
continues to distinguish native image execution from a production-ready target.

## Request

Operator direction, verbatim:

> Great. Continue driving

The operator confirmed `gateway-native-qualification` and authorized the
bounded isolated native ARM64 proof. **Public-safe scope summary** (private
venue identity omitted): build the exact candidate with a finite timeout;
run a synthetic Gateway, two services and observer on an internal network;
limit each runtime container to 256 MiB, one CPU and 64 PIDs; restart only one
run-owned test service; stop/remove only receipted run-owned containers/network.
Retain the image and private proof results.

No production mounts, published ports, real credentials, production service
restarts, privileged emulation, enrollment, provider changes or deployment are
authorized. These limits also preserve the existing local amd64 proof boundary.
Detailed design, sequence and validation below are **agent-recommended**.

## Plan

### Phase 0 - Review the finite qualification contract
- [x] Land this plan through ordinary current-head review and merge.
- [x] Preserve the completed predecessor and record the admitted native venue
  and exact source ownership without publishing private inventory.

### Phase 1 - Explicit native architecture selection
- [x] Add explicit bounded amd64/ARM64 selection to the existing runner,
  retaining amd64 as its unchanged default and refusing unknown selections.
- [x] Require selected architecture, native Linux engine, built image and actual
  installed-process architecture agreement. Normalize documented aliases only;
  do not infer native execution from Buildx platform advertisement or image tags.
- [x] Make build platform, source/image receipts, pre-execution state and final
  report truthful for either architecture. Mismatch fails without a native-execution PASS or
  network/service composition; failed cleanup cannot become successful proof.
- [x] Keep any new helper in the exact Git source closure, preserving canonical
  build resources and the existing command/resource/credential boundaries.
- [x] Add focused regression coverage and update the existing witness/image/
  readiness documentation without growing unconditional CI integration lanes.

### Phase 2 - Actual native qualification
- [x] Run the exact committed candidate on the admitted native amd64 engine,
  checking the existing default and all assembled contract stages.
- [x] Execute the same committed candidate on the explicitly admitted native
  ARM64 venue, with installed wheel and actual Gateway/two-service behavior.
- [x] Inspect architecture, source/image and stable Gateway process receipts,
  all ten stage outcomes and exact-owned cleanup on each venue. Retain private
  results and publish only a generic, non-secret conclusion.
- [x] Preserve truthful failures; do not install emulation, weaken deadlines,
  touch unrelated workloads or claim a live target ready to force completion.

### Completion
- [x] Resolve every Plan/Validation item or transfer a concrete residual to a
  named tracked objective before marking Done and archiving.
- [x] Promote durable architecture truth into the living readiness docs, retaining
  #7257 provider/live-edge gates and existing lifecycle/deployment ownership.
- [x] Merge the implementation and completion record, settle owned remote/local
  resources, release effort focus and finalize the source worktree.

## Validation Plan

- [x] **Selection:** default amd64 compatibility, explicit amd64/ARM64 and
  supported aliases, unsupported platforms/OS and wrong-engine refusal.
- [x] **Build:** selected native platform reaches the Docker build; wrong
  image architecture, OS or source revision refuses before service composition.
- [x] **Execution:** installed process machine/platform must match the selected
  native architecture before native execution is reported.
- [x] **Custody:** exact-source helpers and canonical resources remain complete;
  existing source/argv/private-results/deadline/termination/cleanup guards pass.
- [x] **Native amd64:** actual assembled API/HTML/SSE, policy, role/caller denial,
  replacement, restart/reconnect and revocation pass under stable Gateway identity.
- [x] **Native ARM64:** the same real assembled assertions pass on native ARM64,
  not emulation, with source/image/process evidence and complete exact-ID cleanup.
- [x] **Contribution:** focused bounded tests, lint, relevant repository guards,
  complete current-head review and actual merge cover the changed source.
- [x] **Boundary:** no production identity, key, DNS, Access, firewall, service,
  enrollment or deployment change is included or implied.

## Proposal

Extend the existing runner with one explicit native architecture choice and a
shared small architecture-normalization/checking boundary. Keep the current
report/receipt owners and selected-source materialization; do not add a second
runner, remote Docker context, installer or supervisor.

Native venue preparation uses its registered repository/worktree and the exact
committed source, not a source overlay or deployment into runtime directories.
Remote execution remains a bounded assigned qualification operation. A successful
ARM64 result closes only this image/runtime qualification gap; supported
cloudflared validation, Gateway-owned Entra/Authelia interoperability, durable
revision custody and real adoption remain separately tracked.

## Journal

### 2026-10-20 - Follow-on intake and bounded authorization
- Operator requested continued driving after the predecessor's reviewed closure.
- Rechecked #7257 and retained #5789/#6804 coordination, searched current open
  work, and reserved the existing qualifier/test/docs scope without replacing
  the retained authentication/provider owner.
- Read-only inspection established native ARM64 Linux/Docker capability. The
  operator approved the canonical slug and bounded synthetic execution scope.
- Reused and synchronized the owned source worktree. Native witness code and
  proof execution remain pending this plan's review; no runtime was deployed.

### 2026-10-20 - Plan reviewed; native selection implementation
- Plan PR #7390 merged as `f3d585d79d0955bd788f14501e81e74b1bc5424b`
  after exact-head Copilot approval and successful required CI.
- Re-read the canonical effort and selected Phase 1. Extend only the existing
  witness's native architecture contract and its exact-source closure; real
  amd64/ARM64 proof and implementation review remain pending.
- Added explicit selection and engine/image/installed-process agreement with
  pre-execution `UNRUN` receipts and value-free wrong-architecture refusals.
  The new helper is part of the fixed Git-only witness source closure.
- The focused native-selection and retained runner/probe portfolio passes
  eighty-eight cases. Lint, install-contract and documentation consistency
  guards pass. These are mechanical guards, not native execution evidence.

### 2026-10-20 - Exact-source amd64 proof; ARM64 qualification assigned
- Candidate `7a6e0614034814708297935660866b8e3eb7186d` passed all ten
  assembled stages on native Linux amd64. Selected architecture, engine,
  inspected image and installed process agreed; seven Gateway identity samples
  were identical and exact-owned cleanup completed without errors.
- Assigned that same committed candidate to the explicitly admitted native
  ARM64 qualifier. Retained allocation and conversation identity through a
  supported startup-registration recovery; durable task admission is not
  proof execution. Actual ARM64 receipts remain pending.
- Private run results and image IDs stay with the executing owners. No
  production configuration, identity enrollment or runtime deployment changed.

### 2026-10-20 - Native ARM64 proof independently verified
- The same candidate passed all ten stages on an actual Linux/aarch64 engine,
  with an inspected ARM64 image and noneditable installed wheel. Engine, image
  and installed-process architecture agreed; no emulation was used.
- The coordinator independently verified report/receipt hashes, exact source
  and source-manifest custody, retained image identity/revision, unfinished
  backend SSE and all stage outcomes. Seven Gateway identity samples matched.
  All six receipted containers and the one receipted network were independently
  confirmed absent; cleanup was complete with no errors.
- Updated the living readiness and image qualification boundaries for both
  native architectures. Implementation review/merge and final resource
  settlement remain open; provider/live-edge, lifecycle and adoption owners
  retain their existing gates.

### 2026-10-20 - Qualified implementation reviewed and merged
- PR #7393 merged as `05dcc4e8149d486439cf9e373bca969ec3fa9947`
  after exact-head Copilot approval with no findings and successful required
  CI. The final documentation-only delta did not change the qualified witness,
  image inputs or runtime source.
- Re-read this effort's completion gate after merge. Actual native proof and
  contribution validation are complete; the closing record, remote task and
  retained-source settlement remain the next bounded leg.

### 2026-10-20 - Finite qualification complete
- Confirmed the native qualifier's submitted result after independent host
  verification and implementation merge. Normal landed-content checks
  finalized its source and controller worktrees; explicitly authorized
  exact-session retirement and exact-ID worktree cleanup completed.
- Retained private receipts and both qualified images intentionally. No proof
  containers or test network remain, no worker is left running, and no
  production rollout was performed.
- All qualification Plan and Validation items are resolved. Archive this
  completion record through the ordinary review/merge gate, then release
  coordinator focus and finalize its source worktree.
- #7257 retains supported vendor ingress and Gateway-owned provider
  interoperability. #5789/#6804 retain authentication/provider ownership,
  #6240/#6424 retain accepted-revision/install lifecycle, #6030 retains
  placement/adoption, and #6236 remains the broader integrated product.
