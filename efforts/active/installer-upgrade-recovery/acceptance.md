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
| U2 - Explicit versus automatic | Automatic update is inhibited, with otherwise valid incoming inputs; compare explicit update with its automatic caller, including automatic `--force` and held clients. | Explicit replacement proceeds without clearing the preference; the automatic path remains inhibited even with `--force`. Preserve classification through every reached boundary. Without inhibition, automatic `--force` must still preserve session hosts, keepers, held clients and protected connectivity; neither automatic nor explicit force authorizes unsafe retirement. | Owning update request/entry paths; #10653 tracks unsafe forced cutover. #7030 retains the distinct managed-publication policy; it is not an automatic KillSwitch substitute. |
| U3 - Construction compatibility | An affected runtime needs construction with supported UV, native dependency/bootstrap or source inputs. | Supported native arguments and immutable input policy; real incoming health before selection; actionable bounded errors and settled children on failure. | #9489 and canonical construction owners; #9501 is already merged. |
| U4 - Existing installer state | Existing installation requests, incomplete foundation selection, stale transaction or publication records. | The explicit latest entry reaches ready/current incoming state without old recovery permission. Preserve original request/effect history; coordinate concrete writers by bounded native means, not persistent queuing. | #9448 and #7758. Existing refusal controls describe old behavior, not an exemption from authoritative replacement. |
| U5 - Historical generated integration | Prior generated command, launcher, provider, task or service registration differs from the incoming producer. | Applicable current registrations select incoming code; user state and unrelated/protected execution survive. Unprovable old central cleanup is separately reported, not an incoming veto. | #6820, #10180 and per-component registration/lifecycle owners. |
| U6 - Source and platform identity | Retained source-qualified or marker-only slots, changed source assembly, and Windows/POSIX path/layout differences. | Select actual admitted incoming identity without mutable-source references; invoke installed consumers with the source unavailable. No version-blind ready result or guessed compatibility. | #7028, #9290 and owning native resolver/bootstrap families; classify a concrete case before implementation. |
| U7 - Current serving generation | An old central service remains reachable and capable of serving after incoming selection. | Verify actual required incoming serving identity and every applicable current route; fresh calls must reach the incoming generation, not merely a capable old server. Transfer/exclude conflicting fresh admission where needed. Existing stateful clients and old-code keepers may survive; final predecessor drain/exit, census and cleanup are independent. | Reuse #6389 for stale-capable-serving/fresh-admission acceptance under #10377. Retain #6820/#3491 lifecycle/adopter ownership and #9716 census ownership. #10486 full adopted-client reattachment remains separate, not an incoming prerequisite. |
| U8 - Manifest-only current shortcut | A matching deploy manifest survives a missing interpreter, incomplete selected slot or wrong current selector; invoke ordinary selected-target update. | Reach the normal owning installer, preserve configuration, keep failed repair failed, then invoke usable selected Python and reuse unchanged healthy slot bytes on repeat. Retain source/profile and selector-free legacy controls. | Bounded non-serving footprint claim on #6389 under #10377; service and retirement owners remain separate. |
| U9 - Distinct-generation producer repair | Codespaces normal install/update reaches a selected incomplete or completed-but-unlaunchable same-version identity. | Use existing construction/retention owners to build and health-gate a distinct generation, preserve old-slot/state bytes and publish applicable selections. Invoke usable selected CLI, reuse healthy repair on repeat and keep actual incoming failures failed. | Bounded Codespaces adapter claim on #5472 under #10377; no shared admission/guardian, watchdog, diagnostics or service-owner takeover. |
| U10 - Windows venue fallback path | Ordinary preparation retains a long Git-local private installation home that exists but cannot be a native process working directory. | Prepare a compact full-source-identity home through the normal entry; publish usable payloads, preserve prior bytes/source declarations, reuse on repeat, and refuse genuinely unsupported depth before installation/publication. | #10451 under #10377; #8242 cache/inventory, transport/closure owners and parked installation-cell migration remain separate. |
| U11 - Automatic launch source freshness | A newer locally sourced/selected runtime coexists with an older marketplace payload, or a cached launch action outlives its payload/source/selector facts. | Use one read-only source-qualified decision at staging/planning and actual apply; preserve installed/serving freshness, same-version independent sources, ordinary forward updates, payload refresh and explicit update/recovery. Refusal leaves runtime inventory unchanged. | Bounded #7030/#2404 follow-up under #10377, source handle `dusty-contraption`; ROOT and native application/cache-consumer ownership remain separate. |
| U12 - MCP fixed-recipe prerequisite | MCP's native `init` actions lack fixed first-use admission; cold provision must acquire its canonical tools and join the existing authenticated installation owner. | Real init-backed stamp/provision, durable original queue/result, native participant admission at default/explicit execution roots, installed consumers, healthy reuse and real configuration preservation on incoming failure. | Bounded #10499 prerequisite under #10377/#7758; ordinary publishers, production caller-Job loss, live queue recovery and existing construction/application owners remain separate. |
| U13 - MCP ordinary-entry fresh settlement | Exercise supported native Windows/POSIX ordinary bootstrap/command and explicit init/install/update entries with old cleanup, census or control-home state still unresolved. | Authentic incoming runtime readiness, every applicable current selector/command/route/registration and fresh calls reaching that generation establish incoming completion. Old installer-request recovery, control-home idleness, cleanup provenance and full client handover are not prerequisites. Necessary state-preserving compatibility/migration and transfer/exclusion of conflicting work admission remain readiness gates. Use an owned incoming namespace and real permissions, not takeover of an unknown foreign listener. Record platform results and omissions separately. | #10652 under coordinator #10377; retain #10054/#10499/#7758/#7028 application, execution and installation owners, #9716 census ownership and #10486 separate full reattachment. U12's delivered source/release results remain unchanged; this row claims no implementation or qualification. |
| U14 - MCP legacy bootstrap adoption | Session-start still runs direct stamp and legacy conhost/nohup drift reconcile with PID-age ownership despite the released fixed recipes. | Actual first stamp/drift provision through the original queue, unchanged hook deadline/output, source-aware readiness, configuration/installed consumers and production submitting-Job/group-loss proof with outer cleanup retained. | Bounded #10499 follow-up under #10377/#7758; [review-first proposal](../source-managed-runtime-installs/mcp-bootstrap.md). Commands, explicit installers, other migrations, live queue recovery and incumbent construction/application/lifetime owners remain separate. |
| U15 - MCP held-session drain truthfulness | Legacy control-client drain converts a nonzero attached-session count to successful drain when force reaches its timeout. | Force cannot supply zero-session proof. Exercise the real loopback control reply and direct orchestrator rollback/retirement decision; preserve a zero-session success control. | Source #10736 merged as `3389a4e16f`: 41 controls pass with eight platform skips, actual approval and full CI. Normal containing release #10825 merges as `b408a6500b`; published MCP `0.2.189-dev1` archive/index digests and four nested-wheel implementation bodies match the qualified source. No installed update or whole-flow recovery is credited. U13 incoming publication, broader #10653 reaping/classification, #10486 handover and #6928 watchdog retain their owners. Recurring verifier initialization remains #10651; both that issue and this parent stay open. |
| U16 - Canonical bound-knowledge discovery | Existing canonical external-state registration lacks an authored self-entry, or authored topology assigns that same name stale remote routing; actual related readers and briefing producers omit the binding or select ordinary editing. | Actual bare/linked native list/resolve, aligned-only policy, complete consumer markers and exact topology/configuration preservation. Refuse synthesized writer/upsert/scaffolder writes, including existing-key and force cases. Discovery is not pair readiness or permission to edit an anchor. | Bounded #1419 K3/K5 source #10956 merges as `27af49e7ea` after current-head review and required CI. Exact-source hosted `42204493016` qualifies Linux 22 discovery +159 consumers and Windows 22 +151 with eight explicit Bash-only skips covered on Linux; both native modes and former CLI timeout cases pass on both platforms at unchanged limits. Normal release #10991 admits containing Dev `8073d6b937`, publishes Main `f921808d22` and Worktrees `1.24.504-dev1`; fifteen defining bodies match exactly. K1 retrofit/feasibility, K2 overlay, actual K4 write/cleanup, full receiving lifecycle and this parent remain open. No installed result or rollout is credited. |
| U17 - Native registration inspection | A setup caller must select one registered project and its exposure from canonical and legacy adoption records before registration effects; the old reverse lookup selects the first match and hides read errors. | Compose existing native readers into one versioned read-only CLI contract. Return the exact unique identity/policy or a genuinely unclaimed fresh default; refuse ambiguity, inconsistent roots, collisions and malformed/unknown acquisition. Prove real parser/process behavior and exact no-mutation preservation. | Bounded #10996 under #10377, source handle `dusty-contraption`; plan #10997 merged `3ca033f946`. Combined source `8c79b42dec` independently qualifies 39 Linux/39 Windows in `42269611394`, with exact source pins, identical complete downloaded sets and zero skips/errors. Full `42269629159` passes all 252 capture and 252 independent-verification cases inside the original 240-second POSIX caller bound, plus all original 115 and three locator controls, 118 total. Every incoming case and group/item/resource limit remains. The native publication owner verifies 3,074 preimages and materializes all 140 actual outputs; every byte matches, checker passes 2 contracts/1,051 fixtures and HTTP-51 is untouched. Earlier 0055/245 and other captures retain their actual source attribution. Final metadata-only `e6715eb2d9` passes required CI `42272970981` and exact-head Approved review; both threads resolve. #11003 merges as `82bbc54186`. Containing promotion `42275291989` explicitly admits that source and succeeds; #11071 merges generated candidate `565db90d18` to Main `3a9a766662` at 19:14:41 UTC. The published `python-artifacts-82bbc541865e6f5e79d065201dd19190e603dac4` assets and exact released reader blob are verified; Worktrees is `1.24.510-dev1`. Source/release are delivered, not installed or running. Windows historical-seed performance remains an explicit owned follow-up before finalization; writer/routing owners, caller/cold-entry and installed acceptance stay separate. |

| U18 - Incoming registration inspection bootstrap | A new payload's ordinary command still selects an older, unsupported or broken runtime, while setup needs the U17 identity/policy before effects. | Compose the existing frozen-input constructor and owned child boundary to execute the version-1 reader independently; no alternate registry parser, builder, fabricated installation receipt or active publication. Qualify actual incoming construction, prior-state preservation, fresh/alias/refusal, repeat and cleanup. | Bounded #11083 under #10377; U17 remains delivered. Caller migration, installed recovery and ordinary routing/writer owners stay separate. |

These are candidate families, not assertions that each needs a new defect or
issue. Each implementation unit must identify a
specific failing boundary, supported contract and unconflicted owner.

## L5820 - Whole installer and resident-companion lifecycle

Source: #5820, including its original version-slot/whole-lifecycle intent,
resident-helper refinement and inherited #2395 acceptance. Direct owner:
the existing [installer-recovery Plan](README.md#whole-installer-and-resident-companion-lifecycle---5820),
not a new implementation appointment. Preserve all original owners, claims,
holds and activation gates.

| Clause | Unresolved acceptance retained directly in the Plan |
|--------|----------------------------------------------------|
| S1 - Payload exclusion | Only fast payload copy enters an attributable version-slot OR equivalent pending candidate; build/health/cutover/task waits stay outside replaceable payload throughout lifetime, with immutable completed slots preserved. |
| S2 - Failure alternative | Staging failure hard-fails OR performs an enforced bounded protected attempt; never silently executes unprotected in place. Both alternatives are not mandatory. |
| S3 - Independent lifetime | Background reconcile, descendants, tree custody, uncertainty and owned cleanup have independent bounds; a staged child UV timeout alone is insufficient. |
| S4 - Exact companion chain | Exact registered resident `dtssh` start/stop/health command AND waiting PowerShell/host-helper descendants remain covered across supervision, health, restart and update. |
| S5 - Identity and ownership | Selected source/provenance, argv/config/service identity, containment, child exit, readiness/reporting, exact-generation ownership and owned cleanup remain explicit, consistent with authoritative incoming replacement rather than an old-state veto. |
| S6 - Native update witness | Ordinary native Windows plugin update while this companion is actively supervised, without manual killing, retry race or stopping unrelated/shared trees; automatic post-update supervision/readiness. |
| S7 - Separate inherited acceptance | #2395 temporary per-unit restart-inhibition/expiry remains separately open unless implemented OR explicitly transferred to its rightful supervision owner. |

Delivered fail-closed MCP/Budget staging and narrower constructor/reconcile/
lifetime source credit are retained. #5827's CWD-prevention evidence and
installer rename/CWD fixtures do not accept this exact Windows companion chain.
V5820-L, V5820-N AND G5820 preserve the complete family; technical/native proof
is future unchecked work, not a prerequisite to documenting it. The original
#2385 consolidation condition remains: accept this concrete resident-helper
tail while preserving #5820's complete current contract and existing ownership.
No updater, live companion operation, resource release or custody transfer
is authorized by this documentary intake.

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
| Old-state non-veto positive controls | Separately and together, retain a live old keeper, unknown old process census and occupied old control home while the real latest explicit entry makes incoming code ready/current and fresh calls reach it. Preserve old stateful clients; unknown cleanup is deferred, not incoming failure. |
| Genuine incoming negative controls | Pair the same fixtures with rejected incoming source/authenticity, failed authentication/build, actual denied or failed incoming writes, and failed incoming health. Each remains a failure; necessary state compatibility/conflicting-work protection remains enforced. No unhealthy candidate or partial selection is advertised as incoming completion. |
| Automatic force and protected clients | With held clients present, automatic `--force` remains inhibited when automatic updates are inhibited. An uninhibited automatic request and an explicit update preserve protected clients too; force never supplies central-role proof or authorizes killing state holders. |
| Independent central retirement | Only proven eligible central processes/registrations may be retired. Revalidate native identity, installation, central role and then-current selection/registrations immediately before each mutation; skip unknown, future or reselected targets. No process-name sweep or protected-child retirement; deferred cleanup does not reverse incoming success. |

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
| U12 | #10574 merged as `322a4e5adebcd066a920ca52c5e808d41fe1d617`; journal #10613 records the source milestone. Release #10626 publishes qualified Dev `f21518183df46b54a1328ebdd2626c96aa62d28f` as Main `19649c0bc538638cb5e58f8f16c96038b06acb8a`, MCP `0.2.181-dev1` and Worktrees `1.24.473-dev1`. All four changed canonical implementation bodies and the packaged MCP recipe/receiver match. | Exact-head Windows/Linux run `41973788482` passes all MCP protocol/refusal, materialized stamp/prerequisite, actual native participant, shared-receiver and cold construction/consumer/reuse/failure-preservation steps. Installed configuration loading checks the actual `bridges` directory; no server or authentication provider is started. Current-head clean review and both required CI runs pass. | Contained caller-process loss is not production caller-Job loss. Ordinary bootstrap/command/explicit-installer adoption, existing fenced-queue recovery, macOS and affected-host rollout remain unqualified under #10499/#7758/#7028 and the parent effort. |
| U14 | #10700 merged as `b1e7f13079e3cd6968e6967ba878a684064b35c1` from final runtime head `ed8cfa89a5e88fd2a754362294e2278970cf2a2a`. Normal promotion `42054939302` qualifies that exact Dev; #10732 releases it as Main `be5f4c9991e3726f90eba0c1c035eb7403ee5595`, MCP `0.2.185-dev1` and Worktrees `1.24.482-dev1`. All 35 changed production blobs and four packaged MCP receiver bodies match the qualified source. | Exact-head native run `42051032188` passes both complete Windows/Linux portfolios and the distinct Windows HOME/USERPROFILE path. Caller-loss packets pass in 94.955/87.222 seconds with both cycles, cold coalescing, caller-boundary destruction, same-owner survival, bounded-child retirement, eleven/six settled original records and empty inventories. Windows' qualified desktop witness records zero new windows/focus changes; restrictive-outer controls remain. Current-head clean owner-acceptable review and all required CI pass. | This releases only the legacy automatic MCP bootstrap adoption; command/explicit-installer adoption, existing fenced-queue recovery, macOS and affected-host rollout remain separately owned under #10499/#7758/#7028 and the parent. Historical failed/held records are not retroactively settled. No installed update or rollout is credited. |

## Reconciled existing construction owners

The U9 Windows lifecycle follow-up adds seven standard-budget native controls
for forced incomplete-generation repair/repeat, direct install/provision
venv/package failure exits and marketplace uninstall after constructing and
invoking a real temporary venv. Configuration, unknown state and the original
marketplace entry/manifest survive removal. UV package delivery remains
synthetic, and host-service effects remain guarded. This extends source
consumer coverage only; it does not replace a consuming harness's clean-room
scenario, installed-host qualification or the parent completion gate.

The unsupported immutable-UV option correction is delivered in #9501, whose
merge is contained in the actual qualified Dev for #10463. Its exact-head
Windows/Linux bootstrap and UV lanes are recorded on that original PR, not
reclassified as new executions here. Four current contained native source-build
controls pass in 187.69 seconds with real UV and a synthetic backend. They
exercise fresh/warm construction, leaf-only source invalidation, request-witness
and Job-scope key stability, and meaningful build-environment invalidation.
These are constructor consumers, not a complete ordinary suite update.

The packaged pywin32 allowance from #9623 and original completion diagnostic
from #9878 now also have a joined native Dispatch completion packet. Twelve
Windows PowerShell 5.1/7 controls pass in 98.16 seconds: six against the current
source and six against the complete released Main script snapshot
`b408a6500bdd8a9ec5812cfaab1bbd882a610381`. The actual caller and helper
accept the static packaged hook, reject missing/modified bootstrap inputs with
the child diagnostic, confirm helper exit and preserve the prior state.
An external sentinel remains absent even for removed rejected candidates.
Without opt-in the six permanent T3 controls skip. This does not acquire
packages, activate a Dispatch CLI or qualify #7305's installed ordinary-update
outcome; the released Git replay is separate from the differing current source
and from inspection or installation of packaged release artifacts.

#9290 retains its existing lifecycle/publication-envelope correction and the
separate promoted-input/profile work. #9572 fixes Job-custody identity only;
the four controls do not vary lifecycle tickets or prove the complete reported
cache/deadline recovery. Coordination is retained in
[comment 6635053007](https://github.com/ThomasMichon/copilot-extensions/issues/9290#issuecomment-6635053007).
#7743/#7758/#9448 retain fresh-entry and existing-request recovery. #3004
retains actual first-restarted-model knowledge prompting; the closed #1159
write guard is not that acceptance. None of these owner records authorizes an
installed retry or supersedes an open contributor implementation.
