# Gateway Native Qualification

- **Slug:** `gateway-native-qualification`
- **Repo:** copilot-extensions
- **Branch(es):** serial reviewed slices against `dev`
- **Created:** 2026-10-20
- **Status:** Active
- **Vision:** [Declarative Fleet Gateway](../../../visions/machine-fleet/gateway/README.md), `thin-hosting-wrappers`
- **Umbrella issue:** #7257
- **Parent effort:** [Fleet Control V2](../fleet-control-v2/README.md), #6236
- **Predecessor:** [Gateway Role Tunnels](../../2026/10/19%20gateway-role-tunnels/README.md)

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
changed by discovery. The [living readiness record](../../../fleet-manager/docs/gateway-adoption-readiness.md)
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
- [ ] Land this plan through ordinary current-head review and merge.
- [ ] Preserve the completed predecessor and record the admitted native venue
  and exact source ownership without publishing private inventory.

### Phase 1 - Explicit native architecture selection
- [ ] Add explicit bounded amd64/ARM64 selection to the existing runner,
  retaining amd64 as its unchanged default and refusing unknown selections.
- [ ] Require selected architecture, native Linux engine, built image and actual
  installed-process architecture agreement. Normalize documented aliases only;
  do not infer native execution from Buildx platform advertisement or image tags.
- [ ] Make build platform, source/image receipts, pre-execution state and final
  report truthful for either architecture. Mismatch fails without a PASS or
  network/service composition; failed cleanup cannot become successful proof.
- [ ] Keep any new helper in the exact Git source closure, preserving canonical
  build resources and the existing command/resource/credential boundaries.
- [ ] Add focused regression coverage and update the existing witness/image/
  readiness documentation without growing unconditional CI integration lanes.

### Phase 2 - Actual native qualification
- [ ] Run the exact committed candidate on the admitted native amd64 engine,
  checking the existing default and all assembled contract stages.
- [ ] Execute the same committed candidate on the explicitly admitted native
  ARM64 venue, with installed wheel and actual Gateway/two-service behavior.
- [ ] Inspect architecture, source/image and stable Gateway process receipts,
  all ten stage outcomes and exact-owned cleanup on each venue. Retain private
  results and publish only a generic, non-secret conclusion.
- [ ] Preserve truthful failures; do not install emulation, weaken deadlines,
  touch unrelated workloads or claim a live target ready to force completion.

### Completion
- [ ] Resolve every Plan/Validation item or transfer a concrete residual to a
  named tracked objective before marking Done and archiving.
- [ ] Promote durable architecture truth into the living readiness docs, retaining
  #7257 provider/live-edge gates and existing lifecycle/deployment ownership.
- [ ] Merge the implementation and completion record, settle owned remote/local
  resources, release effort focus and finalize the source worktree.

## Validation Plan

- [ ] **Selection:** default amd64 compatibility, explicit amd64/ARM64 and
  supported aliases, unsupported platforms/OS and wrong-engine refusal.
- [ ] **Build:** selected native platform reaches the Docker build; wrong
  image architecture, OS or source revision refuses before service composition.
- [ ] **Execution:** installed process machine/platform must match the selected
  native architecture before native execution is reported.
- [ ] **Custody:** exact-source helpers and canonical resources remain complete;
  existing source/argv/private-results/deadline/termination/cleanup guards pass.
- [ ] **Native amd64:** actual assembled API/HTML/SSE, policy, role/caller denial,
  replacement, restart/reconnect and revocation pass under stable Gateway identity.
- [ ] **Native ARM64:** the same real assembled assertions pass on native ARM64,
  not emulation, with source/image/process evidence and complete exact-ID cleanup.
- [ ] **Contribution:** focused bounded tests, lint, relevant repository guards,
  complete current-head review and actual merge cover the changed source.
- [ ] **Boundary:** no production identity, key, DNS, Access, firewall, service,
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
