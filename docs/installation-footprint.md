# Installation footprint and repair boundaries

Use this reference to identify **which installation owns an artifact**, what
its selected release should produce, and how to verify repair. It is not a
new installer, a machine inventory, or permission to operate on another user's
installation. Exact commands and schemas remain with their existing owners.

The goal is a **clean-install-equivalent canonical software footprint for the
selected authentic release and preserved operator configuration**, not factory
reset or a custom layout. The
[authoritative replacement contract](install-contract.md#authoritative-replacement-and-completion)
owns success and preservation. The
[architecture adoption boundary](architecture.md#replacement-lifecycle-target-and-current-adoption)
and [application migration guide](patterns/fast-application-install-and-cutover.md)
distinguish that target from current implementation; this inventory does not
claim that all installers already realize it.

## Resolve the installation before counting artifacts

Record the addressed user/installation, platform, selected source/release and
dependency profile, enabled capabilities, effective configuration, and exact
command binding. Resolve roots through that owner's configuration/context,
not a familiar basename, ambient `PATH`, or another marketplace's payload.
Keep actual machine paths, identities and receipts in the operator's durable
private channel, not public reports.

| Layer | Meaning | Evidence owner |
|-------|---------|----------------|
| Source | The admitted release, configured source/ref or prepared snapshot from which installation is requested | [Source/authentication contract](install-contract.md#update-source-authentication), source receipt and selected owner |
| Delivered payload | The plugin files Copilot currently loads; not proof of runtime deployment | Marketplace catalog, `plugin.json`, effective Copilot settings and payload command declarations |
| Constructed runtime | A physical candidate/retained slot, with its environment and completion evidence | [Versioned runtime contract](install-contract.md#completion-marker--no-corpse-reuse-clean-retry) and deploy manifest |
| Current selection | The runtime chosen by selectors, command redirects and service discovery | Selected installation's `current-version`, applicable last-good marker, launcher and route owners |
| Running | The actual process/generation serving the capability | Owning service's authenticated readiness, native process identity and generation/route evidence |

These can differ legitimately during staging, cutover or rollback. Package
version equality, a recent payload, or the presence of a slot is not proof that
current commands or a running service use it. Do not sort source-qualified
generation hashes to guess a selection.

## Footprint inventory by owner and variant

Paths below are **layout examples**, not lookup instructions or an exhaustive
plugin roster. Root overrides, standalone applications and installation modes
must be resolved through their owners. Classify every observed artifact as
required by the selected configuration, optional/unconfigured, retained
historical, or unknown; unknown is not disposable.

| Artifact family | Existing variants and canonical owner | Repair/preservation boundary |
|-----------------|---------------------------------------|------------------------------|
| Plugin payload | Copilot's installed marketplace payload, or an explicitly selected directory source; `plugin.json`, skills/hooks/extensions, `payload-invocation.json` and generated `bin/` commands | Restore authentic delivery through its owner. Never modify package code or templates in the installed copy. An installed payload alone does not activate a service. |
| Legacy runtime root | Resolved `~/.agent-<name>/` examples in [architecture](architecture.md#install-topology--marketplace-to-local-paths); `versions/<generation>/`, `current-version`, deployment/completion records, plus component-owned state | Replace software through canonical construction/publication; preserve configuration, databases and live resources co-located under the root. Never erase the whole root. |
| Explicit installation cell | Eligible core `agent-*` implementations only: source-neutral `~/.copilot-extensions/marketplaces/<marketplace-id>/plugins/<plugin-id>/` with installation, versions/snapshots, state/run/log ownership | [Cell pattern](patterns/marketplace-installation-cells.md) and [install contract](install-contract.md#core-agent-service-installation-cell-contract) own receipts and supported adoption. Do not enable cells to fill an inventory, merge their state with legacy roots, or make downstream harnesses reconstruct cell internals. |
| Python environment | Historical flat slot: Windows `Scripts/python.exe`, POSIX `bin/python`; application slot: nested `.venv/` plus complete locked project, associated wheels/resources and app-local install/launch entries | [Application owner](install-contract.md#local-wheel-applications) chooses the variant and profile. Rebuild a damaged immutable slot canonically; do not pip-install or edit code inside a completed generation. Separate durable/heavy companion environments retain their own explicit lifecycle. |
| Commands and generated launch assets | Attributable payload-local shims; legacy user management wrappers; project commands; Windows `.ps1` and `.cmd` siblings; standalone app front doors and slot-local launchers | [Binstub contract](install-contract.md#binstub-format-windows) and [application generator](../libs/installer-engine/runtime_application_binstubs.py) own formatting/selection. Recompare and regenerate the addressed artifact from authentic templates with resolved inputs. Preserve foreign commands and unrelated intentional customizations; do not invent a shadow launcher. |
| Central service and registration | Per-owner routes, instance records and platform supervision; Windows tasks or user-mode lifecycle where supported, POSIX user units; filesystem provider contributions | Use the selected component's [lifecycle owner](patterns/service-lifecycle-supervision.md). Repair owned registration/launch targets; retire only proven obsolete central generations/registrations. Task or interpreter name alone is not ownership. |
| Transaction, lock and update activity | Installer directory locks, owner receipts, queue/request records, retained synchronization files, staging and quarantine | [Operational-lock recovery](../plugins/agent-conduct-guidance/skills/recovering-operational-locks/SKILL.md) owns stale-state evidence, exclusion, compare-before-mutation and receipts. Retained kernel-lock files are not dead-PID leases. A queue record is not completion evidence. |
| Configuration and adoption | Copilot enablement, plugin/provider configuration, Manager preferences/source policy, registered repositories and project adoption | [Configuration map](configuration.md), [Manager configuration](../worktree-manager/docs/configuration.md), and component schemas own edits. Preserve every unrelated value/byte; install does not rewrite committed repository policy. |
| Durable application state and live connectivity | Credentials, repositories/worktrees, sessions, databases, claims, session-host/keeper processes, relays and forwards | Preserve through their owners, including separately owned children of central services. Never capture a peer's session/state to manufacture repair evidence. |

**Windows process-count trap:** a stdlib venv launcher and its base-Python
worker can be one logical central daemon, not duplicates. Correlate the native
parent/child identities, selected slot, `pyvenv.cfg` base and owner service
record before deciding anything is obsolete. A base interpreter path or two
`python.exe` entries is not permission to kill either.

Optional/unconfigured services stay inactive. A complete inventory reports
them as not applicable; it does not provision Vault, Index hosting, Manager
maintenance or another capability merely to make every row green.

## Installer-equivalent repair

Within an **already authorized repair scope**, use the official update/repair
owner first. If it fails, refuses, or cannot perform a subtask it normally
attempts or is expected to complete, that is **not a blanket prohibition on
operational repair**. Any such installer-equivalent subtask may be restored:
configuration, binstubs/launch assets, owned central services and schedules,
doctorable configuration/worktree state, footprint directories/junctions,
or demonstrably stale locks/transactions.

Prefer authentic payloads, shared installer subtasks and standalone owner
helpers. If those are unavailable, bounded reconstruction of an artifact is
permitted when the selected authentic release's template/schema and resolved
inputs establish the canonical result. Preserve before-state, use the owner's
exclusion/conditional-publication boundary, and verify through the canonical
reader and actual consumer. **Generated launcher/template output is not runtime
package code:** exact regeneration is artifact repair, not monkey-patching.
Do not edit the shipped template or runtime logic to make the output work.

This authority does not enroll automatic maintenance, select a different
release/feed/profile, grant OS privileges, waive source authentication or
dependency admission, or transfer another owner's live work. Missing installer
capabilities and stale incumbent gates are realization gaps, not manual-update
permission gates. Actual authentication, build, I/O, privilege and incoming
health failures remain failures. If canonical inputs, ownership or safe
publication cannot be established, report the specific missing proof or
permission and preserve state; do not substitute a custom footprint.

Stale operational guards retain the existing
[recovery procedure](../plugins/agent-conduct-guidance/skills/recovering-operational-locks/SKILL.md):
independent inactivity evidence, exact-object quarantine, repair exclusion,
recomparison/CAS and before/action/after receipt. Do not fabricate a success
receipt, remove another writer's lock, or equate a missing PID with settled
descendants. Repair must restore the operation, not just its error message.

### Failure → owning subtask → artifact → verification

These are **decision routes**, not claims that the repairs have all been
implemented or field-qualified.

| Observed failure | Owning subtask | Expected canonical artifact/result | Required verification |
|------------------|----------------|------------------------------------|-----------------------|
| Selected runtime differs from payload/request, or a slot is incomplete | Runtime/source-profile construction and publication | Admitted complete physical slot and applicable selected-generation records | Check source/profile and completion through the owner; invoke the selected command; prove required incoming serving generation |
| Missing, shadowed or damaged command/launch asset | Owning binstub/template generator | Canonical platform siblings targeting the resolved installation/current slot | Check exact generated target and attribution, argument/exit forwarding through a harmless consumer; PATH evidence is supplementary |
| Older Manager lacks a maintenance setter | Manager's configuration writer/schema | Only the intended `[maintenance] automatic` boolean changes in resolved `config.toml` | Preserve unrelated bytes under its configuration lock/recomparison; verify the intended TOML boolean is present, strict `load_config(resolved_root)` succeeds and `.maintenance.automatic` matches the requested choice; separately check actual consumer admission |
| Invalid provider/configuration or doctorable worktree record | Plugin configuration/adoption/state doctor | Schema-valid, minimally repaired addressed record | Canonical reader/doctor plus affected consumer; preserve identities, active claims, repositories, worktrees and sessions |
| Broken installation junction/root redirect | Owning installation/launcher publisher | Exact owned redirect to the canonical physical target | Recompare redirect/ownership before replacement; resolve the real target and invoke the consumer; never traverse and erase its referent |
| Stale lock/queue blocks explicit installation | Existing lock recovery and request execution owner | Quarantined proven residue and a normally executable original request | Exclusion/CAS and receipt, then actual requested installation result; stale-queue or dead-PID evidence alone is not success |
| Central task/unit or provider selects an obsolete generation | Owning service/registration installer | Applicable current registration and authenticated ready incoming service | Verify exact installation/role, native identity and current target; exclude session hosts/keepers, relays/forwards and foreign registrations |
| Outgoing central daemon remains after selection | Outgoing lifecycle owner; optional finite long-grace fallback | Independent central-only retirement, never a new periodic reaper | Revalidate generation/role/native identity and then-current selection at mutation; report skipped/unfinished retirement separately from incoming success |
| Real source/feed authentication, privilege, build or health failure | Acquisition/construction/readiness owner | Explicit failed/partial result with intact preserved state | Fix the actual prerequisite within granted scope; never mark a failed candidate healthy or bypass admission |

Fail-closed admission predicates are not strict configuration readback:
`automatic_maintenance_enabled()` also returns `False` on unreadable/invalid
configuration. That result alone cannot verify a requested disabled setting.

## Verified subtask examples

These are sanitized Windows repair observations, **not universal current-version
guarantees or a completed whole-installation update**. They addressed capabilities
already selected by operator configuration under existing repair authority;
they do not authorize activating an unconfigured service or enabling an
intentionally disabled capability. Recheck the selected authentic release's
implementation before applying the same route.

| Observed boundary | Canonical repair and verification | What was not established |
|-------------------|-----------------------------------|--------------------------|
| An interrupted same-boot update fence remained with no live installer | Explicitly authorized `update --recover-for-repair` acquired the existing installation lock nonblockingly and released the queue fence through its owner; see [operator-authorized queue release](../libs/update-driver/README.md#explicit-operator-authorized-queue-release). A current lock owner refuses release; historical cleanup proof is not required. | Release did not clean up or settle the interrupted operation. Subsequent ordinary updates and their actual results were still required. |
| Bridge runtime and commands were installed, but an existing disabled scheduled task referenced a missing launcher | The selected authentic [Bridge installer](../plugins/agent-bridge/scripts/install.ps1) supplied the exact `launcherBody` expression, selected through its PowerShell AST and evaluated with normal template inputs. Original task XML was retained; task/source hashes and the missing-file condition were rechecked before restoring only that launcher and enabling the exact existing task. Service health then proved the current generation, with source/configuration bytes unchanged and no elevation. | The existing-task “leave untouched” branch had not regenerated the missing artifact. Task presence or successful runtime/binstub construction alone had not proved service readiness. |
| Index project activation selected client while the machine's explicit role was host | The completed/current client slot lacked its required host-role server sibling. Repair reused the authentic [Index installer's](../plugins/agent-index/scripts/install.ps1) `Install-ServerVenv` with the explicit host role, under `Enter-IndexBuildLock`, then `Ensure-ServiceRunning`. Native cutover health passed; installer/configuration/primary completion bytes and index data were unchanged. | An ordinary owner update had returned zero despite a host-runtime-unavailable error and reused the completed client slot without constructing the sibling. Neither its exit code nor primary completion established the configured host footprint. This was not enrollment of an optional, unconfigured host. |
| Bridge published an endpoint, then exceeded its native installation deadline | A fresh process census after installer exit found neither the installer nor a live Bridge central process; the published PID was dead. Supported installer `start` through its canonical scheduled task restored the current service. | Endpoint/marker publication was not continuing serving proof. The timeout was not a successful update, and the empty census was not itself recovery. |
| Vault activation failed inside ordinary update although its standalone activation reader worked with normal interpreter defaults | The published [Vault installer](../plugins/agent-vault/scripts/install.ps1) update succeeded from the intended project context without a Python override. Its existing canonical task nevertheless remained disabled after an “already correct”/zero-exit result; exact-task enable/start used XML before-state, compare-before-mutation and after-state receipts. | The contrast did not prove a global Python defect or justify changing PATH/interpreter policy. Installer success had not verified the task's enabled/running state. Final ordinary full-update verification remained a separate obligation. |

The Index case constructed an absent role-specific sibling through its existing
build owner. It was not a package-logic edit or a generic in-place rebuild of
completed primary code.

These subtask repairs did not patch runtime package code, erase roots/slots/
caches, modify global identity or security policy, or manually kill processes.
Known session/Picker identities were retained. This is preservation evidence
for those addressed operations, not a claim that every possible session,
connectivity path or later release has been qualified. A newer release admitted
during a subsequent update must be verified as that update's selected set.

## Completion evidence and limits

For the **requested set**, verify authentic source/profile, completed incoming
slots, actual required serving readiness and **all** applicable current pointers,
routes, commands and service/task registrations. Record preserved configuration,
credentials, databases, repositories/worktrees, sessions and connectivity.
Accepted/queued work, launcher regeneration, an empty process search or
Manager-only success does not complete that set.

Report outgoing retirement separately. Old recovery/ZDD is best-effort;
outgoing central self-retirement and the optional **one finite asynchronous**
long-grace central-only fallback follow the
[replacement contract](install-contract.md#authoritative-replacement-and-completion).
Do not wait for predecessor exit to declare a healthy fully selected incoming
set ready, or kill session keepers to tidy the process list. Never use whole-root
erase, recursive deletion through junctions, wildcard state deletion or
process-name termination as repair.

Keep the evidence boundaries narrow: documented environment variants, canonical
configuration readback and the [verified subtasks](#verified-subtask-examples)
do not establish whole-set update completion or outgoing central retirement.
New cases should name their owning artifact and consumer assertion without
private host inventories, transcripts or identifying receipts.

Return to [installation/recovery](installation.md#maintenance-and-recovery) or
the [diagnosis skill](../plugins/copilot-extensions-harness/skills/diagnosing-copilot-extensions/SKILL.md)
for task-time routing.
