# Launch Contract and Migration

Back to the [canonical effort](README.md).

This is the initial source-grounded inventory at
`32b43c74e7e0f35a947eb605f8ef5ea83ad5b718`, not a claim of implemented behavior.
Proposed refinements are **agent-recommended** until their production slice
clears review.

## Current continuous launch path

| Consumer / boundary | Current owner and behavior | Required migration or retained obligation |
|---------------------|----------------------------|-------------------------------------------|
| Ordinary Manager launch, resume and new-window modifier | `worktree-manager/src/worktree_manager/relocated_launch.py` delegates to the Manager payload's `launch-session` script. POSIX exec-replaces; ordinary Windows waits; new-window delegates a separate terminal spawn. | Preserve launch mode, prepared seed/attempt identity, arguments and environment. #5579 retains forwarding-layer changes. |
| Linux mux pane construction | `worktree-manager/bin/launch-session.sh` selects its sibling `pane-wrapper.sh` and passes worktree/project/mux session and optional prompt/AHP controls. | Copy a self-contained session shell before starting the pane; an installed script must not be its lifetime dependency. Preserve existing prepared-command and credential-isolation behavior. |
| Windows mux pane construction | `worktree-manager/bin/launch-session.ps1` builds a one-shot file-based argv handoff to `pane-launch.ps1`, then its sibling `pane-wrapper.ps1`. | Preserve file-consumption/receipt semantics and literal argv. A copied wrapper cannot reach back to an installed pane launcher after startup. |
| Registration at create/join | `Invoke-ManagedMuxRegister` / `_aw_publish_managed_mux_live` use the current launch script's sibling `manager-runtime` helper. | Preserve joins of an existing live session and daemon status routing. Distinguish a new leg's start from an attach; do not re-register an unrelated incarnation. |
| Windows wrapper ownership | `worktree-manager/bin/pane-wrapper.ps1` owns a kernel kill-on-close Job for the pane subtree. | The copied shell must keep this handle for its entire lifetime. Moving it into a short-lived hook would kill the session or lose containment. |
| Pane end | Both pane wrappers record exit activity and use a sibling `manager-runtime` helper to dispatch removal. | Resolve the current compatible same-installation implementation at end, not the payload captured at start. Remove policy-only installed imports from the waiting shell. |
| Manager callback dispatch | `manager-runtime.ps1` / `.sh` deliberately select the wrapper payload's immutable slot. `worktree_manager.mux_callback` waits for daemon-worker spawn, not registry admission. | Use a non-provisioning boundary and distinguish spawn success from actual bounded lifecycle admission. Preserve exact root/cell attribution. |
| Existing stale-end protection | `mux_mapping_registry.MuxMappingRegistry.remove` compares live session incarnation/revision and persists tombstones. | Reuse this owner and its fences; a delayed A end cannot tombstone B. A mux display name alone is not an incarnation. |
| Agent-worktrees new-pane/handoff path | `plugins/agent-worktrees/src/agent_worktrees/sessions.py::_mux_pane_cmd` selects Manager wrapper assets on Windows or a materialized legacy wrapper; prompt controls require a wrapper. | Migrate this actual consumer too. Preserve native interactive prompt transport and missing-wrapper refusal, not just ordinary Picker launches. |
| Asset materialization | `plugins/agent-worktrees/launch-wrapper-assets.json` and `launch_wrapper_assets.py` declare the canonical Manager shell assets copied into the plugin. | Any new/retired asset must keep both production consumers and packaging declarations consistent. No second canonical wrapper implementation. |
| Direct/no-mux path and post-attach finalization | The ordinary launch scripts execute/attach and run current agent-worktrees post-exit logic. | Retain supported semantics and the installed exit-noise repair. Mux detach is not child exit; a generic end hook must not finalize a still-live session. |

## Proposed smallest boundary

The copied shell owns only immutable launch data, native child execution,
terminal/containment lifetime and two bounded handler invocations. It does not
own environment preparation, update orchestration, registry policy or a
parallel lifecycle ledger.

At each boundary, use the then-current compatible entry point attributable to
the original installation. A missing installation is not permission to use
another marketplace, provision a runtime or replay an uncertain effect.
The entry point owns its internal delegation to the existing Manager mapping
and worktree lifecycle authorities.

Before implementing this boundary, close the following concrete design items
against the real baseline rather than guessing:

- Exact installation identity/root handed to the shell, including standalone
  Manager versus plugin-materialized wrappers and source-mode tests.
- Launch-owned storage that survives removal of both products and is reclaimed
  only after the actual leg is terminal; reuse existing launch/attempt ownership
  instead of adding a second state authority.
- Handler compatibility and a finite call deadline measured at the real
  subprocess boundary, including process-tree cleanup on timeout.
- Data required for new-session start, attach-only registration and end:
  project/worktree, launch attempt, actual mux incarnation, optional Copilot
  session binding and real exit code.
- Which mux executable owns the running server and whether documented
  uninstall removes or locks it. Keeping an old runtime directory alive is not
  installation-removal acceptance.
- Synchronous end-admission result versus detached follow-up policy. A spawn
  handshake alone must not be described as lifecycle delivery.
- Existing launch-depth source ownership on #5579. Keep its wait-only
  forwarding acceptance distinct even if the same launch contract is shared.

## Validation boundaries

The Linux baseline worker owns only actual disposable install/uninstall
execution and evidence, pinned to the reviewed source above. It cannot fix a
setup jam or replace real uninstall with fixture deletion and still call the
literal test passing.

The main driver owns this production trace and Windows/source integration.
Source-mode shell tests, deterministic child fixtures, real Copilot interaction,
native Windows and real installed cleanup remain separately labelled evidence.
None substitutes for the others or authorizes a host runtime update.
