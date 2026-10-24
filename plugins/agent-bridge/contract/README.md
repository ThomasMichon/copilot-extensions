# agent-bridge contract evidence

This directory freezes observed bridge contracts so compatibility changes are
reviewed against independent evidence rather than producer-generated snapshots.
The registry is repository-side evidence; no runtime component reads it.

## Updating a contract

Classify the change before touching a golden packet:

- An implementation-only flow change leaves every fixture and its original
  `captured_from` provenance untouched. Refresh only that source's fingerprint
  in `registry.json`, set `semantic: false`, and explain the unchanged wire
  behavior in `non_semantic_reason`. Run the affected producer/consumer tests;
  the classification is not permission to skip behavioral validation.
- A semantic change keeps `semantic: true` and requires matching current
  evidence for the affected source. Add the affected capture and its provenance;
  unrelated captures may retain their original commits and packets. They do not
  need to share the newest capture's commit or source bytes.
- Actual protocol generations, capability floors and meaningful wire digests
  remain contract assertions. This maintenance path does not bypass a version
  bump or mixed-version validation when the contract really changes.

The registry checker owns source freshness, capture provenance and immutable
fixture hashes. Packet replay tests own behavioral expectations; they must not
add a second, stricter rule that every capture matches today's implementation.
The checker still rejects unrefreshed source hashes, unjustified non-semantic
classification, uncovered semantic changes and corrupt historical provenance.
Never bulk-rebaseline goldens to make an implementation-only update pass.

When a wire contract does change:

1. Change the owning source and its tests.
2. Add the affected capture or a new generation fixture; preserve historical
   captures. The three refreshable current authority vectors use the bounded
   capture flow below.
3. Record exact source commit, plugin version, capture method, support window,
   and removal gate in `registry.json`.
4. Refresh every changed fixture and source SHA-256 in the registry. Text
   fingerprints use UTF-8 with line endings normalized to LF so Windows and
   POSIX checkouts produce the same evidence.
5. Run:

   ```text
   python tools/check-agent-bridge-contracts.py
   python tools/run-plugin-tests.py agent-bridge --guards
   ```

HTTP generation 47 adds explicitly negotiated incompatible-preservation cold
drain. Confirmed disjoint ranges select cold by default; graceful-only control
refuses before destructive effects. Independent expected control vectors live in
`inputs/http47/negotiated-drain.json`; the real ASGI and owned-process counterparts
are in `tests/test_cold_drain.py`. The preservation range is independent of the
HTTP generation. A confirmed cold result cannot satisfy a required-graceful
consumer. Same-native recovery admits one new outage-keyed reconnect prompt,
not a replay of the interrupted request. See the
[cold-drain contract](../docs/cold-drain.md).
As with every semantic generation, publication must capture matching committed
source and update the registry; expected input vectors alone are not immutable
capture provenance. Published generation-46 startup packets and older evidence remain unchanged.
An explicitly affected source may promote its registered current-generation
unchanged-source link to shared semantic observations. That transition requires
the same canonical source path and the exact registered packet hash; dirty,
unregistered or foreign packets still refuse. Older-generation packets and
their provenance are never rewritten by that promotion.

HTTP generation 45 adds distinct ordinary native keyed-admission and
receipt-only endpoints. Immutable binding facts and an admission receipt are
inserted atomically with the turn using the existing unique delivery key.
Identical running or settled replay returns that receipt without another SDK
submission; an admitted-but-not-submitted crash is not proof of execution and
is never automatically resubmitted. Intentional unkeyed follow-up turns remain
unchanged. The new CLI transports the complete invocation through UTF-8
stdin/file input and refuses an older receiver without legacy fallback.

Keyed sends and receipt reads reject alternate payload, envelope, notification,
queue/interrupt, reply-wait and history options before client preflight or
stdin/file decoding, using `mixed_native_invocation`. Canonical parser defaults
(`prompt` kind, `steer` delivery), receipt-only/no-wait and output/feed-format
flags remain accepted; they do not replace the frozen invocation or start a
different delivery mode. Real-parser refusal probes require zero input reads
and zero requests and replay the independently expected CLI refusal vector.
An explicitly empty invocation selector remains keyed intent, never an unkeyed
fallback, and is refused before input reads or client preflight.

The independently authored `ordinary-native-admission-generation-45.json`
vectors run through the typed public ASGI routes and actual turn journal with
a mock SDK, covering both running and settled replay and changed-prompt
refusal. Linked source re-attestations preserve the frozen previous vectors
apart from the advertised protocol version; explicit prior fixture/hash links
are checked by tests. Provenance names a real local source checkpoint, not an
invented future publication commit. This is source evidence, not a deployed
daemon, qualified native session or task-verification claim.

## HTTP-46 startup observation evidence

HTTP generation 46 adds startup-progress capability negotiation for observing an
existing ordinary hosted session. `inputs/http46/startup-observation.json` is
independently authored operation evidence: the real parser, producer, observer,
GET preflight, extracted prompt client and send renderer replay its expectations
against synthetic peers and clocks. It covers JSON and text protocol-45 refusal
before mutation, receipt-time communication expiry, additive progress/keepalives,
the independent 300-second startup ceiling, readiness ending startup clocks, and
exactly one `no_resume: true`, `allow_retry: false` prompt submission. A lost
admission acknowledgement after readiness remains code 70/unknown, not retryable.
This packet is not a real deployed-daemon or native-host capture.

Every generation-45 fixture remains byte-for-byte frozen. Generation-46 linked
re-attestations retain those complete old packet bodies, including their old
advertisements and capability floors, and name the exact predecessor path/hash.
They assert preservation of those operation families, not new independent
runtime observations or current health responses. The separate new-operation
packet asserts the actual current advertisement/floor of 46. Shared startup
packets explicitly link that new operation; they are not independent captures.
`client_prompt.py` owns the extracted observed prompt-admission wire behavior
and is registered as semantic. Preparation/connection forwarders are classified
non-semantic because the registered startup producer owns their event framing.

After committing matching source, stage the new replay test and independent
input so the repository snapshot runner includes them, then run with the
repository's existing Python 3.11 outer tooling:

```text
python plugins/agent-bridge/contract/capture_http46.py --source-commit <full-source-commit> --write
python plugins/agent-bridge/contract/capture_http46.py --source-commit <full-source-commit>
python tools/run-plugin-tests.py agent-bridge --allow-explicit-tiers --test-file tests/test_http46_startup_evidence.py
python tools/check-agent-bridge-contracts.py --base <reviewed-base>
```

The generator verifies committed source bytes and all historical fixture hashes,
runs the bounded model-free replay before writing, and writes only new
generation-46 fixture paths plus the registry. It does not edit old captures,
production source, capability floors or the checker. Stage its new outputs
before the final snapshot-based replay. Missing history is never fetched.

The client and CLI keyed-send vectors reference that shared new-operation
evidence explicitly; they are not independent captures or legacy re-attestations.
Prompt admission also retains linked native-bound and managed-drain predecessor
families. Workspace creation has its own real parser/client-builder,
complete-receipt and floor-refusal vectors, not a copied prompt-admission body.
`provider-creation-refresh-generation-45.json` preserves its consumer's named
predecessor alias. The validator checks these families and shapes in addition
to source/fixture hashes.

Independent expected inputs live in `inputs/http45/`. After committing matching
source, use the Bridge development interpreter with `agent-procutil` available:

```text
python tools/capture_bridge_http45.py --source-commit <full-source-commit> --write
python tools/capture_bridge_http45.py --source-commit <full-source-commit>
```

This owned capture verifies source bytes, runs the bounded model-free operation
probes through the existing contained runner, then writes only the corrected
current families and registry. The no-write command verifies reproducibility.
Historical vectors and capability floors are preserved. Execute it only in an
admitted test environment; a passing source replay is not host qualification.

## Capturing HTTP generation 47

`tools/capture_bridge_http47.py` owns only generation 47. Do not use or broaden
the generation-45 or three-vector preference-authority tools for this flow.

Make an ordinary reviewed **source checkpoint commit first**, including the
runtime changes, capture tool/helper, independent inputs and selected probe
tests. Keep hooks enabled. This repository's `tools/hooks/pre-commit` checks
staged lint/structure/size; it does not require generated contract evidence.
The contract gate remains required before publication/CI completion, not before
this local source checkpoint. No hook bypass, fabricated SHA, temporary guard
disablement or version edit is needed. The later evidence changes form their
own ordinary commit.

The generated registry uses compact JSON encoding to retain every
provenance record within the repository's unchanged file-size limit. Packet
formatting and historical packet bytes remain unchanged.

From the Bridge development interpreter in an admitted, bounded test environment:

```text
python tools/capture_bridge_http47.py --source-commit <real-full-source-checkpoint> --write
python tools/capture_bridge_http47.py --source-commit <real-full-source-checkpoint>
python tools/check-agent-bridge-contracts.py
```

Use the consuming harness's bounded supervisor around these invocations. The
capture itself runs the existing contained plugin runner with an exact
model-free ASGI/owned-process probe portfolio. A missing, skipped, failed or
duplicated required probe refuses publication. No real model, workspace owner,
service installer or production target is used.

Independent expected cases are in `inputs/http47/capture-cases.json`.
The generated primary packet records actual observations, including graceful,
strict refusal, authenticated default cold drain, real fixture-process exit,
same-native loading, actual reconnect RPC response evidence and unchanged
no-native allocation. Supporting native-fixture regressions cover ownership
races, interrupted drain/registration/claim windows, supersession ordering,
partial exit and uncertain admission. The required portfolio also covers
ordinary and cold recovery versus concurrent drain, strict completion-flag
decoding, the unresolved-reconnect fence across
prompt-triggered, automatic and externally seeded handoffs, durable explicit-stop
intent across recovery and schema migration, and real cancelled launcher threads
retained in same-frontend, rehydrated and orphan-row drain censuses.
These are source evidence, not native
Copilot, deployment or host qualification.

The affected semantic owners share this explicitly labelled operation evidence.
Unchanged semantic sources receive link-only reattestations to hash-verified
previous-generation vectors; they are not copied or relabelled as newly observed
runtime packets. Existing generation-46 and older files, provenance, support floors and
other contracts remain intact.

The tool requires a genuine local generation-47 commit and committed execution
inputs, checks source/input bytes before and after probes, and binds imported
local production bytes. The primary packet retains the complete execution
manifest, not just its digest. The canonical checker reconstructs its inventory
from the source checkpoint and validates each source/blob/hash, including the
cold-termination owner. If the checkpoint is orphaned, validation requires the
complete current inventory and independently provable content-addressed blobs;
an unprovable or changed inventory requires a fresh capture.

Capture-only instrumentation verifies production module origins and executable
code in the observer and every contributing recovery worker, Session Host and
stdio peer. Each actual child launch must have its own pre-execution entrypoint
receipt; crash-window exits do not depend on `atexit`. Missing bootstrap,
substituted child-only source, stale executable bytecode or incomplete launch
coverage refuses capture. The observer's third-party pytest entrypoint is
checked against its installed distribution record, not claimed as repository
source. Private PID/path receipts are not published. The deterministic packet
records the binding policy; the no-write pass must repeat the actual checks.

Every output is confined to the owned HTTP-47 paths and
registry; traversal, symlink/reparse, hardlink, collision, unowned output and
dirty-output cases refuse before writes. The no-write invocation reruns the
probes and verifies reproducible evidence without rewriting public outputs.
Registered HTTP47 packets are refreshable only within the exact write plan
derived from the committed registry, under the existing family, generation,
source-owner and registered-or-deterministically-planned hash checks. All planned
outputs, including absent files and the registry, are snapshotted across probes.
Unplanned packets, other contracts and deferred references retain committed-hash
protection; a write-plan collision with any immutable reference refuses before
probes or writes. Registered refresh followed by same-source no-write therefore
does not require preserving obsolete hashes of its own authorized outputs.
Neither invocation fetches history or supplies a guessed publication commit.

HTTP generation 44 added the Bridge-owned startup plugin-readiness requirement,
after generation 41's direct remote ACP targets and generation 42's attention
boundaries, followed by generation 43's managed remote ACP ownership.
All historical generations and their capability floors remain
unchanged.
Its frozen vectors include typed readiness results, readiness-bound task/mode
inputs and omission-compatible ordinary input. Every readiness golden is replayed
through the public HTTP router, durable readiness-control acknowledgment and
SQLite inbox. Public input uses `kind: prompt`; only the admitted inbox message
carries the generated `startup:prompt:<control-id>` kind. The frozen control
digest, admission receipt, inbox packet, retry identity and changed-selection
refusal are checked at that boundary, not only through model parsing. The native
observer and inbox tests consume the same generated packet; a pending selection
must produce no SDK send or acknowledgment. Historical generation-40 packets remain unchanged,
and earlier capability floors do not move merely because the health
advertisement advances. Each semantic source has current-generation provenance,
including the joined-session observer and delivery producer.

## Re-attesting the current preference-authority vectors

After committing the authority source, run from the repository root:

```text
python tools/capture_bridge_current_authority.py --source-commit <full-source-commit> --write
python tools/capture_bridge_current_authority.py --source-commit <full-source-commit>
python tools/check-agent-bridge-contracts.py
```

The capture verifies that every authority-closure source still matches the
selected commit. It runs the real terminal settings reader and receipt emitter
with deterministic synthetic settings, namespace, PID and channel inputs; no
real child is executed. It verifies the resulting receipt, every existing
negative vector, terminal consent/refusal, and the real Host's
verify-before-consent and refusal/reaping order. It refuses changes to receipt
semantics, authority closure, binding environment or wire generation rather than
silently broadening this capture.

The component digest is an actual attested wire value, not just provenance
bookkeeping. Changing an included authority component legitimately changes it,
even without a framing-version bump. Refresh those three current captures with
this tool; do not relax their live-digest assertions or re-attest unrelated
HTTP packets to accommodate that bounded change.

Only the three current `wrapper-authority-v1`, `wrapper-exec` and
`wrapper-consent` fixtures and their registry hashes/provenance are written.
Historical fixtures remain unchanged. Review these four outputs; do not replace
the captured commit with a guessed future publication commit. A squash may make
the genuine capture commit unavailable: the checker treats historical commit
lookup as opportunistic, verifies a resolvable blob against its SHA-256, and
always validates tracked source/fixture hashes. A wrong resolvable blob/hash
still fails. Re-running without `--write` verifies reproducibility; unexpected
dirty source or output bytes are refused. Before any output is written, all
four approved files and their parent chains must remain inside the resolved
repository root, without symlinks, reparse points or file aliases. Any invalid
output refuses the whole write pass.

Externally owned contracts remain references until their owner promotes them.
Do not copy or reinterpret another protocol's normative corpus here.

Historical producer tests use local Git evidence when available. A minimal
disconnected snapshot may omit an older release's commit; the registered frozen
producer source then supplies the same bytes, checked against both its SHA-256
and Git blob identity before execution. Other Git errors still fail. This does
not fetch history or replace the registry's independent provenance checks.
The historical test's read-only Git command trusts only its explicitly selected
checkout for that invocation, so a root-staged test snapshot does not depend on
the test user's redirected HOME carrying a global `safe.directory` setting.
