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

## Validation and completion

- [x] The bounded archive/file-inspection component and complete-profile byte
  comparison are integrated through one shared verifier (#8419). This closes
  neither complete retained-set custody nor consumer approval/construction.
- [x] Actual wheel fixtures match the full selected record; independently
  changed hash, name/version, tags, dependency metadata, extras and Python
  requirement refuse before construction.
- [ ] Duplicate/ambiguous metadata, unsafe ZIP paths, oversized/compressed
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

### Exact-set byte custody, 2026-10-24

The next component adds `installer_wheel_snapshot.retain_wheel_inputs` beneath
the existing private temporary-input owner, with explicit count, per-wheel,
closure-byte, temporary-storage and shared-deadline budgets. Read-only retained
copies/streams compose with the complete-profile verifier; originals remain
caller-owned. Source identity/mutation checks, custody rechecks and cancellation/
partial-copy cleanup do not confer approval or introduce another constructor.
Offline UV assembly, installed receipts/reuse, authenticated adapters, fallback
and the parent effort's broader acceptance remain open.

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
