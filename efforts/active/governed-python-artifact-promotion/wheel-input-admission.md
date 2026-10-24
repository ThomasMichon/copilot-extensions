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

- [ ] Actual wheel fixtures match the full selected record; independently
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
