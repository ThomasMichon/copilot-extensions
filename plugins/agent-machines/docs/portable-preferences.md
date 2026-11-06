# Portable preference previews

`agent-machines preferences preview` reads explicit local JSON snapshots and
projects typed defaults for a supplied machine/harness scope. It does **not**
publish to OneDrive, persist acceptance, resolve canonical repository identities,
apply configuration, change bindings, discover packages, run modules, install
software or activate services/schedules. All domains report
`native-owner-required`; a suggestion is not an effective binding.

The [reviewed Phase 4 plan](../../../efforts/active/machine-health-heartbeats/preferences.md)
owns the remaining exchange/adoption and carrier qualification work. This command
is the first read-only consumer, not a claim that those future capabilities exist.

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
An unknown chain requires future explicit reconciliation; this command cannot
accept it. No returned revision or digest is application authority.

Exit `0` means candidate data is eligible for further **preview**, not adopted.
Exit `1` means unavailable/conflicting/stale candidate; JSON still reports the
last-known projection. Exit `2` means invalid input/request, with a bounded
reason-code diagnostic that does not echo the synchronized document.
Inputs must be regular local files, not links/reparse points or device streams.
Cloud-placeholder compatibility and real carrier publication/receipt remain
unqualified; a local file pass is not OneDrive qualification.
