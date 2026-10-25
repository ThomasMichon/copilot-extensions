# Pattern: Version-Skew-Safe Service and CLI Contracts

**Serves:** *Vision plugin-services*
[version-skew-tolerant-contracts](../../visions/plugin-services/README.md#version-skew-tolerant-contracts)
and [interoperate-across-version-skew](../../visions/plugin-services/README.md#interoperate-across-version-skew).
**Existing foundations:** Worktrees versioned IPC, Dispatch control capabilities,
and Bridge's contract-evolution work. These are per-boundary foundations, not
evidence that every internal interface already implements this pattern.

This is the design and review contract for new or changed independently deployed
interfaces. It does not assert that runtime contract metadata or negotiation is
already implemented throughout the suite. Existing boundary adoption remains
tracked by [#5957](https://github.com/ThomasMichon/copilot-extensions/issues/5957);
Bridge's envelope evolution has its own
[#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460) owner.

## A command line is a service contract

Independently released peers, a CLI and its daemon, and a new payload with an
older selected runtime can legitimately differ in version. Updating everything
together reduces skew; it is not a correctness prerequisite within a declared
support window.

The contract includes commands, arguments, defaults, exit codes, stdout/stderr,
JSON requests and responses, and their effects and retry semantics. Package
versions identify builds; contract identities and versions identify interoperable
behavior. Do not infer one from the other, or require identical package versions.

**Keep CLI parsing strict.** Unknown commands, options and invalid values should
fail with useful syntax guidance, especially for agents. Tolerant additive JSON
readers are not permission to silently discard misspelled CLI arguments, change
existing field meanings, or ignore a required capability.

## First ask whether the interface needs to change

Prefer implementation inside the operation that already owns the behavior.
For example, an existing launch operation can read an optional repository-config
flag internally and enforce it at its normal admission/execution boundaries.
Do not add a prerequisite policy RPC, new verb or `--require-*` option merely to
make ordinary callers request behavior the owner must already enforce.

An optional JSON/config property is backward-compatible only when its documented
absence preserves the prior behavior and supported readers/validators permit the
addition. A strict older schema may make even an optional property incompatible.
An old reader ignoring a new field proves structural tolerance, not enforcement
of its meaning. An explicit prohibition, required permission or other mandatory
semantic must be enforced by a capable owner or refused before effects; never
report successful enforcement by a reader that ignores it.

Configuration-file migration has a separate owner:
[config-schema-migration](config-schema-migration.md). Wire compatibility does
not authorize rewriting repository config or relaxing on-disk schema validation.

## Declare support against the runtime that actually executes

When adding or changing a boundary, its owner must document:

- a stable contract identity and version/generation, separately from the package
  version;
- the supported peer contract range or explicit supported generations, plus
  capabilities when a version alone cannot describe optional behavior;
- additive-reader/default semantics and the deprecation/removal window;
- the compatibility selection path and truthful unsupported-contract outcome.

Publish machine-readable declarations with the owning immutable runtime slot,
reusing an existing suitable descriptor where possible. Their schema and lookup
are themselves stable, versioned contracts. Select metadata from the exact
installation and runtime chosen for execution, not a newer marketplace payload,
staged update, ambient command on `PATH`, or a mutable root-wide package stamp.
A resident daemon advertises the contract of its running generation through its
existing discovery/handshake seam; a newer installed slot is not evidence that
the old daemon supports it.

Use existing released handshakes where they govern, such as MCP negotiation.
Do not add a second universal handshake, daemon or per-call preflight subprocess.
Resolve immutable-slot metadata once for that selected generation where useful,
or extend the existing stable discovery/response contract. Re-resolve when the
selected runtime or live peer changes, and retain effect-side enforcement.
Compatibility metadata neither grants authority nor replaces live identity,
admission or lifecycle checks.

Missing metadata from a legacy peer means unknown support, not "latest" or
automatic denial of every previously supported operation. Use only a documented,
tested legacy baseline whose semantics suffice; otherwise name the unsupported
requirement and recovery path before effects. Introduce the metadata/discovery
mechanism itself provider-first, rather than assuming old peers understand a new
`capabilities` command.

## Roll out in phases: expand, migrate, contract

1. **Expand providers first.** Receivers accept the new form while retaining the
   old form and its meaning. Publish the new support declaration through a seam
   existing consumers can safely ignore. A new command/argument remains unused
   by ordinary consumers until receiver support can be established.
2. **Migrate consumers conditionally.** Select a mutually supported contract
   before emitting new syntax. Use the new form only for capable peers; use an
   explicitly equivalent old path inside its support window. If no safe path
   exists, explain the actual contract gap and supported recovery. Do not make
   the entire tool unavailable for a missing optional feature.
3. **Contract deliberately.** Remove an old form only after its declared
   deprecation window and supported consumer-upgrade **and provider-rollback**
   window have closed. Removal or changed meaning is a breaking contract
   revision, even if the package release is only a patch. Follow the repository's
   separate changefile/release policy; do not hand-bump package versions.

Provider-first describes compatibility order, not a promise of synchronized
deployment. A single source PR or coordinated release does not prove every live
provider upgraded. Document which release establishes receiver support, which
enables callers, and when retirement is permitted. Older callers must continue
to reach upgraded providers; upgraded callers must handle supported provider
rollback. Add a bridge/adaptor phase when those guarantees cannot coexist in one
release.

Never try an effectful new invocation, catch an unknown-argument error, and
blindly replay an older form. Failure can occur after allocation or acceptance;
a parser-looking error is not proof of no effects across wrappers and remote
hops. Preserve existing idempotency, custody and unknown-outcome semantics.
Keep negative policy and safety requirements intact on every compatible path.

## Validation and review

For a changed boundary, exercise the real parser/process or transport seam with
focused current/prior fixtures, not just mocks that assert new flags were sent:

| Combination or control | Required assertion |
|------------------------|--------------------|
| New caller / supported old provider | Equivalent baseline still works; unsupported optional features are explicit |
| Old caller / new provider | Old syntax, defaults, output and effects retain their meaning |
| New caller / new provider | Capability selection uses the intended new form |
| Supported rollback | A provider rollback retains the documented path or gives the declared pre-effect refusal |
| Missing, invalid or out-of-range declaration | No guessed support, accidental side effects or blanket legacy rejection |
| Unknown CLI argument / additive JSON property | Typo is rejected; only intentionally extensible fields tolerate additions |
| Failure response | Actual contract/version gap and meaningful stdout/stderr survive wrappers |

Scope tests to the affected boundary and supported window. This pattern does not
add an exhaustive all-service/native matrix to required PR CI; follow
[TESTING.md](../../TESTING.md)'s portfolio rules.

The contributor's **Contract impact** statement names the boundary, old/new
semantics, active-runtime discovery, phased rollout/retirement and supported
upgrade/rollback coverage. If the implementation can stay inside an existing
operation, explain why any proposed new cross-process surface is necessary.
Reviewers flag unnegotiated new syntax, lockstep-version assumptions, permissive
CLI fallbacks, silent policy weakening and missing mixed-version coverage.
Documentation-only adoption of this pattern is not runtime implementation or
proof that existing service contracts have been upgraded.
