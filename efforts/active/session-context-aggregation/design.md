# Session Context Aggregation - Investigation and Proposed Design

[Effort](README.md) ·
[Issue #1325](https://github.com/ThomasMichon/copilot-extensions/issues/1325)

## Findings

### The failure is at the host aggregation boundary

The host runs all configured `sessionStart` hooks, but only one non-empty
`additionalContext` output reaches the model. Each producer can be correct in
isolation and still disappear. Adding retries, changing hook order, or making
individual producers faster cannot create a correctness guarantee.

The workaround therefore needs one suite-owned broker to produce one combined
value. The aggregator plugin has one host hook, while migrated producer hooks
call the same broker and return the same aggregate during the compatibility
period. The broker should not absorb bootstrap, registration, service
reconciliation, or other side effects that happen to run at `sessionStart`;
those hooks can continue to run directly and return `{}`.

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

The selected aggregator is source-qualified by user-local policy. Repository
configuration cannot select or replace it. Configuration allows at most one
authority for a session. If the authority is absent, ambiguous, incompatible,
or cannot prove the host's effective plugin set, migrated producers retain their
direct path.

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

### 4. One brokered result per session

The hook payload supplies `sessionId`. The aggregator hook and every migrated
producer hook call one payload-local broker owned by the selected aggregator.
The broker computes or reads one session-local result and returns byte-identical
aggregate JSON to every caller. The affected host may preserve any one of these
identical non-empty results without losing a migrated sibling.

```text
producer wrapper
  -> resolve configured source-qualified aggregator authority
  -> invoke that exact payload's broker with original hook input
  -> broker returns shared aggregate, or an explicit direct-fallback disposition
  -> on direct fallback, invoke this plugin's original producer
```

The producer wrapper contains only the dependency-light authority locator and
fallback path. It does not carry a second implementation of active-set
resolution, contributor admission, budgets, or compatibility decisions.

The broker uses a session-local, ownership-checked rendezvous or deterministic
recomputation. Concurrent callers may elect a worker and cache the result, but
every caller must be able to recover from a stale owner and return the same
completed aggregate. Returning the same aggregate from several hooks is safe;
returning different partial aggregates is not.

### 4a. Authoring and review enforcement

`customizing-copilot:authoring-skills` must define the host workaround
explicitly: while affected host versions retain only one session-start
`additionalContext` result, `context-injection` is the only active-stack hook
that may emit a direct aggregate. A migrated producer declares its context
contributor and calls the selected coordinator's broker; it invokes its
original direct producer only when that broker returns the explicit fallback
disposition. Hooks that only reconcile state, register providers, or perform
other side effects remain independent and return `{}`.

`customizing-copilot:reviewing-customizations` must enforce this mechanically
without executing hooks. It reads the versioned contributor declaration and
the configured plugin stack, classifies session-start entries as aggregator,
migrated wrapper, known legacy direct emitter, or unknown, and reports a
blocking finding when more than one possible non-empty result is not proven to
be the byte-identical output of one compatible broker. Thus a legacy direct
emitter alongside a non-empty aggregate remains blocking during partial
migration; an aggregator and its migrated wrappers are permitted only because
they return the same brokered aggregate. An unknown output remains a warning
rather than being assumed safe. The scanner reports identities and roles only,
never hook commands or emitted context.

This creates version-skew states:

| Aggregator | Producer | Producer-hook behavior | Aggregator behavior |
|------------|----------|------------------------|---------------------|
| absent | old | emits directly | none |
| present | old | emits directly | ignores undeclared producer |
| absent | migrated | broker lookup fails; emits directly | none |
| incompatible | migrated | broker returns fallback; emits directly | stands down |
| compatible | migrated | returns shared aggregate | returns the same aggregate |
| ambiguous | migrated | broker returns fallback; emits directly | all candidates stand down |

Aggregator-first deployment and producer-first deployment do not create a new
context-loss state. Full deterministic delivery begins only when every
competing context producer in the loaded set is migrated or the host bug is
fixed. A remaining legacy or repository-owned producer can still win the host
race and hide the aggregate; diagnostics must label this partial state degraded.

The wrapper must not search PATH or enumerate wildcard same-named payloads. It
resolves the configured source-qualified authority and validates its exact
payload before invoking the broker.

### 5. Host-set authority and trust gates

Settings-derived reconstruction is not always the host's effective plugin set.
ACP dispatch may use staged `--plugin-dir` payloads while ignoring
`enabledPlugins`, and repository-scoped settings are folder-trust-gated.

Aggregation therefore activates only when the launch source and available host
inputs prove that settings-derived resolution is authoritative. V1 must:

- ignore repository-scoped enablement unless persisted folder trust is proven;
- stand down for ACP or staged-plugin launches unless the host supplies a
  complete attributable staged-payload inventory;
- never invoke an installed plugin merely because settings name it when the host
  did not load it; and
- send every migrated producer through direct fallback when the authority check
  is indeterminate.

This is intentionally conservative. A false negative restores today's direct
behavior; a false positive can execute a plugin the host refused to load.

### 6. Aggregate deadline and failure behavior

The broker owns a hard aggregate wall-clock deadline below the shortest
registered wrapper timeout. Admission considers declared worst-case cost and
uses bounded parallelism where platform parity permits it. Safety and command
discovery classes run before ordinary guidance.

All callers return the same completed aggregate. A contributor that crashes,
times out, or emits malformed output is represented by a bounded omission notice
in that aggregate. The broker itself must recover stale computation ownership;
one crashed caller cannot leave every sibling returning `{}`.

The aggregate budget is a configured share below the host's full context cap,
leaving headroom for repository instructions, nonmigrated hooks, and future
native host aggregation.

### 7. Shadow mode before emission

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

### 8. A-la-carte reconciliation

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

The host may stage plugins through `--plugin-dir` without recording them in
ordinary user or repository settings. The aggregator needs a trustworthy input
or environment seam for those roots. It must not rediscover them by scanning
temporary directories.

If the host exposes no complete staged-plugin inventory, v1 must stand down and
ensure staged contributors retain direct emission. It must also avoid invoking
installed settings-declared plugins that the staged launch did not load.

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

The proof succeeds when the aggregator hook and both migrated producer hooks
return byte-identical aggregates containing both fragments, every child sees its
own payload identity, and every version-skew or indeterminate-authority state
preserves the standalone direct path. The proof must explicitly report that an
unmigrated competing hook can still hide the aggregate under the affected host.
