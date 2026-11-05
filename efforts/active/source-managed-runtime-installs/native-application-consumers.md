# Standard environments and native application consumers

Back to the [canonical effort](README.md).

**Reviewed bounded execution plan; released-consumer acceptance remains open.**
Coordination stays on #7030 under #7028. The implementation source handle is
`weathered-diode`; existing construction, artifact/profile, application,
command, cache and historical-migration owners retain their scopes.

## Request and reconciliation

The portable operator direction is:

> Ideally we standardize on modern uv and venv compliant conventions for everything

The bounded request combines that direction with plugin delivery independent of
service installation. This slice addresses native Worktrees consumers of the
released application model, not immediate migration of every application.

It extends the Runtime Assembly vision's
[standard-environment intent](../../../visions/plugin-services/runtime-assembly/README.md#standard-python-environments)
and closes part of the existing
[managed-deployment contract](deployment-policy.md).
The [application support pattern](../../../docs/patterns/fast-application-install-and-cutover.md#common-forward-environment-contract)
owns concrete layout conventions.
[Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
remains unchanged: consumer readiness checks do not become an old-footprint
veto on explicit incoming installation, and no new installer, supervisor or
source registry is introduced.

## Evidence and scope

A fresh Linux consumer independently installed the ordinary default-head
Worktrees payload and a Git-backed application. Direct installed commands,
imports and authenticated monitor serving worked. Installation mode and the
documented managed-deployment deny policy validated, but the delivered catalog
reported unresolved binding while advertising its direct command as ready.
That exact command's `--help` exited 1 without diagnostics. The aggregate
hook/first-use/end interval also added bytecode inside the selected slot.
Native payload refresh was not reached.

The inspected native resolver uses flat-slot interpreter paths; the application
has a nested project environment. This is an observed integration mismatch,
not a unique established cause of every symptom. An unadopted fixture's lifecycle
warning is not proof of a product registration defect.

Prior art is credited, not replaced: canonical Python `versioned_runtime`
already recognizes nested application environments, Dispatch has physical
flat/nested consumer controls, and the parent effort has an external-bytecode
cache owner. Remaining native adapters must converge on these foundations.
Historical global-command recovery, other app startup/cutover work, construction
and retained-wheel/profile changes are not taken over.

## Plan

- [x] Land this intent and bounded plan through normal source review before
  changing runtime behavior.
- [x] Trace the advertised catalog entry, native shell/PowerShell adapters,
  command binding and hook execution against the actual installed generation.
  Establish normal preparation/adoption prerequisites through their owners;
  do not manufacture metadata to silence an unqualified fixture.
- [x] Reuse canonical physical-environment selection in affected consumers,
  including standard POSIX/Windows interpreter paths and real venv prefix
  behavior. Preserve completion, explicit source-generation selection,
  physical-path and foreign-scope checks; do not flatten or alias the project.
- [x] Resolve application-owned command/readiness integration through the
  existing installation and command owners. Do not synthesize a legacy
  deployment receipt or infer ownership from a name or package-version match.
  Prefer the existing operation and immutable installed descriptor; if the
  boundary needs expansion, declare it provider-first under the
  [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
  before migrating consumers.
- [x] Preserve valid managed-deployment denial and supported standalone
  behavior. A healthy selected application remains callable despite payload
  skew; unsupported or missing requirements produce actionable diagnostics,
  not silent exits, runtime reconstruction or whole-hook suppression.
- [x] Route affected hook/command imports through the existing external-cache
  owner. Preserve caching and normal non-deployment hook duties without new
  slot files, cache exemptions, site-startup hooks or deletion of prior state.
  The released cold/repeated consumer proves unchanged runtime files and
  positive creation/reuse of all 602 external cache files. Actual changed-input
  acceptance remains a separate unchecked validation item below.
- [ ] Qualify the exact native consumer, then publish source changes through
  review/CI and actual Main promotion. Record released-consumer results and
  remaining adoption scope separately; no host rollout is included.

## Validation plan

- [x] Focused controls distinguish completed physical nested environments,
  supported flat compatibility, bad/redirected environments, source-qualified
  selection and unchanged fallback meaning. Use real UV-created venvs to check
  `pyvenv.cfg`, `sys.prefix`/`sys.base_prefix`, installed imports and both
  platform interpreter layouts; no activation-policy or symlink privilege
  changes.
- [x] Exercise the actual command/binding parser boundary across supported
  old/new callers and providers, explicit rollback, missing or incompatible
  metadata, managed refusal and strict unknown-argument rejection. Do not add
  a whole native matrix to the required unit-only packet.
- [ ] A fresh disposable consumer acquires Copilot content through ordinary
  default-head or actual-local-checkout declarations and records what it
  receives, independently of backend Git inputs. Execute the exact delivered
  catalog argv and real hook/first-use entries after canonical preparation.
  A copied snapshot or plugin-directory injection is not plain native evidence.
- [ ] Compare complete runtime files, generations, selectors and redirects
  before/after the relevant consumer interval. Observe positive external
  bytecode creation/reuse; do not prewarm, disable caching or exclude new
  bytecode to manufacture slot equality. Preserve failed attempts separately.
- [ ] Count an actual frontend-update witness only when native refresh delivers
  changed payload input. An initial install or unchanged refresh is not that
  proof. Record unsupported/unavailable inputs explicitly rather than pinning
  the frontend to match its backend.
- [ ] Retain exact guest/process cleanup and honest platform/serving limits.
  Source merge, release, isolated native acceptance and installed-host rollout
  remain different outcomes. This slice does not close #7028, #7030 or every
  application in the contributor matrix.

## Journal

### 2026-11-05 - Released cold/warm consumer qualified; changed-input timing remains

The fresh exact-role witness passes both complete installations, installed
commands/imports, unavailable-source and argument-refusal preservation, and
authenticated serving under a real consumer. After release it confirms native
exit in 86.35 seconds, no active route and the original PID retained only as
previous. This directly validates the demand-aware and active-versus-previous
fixture corrections without changing production service policy.

Cold and repeated public standalone reconciliation launch and complete with
canonical version-1 `ok: true` status, no failed operations and the actual
repository path. Every compared runtime file, version selection and redirect
is unchanged through native hook/catalog/end, explicit reconciliation and an
unchanged refresh. All 602 external bytecode files are reused without rewrite.
No prewarming, bytecode suppression or backend-file exclusion is introduced.
Container native activation remains explicitly unqualified.

The aggregate remains three passes and one failure: no changed default-head
release appears within the original 480-second bound. The exact guest is
absent and all watched host state remains unchanged. Ordinary promotion #10532
lands immediately after that window and changes the payload from
`1.24.465-dev1` to `1.24.466-dev1`; it cannot retroactively qualify the failed
attempt. The following frozen candidate's Worktrees tree is identical, so
another guest is not launched for it. A finite read-only metadata observer
waits for a genuinely changed pending payload, without pinning a prior
frontend, forcing release or extending the witness limits. Changed-input and
full native-activation acceptance remain separate open work.

### 2026-11-04 - Actual Main release verified independently of the recovery run

#10523 is provider-confirmed merged at `2026-11-04T22:41:59Z`, Main
`3b7ef44ed5d98093d18e971c55f3a5fb73d197b5`, from candidate
`c0cbedefc231063dc64ec1161f6f4f64cd9a73ce`. The actual release state admits
Dev `d30a64fa4bc0e8514275fcf82df76425e41e868b`; Git ancestry contains #10505's
merge and all three reviewed standalone/guidance consumer blobs match.
The actual producing promotion run is successful `41925387683`, not the
older recovery run.

The older run's attempt 2 completes without a promotion PR and skips downstream
qualification/publication/merge. Its green status alone is not release proof.
The newer actual Main receipt and code establish the release and successful
credential-dependent publication. The full actual Main export verifies ten
critical consumer blobs and archive SHA-256
`a4bdf834c3f4789e622c17682781cfc296bcdf0116e465b8c7d75b65223375fd`.
No host rollout or full native activation is inferred.

The distinct released consumer constructs the pair and completes Manager
installation/readiness/preservation. Worktrees installation, command/import
readiness and initial authenticated serving pass; the actual installed files
match all three reviewed target blobs. The unsupported argument is explicitly
refused and selections plus completion bytes stay unchanged, but the following
canonical monitor-serving check returns false while command/import checks pass.
The result is two passes and one failure before native update. Its exact guest
is absent and watched host state remains unchanged.

The cause is unestablished, not declared flaky or uniquely attributed to a
source change. A fresh witness adds only failure-time read-only serving
diagnostics and bounded log retention, without readiness retry or weaker
assertions. Installed consumer/update qualification remains open.

That witness reproduces the failure, but its route diagnostic itself raises
`AttributeError`; the bounded monitor log is retained. The source contract
explicitly permits idle exit after three empty sweeps without a real consumer.
The fixture has no active consumer demand during preservation checks. A new
fresh witness therefore retains the installed owner's real transition-demand
subscription during those unchanged checks, releases it explicitly, and also
requires native idle exit afterward. It does not fake session adoption, suppress
idle exit, restart the service or weaken the serving assertion. Both outcomes
must be observed before treating the missing demand as the fixture cause.
Missing/empty-route diagnostic controls fail on the old helper and pass after
the guard correction; unavailable observations remain explicit. No production
or service-policy change is made.

With the real consumer present, the next witness passes initial serving,
after-refusal serving and serving without the preparation source, together
with command/import and selector/completion preservation. It still fails the
new 120-second post-release native-exit/route-removal control. That boundary
lacks sufficient final state, and canonical client release is best-effort;
a returned context is not remote unsubscription proof. The exact guest is
absent and watched host state is unchanged. Another fresh witness adds only
final-state capture before that same assertion, preserving its bound and
all positive checks. Synthetic timeout/read-error/native-exit controls verify
capture without accepting unavailable observations. No complete fixture-cause
or product-regression conclusion is claimed yet.

The final-state witness proves native process exit and loss of its birth
identity; the old listener refuses connections. Its remaining failed assertion
uses `read_active_endpoint(..., verify_listener=False)`, which deliberately
searches both active and retained previous entries. Canonical `clear_if_owner`
demotes its active endpoint to previous for in-flight fallback compatibility.
The witness has not captured raw route roles, so it cannot infer a remaining
active entry from that convenience reader alone.

A fresh correction checks the raw active role, preserves valid previous
retention, and still requires original native exit within the same bound.
Controls reproduce the old retained-fallback false failure; dead-active,
native-live and unknown-role inputs still refuse. No production routing or
service policy changes. Actual plugin input must still change before update
acceptance can pass.

### 2026-11-04 - Main promotion blocked by release credential scope

Run `41920408755` admits Dev
`be98510a8f4e98b4377de29f756111a05f89027f`, which contains the #10505 merge.
Source admission, frozen candidate, unit coverage, first-party wheel build and
artifact attestation pass. The sole failed job is `Promote dev -> main`
(`126896518220`): GitHub refuses the exact candidate
`edf092097252cbc9cc60ed11aec1df1d958365b0` push because the release PAT lacks
`workflow` scope for `.github/workflows/installation-context-full.yml`.

The existing watchdog issue #10515 owns the tracked authorization blockage;
this is not a runtime-test failure or a demonstrated flake. No promotion PR,
forced retry, credential substitution, permission expansion or bypass is
attempted. The existing release credential owner must repair the authorization
before the documented exact-candidate recovery can proceed. Source merge is
not release, and released native/update acceptance remains open.

The credential owner subsequently reports adding Workflows read/write.
Recovery is requested for only the existing failed publication job on the same
run, retaining its original candidate and qualification. That request does not
prove successful publication. The bounded disposable witness expires waiting
for actual Main before any backend build or install. Its exact guest is absent,
but two watched host selectors change under an unknown owner; host preservation
is not claimed or restored. The failed attempt remains separate from any
future fresh witness.

### 2026-11-04 - Standalone correction merged; release and update proof remain

#10505 merged to Dev at `2026-11-04T21:37:29Z`, commit
`39dcfd9404de537b78d4d49bf872cf9c8a537df4`. Genuine Copilot review
`5775823654` covered published head
`05ccf04f616333d5e32c68f77e4aa99fbeac2598`; its sole Low finding was addressed
in the PR description and the actual thread resolved. The Performance impact
section records synchronous completion/RECORD identity work, its frequency and
scaling, reuse/invalidation, a reproducible timing flow and unavailable native
latency/CPU/tail evidence. Cache reuse is not latency qualification.

The description update initially flattened the required source marker.
Attribution correctly failed, then passed after the actual assigned marker
was restored on its own line and the complete scalar body roundtrip verified.
Unit coverage, guards/lint, identifier and workflow-lockdown guards and the
required PR gate pass on the unchanged source head. The managed Maintainer
merge bypassed only the separate review-count gate, not CI or feedback.
Both #7028 and #7030 remain open.

The continuing source worktree synchronized to the actual squash merge.
Main promotion, released positive reconciliation and genuinely changed native
payload input remain the next gates. No host rollout, full native-activation
qualification or completion of the broader application objective is claimed.

### 2026-11-04 - Released worker verified; standalone reconciliation remains

#10489 merged at `2026-11-04T19:17:28Z`, promoting admitted Dev
`c3437ffe05261e7b71c2763517133b6f36f46d45` to Main
`fd3b1de9b451f6efaa3d4243207dbe694bf99ed1`. The admitted source contains
the #10481 merge, and the released Git worker and actually built wheel both
match reviewed worker blob `52fe070611f48577663b9068fecd36863d262470`.

The pre-promotion fresh witness reaches its bounded backend-availability limit
before release and performs no backend installation. A distinct post-release
guest acquires ordinary native payload `1.24.462-dev1` and the actual Main
backend independently. Canonical pair construction, both full installs and
readiness/preservation controls pass, but its first full hook/catalog/end
interval adds 175 new slot bytecode files. Existing files, generations,
selectors and redirects remain unchanged; external caching is also active
(577 files). Refresh is not reached. Both exact guests are absent and watched
host state is unchanged.

The diagnostics show standalone background reconciliation launching. Its
existing `_start_provisioning_if_needed` command starts another plain
`python -m agent_worktrees reconcile-plugins` process. That is a concrete
additional cache consumer, not yet a unique attribution of every observed file.
The prior passing candidate does not substitute for this released result.

The argv construction is moved into the existing session-provisioning component,
preserving all root override callbacks and shrinking the compatibility root.
All 12 existing controls pass with that mechanical move. The subsequent argv
regression and actual-child cache-location regression both fail before the
cache correction; all 57 standalone/guidance lifecycle controls pass after it.
Python lint, the 13-plugin install contract and whitespace pass. The normal
planner, opt-outs, deadlines, environment, process flags, status and reaping
are unchanged. No reconciliation or bytecode suppression is used to hide the
slot delta.

The first distinct candidate preserves runtime bytes and reuses 537 external
cache files, but neither native interval exercises standalone reconciliation.
Its positive-path assertion correctly fails: an unexercised path is not a pass.
The next fresh consumer uses the same committed candidate
`a3576804891ba777d5b2dea9e7cf8089087f1c4b` and explicitly invokes the existing
public `session-lifecycle --stdin --source-bound --timeout-seconds 10` command.
Both cold and repeated operations report background launch and complete with
canonical version-1 status, `ok: true`, no failed operations and the actual
repository path. All compared runtime files, selected generations and redirects
remain unchanged. The cold interval creates 602 external bytecode files; the
repeated interval reuses all 602 without rewrite. No prewarming or bytecode
suppression is used. This qualifies the standalone candidate path, not original
Copilot-process activation.

The overall witness still fails honestly: no changed ordinary default-head
payload becomes available within its original 480-second bound, so an unchanged
refresh is not counted as update proof. Both exact guests are absent and watched
host state remains unchanged. Required source review/CI, actual Main promotion,
released-consumer and changed-input acceptance remain open; no host rollout
is included.

Missing committed project authority configuration and unsupported container
native epochs remain separate preparation/platform limits. Canonical local
registration is not a fabricated committed authority declaration or an
original Copilot-process witness; neither admission boundary is weakened.

### 2026-11-04 - Backup-worker correction merged; released acceptance continues

#10481 is provider-confirmed merged to Dev at `2026-11-04T18:47:21Z`,
commit `35f7be610d297fad3ee582686178849f374319dd`. Genuine Copilot approval
`5773807726`, zero review threads and the complete hosted CI run passed on
published head `dcedae3ba59a462386591413c14f47cdb2e6706a`. The managed
Maintainer path bypassed only the separate review-count gate, not CI or feedback.
The source ROOT, this subplan and the adopter's parent gates remain open.

The bounded candidate proves complete-file immutability and positive cold/warm
cache behavior, not a released frontend update. Next is ordinary native
acquisition before promotion, independently acquiring the actual released Main
backend when available within the existing whole-run bound, then executing a
genuinely changed native refresh. No older frontend ref, fabricated receipt,
cache suppression or host rollout is introduced.

### 2026-11-04 - Released catalog works; backup guidance worker remains

#10473 promoted the exact admitted Dev source
`dc17081d864e826110b7e4444dbae669e8213410` to Main
`913dcc766be9d434efb414945dea283f060bcb8c` at `2026-11-04T17:08:51Z`.
The seven critical native adapter, lifecycle-contract and catalog-generator
blobs match the reviewed implementation.

A distinct fresh Linux consumer acquired the ordinary default-head payload
before constructing the backend pair from actual released Git inputs through
the canonical artifact/profile owners. Both complete installations, installed
commands/imports, authenticated Worktrees monitor serving, unavailable-source
and refusal-preservation controls pass. Canonical project registration and
worktree creation pass. The exact delivered catalog command now passes
`--help` and `--version`.

The full hook/first-use/end interval nevertheless adds 59 bytecode files inside
the selected Worktrees environment. Existing files, generations, selectors and
redirects are unchanged; refresh is not reached. The backup local-guidance
worker starts a fresh interpreter without external-cache arguments, so that
remaining path is reopened under the existing cache owner and reviewed scope.
The failed guest is confirmed absent and watched host state is unchanged.

The native lifecycle source independently refuses container epochs. Preserve
that supported-platform boundary: project registration is not native process
qualification, and this result does not authorize a fabricated source witness,
container admission changes or a full native-activation claim. Source
acceptance, changed-input refresh and installed-host rollout remain distinct.

The follow-up passes the selected runtime root to the existing external-cache
owner when constructing the backup worker's Python argv. It does not change
worker admission, environment scrubbing, headless/process flags, metadata
custody or native-source policy. The focused regression fails on the previous
plain `-m` invocation, then all 45 affected lifecycle controls pass after the
correction. Python lint, the 13-plugin install contract and whitespace pass.
Released-consumer cache/update acceptance remains open; the earlier failed
guest is not repaired into a pass.

The distinct committed-source candidate
`0c178b2a71407f584b68dd06531baa37f9232155` then passes actual Git-bundle
acquisition, canonical pair construction and both complete installations in a
fresh guest, with ordinary unpinned native payload `1.24.460-dev1`. Cold and
repeated exact hook/catalog/end intervals preserve all 3,546 compared backend
files and selected generations. The cold interval creates 571 external
bytecode files, and the repeated interval reuses all 571 without rewrite.
No prewarming, bytecode suppression or backend-file exclusions are used.
The changed-default-input wait is still pending at this observation; this
candidate result is not released-backend, full native-activation or host-rollout
evidence. All 40 already-admitted typing projects also pass their Linux and
Windows target checks; pending projects are not relabeled as typed.

The candidate witness subsequently ends with three passing stages and one
failed aggregate native stage: nine successful default-head observations find
no changed release within the unchanged 480-second bound. No native refresh is
performed or counted. The cache and complete-file observations above remain
valid bounded results, not a passing update witness. The exact guest is
confirmed absent and all four watched host states are unchanged.

### 2026-11-04 - Implementation merged; release and native acceptance continue

#10438 is provider-confirmed merged to Dev at `2026-11-04T16:44:33Z`,
commit `dc17081d864e826110b7e4444dbae669e8213410`. Genuine Copilot approval
`5772112561` and required hosted CI passed on reviewed head
`5b40b77e62ae71148112e16f06b2ce22444eb7e7`; all six published findings are
resolved, and the additional cache/catalog/documentation assessments are recorded.
The authorized Maintainer path bypassed only the separate review-count gate,
not CI or feedback.

The implementation and focused source-control items above are complete.
Actual Main promotion, ordinary independently sourced native acquisition,
complete backend-file/cache observations and changed-input refresh remain open.
Next is promotion tracking followed by a distinct fresh consumer, with no
frontend ref override, forged preparation metadata or host rollout. The failed
R17 fixture remains unchanged. This does not close #7028, #7030, other apps or
the broader adopter objective.

### 2026-11-04 - Bounded proposal

The native consumer failure and standard-environment direction motivate this
reviewed intent slice. Existing nested-environment and cache work is retained.
No runtime source implementation, installed adoption or rollout is claimed.

### 2026-11-04 - Reviewed intent and native trace

#10423 merged the intent and bounded plan before runtime edits. The native
Worktrees adapters still search only direct-slot interpreter paths; a selected
application instead contains a physical `.venv`. Under POSIX `set -e`, a failed
current-selector lookup can terminate the caller before its explicit readiness
diagnostic. Catalog ownership independently requires a legacy deploy-manifest
receipt and does not yet describe the application-owned installation.
Implementation must address these boundaries without manufacturing that receipt.

### 2026-11-04 - Bounded source candidate

The candidate adapts Worktrees' native resolvers and hook runtime to completed
physical project environments, retaining direct-slot compatibility and exact
source recovery. Failed POSIX selector probes now reach the existing fallback
or readiness outcome instead of exiting silently under `set -e`.

The existing lifecycle operations are declared in the selected provider's
packaged immutable hook metadata. A capable provider does not need the same
package version as its frontend. Missing application support is explicit before
lifecycle work; the running resident retains its live handshake. The plugin's
pre/post/end hooks use their own payload helpers without requiring a historical
root-wide snapshot. Direct and legacy-fallback lifecycle imports use the
existing external-cache contract.

The legacy catalog binding continues to mean command-publisher ownership;
it is not rewritten into application ownership. Backend flight status and the
exact command independently select the completed application without a forged
receipt. A native catalog failure also exposed Python 3.9 being admitted by
the Windows launcher; the generator now checks the helper's Python 3.11 floor
before admission, retaining no retry after an admitted helper fails.

Validation so far: the Windows changed-hook/policy packet passes 211 tests
(78 platform skips); a fresh Linux clean-room source packet passes 281
(16 platform skips), including actual uv-created environments and lifecycle
parsing. Seven native catalog interpreter/failure controls pass. Two Windows
native junction controls and eleven focused legacy-cache/contract/hook-routing
controls pass. Python lint, install contract, generated-content check and
whitespace pass. The disposable guest is confirmed absent and watched host
state is unchanged.

This is source-consumer qualification, not ordinary Copilot installation,
application construction, frontend refresh or released-adopter evidence.
Those actual native acceptance gates remain open through source review,
merge and release. No host rollout or other application's migration is included.

### 2026-11-04 - Review corrections

#10438's initial review found two real retained-path regressions. All four
existing hook publishers now carry the canonical resolver's complete dependency
closure, and the installed-bin fixture actually resolves a completed nested
runtime rather than merely importing the hook. The canonical selector treats
invalid UTF-8 like an unreadable selector and retains qualified recovery.
Its byte-identical copies and consumer changefiles are synchronized; this is a
two-line shared-reader correction, not another application migration.

The strict runtime-resolution guard is unchanged. The native adapters now use
the same outer physical-environment and inner interpreter-path loops as the
canonical Python selector; their refusal of a linked project environment remains
intact. Focused installed/native controls pass 16 cases, canonical source recovery
passes five, and the strict guard, install contract, projection synchronization,
Python lint and whitespace checks pass. The initial CI failure and review remain
historical evidence until the corrected head receives its own passing gates.

The complete affected hook/resolver packet subsequently passes 190 cases
(15 platform skips). All 18 native publication-rollback cases pass in three
bounded failure-family groups under the original item and group deadlines.
The earlier combined packet timed out during rollback and is not credited as
a pass; the exact failed family also passes independently on all three native
shells. An additional direct callback-copy transaction control publishes the
fourth owner's actual wrapper set and resolves its completed installed runtime.

A subsequent review found the remaining completion-file I/O edge. Read-only
resolution now treats an unreadable candidate as unavailable and can select a
healthy last-known-good runtime. Raw completion reads and installation/publication
admission still propagate storage errors; the regression controls assert that
distinction and retain exact-source fallback refusal. Nine focused selector and
recovery controls pass. A Dev rebase required only regeneration of the shared
footprint producer digest, with its revision and semantics unchanged.

The shrink-only module cap is retained: the facade delegates completed-slot
selection to its existing completion/construction component. No new runtime
dependency, public CLI or component file is introduced. After that factoring,
the nine recovery controls and 17 installed/native consumer controls pass, as do
the unchanged strict resolver guard, install contract and Python lint.

### 2026-11-04 - Required unit qualification and PATH-independent End

The required unit job refused three stale source-input hashes before tests ran.
Only those declarations were refreshed; the 4,044-node inventory, coverage
targets and 300-second budget are unchanged. A frozen fresh-Linux run passes
all 4,044 tests and records observed coverage in 221.607 seconds.

The next review found that SessionEnd still depended on PATH Python before
reaching the selected runtime. Its existing native deregistration receivers now
select the completed environment read-only and invoke the owning payload client
with an external bytecode cache. Explicit contexts never fall back to legacy
state. The Windows bootstrap uses the existing context SDK with site loading
disabled before admitting the client, without adding another validator or CLI.
Real uv-created nested and flat environments pass without PATH Python under
both Windows PowerShell and PowerShell 7; invalid contexts do not load slot site
code. The focused Windows End/routing packet passes 19 cases (six platform
skips). A fresh Linux packet then passes 13 End/routing cases (12 platform
skips). Its initial failure was an incomplete Python-free test PATH missing
the native validator's temporary-file utilities, not a credited pass; that
attempt is retained separately. Both exact guests are absent, with watched
host state unchanged.

The final Dev reconciliation preserves the newer 4,061-node required-unit
inventory and its unchanged 300-second budget. Only the same three input
hashes differ from Dev, and every declared source hash matches. The existing
owner tool regenerates the lifecycle archives against the now-current accepted
source; the footprint revision and semantics remain unchanged. The qualified
End implementation is byte-identical across that reconciliation. Corrected-head
PR review and required CI remain open.

The next review accepted PATH-independent End selection but identified Windows
locale decoding of its UTF-8 input as a remaining lifecycle edge. Both native
receivers now request Python UTF-8 mode explicitly. The real-environment fixture
sends unescaped non-ASCII input, proves a non-UTF-8 control with UTF-8 mode
disabled, and checks the actual client codec and forwarded event. All 12 Windows
cases pass under the two native PowerShell hosts (six platform skips).

The resulting review has no open findings but asks for assessment of fresh
bootstrap cache growth. The native receivers now reuse the existing hook
runtime's cache selector and installed cache owner, rather than always choosing
an unsealed namespace. As in the existing command bootstrap, only read-only
validation/cache selection suppresses bytecode writes; the actual client runs
with caching enabled in the selected external namespace. Legacy providers
retain the owner's unsealed fallback. The real-environment fixture verifies
first creation, second-invocation reuse without recompilation, and no new
unsealed directory for sealed fixtures. All 12 Windows cases pass (six platform
skips); the initial PowerShell 5 quoting failure is corrected, not credited.

The subsequent review found the complementary output-decoding edge: Windows
PowerShell must select UTF-8 before consuming either bootstrap response, not
only before forwarding the event. Encoding initialization now precedes both.
The existing real-environment fixture uses a non-ASCII installation root and
starts each Windows hook under code page 437, retaining the non-UTF-8 Python
control, exact event forwarding and warm-cache checks. All 12 Windows cases
pass under both native hosts (six platform skips); no host locale changes.

That review has no published findings but identifies another encoding concern
for assessment. A new real uv interpreter beneath a non-ASCII path reproduces
the catalog's incorrect `binding-interpreter-unavailable` result under code
page 437 on both Windows shells. The existing catalog generator now establishes
UTF-8 decoding before either interpreter path is consumed, avoiding an
unnecessary console-writer reset when UTF-8 is already active. Both regressions
and the existing admission/no-retry/read-only controls pass: 11 cases, two
platform skips. All 13 generated manifests are in sync.

This follow-up changes only Windows catalog output. The available Linux
clean-room lane cannot qualify Windows PowerShell console decoding; no new
Windows clean-room venue is available here. The real first-touch uv fixtures
and native child processes provide the Windows consumer control, while the
unchanged Linux End/cache behavior retains R26's qualified evidence.

While required Actions jobs awaited runners during the reported Actions
incident, Dev advanced and introduced a real conflict. The reviewed source tip
was backed up before native synchronization; a single owned rebase resolved
only the derived footprint digest and unit-input declarations. Upstream
post-hook lookup changes and immutable-environment admission remain intact.
The exact End receivers, catalog generator and catalog output are unchanged
through reconciliation. All declared source hashes match; the 4,061-node
inventory, coverage targets and 300-second budget remain unchanged.

Nine canonical recovery controls and ten post-hook controls pass (three
post-hook platform skips). Module ceilings, strict runtime resolution and all
13 generated manifests pass. This does not substitute for the new head's
required hosted CI, review, merge or release.

Fresh Linux R27 qualifies source `2d92adc369932a2312db7f1d56661c3b78885940`:
all 4,061 tests pass with coverage in 169.290 seconds under the unchanged
300-second budget. Its exact guest is absent and watched host state is unchanged.
The subsequent cleanup changes delivery metadata/documentation only: Dev now
already contains the canonical archives, so the obsolete WSL-only changefile
is removed. Fleet/Chat retain declarations because their existing build
resources still include the changed versioned-runtime library. Required hosted
CI on the final published head remains an independent gate.
