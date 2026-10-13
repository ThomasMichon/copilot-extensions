# Serving convergence audit - 2026-10-12

Canonical campaign: [Vendored Installer Engine](README.md).

## Evidence and limits

The [full source-pinned matrix and seven causal chains](https://github.com/ThomasMichon/copilot-extensions/issues/5703#issuecomment-6133730784)
cover all 14 public `agent-*` plugins, including 12 runtimes and two
payload-only entries. Source pins:
`dev 1604313dc1971fc37e551aaa2ee05575b6068757` and historical released
`main cfa7e277739d64890f6ee995c41488e4a7064309`.
Development base versions do not establish deployment age; promotion
materializes pointers and computes published versions.

The execution-free install-contract guard passed 13 runtime plugins (the
12 agent runtimes plus budget-guidance). That establishes structural
conformance, not serving convergence. No installer, service, runtime test or
live repair was executed by this audit. Later releases and peer-owned
repairs do not retroactively prove these paths converged.

## Findings and existing ownership

| Concern | Concrete evidence at the audit pin | Tracking and boundary |
|---|---|---|
| Unified updater reports current from build metadata | `update_runtime.py:485-500` skips on deploy-manifest source version; `reconcile.py:441-458` reads that version. Launch's running-version path is different and PID/version-only. | #6389 owns semantic integration/reporting. #5703 retains its original partial-update recovery context. |
| Bridge current-build and bootstrap paths adopt stale serving state | `install.ps1:743-780,2069-2094,2759-2782` return before live reconciliation; bootstrap calls install, which selects the slot and starts via healthy-process adoption (`2456-2462`). | #438 remains the Bridge owner; #5750's outage-shaped fallback is adjacent, not fixed here. |
| Worktrees monitor failure/rollback remains success-shaped | Installer publishes selection before callback; `status_monitor_cutover.py:473-503` returns 0 for failed/busy transitions retaining old serving state. | #6389; conditional transition owner #6143. #6373 owns only the Windows redirector/PID adapter, not a proved rollback cause. |
| Index loses strong inner validation at its installer boundary | Native deploy checks expected version/PID/ownership, but `install.ps1:2308-2337` discards failure and checks only HTTP 200; POSIX `2111-2135` falls back to healthy-old adoption without systemctl. | New #6390 is distinct from #5769's role resolution/Job detachment and #5873's engine adoption. |
| Auxiliary lifecycles do not share expected-version semantics | Codespaces starts/adopts a live Owner or defers while busy; Vault uses fixed-endpoint stop/rebind; MCP uses best-effort cutover/reaping. | #6389 coordinates adapter-specific outcomes, not identical lifecycle scripts. #5558/#2386 retain Dispatch worker/pool propagation. |
| Retirement acknowledgement is not universal exit proof | Ordinary shared ZDD requests shutdown (`cutover.py:500-557`); Bridge additionally confirms predecessor exit (`venue_cli.py:315-340`). Vault/MCP paths differ. | #6143/#6164 retains conditional bounded cutover and retirement work. Do not claim every consumer lacks stronger checks. |
| Source provenance is not one universal stable pointer | MCP, Containers and Machines legacy manifests retain staging paths; SSH bootstrap can compare a surviving old stored stage against itself. | #6389: stable original-source evidence and honest out-of-band status. Cell schema-4 source/runtime separation remains intentional. |

#5956 owns the separate architecture decision about the vision's
full-entrypoint promise versus the shared engine's documented scope.
This audit does not authorize silently removing Worktrees' intentional
engine exception, generalizing installation cells to downstream plugins,
or moving service-specific behavior wholesale into the build engine.

#6369/#6374 already fixed source-fingerprint drift from generated egg-info.
That merged fix is not a serving-convergence proof and must not be
duplicated. Existing open author PRs and their owners remain intact.

## Recommended semantic contract

**Agent-recommended; implementation remains open under #6389/#6390.**

Record these independently: reconciled payload/source provenance; completed
selected generation (slot/package version, content hash and ownership);
actual serving identity/readiness; exact previous-generation retirement.
Skipping a build is valid, but must not skip the remaining service phase.
Install, update, bootstrap, first provision and recovery should use the
same decision. A failed, busy or rolled-back transition stays explicitly
partial/deferred, never fully current. Retry reuses the completed immutable
slot rather than rebuilding or overwriting it.

Adapters retain legitimate role differences: payload-only plugins and
CLI-only providers have no daemon; Logger's in-flight scheduled CLI jobs
are not stale serving daemons; Codespaces' busy Owner may defer safely;
Index's durable engine versions independently; Machines may select an
explicit historical runtime; SSH's out-of-band runtime is not payload-only.
Unconfigured/NoService outcomes must be explicit and must not activate a
service. A Windows launcher/worker pair is one owned instance, not two
independent daemons.

## Validation cases

**Agent-recommended; none of these are claimed passed by the source audit.**

- Current completed slot plus healthy older service: updater still invokes
  serving reconciliation; bootstrap/install and update agree.
- Failed native cutover plus retained old HTTP health: report partial/failure,
  not new-version success. Include the supported non-systemd Index host.
- Retry after build completion: no rebuild, no immutable-slot mutation;
  converge the remaining service phase from the selected completed slot.
- Shutdown acknowledged but exact predecessor remains alive: no full success;
  ownership, start identity and Windows pair handling prevent false retirement.
- Successor outlives the management caller's Job/process group; its expected
  identity/version remains routed and read-ready after caller exit.
- Cleaned staging directory and surviving historical stage: bootstrap resolves
  authoritative original source rather than self-comparing stale provenance.
- Role/policy cases above: explicit N/A, historical, independent-engine,
  deferred-busy and out-of-band results without unintended activation/downgrade.

Future implementing PRs must distinguish unit, simple consumer end-to-end,
fresh-environment and available live-venue evidence, with explicit reasons
for skipped lanes. The structural guard is a separate check, not a proxy.
