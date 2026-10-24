# Wheel-input validation and retained construction

[Canonical effort and completion gate](README.md). This is the next bounded
consumer slice under #4876, not a replacement for the
[exact-profile contract](exact-profile-contract.md) or its approval adapters.

## Intent and ownership

Implement the existing requirement to inspect real wheel bytes before
constructing an exact selected runtime. Cached and newly acquired files must
pass the same checks; a cache filename, manifest or matching digest alone is
not feed admission or profile approval.

This closes the Runtime Assembly vision's inspected-input and immutable
construction intent without changing authority, feed, fallback or lifecycle
policy. The Consumer coordinator owns this slice through normal review, CI
and integration. #6234/#6737 retain profile approval and engine application;
#6941 retains candidate constraints; the existing installer and runtime owners
retain construction, publication, health, rollback and cutover.

The initial component must remain usable by a bootstrap interpreter without
adding a runtime dependency. Put its canonical implementation in
`libs/installer-engine`, vendor it through the existing synchronizer, and use
the existing profile validator for record/closure semantics. Do not introduce
another installer, approval store, feed resolver, daemon or shared mutable venv.

## Ordered implementation

1. Land this concrete plan before code. Preserve the broader effort's open
   acceptance; this sub-plan does not authorize enabling an incomplete consumer.
2. Add bounded wheel-byte inspection. The caller supplies the selected exact
   record and explicit finite input limits/deadline. Verify ordinary owned
   inputs, streamed file size and SHA-256, unambiguous ZIP members, bounded
   metadata, distribution identity/version, Requires-Python, Requires-Dist,
   extras and WHEEL tags against the record. Do not extract archive members or
   execute a backend. Reject malformed, duplicate, linked or changed inputs.
3. Treat the result as inspected-byte evidence, not approval. Keep profile
   authentication and feed/seasoning/yank/redirect admission with their existing
   owners. Reuse the same inspection for candidate files and cached files;
   there is no new permission granted by being cached.
4. Compose inspected inputs with the existing frozen/retained construction
   transaction. Persist exact mode, source/artifact/profile, target and policy
   bindings through the installation owner's existing receipt boundary.
   Unchanged admitted reuse needs no feed request; changed policy or supplied
   revocation invalidates it. Missing evidence is unavailable, not permission.
5. Build only from the complete inspected exact set through the existing
   candidate constructor. Disable network/resolution, ambient indexes/caches,
   editable installation and build-backend discovery for wheel assembly.
   Preserve actual installed health, input custody and immutable selection.
   Do not activate any profile consumer before its authenticated adapter and
   acquisition contracts pass.
6. Keep artifact availability optional. Authority-selected verified-source
   fallback retains its distinct exact lock, source proof and trigger without
   requiring an artifact manifest. Integrity/provenance refusals are never
   reclassified as availability to reach that fallback.

Detailed APIs and test fixtures are agent-recommended implementation choices.
Limits initially remain explicit caller inputs, not guessed enabled defaults.
Measure representative permitted closures before enabling production defaults.
Do not modify another contributor's open candidate/profile PR to obtain inputs.

## Next acquisition component: exact selected wheels

This realizes the existing acquisition item; it does not authorize a new feed,
profile authority, resolver or production consumer. Review this concrete
integration design before implementation.

1. **Start from the existing authority-selected artifact profile.** Its complete
   wheel names, versions, filenames and hashes are already fixed. The native
   UV compiler remains the source-mode resolver; do not expand this acquisition
   client into a dependency resolver, select a different pin or enumerate feed
   release histories. #8705's source candidate is still not governed-feed
   admission. Source-mode acquisition and receipt composition remain separate
   parent gates.
2. **Capture existing configuration under existing custody.** The existing
   installation owner and retained UV configuration supply the effective
   explicitly configured indexes, named-index identities, authentication and
   applicable seasoning policy. Do not add a second UV configuration reader,
   trust an implicit public default or reuse the promotion build-tooling
   exception. This is an authority-invoked operation, not approval inferred
   from reading incidental environment variables. Fail unsupported effective
   configuration explicitly rather than silently changing its meaning.
3. **Keep additional download trust in the existing owned policy.** Configured
   indexes may serve their own files; a page cannot authorize a different
   origin. Optional exact HTTPS download origins belong in the existing
   installation-owned `runtimeProfiles` policy, not a new approval file,
   global allowlist, mandatory environment variable or public profile.
   Absent that optional narrowing/extension, only the selected index origin is
   permitted. No wildcard host, redirect-learned host or public-index escape is
   admitted. Bind effective acquisition policy to the local evidence/reuse
   identity without publishing private URLs.
4. **Use an invocation-local, bounded HTTP client, not a service or proxy.**
   Fetch only normalized project metadata and the selected wheel from an
   affirmatively permitted index. Accept standard simple-index HTML/JSON while
   preserving exact file hash, upload-time/seasoning, Requires-Python and
   yanked/withdrawn facts. An exact pin does not override a refused file.
   Reject ambiguous/conflicting entries, unsupported encodings and missing
   required policy facts before downloading the wheel. Wheel metadata-sidecar
   hints never replace the existing actual-byte inspector.
5. **Check every network hop before issuing its request.** The initial link and
   every redirect must satisfy the selected policy and HTTPS requirement.
   Preserve native certificate/explicit CA and named-index authentication
   semantics; never forward index credentials to another origin or include
   them in argv, diagnostic exceptions or receipts. Unsupported credential/
   trust-provider configurations defer without a request or an activation.
   Keep ordinary package-source behavior unchanged until this lane is complete.
6. **Bound and retain the whole transaction.** Declare positive finite request,
   redirect, per-project candidate-count, metadata-byte, per-wheel/closure-byte,
   temporary-storage and shared-deadline limits before the first request.
   Stream through these inclusive limits, hash actual bytes and retain only
   invocation-owned partial files beneath the existing private input owner.
   The result stays live only through complete-profile inspection and the
   existing immutable constructor. Failure/cancellation cleans partial inputs
   without touching caller caches or the selected healthy generation.
7. **Qualify the real consumer seam before enabling it.** Controlled feeds must
   prove exact-file selection, same-origin success, explicitly permitted
   redirects, pre-request refusal of untrusted/insecure targets, credential
   isolation, OS/explicit-CA trust, seasoning/yank refusal and every independent
   budget. Exercise acquisition into real inspected/offline construction on
   fresh Windows and available POSIX inputs, including poisoned ambient
   configuration and failed-build preservation. Choose enabled defaults only
   after measuring representative closures; explicit test budgets are not
   production defaults.

No new daemon, egress rule, routing broker or alternate constructor is needed.
The acquisition mechanism establishes file-policy and byte facts; the existing
installation owner still selects modes and owns activation, source fallback,
supplied revocation and unchanged admitted reuse. A missing file can be a typed
availability outcome. An untrusted source, malformed admission evidence or
integrity failure cannot become that outcome.

## Validation and completion

- [x] The bounded archive/file-inspection component and complete-profile byte
  comparison are integrated through one shared verifier (#8419). This closes
  neither complete retained-set custody nor consumer approval/construction.
- [x] Actual wheel fixtures match the full selected record; independently
  changed hash, name/version, tags, dependency metadata, extras and Python
  requirement refuse before construction.
- [x] Duplicate/ambiguous metadata, unsafe ZIP paths, oversized/compressed
  metadata, file/input-count limits, deadline exhaustion and source replacement
  refuse without extracting or executing archive content.
- [ ] Fresh and cached inputs follow the same validator. An unadmitted cache
  entry cannot manufacture approval; changed policy/revocation, target, mode,
  profile, source or artifact identity invalidates retained reuse.
- [ ] A real isolated child assembles a tiny exact wheel closure with poisoned
  ambient resolver/cache settings and unavailable network. Verify installed
  distributions and health, not merely the planned argv.
- [ ] Failure/cancellation retains the healthy prior generation, settles owned
  children and removes only invocation-owned partial inputs. No selector moves
  before the existing completion/activation boundary.
- [ ] Native Windows and available POSIX/fresh-environment paths cover changed
  consumers; record unavailable lanes rather than crediting skips as passes.
- [ ] Existing profile, source, dependency-policy, retained-construction and
  vendoring guards pass with normal review/CI. Record concrete component
  delivery separately from authenticated consumer enabling and installed proof.

This slice is complete only when its component and integration checks pass or
an explicit named receiving objective accepts a residual. The parent effort
retains all acquisition, platform, fallback, retention and rollout obligations.

## Journal

### Component merged; authority/acquisition integration remains open, 2026-10-24

#8534 merged as `bd4c5539a3336b02c828e26e3d1a3e8ccf1cef5d` after
current-head approval with no findings or open threads, required CI and all six
Windows/Linux wheel-input, source-profile and real artifact jobs passed.
The bounded constructor/receipt mechanism is delivered. This sub-plan still
retains authenticated admission, supplied revocation, measured acquisition
defaults and enabled consumer/source-fallback acceptance; the parent effort
remains Active. No record, receipt digest or temporary snapshot becomes an
approval adapter, and no production mode is enabled by this merge.

### Closed transaction and installed receipt integration, 2026-10-24

Implementation now composes the complete-profile verifier and retained snapshot
with the existing Python candidate constructor. The explicit closed transaction
accepts no source/resolver arguments, strips ambient UV/PIP/Python inputs and
uses only offline, hash-required local wheels with no cache, dependency
resolution or build discovery. Ordinary source seasoning refusal remains intact.
The existing construction identity and origin receipt bind the complete
profile/lock and actual installed distribution versions, exact closure and
hash-qualified origins; source receipts remain compatible.

Native Windows controls exercise two real independent venvs, a transitive wheel
dependency, PEP 440 version aliases, poisoned ambient configuration, unchanged
cached-byte reuse from a different retained path, changed mode/profile/target/
policy bindings, installed-origin/closure corruption, and health failure/
cancellation with the selected prior preserved. Hosted Linux/Windows validation,
required CI, review and merge are still owed for this component. No production
consumer or approval adapter is enabled, and acquisition, revocation, source
fallback and broader effort acceptance remain open.

The prepared CPython 3.14 native run exposed NTFS last-access updates advancing
handle change time without a byte/path/mtime change. Two failing-first controls
reproduced the false refusal and the absence of OS-enforced read custody.
Windows source/retained handles now deny writes and deletion while permitting
readers; the existing identity/digest checks remain, and POSIX change-time
checks are unchanged. The 133-case native snapshot/construction/profile cohort,
including the extended-length-path regression and the real editable backend
control, passes on prepared CPython 3.14 tooling. The canonical candidate suite
also passes all 68 cases. A broader source-policy/compile/inspection/sync run
passed 558 cases with one unavailable POSIX-only case; its missing-backend
failure was resolved by supplying the declared seasoned test backend in the
isolated test venv and rerunning that exact native control successfully.
Hosted evidence and review remain required; local passes do not close those items.

On #8534's initial head, all six optional Linux/Windows wheel-input,
candidate-profile and real artifact jobs passed. Required CI correctly refused
the oversized policy module and stale exact-source admission; the projection
now lives in the existing private policy helper and the admitted byte-only
verifier digest is updated without changing the no-actor inventory or gates.
The review's Medium large-closure argv finding reproduced at 70,534 characters.
Health now reads the existing owned receipt and validates parent-supplied
canonical SHA-256 bindings instead of transmitting full profiles/maps in argv.
A real 97-wheel construction/reuse control enforces a 16,000-character health
command bound. The 100-case closed/profile cohort and four focused existing
source construction/origin/reuse controls pass after this correction; fresh
current-head CI, native acceptance and review are still owed.

The next current-head review confirmed the argv issue resolved and identified
caller-relative UV paths surviving into the private working directory. The
real constructor regression reproduced the relative command. Resolution now
happens under the canonical policy owner before retention, preserving native
Windows executable selection and PATH command-name support; all derived policy
helpers are synchronized. Four focused real path/command and poisoned-config
controls pass. Required CI and all six native jobs passed on the previous
head, but fresh evidence is still required after this correction.

### Exact-set byte custody, 2026-10-24

The next component adds `installer_wheel_snapshot.retain_wheel_inputs` beneath
the existing private temporary-input owner, with explicit count, per-wheel,
closure-byte, temporary-storage and shared-deadline budgets. Read-only retained
copies/streams compose with the complete-profile verifier; originals remain
caller-owned. Source identity/mutation checks, custody rechecks and cancellation/
partial-copy cleanup do not confer approval or introduce another constructor.
Offline UV assembly, installed receipts/reuse, authenticated adapters, fallback
and the parent effort's broader acceptance remain open.

#8523 merged as `1c386200bd4297e0bb5321d43185e485f75777bd` after current-head
approval with no findings/threads, required CI and all four Linux/Windows
inspection/artifact jobs passed. Thirty snapshot controls and the existing
archive/profile controls prove the input-bound refusal item above. Local
validation passed 289 cases with one POSIX-only installer case unavailable on
Windows; that skip is not credited as platform proof. The next owned stretch
is the canonical closed UV transaction and installed receipt/health integration,
not a second constructor or production consumer enabling.

### Component delivery and next stretch, 2026-10-24

#8419 merged as `90663eab5b44d2d63032fa8a0ac95cf016ce8d6d`.
The reviewed final head passed the required 1,414-case packet and all four
Linux/Windows native inspection and real-built/offline artifact jobs. Its
16,547,830-byte coverage report remains under the unchanged 16 MiB bound.
The existing worktree is pulled forward for retained exact-set snapshots and
canonical offline constructor/receipt integration. All broader validation items
above remain open until their actual consumer contracts are proven.

### Component validation, 2026-10-23

The shared stdlib archive verifier and repository-only semantic profile facade
are implemented without enabling a consumer. Targeted native wheel/profile/
promotion selectors pass (174 tests), and installer-contract controls pass.
The full regression cohort passed 362 cases; its one digest-order diagnostic
was corrected and 97 focused retests pass. Five synthetic
wheel controls pass inside the actual no-actor collector. A real native Windows
bootstrap child uses no site packages. Vendoring and touched-Python lint pass.

Retained exact-set count/total limits, frozen assembly, authenticated acquisition,
receipts/reuse/fallback and installed health remain open. Linux hosted required
CI must pass before merging this component; no local POSIX lane is available.
The full packet's native Windows diagnostic does not pass: an activation
preservation case attempts a forbidden native-loader operation, and the
dropin-registry FIFO case skips on Windows. These are not credited as unit
proof; the inspector's five-node diagnostic preserves the unchanged no-actor
guard and does not replace the complete hosted packet.

### Complete-profile integration, 2026-10-23

After #8399 merged, the bounded archive core was integrated beneath its
existing complete-profile API rather than retaining the unpublished per-wheel
semantic facade. Complete filename sets, shared archive/unpacked budgets,
per-metadata-file budgets, SHA-256/384/512 RECORD support and strict first-party
source/generator/SHA-256 policy remain intact. Source-binding facts retain
SHA-256 regardless of a runtime wheel's RECORD algorithm. Optional explicit
inspector limits and a shared deadline also bound archive hashing and the
packaging parsers' compressed-tag expansion.

The combined component/bootstrap cohort passes 181 tests. A broader run passed
622 cases with 15 existing native skips and exposed a platform-dependent UV
mock; pinning that mock's selected executable to a fixture path preserves its
authority checks without touching live UV. The corrected source/profile/core
cohort passes 233 tests. The four derived inspector copies and touched-Python
syntax/import checks pass. Fresh hosted native, real-built artifact, required
CI and review evidence are still required for this integrated head; the prior
head's green runs are not credited to it. Authenticated acquisition, retained
construction and the parent effort's remaining gates stay open.

The integrated review identified three caller-boundary defects: the promotion
archive cap leaked into runtime verification, initial hashing used only the
closure budget, and profile validation renewed rather than shared the supplied
deadline. All reproduced before fixing. Runtime archive bounds now derive from
the caller, initial hashing reads no more than the per-wheel cap plus one
overflow byte, and validation/packaging/dependency walks retain the same
deadline. The corrected profile/source/native cohort passes 292 tests; fresh
review and hosted evidence remain required after these fixes.

On `35a9eb058a`, all four hosted Linux/Windows inspection and real-built/offline
standalone artifact jobs passed. Required CI executed all 1,411 eligible cases
successfully, then correctly rejected a 17,291,606-byte coverage report at its
unchanged 16,777,216-byte packet-file bound. Diagnostic reporting now names the
observed size and bound. The profile tests' state-free class wrapper was
flattened into the same 26 functions / 82 parameterized cases to remove repeated
class-name text from per-line coverage contexts; every test body/decorator and
the packet limit remain intact. All 82 cases pass locally. The next CI run must
prove the resulting packet actually fits; this is not a test-selection or
admission exception.

The subsequent approval still listed a Medium ZIP32-boundary finding and a Low
stale API reference, so merge remained blocked. The 65,534/65,535/65,536-entry
stdlib regression reproduced only the exact ZIP32 maximum failure. Count-only
`0xffff` now permits actual-directory verification without a ZIP64 locator;
size/offset sentinels still require ZIP64 and all three over-budget counts
refuse. The four derived copies are synchronized, the removed facade reference
now names complete-profile verification, and 243 component/bootstrap/profile
checks pass. Fresh final-head native/CI/review gates remain required.

The next review raised an overview-only directory-budget mapping defect despite
showing no inline issues. Valid wheels with central-directory comments reproduced
the failure with and without explicit inspector limits. Per-file metadata caps
now remain independent of directory/aggregate budgets; first-party promotion
keeps its own stricter bounds. A failing-first final-fact-parsing deadline case
also closes the direct stdlib return boundary, not just its higher-level callers.
No approval or budget gate is bypassed by these corrections.

Concurrent upstream work added `agent-pull-requests` to the Python installer
consumer roster before this component merged. The reconciliation preserves
that new consumer and synchronizes the verifier into its payload too, rather
than restoring the older roster. Its patch changefile was already included.

### Final digest deadline, 2026-10-24

A final-rehash EOF read can exhaust the deadline without producing another
chunk, so a pre-read-only guard permits success after expiration. The
failing-first retained-stream regression reproduces that exact last-wheel
boundary. The digest helper and complete-profile return boundary now both check
the shared deadline before success. Validation history is separated from the
completion specification under this Journal heading.

Four newly added test labels were shortened without changing bodies,
decorators or cases: the admitted report measured 193,680 repeated label bytes
for those redundant prefixes. This keeps all new regressions in the unchanged
bounded packet rather than dropping tests or raising its cap. All 247 selected
component/profile/bootstrap checks pass locally; fresh publication gates remain
required.
