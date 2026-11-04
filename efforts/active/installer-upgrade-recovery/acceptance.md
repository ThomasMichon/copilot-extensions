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
