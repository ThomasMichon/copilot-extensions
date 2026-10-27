# Lean Runtime Dependencies

- **Slug:** `lean-runtime-dependencies`
- **Repo:** copilot-extensions
- **Branch(es):** independent sequential slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active
- **Vision:** [Plugin services](../../../visions/plugin-services/README.md):
  `capability-sized-runtime-dependencies`, `compatible-helper-runtime-reuse`;
  [agent-index](../../../visions/plugins/agent-index/README.md):
  `lightweight-client-and-declared-host-service`
- **Umbrella issue:** #6234
- **Related issues:** #5768, #5873, #4876, #6143

## Guiding Intent

Reduce repeated installation, package footprint and cold-start work without
removing useful capabilities or weakening independently installable services.
The first wins are removing the unused worktrees UI dependency and completing
the index client/service dependency boundary. Compatible separate-process helper
reuse is a first-class investigation, not a preselected mandatory global broker.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, integration, sequential PRs and completion | Managed upstream worktree |
| Helper investigator | Bounded shared-runtime and ZDD evidence | Read-only delegated investigation |
| Compiler-free implementer | Compiler-free first-party execution amendment and owner integration | #6234 / #8403; managed source worktree |

## Coordination

- **Topology:** independent sequential slice PRs; one implementation writer per slice.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Helper investigator returns evidence and a compatibility proposal;
  no concurrent edits or publication from the coordinator's worktree.
- **Handoff:** this README owns the completion gate; each successor reads the
  current slice and journal. No phase completion closes the campaign.
- The index service distribution and rollout remain owned by
  [agent-index standalone service](../agent-index-standalone-service/README.md)
  (#5768). This effort owns dependency boundaries and measured footprint changes,
  not a competing index service implementation. Coordinate overlapping installer
  work with #5873 and open PRs before editing.
- Shared daemon ownership composes with #6143; artifact selection composes with
  #4876. Do not recreate either campaign or preempt another contributor's PR.

## Context

[Dependency audit](audit.md) preserves the measured motivation, package frequency,
source evidence, recommended reductions and limits of the measurements.
[Shared-runtime investigation](shared-runtime.md) defines the decision gate for
installer de-vendoring and candidate shared ZDD fronts.
[Profile results and remaining acceptance](profile-results.md) consolidates the
delivered source changes, fresh-install measurements, validation limits and
explicit unresolved owner boundaries.

Promotion already expands canonical shared-library references into independently
shippable payloads. That source-sharing mechanism does not itself avoid repeated
venv installation. Installed compatible helpers can potentially be reused over
efficient process boundaries while payloads remain self-contained.

## Request

Operator direction, with only private destination names abstracted:

> This is great analysis. Ensure this is documented as the motivation for an
> effort (and put a log/journal entry in [the private knowledge repository]), and
> then plan an effort to go down your priority list and tackle the biggest
> issues. Textual removal from agent-worktrees is an obvious win, as is finishing
> the server/client split in agent-index. I also think there's an opportunity to
> have our service installers explicitly "de-vendor" shared code, so that if
> installation of one service already put together a shared-lib venv then another
> install doesn't have to. This of course only works if we have some efficient
> separate-process helpers. ZDD fronts are a good example: we might get away with
> having only one instance of ZDD, for example.
>
> Use your analysis to drive vision updates, effort buildouts, and get started
> on work.

The operator confirmed the slug `lean-runtime-dependencies`.
The additional package/profile priorities below originate in the audit and are
marked agent-recommended; exploratory backend replacements are not commitments.

### 2026-10-16 - Operator-approved zero-configuration default

Follow-up direction, with only the private adopter name abstracted:

> We need to make an executive decision on [the adopting harness], on the
> assumption that most users will never adjust local config for themselves.
>
> Great. Update and drive the vision and effort for this

**Approved decision summary:** the adopting harness owns a complete baseline,
not a requirement for personal approval configuration. Prefer verified
release-approved exact profiles; automatically select a distinct governed
source lock when artifact availability/admission prevents that mode. CPU is
the fresh engine default; retain explicit accelerator intent and explicit
unprofiled migration. Reuse admitted unchanged inputs. Existing feed,
seasoning, integrity and provenance restrictions remain authoritative.
Optional local/knowledge overlays are not prerequisites. This authorizes
driving source implementation, not production rollout, model/corpus mutation or
new package feeds.

### 2026-10-26 - Compiler-free execution requirement

Operator verbatim:

> Great. I just want to avoid having to compile anything at either end. No native-code in the repo, and no on-demand native compilation on device.

The operator accepted thin shell launchers with shared standard-library Python
for process-lifetime safety. This supersedes the earlier prebuilt-C/source-build
choice in leased-daemon custody; compiling at release time is not a substitute.
The [amendment](compiler-free-execution.md) preserves the safety contract and
separates this required subtraction from agent-recommended implementation
sequencing. It does not authorize installed changes.

The operator clarified the prerequisite owner:

> Installing Python is a prereq of the whole harness, which is installed by agent-machines

Use that existing machine-provisioning boundary, not a second embedded-Python
download or interpreter installer in each helper. The amendment's
[Python prerequisite boundary](compiler-free-execution.md#python-prerequisite-boundary)
defines the resulting Windows ordering and qualification.

### 2026-10-27 - Setup-owned PATH Python

Operator verbatim:

> We don't need any signature verification and any of that stuff; it's way more robust to *trust* python from PATH, because this allows the system to be more composable. All this extra logic does is slow stuff down. It's better to have our own upfront setup flow from the harness set up the correct Python, and then just call `python` on path. Now, it's good if we can avoid putting random versions of python on PATH, like versions from our own worktrees or temporary folders, but that's a matter of us keeping tidy, not adding signature verification and everything.

This supersedes the earlier interpreter-admission design: upfront setup owns
supported Python and PATH hygiene; helpers trust normal PATH selection without
signature, registry, physical-path or disposable-location admission gates.
Process identity, containment, leases, authenticated endpoints and safe cleanup
remain separate requirements. The [amendment](compiler-free-execution.md#python-prerequisite-boundary)
owns the revised implementation and validation boundary.

## Plan

### Phase 1 - Reviewed intent and dependency baseline
- [x] Land this proposal, the audit motivation and vision amendments through review (#6235).
- [x] Refresh declaration/import and installation-profile evidence against the
  execution snapshot; record direct/transitive, active/retained and logical/physical distinctions.
- [ ] Coordinate overlapping index and daemon work with existing owners.

### Phase 2 - Worktrees UI dependency removal
- [x] Remove Textual from agent-worktrees while retaining Worktree Manager's UI.
- [x] Preserve version comparison and Manager process dispatch without relying
  accidentally on removed transitive packages.
- [x] Add dependency-minimal regression evidence, update directly related docs
  and ship the slice with a changefile.

### Phase 3 - Index client and hosted-runtime separation
- [x] Complete lightweight client import/install boundaries and route hosted
  controller, indexing workers and foreground commands through their owning runtime.
- [x] Eliminate duplicated store/server installs only after replacing the
  primary-runtime fallback with a supported explicit outcome.
- [ ] Preserve native and standalone-service contracts, configuration, queue,
  corpus state and the separately managed warm embedding engine.
- [x] Isolate legacy stdio MCP in its own opt-in extra without removing
  agent-facing tool capabilities. _(agent-recommended)_

### Phase 4 - Largest optional installation costs
- [ ] Govern explicit compatible CPU/GPU engine wheel profiles; eliminate
  accidental accelerator downloads and unsafe install-then-swap assumptions.
  _(operator-approved default; coordinate artifact ownership with #4876)_
- [ ] Implement the reviewed [zero-configuration default](zero-configuration-defaults.md)
  through the existing installation authority: authentic release profiles first,
  automatically authorized governed source fallback, optional overrides only.
  Do not require users to assemble an approval map or wait for artifact
  publication before source installation.
  Configuration-intent resolution merged in #8494; authenticated selection,
  governed closure and constructor acceptance remain open.
- [x] Make restricted-SSH Paramiko provisioning genuinely opt-in.
  _(agent-recommended)_
- [x] Replace full-table pandas statistics materialization with projected
  bounded aggregation. _(agent-recommended)_
- [x] Decide syntax grammar extras and unseeded installer profiles from measured
  benefit; retain supported fallback installation. _(agent-recommended)_

### Phase 5 - Installer de-vendoring and shared helpers
- [x] Execute the [shared-runtime investigation](shared-runtime.md), including
  separate-process reuse and a shared ZDD front candidate.
- [x] Review the chosen compatibility, lifecycle and installation-cell design
  before implementing a reuse mechanism.
- [ ] Implement the smallest accepted reuse slice, or explicitly transfer an
  accepted-but-deferred design to a named tracked objective. If evidence rejects
  reuse, retain per-service execution and close that implementation path with
  measured costs and the rejection decision; do not transfer rejected work.

### Phase 6 - Close the measured delta
- [x] Publish before/after profile footprint and fresh-install evidence, with
  running-state proof for any authorized rollout.
- [ ] Remove redundant package-preparation transactions identified by the
  installation-latency audit, preserving build prerequisites, source/feed
  admission, immutable construction and health/cutover semantics.
- [ ] Resolve or explicitly transfer every implementation and validation item;
  experimental ONNX/storage replacements remain separately tracked decisions.
- [ ] Update reality docs and archive only after the campaign completion gate.

### Phase 7 - Compiler-free first-party execution

- [x] Review the [compiler-free execution amendment](compiler-free-execution.md)
  and `plugin-services/compiler-free-first-party-execution` intent before
  implementation (#9120).
- [ ] Complete the shared daemon replacement through the reactivated
  [leased-daemon instance owner](../leased-daemon-instances/README.md), preserving
  its existing registry, lifecycle protocol and safety guarantees.
- [ ] Replace executable embedded Windows helper compilation and runtime code
  emission through their existing installation-context, operation-lock,
  update-driver, process and session-host owners. Establish the existing
  setup-owned Python prerequisite before these helpers, invoke normal PATH
  Python without interpreter attestation, and remove the superseded signature,
  registry and physical-path gates rather than introduce another acquisition
  or admission path.
- [ ] Remove first-party native sources, generated copies, custom compiled
  artifacts, compiler build hooks and release construction/verification
  compilation; retain normal integrity and source admission.
- [ ] Prove compiler-free fresh construction, installed invocation, expiry and
  cleanup on the supported platforms, with regression guards against both
  checked-in native code and embedded/on-demand compilation.
- [ ] Reconcile source-managed maintenance and native adopter gates from
  actual evidence. A source merge does not authorize rollout or close their
  independent acceptance.

The Linux slice merged in #9129. The leased-daemon owner's
[implementation journal](../leased-daemon-instances/journal/2026/10.26%20compiler-free-amendment.md#linux-implementation-merged)
records compiler-absent construction, installed lifecycle qualification and
namespace-bound monotonic leases. Windows bootstrap/embedded-code replacement,
whole-campaign acceptance, release attribution and authorized rollout remain
open.

## Validation Plan

- [x] Unit and dependency-contract tests cover each changed declaration,
  optional extra, import boundary and helper compatibility rule.
- [x] Simple end-to-end checks exercise actual client/Manager/worker process
  boundaries in dependency-minimal environments, not only mocked imports.
- [ ] Clean-room first installation proves each affected solo plugin/profile,
  combined installations, reuse and missing/incompatible helper outcomes.
- [ ] Windows, Linux and macOS checks preserve supported installer and process
  behavior; unavailable platform lanes require an explicit documented safe
  fallback or feature exemption, not assumed parity. Checks preserve
  headlessness, caller-Job independence,
  graceful cutover, ownership and rollback; shared-runtime concurrency includes
  two installers and two independent marketplace installations.
- [ ] Index integration preserves authenticated routing, HTTP/SSE/MCP contracts,
  background jobs, retrieval semantics and durable state across upgrades.
- [ ] Fresh adoption with no runtime-profile overrides proves the shared
  baseline, CPU closure and automatic governed fallback; unchanged warm reuse
  makes no resolver/feed call. Exercise stricter policy, supplied revocation,
  retained accelerators, unprofiled migration and all-modes-blocked retention.
- [x] Every slice records applicable unit, integration, clean-room and available
  live-venue evidence, or a specific reason a tier is unavailable.
- [x] Measurement separates compressed downloads, unpacked logical files,
  hardlink-aware physical allocation, model/cache data and retained version slots.
- [ ] Measure complete cold and unchanged-path update latency from the existing
  updater's phase logs. Footprint reductions or isolated constructor timings
  alone do not establish an end-to-end installation speedup.
- [ ] Publication checks, changefiles, review, merge and release attribution are
  satisfied; rollout authorization and its completion owner are explicit.
- [ ] The compiler-free amendment's complete source, packaging, bootstrap,
  kernel-identity, Windows Job/headlessness and minimal-machine checks pass;
  unsupported lanes remain explicit blockers, not assumed parity.

## Proposal

Approved and merged in #6235. Implementation proceeds in reviewed slices.
Shared reuse is a design investigation; neither one global ZDD process nor one
shared mutable venv is presumed acceptable.

The compiler-free Phase 7 amendment merged in #9120 after current-head approval
with no findings and successful required CI. The Linux shared-instance
replacement subsequently merged in #9129; the next source slice replaces the
Windows compiled interpreter selector with setup-provided PATH Python. Neither
merge closes the complete bootstrap, platform, release or adoption gates.
The prerequisite clarification and Linux merge reconciliation then merged in
#9162. Windows implementation now proceeds from the existing machine-owned
Python prerequisite, not another interpreter acquisition mechanism.
The private original-parent Windows Job primitive merged in #9170. It has
native parent-loss and descendant-settlement evidence, but no compiled caller
has switched and the interpreter admission boundary remains open.
The unused stock-framework file guard merged in #9181, including native
directory sharing and same-process refusal-cleanup controls. The cached-only
held-file signer merged in #9187. The unused Core file/final-path adapter
merged in #9197 and the Core signer in #9204. Retained ancestors and the current
drive-query primitives merged in #9210. Complete canonical-name, short-alias,
drive-stability and source admission remain open before any compiled bootstrap
migration; these private primitives do not enable a caller.
The unused physical-address custody and per-component long-name candidate
subsequently merged in #9235. Its bounded dual-shell qualification does not
establish complete interpreter/source admission or actual physical-executable
and helper startup. The 2026-10-27 PATH-Python direction removes interpreter
attestation from the migration gate. Actual compiler-free helper startup,
process/Job behavior and caller compatibility still require qualification.

### Current execution boundary

The delivered dependency reductions, helper investigation and compatibility
boundary have cleared review. This is not approval to enable a production
artifact fast path. The remaining engine-profile application (#6737) depends on
#4876's authenticated authority integration and actual permitted exact closures.
Release-profile publication is required for artifact-mode acceptance, not a
prerequisite for the independently verified source mode approved in #7066.
The selection contract's design merged in #6795; actual approval adapters,
feed-compatible closures and Phase 3 consumer admission remain open. Do not
invent a substitute resolver here. Held construction merged in #6598; that is
not profile admission or proof of this campaign's remaining native acceptance.

The existing owners have the concrete acceptance requests; no reply or accepted
transfer is assumed. To drive rather than merely wait on that prerequisite,
this campaign contributes the bounded
[exact-profile contract](../governed-python-artifact-promotion/exact-profile-contract.md)
to #4876's existing authority design. The inert exact record and pure selection
API merged in #7044, not approval-source integration, a loader, publication or
acquisition. The repository-only actual-wheel verifier merged in #8399; it
closes the byte/metadata comparison prerequisite, not authority, governed
acquisition, reusable admission or native engine integration.
The bounded shared inspector (#8419) and invocation-owned exact wheel snapshots
(#8523) now supply the underlying byte and custody primitives. Neither approves
a profile, authorizes acquisition or supplies an admitted reuse receipt.
The existing-owner configuration-intent reader merged in #8494, including
management publication holds and policy invalidation. It does not close the
authenticated adopting-policy/source adapter or its actual governed closure.
The release-authentication adapter (#8568) and policy/release planner (#8586)
also merged under the artifact owner's continuation. They remain repository-only
preparation: no independently locked source closure, governed acquisition or
production consumer is enabled. Coordinate actual source-lock/acquisition
composition with that owner under #4876 rather than duplicating it.
#6941 retains the separate contributor-owned
candidate-input generator. Native/standalone queue, schema, canary, warm-engine and
Darwin acceptance remain with #5768. Apply the reviewed profile contract under
#6737, composing the merged #6598 construction and #6707 admission rather than
creating another constructor. The effort and retained runtime/state stay open.

The next stretch follows the operator-approved
[zero-configuration default](zero-configuration-defaults.md). A harness-supplied
policy reaches the same authority seam for release and source modes; hand-built
adopter approval files are not the default integration. The amended intent and
plan merged in #7066. The next implementation boundary is the authenticated
adopting-policy/source adapter and its actual governed closure, not another
profile-record API or a competing admission constructor. Direct invocation of
the trusted setup by a user or authorized agent is the dependency-operation
authority for the selected configuration. Carry it through the existing
installation/custody owner rather than introducing a separate package approver;
configuration reads and candidate discovery alone do not establish invocation.
Continue the source/helper consumer through direct reviewed contributions;
unreliable peer acknowledgement is not a prerequisite for implementation.
Reconcile overlapping source changes through the PR flow rather than leaving
the campaign blocked. Preserve the accepted immutable-helper-artifact boundary
from #6767: no rejected routing broker or shared mutable venv, and no claimed
transfer without acceptance by a named tracked objective.

## Journal

### 2026-10-27 - Trust setup-provided PATH Python

- Captured the operator's explicit removal of interpreter signature and related
  attestation machinery in Request, the service-model vision and the canonical
  compiler-free amendment. Supported Python and durable PATH hygiene belong to
  upfront setup, not a replacement blacklist in each helper.
- Physical interpreter/source-admission experiments are no longer prerequisites
  for this campaign. Their results remain historical; no caller or installed
  runtime changed from them. The generic fragment-loader compatibility fix is
  separate from interpreter trust and does not create an admission gate.
- Next implement normal PATH invocation and remove compiled interpreter
  admission through the existing bootstrap and packaging owners, then continue
  the shared lifecycle migration. Preserve process/Job, endpoint, lock and
  cleanup safety; review, complete platform qualification and rollout remain
  distinct gates.

### 2026-10-27 - Preserve physical helper-source addressing

- Read-only inspection on both qualified shells observed native OS
  architecture, physical-path version metadata, a bounded PE-header read from
  the original retained handle and cached PSF signature verification without
  closing that original handle. These are inspection interfaces, not an
  interpreter-selection or helper-startup verdict.
- A controlled existing-dependency startup comparison separated an ordinary
  filename from a physical one while keeping all other process-start fields
  unchanged. The ordinary baseline completed; the physical PS5 managed seam
  failed with native error 87. No caller falls back to the ordinary path from
  that failure. Existing stock process-creation metadata is being qualified
  before any alternative seam or policy change.
- Direct stock process-creation controls subsequently failed with error 87
  for both null and explicit physical application names. A single ordinary
  application-name control completed with the same full command line and
  three-pipe protocol, including successful returned-handle ownership and
  closure. This narrows the observed failure without establishing a general
  namespace restriction or authorizing an ordinary-path production fallback.
- That ordinary application-name control reported a physical `sys.executable`
  while its standard-library paths and module origins remained DOS-addressed.
  The command line's executable spelling is therefore not image-selection or
  complete interpreter-namespace proof. Physical startup and prerequisite
  admission remain open.
- The baseline separately proved that resolving a physical source path
  reintroduces an extended DOS drive path. The Python fragment loader now keeps
  a `GLOBALROOT` entrypoint's lexical parent; ordinary and relative entrypoints
  retain their previous resolution behavior. This does not change SDK input
  normalization, source admission, interpreter authority or an active caller.
- Regenerated all declared copies through the installation-context owner.
  Five focused loader controls and four vendoring regressions passed through
  the bounded supervisor. Actual complete held-source import and physical
  executable startup remain distinct native gates; publication and review
  are still required.

### 2026-10-26 - Physical-address canonical custody candidate

- Continued the reviewed compiler-free bootstrap slice without changing a
  caller. A single strict current-volume mapping now supplies an internally
  derived physical address; root, ancestors and leaf remain retained while
  each actual long name is checked. DOS device aliases, wildcard components,
  nonphysical mappings and nonfixed volumes refuse explicitly.
- Reused the existing handle-open/identity worker and reverse-order closure.
  Windows PowerShell uses existing native declarations; Core uses its existing
  file-system and DriveInfo wrappers. The Core long-name getter returns a
  non-boxable span, so bounded owned memory reads the qualified existing
  value-type record without defining a type or emitting code.
- Native callability and complete helper qualification remain distinct.
  The new source and controls are under qualification; no full native pass,
  interpreter/helper admission or migration is claimed here. The parent
  Plan and Validation Plan remain open.
- The first complete native candidate run exposed an observer defect. A
  bounded diagnostic confirmed on both shell editions that a new live
  allocation could reuse an earlier freed address; the observer then rejected
  the address before calling the real free. Replaced its historical-address
  check with live-allocation tracking and added deterministic address-reuse,
  double-free and unknown-address controls. The helper is unchanged, and the
  corrected complete native packet remains required before qualification.
- The corrected packet isolated four remaining alias-control failures. A
  bounded diagnostic proved that both shells already expanded the aliases and
  rejected their original spelling before any guarded open; the controls had
  required only the later long-name error. Preserve that early refusal and
  separately exercise the post-open defense with an unexpanded-input
  validation control and actual native alias lookup. No production validator,
  helper policy or caller changed; complete fresh qualification remains open.
- Final exact-source qualification passed all 270 selected cases, with zero
  failures or skips and 2,542 deselected, on Windows PowerShell
  5.1.26100.9711 and Core 7.6.9/CLR 10.0.14. The count comprises five portable
  source contracts and 265 host-dependent tests, not native-call counts.
  All eight actual-alias controls ran, separating stock early normalization
  from the explicitly injected unexpanded-input post-open defense.
- The packet used a fresh source-only snapshot and its own canonical offline
  environment. All eleven tested source files matched the frozen export and
  repository bytes before and after execution. Selected controls asserted
  search/file closure, allocation release and zero live state; the original
  bounded Job owner settled all contained processes before fixture cleanup.
  Runtime limits and the twenty-second PowerShell child bound were unchanged.
- The final portable packet separately passed five source contracts with 265
  explicit native skips. Neither packet proves actual DOS-remap races,
  interpreter/helper startup, complete source admission or desktop behavior.
  Review and merge remain required; next compose the existing Python
  prerequisite and complete helper closure before switching any caller.
- #9235 merged as `0b65e67063d41777193128eb4c6d192782f83ae1` after
  genuine current-head Copilot approval `5663099778`, no inline or open review
  issues and successful required CI. Managed publication retained all eleven
  qualified source files unchanged, and the continuing worktree advanced
  onto the verified merge without finalization.
- Both entire owning READMEs and their complete Plan/Validation Plans were
  re-read after merge. No aggregate item closes from this unused candidate.
  Next qualify physical-path version/architecture/signer inspection and
  retained-handle reads for the existing Python prerequisite, then its actual
  complete helper handoff. No new interpreter acquisition, shell prerequisite,
  caller switch or production rollout is authorized by this source merge.

### 2026-10-26 - Retained ancestors and current-drive query candidate

- Added unused root-to-leaf custody composition over the existing file guard,
  with a bounded component count, no-follow directory/file checks and reverse
  release on partial admission. Cleanup attempts every retained entry and
  preserves both admission and cleanup errors.
- Added an unused current-drive mapping query through the exact existing
  Windows PowerShell and Core declarations. Bounded preliminary native
  callability succeeded on both editions; Core accepts owned IntPtr arguments
  for its existing pointer parameters. The Core raw thunk does not capture
  native last error, and the Windows PowerShell StringBuilder marshaler does
  not expose the native MULTI_SZ tail. The implementation preserves these
  limits rather than reporting stale errors or pretending to observe the tail.
- Earlier metadata searches hit their declared limits; they do not prove
  Windows PowerShell final-path support absent. The exact symbolic-link
  helper type has no matching final-path method in its completed narrow
  inspection. Other incomplete scopes remain unknown.
- Complete source-helper tests, native composed-custody qualification,
  independent review and merge remain gates. These primitives do not decide
  canonical names, short-name aliases, drive stability or interpreter/source
  admission, and no compiled caller or installed runtime changes. This targets
  a prerequisite delta of `plugin-services/compiler-free-first-party-execution`;
  no aggregate Plan or Validation Plan item is complete from this candidate.
- The first native preparation stopped before environment construction because
  expected PowerShell hashes described Git's LF blobs rather than the
  repository's CRLF archive exports. Actual archive entries and extracted
  bytes agree exactly, and their LF-normalized content matches Git; no source
  normalization or repair was performed. Qualification must bind the actual
  exported bytes. The exact component-limit fixture now uses one-character
  names to avoid depending on long-path settings, and the count gate precedes
  framework normalization.
- The corrected source packet passed all 155 controls with zero failures or
  skips on Windows PowerShell 5.1 and Core 7.6.9: four portable source contracts
  and 151 host-dependent tests, not 151 asserted native API calls. Test time
  was 81.88 seconds, 100.69 seconds including preparation and settlement.
  The fresh canonical environment used existing offline inputs; all nine bound
  source/runner files and the original signed executable remained unchanged.
  The selected cleanup assertions and canonical Job/sandbox settlement passed.
  The final hardened Linux packet passed four source controls with 151
  explicit native skips.
- Corrected a containment-description error without changing the runner:
  the original canonical monitored thresholds are 128 target processes,
  4096 MiB peak Job memory and 2048 MiB temporary storage; kernel emergency
  ceilings are 257 total Job processes and 8192 MiB Job memory. There is no
  per-process memory cap. The same canonical source governed the earlier
  signer packet. Monitored thresholds must not be reported as kernel caps.
  Independent review and merge remain gates, and complete canonical-path,
  interpreter/source admission and compiled-caller replacement remain open.
- Review identified a file-mode trailing-separator path that composition could
  silently shorten before opening. The shared validator now refuses non-root
  trailing separators for both guard forms before binding or opening anything,
  while preserving drive-root directory custody. Existing-file/directory,
  zero-output/before-binding and root-directory regressions cover the class.
  The earlier 155-control packet is historical evidence for the preceding
  source; final-candidate native requalification remains pending.
- The initial CI attempt also caught incomplete shared-library changefile
  coverage. All eight declared installer-engine consumers now have release
  entries in this contribution; no helper or caller behavior changed for that
  metadata-only repair.
- The final corrected packet passed 169 controls with zero failures or skips,
  including the existing-object trailing-separator refusals and valid
  root-directory custody on both hosts. Test time was 87.27 seconds, 101.35
  seconds including preparation and settlement. All nine bound source/runner
  files remained unchanged and the canonical cleanup completed. The matching
  hardened Linux packet passed four source controls with 165 explicit native
  skips. Current-head independent review, required CI and merge remain gates;
  no complete canonical-path or interpreter/source admission is inferred.
- A later review identified the .NET regex end-anchor exception for a final LF
  in a drive value. The drive grammar now uses absolute start/end anchors.
  Malformed-input controls preserve newline, NUL and Unicode separator data
  through an encoded test transport, require rejection before binding and
  assert zero output. Focused corrected-drive native qualification is pending;
  the preceding combined packet continues to cover unchanged custody/signer
  code, not this new drive-validator revision.
- The final drive revision passed its focused 57-control native packet with
  zero failures or skips on both shell editions, including faithful transport
  of all 12 malformed values, zero binding calls and zero helper output.
  Test time was 32.71 seconds, 49.95 seconds including preparation and
  settlement. All nine bound source/runner files and the original signed
  executable remained unchanged; canonical cleanup passed. The focused
  hardened Linux source control passed with 56 explicit native skips.
  This focused result complements the preceding 169-control custody/signer
  packet; no 181-control combined rerun is claimed. Current-head review and
  merge remain gates, and complete canonical/interpreter admission stays open.
- #9210 merged as `506a17c83760bf1811cd1a35e18adc75519b7833` after
  exact-head Copilot approval `5662234581`, no active review issues and
  successful required CI. Both entire owning effort READMEs and their
  Plan/Validation Plans were re-read after merge; all aggregate compiler-free
  and bootstrap obligations remain open.
  Post-merge reconciliation retained the original head and dropped only
  provider-confirmed merged history before the managed sync succeeded.
  No unmerged tail was discarded, and the worktree remains active.
- A bounded, read-only Windows PowerShell witness qualified existing physical
  volume-path opens, exact file/directory identity comparisons, the stock
  long-name getter after handle retention and physical-root drive-type queries.
  This is two-target callability, not short-alias refusal, remap-race,
  equivalent canonical-path or interpreter-startup proof. Next compose and
  qualify canonical namespace/name and source custody through the existing
  Python prerequisite owner before changing a compiled caller. No additional
  interpreter acquisition, new shell prerequisite or runtime rollout is enabled.

### 2026-10-26 - Core held-file signer candidate

- The existing typed Core `WINTRUST_DATA` and `WINTRUST_FILE_INFO` passed a
  full-byte owned-heap roundtrip through public Marshal APIs, including their
  pointer fields. All five owned allocations were freed before host exit.
  That witness made no WinTrust/provider/library calls and is not native
  signer or interpreter admission.
- Added an unused Core adapter through those shape-checked existing records
  and stock by-reference WinTrust imports. Preserve no-UI/cache-only flags,
  the caller's exact file handle and returned provider state through CLOSE,
  including exceptions after a real verification call.
- Source verification rejected the candidate's public loader choice:
  CoreCLR first tries an altered-search-path load for absolute paths, stripping
  the requested dependency-search flags even through the three-argument API.
  A matching loaded module path cannot prove the original System32-only
  dependency policy. Qualify an existing stock flag-preserving binding before
  publishing Core signer support; do not emit or compile another binding.
- Core has no provider signer/certificate records in the inspected managed
  scope. The shared x64 `wintrust.h` provider ABI remains the extraction
  contract, with native signed-file/provider-state qualification required;
  neither scoped absence nor pointer roundtrip is treated as impossibility
  or positive signature evidence.
- The candidate's combined dual-shell native packet is pending. No compiled
  caller, synchronizer or installation path enables the helper; complete
  canonical path, drive/ancestor/source closure and shell handoff remain open.
- The first candidate passed 73 combined native-host tests without skips,
  including actual Core provider extraction and exception-path state cleanup.
  That did not qualify the rejected public loader's dependency-search policy.
  A subsequent bounded metadata pass found existing CoreLib managed
  `LoadLibraryEx(String, IntPtr, Int32)` and BOOL `FreeLibrary` wrappers.
  The corrected candidate validates those declarations and passes the
  original flags directly, with exact path/flag/type/module-reference controls.
  Its native qualification remains pending; no earlier signer pass waives it.
- The corrected packet passed all 73 controls with zero skips on both shell
  editions in 48.80 seconds, 64.75 seconds including canonical preparation and
  cleanup. Read-only inspection of the installed Core wrapper proved that
  the original flags argument reaches the existing `LoadLibraryExW` thunk
  unchanged; the actual call-through tests retained `0x800`, its exact integer
  type and balanced owned references before success. This is installed-wrapper
  argument-flow evidence, not global OS interception or universal version
  support. Every owned test handle, provider state, module reference, shell
  host and Job process settled.
- The final hardened, network-disconnected portable packet passed two source
  controls with 71 explicit native skips. Independent review and merge remain
  gates; complete interpreter/source admission, compiled-caller replacement
  and production rollout remain open.
- #9204 merged as `5cf50e5f0e78f2efb1a6142e3bfefb8a82e438f1` after
  current-head Copilot review `5661678432` reported no open or inline issues
  and required CI passed. The owner-authored clean-comment policy applies;
  the native concerns were assessed against the actual dual-shell packet and
  direct loader argument-flow evidence, not portable source inspection alone.
  Both entire owning effort READMEs were re-read after merge; all aggregate
  implementation and validation gates remain open.
  Next qualify Windows PowerShell final paths and both editions' drive-target
  queries, then compose canonical-path/ancestor/source custody before any
  compiled-caller switch. The source worktree is reconciled; no runtime
  rollout or complete admission is inferred.

### 2026-10-26 - Core held-file and managed final-path adapter

- Extended the unused file guard with a shape-checked CoreLib 10 adapter:
  the existing managed `CreateFile` wrapper, the same `SafeFileHandle` passed
  into file-information queries, and the qualified 52-byte identity layout.
  Windows PowerShell retains its existing stock-framework adapter.
- Added a Core-only fixed-buffer final-path operation through the existing
  managed array-pin wrapper, with retained-handle lifetime and explicit native,
  length and termination failures. The preliminary callability witness used
  the same handle for a small and full buffer and closed it before host exit;
  that witness was not file-guard or complete interpreter admission.
- Extended the bounded native packet to both shell editions, with additional
  Core final-path file/directory, same-handle and failure/release controls.
  The combined native-host packet passed 58 tests with zero skips, including
  the two portable source contracts, on Windows PowerShell 5.1 and Core 7.6.9.
  It retained the existing held-file signer and explicit Core trust refusal.
  Test time was 35.52 seconds, 52.25 seconds including canonical preparation
  and cleanup. The isolated Linux source contract passed two portable tests
  with 56 explicit native skips. All owned test handles, shell hosts and Job
  processes settled; the source fixture remains retained for verification.
- Core signature verification still refuses before native binding or library
  load. Complete path/drive/ancestor/source admission and the compiled-caller
  migration remain open; no production caller, synchronizer or installation
  path enables these primitives. No aggregate Plan or Validation item closes.
- #9197 merged as `fbb1b5dc4974e82f8bf4379aadd1ef7f506f2064` after
  exact-head Copilot approval `5661422757`, no open review findings and
  successful required CI. Both entire owning effort READMEs were re-read
  after merge; all remaining Plan and Validation Plan obligations stay open.
  The managed source worktree is reconciled with `dev`; this is neither
  release attribution nor an installed update.
  Next qualify the existing Core by-reference WinTrust record marshalling,
  provider layouts and owned native-library lifetime before implementing
  Core signer parity. The current explicit Core trust refusal remains until
  that separate native qualification passes.

### 2026-10-26 - Stock-framework held-file signer qualification
- Added an unused, repository-only WinTrust adapter through the existing
  qualified Windows PowerShell bindings. It retains its own System32 library
  reference, passes the caller's held file handle, preserves no-UI/cache-only
  verification and extracts the actual verified leaf before closing provider
  state. The original selector and distributed bootstrap callers remain
  unchanged; the adapter does not enforce interpreter/source admission.
- Bounded native qualification is in progress. The hardened portable packet
  passed its source contract with 12 explicit native skips. Signed, unsigned,
  modified and mismatched path/handle controls, repeated closure and failure
  cleanup are separate native obligations, not inferred from metadata or
  portable success. Independent review remains pending.
- This is a prerequisite slice of
  `plugin-services/compiler-free-first-party-execution`, consistent with the
  existing independent-installation, explicit-error and platform-parity
  contracts. Complete canonical paths, ancestors, drive mappings,
  interpreter/helper closure, Core parity and bounded shell handoff remain
  open before any compiled caller switches. No production rollout is enabled.
- The final native packet passed all 13 controls without skips (12 native and
  one portable), including consumer-visible publication only after cleanup
  and zero success objects on provider/close failures. The final hardened
  portable packet passed its control with 12 explicit native skips.
  The first candidate was rejected by the stock parser before native calls
  because its return was inside `finally`; the corrected return follows
  completed cleanup. The signed, unsigned, modified and mismatched path/handle
  controls retained their original expectations. Independent review and merge
  remain gates; neither complete admission nor Core parity is inferred.
- #9187 merged as `f2580c7aa4999257047d3f7806b3f50b6aa66efd` after
  exact-head Copilot review `5661216451` reported no open issues and all
  required checks passed. The owner-authored clean-comment verdict satisfies
  the repository's review policy; actual bounded Windows qualification
  supplies execution evidence the reviewer environment could not run.
  Both owning Plan/Validation Plans were re-read after merge and remain open.
  Existing Core metadata now identifies a managed final-path wrapper with
  safe-handle/character-array inputs and the stock by-reference WinTrust
  interface. Next qualify their actual callability and lifetime before
  enabling a Core adapter or changing any compiled caller. Metadata is not
  parity or interpreter admission; no installed runtime changed.

### 2026-10-26 - Stock-framework file custody qualification
- Added an unused, repository-only Windows PowerShell file-guard primitive
  through exact existing framework bindings, without embedded compilation or
  runtime interop emission. The original selector and distributed bootstrap
  callers remain unchanged.
- The initial native packet passed 18 controls and failed directory custody.
  An attribute-only directory handle retained identity but allowed rename;
  treating its sharing flags as immutability was incorrect. Directory guards
  now request directory-list access too, with an explicit differential control
  retaining the weaker metadata-only behavior. No custody expectation was
  weakened to make the earlier failure pass.
- The corrected native Windows packet passed all 20 controls with no skips,
  including directory/file exclusion, release and failure cleanup, final
  junction refusal and the weaker metadata-only differential. Wrong-kind and
  junction refusals inspect the exact captured handle's closure before the
  PowerShell process exits, not after OS exit cleanup. The hardened,
  network-disconnected Linux packet passed its portable control with 19
  explicit native skips. Independent publication review remains pending.
  This does not close interpreter/source-closure admission,
  canonical path and drive checks, signer verification, shell framing,
  PowerShell Core parity, compiled-caller replacement or native desktop gates.
  Phase 7 and the parent campaign remain Active.
- #9181 merged as `8f6553d7733aa161fa5b5209543946c9504f126e` after
  independent approval of exact head `e1b62491e6d7a5d7eeeb47925871f356e9e8a9f0`,
  resolution of the same-process cleanup coverage gap and required green CI.
  The final native packet passed all 20 controls; the portable packet passed
  its control with 19 explicit native skips. Both owning effort READMEs were
  re-read after merge; no aggregate Plan or Validation Plan item closes.
  Existing stock trust and PowerShell Core bindings have metadata evidence
  only, not callable or admission qualification. Continue the existing Python
  prerequisite handoff; no compiled caller switched or installed runtime changed.

### 2026-10-26 - Windows original-parent lifetime building block
- The existing path resolver and signed-executable selector do not provide
  complete prerequisite-to-helper admission. No interpreter installer or
  compiled-bootstrap replacement was inferred from their presence.
- Added a private shared Python startup primitive that transfers a strict
  Job's continuing handle into the original, birth-pinned parent before
  application work. Kernel parent loss, rather than a polling watchdog, owns
  the sacrificial helper and its descendants. Existing atomic target-Job
  execution remains the descendant-settlement owner.
- The hardened, network-disconnected caller-owned regression packet passed
  177 tests with 20 explicit platform skips; the final focused portable packet
  passed 40 controls. Native Windows passed all 45 focused controls, including
  both cycles of parent exit and explicit Job closure with helper, target and
  grandchild pinned and confirmed gone before observer cleanup. The initial
  native failure was an observer wire-string/raw-FILETIME mismatch, corrected
  without weakening identity checks. Independent review and merge remain
  gates. Complete source archives were regenerated normally; no existing
  bootstrap caller switches.
- Phase 7 and both owning efforts remain open. Next complete review of this
  native transfer building block, then the existing machine-owned interpreter
  handoff and bounded shell framing before removing compiled implementations.
- #9170 merged as `cbbaffe972b7de4cf0119cf08591b5fd69515b29` after
  independent approval of exact head `8f2d800c6fb7b65f1fa50e8189307f497d1f11ec`,
  no review findings and required green CI. The final hardened owned-process
  packet passed 182 tests with 20 explicit skips. Both effort READMEs were
  re-read after merge; no aggregate Plan or Validation Plan item closes from
  this unused primitive alone. Continue the machine-owned interpreter admission
  and shell handoff, preserving all native desktop and consumer gates.

### 2026-10-26 - Linux compiler-free slice merged
- #9129 merged to `dev` as `1ad9ecc02cb99a1ceed9e03462033f6e2eddeb1d`
  after current-head independent review, resolved findings and required green
  CI. [The shared owner journal](../leased-daemon-instances/journal/2026/10.26%20compiler-free-amendment.md#linux-implementation-merged)
  records the exact implementation and qualification boundary.
- Phase 7 remains open. Next: qualify genuinely pre-Python Windows interpreter
  admission/acquisition, then replace the existing process, context, lock and
  update-driver helpers without a compiler or emitted interop types. No
  installed runtime or automatic-maintenance enrollment was changed.

### 2026-10-11 - Audit and operator direction
- Preserved the dependency audit as motivation and captured the operator's
  explicit Textual, index-split and installer de-vendoring priorities.
- Claimed #6234 after searching existing work; retained #5768, #5873, #4876 and
  #6143 as neighboring owners rather than creating duplicate implementations.
- Created the proposal and validation gate. Private deployment-specific
  motivation and the full operator request are recorded only downstream.

### 2026-10-12 - Proposal review clarification
- Corrected vision navigation and reconciled helper reuse with standalone
  runtime ownership: each consuming installer must secure a durable compatible
  dependency and its reference lifetime, not borrow a sibling's private venv.
- Reuse remains an unimplemented investigation; reality-pattern changes will
  accompany a separately reviewed design before any runtime migration.

### 2026-10-12 - Reviewed proposal and first implementation slice
- #6235 merged after approval and green CI; the worktree advanced onto the
  reviewed `dev` proposal before implementation.
- Began Phase 2: remove unused Textual and declare `packaging` directly to
  preserve monotonic version reconciliation, with an isolated no-UI import
  regression and dependency-contract check.
- Refreshed the execution census: 40 canonical pyprojects, 36 external base
  declarations, 16 optional declarations and 25 external names. The historic
  audit remains tied to its original snapshot rather than silently rewritten.
- Phase 2 targeted validation: 144 tests passed, including actual isolated CLI
  and Manager-process handoff with UI imports rejected; install contract reports
  zero violations and touched Python passes focused lint.
- The fresh managed test environment resolved without Textual/Rich; Pygments is
  present only through the test runner's pytest dependency. Docker clean-room
  execution is unavailable because no daemon is listening on this host.
  Native Windows evidence is local; Linux runs in PR CI. No macOS lane or live
  runtime rollout is claimed for this declaration-only slice.

### 2026-10-12 - Textual slice merged
- #6293 merged to `dev` as `cd7f0f939` after a current-head approving review
  and successful CI run `38250963792`, including Linux guard execution and the
  Windows launch lane. Version comparison and the actual Manager process boundary
  remain intact without Textual. Release promotion is tracked separately; a
  `dev` merge is not a production rollout or proof of publication to `main`.
- Review added normalized-distribution guards for equivalent UI package spellings.
  The final eight focused tests pass. Dependency comments reflect required
  `packaging` while retaining existing defensive damaged-runtime behavior.
- The docstring correction briefly exceeded the shrink-only module ceiling;
  it was condensed rather than widening the baseline or bypassing CI.

### 2026-10-12 - Index dependency boundary implementation
- Implemented the native base-only primary CLI and required healthy host sibling
  without duplicating complete service-cell or explicit-source native runtimes.
  Hosted query/index/worker commands preserve original arguments and exit codes;
  missing host dependencies are explicit errors. No production activation occurred.
- Full contained Windows suite passed: 789 tests, 97 platform/explicit-tier skips.
  Subsequent headless-command and lexical-path normalization checks passed:
  43 tests, one POSIX-only symlink skip. Both installer parsers, focused Python
  lint, headless-launch and installation-contract guards pass.
- Real fresh base installation contains no store/server/model packages and
  refuses hosted startup honestly. The actual PowerShell sibling provisioner
  installed a complete, origin-checked host environment beside it without
  starting a daemon, registering supervision or publishing an active version.
- RECORD-accounted external logical files measured 2.46 MiB for the unseeded
  development client profile and 508.85 MiB for the signed native host sibling.
  The latter includes pip/setuptools; versions and seeding differ, so these are
  profile evidence, not physical reclaim or a universal before/after estimate.
  LanceDB 0.39.0 contributes 314.31 MiB, chiefly its 312.02 MiB native extension.
- Docker clean-room and local Linux/macOS execution remain unavailable.
  Available Linux validation belongs to PR CI; existing live canary/warm-engine
  rollout remains with the standalone-service owner and was not performed.
- The first Linux CI run exposed eight stale POSIX test-harness expectations:
  a missing dropin fixture, an optional-server success assertion and an overly
  broad procutil extraction. Corrected those harnesses to exercise the required
  host package failure before activation; Linux execution of the corrections
  remains a gate rather than being inferred from Windows skips.

### 2026-10-14 - Merge reconciliation and next slice
- #6317 merged to `dev` as `a0869fbae` on 2026-10-13 after the POSIX fixture
  corrections. Its final Linux index suite, Windows checks and required PR gate
  all passed. The PR description now records the graceful-cutover assessment.
- Marked the delivered client/host boundary and duplicate-install removal items
  complete. The broader native/state-preservation validation remains open:
  passing contained checks are not authorized live rollout or macOS evidence.
- The Textual slice includes the dependency-minimal regression, docs and patch
  changefile in merged #6293. Its remaining "ship" checkbox awaits release
  attribution rather than treating a `dev` merge as a release.
- Prepared the bounded two-consumer helper experiment for review. It does not
  introduce a shared runtime or close the compatibility/lifecycle design gate.
  Next implementation slice: isolate legacy stdio MCP dependencies, retaining
  the existing CLI bridge and explicit opt-in legacy entry point.

### 2026-10-14 - Helper evidence merged
- #6643 merged to `dev` as `9015d09d8` after approval and green CI.
  Review corrected assertion-elision under optimized Python; four process
  contract tests now pass under `-O`. This closes publication of the bounded
  probe, not installation deduplication, auth isolation or shared lifecycle work.
- The full Phase 5 design gate remains open. Proceeding with the already
  planned optional dependency reduction before choosing a reuse mechanism.

### 2026-10-14 - Explicit legacy stdio MCP profile
- Moved the MCP SDK requirement out of `[store]` into `[stdio-mcp]`; native
  `[store,server]` installers do not request it. The normal agent read CLI is
  unchanged. The legacy adapter is HTTP-client-only and runs in the explicitly
  opted-in invoking runtime, not a host sibling that no longer installs its SDK.
- Full contained Windows validation passed: 857 tests, 180 explicit/platform
  skips. The suite includes a real `agent-index mcp` initialize/tools-list
  handshake, plus role-parametrized no-host-dispatch and manifest checks.
- A fresh 12-package base profile has no MCP SDK, NumPy or FastAPI and refuses
  `mcp` with exit 2 and actionable opt-in guidance. Adding only `[stdio-mcp]`
  exposes all five real SDK tools and completes the real stdio handshake without
  NumPy or FastAPI. The SDK has its own transitive HTTP dependencies; the extra
  is not described as dependency-free.
- Focused Python lint and the 13-plugin installer contract pass. Linux behavior
  remains subject to PR CI; no local macOS lane or Docker daemon is available.
  No managed runtime slot, service, model or production endpoint was changed.

### 2026-10-14 - Optional MCP slice merged
- #6658 merged to `dev` as `0976e89aa` after a current-head approval and green
  Linux/Windows checks. Review also removed MCP from both launcher provisioning
  allowlists, aligned the solo clean-room witness with honest SDK absence, and
  declared the Python 3.10 test backport.
- Linux CI exposed premature EOF in a pipelined protocol fixture. The final
  test uses the actual SDK client to await initialize and list-tools responses
  before closing its caller-owned transport. No sleep or retry hides the race.
- This completes the explicit optional-MCP implementation item. Release/rollout
  and broader native-state validation remain separate outstanding obligations.

### 2026-10-14 - Bounded statistics implementation
- Confirmed Textual removal in released `main` snapshot `f327cf447` (#6642):
  required `packaging`, absent Textual and the normalized UI dependency guard
  are all present. Phase 2 publication is complete; this does not prove local
  installation or authorize runtime rollout.
- Replaced full-table pandas materialization with a source/language-only Arrow
  scan requesting 1,024-row batches. Histograms omit nulls, retain empty strings
  and preserve the existing empty-store shape. Count and mid-scan failures now
  propagate rather than manufacturing zero or partial success.
- A real LanceDB fixture with 2,502 rows proves complete counts beyond the
  default query limit and across multiple batches without `to_pandas`.
  Structural coverage pins projection and batch size; failure tests reject both
  count errors and partial scans.
- The full contained Windows suite passed: 858 tests, 180 platform/explicit-tier
  skips. Focused content/statistics checks passed 16 tests. The implementation
  stays within the existing shrink-only module ceiling; no baseline was widened.
- This changes status-query execution, not storage layout or service ownership.
  No fresh installer, production endpoint, embedding model or live deployment
  was exercised. Linux validation remains a PR CI obligation.

### 2026-10-14 - Bounded statistics merged and next dependency slice
- #6686 merged to `dev` as `cbbef0837` after current-head approval and complete
  CI, including the actual Linux index suite. Marked bounded aggregation done.
  Release promotion and any authorized deployment remain separate.
- Next: make restricted-SSH Paramiko provisioning genuinely explicit while
  preserving existing configured provider-exec capabilities and update behavior.
  Inspect current installer/config profile ownership before selecting a mechanism;
  no new mutable shared runtime or machine-global default is presumed.

### 2026-10-14 - Persistent optional restricted-SSH profiles
- Chose a persistent installation profile rather than a transient environment
  switch or a second always-on helper. This closes the reviewed
  `capability-sized-runtime-dependencies` intent without extending it.
- New Containers installations default to base. Explicit provider-exec selection
  persists across updates; an existing unprofiled selected runtime with Paramiko
  preserves that capability. Neither incidental repository configuration nor a
  shared mutable environment selects the dependency closure.
- Both native installers use one stdlib profile coordinator with canonical
  source admission, construction/retention leases, immutable completion and
  marker activation. Slots bind package version plus profile. A failed requested
  extra never silently activates base; completed slots are reused, not rewritten.
- Windows contained coverage: 774 passed, 20 skipped. Actual isolated first-touch
  installs proved base and provider-exec, the whole PowerShell installer, ordinary
  profile retention, base rollback, real CLI dispatch and plain-pip local-library
  resolution. The tests cover source conflicts, failure retention, legacy
  capability migration, bounded diagnostics and current-profile bootstrap.
- Same-interpreter fresh package-file measurements: base 18 distributions /
  23.29 MiB versus provider-exec 25 / 36.30 MiB: 7 distributions and 13.01 MiB
  logical recorded-file delta. Both include pip/setuptools. These are not full
  venv, compressed-download or hardlink-aware physical-allocation measurements.
- Linux shell execution remains assigned to PR CI. No Docker daemon or macOS
  lane is available locally; the isolated fresh-root checks exercise first touch
  without modifying any deployed installation. No production rollout, listener,
  container, lease or service activation is part of this slice.
- Publication, Linux CI and review remain pending; the Plan item is not yet
  claimed complete.

### 2026-10-14 - Profile review corrections and engine ownership
- #6735 opened for the persistent profile slice. Review identified relative-root
  interpretation, Windows PowerShell's unavailable-interpreter stderr handling
  and fallback ordering of profile-qualified versions. Added focused regressions
  and corrected all three boundaries rather than widening module ceilings.
- Canonical Python/PowerShell/POSIX recovery now compares package versions before
  profile suffixes. Synchronized every primitive/resolver consumer and supplied
  changefiles for all 13 affected runtime plugins. Focused owning-suite coverage:
  Containers 34 passed / 7 skipped, Bridge 126 / 4, Codespaces 9 / 8.
- The native staging smoke check also exposed a receipt deadline shorter than
  the cold parent startup plus its child's 20-second watchdog. The receipt wait
  now permits 30 seconds while still rejecting early child exit immediately;
  18 staging/watchdog tests passed locally, one platform-specific skip.
- Filed #6737 for complete engine CPU/accelerator dependency profiles, preserving
  #4876's exact authority-owned lock/profile decision and #6598's seasoning and
  construction ownership. The current engine's default-install/torch-only-swap
  assumption is not a safe substitute for an approved compatible closure.

### 2026-10-14 - Native staging and bounded CI follow-through
- The next review identified caller-relative roots crossing marketplace
  self-staging and a contradictory profiled development-version fixture.
  Normalize and forward installation roots before either shell changes CWD;
  a native staged-child regression preserves the root, profile, action and
  force flag without provisioning a live runtime. Both recovery parameters now
  compare development versions before testing the final release.
- Windows CI then exhausted the fleet lane's 120-second budget in the
  production-window replay test: over 1,000 independent fully synchronous
  SQLite transactions. The exact unmodified selection passed locally in
  61.47 seconds, with that test taking 10.15 seconds; hosted-runner progress
  stopped at the same boundary. Required CI now runs the identical retention
  assertions with a four-event window; the full 1,024-event parameter remains
  in unfiltered and explicit manual validation, with an appropriate full-suite
  budget. Production retention, durability and required CI timeouts are unchanged.
- The required fleet selection passed 597 tests / 6 skips in 55.11 seconds
  under its unchanged 120-second containment budget. The complete suite,
  including production-window retention and the full artifact matrix, passed
  605 / 6 in 69.78 seconds. Workflow selection contracts: 17 passed.
  Containers profile/dependency/transaction coverage: 34 / 8; Codespaces
  recovery: 8 / 8. Focused lint and the install contract pass.
- These are candidate checks, not a merge, released payload or production
  rollout. Current-head review and hosted CI remain required.

### 2026-10-14 - Immutable helper ownership discovered during measurement
- Two-consumer closure measurement exposed an installer boundary that unit
  command mocks had not exercised: uv honored development editable-source
  declarations even when all canonical library paths were explicit. Those
  helpers could resolve outside a supposedly immutable runtime slot.
- Profile construction now disables uv source remapping after explicitly
  supplying the admitted local libraries. Health rejects any Containers/helper
  module outside the selected slot. A real isolated Python regression proves
  that an editable `.pth` escape fails rather than activating a partial runtime.
- Targeted profile/dependency/transaction coverage: 36 passed / 8 skipped;
  module ceilings, focused lint and the install contract pass. Both profiles
  were also freshly provisioned with the registered, signed Windows Python
  3.11.9 interpreter; both copied runtime interpreters retain valid signatures,
  own every helper and execute the actual CLI outside the checkout.
- This supersedes the earlier development-source distribution-record census:
  the corrected self-contained seeded base has 11 distributions / 25,165,401
  logical RECORD bytes; provider-exec has 18 / 38,810,561. The difference remains
  7 distributions / 13,645,160 bytes (13.01 MiB). The full runtime trees allocate
  29,044,088 and 45,901,048 NTFS file-stream bytes respectively on this test host;
  filesystem metadata, shared interpreter and retained production slots are
  excluded. No live rollout is claimed.

### 2026-10-14 - Required Windows reconciliation collection bound
- The approved profile head could not merge because the actual required CI run
  timed out in the reconciliation wrapper, despite metadata checks reporting
  success. The merge guard correctly refused the missing required PR gate.
- Reproduced the exact selection locally: all 18 checks pass, but collecting
  10,175 items for those checks takes the suite to 145.09 seconds. Each native
  policy/pointer case still performs both real helper calls and whole-profile
  byte/mtime snapshots; this is aggregate collection/cold-start cost, not a
  diagnosed helper deadlock.
- Added explicit owned test-file selection to the contained plugin runner.
  The required lane now collects the two owning files, preserving all 18 checks
  and the existing 120-second budget rather than skipping policy cases or
  widening the gate. The actual focused native run passed in 102.84 seconds.
- Runner selection/admission and all directly dependent CI scope contracts:
  122 passed / one unavailable-symlink skip. Focused lint and source module
  ceilings pass. Fresh hosted CI and review remain required before merge.

### 2026-10-14 - Persistent profile slice merged; next measured reduction
- #6735 merged to `dev` as `b54361f6f` after an exact-head approving review and
  complete successful required CI run `38637358082`, including both native
  runner lanes, standalone services and all affected plugin suites. Restricted
  SSH provisioning is now explicitly opt-in with durable compatibility.
- Retained the continuing effort worktree and pulled forward to the merged
  contribution branch. No production update, listener, model or corpus mutation
  occurred. Promotion and an owner-authorized rollout remain separate.
- Shared helper measurements were supplied to the existing artifact owner
  (#4876 / #6697); standalone activation, schema/queue rollback, Darwin and warm
  engine acceptance remain with #5768. No parallel artifact or service owner
  was introduced.
- Next implement the measured Windows Containers reduction: when uv performs
  installation, create signed stdlib venv copies without pip/setuptools seeding.
  Ordinary-pip fallback still receives its required seed packages. The isolated
  signed-interpreter proof already preserves the real base CLI with nine
  runtime distributions and 3,225,176 allocated file-stream bytes, versus
  29,044,088 for the seeded profile.

### 2026-10-14 - Conditional unseeded Windows construction
- Implemented the narrow Containers continuation: signed Windows stdlib copies
  receive `--without-pip` only when uv will install the admitted packages.
  The ordinary-pip constructor remains seeded; no package is removed from a
  completed runtime and no shared mutable environment is introduced.
- Full contained Windows coverage passed 781 tests / 21 skips. Native fresh
  base and provider-exec construction passed with 9 / 16 distributions, without
  pip or setuptools; all copied `python.exe` and `pythonw.exe` signatures remain
  valid. Both actual CLIs, retained provider intent and immutable base rollback
  pass outside the checkout.
- The candidate base records 2,265,172 logical package bytes / 3,248,664 allocated
  runtime file-stream bytes; provider-exec records 15,903,843 / 20,141,176.
  The real no-uv fallback also constructs and runs successfully with its 11
  seeded distributions and functioning `python -m pip`. These are isolated
  source-profile observations, not production storage reclaim or rollout.
- Focused lint, install contract and module ceilings pass. Linux/native
  platform coverage, publication and review remain assigned to the next gate.

### 2026-10-14 - Unseeded slice merged and helper decision prepared
- #6766 merged to `dev` as `5d08c74d7` after Copilot's exact-head approving
  review and complete successful required CI run `38641865532`. Native Windows
  and Linux runner checks, Containers and the owning harness suite passed.
  The first review wait expired without a verdict; an explicit re-request
  produced the approval. No gate was bypassed.
- Resolved the remaining syntax/seed decision: retain the five existing host
  syntax-grammar defaults (5,390,318 logical / 5,639,720 allocated file-stream
  bytes, about one percent of the measured host closure). Another profile is
  not justified by that cost; no parsing-quality claim is made. Windows
  unseeded construction is now conditional on uv, preserving signed copies,
  ordinary-pip fallback and completed immutable slots.
- Prepared the [helper closure evidence](helper-closure-evidence.md), its
  reproducible probes and the compatibility/lifecycle decision table for
  review. Preserve independent venvs/processes; prefer immutable artifact/file
  reuse under #4876 rather than a new helper-runtime owner or ZDD broker.
  This does not approve a production fast path or transfer an unresolved item.
- The campaign remains Active. Next secure review of this evidence/design,
  reconcile the remaining engine/standalone-service acceptance with their
  existing owners and publish the final profile/validation matrix. No
  production runtime update or warm-engine/corpus mutation occurred.
- Consolidated the [profile/validation matrix](profile-results.md) for that
  same evidence review. Measurement and retirement guards remain effective
  under optimized Python; all eleven retained-probe controls pass with `-O`.
  The artifact owner has been asked to acknowledge the existing acceptance
  boundary and name the exact approved profile contract needed by #6737.
  Pending replies and native/service gates remain open, not completed by
  relabeling them as another owner's work.
- Evidence review identified that in-runtime metadata alone could conceal a
  `.pth` helper import outside the measured environment. Both measurement
  lanes now check required first-party module origins and refuse escaping
  RECORD entries. Real disposable-venv regressions cover both failures;
  the original 9/30 source environments and surviving 30-package artifact
  environment pass the stronger checks. The retired first artifact consumer
  was not reconstructed merely to claim a new installation measurement.

### 2026-10-14 - Helper decision and profile matrix merged
- #6767 merged to `dev` as `9c5ce3bd4` after exact-head Copilot approval and
  successful complete required CI run `38648816562`. The import-origin finding
  is fixed across both measurement lanes and the survivor check; its thread
  is resolved. The fresh review, not the author's own reply review, supplied
  approval. Required CI completed normally before the authorized self-merge.
- Re-read this effort after merge and closed the delivered investigation,
  design-boundary review, measured-profile publication and corresponding
  changed-code/process/measurement records. The artifact implementation,
  engine profile, native/service, platform and release/rollout gates remain
  open. No unacknowledged owner transfer was used to clear them.
- The accepted choice retains separate owned venvs/processes and prefers
  immutable helper artifacts under #4876. Reject the measured routing-read
  broker; do not create a reusable helper runtime without an actual matching
  closure and a measured end-to-end benefit.
- Confirmed the remaining dependency in the artifact owner's canonical Plan:
  exact authority-approved profile design/publication and consumer admission
  are still unchecked. Requested its selected-contract/acceptance response
  before applying CPU/accelerator profiles under #6737; no response is
  presumed. The campaign remains Active at that explicit external gate.

### 2026-10-15 - Driving the authority-profile prerequisite
- Resumed upstream work after explicit direction to drive blockers. The source
  worktree advanced to current `dev`; #6697 is now merged, while its new-trusted-
  workflow publication proof is blocked by the existing #6551 PID-admission
  repair. Preserve that original repair rather than creating a competing PR.
- Reserved a proposal-only contribution to #4876's unchecked exact-profile
  contract and inspected its schema-3 manifest/build-toolchain prior art.
  Prepared the linked authority/selection/closure/fallback and engine-consumer
  contract for review. It names concrete acceptance and composes the still-open
  #6598 construction and #6707 admission owners.
- Native context management preserves this session's responsibility. No manual
  handoff, profile implementation, engine/model download or rollout is part of
  this proposal.

### 2026-10-16 - Reviewed exact-profile prerequisite and bounded implementation
- #6795 merged to `dev` as `40084a99c` after current-head Copilot approval
  `5527977396` and successful required CI `38680105914`. The review corrected
  the blocked-artifact case: authority-selected source fallback may use a
  distinct admissible closure, never bypass a blocked dependency or an
  integrity/provenance failure.
- Re-read both owning efforts and advanced this retained worktree through
  managed post-merge reconciliation. #6598 has since merged; #6707 and #6551
  retain their open scopes. #6941's candidate-input contribution is separate
  and is neither modified nor superseded here.
- Implemented the reviewed inert record/selection slice under #4876. Full wheel
  graph, extras, markers, targets and whole-record approval references are
  validated without downloading or installing dependencies. Actual authority
  adapters, inspected-wheel admission and native engine application remain
  open. No production rollout, model or corpus mutation occurred.
- The existing wired installation-contract suite passed 171 cases with 14
  unavailable native-POSIX skips on Windows. The final 53 exact-profile cases include
  an actual fresh Python process using the API without creating a runtime.
  The focused cases also run under optimized Python with explicit production
  refusal checks, not removable production assertions.
  This is not a released lock, fresh installer or acquisition/admission proof.
  Hosted current-head checks and normal review remain required.
- Review corrected optional Requires-Python handling: an absent wheel header
  has an empty specifier, not an invented minimum. Empty values now remain
  representable while malformed or untrimmed metadata still refuses.

### 2026-10-16 - Inert exact-profile record merged
- #7044 merged to `dev` as `8f144844d` after current-head approval
  `5554395793`, successful CI `38974962162` and all 45 actual check contexts
  completed. Linux/Windows service and native Windows/macOS construction
  contexts passed; these regression checks do not prove a profile integration
  that this inert slice does not enable.
- The post-push review wait timed out after the full 600-second window.
  Explicit re-request produced the approving review without a dummy commit or
  merge bypass. The optional Requires-Python finding is fixed and resolved.
- Re-read the canonical completion gates and reconciled the retained worktree.
  Closed only the inert record/selection item. Authority-source adapters,
  real permitted exact CPU/accelerator closures, inspected-byte admission,
  engine constructors and this campaign's remaining native/service acceptance
  are still open; the effort remains Active. No production rollout occurred.

### 2026-10-16 - Complete baseline rather than per-user approval setup
- The operator approved a complete harness-owned default on the assumption
  that most users never edit runtime configuration, then directed updating and
  driving the vision and effort. Captured the literal direction above and the
  approved decision separately; no new rollout or package-feed permission is
  inferred.
- Amended Runtime Assembly and engine intent plus the linked authority,
  resolution, integration and fresh-adopter validation plan. Keep one canonical
  campaign and the existing construction/admission/input-generator owners.
  The inert API's caller-provided approval map is not an implemented default;
  ordinary users must not have to construct that map themselves.

### 2026-10-17 - Zero-configuration plan reviewed and merged
- #7066 merged to `dev` as `1d18d3a05` after reduced current-head Copilot review and
  successful CI `38986244874`; all 42 actual check contexts completed without
  failures. The earlier approving review found a conflicting automatic-GPU
  definition; that definition now consistently follows authority capability
  policy. The final automated review could not run its full analysis because a
  runner was unavailable before its timeout. Its reduced review completed with
  only optional wording suggestions, considered and explained on their resolved
  threads; there were no unresolved blocking findings.
- Re-read the canonical effort and default sub-plan after merge. Closed only
  the ordered review gate and transitioned this retained worktree from Phase 5
  (helper investigation) back to the still-open Phase 4 engine-profile work.
  This does not reopen completed helper investigation. The parent campaign
  remains Active; no implementation, platform
  acceptance, accepted owner transfer or production rollout is inferred.
- Continued the authority integration trace: the existing shared
  `installer_dependency_policy.py` owns configured UV resolution, stronger
  seasoning and private retained input/configuration comparison, but has no
  exact-profile approval-source adapter. The record API remains repository-only.
  Existing promotion validators bind first-party source/manifest/wheel content;
  they do not by themselves authorize a consumer runtime closure.
- Keep #6941's contributor-owned candidate inputs and #6707's constructor/
  admission work intact. Establish a disjoint authenticated-policy and
  exact-closure integration contract with those existing owners before native
  application under #6737. Do not turn the missing contract into a self-approved
  JSON loader, replace the original drafts, or make source mode wait for
  artifact publication.
- The operator selected "Keep coordinating with the existing owner; leave its
  admission implementation untouched" rather than taking over #6707. Requested
  the explicit adapter handoff on the original PR. Its published source
  codename did not resolve through the available managed-worktree lookup;
  that is not evidence of abandonment or permission to create a replacement.
  Native integration remains blocked on an accepted owner contract, with
  responsibility retained here and no completed transfer claimed.

### 2026-10-23 - Consume merged admission and verify actual profile wheel bytes
- #6707 merged as `7519c4113c84c00b7831d3fc146dab455013097f` on October 20.
  Consumed its shared constructor through managed post-merge reconciliation;
  the earlier open-draft coordination wait no longer applies. Its retained
  source transaction, construction/health ownership and source identity remain
  the implementation to compose, not a replacement target.
- #7092 merged as `1028136fa616c3d81401b4e07ff1f89a60c90310` after a clean
  reduced current-head review and successful CI `39001643452`, with all 45
  actual contexts complete. Re-read the canonical Plan and default sub-plan;
  only intent/review accounting closed. The earlier native timeout remains
  tracked separately in #7020, not resolved by a different passing invocation.
- Advanced the actual-byte prerequisite of the reviewed exact-profile
  contract. The existing wheel validator now supplies inspected metadata and
  unpacked size to a repository-only complete-set verifier. Exact archive
  digests, actual Python/dependency/extra metadata, wheel identity/tags and
  RECORD content must agree within explicit caller budgets; no extraction or
  package execution occurs. First-party promotion keeps its generator check.
- The source-only pure unit packet includes the actual-byte refusal and
  boundary controls; it does not become a runtime integration test. This
  precursor neither authenticates the profile nor proves a real permitted CPU
  closure, target device, feed admission or reusable receipt. Those and native
  application remain open. #6941 retains candidate-input ownership.
- The operator renewed direction to continue driving this campaign as owner.
  Keep the next authority/actual governed closure and native integration gates
  here; no takeover, accepted transfer, model/corpus mutation or rollout is
  inferred from the admission merge or byte verification.
- Actual Windows packet validation exposed native operations in two existing
  unit wrappers: FIFO creation skipped without `os.mkfifo`, and preservation
  file access reached the native loader. Retained the original assertions with
  synthetic FIFO metadata and ordinary synthetic file read/replace adapters.
  Production adapters and original native library tests remain unchanged.
  Keep the observer, zero-skip rule and whole-packet deadline intact (#1303).
- Pre-publication code validation passed the complete admitted packet: 1,175 cases,
  no skips or actor effects, 234.094 seconds including coverage recording
  within the unchanged 300-second bound. The focused verifier/source-metadata
  regression selection passed 135 cases; the earlier normal and optimized
  Python selections each passed 121. Actual retained file handles exercise the
  helper's consumer contract, not just in-memory asserted records.
- Module ceilings and changed-code lint pass. The static source-metadata
  validator moved unchanged into its existing source-admission component with
  a public re-export; its function AST is identical. Broader mixed native
  artifact cohorts exceeded their bounded invocation windows; their full
  completion is not claimed. Initial Windows fixture-path failures were
  diagnosed and avoided with a shorter private fixture root, without changing
  Git configuration or filesystem policy.
- Clean-room provisioning and live engine validation are not applicable to
  this repository-only, non-enabled helper slice. Real governed acquisition,
  first-use construction, approved CPU/accelerator closure and external native
  acceptance remain explicit prerequisites of the later consumer integration.
- Hosted Linux CI exposed an incidental native-loader import through the
  builder's filename parser. Moved that pure grammar into the existing static
  source-admission component and retained the builder's public delegation/error
  type. Byte verification now has a regression that forbids builder/feed
  imports; no effect-observer exception or preloading workaround is used.
- Review also corrected runtime wheel compatibility for dotted distribution
  aliases and SHA-384/SHA-512 RECORD entries. Archive/source identities remain
  SHA-256, and first-party promotion keeps its existing generator, exact
  version spelling and SHA-256 RECORD requirements. These corrections do not
  add a feed, acquisition authority or construction mode.
- Post-review controls passed 154 cases under both normal and optimized Python,
  including the old builder parser API. All 187 changed byte-verifier and
  portable-wrapper nodes also passed under the unchanged effect observer with
  coverage in 49.734 seconds. The latest whole-Windows packet reached its
  unchanged 300-second bound and is not counted as a completed packet; required
  hosted full-packet validation remains independent. No deadline was widened
  or partial result promoted to a full receipt.
- Subsequent review caught a caller-side `packaging` dependency in that shared
  token parser. Restored dependency-free structural parsing and the builder's
  original numeric-version policy; PEP 440 comparison remains lazy in the
  runtime inspection path. A real fresh `-I -S` caller with `packaging`
  deliberately unavailable now exercises both the builder parser and
  first-party inspector, preserving the locked-toolchain bootstrap boundary.
- That clean-caller correction passed 156 focused cases under normal and
  optimized Python, plus all 188 changed nodes under the original effect
  observer in 71.281 seconds including coverage. The native child check stays
  outside the no-actor packet. Upstream unit-inventory conflicts were
  reconciled as this PR's exact delta, preserving other contributors' additions
  and removals and rebinding merged source hashes without changing policy.
- #8399 actually merged to `dev` as
  `b4f1a24ea4b47a26ea8b7e866af97d08dfce1648` after current-head Copilot
  approval with no open findings and successful required CI `40059533965`.
  The hosted complete packet passed 1,274 cases in 18.115 seconds including
  coverage. Re-read this parent Plan/Validation Plan and the default sub-plan:
  only the repository-only byte-comparison prerequisite is delivered.
- Managed post-merge reconciliation initially failed on the generated unit
  inventory. Retained backups, proved the delivered files and all owned nodes
  already existed on pinned upstream, and dropped only redundant merged
  history. Managed reconciliation then reported up-to-date; no unmerged work
  was discarded. The worktree remains bound to the open Phase 4 objective.
- Next extend the actual installation-authority/source-policy seam and prove
  a real permitted exact CPU closure through existing governed mechanisms.
  Preserve #6941's original input-generator contribution rather than
  duplicating it; its old cancelled CI run is not a green acceptance result.
  Default enabling, immutable constructor application and native/service
  acceptance remain open. No runtime/model/corpus mutation or rollout occurred.

### 2026-10-23 - Existing-authority default-policy input

- Traced the retained constructor and source-pin checks into the existing
  installation-context source/mode/deployment owner. Source content pins are
  not approval; the new reader resolves configuration intent at that actual
  owner rather than inventing a profile directory or an approvals map.
- `resolve_runtime_profile_policy` and its stdlib-only Python CLI action
  provide a CPU-first, artifact-preferred, governed-source-permitted baseline
  without a policy file. Optional overrides remain in the already owner-bound
  `runtime-deployment.json`; they may disable fallback or explicitly select a
  capability. Retained capability, deliberate changes and explicit unprofiled
  migration stay distinct. Existing managed-publication holds remain intact.
- The policy identity binds the resolved source/installation owner, effective
  intent and managed flag. It is an invalidation input, not profile approval,
  source-release verification, a feed/device permission or reusable admission.
  The engine constructors do not yet consume this API.
- Actual fresh-adopter and deployment regressions passed 88 combined cases,
  including real project marketplace configuration and a fresh `-I -S` CLI
  without profile/approval files. Native Python/PowerShell parsing passed
  32 targeted new/existing controls. A long-path fixture failure was rerun
  with the retained short fixture root; the same assertions then exposed and
  corrected a real PowerShell optional-output property bug.
- The existing release-metadata adapter is owned by #8367, not a competing
  implementation here. The canonical installed wheel-input work remains
  #8419, and #6941 retains candidate-input generation. Posted coordination
  on the first two PRs, including the already merged #8399 prerequisites.
  This policy-input slice does not close their gates or this parent effort.
- #8494 carries this implementation. The initial hosted head passed the complete
  1,421-unit packet in 34.177 seconds including coverage, and the opt-in native
  reader lane passed on both Linux and Windows. Required guards caught two
  newly registered SSH runtime-source copies introduced by the rebase target;
  rerunning the canonical owner synchronized those copies. This correction
  still requires current-head CI/review; the earlier passing jobs do not
  authorize merging a failed guard.
- Review found a real Bash parity defect: blank/newline profile property names
  were discarded by line-oriented key enumeration. The canonical reader now
  compares recognized fields with the declared object size, preserving a true
  empty object while refusing blank keys. Synchronized all native copies and
  added exact blank, newline-only and mixed-key parity cases. The corrected
  focused cohorts passed 90 API/pure cases and 38 actual Python/PowerShell
  reader cases. An earlier broader invocation exceeded its unchanged
  300-second limit and is not counted as a full pass.
- Required workflow validation also refused a dynamic matrix runner route.
  The optional reader lane now uses explicit hosted Linux/Windows jobs with
  the same shared steps; the trusted-CI contract passes locally. Current-head
  hosted native acceptance, full required CI and a fresh review remain gates.
- Follow-up review caught a known key plus a newline-suffixed alias that the
  count-only correction still accepted. Removed line-oriented key enumeration:
  Bash now queries only the two permitted literal properties and compares
  their presence with the complete object size. Added prefix/suffix alias
  parity controls; all 41 pure controls pass. The previous corrected head's
  complete required CI and Linux/Windows reader acceptance passed, but its
  review was not a passing verdict and is not reused as approval.
- Self-review also closed a compatibility hazard: valid explicit profile
  settings must not be parsed and then ignored by ordinary publication.
  Such settings now retain a profile-aware-consumer-required hold, including
  for a valid empty object. The no-file standalone baseline is unchanged;
  policy identity also binds the publication hold. Those API and actual
  Python/PowerShell controls passed 90 and 38 cases respectively before the
  additional alias fixtures; current-head hosted acceptance remains required.
- The next review found two actual boundary gaps. Explicit management leases
  and borrowers could ignore valid profile settings, and mode readiness with
  `reason: activation-required` incorrectly yielded a legacy-root CPU policy.
  Real-path regressions reproduced both before the fix. Management now rejects
  profile settings by presence before custody validation, including an empty
  object and an already-live ticket; managed-only publication is unchanged.
  Pending activation now returns blocked intent with no owner, selection or
  policy identity. The optional Linux/Windows lane exercises both the new
  regressions and existing custody controls. One broad local cohort reached
  its unchanged 300-second limit without a full receipt; it is not a pass.
  The focused real-path cohort then passed all 13 cases, and all 53 pure
  controls passed under the actor-free observer with source coverage in
  15.860 seconds. Sync, source hashes, lint, install/docs and trusted-CI
  contracts also pass locally.
  Current-head unit evidence, native acceptance and a fresh review remain gates.
- Review of the management correction found an additional invalidation gap:
  managed-only policy and managed policy with an empty profile object shared
  an identity, despite different management-publication permission. Pure and
  actual owner-bound regressions reproduced that collision. The policy digest
  now also binds the profile-aware-publication requirement. The preceding
  head passed all 1467 required units and hosted Linux/Windows reader/custody
  acceptance; those results do not substitute for validation and review of
  this correction.
  The corrected local slice passes 53 actor-observed units with coverage in
  22.047 seconds and all 20 actual owner-policy/isolated-CLI cases in
  30.83 seconds; source closure and fatal lint also pass.

### 2026-10-24 - Configuration input merged; continue authority composition
- #8494 merged as `d1f60ea72fbf5023496269fe98795aff2f2cad2a`.
  Genuine review approved exact head `2c3e3ca9e`; required CI `40081349938`
  passed the complete 1467-unit packet and all required guards. Hosted native
  acceptance `40081362042` passed actual Linux and stock Windows reader,
  management-custody and compatibility checks on that same head.
- Re-read the parent Plan and Validation Plan and the zero-configuration
  sub-plan after merge. Close only configuration-intent input: no authentic
  runtime closure, acquisition/admission receipt, offline reuse or native engine
  construction is claimed. All aggregate Phase 4 and corresponding validation
  items remain open; the parent worktree remains active.
- Consume the separately merged bounded inspector (#8419,
  `90663eab5b44d2d63032fa8a0ac95cf016ce8d6d`) and exact input snapshots
  (#8523, `1c386200b`) rather than duplicating those primitives. Their contracts
  explicitly leave authority, acquisition and reusable admission to the caller.
  Continue the existing adopting-policy/source-adapter and actual governed
  closure stretch, preserving #8367 release-authentication and #6941 candidate
  input ownership. No production rollout, engine activation or model/corpus
  mutation follows from these preparation components.

### 2026-10-24 - Setup invocation is the dependency-operation authority
- Clarified the existing authority boundary: a user or authorized agent
  directly invokes trusted harness setup, which installs its selected
  configurator and plugin suite. That operation authorizes their required
  dependency closure; no separate package approver, per-package consent or
  operator-authored exact-lock approval file is needed.
- Preserve the distinction between operation authorization, authenticated
  producer release metadata and admission of actual bytes. Source mode can
  resolve and freeze its own complete lock under that invocation and its
  selected configuration after source, constraints and acquisition checks.
  Invocation does not waive integrity, seasoning, revocation or source policy,
  nor authorize unrelated configuration, activation or model/corpus changes.
- The retained UV path already preserves resolver configuration, source inputs
  and stronger seasoning. Its settings projection is not an invocation grant;
  the existing build-toolchain trust allowlist is not a runtime approval
  authority. Compose the actual setup-to-component installation/custody
  boundary instead of adding an unrelated approval mechanism.
- This is an intent/contract clarification, not consumer implementation or a
  permitted CPU-closure result. Parent Plan and Validation Plan remain open.
  Next bind the invoked setup and selected configuration at the existing owner,
  then prove real source resolution and exact retained-byte admission.
- The clarification merged in #8658 as
  `6f6e3343796d6ed92bb35d31743b6ae41e0cc9eb` after genuine current-head
  approval, no open review threads and green required CI `40128173965`.
  Re-read this Plan and Validation Plan after merge; no aggregate
  implementation or acceptance item closes from a documentation clarification.
- Consume the independently merged closed-wheel constructor composition
  (#8534, `bd4c5539a`) alongside the inspector and snapshots. It binds
  inspected wheel facts to existing immutable construction and offline install,
  but deliberately leaves semantic profile, source/feed and target admission
  with the actual setup/installation owner. Do not add another constructor.
- The adopting harness's matching authority intent also cleared its own
  required CI and review and merged. No runtime mechanics changed. The parent
  Plan and Validation Plan were re-read again; the real closure, native consumer,
  helper reuse and service-acceptance gates remain open.
- Refreshed #4876 coordination after discovering the owner's subsequently merged
  release adapter (#8568) and policy/release planner (#8586). Consume those
  components, and requested confirmation of the existing source-lock/acquisition
  continuation and its engine-consumer handoff. No response or accepted transfer
  is assumed; #6941 retains its original candidate-input contributor.

### 2026-10-24 - Reconcile the accepted helper-consumer residual
- Re-read the reviewed #6767 decision, full compatibility/lifecycle boundary
  and both parent acceptance plans. The accepted immutable-helper consumer
  lane already composes with #4876's admission/consumer/retention work; it is
  not a separate shared venv or broker implementation.
- The artifact owner reported source-graph export merged in #8600 and
  explicitly retained source identity/trigger/target lock binding, acquisition
  and consumer realization. Do not duplicate that continuation or substitute
  a build-cache preparation change for the accepted consumer/lifecycle result.
- Requested explicit acceptance of the deferred helper-consumer requirements
  into #4876, or one agreed non-overlapping implementation slice, in
  [the coordination request](https://github.com/ThomasMichon/copilot-extensions/issues/4876#issuecomment-6390022767).
  The request includes whole-closure/extras parity, standalone installation,
  reference survival, matched allocation savings, installation-cell/trust
  isolation, concurrency, failure recovery and rollback. #6697 is a completed
  publication PR, not a named active delivery objective.
- The request is not acceptance. Phase 5 implementation and its validation
  items remain unchecked; source/default-profile and native/service gates
  also remain open. No runtime, acquisition, activation or rollout changed.

### 2026-10-24 - Direct source-profile construction continuation
- The operator directed direct PR delivery instead of waiting for unreliable
  agent acknowledgement. Resumed implementation in this campaign's existing
  workspace; ordinary review, source integrity and acquisition policy remain
  binding. The earlier request is not a completion or accepted transfer.
- Consumed the merged source-lock binding and implemented complete source
  profiles from its retained inputs: build required first-party/helper wheels,
  require exact pinned third-party bytes, inspect the whole metadata/extras
  closure, then use the existing offline immutable constructor. The owning
  context supplies the root, interpreter and source generation; no alternate
  slot allocator, activation or service owner is introduced.
- Bound the retained source fingerprint across compilation, build and health,
  with a final check before exposing a candidate. Construction receipts retain
  the source trigger, exact profile, selected policy and locked build tools.
  Existing build helpers now carry the caller's deadline to each child.
- Final focused source validation passed all 57 cases, including real helper
  builds and the actual constructor/isolated CLI. Earlier combined validation
  passed 329 cases but exposed two twenty-second collection startup timeouts.
  A bounded import profile showed pytest import dominating startup. The
  collection control now runs all four real selectors in one child, sharing
  imports only; all 69 CI-scope cases pass with every original inventory
  assertion and the same twenty-second cap. No timeout increase or test
  omission is accepted.
- The source consumer remains repository-only. Permitted third-party
  acquisition, production CPU/accelerator profiles, admitted persistent reuse,
  native engine/application and remaining service/platform gates stay open.
  Separate Linux/Windows/macOS source-profile qualification uses the existing
  wheel-input opt-in; hosted proof, unit-packet admission and PR review/merge
  remain publication gates. No production rollout is authorized or claimed.
- Opened direct draft #8791 after preserving all upstream unit additions
  during publication rebase (1587 existing plus eight new units). The eight
  focused units pass the unchanged no-actor observer, and actual constructor
  requalification passes with interpreter checks before construction and
  after installed health. The full local packet exceeded its fixed budget;
  no full local pass is claimed and required hosted qualification remains open.
- Initial Linux required CI exposed eager `ctypes` loading through the
  build-toolchain trust module. Deferred it to the actual native operations,
  not a framework preload or an allowed-effect exception. A clean `-I -S -B`
  caller now refuses native loading while importing the profile consumer;
  native import/provenance controls pass along with the updated CI-scope
  contracts. Hosted native lanes also include macOS without claiming
  standalone-service supervision or engine rollout parity.

### 2026-10-25 - Source closure qualification retained
- The complete source/helper closure contribution #8791 merged as
  `7d2ba9dd76f7e25166ac9e84be555c967f7eb2aa`. Its qualified head passed actual
  required CI `40170736550` with 1,788 units and all seven selected native
  Linux/Windows/macOS source-profile, compile and wheel jobs in
  `40170954816`.
- This closes that source-preparation slice, not production source
  authentication, governed acquisition, admitted persistent reuse or engine
  adoption. The aggregate Plan and Validation Plan remain open.

### 2026-10-25 - Installation latency takes priority over preparation plumbing
- The operator reported general installation timeouts and reprioritized
  historical-log analysis plus targeted performance fixes. Central rollout
  remains with its existing deployment owner; this campaign runs no updater.
- Audited twenty retained Windows update histories. Successful runs took
  roughly 28-53 minutes; runtime reconciliation and repeated package operations
  accounted for large spans. Marketplace refresh also caused failures before
  runtime construction. Preserve the distinction between observed log spans,
  buffered presentation and controlled physical-phase measurements.
- A disposable same-base signed-Python comparison completed unseeded copied
  venv construction in 2.760 seconds. Its seeded control reached 45.059 seconds
  inside `ensurepip`; copying the interpreter itself took under 0.2 seconds.
  This is not a total-update speedup measurement.
- Merged #8970 as `1f5d2f0a3686ff9e409beebed7a06392c20f49d9` after current-head
  approval, resolved review findings and green required CI `40193754354`.
  Exact qualified head `4fc3f4bf1f9d6b531c92f1bdeb0f0128559fca2b` passed all
  six selected Linux/Windows native jobs in `40193814889`.
- Known UV-owned signed-venv callers now omit unused pip seeding; the generic
  seeded default and genuine pip fallback remain. Codespaces submits its eight
  helper roots and plugin in one retained transaction, with explicit
  non-editable normal mode and preserved editable selection. Tests cover
  canonical editable mappings, cold/warm source edits, signatures, retries
  and recovery. No production deadline or admission check was weakened.
- Post-package Bridge and Dispatch spans include real warm-up, graceful
  handoff, drain and supervisor reconciliation, not only file copying.
  No healthy-lifetime or re-adoption safeguard was removed. Whole-update
  cold/warm evidence and the broader parent acceptance remain open.
- Next reduce Logger's repeated governed install transactions while retaining
  its existing setuptools/PyYAML prerequisites, then measure the authorized
  updater's actual changed and unchanged paths. Do not restart profile plumbing
  or infer an installed speedup from the merged source fix.
