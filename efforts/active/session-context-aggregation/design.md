# Session Context Aggregation - Investigation and Proposed Design

[Effort](README.md) ·
[Issue #1325](https://github.com/ThomasMichon/copilot-extensions/issues/1325)

## Findings

### The failure is at the host aggregation boundary

The host runs all configured `sessionStart` hooks, but only one non-empty
`additionalContext` output reaches the model. Each producer can be correct in
isolation and still disappear. Adding retries, changing hook order, or making
individual producers faster cannot create a correctness guarantee.

The workaround therefore needs one suite-owned aggregator to produce one
combined value. Trusted plugin-owned `.context-injection/config.yaml` adoption
selects the exact direct marketplace authority
`context-injection@copilot-extensions` and binds its engine schema and version;
host settings only enable the plugin. The authority discovers and runs every
declared pure contributor.
Once that authority is proven, participating producer hooks rendezvous on the
same result and emit the same cached aggregate bytes as the authority. Whichever
hook result the host retains is therefore equivalent. A later `{}` can erase an
earlier non-empty result on affected hosts, so producer-empty is not a valid
compatibility protocol.

Current public documentation guarantees only that plugin hooks are loaded after
policy, repository, user, and inline hook sources. It defines no hook priority,
plugin dependency, `loadAfter`, or cross-plugin ordering field. Alphabetical
plugin names, marketplace order, installation order, and JSON object order are
therefore not contracts to build on. Byte-identical shared output makes those
unknowns irrelevant rather than trying to infer an ordering seam.

Neither design should absorb bootstrap, registration, service reconciliation,
or other side effects that happen to run at `sessionStart`; those hooks continue
to run directly and return `{}`.

### The resolver foundation exists, but its question differs

`agent-plugin-activation` resolves a machine-wide effective set for global and
registered-project drop-in reconciliation. `plugin-resolve` reads one
repository's settings and local marketplace declarations. The session
aggregator needs a narrower answer:

```text
Given this session cwd and this explicitly selected aggregator payload,
which source-qualified plugin payloads are effectively active here?
```

That resolver must combine user-global and current-repository settings, honor
local overrides, locate installed remote-marketplace payloads, retain directory
marketplace containment, recognize staged payloads when the host exposes them,
and return uncertainty rather than guessing.

The Python libraries should remain the executable reference where available.
Because the aggregator must run before any sibling runtime is provisioned, the
operative hook needs dependency-light native PowerShell and POSIX parity rather
than importing agent-bridge's runtime.

### Plugin dependencies are not host-enforced

Copilot plugin manifests may document a `dependencies` field, but the current
host does not install transitive dependencies. Contributor plugins cannot assume
that declaring the aggregator makes it present. Consumer settings, setup flows,
and documentation must explicitly enable it until the host provides an enforced
dependency contract.

This makes removal of direct producer hooks unsafe unless each producer can
prove the aggregator is present and compatible at session start.

## Proposed architecture

### 1. One explicit aggregator authority

Add a payload-only plugin named `context-injection`, containing:

- one `sessionStart` command hook;
- dependency-light PowerShell and POSIX aggregator implementations;
- the session-exact active-plugin resolver;
- contributor schema validation; and
- tests and clean-room fixtures.

Trusted `.context-injection/config.yaml` repository configuration selects exactly
`context-injection@copilot-extensions` and binds the compatible engine contract;
it cannot nominate a same-named authority from another source. Configuration
allows at most one aggregate authority for a session. If that authority is
absent, ambiguous, incompatible, or cannot prove the host's effective plugin
set, migrated producers retain their direct path.

The runtime reads that plugin-owned configuration only after exact persisted
repository-trust proof. Its version-1 YAML shape is closed: unknown or duplicate
keys, malformed indentation, unsupported values, path escape, and incompatible
schema or engine versions all restore producer-local direct behavior. Host
settings carry enablement and marketplace discovery only; they cannot select
or override the authority.

This is explicit cross-cell interoperability, not implicit marketplace
federation. The aggregator may compose contributors from several active cells
only because user-local policy selected that exact aggregator as the authority
and each contributor consented to that authority.

### 2. Payload-owned contributor declarations

A context-producing plugin publishes a payload-relative manifest, referenced
from a proprietary manifest field such as:

```json
{
  "sessionContext": "session-context.json"
}
```

The referenced file uses a versioned schema:

```json
{
  "schema": "copilot-extensions.session-context-contributors",
  "version": 1,
  "contributors": [
    {
      "id": "command-catalog",
      "order": 200,
      "priority": "normal",
      "timeoutSeconds": 10,
      "maxBytes": 2048,
      "powershell": ["scripts/emit-command-catalog.ps1"],
      "bash": ["scripts/emit-command-catalog.sh"]
    }
  ]
}
```

Exact names and fields remain design decisions. The binding requirements are:

- paths are relative to and contained by the declaring payload;
- ids are unique within the source-qualified plugin;
- contributors are same-cell-only by default and explicitly name any accepted
  cross-cell aggregator identities;
- platform commands are argv arrays, not shell text;
- timeouts and byte limits are bounded by aggregator-wide maxima;
- scripts receive the original hook input unchanged;
- scripts emit exactly `{}` or `{"additionalContext":"..."}`;
- stdout is protocol-only and stderr is diagnostic-only; and
- scripts are read-only, side-effect-free, re-entrant context producers; mixed
  hooks must split side effects before migration; and
- repository data cannot replace payload-owned commands.

Keeping the existing producer output protocol minimizes migration, but every
producer still requires an environment review. The aggregator must construct a
fresh child environment with `COPILOT_PLUGIN_ROOT`, compatibility root aliases,
and validated installation context set for the contributor payload. It must
strip the aggregator's own payload/data identity rather than let a child emit
paths into the wrong plugin.

### 3. Deterministic composition

The aggregator:

1. resolves the exact active payload set;
2. loads and validates contributor manifests without executing plugin code;
3. sorts by order, source-qualified plugin identity, then contributor id;
4. invokes each contributor independently with bounded input, time, stdout, and
   stderr;
5. accepts valid nonblank context fragments;
6. enforces per-contributor and aggregate UTF-8 budgets;
7. joins fragments with a stable separator; and
8. emits one final JSON object.

The contributor protocol is a durable suite surface, not merely a temporary
hook workaround. Plugins continue to define their own rules, guidance kernels,
and command glossaries. The coordinator owns composition concerns that would
otherwise be duplicated in every producer: active-stack resolution, ordering,
source attribution, admission, diagnostics, and compact rendering. Structured
contributors such as command catalogs may declare a known format so the
coordinator can merge repeated headings and envelopes losslessly while
preserving each exact command `argv`.

Every fragment receives an aggregator-authored, source-qualified owner marker.
Owner-marker-shaped lines inside contributor text are rejected or neutralized
so one contributor cannot forge another contributor's policy boundary.
Duplicate contributor ids, malformed output, and timeouts become bounded
diagnostics; they do not erase valid sibling output.

The payload also provides a bounded status/doctor surface. It reports authority,
activation disposition, admitted contributor identities, partial-migration
competitors, budgets, deadlines, and last-result metadata, but never prints the
context fragments themselves.

Budget policy is an admission contract, not best-effort truncation. `priority`
is distinct from display `order`. Aggregation activates only when all admitted
contributors' declared maxima plus separators, owner markers, and reserved
headroom fit the configured aggregate share. If the set cannot fit, the broker
declines aggregation and producer wrappers use their direct paths. Once admitted,
a successful contributor is never omitted merely because a lower-priority
fragment consumed the budget first.

### 4. Authority-owned rendezvous

The hook payload supplies `sessionId`. The aggregator hook and every migrated
producer hook call one payload-local broker owned by the selected aggregator.
The broker computes or reads one pair-key result. The authority and every
proven producer return its byte-identical aggregate JSON after the result is
published or read.

```text
producer wrapper
  -> resolve the configured source-qualified aggregate authority
  -> invoke that exact payload's broker with the original hook input
  -> before proof, invoke this plugin's original producer
  -> after proof, compute or read the shared result and emit it

authority hook
  -> invoke the same broker without a producer identity
  -> compute or read the shared result
  -> emit the cached aggregate
```

The producer wrapper contains only the dependency-light authority locator and
fallback path. It does not carry a second implementation of active-set
resolution, contributor admission, budgets, or compatibility decisions.

The broker uses an ownership-checked rendezvous keyed by
`(sessionId, canonical resolved cwd)`. Concurrent callers may elect a worker
and cache the result, but every caller must read the same completed bytes.
Producer callers remain empty after proof; only the authority returns aggregate
bytes. Post-proof failures publish one shared cached `{}` and do not re-enter a
caller-specific direct fallback.

The rendezvous root is per-user. POSIX roots are current-user-owned `0700`
directories, lock and result files are `0600`, and unsafe or symlinked paths
stand down rather than exposing or accepting shared state.

### 5. Authoring and review enforcement

`customizing-copilot:authoring-skills` must define the host workaround
explicitly: while affected host versions retain only one session-start
`additionalContext` result, the exact direct
`context-injection@copilot-extensions` authority is the sole aggregate emitter.
A migrated producer declares its pure context contributor and retains its
original direct hook as the pre-proof backup path. After proof it joins the
selected coordinator's rendezvous and emits `{}`. Hooks that only reconcile
state, register providers, or perform other side effects remain independent,
restart-safe-idempotent, complete-declared, and return `{}`.
When a pure contributor consumes context computed by one of those hooks, the
direct hook atomically publishes an explicit completion snapshot. The
contributor may wait for that snapshot within its bounded deadline but never
replays the side effect.

`customizing-copilot:reviewing-customizations` must enforce this mechanically
without executing hooks. It reads the versioned contributor declaration and
the configured plugin stack, classifies session-start entries as aggregator,
migrated wrapper, known legacy direct emitter, or unknown, and reports a
blocking finding unless exactly one compatible authority is selected and every
consumed session-start context producer is complete and authority-aware.
Side-effect-only hooks must be explicitly complete-declared with no
contributors. An unknown output remains a warning rather than being assumed
safe. The scanner reports identities and roles only, never hook commands or
emitted context.

This creates version-skew states:

| Aggregator | Producer | Producer-hook behavior | Aggregator behavior |
|------------|----------|------------------------|---------------------|
| absent | old | emits directly | none |
| present | old | emits directly | ignores undeclared producer |
| absent | migrated | broker lookup fails; emits directly | none |
| incompatible | migrated | broker returns fallback; emits directly | stands down |
| compatible | migrated | rendezvous; emits `{}` | emits the shared aggregate |
| ambiguous | migrated | broker returns fallback; emits directly | all candidates stand down |

Aggregator-first and producer-first execution do not create a context-loss
state because the aggregator independently discovers every contributor and all
proven producers are empty. Full deterministic delivery begins only when every
competing context producer in the loaded set is migrated or the host bug is
fixed. A remaining legacy or repository-owned producer keeps authority proof
from succeeding.

The wrapper must not search PATH or enumerate wildcard same-named payloads. It
resolves the configured source-qualified authority and validates its exact
payload before invoking the broker.

### 6. Host-set authority and trust gates

Settings-derived reconstruction is not always the host's effective plugin set.
ACP dispatch uses explicit staged `--plugin-dir` payloads, and
repository-scoped settings are folder-trust-gated.

Aggregation therefore selects its inventory by launch mode. For a staged ACP
launch it reads the repeated `--plugin-dir` values from the raw Copilot process
ancestry without shell evaluation, validates every root and manifest, and uses
only staged roots whose manifest names resolve to exactly one effective
source-qualified repository identity. For a non-staged launch it uses the
ordinary settings and installed-payload resolver. V1 must:

- ignore repository-scoped enablement unless persisted folder trust is proven;
- reject malformed, missing, ambiguous, duplicate, escaping, or conflicting
  staged roots before authority proof;
- require the configured authority payload itself in the staged inventory;
- never invoke an installed plugin merely because settings name it when the host
  did not load it; and
- send every migrated producer through direct fallback when the authority check
  is indeterminate.

This is intentionally conservative. A false negative restores today's direct
behavior; a false positive can execute a plugin the host refused to load.

### 7. Aggregate deadline and failure behavior

The broker owns a hard aggregate wall-clock deadline below the shortest
registered wrapper timeout. Admission considers declared worst-case cost and
uses bounded parallelism where platform parity permits it. Safety and command
discovery classes run before ordinary guidance.

All callers rendezvous on and emit the same completed result. A contributor
that crashes, times out, or
emits malformed output is represented by one shared cached failure result. The
broker itself must recover stale computation ownership; one crashed caller
cannot send later producers back through caller-specific direct fallback.

The aggregate budget is a configured share below the host's full context cap,
leaving headroom for repository instructions, nonmigrated hooks, and future
native host aggregation.

### 8. Shadow mode before emission

The aggregator initially supports audit-only execution:

- resolve active contributors;
- validate manifests;
- invoke them;
- calculate ordering and budgets;
- report counts and attributable diagnostics; and
- emit `{}`.

This permits parity comparison with existing direct hooks without introducing a
new non-empty competitor. Contributor commands must be pure and re-entrant
because shadow mode may execute them alongside their direct hook.

### 9. A-la-carte reconciliation

The aggregator is optional composition, not a prerequisite for a plugin's core
reachability. A single-plugin install continues to emit its context directly.
When the exact aggregator authority is installed and selected, the producer uses
the aggregator's declared broker surface; it does not inspect sibling internals.
Missing or uncertain aggregation degrades only deterministic composition and
restores the standalone path.

This extends the provider-manifest composition model to session context without
creating a mandatory service or runtime dependency. The a-la-carte and
marketplace-installation-cell patterns must be updated as part of the first
operative implementation.

## Boundaries and open questions

### Repository-owned hooks

A plugin-only aggregator does not stop an unrelated repository
`sessionStart` hook from racing with the aggregate under the current host bug.
The first implementation should not silently execute arbitrary repository code.
The effort must choose one of:

- explicitly scope the workaround to plugin-owned context and retain static
  repository fallbacks until the host fix;
- allow trusted repositories to contribute bounded static context data; or
- define a separate trusted repository contributor contract with the same
  containment and folder-trust semantics as repository hooks.

Dynamic repository scripts should not enter v1 unless their trust and execution
model is at least as explicit as the host's own hook contract.

### Staged payload visibility

The host stages plugins through repeated `--plugin-dir` arguments on the
Copilot ACP process. The aggregator reads that complete raw argv list from
process ancestry, never by evaluating the ancestor shell command or scanning
temporary directories. It canonicalizes and deduplicates existing roots,
requires each plugin manifest to remain contained by its root, and
source-qualifies manifest names against the repository's effective
`enabledPlugins` identities.

When that staged inventory exists, it is the host-loaded set. Disabled staged
payloads are excluded, settings-enabled but unstaged payloads are not invoked,
and the configured authority must be present among the staged roots. Any
malformed argument, missing value, path failure, duplicate identity, or source
ambiguity stands down before authority proof and preserves producer-local
direct emission. Launches with no staged arguments retain ordinary
settings/installed-payload resolution.

### Aggregator absence and consumer setup

Because dependencies are not transitively installed, a plugin cannot require
the aggregator merely by adding a manifest field. The adoption surface needs to
enable both plugins explicitly, and the brokered producer wrapper remains the
runtime safety net.

### Native host fix

The upstream host should still preserve every successful context output. Once
that lands, one aggregate remains valid and may provide useful deterministic
ordering, ownership, and budget enforcement. If those benefits do not justify
the layer, the same brokered fallback and ownership receipts must support a
safe return to direct host aggregation.

## Recommended first proof

Use two contributors with different delivery shapes:

1. `agent-logger` command catalog - runtime-bearing, multi-command, and the
   concrete failure that motivated the fallback requirement.
2. `ai-attribution` policy kernel - payload-only, cwd-gated, and already
   dependency-free on both platforms.

The proof succeeds when authority-first, producer-first, and concurrent calls
all produce exactly one non-empty hook result with byte-identical authority
bytes containing both fragments, every child sees its own payload identity, and
every version-skew or indeterminate-authority state preserves the standalone
direct path. The same session id with another canonical cwd and the same cwd
with another session id must not reuse a result.
