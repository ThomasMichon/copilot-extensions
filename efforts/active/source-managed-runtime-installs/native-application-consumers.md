# Standard environments and native application consumers

Back to the [canonical effort](README.md).

**Agent-proposed bounded execution plan; review precedes behavior changes.**
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
- [ ] Trace the advertised catalog entry, native shell/PowerShell adapters,
  command binding and hook execution against the actual installed generation.
  Establish normal preparation/adoption prerequisites through their owners;
  do not manufacture metadata to silence an unqualified fixture.
- [ ] Reuse canonical physical-environment selection in affected consumers,
  including standard POSIX/Windows interpreter paths and real venv prefix
  behavior. Preserve completion, explicit source-generation selection,
  physical-path and foreign-scope checks; do not flatten or alias the project.
- [ ] Resolve application-owned command/readiness integration through the
  existing installation and command owners. Do not synthesize a legacy
  deployment receipt or infer ownership from a name or package-version match.
  Prefer the existing operation and immutable installed descriptor; if the
  boundary needs expansion, declare it provider-first under the
  [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
  before migrating consumers.
- [ ] Preserve valid managed-deployment denial and supported standalone
  behavior. A healthy selected application remains callable despite payload
  skew; unsupported or missing requirements produce actionable diagnostics,
  not silent exits, runtime reconstruction or whole-hook suppression.
- [ ] Route affected hook/command imports through the existing external-cache
  owner. Preserve caching and normal non-deployment hook duties without new
  slot files, cache exemptions, site-startup hooks or deletion of prior state.
- [ ] Qualify the exact native consumer, then publish source changes through
  review/CI and actual Main promotion. Record released-consumer results and
  remaining adoption scope separately; no host rollout is included.

## Validation plan

- [ ] Focused controls distinguish completed physical nested environments,
  supported flat compatibility, bad/redirected environments, source-qualified
  selection and unchanged fallback meaning. Use real UV-created venvs to check
  `pyvenv.cfg`, `sys.prefix`/`sys.base_prefix`, installed imports and both
  platform interpreter layouts; no activation-policy or symlink privilege
  changes.
- [ ] Exercise the actual command/binding parser boundary across supported
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
