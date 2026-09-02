# context-injection

Compatibility aggregator for Copilot CLI hosts that retain only one
`sessionStart` `additionalContext` result.

The engine resolves the effective user plus trusted-repository plugin stack and
emits an aggregate only when the repository explicitly adopts the exact
source-qualified marketplace authority and its compatible engine. Every active
session-start plugin must be complete-declared. The engine runs only pure,
payload-declared context contributors and applies intentional 64 KB and
20-second admission budgets.

For an ACP launch with explicit `--plugin-dir` arguments, the engine extracts
the complete repeated argument list from raw process ancestry without
evaluating a shell command. It canonicalizes and deduplicates existing payload
roots, loads each contained manifest, source-qualifies the manifest name
against the effective repository `enabledPlugins` map, and uses the resulting
enabled staged roots as the host-loaded plugin set. Settings-enabled but
unstaged payloads are not invoked. The configured authority must itself be
staged. Malformed arguments, path or identity conflicts, ambiguous sources,
duplicate identities, and uncertain ancestry all fail before authority proof,
so producer callers retain their local direct fallback. Non-staged launches
continue to use ordinary settings and installed-payload resolution.

Repository settings only enable the plugin. The trusted repository selects the
direct marketplace authority through `.context-injection/config.yaml`:

```yaml
schema: copilot-extensions.context-injection
version: 1
authority: context-injection@copilot-extensions
engine:
  schema: copilot-extensions.context-injection-engine
  version: 5
```

The engine reads this file only after exact persisted folder-trust proof. The
v1 parser is intentionally constrained: unknown keys, duplicate keys, malformed
indentation, unsupported YAML shapes, path escape, and incompatible values all
stand down before authority proof. The retired `sessionContextAggregation`
settings key is ignored and cannot select an authority.

Engine contract version 5 is the current compatibility boundary: repository
adoption proof, producer-mode direct fallback, pair-key rendezvous identity,
byte-identical shared delivery, and CWD-isolated spill targets.
Producer hooks may invoke
`aggregate_context.py --producer <plugin@marketplace>/<contributor-id>`. A
missing, malformed, ambiguous, inactive, or incompatible authority restores
that contributor's direct output. After exact authority proof, producers
participate in or wait for the shared rendezvous and emit the same cached
aggregate as the authority's own session-start invocation. It may run before,
after, or concurrently with producers: order does not affect the selected
result because every participating hook returns byte-identical bytes.
Post-proof failures publish one shared cached `{}` and never re-enter
producer-local fallback.
Completed results are cached by
`(sessionId, canonical resolved cwd)`, so only the exact pair can reuse bytes.
The cache lives in a per-user runtime or cache directory. POSIX roots must be
owned by the current user with mode `0700`; lock and result files use `0600`,
and unsafe or symlinked paths are rejected.

This order-independent protocol does not depend on how the host selects among
hook results: every authority-aware producer and the authority return the same
non-empty aggregate after proof.

Aggregates above 8 KB are atomically written to
`~/.copilot/session-state/<sessionId>/files/startup-context-<cwd-digest>.md`.
The complete spill remains bounded to 128 KiB so broad composed marketplaces
can participate without turning the session-state file into an unbounded sink.
The 96-bit SHA-256 prefix keys the spill to the same canonical CWD used by the
rendezvous without exposing that path in the filename or exceeding common
Windows path limits. Every hook then returns the same compact critical kernel:
an explicit `delivery=spill` marker with the absolute spill path, exact command
catalogs when they fit, complete
highest-priority owner fragments selected by declared order, and a bounded
exact-excerpt index for deferred contributors. If the complete index cannot fit,
the kernel retains a count and roster digest while the spill remains the full
attribution source. This keeps repeated hook output below host-wide context
limits, preserves safe first-turn decisions without a file read, and leaves the
complete attributable aggregate available on demand.

Set the host-level `timeoutSec` on producer and authority hooks to at least the
engine's 25-second rendezvous deadline. A value of 30 seconds is recommended so
the wrapper can still emit fallback or rendezvous output after engine overhead.
This is separate from each pure contributor's `timeoutSeconds` limit.

Every contributing plugin carries byte-identical Bash and PowerShell producer
wrappers. They use the exact payload-relative contributor id and command,
preserve stdin as UTF-8, resolve the exact source-qualified adopted authority,
and otherwise run the local pure contributor directly. Engine and direct
contributor output is buffered and accepted only when the child exits zero with
exactly one JSON object; every other result becomes one `{}` with hook exit zero.
If a non-reading child closes stdin early, a broken-pipe write does not override
its successful exit and valid buffered output.
PowerShell uses `ArgumentList` where available and the Windows command-line
quoting algorithm on Windows PowerShell 5.1 so empty, quoted, and
backslash-terminated arguments survive unchanged. The wrapper requires Python
for authority resolution and full JSON validation. Without Python, the POSIX
compatibility path buffers a successful local contributor and accepts only one
structurally bounded object result; every other result becomes `{}`.

## Complete suite-owned stack

The initial marketplace inventory contained 16 plugins with 43 `sessionStart`
hooks and 21 pure context contributors. Splitting agent-worktrees' two mixed
context-and-mutation hooks into one pure contributor reduced the migrated
direct-hook inventory to 42:

- four context-only plugins declare `sideEffects: none` and
  `context: authority-aware`;
- eleven mixed plugins declare
  `sideEffects: restart-safe-idempotent` and `context: authority-aware`, while
  keeping their idempotent side effects as direct `{}`-emitting hooks; and
- `context-injection` declares `sideEffects: none` and
  `context: aggregate-authority`.

All 15 producers use the engine-v5 wrapper with a 30-second host timeout. The
aggregator never invokes direct side-effect hooks. Repository guards derive the
marketplace-owned inventory and reject incomplete declarations, legacy direct
context emitters, wrapper drift, contributor identity mismatches, insufficient
host timeouts, and mixed hooks whose side effects were not separated.
When pure context depends on a direct side effect's result, that hook atomically
publishes an explicit completion snapshot. The pure contributor waits only for
that bounded snapshot and never replays the side effect.

## Clean-room completeness witness

`tools/clean-room/scenarios/context-injection-eval/` validates the distinction
between broker correctness and model-visible delivery. It creates a disposable
local marketplace containing this unpublished payload, two variant-selected
synthetic authority-aware producers with high-entropy canaries, and one
`restart-safe-idempotent` / `context: none` side-effect-only plugin. The
manifest explicitly stages those four payload roots through
`eval.acp_plugin_dirs`; repository `enabledPlugins` still source-qualifies the
applicable producer identities. `payload_fingerprint_dirs` remains a separate
evidence surface and does not activate plugins.

The setup leg runs deterministic authority-first, producer-first, concurrent,
session-identity, and CWD-isolation permutations before any model turn. Tier E
then drives fresh Copilot sessions through agent-bridge under literal mode. The
agent must return one strict JSON object containing every injected canary
exactly once and may not read files or run diagnostics to reconstruct a missing
token. Run variant A with two fresh sessions and variant B with one:

```powershell
$env:CR_CONTEXT_VARIANT = 'A'
.\tools\clean-room\run.ps1 -Scenario context-injection-eval -Mode eval `
  -HarnessMount <source-checkout> -PassEnv CR_CONTEXT_VARIANT -Runs 2

$env:CR_CONTEXT_VARIANT = 'B'
.\tools\clean-room\run.ps1 -Scenario context-injection-eval -Mode eval `
  -HarnessMount <source-checkout> -PassEnv CR_CONTEXT_VARIANT -Runs 1
```

The results directory receives counts-only evidence: expected contributor
count, observed token hashes and occurrence counts, session/CWD identity hashes,
side-effect marker count, and one PASS/FAIL result. Raw ambient context is never
copied into that evidence. A CLI/runtime that cannot load the unpublished local
marketplace payloads through the explicit staged bridge/ACP path is a
`scenario-transport-gap`; manual payload copying does not count as completeness.

### Latest observed result

On Copilot CLI 1.0.82, the staged deterministic Tier-P matrix passed
authority-first, producer-first, concurrent, two-session, and two-CWD
permutations. Each authority result contained both expected contributor token
hashes exactly once.

The corrected agent-bridge Tier-E packets also passed independent clean-room
judgment: two fresh variant-A sessions and one fresh variant-B session each
returned both expected canaries exactly once in the strict response, used no
tools, invented no canaries, and left exactly one idempotent side-effect marker.
The tracked repository contains counts and hashes only, never raw canaries.

The earlier three-session `FAIL / scenario-transport-gap` is superseded as an
**invalid scenario**, not a product or agent-bridge transport verdict. That run
omitted the `eval.acp_plugin_dirs` activation arguments required by ACP and
copilot-extensions plugin hooks. Its machine-local evidence remains preserved
historically, but it must not be cited as a context-delivery defect.
