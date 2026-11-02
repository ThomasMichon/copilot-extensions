# Wheel-first contributor handoff and app tracking

[Back to the canonical effort](README.md) |
[Application support/runbook](../../../docs/patterns/fast-application-install-and-cutover.md) |
[Reviewed transition](wheel-first-transition.md)

**Snapshot: 2026-11-02. The parent effort remains Active.**
This is the shared implementation map, not a deployment approval or a second
effort. Keep progress here and contracts in the linked guide. Update a row when
claiming work, after qualification, after merge and after release; do not mark
an app converted from wheel production or a callback declaration alone.

## Request capture

The requested contributor documentation, verbatim:

> Take a moment to ensure the effort points to a runbook/architecture guide for exactly what each app needs to support for the fast install and cutover flow, and let's also ensure that the end-to-end ideal install flow and sequence is documented somewhere, even if it isn't fully implemented yet. The effort should contain that ideal flow outlines, and include information about each app's tracking toward it. I want to be able to let other agents (even from other operators) contributr the necessary updates to other apps, if that can help accelerate things.

The earlier core-service priority refinement, verbatim:

> Don't rabbit-hole too far into the Index service. We need to focus on getting the main services: worktree-manager, agent-worktrees, agent-bridge, agent-mcp, agent-dispatch, ssh-manager, agent-codespaces, agent-containers. agent-logger and agent-index are less important. Focus on getting a couple through, like agent-worktrees or worktree-manager; perhaps we support some the old way and some the new way initially. We just need the install flow to be fast enough not to time out, and to not fail.

This permits independently releasable app slices and supported old/new
coexistence; it supersedes the earlier one-final-whole-flow-PR sequencing, not
the remaining whole-system validation. App contract decomposition, the status
labels and contribution template below are agent-recommended implementation
organization, not additional operator prerequisites.

## Ideal install and update sequence

This is the intended complete flow, including unimplemented integration.
Exact current commands remain with their component owners; proposed switches in
the transition document are not instructions to execute them against old tools.

1. **Enter independently of Copilot and the old runtime.** The trusted upstream
   self-install/recovery front door selects official Main by default, or an
   explicitly configured source repository/ref/checkout/commit. Preserve source
   authentication and the selected repository's policy. Installation source and
   optional repository adoption are separate inputs.
2. **Resolve once and prepare upstream.** The existing promotion/assembly owner
   supplies the selected Git commit's install-ready snapshot, application
   projects, matching first-party wheels and target locks/profiles. Reuse its
   complete compatible outputs when the commit is unchanged. Configured Dev
   uses local promotion without package-version increments; it does not install
   a mutable checkout or create a competing builder/cache.
3. **Install Manager first.** Consume only the Manager closure, verify its real
   command and required readiness, and keep an independently usable last-good
   recovery entry. Do not prepare Index/Engine or the full portfolio to unblock
   this first useful result.
4. **Derive wanted local apps.** Use the explicitly supplied repository or
   authorized registered-repository sweep, read declarations/plugin selections,
   and combine requirements respecting role, locus, optional profiles and
   installation scope. Missing declarations never imply uninstall. Repository
   adoption/authentication remains its own operation.
5. **Stage with bounded parallelism.** Copy complete promoted application
   projects into owned immutable version slots; run stage-only preparation.
   Keep active markers, commands, registrations, work and incumbent serving
   state unchanged. Record per-app progress and literal errors. Today's
   environment-only `staged` result is not full candidate readiness.
6. **Choose the declared activation strategy.** For independent app slices,
   activate only qualified selected apps and retain supported legacy paths for
   the rest. For the intended atomic toolset, qualify every required candidate
   before one coordinated selection; do not approximate this with unrelated
   per-app marker writes. Whole-toolset atomicity remains an open integration
   gate, not a feature established by the first pair.
7. **Publish and start incoming generations.** Existing application/transaction
   owners publish stable front doors and registrations, select the candidate
   and launch it independently of the predecessor. No stop/uninstall/delete
   prerequisite and no dependence on old IPC. Verify authenticated installed
   readiness and a real app operation before reporting that app installed.
8. **Settle outgoing owners asynchronously.** Old owners observe supersession,
   stop taking new work, preserve/checkpoint supported work and self-retire.
   Transfer compatible hosts/queues through existing contracts; acquire
   exclusive resources or cold replay only at their permitted exit boundary.
   The caller need not wait for full drain, but unfinished retirement/recovery
   remains observable and owned.
9. **Recover honestly.** Before the app's serving commit, guarded rollback
   preserves the still-owned incumbent. After commit, use the app's existing
   commit-forward/reconciliation contract. Keep configuration, data, unrelated
   services and compatible last-good state; report partial/unknown results,
   retain diagnostics and support a safe retry. Applying a requested
   installation and proving readiness/handoff are distinct outcomes.
10. **Keep foreground launch cheap.** An admitted Manager may prepare updates
    after it loads, under the existing maintenance/execution owner. A later
    launch may select already-ready tools. Optional cadence/notification uses
    existing owners, not another scheduler. Plugin session start only discovers,
    reports and emits guidance; it does not reinstall or start runtimes.
11. **Respect operation class and release boundaries.** Manager's
    Installation & Updates control inhibits automatic `<repo> update --auto`,
    not manual update, explicit self-install, doctor or ordinary launch.
    Preserve transaction/re-entrancy admission. Review/CI and actual Main
    promotion precede a release claim; addressed host installation and fleet
    rollout require their separate authority and running-state proof.

## Per-app tracking

The canonical inventory is `tools/python_project_inventory.py`'s runtime roots,
their `pyproject.toml` entry points and standalone declarations. At this snapshot
it yields **21 runtime roots**. The requested shared SSH Manager library is a
separate dependency row below, not a twenty-second independently declared app.
This corrects the ambiguity in an informal "22 apps" portfolio count.

**Two app front doors have the bounded new flow released:** Manager and
Worktrees (#9990, Main promotion #10024). Neither release proves fleet rollout,
every platform, whole-toolset atomicity or the complete target stage contract.
Bridge/MCP are in active unmerged qualification (#10054). Other rows are not
counted converted: retained WIP or prior legacy functionality is not release
evidence for this flow.

| App / source owner | Fast-flow evidence and current gap | Next bounded acceptance / coordination |
|--------------------|------------------------------------|---------------------------------------|
| [worktree-manager](../../../worktree-manager/README.md) (`copilot-extensions-worktree-manager`) | Bounded app-project/UV path released in #9990/#10024; Windows cold/repeat/update, real command/readiness, retained state and caller-Job lifetime qualified. Full stage-readiness, native POSIX and rollout remain open. | Preserve released behavior; complete only an agreed remaining platform/integration gap under #7028. |
| [agent-worktrees](../../../plugins/agent-worktrees/README.md) | Same bounded released pair; historical command-owner restoration and installed-wheel consumer admission included. Full adopted-system/status-monitor transition remains separate. | Verify actual worktree/registry operations and monitor continuity for the claimed integration/platform; do not rebuild the first pair to begin another app. |
| [agent-bridge](../../../plugins/agent-bridge/README.md) | Active #10054 owner; wheel/callback and focused unit work exists, but live Session Host/ACP reattachment has not converged. Review also identifies cancellation, relay overlap, unset install-root and mutating-hydration gaps. Not released. | Current owner resolves findings and qualifies a real fixture Host/peer across forward/reverse updates, failure/retry and native exits. Coordinate there; do not open a competing repair PR. |
| [agent-mcp](../../../plugins/agent-mcp/README.md) | Active #10054 owner; rollback/reuse unit controls exist. Native denied endpoint replacement reached guarded route restoration, but full failure/retry witness remains incomplete. Not released. | Current owner completes authenticated data/control-handle, unchanged-generation reuse, failed-flip retry and native lifetime proof. Keep SQLite fixture-upstream checks distinct from a product DB claim. |
| [agent-dispatch](../../../plugins/agent-dispatch/README.md) | Retained implementation scope; new-flow app acceptance/release not established. Existing coordinator cutover is not automatically this install contract. | Candidate must not dequeue/claim before ownership. Prove task/state round trip, held claims/reservations, update/retry and coordinator self-retirement; claim a non-overlapping slice under #7028. |
| [agent-codespaces](../../../plugins/agent-codespaces/README.md) | Retained provider/setup scope; new-flow installed-app qualification and release open. | Stage without provider writes; normal setup contributes a plain manifest to Bridge's configuration-root `providers.d` via the installed command. Prove either install order, manifest discovery and preserved reservations/forwards; live venue evidence is separate. |
| [agent-containers](../../../plugins/agent-containers/README.md) | Existing source/profile preservation is bounded prior work, not new-flow completion. New app qualification/release open. | Preserve base/provider-exec profile, copied-wheel resources, provider registration and existing fleet custody; test both install orders and no foreign-container mutation. |
| [agent-ssh](../../../plugins/agent-ssh/README.md) | Retained command/source work; new-flow app acceptance/release not established. Not the SSH Manager library or remote SSH service. | Prove generated command execution, profile/config preservation and existing forward/connection use without a new daemon or credential acquisition; coordinate shared-library changes below. |
| [agent-dispatch-service](../../../services/dispatch/README.md) | Separate standalone runtime root, not automatically covered by the Dispatch plugin. No `[project.scripts]` entry in this snapshot; an emitted callback alone cannot qualify it. | Resolve the existing service entry/owner before emitting an application; prove task admission and durable state without introducing a competing coordinator. |
| [agent-ssh-service](../../../services/ssh/README.md) | Standalone service; retained native/source scope is not complete new-flow acceptance. | Preserve enrollment, signatures/roles, connections and native relay custody; qualify a credential-free fixture and explicitly separate any unavailable live SSH lane. |
| [agent-pull-requests](../../../plugins/agent-pull-requests/README.md) | Retained source/native work; app-level new-flow release open. | Qualify installed commands and supported cache/service discovery, without publishing reviews or changing PRs as an install side effect. |
| [agent-pull-requests-service](../../../services/pr/README.md) | Separate standalone service; bounded retained witnesses do not establish whole-app acceptance. | Prove service readiness and durable request/cache continuity, ownership-safe update/rollback and independent client skew. |
| [agent-chat-service](../../../services/chat/README.md) | Separate service/client entry points; retained catalog/packaging WIP, new-flow qualification open. | Package and test both declared commands, preserve member/cursor/state and native ownership; no implicit join, credential enrollment or new chat store. |
| [fleet-manager](../../../fleet-manager/README.md) | Shared source preparation/resources exist; new-flow native application acceptance/release not established. | Preserve registry, execution/maintenance and connection ownership. Prove installed client/service operations and independent startup; no installation-triggered remote rollout. |
| [agent-vault](../../../plugins/agent-vault/README.md) | New-flow acceptance open; existing installer/recovery contracts remain. | Use a disposable vault fixture; prove credential-free status and configuration retention plus owned service retirement, without unlocking a real vault or printing secrets. |
| [agent-machines](../../../plugins/agent-machines/README.md) | CLI root; a declared no-start callback is not app qualification. | Test copied-wheel command/resources and repeat install; installation must not apply arbitrary machine requirements or create privileged tasks. |
| [budget-guidance](../../../plugins/budget-guidance/README.md) | CLI root; new-flow app acceptance/release open. | Prove installed read/status commands with configured offline fixture inputs; no backend refresh, polling service or policy change at install. |
| [agent-logger](../../../plugins/agent-logger/README.md) | Retained WIP; deliberately lower priority, no new-flow release claim. | Preserve every declared command and configured source/sink; stage/setup must not start logging or rewrite history. Do not gate the main-service slices on this row. |
| [agent-index](../../../plugins/agent-index/README.md) | Lower priority; separate CLI/service/engine closures remain distinct. Retained work is not whole-app acceptance. | Qualify only an agreed bounded command/setup slice; preserve configured host selection and avoid provisioning heavy engine dependencies as a core-install prerequisite. |
| [agent-index-service](../../../services/index/README.md) | Optional standalone service, not qualified by the plugin wheel or Engine tests. | Preserve configured store/corpus and optional native profile; qualify independently when prioritized. |
| [agent-index-engine](../../../plugins/agent-index/server/pyproject.toml) | Optional heavy engine, separate root and dependency boundary; retained native qualification blocked on toolchain availability. Not released. | Preserve the existing owner/evidence; no toolchain/privilege work or lower-priority engine build to unblock the main services. |

**Shared SSH Manager:** [source/contract](../../../libs/ssh-manager/README.md),
distribution `agent-ssh-manager`, is a dependency with no independent
`[project.scripts]` entry. Its priority is retained, but do not invent an app
daemon/install callback for it. Qualify its wheel/resources and actual consuming
apps' SSH connection/forward continuity, native custody and version skew.
Coordinate through #7028 with the existing SSH source/native owners before
touching shared code.

Rows marked open mean **not qualified/released by this campaign**, not proof
that no source or other contributor implementation exists. Existing app-specific
efforts, open PRs, retained WIP and resource reservations remain authoritative.

## Contributing from another agent or operator

Independent app work may proceed in parallel; "serial per-slice PRs" does not
require an unrelated app to wait for Bridge/MCP. Serialize coupled shared
changes, not independent app evidence.

1. Read the parent effort, guide, selected app references and contribution/test
   rules. Search #7028 and that app's open issues/PRs before claiming anything.
   Discover active owners; do not supersede another author's open PR or assume
   an unassigned table row means its retained source/native scope is free.
2. Agree a bounded app slice through the existing public issue/PR owner and
   record the app, public issue, participant/source handle, owned files, exact
   acceptance, dependencies and next action in this matrix or a linked issue.
   Keep private operator/machine/topology details out of the public record.
   This is coordination, not a new global lock or approval ceremony.
3. Use a separate managed source worktree and PR against `dev`. Prefer app-owned
   adapters, declarations, resources, tests and docs. Request shared emitter,
   constructor, selection or process-primitive changes from their current
   owners instead of copying a private variant.
4. Include the guide's stage/activation/failure/native tests, real front-door
   timing, source/profile identity and explicit platform/external-lane gaps.
   Use existing contained runners. Do not install on another operator's host
   or clear a maintenance hold to qualify a source change.
5. Obtain required review and CI, verify merge, then verify actual Main
   promotion. Update the row with the bounded evidence and remaining gates;
   "merged", "released", "installed" and "running" are different states.
   The coordinator retains whole-suite and deployment acceptance.

Use this compact claim/evidence shape in the existing tracker:

```text
App and bounded slice:
Public issue / participant or source handle:
Owned paths; excluded shared/native/resource scope:
Required stage, install, repeat, update, reverse and failure checks:
Source/profile and claimed platform/shells:
Timing and real-operation evidence:
Current blocker / next action:
Merged commit; Main release evidence; rollout owner (separate):
```

## Remaining common gates

- [ ] Full app-owned stage readiness, separate from environment synchronization.
- [ ] Remaining main-service rows through native qualification and release.
- [ ] Repository-derived wanted-set and independent Manager-first bootstrap.
- [ ] Whole-toolset atomic selection/readiness/rollback, where selected.
- [ ] Whole-system Windows-container and available external/platform evidence.
- [ ] Plugin-first-use reconciliation, instruction/hoist and prepared-launch integration.
- [ ] Explicit deployment-owner handoff, addressed host proof and fleet rollout.

These are a navigational summary of the parent Plan/Validation Plan, not a new
completion gate or a transfer of their unresolved items.
