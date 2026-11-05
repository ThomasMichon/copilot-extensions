# Upgrade Recovery Acceptance

[Canonical effort](README.md) | [Workstream issue](https://github.com/ThomasMichon/copilot-extensions/issues/10377)

## Authority and selection

Evaluate every case against
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
and the owning [install contract](../../../docs/install-contract.md).
This matrix is a source/contained-consumer plan, not permission to repair an
installed host or a claim on another contributor's code.

Before selecting a unit, record the exact latest source and relevant released
artifact, original failure, current issue/PR owner and actual supported entry.
Historical symptoms may already be fixed. Add an issue only for a remaining
generic gap with no existing tracker; retain the existing tracker otherwise.

## Initial candidate families

| Case | Affected before-state and entry | Required evidence | Existing coordination |
|---|---|---|---|
| U1 - Update reachability | Installed Python, environment, command or Manager is unavailable; invoke the ordinary repository update or standalone entry. | Fresh attributable incoming code runs before broken installed code; forwarding and exact failures survive. Ordinary non-update behavior is unchanged. | #7743; preserve its entry/stdio/settlement owners and #1238 command ownership. |
| U2 - Explicit versus automatic | Automatic update is inhibited, with otherwise valid incoming inputs; compare explicit update with its automatic caller. | Explicit replacement proceeds without clearing the preference; the automatic path remains inhibited. Preserve classification through every reached boundary. | Owning update request/entry paths. #7030 retains the distinct managed-publication policy; it is not an automatic KillSwitch substitute. |
| U3 - Construction compatibility | An affected runtime needs construction with supported UV, native dependency/bootstrap or source inputs. | Supported native arguments and immutable input policy; real incoming health before selection; actionable bounded errors and settled children on failure. | #9489 and canonical construction owners; #9501 is already merged. |
| U4 - Existing installer state | Existing installation requests, incomplete foundation selection, stale transaction or publication records. | The explicit latest entry reaches ready/current incoming state without old recovery permission. Preserve original request/effect history; coordinate concrete writers by bounded native means, not persistent queuing. | #9448 and #7758. Existing refusal controls describe old behavior, not an exemption from authoritative replacement. |
| U5 - Historical generated integration | Prior generated command, launcher, provider, task or service registration differs from the incoming producer. | Applicable current registrations select incoming code; user state and unrelated/protected execution survive. Unprovable old central cleanup is separately reported, not an incoming veto. | #6820, #10180 and per-component registration/lifecycle owners. |
| U6 - Source and platform identity | Retained source-qualified or marker-only slots, changed source assembly, and Windows/POSIX path/layout differences. | Select actual admitted incoming identity without mutable-source references; invoke installed consumers with the source unavailable. No version-blind ready result or guessed compatibility. | #7028, #9290 and owning native resolver/bootstrap families; classify a concrete case before implementation. |
| U7 - Current serving generation | An old central service remains reachable after incoming selection. | Verify actual required incoming serving identity and routes; close/exclude conflicting admission where needed. Final predecessor drain/exit is independent; keep session hosts, keepers and connectivity out of retirement. | #6820 and #3491 lifecycle/adopter acceptance; no takeover of their serving transition. |
| U8 - Manifest-only current shortcut | A matching deploy manifest survives a missing interpreter, incomplete selected slot or wrong current selector; invoke ordinary selected-target update. | Reach the normal owning installer, preserve configuration, keep failed repair failed, then invoke usable selected Python and reuse unchanged healthy slot bytes on repeat. Retain source/profile and selector-free legacy controls. | Bounded non-serving footprint claim on #6389 under #10377; service and retirement owners remain separate. |
| U9 - Distinct-generation producer repair | Codespaces normal install/update reaches a selected incomplete or completed-but-unlaunchable same-version identity. | Use existing construction/retention owners to build and health-gate a distinct generation, preserve old-slot/state bytes and publish applicable selections. Invoke usable selected CLI, reuse healthy repair on repeat and keep actual incoming failures failed. | Bounded Codespaces adapter claim on #5472 under #10377; no shared admission/guardian, watchdog, diagnostics or service-owner takeover. |
| U10 - Windows venue fallback path | Ordinary preparation retains a long Git-local private installation home that exists but cannot be a native process working directory. | Prepare a compact full-source-identity home through the normal entry; publish usable payloads, preserve prior bytes/source declarations, reuse on repeat, and refuse genuinely unsupported depth before installation/publication. | #10451 under #10377; #8242 cache/inventory, transport/closure owners and parked installation-cell migration remain separate. |
| U11 - Automatic launch source freshness | A newer locally sourced/selected runtime coexists with an older marketplace payload, or a cached launch action outlives its payload/source/selector facts. | Use one read-only source-qualified decision at staging/planning and actual apply; preserve installed/serving freshness, same-version independent sources, ordinary forward updates, payload refresh and explicit update/recovery. Refusal leaves runtime inventory unchanged. | Bounded #7030/#2404 follow-up under #10377, source handle `dusty-contraption`; ROOT and native application/cache-consumer ownership remain separate. |
| U12 - MCP fixed-recipe prerequisite | MCP's native `init` actions lack fixed first-use admission; cold provision must acquire its canonical tools and join the existing authenticated installation owner. | Real init-backed stamp/provision, durable original queue/result, native participant admission at default/explicit execution roots, installed consumers, healthy reuse and real configuration preservation on incoming failure. | Bounded #10499 prerequisite under #10377/#7758; ordinary publishers, production caller-Job loss, live queue recovery and existing construction/application owners remain separate. |

These are candidate families, not assertions that each needs a new defect or
issue. Each implementation unit must identify a
specific failing boundary, supported contract and unconflicted owner.

## Required controls for a selected case

| Control | Observable postcondition |
|---|---|
| Affected prior footprint | Latest supported real entry recovers the selected incompatibility; no manual deletion, receipt fabrication or broad cache reset. |
| Fresh first-touch | The same implementation still supports clean installation, but that result is recorded separately. |
| Healthy current installation | Reuse/currentness is truthful and does not reconstruct unchanged healthy work unnecessarily. |
| Immediate rerun | The final state remains usable and current without repeated migration or another hidden repair step. |
| Failed incoming preparation | No unhealthy candidate is selected or advertised as whole-set complete; useful error and partial-outcome evidence survive. |
| Durable user state | Configuration, credentials, repositories, worktrees, sessions, databases and semantic effect history retain their meaning and bytes where no owning migration is required. |
| Supported mixed versions | Current/prior caller and provider paths use documented contracts; unsupported mandatory semantics are explicit before effects, not ignored. |
| Protected execution | Foreign/session-owned work and connectivity are not retired by a central-service cleanup fallback. Revalidate then-current central generation at mutation. |
| Real I/O or permission failure | Report failure honestly; do not turn an actual denied write into a stale-metadata success case. |
| Final consumer | Invoke the actual installed command or serving boundary and check all applicable current selections, not just a completion marker or package version. |

## Evidence and completion

For each selected case, append the issue and exact source/release references,
fixture shape, invoked public entry, native result, preservation assertions,
remaining unsupported boundaries and explicit lane omissions to the effort
journal or a bounded linked case record.

Source merge, containing release, installed selection, required service
readiness and affected-host recovery are separate facts. Retain the parent
tracker across partial slices. No record here authorizes host rollout, new
capacity, authentication, process cleanup or a broader support-floor change.

## Selected case outcomes

| Case | Source and release | Qualified consumer boundary | Remaining acceptance |
|---|---|---|---|
| U8 | #10398 merged as `fbcbf22b0ef518519daf67f865a2f9b551f7af89`; containing release #10430 verified with matching planner bytes. Joined packet #10448 merged as `7fa780be4f7792d4453e6233a8e387b146e88750` and released through #10452. | 257 focused passes/three platform skips; two additional real outer collected-update/production Codespaces cases prove repair, current repeat and failed incoming preservation. | Repository-shell fresh bootstrap, other production installers and required serving state are not implied. |
| U9 | #10426 merged as `8d71dd2f275c3289801767440a0adef0b2abf162`; containing release #10449 verified, Codespaces `0.5.248-dev1`. Joined packet #10448 is released through #10452. | 141 focused passes/twelve platform skips plus four native completion/publication controls and the two joined outer-consumer cases; real disposable venv, producer and generated CLI. | Native macOS/Bash 3.2 and live feed/service/affected-host lanes remain unperformed and separately owned. |
| U10 | #10456 merged as `affb395f47114f88671c4aecd75c17b1000b74bd`; exact-source promotion 41857182391 and release #10463 verified. #10451 is closed. | Seven focused native/identity controls and 234 existing preparation regressions pass. Normal preparation/register/repeat use an independent synthetic installer and actual native cwd launch; old corrupt receipt/payload bytes survive. Canonical and materialized Bridge resolver bytes match the released source. | Native POSIX, live Copilot acquisition/hooks and installed-host rollout are unperformed; unsupported Git-metadata depth remains an actionable pre-effect failure. |
| U11 | #10508 merged as `7ebeee4b2b46fcea8eb06b0df418651ca1f416b0` from approved head `7f3f8e2920`. Release #10548 publishes qualified Dev `20c140f142b273ec132b8d740240ed0dd3135e02` as Main `0adc4e2e8284bdbd72b8a99cbb4172841aac2088`, Worktrees `1.24.468-dev1`; all six changed production blobs match. | Forty-six new freshness consumer cases, 109 retained cases, 75 staging/monotonic controls and seventeen pure units pass at their recorded revisions. Six dependency controls additionally pass under actual Python 3.10; exact-head hosted CI qualifies 4,108 units and all 545 dependency declarations. | Synthetic lookup completion/interpreter observations do not prove installed health. ROOT, native application/cache consumers, full ordinary-update qualification and affected-host rollout remain with #7030/#7028 and their existing owners. No installation was updated by release observation. |
| U12 | #10574 merged as `322a4e5adebcd066a920ca52c5e808d41fe1d617`; containing release still pending. | Exact-head Windows/Linux run `41973788482` passes all MCP protocol/refusal, materialized stamp/prerequisite, actual native participant, shared-receiver and cold construction/consumer/reuse/failure-preservation steps. Installed configuration loading checks the actual `bridges` directory; no server or authentication provider is started. Current-head clean review and both required CI runs pass. | Contained caller-process loss is not production caller-Job loss. Ordinary bootstrap/command/explicit-installer adoption, existing fenced-queue recovery, macOS and affected-host rollout remain unqualified under #10499/#7758/#7028 and the parent effort. |

## Reconciled existing construction owners

The unsupported immutable-UV option correction is delivered in #9501, whose
merge is contained in the actual qualified Dev for #10463. Its exact-head
Windows/Linux bootstrap and UV lanes are recorded on that original PR, not
reclassified as new executions here. Four current contained native source-build
controls pass in 187.69 seconds with real UV and a synthetic backend. They
exercise fresh/warm construction, leaf-only source invalidation, request-witness
and Job-scope key stability, and meaningful build-environment invalidation.
These are constructor consumers, not a complete ordinary suite update.

#9290 retains its existing lifecycle/publication-envelope correction and the
separate promoted-input/profile work. #9572 fixes Job-custody identity only;
the four controls do not vary lifecycle tickets or prove the complete reported
cache/deadline recovery. Coordination is retained in
[comment 6635053007](https://github.com/ThomasMichon/copilot-extensions/issues/9290#issuecomment-6635053007).
#7743/#7758/#9448 retain fresh-entry and existing-request recovery. #3004
retains actual first-restarted-model knowledge prompting; the closed #1159
write guard is not that acceptance. None of these owner records authorizes an
installed retry or supersedes an open contributor implementation.
