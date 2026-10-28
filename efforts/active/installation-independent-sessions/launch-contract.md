# Launch Contract and Migration

Back to the [canonical effort](README.md).

Initial inventory used `32b43c74e7e0f35a947eb605f8ef5ea83ad5b718`;
the seed/setup boundary was refreshed at
`bad5db98a52f844d614832b43b1c0cc310dbf4f1`.
This is not a claim of implemented behavior.
Proposed refinements are **agent-recommended** until their production slice
clears review.

## Reviewed baseline continuous launch path

| Consumer / boundary | Current owner and behavior | Required migration or retained obligation |
|---------------------|----------------------------|-------------------------------------------|
| Ordinary Manager launch, resume and new-window modifier | `worktree-manager/src/worktree_manager/relocated_launch.py` delegates to the Manager payload's `launch-session` script. POSIX exec-replaces; ordinary Windows waits; new-window delegates a separate terminal spawn. | Preserve launch mode, prepared seed/attempt identity, arguments and environment. #5579 retains forwarding-layer changes. |
| Linux mux pane construction | `worktree-manager/bin/launch-session.sh` selects its sibling `pane-wrapper.sh` and passes worktree/project/mux session and optional prompt/AHP controls. | Copy a self-contained session shell before starting the pane; an installed script must not be its lifetime dependency. Preserve existing prepared-command and credential-isolation behavior. |
| Windows mux pane construction | `worktree-manager/bin/launch-session.ps1` builds a one-shot file-based argv handoff to `pane-launch.ps1`, then its sibling `pane-wrapper.ps1`. | Preserve file-consumption/receipt semantics and literal argv. A copied wrapper cannot reach back to an installed pane launcher after startup. |
| Deferred seed/admission command | `agent_worktrees.launch_seed_exec.deferred_command` places a runtime Python above the setup command. On Windows `_launch` waits for that command; even POSIX retains a waiter when admission or execution correlation needs it. | Preparation/admission must settle before handing the execution leg to the stable shell. Copying the pane wrapper alone leaves this removable interpreter alive. Preserve admission, correlation and unknown-seed custody. |
| Actual Copilot boundary in default setup | `scripts/default-setup.ps1::Invoke-CopilotBackend` calls `launch_seed_exec --invoke` when a staged seed is present. That Python starts Copilot and waits for its full lifetime. The setup script also owns post-exit fsmonitor teardown. | Prepare literal backend argv and seed custody without retaining the runtime as Copilot's waiter. Move supported post-exit policy to the current boundary owner, not out of the contract. Cover actual seeded/resume launches as well as seed-free fixtures. |
| Registration at create/join | `Invoke-ManagedMuxRegister` / `_aw_publish_managed_mux_live` use the current launch script's sibling `manager-runtime` helper. | Preserve joins of an existing live session and daemon status routing. Distinguish a new leg's start from an attach; do not re-register an unrelated incarnation. |
| Windows wrapper ownership | `worktree-manager/bin/pane-wrapper.ps1` owns a kernel kill-on-close Job for the pane subtree. | The copied shell must keep this handle for its entire lifetime. Moving it into a short-lived hook would kill the session or lose containment. |
| Pane end | Both pane wrappers record exit activity and use a sibling `manager-runtime` helper to dispatch removal. | Resolve the current compatible same-installation implementation at end, not the payload captured at start. Remove policy-only installed imports from the waiting shell. |
| Manager callback dispatch | `manager-runtime.ps1` / `.sh` deliberately select the wrapper payload's immutable slot. `worktree_manager.mux_callback` waits for daemon-worker spawn, not registry admission. | Use a non-provisioning boundary and distinguish spawn success from actual bounded lifecycle admission. Preserve exact root/cell attribution. |
| Existing stale-end protection | `mux_mapping_registry.MuxMappingRegistry.remove` compares live session incarnation/revision and persists tombstones. | Reuse this owner and its fences; a delayed A end cannot tombstone B. A mux display name alone is not an incarnation. |
| Agent-worktrees new-pane/handoff path | `plugins/agent-worktrees/src/agent_worktrees/sessions.py::_mux_pane_cmd` selects Manager wrapper assets on Windows or a materialized legacy wrapper; prompt controls require a wrapper. | Migrate this actual consumer too. Preserve native interactive prompt transport and missing-wrapper refusal, not just ordinary Picker launches. |
| Asset materialization | `plugins/agent-worktrees/launch-wrapper-assets.json` and `launch_wrapper_assets.py` declare the canonical Manager shell assets copied into the plugin. | Any new/retired asset must keep both production consumers and packaging declarations consistent. No second canonical wrapper implementation. |
| Direct/no-mux path and post-attach finalization | The ordinary launch scripts execute/attach and run current agent-worktrees post-exit logic. | Retain supported semantics and the installed exit-noise repair. Mux detach is not child exit; a generic end hook must not finalize a still-live session. |

## Preparatory production candidate

The first implementation slice copies the canonical platform pane assets into
the existing launch-attempt namespace (`*.shell`), outside the engine and
Manager installation roots. It does not copy a runtime, introduce a second
wrapper implementation or change the seed/admission owner.

Tracked launch plans advertise `pane_shell_version: 1` and their exact
`record_path`. Manager uses this only when the executing engine exposes that
capability and its selected wrapper carries the version-1 boundary marker.
The engine's own mux consumer applies the same wrapper-marker check. Older
owners, untracked/anchor launches and MSYS/Cygwin retain legacy construction.
This is provider-first expansion, not a promise that independently updated
consumers already have installation-survival support.

The copied shell retains OS child/terminal ownership, including the Windows
kill-on-close Job. At each boundary it selects the current completed runtime
within the original Manager root without provisioning or starting a daemon.
`worktree_manager.pane_lifecycle --contract-version 1` admits through the
existing registry synchronously. Native session incarnation, registered live
CLI pane and original installation/source-selection identity fence end admission;
each end retires only its own pane, retaining a concurrent handoff predecessor
or successor. Strict snapshot-fenced starts refuse corrupt registries. Exit
fences apply to tombstones too; missing state is unconfirmed, not delivery.
Admitted changes publish to an existing status monitor without starting one;
same-owner reconnect retains observed-exit evidence, and companions follow their
pinned pane within a multi-pane mapping. End metadata carries runtime and launch
correlation to a bounded current-installed-engine activity writer. Diagnostics
remain separate from admission and cannot trigger duplicate-end replay;
reattachment through the same mux executable preserves that identity.
Reconciliation retains dead CLI pane fences; delayed creation cannot revive
them or a same-incarnation whole-mapping tombstone, even after a CAS retry.
An exit records its evidence without reviving an explicitly retired mapping.
Replacing a different-incarnation tombstone during start requires fresh native
CLI/pane/path proof from the existing discovery owner, repeated after any
snapshot-CAS conflict. Incarnation timestamps are identity, not takeover order.
Creation callbacks require current native CLI identity, liveness and path
evidence even without a conflicting mapping. A fenced `cancel` retires an
admitted pane when transport validation or receipt publication fails before
child dispatch, without inventing `observed_end`. Start still precedes the
provisional receipt, so the receipt's grace interval cannot run while a start
callback is pending. Windows command-invocation failures reach teardown with
an explicit failure result.
Missing, unsupported or unconfirmed callbacks remain quiet and do not claim
delivery. Pane lifecycle child calls retain a five-second deadline and native
mux queries a two-second deadline. Windows native setup/seed/execution phase
calls have the operator-approved ten-second deadline, including current-runtime
startup; they never retry or become resident waiters. These are containment
limits, not performance qualification.

Preparation reads one tracking record and copies one POSIX asset or two Windows
assets once per constructed pane. Start/end use finite current-selection reads,
native mux queries and one short-lived lifecycle process per boundary; there is
no new resident helper, external network call or history scan. The existing
status-monitor publish uses bounded local IPC, without a provisioning fallback.
The existing exit activity mark uses one finite current-engine subprocess with
its own one-second deadline inside the shell's unchanged boundary deadline.
No captured engine invocation or PATH fallback supplies that writer.
Start registration is
rechecked on attach by the existing owner rather than reusing stale native
incarnation evidence. Native cold/warm end-to-end launch latency and tail
qualification remain unmeasured; fixture help preparation is not that baseline.

This candidate does **not** qualify removal or migration. Native-capable
normalized Windows/PowerShell 7 setup now runs directly from launch-owned
storage and uses finite preparation, recording the existing setup host and
passing its operation ID to the actual backend. Its direct-executable and
PowerShell-script backend path uses the same existing shell's native child
handle and wait, not either runtime-Python waiter. Admission-only preflight
releases its seed-authority guard before running the child. Resolution
correlation and the literal native argument envelope stay separate.
Old scripts/Windows PowerShell, custom launchers, batch backends and POSIX
still retain their legacy path. Current-root finite calls refuse unsupported
providers rather than provisioning, falling through to a foreign installation
or starting an uncorrelated backend. This bounded normalized consumer
integration is not full launch-path parity or concurrent mux admission proof.
Cold `embody` separately copies the canonical Windows setup assets before its
actual seed-free or terminal-fallback mux spawn, using the same canonical setup
marker without activating the finite seed provider. It does not duplicate
Manager health probes: the existing pane-construction owner still selects that
provider. Qualified startup prompts
retain their existing deferred preparation. Existing launch and prompt owners
retain admission and seed custody. Previews and existing live-mux joins do
not copy assets. Retained setup-time runtime arguments and consumed one-shot
manifest references still require proper uninstall consumer classification;
copying assets is not permission to waive that conservative guard.
Copied assets are retained, not reclaimed on age or inferred liveness. Their
terminal reclamation, missed-boundary recovery, actual installed consumer
parity and the literal clean-middle round trip remain this effort's obligations.
See the [current architecture](../../../plugins/agent-worktrees/docs/architecture.md#launch-owned-pane-assets-and-current-boundary-handlers)
and explicit native packet in `TESTING.md`; synthetic fixture execution is not
real Copilot, TTY or installer-removal acceptance.

## Continuing smallest-boundary obligations

Launch exclusion is per worktree, never a project-wide or global launch lock.
Hold it only across the initial check/create race until the owning mux session
is discoverable. A contender for that worktree rechecks and joins the existing
mux instead of launching a duplicate. Different worktrees launch concurrently;
the lock must not remain held for a Copilot session's lifetime. Preserve the
separate seed-delivery and lifecycle authorities without extending this lock's
scope or nesting a seed mutation under its own already-held admission lock.
Acceptance must include overlapping same-worktree attempts, parallel
different-worktree launches and release at mux discoverability.

The bounded Windows implementation coordinates Picker and detached mux
creation using a session-keyed kernel lease in the user mux registry, separate
from seed admission and both product installations. It rechecks on contention,
releases at discovery before the creation client returns, and reports a late
join without claiming or typing a seed. Its native overlap packet executes
the actual PowerShell/Python boundaries against private synthetic mux protocol
processes. This is not full launcher or actual PSMux acceptance. The next
engine slice adds a distinct per-worktree lifecycle lease to native launch
and retirement owners before fresh checks through removal. It retains the
repo-wide launch barrier for independently running older retirement owners.
Publication is the provider-first expansion, not proof those peers were
upgraded. Migration must qualify current peer support before removing the
compatibility barrier and measuring different-worktree whole-path parallelism.
Actual-mux qualification and POSIX parity also remain open. The separate
`pane_create` new-session fallback also
retains its existing implementation and remains in this continuing inventory.

Use the native CLI's `/exit` for controlled live migration, then verify actual
pane/backend exit. Ctrl+C is not an exit receipt: a first press can cancel a
turn, repeated presses within the backend's own window can terminate it, and
an attach client can disappear while its pane keeps running. Signal validation
must preserve that backend-owned distinction without inventing a fixed Copilot
count or duration. Detach never authorizes end handling or replacement.

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
- The real Windows baseline also contains nested `launch_seed_exec` waiters
  below the pane wrapper. Their source is the deferred setup and final
  `--invoke` boundaries above, not proof of duplicate application work.
  Removing only `manager_launch_cli` / `relocated_launch` cannot close this
  installation-survival gate; those remain separately owned by #5579.

## Validation boundaries

The Linux baseline worker owns only actual disposable install/uninstall
execution and evidence, pinned to the reviewed source above. It cannot fix a
setup jam or replace real uninstall with fixture deletion and still call the
literal test passing.

The main driver owns this production trace and Windows/source integration.
Source-mode shell tests, deterministic child fixtures, real Copilot interaction,
native Windows and real installed cleanup remain separately labelled evidence.
None substitutes for the others or authorizes a host runtime update.
