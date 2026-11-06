# Portable preference exchange

`agent-machines preferences preview` reads explicit local JSON snapshots and
projects typed defaults for a supplied machine/harness scope. It does **not**
publish to OneDrive, persist acceptance, resolve canonical repository identities,
apply configuration, change bindings, discover packages, run modules, install
software or activate services/schedules. All domains report
`native-owner-required`; a suggestion is not an effective binding.

`preferences accept` and `preferences publish` add explicit local transactions,
each read-only until `--apply --expect <preview-token>` is supplied. Acceptance
stores data, not domain configuration; publication replaces only a selected local
file. Neither command discovers or enrolls a cloud carrier.

The [reviewed Phase 4 plan](../../../efforts/active/machine-health-heartbeats/preferences.md)
owns the remaining native adoption and carrier qualification work. Local exchange
does not establish installed adoption, synchronization health or peer receipt.
`preferences copilot` is a separate, explicitly confirmed native scalar-floor
operation described below; neither receiving nor accepting invokes it.

## Snapshot

The exact schema is `agent-machines.preferences`, integer version `1`.
`publisher` is the expected configured participant identity, not authentication.
`revision` is a positive integer no greater than `2^53-1`; `parent` is null for
an initial snapshot or a positive earlier revision. `domains` is a complete
snapshot; `machines` optionally maps logical machine identities to domain
overrides. Case-insensitively duplicate machine identities refuse.

```json
{
  "schema": "agent-machines.preferences",
  "version": 1,
  "publisher": "example-publisher",
  "revision": 1,
  "parent": null,
  "domains": {
    "copilot": {"model": "supported-model-id", "effortLevel": "high"},
    "telemetry": {"capture": "retained-local", "reporting": "local"},
    "repositories": {
      "knowledge": [
        {
          "harness_repository_id": "repo-example-harness",
          "knowledge_repository_id": "repo-example-knowledge"
        }
      ],
      "venues": [{"repository_id": "repo-example-product", "venue": "codespace"}]
    },
    "maintenance": {"updates": "manual"}
  },
  "machines": {}
}
```

These are illustrative IDs, not a usable registration or recommended model.
Portable identity comes from the native repository owner; this preview treats
supplied scope as data and reports `identity_resolution: not-performed`.
Never substitute a checkout basename or absolute path for a canonical identity.
Several harness records may name the same knowledge identity.

All domain fields are optional; unknown fields/domains refuse. Supported shapes:

| Domain | Fields |
|---|---|
| `copilot` | `model`: bounded identifier; `effortLevel`: `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. These are preview values, not proof of client capability. |
| `telemetry` | `capture`: `disabled` or `retained-local`; `reporting`: `disabled` or `local`. No sink path or raw telemetry. |
| `repositories` | `knowledge`: unique harness/knowledge identity records; `venues`: unique repository/venue records, with `local`, `codespace`, `container` or `ssh`. |
| `maintenance` | `updates`: `manual` or `automatic`. `automatic` is data, never unattended-execution consent. |

Identifiers are ASCII, 1-128 characters, begin with a letter/digit and otherwise
contain only letters, digits, dot, underscore, colon or hyphen. The whole
document is bounded to 256 KiB and depth 16, with 64 machine fragments and
128 total relationship records across shared and machine domains. Duplicate
JSON keys/relationship identities, malformed UTF-8/JSON, unsupported versions,
commands, paths and arbitrary requirement-package fields refuse.

## Local overrides and precedence

`--local-overrides` reads a separate exact document:

```json
{
  "schema": "agent-machines.preferences.local",
  "version": 1,
  "machine": "example-worker",
  "domains": {
    "repositories": {
      "knowledge": [
        {
          "harness_repository_id": "repo-example-harness",
          "knowledge_repository_id": null
        }
      ]
    }
  }
}
```

Select shared defaults, then the matching named-machine fragment, then this
explicit machine-local document. Host and `-wsl` identities are distinct.
The local document must match the supplied machine; it cannot redirect scope.
Local null knowledge/venue targets explicitly suppress a suggestion. Ordinary
fields overlay individually; relationships overlay by exact canonical identity,
not list position. Output provenance identifies the winning tier.

This projection does not inspect or supersede an existing local binding.
Native adoption must separately preserve that binding, unresolved-binding
failures, live values, enforce constraints, registration/class/account checks
and explicit approval. Current ordinary model/effort floors remain unchanged.

## Invocation and revision controls

```text
agent-machines preferences preview --input <received.json> --publisher <publisher> --machine <logical-id> --harness <canonical-id>
```

Optional `--accepted <snapshot.json>` supplies the last accepted snapshot;
neither input is written. `--conflict` records a known provider conflict and
blocks the candidate. This first consumer does **not** discover provider
conflict copies or claim synchronization health.

Equal revision/content is `unchanged`; a newer revision whose parent matches
the accepted revision is `pending`. Same-revision/different-content, older
replicas, publisher mismatch and missing parents block the candidate and
project only the accepted snapshot plus local overrides. A missing candidate
reports `unavailable`, preserves the accepted projection and creates no file.
Malformed/unreadable present input is an explicit error, not optional absence.
An accepted snapshot with another publisher refuses. There is no clock/mtime
winner or automatic reconciliation.

Complete new snapshots may withdraw defaults by omitting them. Withdrawal
does not undo a binding or other configured effect, and local overrides remain.
An unknown chain requires explicit reconciliation; `preview` cannot accept it.
No returned revision or digest is native-application authority.

Exit `0` means candidate data is eligible for further **preview**, not adopted.
Exit `1` means unavailable/conflicting/stale candidate; JSON still reports the
last-known projection. Exit `2` means invalid input/request, with a bounded
reason-code diagnostic that does not echo the synchronized document.
Inputs must be regular local files, not links/reparse points or device streams.
Cloud-placeholder compatibility and real carrier publication/receipt remain
unqualified; a local file pass is not OneDrive qualification.

## Explicit local acceptance and publication

Prepare a dedicated **unsynchronized local state directory** for one exchange
namespace. Place a strict `policy.json` there:

```json
{
  "schema": "agent-machines.preferences.policy",
  "version": 1,
  "publisher": "example-publisher",
  "participant": "example-receiver",
  "domains": ["copilot", "telemetry", "repositories", "maintenance"]
}
```

The configured publisher is an administrative role, not an election,
authentication or execution consent. Only a participant matching `publisher`
may publish. Readers may accept only the expected publisher's complete snapshots.
`domains` is a nonempty, nonduplicated subset of the four domain names; any
unpermitted domain, including a named-machine fragment, blocks the candidate.
The policy has the codec's size/depth limits and rejects unknown fields.
Changing it is an explicit local configuration operation, not a synchronized
instruction to change roles. Use distinct local state directories for distinct
namespaces; never place these state directories in the synchronized carrier.

```text
agent-machines preferences accept --input <received.json> --state-dir <local-state> --machine <logical-id> --harness <canonical-id>
agent-machines preferences publish --input <complete-candidate.json> --state-dir <publisher-local-state> --output <exact-local-document.json>
```

Both commands emit `agent-machines.preferences.exchange`, version `1`, with
`operation`, `status`, `eligible`, a `preview_token`, candidate/local-watermark
revisions, policy and separate effect booleans. Acceptance also emits the same
scoped projection used by `preview`. To commit, repeat the exact command with
`--apply --expect <preview-token>`. The token binds normalized paths, policy,
candidate, local watermark, publication target content, scope, override content
and reconciliation flags. Apply rereads these under a five-second bounded native
**local** writer lock. Changed inputs require a fresh preview; tokens do not
grant native configuration or binding authority.

Acceptance writes only `accepted.json` inside the supplied state directory.
`--local-overrides` and `--conflict` have the preview meanings. Repeated identical
revision/content is `unchanged` and does not rewrite acceptance. Older revisions,
same-revision conflicts, wrong publishers and known provider conflicts refuse.
`--full-snapshot` explicitly previews a parent-gap reconciliation; it may accept
a newer complete snapshot or initialize a missed chain, never roll back a
revision or bypass a conflict. Publisher/same-revision conflict reconciliation
is not implemented; do not edit the watermark to guess a winner.
Offline inputs preserve the last-accepted projection and local overrides.
A narrowed policy blocks newly received unpermitted domains; prior accepted
data is retained, not an authorization to apply excluded domains.

Publication uses a separate `publication.json` local watermark, never
`accepted.json`. This records **publication intent**, not receipt: it is written
before the exact output file is atomically replaced. A failed output replacement
reports `publication-pending`; preview and retry the identical complete snapshot.
Even if the output vanishes or an older replica returns, the local watermark
prevents rollback. A foreign publisher, newer output or same-revision conflict
refuses; an untracked output chain is not silently adopted. Publisher transfer
and recovery of lost local publication state remain explicit future
reconciliation work, not permission for another participant to take over.

Files are written as complete same-directory temporary files, flushed before
atomic replacement, with bounded output and temporary-file cleanup. On POSIX,
the local watermark directory is synced before publication or success, including
unchanged/retry paths. A failure reports `state-sync-pending` and does not
publish; refresh the preview and retry. This is not a qualification of Windows
or synchronized-provider crash durability. The local
`exchange.lock` is retained for OS writer serialization, not synchronized or
used as a fleet claim. Existing directory chains, inputs, outputs and state
files must be ordinary nonlinked paths; aliases among the selected files refuse.
Parent-traversal (`..`) components refuse before normalization, including in
`--state-dir`, so inspected paths and actual lock/write targets cannot diverge.
Write parent directories must already exist; an unavailable received directory
still projects the accepted snapshot as offline data. Output replacement is local publication
only: `remote_receipt` stays `not-observed`, and publication does not prove a
provider received or preserved it. There is no distributed compare-and-swap;
concurrent provider changes/conflict-copy detection require the future qualified
carrier adapter, not an mtime winner or a synchronized lock.

Exit `0` means eligible preview or completed local transaction, `1` means a
blocked/unavailable preview, and `2` means invalid input, changed confirmation,
refused apply or unavailable transaction. Inspect `applied` and
`local_published`, not exit code alone. Failures after publication intent can
leave that watermark advanced; no cross-file or cross-domain atomicity is claimed.
All outcomes retain `configuration_applied: false` and `binding_changed: false`.

### CLI compatibility

These are additive, explicitly selected provider operations; no existing
consumer begins calling them automatically. The original `preferences preview`
syntax, version-1 snapshot/projection schema and exit meanings are unchanged.
Discover commands with `preferences --help` on the **selected runtime** before
using acceptance/publication; an older runtime may support only `preview`.
Missing support means exchange is unavailable, not permission to copy state
manually, relax parsing or replay a different effectful operation.
Provider rollback preserves the read-only preview baseline. Local policy and
exchange output have their own strict version-1 schemas, independent of package
versions; no retirement window is opened by this additive change.

## Confirmed Copilot scalar floors

```text
agent-machines preferences copilot --state-dir <local-state> [--local-overrides <local.json>]
agent-machines preferences copilot --state-dir <local-state> [--local-overrides <local.json>] --apply --expect <preview-token>
```

The local policy must permit `copilot`, and `accepted.json` must already contain
a valid snapshot from that policy's publisher. No received candidate or cloud
file is applied directly. Scope comes from the current qualified native
configuration owner, not a supplied hostname, harness basename or target-machine
simulation. An unavailable or unresolved owner blocks this operation without
bootstrap, binding or fallback.
The native identity resolver supplies the same admitted aliases to discovery
and layering, including alias-named directories and per-machine overlays.
Host and guest alias sets remain separate; a different canonical identity refuses.

Selection retains shared -> matching named-machine -> explicit local preference
precedence. Only `model` and `effortLevel` enter the native settings owner as
ordinary scalar floors. Native package discovery, parsing, scope, ordinary
default selection and authority validation still run. Native defaults and
existing live values win over these fallback floors; native enforce rules apply
afterward and conflicting enforce declarations refuse. This does not certify
that a running client supports the requested model or reasoning value.

The adapter filters native settings contributions to the same two fields; it
does not reconcile other native settings, resources, modules, permissions,
bindings or schedules. It never generates requirement YAML. The existing
settings owner preserves unrelated live keys and backs up before atomic writes.
Malformed/duplicate-key live settings refuse rather than being repaired.

Preview emits `agent-machines.preferences.copilot`, version `1`, with native
scope, accepted revision, selected floors, preference provenance, overridden
fields, proposed changes and a confirmation token. The token binds policy,
accepted/local data, native context and packages, target and live settings.
Apply recomputes it and rechecks at the native settings-write precondition;
changed inputs require a new preview. Withdrawal contributes no floor and
does not erase a value already adopted. No cross-domain transaction is claimed.
All native settings applies hold one target-scoped inter-process lock across
live read, merge, confirmation, backup and replacement. The retained sibling
`settings.json.lock` is local writer coordination, not synchronized fleet state.
Different preference-state directories do not bypass it. Read-only previews
take no writer lock and create no lock file.
Only the parent directory is resolved for lock identity. Linked settings or
lock filenames refuse before read/merge/write, including native callers.

Exit `0` is a successful preview or native settings transaction; `2` means
invalid/unavailable input or a refused confirmation. Inspect
`configuration_applied` and `changed` independently. `binding_changed` stays
false and `running_session_delivery` stays `not-performed`; no active-session
model change, runtime update or service reload is performed.

Discover `copilot` with `preferences --help` on the selected runtime first.
Older providers retain their existing preview/exchange commands and reject this
new operation; do not fall back to a general restore or hand-written settings
change. Snapshot and exchange schemas remain unchanged.
