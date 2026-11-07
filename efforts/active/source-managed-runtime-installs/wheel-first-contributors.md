# Wheel-first contributor handoff and app tracking

[Back to the canonical effort](README.md) |
[Application support/runbook](../../../docs/patterns/fast-application-install-and-cutover.md) |
[Reviewed transition](wheel-first-transition.md)

**Evidence refresh: 2026-11-06. The parent effort remains Active.**
This is the shared implementation map, not a deployment approval or a second
effort. Keep progress here and contracts in the linked guide. Update a row when
claiming work, after qualification, after merge and after release; do not mark
an app converted from wheel production or a callback declaration alone.
This refresh records bounded Linux CodeSpaces, base-profile Containers,
base-profile SSH CLI and separate Dispatch plugin/standalone controls, and
credits the previously recorded Windows releases and CLI qualification.
Evidence remains source-, profile- and platform-specific; it does not requalify later binaries
or establish whole-app acceptance.

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

Every app slice evaluates the canonical
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
contract. The sequence below does not add an old-recovery, provenance or stale
transaction permission gate. Current source evidence is not qualification of
that complete target.

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
8. **Retire outgoing central owners independently.** Old owners observe supersession,
   stop taking new work, preserve/checkpoint supported work and self-retire.
   Transfer compatible hosts/queues through existing contracts; acquire
   exclusive resources or cold replay only at their permitted exit boundary.
   Success requires all requested incoming readiness and all current selections,
   never predecessor drain/exit. One optional finite async fallback after a
   longer-than-cutover grace may target remaining outgoing central daemons and
   obsolete central schedules, not session hosts/keepers, session-owned Bridge/
   agent processes, foreground clients or connectivity. Recheck then-current
   selection and role at each mutation; no periodic resident reaper is required.
   Unfinished retirement/recovery remains separately observable.
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

**Published bundles, bounded app qualification and deployment are separate.**
Main promotion #10862 (`072a626be5f8507df1719d69ed736db9e51f5fa2`)
released the selected Dev snapshot
`b53f38db3e72362fc3343837dc743c46fee32746`. Its schema-2
`python-artifacts.json` lists **21 application bundles plus one shared-core
composition**. The composition is not another application, and bundle
publication does not prove that every installer, callback, platform or lifecycle
consumer is qualified.

Manager and Worktrees retain their bounded released front doors (#9990,
Main promotion #10024). SSH's CLI and service have the additional bounded
qualification from #10462, released in #10862, as scoped in their rows below.
Bridge/MCP startup #10054 is merged; their remaining app/lifecycle acceptance
stays with the existing owners. CodeSpaces #10311, Containers #10347 and
Dispatch's plugin/standalone slice #10345 also have recorded bounded Windows
releases. The additional CodeSpaces, base-profile Containers/SSH CLI and
Dispatch Linux controls below use their own explicit Main snapshots, not an
inferred qualification of current Dev.
There is no new portfolio-wide conversion count here: release, installed
operation, full target conformance and host/fleet rollout are different claims.
Neither bundle publication nor these app slices establish every platform,
whole-toolset atomicity or the complete stage contract.

| App / source owner | Fast-flow evidence and current gap | Next bounded acceptance / coordination |
|--------------------|------------------------------------|---------------------------------------|
| [worktree-manager](../../../worktree-manager/README.md) (`copilot-extensions-worktree-manager`) | Bounded app-project/UV path released in #9990/#10024; Windows cold/repeat/update, real command/readiness, retained state and caller-Job lifetime qualified. Full stage-readiness, native POSIX and rollout remain open. | Preserve released behavior; complete only an agreed remaining platform/integration gap under #7028. |
| [agent-worktrees](../../../plugins/agent-worktrees/README.md) | Same bounded released pair; historical command-owner restoration and installed-wheel consumer admission included. Full adopted-system/status-monitor transition remains separate. | Verify actual worktree/registry operations and monitor continuity for the claimed integration/platform; do not rebuild the first pair to begin another app. |
| [agent-bridge](../../../plugins/agent-bridge/README.md) | Independent wheel-application startup #10054 merged on 2026-11-03. Its bounded Windows witness passed staging, first touch/reuse, pre-publication failure, Host/ACP upgrade and rollback, retained data, caller-Job closure and native exits. These historical candidate checks do not qualify subsequent changes, an installed Copilot agent or the whole app. | Existing owners retain native POSIX, fresh-OS clean-room, full credential-relay/provider and broader legacy-adoption acceptance. Coordinate through #10054/#7028 and their follow-ups; do not repeat the completed Windows witness as unfinished work or open a competing repair PR. |
| [agent-mcp](../../../plugins/agent-mcp/README.md) | Independent wheel-application startup #10054 merged on 2026-11-03. All 15 bounded Windows phases passed, including three-shell first touch, same-generation reuse, denied publication with exact recovery/retry, forward/reverse replacement, caller-Job survival and native exits. These historical candidate checks do not qualify subsequent changes or complete authoritative-replacement/whole-app acceptance. | Existing owners retain native POSIX, fresh-OS and broader legacy-adoption acceptance. Keep SQLite fixture-upstream checks distinct from a product DB claim. The [parent Plan](README.md) tracks the unchecked authoritative-replacement adoption delta under existing #10054/#7028/#9716/#10377 owners: #10652 incoming completion, #6389 fresh serving convergence and #10653 protected retirement. Unknown outgoing cleanup is separately deferred; #10486 full reattachment remains required separate work, not an incoming gate. This refresh claims no additional runtime fix or qualification. |
| [agent-dispatch](../../../plugins/agent-dispatch/README.md) | Bounded modern user-mode application slice #10345 released on Main `a48e41419190fe43ef006a8ef6dfa08e134785b6`. Historical Windows cold/restart/handoff and held-state/native cleanup remain credited. The separate Linux x86_64 witness below uses explicit Main `0dbbdd374e1af0b6443c84cead45102333855da3`: actual stage/cold/reuse, authenticated coordinator readiness, logical work preservation and source-concealed reads. Neither record establishes whole-SDK, later-binary or whole-app qualification. | Existing owners retain configured-supervisor, flat/nonprofile and native-service-owned transitions, distinct-source/reverse/recovery and complete authoritative-replacement acceptance. Preserve candidate admission and unfinished-work custody; #9026 fixed first-use recipes and #9673 guarded conversation replacement remain separate work. |
| [agent-codespaces](../../../plugins/agent-codespaces/README.md) | #10311 released bounded Windows application/provider work on Main `27aab5a3262cc331c0d73564a8a59dfd20fced01`, including historical A/B/A Owner/replacement and both provider-install orders. The separate Linux x86_64 CLI/provider controls below pass on explicit Main `1a83c1d250ae8093a47afab1107c3ebb9553a53e`, with Connection Owner disabled. Neither record qualifies every newer SDK or the whole app. | Existing owners retain Linux daemon lifecycle, source-change/reverse/failure integration, healthy foreign-provider collision coverage on Linux, other architectures, pristine OS, live backend/forwards and rollout. Keep stage-only environment preparation separate from full candidate readiness. |
| [agent-containers](../../../plugins/agent-containers/README.md) | #10347 released bounded configuration/provider callbacks on Main `f85bd817958a3ead2ba69182ad01f76bb11e4076`, with historical Windows installed front doors, both discovery orders, repeat and original-manifest preservation. The separate Linux x86_64 base-profile CLI/provider controls below pass on explicit Main `1a83c1d250ae8093a47afab1107c3ebb9553a53e`; neither record qualifies later binaries or the whole app. | Existing owners retain provider-exec/Paramiko, other platforms/architectures, pristine OS, source-change/reverse/retry, broader legacy/replacement and live credentialed fleet/backend acceptance. Preserve profile and foreign-container custody; callbacks do not borrow containers, query fleets or start a resident. |
| [agent-ssh](../../../plugins/agent-ssh/README.md) | Bounded copied-application CLI slice #10462 released in #10862, with historical native Windows cold/reuse and installed source-qualified commands. Separate Linux x86_64 BASE CLI controls below pass on explicit Main `c08449f9a3494a51afe882a37557f6749c005c2b`: stage, genuinely unstaged cold installation, reuse and source-concealed installed reads. This is neither later-binary/whole-app qualification nor the SSH Manager library or remote SSH service. | Preserve installed commands/resources and configuration. Existing owners retain distinct-source update/reverse/recovery, other platforms/architectures, pristine OS, real operator connections/forwards, broader legacy/adoption and full target conformance. Keep #10954 participant composition/writer admission and #8821 snapshot delivery separate; no service or library acceptance is inferred from this CLI witness. |
| [agent-dispatch-service](../../../services/dispatch/README.md) | Separate standalone root exercised explicitly by #10345, released with the plugin slice; its historical Windows cold/handoff, Source-unavailable reads and held-state/native cleanup remain credited. The Linux witness below independently exercises its own copied application and installed wrapper/dependency boundary at the same explicit Main pin as the plugin, not by inference from plugin results. | Retain its own installed entry and dependency boundary. Configured/native-service supervision, broader platform and source-change/reverse/recovery integration, full authoritative-replacement adoption and rollout remain separate under existing owners; no competing coordinator. |
| [agent-ssh-service](../../../services/ssh/README.md) | Bounded installed startup/fenced-transfer slice #10462 released in #10862. Native Windows copied-app cold/reuse, lost-prepare reply/retry, successor readiness and outgoing self-exit passed with fixture state preserved. Installed-wheel regression also covers the stale-writer notification gap. Separate Linux/Windows service CI passed; that is not immutable Linux app adoption. | Preserve enrollment, signatures/roles, connections and native relay custody. Keep immutable Linux app adoption, live operator Gateway/SSH integration, desktop-focus observation, broader recovery and rollout open. Incoming readiness/current selection and outgoing retirement remain distinct; this bounded witness does not establish complete authoritative replacement. |
| [agent-pull-requests](../../../plugins/agent-pull-requests/README.md) | Retained source/native work; app-level new-flow release open. | Qualify installed commands and supported cache/service discovery, without publishing reviews or changing PRs as an install side effect. |
| [agent-pull-requests-service](../../../services/pr/README.md) | Separate standalone service; bounded retained witnesses do not establish whole-app acceptance. | Prove service readiness and durable request/cache continuity, ownership-safe update/rollback and independent client skew. |
| [agent-chat-service](../../../services/chat/README.md) | Separate service/client entry points; retained catalog/packaging WIP, new-flow qualification open. | Package and test both declared commands, preserve member/cursor/state and native ownership; no implicit join, credential enrollment or new chat store. |
| [fleet-manager](../../../fleet-manager/README.md) | Shared source preparation/resources exist; new-flow native application acceptance/release not established. | Preserve registry, execution/maintenance and connection ownership. Prove installed client/service operations and independent startup; no installation-triggered remote rollout. |
| [agent-vault](../../../plugins/agent-vault/README.md) | New-flow acceptance open; existing installer/recovery contracts remain. | Use a disposable vault fixture; prove credential-free status and configuration retention plus owned service retirement, without unlocking a real vault or printing secrets. |
| [agent-machines](../../../plugins/agent-machines/README.md) | Retained Windows single-source qualification under #7028 covers copied-application cold/reuse, installed command/resources and Source-unavailable reads against the merged shared constructor correction. This snapshot evidence is not broader update/platform or release qualification. | Coordinate remaining source-change, failure/recovery, platform and publication scope under #7028 rather than repeating the retained Windows witness. Installation must not apply arbitrary machine requirements or create privileged tasks. |
| [budget-guidance](../../../plugins/budget-guidance/README.md) | Retained Windows single-source qualification under #7028 covers copied-application cold/reuse and installed offline reads against the shared correction, with fixture configuration/data preserved. Publication remains separately gated under its existing owner; this is not a new release claim. | Preserve the original qualified snapshot while resolving publication and remaining update/platform scope. Do not refresh a backend, introduce polling, change policy or rewrite the historical qualification to address an unrelated publication problem. |
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
The released SSH slice exercises its consuming CLI/service paths; it does not
turn the library into a separately qualified application or establish every
consumer's connection/forward continuity.

Rows marked open mean **not qualified/released by this campaign**, not proof
that no source or other contributor implementation exists. Existing app-specific
efforts, open PRs, retained WIP and resource reservations remain authoritative.

### Bounded CodeSpaces Linux CLI/provider record

The additional controls use explicit public Main
`1a83c1d250ae8093a47afab1107c3ebb9553a53e`, tree
`c08de7c4297b244928229c59e2da881d48edcd08`, on Linux x86_64 with
CPython 3.12.11 in a fresh container from a pinned, preprovisioned image.
This is not a merged-Dev selection or a pristine operating system.
The canonical source producer supplies only the CodeSpaces closure: sixteen
first-party dependency wheels are not sixteen converted applications.
The recorded source/wheel preparation takes 13 seconds in the preceding
phase; the later phase reuses those unchanged inputs. Its fresh canonical
toolchain preparation takes 7 seconds and profile preparation 10 seconds,
separately from the prepared-input application calls:

| Generated front-door control | Elapsed | Boundary checked |
|------------------------------|---------|------------------|
| Separate fresh `install --stage` root | 0.344 s | Environment created without selector, global-command or provider publication |
| Fresh **unstaged** normal `install` root | 2.562 s | Environment creation, actual selected completion, installed command and plain provider |
| Repeat normal `install` in that same root | 2.500 s | Same generation, unchanged completion/provider bytes and timestamps, protected fixture state |
| Fresh Bridge-config-first normal `install` root | 2.552 s | Other installation order and existing fixture-byte preservation |

Every call retains its 300-second limit. These measurements do not hide
staging inside a claimed cold call or include source/profile preparation as
installation time. Test execution is network-disconnected, non-root,
capability-dropped and read-only outside owned temporary storage, with no host
checkout, Docker socket or credential mount. Connection Owner is disabled
through the canonical adopted-repository configuration before application
calls; no daemon or backend readiness is inferred.

The installed module/generation, all 103 application-package wheel members
and three required assets match. Three direct calls to the real pinned Bridge
registry consumer cover app-first and Bridge-config-first discovery; the plain
provider's `legacy-unattributed` finding is expected. An unrelated invalid-entry
sentinel is preserved but does not qualify healthy foreign-provider collision
handling. Two installed CLI/module/resource reads pass with the original
checkout, payload and preparation paths unavailable. These are native controls,
not a claim that a pytest suite ran or an installed Bridge daemon was qualified.
Source/wheel, profile and native-proof archives are exported and readback-checked
before exact container cleanup. Subsequent parent review independently checks
their stored hashes and the container's absence.

The earlier missing-dependency and malformed-invocation attempts remain failed;
their lost partial snapshot receives no retention credit. The subsequent
credential-lookup warning is resolved only in the fixture by replacing
deprecated `UV_NATIVE_TLS` with supported `UV_SYSTEM_CERTS`, retaining the same
OS certificate store, hostname verification, configured feed and seasoning.
A disabled-owner configuration preflight is corrected through canonical
repository adoption before any application call. No production parser or trust
policy is changed. Full daemon lifecycle, legacy, source-change/reverse/failure,
whole-suite, live-backend, other-architecture and pristine-OS gates remain with
the parent effort.

### Bounded Containers Linux base-profile CLI/provider record

These controls use the same explicit Main commit and tree named above, on
Linux x86_64 with CPython 3.12.11 in a fresh container from a pinned,
preprovisioned image. The selected application is Containers' **base** profile,
with no extras or Paramiko. This is not a pristine operating system,
provider-exec qualification, current-Dev qualification or a new runtime release.
Canonical source/wheel preparation takes 14.045 seconds and profile preparation
6.594 seconds in the preceding phase. The corrected phase reuses those verified
inputs, not a built virtual environment; its one canonical Bridge-consumer
toolchain reconstruction takes 9.771 seconds separately:

| Generated front-door control | Elapsed | Boundary checked |
|------------------------------|---------|------------------|
| Separate fresh `install --stage` root | 0.193 s | Environment prepared without app completion, selectors, global command or provider publication |
| Fresh **unstaged** normal `install` root | 1.934 s | Nested environment created, selected completion, installed command and plain provider |
| Repeat normal `install` in that same root | 1.926 s | Exact immutable completion, provider, global-command and protected-state snapshots |
| Fresh Bridge-config-first normal `install` root | 1.928 s | Other installation order and unchanged existing fixture records |

All nine generated stage/install calls retain their 300-second limits; three
are expected refusals. Eleven calls to the real pinned Bridge registry consumer
cover both installation orders, enabled-payload precedence and disabled-payload
plain fallback. A healthy foreign container-namespace entry is admitted by that
consumer before normal installation refuses to replace it. Invalid top-level
configuration and a linked registry also refuse without app publication or
protected-file changes. Bridge classifies the copied payload commands but does
not execute them. The plain provider's schema-0 advisory remains expected.

All eight persisted protected fixture snapshots compare exactly. Before and
after reuse snapshots are saved before assertions: all fifteen immutable
records match, including bytes and timestamps. Normal installation still
activates: current/last-good selectors and the success record retain exact
contents while their timestamps refresh. This is not a no-selection-write
claim. An earlier aggregate assertion incorrectly required those mutable
activation timestamps to remain fixed; its failed report remains separate and
unchanged, with no retroactive credit from its unretained in-memory baseline.
Only the fixture assertion changes; no installer fix is claimed.

Four actual installed global-help/module/resource reads include concealed
original source, payload and profile inputs. The installed module resolves
inside its physical environment; all 83 package-member digests match the source
wheel and three installed Python package resources are read. These are native
controls, not pytest or a running Bridge daemon. Execution is network-disconnected,
non-root, capability-dropped and read-only outside owned temporary storage,
without host binds, a Docker socket or credentials. Reports and narrow input/
native-proof archives are exported and readback-checked before cleanup; parent
verification checks stored digests, persisted snapshots and both exact test
containers' absence.

Full source-change/reverse/retry and legacy/replacement acceptance, other
platforms/architectures, provider-exec, pristine OS, live credentialed fleet/
backend, whole-suite and rollout remain with the existing owners under #7028.
No fleet query/borrow, resident, host runtime update or deployment is performed.

**Bounded downstream composition (#10277; source handle `rusty-socket`):**
the additive `install_source.install_application` repository API joins the
existing retained Source/profile constructor to the copied application install
owner. Owned changes are that adapter, canonical generated copies, focused
tests and contract documentation; existing acquisition, builder, process,
publication and service owners are unchanged. Six focused native Windows
controls pass in 149.33 seconds, including a real synthetic-app fresh/repeat
install, changed-declaration refusal before callbacks, failed-start selector
rollback, distinct repair with the new declaration and stable command execution.
Completed slots retain their original declaration. This is source qualification,
not Main release, downstream source acquisition, resident-service readiness,
Linux qualification or host rollout. Required review/CI, actual source merge
and available external/native qualification remain before publication claims.
The parent Plan and remaining common gates below remain open.

### Bounded SSH Linux base-profile CLI record

These controls use explicit released Main
`c08449f9a3494a51afe882a37557f6749c005c2b`, tree
`d0f02bc30c28b470cc9171db03513d5c2023223a`, on Linux x86_64 with
CPython 3.12.11 in a fresh container from a pinned, preprovisioned image.
The BASE CLI profile has no optional extras, 14 wheels (one registry wheel),
one declared `agent-ssh` command and no setup/start callbacks. It is not the
SSH service or an independent SSH Manager library installation. Canonical
toolchain, source/wheel and profile preparation take 29.550 seconds in total,
separately from these actual generated front doors:

| Generated front-door control | Elapsed | Boundary checked |
|------------------------------|---------|------------------|
| Separate fresh `install --stage` root | 0.140 s | Nested environment only; no completion, selection or global-command publication |
| Fresh **unstaged** normal `install` root | 0.807 s | New nested environment, canonical completion, selection and real installed command |
| Repeat normal `install` in that same root | 0.801 s | Same physical environment, exact immutable members and protected fixture state |

All three calls retain the 300-second install bound. Nineteen further actual
installed reads cover global/slot/module help and version, printed synthetic
profiles, static target selection, explicit topology and an unavailable
optional Worktrees owner. Four invalid-input commands refuse with exit 2
without changing the fixture. The explicit snapshot leaves effective routes
and connectivity unobserved; a missing optional owner remains unavailable,
not an empty successful mesh. No reachability probe, SSH connection, forward,
profile write, backend or service is exercised.

The original checkout, source/profile inputs and cache paths are concealed
before repeating the installed reads. Global and slot commands still use their
locked installed project; direct module/resource inspection resolves inside
its physical environment. All 49 admitted non-RECORD wheel members, including
44 package members, match the installed footprint. This does not requalify
the canonical constructor after source concealment.

Persisted protected snapshots and complete read/refusal baselines compare
exactly; 525 immutable slot entries are unchanged on reuse. Current/last-good/
success records retain their contents while their metadata and directory
timestamps refresh, so this is not a no-selection-write claim. The first
version assertion omitted the CLI's documented name prefix; its failed fixture
record remains retained, and correcting the expectation did not replay the
stage/cold/reuse installations or change production code.

Execution is network-disconnected, non-root, capability-dropped, read-only
outside owned temporary storage and resource-bounded, without host binds,
socket access or credentials. Reports, input and native-footprint archives
are exported and fully read back before exact fixture cleanup. Independent
verification checks archive hashes, actual wheel/installed members, persisted
snapshots, raw before/after containment and the exact container's absence.
These 26 native commands are not pytest cases, pristine-OS proof, a new runtime
release or host/fleet deployment.

Distinct-source update/reverse/recovery, other roles/platforms/architectures,
broader legacy/adoption, real operator Gateway/SSH integration and full
authoritative-replacement conformance remain open under #7028 and their
existing owners. This evidence does not take over #10954 or #8821.

### Bounded Dispatch Linux plugin and standalone record

The two independent application roots use explicit released Main
`0dbbdd374e1af0b6443c84cead45102333855da3`, tree
`44dab0808db63ebe92b2ae9d54cc43d930857052`, on Linux x86_64 /
CPython 3.12.11 in a fresh container from a pinned, preprovisioned image.
This is not a pristine OS or current-Dev qualification. Canonical source/profile
preparation takes 51.103 seconds separately. Both profiles select no extras;
the plugin closure contains 30 distributions and the standalone 31, with 18
registry wheels each. Both declarations retain no setup callback and the real
`__source-start` callback; this is not a service-disabled CLI substitute.
The optional supervisor is unconfigured and no live tasks are processed.

| Actual generated control | Plugin | Standalone | Boundary checked |
|--------------------------|--------|------------|------------------|
| Separate fresh `install --stage` root | 0.257 s | 0.267 s | Environment only; all seven protected active-state scopes unchanged |
| Fresh **unstaged** normal `install` root | 4.905 s | 4.331 s | Copied inputs have no environment/completion; real selected coordinator readiness |
| Repeat normal `install` in that same root | 2.709 s | 2.038 s | Same native process/birth, endpoint, configuration and observed held work |

The 300-second install and 60-second readiness bounds are unchanged.
Readiness is authenticated and bound to the actual selected source/profile,
physical interpreter, native identity and active route. The standalone
`agent-dispatch-service` entry delegates to its installed Dispatch dependency;
it does not duplicate that implementation or depend on plugin registration.
All 1,185 plugin and 1,192 standalone non-RECORD runtime wheel members match
both captured input bytes and actual installed footprints.

Actual global, slot and module version/health/list reads succeed with the
original source, inputs, profiles, runner and cache paths concealed; baked-root
global commands also work without the root override. The corrected read phase
executes 28 commands in 29.547 seconds, including four expected HTTP 401 results.
All source paths are restored. Constructor-after-concealment is not tested.
Full immutable slot snapshots, configuration, fixture home, foreign state,
routes and complete observed held-task/reservation/event records remain exact.
Reuse refreshes success-record metadata without changing its content.

Two original fixture failures remain retained, not retroactively passed.
The first adds unsupported `--json` to the already-JSON `list` command.
Correcting that spelling does not replay installation. The subsequent
physical-state assertion is overbroad: independent offline comparison proves
all 130 schema objects, 42 tables, columns and 14 complete rows unchanged in
each database, with `ok` integrity results. Only database header counters
change; observation helpers invoke a migration-capable constructor whose
index rebuild corresponds to that signature. Exclusive statement-level SQL
causality was not captured. Each log retains its exact prefix and appends
18 GET access records. Bounded logical-work preservation is accepted, not
physical database/log immutability or a fully green original fixture report.

All 1,754 prepared regular input files preserve captured bytes and archive
metadata; whole acquisition-checkout pre/post bytes were not captured.
Execution is network-disconnected, non-root, capability-dropped, read-only
outside private temporary storage and resource-bounded, without host binds,
socket access or credentials. Original/corrected/native-cleanup archives are
exported before shutdown, fully read back and retained with matching hashes.
Both original coordinators exit through their installed stop owner with
kernel pidfd observations before normal container removal; independent parent
verification confirms exact container absence and venue-resource settlement.

This is a bounded install/readiness/read witness under #7028, not a new
runtime release, host installation or rollout. Configured-supervisor,
native-service-owned, flat/nonprofile, distinct-source/reverse/recovery,
broader platform/pristine-OS/live-backend and complete authoritative-replacement
acceptance remain with existing owners, including the separate #9026 and
#9673 work. Neither this record nor the published bundles complete the parent.

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
