# Pattern: drop-in registry hygiene

**Serves:** *Vision plugin-services*
§Features/`self-auditing-drop-in-composition`;
§Behaviors/`stale-drop-ins-are-inert-and-legible`, `degrade-gracefully`;
§Features/`graceful-composition`.

**Exemplars and adopters:** agent-bridge `providers.d`, agent-codespaces
`config.d`, agent-worktrees Picker pivots and machine `config.d`,
agent-dispatch `registrar.d`, and agent-ssh managed `config.d` fragments.

## Problem

Filesystem drop-in registries are the right cross-plugin seam: each contributor
owns one file, consumers avoid cross-venv imports, and no writer races a shared
aggregate document. They also accumulate history. A plugin may be disabled,
uninstalled, moved to a new payload root, or removed from every registered repo
without its last `sessionStart` or uninstall hook ever running. The entry can
remain while its target disappears—or worse, while an old target still exists
but is no longer authorized.

Two bad responses must both be avoided:

1. **Fragile sweep:** one stale entry raises and prevents every valid
   contribution from loading.
2. **Invisible cruft:** the consumer silently skips or indefinitely retains the
   entry, so operators cannot tell what is stale or how to remove it.

Routine discovery is an availability path. Cleanup is a hygiene path. The
standard keeps those responsibilities separate but connected.

## Standard approach

### 1. One entry is one fault boundary

Enumerate accepted files deterministically and parse each entry independently.
A definitively malformed document, unsupported schema, invalid field, missing
target, path escape, unavailable command, duplicate identity, or authorization
failure yields an **inactive finding for that entry**. A transient read failure
is `indeterminate`, as defined below. Continue processing every other file.
Directory enumeration errors degrade to an empty contribution set only when the
directory is authoritatively absent/unconfigured; they never surface a partial
exception into service startup, config loading, or the Picker.

Do not use one broad `try/except` around the entire sweep that makes the failure
silent. The consumer should retain a typed finding even when its operational API
returns only valid contributions.

The scan result is explicitly three-state:

| State | Meaning | Reconciliation |
|---|---|---|
| `complete` | Directory enumeration and all per-entry classifications completed. | Reconcile to this current desired set. |
| `absent` | Registry directory is confirmed not configured/present. | Authoritative empty desired set. |
| `indeterminate` | Directory cannot be enumerated completely (permissions, transient I/O, partial iteration). | Retain the last-known-good desired set, emit one bounded registry finding, and retry. |

An entry-level transient read failure may likewise be `indeterminate`: retain
that entry's last-known contribution if one exists, but never activate a new
one from unreadable data. Definitive facts—missing target, disabled contributor,
identity mismatch—remain inactive. Tests distinguish uncertainty from confirmed
absence so resilience cannot accidentally become stale authority.

### 2. Presence is a candidate, not authorization

A plugin-written entry carries enough provenance to identify its canonical
`name@marketplace` source and current plugin root. The consumer activates it only
while that source is effectively enabled in the applicable scope and the target
resolves through a current, identity-matching root. Where the consumer is
machine-wide, "applicable" may be the union of user-global enablement and every
registered project repo; where it is project-scoped, use that project's
effective settings.

Operator-authored drop-ins may be intentionally unattributed. They still receive
structural and target validation, but doctor labels them `unattributed` and never
auto-removes them. A plugin-written format should be versioned and attributed;
do not add a new anonymous pointer format.

Each registry defines explicit entry classes rather than guessing from missing
metadata:

- **managed plugin** — current attributed schema;
- **operator** — permanent user-owned format/location, always report-only;
- **legacy plugin** — matched only by a registry-specific adapter for known old
  producer filenames/shape/target roots; and
- **unknown legacy** — active only when the registry's compatibility policy says
  so, always report-only.

The result model separates activation from findings. An entry can be
`active-with-advisory` (for example, a known safe legacy plugin pointer that
still needs migration), `inactive`, or `indeterminate`; findings do not imply
inactivity by themselves. Before a legacy cutoff, producer adapters preserve
known plugin entries and emit `legacy-unattributed`. Producers are updated to
rewrite them. After the cutoff, only those known plugin classes lose plugin
authority; permanent operator entries remain unaffected and may be explicitly
adopted into a managed format through a consumer command.

Relative targets are resolved under an identity-verified owner root and proven
contained after canonicalization. Absolute executable targets are accepted only
when the contract genuinely owns a binstub outside the payload; validate that the
command exists and is runnable, and bind it to the attributed plugin source.

### 3. Reconcile a desired set; never only add

Every sweep computes the valid desired set **now** and reconciles live state to
it. If an entry disappears, becomes malformed, loses authorization, or points to
a missing target, the consumer withdraws the state that entry created. A daemon
must not keep a resolver forever merely because an earlier additive scan saw it;
a Picker must not restore a contribution merely because an installed-but-disabled
payload still exists.

When withdrawal needs graceful drain, the consumer may delay the physical stop,
but the stale contribution is no longer eligible for new work.

### 4. Warn without flooding

Operational discovery emits an actionable warning for every newly observed
inactive reason:

```text
[WARN] providers.d/agent-codespaces.json: missing-target
       C:\Users\me\.local\bin\agent-codespaces.cmd
       Run `agent-bridge doctor` for cleanup guidance.
```

Warnings identify:

- consumer registry and entry path;
- stable reason code;
- referenced target or conflicting identity when applicable; and
- the owning doctor command.

Operational output has an aggregate cap (default: **10 detailed findings per
registry per sweep/time window**) followed by a deterministic summary such as
`17 additional findings suppressed; run <consumer> doctor`. Doctor human/JSON
output remains exhaustive. Long-running daemons and frequently refreshed UI
surfaces additionally deduplicate by `(entry, reason, target fingerprint)` and
rate-limit repeats. A changed reason or target is a new finding and may warn
immediately within the aggregate cap. Recovery may emit one concise "active
again" notice.

Stable reason codes include at least:

| Code | Meaning |
|---|---|
| `invalid-entry` | definitively malformed or schema-invalid file |
| `entry-indeterminate` | entry could not be read/classified completely because of transient I/O or permissions |
| `registry-indeterminate` | registry could not be enumerated completely; last-known desired state is retained |
| `missing-target` | referenced file or command no longer exists |
| `target-unusable` | target exists but cannot be read/executed |
| `not-enabled` | attributed plugin is no longer enabled in scope |
| `identity-mismatch` | plugin, marketplace, repo, or owner root does not match |
| `root-ambiguous` | one canonical source resolves to different current roots |
| `duplicate` | two active entries claim one exclusive identity |
| `legacy-unattributed` | compatibility entry lacks current provenance |

### 5. Doctor owns hygiene

Every consumer of a plugin-extensible `*.d` registry exposes its findings through
its own normal diagnostic surface—prefer `<consumer> doctor`; if a mature plugin
already has a namespaced doctor, integrate there rather than adding a competing
top-level command.

Doctor is report-only by default and emits both human and structured output. One
finding contains:

```json
{
  "registry": "providers.d",
  "entry": "/home/me/.agent-bridge/providers.d/agent-codespaces.json",
  "status": "inactive",
  "reason": "missing-target",
  "target": "/home/me/.local/bin/agent-codespaces",
  "owner": "agent-codespaces@copilot-extensions",
  "remedy": "Remove the stale entry or re-enable/reinstall the contributor."
}
```

The human remedy names the **exact entry** and, when known, the producer command
that recreates it. Recommend cleanup; do not make users guess which file is safe.
An optional `--fix` may remove only a file written/adopted through the
**consumer-owned registration API**, which records an opaque instance token in
both the entry and a consumer-owned receipt ledger. Self-declared provenance,
filename, and expected location are not ownership proof.

Immediately before deletion, doctor reopens/revalidates the exact entry:

- regular file, never a symlink/reparse point;
- instance token and managed receipt still match;
- file identity and content digest still match the scanned finding; and
- stale verdict is still definitive, not transient/indeterminate.

If any check changed, refuse and rescan. Never auto-delete an unattributed entry,
a direct-writer legacy entry, a target that merely failed transiently, or a
peer's file. Deletion is an exact unlink of the entry itself—not recursive
cleanup of the target it references. Registries that lack the consumer-owned
writer/receipt simply do not offer `--fix`; precise report-only guidance is
still compliant.

### 6. Migrate without a flag day

Legacy anonymous entries remain loadable for a bounded compatibility window when
doing so is safe, but they surface `legacy-unattributed` in doctor and warnings.
Each registry documents how it recognizes **known legacy producers** without
capturing operator entries, plus an explicit operator adoption/relocation path.
Update producer hooks first so fresh sessions rewrite entries into the attributed,
versioned format. After deployed producers have crossed the window, make
attribution required for the known plugin-owned class; do not preserve anonymous
plugin authority forever and do not disable permanent operator configuration.

### 7. Keep runtime and doctor semantics identical

The operational sweep and doctor use one scanner/classifier. Doctor must not
reimplement parsing or eligibility and reach a different verdict. The scanner
returns both valid contributions and findings; the runtime consumes the former,
doctor renders the latter. Tests assert parity between human/JSON doctor output
and the entries the operational path actually activates.

## Current adoption map

| Registry | Current strength | Delta to the pattern |
|---|---|---|
| `~/.agent-bridge/providers.d/*.json` | Malformed manifests warn and are skipped. | Validate command targets and contributor eligibility; replace additive resolver refresh with desired-set reconciliation; add `agent-bridge doctor`. |
| `~/.agent-codespaces/config.d/*` | Missing pointer targets are skipped; valid configs merge at lowest precedence. | Warn instead of silently skipping; adopt attributed/versioned pointers; include registry findings in `agent-codespaces doctor`. |
| `~/.agent-worktrees/pivots/*.json` | Attributed active-root materialization, command/root identity validation, tri-state reconciliation, bounded warnings, legacy/operator classification, and exhaustive report-only doctor are implemented. | Producer templates remain schema v1; the consumer publishes append-only schema-v2 runtime entries and retains known legacy compatibility during migration. |
| `~/.{project}/config.d/*` | Operator YAML and attributed managed pointers use per-entry validation, applicable-project activation, tri-state reconciliation, bounded warnings, and exhaustive report-only doctor. | No consumer-owned writer/receipt exists, so `doctor --fix` is intentionally unavailable. |
| `~/.agent-dispatch/registrar.d/*.json` | Designed as attributed, eligibility-gated candidates. | Make warnings bounded and add `registrar doctor`/consumer doctor findings as part of the first implementation. |
| `~/.ssh/config.d/50-agent-ssh-*.conf` | Agent-ssh atomically owns namespaced generated fragments. | Validate managed fragments against current transport/registry ownership and add doctor cleanup guidance; never touch unrelated OpenSSH drop-ins. |

## Required tests

Every adopting registry covers:

- one malformed entry beside one valid entry;
- absent versus unreadable/partially-enumerated registry, proving only absent is
  authoritative empty and indeterminate retains last-known state;
- missing and unusable targets;
- disabled/uninstalled attributed contributor;
- deletion/deactivation withdrawing previously-live state;
- duplicate and ambiguous identities isolated from unrelated entries;
- warning deduplication/rate limiting;
- more than the operational aggregate warning cap, with exhaustive doctor JSON;
- doctor human/JSON parity with runtime classification;
- report-only default and ownership-gated `--fix`;
- operator-created files that mimic managed names/provenance, and a
  scan-to-delete replacement race, neither of which may be removed;
- active legacy-plugin advisory versus permanent operator entry across cutoff;
- exact-path deletion that never follows or recursively removes the target; and
- Windows/POSIX canonicalization and executable checks.

## Anti-patterns

- **Silent skip.** Availability is preserved, but the operator gets no reason or
  cleanup path.
- **Fail the aggregate.** One contributor takes down every peer or the host
  service.
- **Unreadable means empty.** A transient registry failure mass-withdraws
  healthy contributions.
- **Add-only refresh.** A removed file remains live until restart.
- **Installed means enabled.** A disabled plugin's cached payload resurrects its
  contribution.
- **Cleanup on startup.** A transient target outage or legacy entry is deleted
  during the availability path.
- **Filename as ownership.** A matching name lets doctor delete operator content.
- **Doctor by reimplementation.** Diagnostics disagree with runtime behavior.
- **Recursive target deletion.** Cleaning a pointer removes the content it
  referenced rather than only the stale registry entry.

## See also

- [`a-la-carte-independence.md`](a-la-carte-independence.md) — why filesystem
  manifests are the cross-plugin seam.
- [`codespace-repo-provenance.md`](codespace-repo-provenance.md) — the
  agent-codespaces config-provider pointer.
- [`install-vs-adopt-boundary.md`](install-vs-adopt-boundary.md) — machine-local
  cleanup versus repo mutation.
- Tracking: [ThomasMichon/copilot-extensions#1043](https://github.com/ThomasMichon/copilot-extensions/issues/1043).
