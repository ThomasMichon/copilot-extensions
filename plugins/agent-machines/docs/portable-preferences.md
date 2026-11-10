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
file. Neither command discovers or enrolls a cloud carrier. The additive
`preferences carrier` commands select that file from an explicitly configured
local OneDrive replica, as described below; they do not call the provider.

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
the carrier adapter's conservative local name census below is not a provider
concurrency guarantee, an mtime winner or a synchronized lock.

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

## Read accepted repository defaults

`preferences repositories --state-dir <local-state> --machine <native-logical-id>
--harness <native-canonical-id> [--local-overrides <local.json>]` projects only
the unsynchronized local `policy.json`, `accepted.json` and optional explicit
override. It does not read a carrier, accept new input, resolve repositories,
call Worktrees or create any files. Missing accepted data reports an absent
preference; withdrawal and local suppression retain their existing meanings.
The policy must admit repositories and all domains present in the selected
documents. Wrong publishers, invalid inputs and mismatched local machine scope
remain errors, not an empty default.

The read-only response uses `agent-machines.preferences.repositories`, integer
version `1`, with `scope`, `accepted_revision`, `knowledge_preference`, winning
`provenance` and an `input_revision` bound to the exact selected input bytes and
paths. It always reports `configuration_applied: false` and
`binding_changed: false`. Exit `0` includes absent/suppressed defaults; exit `2`
means unavailable or invalid selected input. No apply flag is supported.

Worktrees' optional
[Repositories Configuration consumer](../../agent-worktrees/docs/repositories-configuration.md#accepted-default-in-configuration)
supplies native identities and owns explicit adoption. Before invoking this
additive command it checks `preferences-repositories-v1.json` in the exact
selected Machines runtime slot: this schema/version, `accepted_projection: 1`,
`read_only: true` and `target_identity: caller-native`. Missing, invalid or
unsupported metadata leaves only the optional feature unavailable; a newer
payload is not proof of runtime support. Old preview/exchange callers keep their
existing syntax and behavior. No baseline is retired or effectful fallback added.

## Selected OneDrive local replica

The optional `preferences carrier publish|receive` commands reuse the same
policy, revision checks, local watermarks, atomic writer and confirmation lock.
They never discover an account or directory, install/authenticate a sync client,
call a provider API, hydrate a placeholder, enroll a participant or start a
watcher. A configured account label is not authenticated account evidence.

After separately approving the carrier/account, audience, participating
identities and permitted data, place `carrier.json` beside the unsynchronized
`policy.json`. This command does not create that configuration:

```json
{
  "schema": "agent-machines.preferences.carrier",
  "version": 1,
  "provider": "onedrive",
  "account": "example-account",
  "directory": "<absolute selected local replica namespace>",
  "participants": ["example-publisher", "example-receiver"]
}
```

Replace the directory placeholder with an absolute native path to a
**preprovisioned dedicated preference namespace**, not the whole sync root.
The directory may differ between participating machines. It contains only the
fixed `preferences.json` document; health and Vault use separate namespaces.
The local state directory must be disjoint from it, in either direction.
Locally authored candidates and local overrides must also remain outside it.
No directory or synchronized file is created by a preview.

This strict local schema rejects unknown fields, versions/providers, relative
or parent-traversing paths, linked/reparse paths, duplicate participant
identities and more than 64 participants. Both the policy's publisher and
participant must be explicitly listed, with exact spelling. The existing policy
still selects permitted domains and the sole administrative publisher; the
carrier cannot expand either. A received snapshot cannot configure its carrier.
Only the existing typed snapshot fields are published: no local account labels,
paths, policy, watermarks, overrides or raw telemetry are exported.

```text
agent-machines preferences carrier publish --state-dir <publisher-local-state> --input <complete-candidate.json>
agent-machines preferences carrier receive --state-dir <receiver-local-state> --machine <logical-id> --harness <canonical-id> [--local-overrides <local.json>]
```

Repeat the exact operation with `--apply --expect <preview-token>` to confirm.
`receive` means local acceptance only, not configuration application; its
`--full-snapshot` flag retains the existing explicit parent-gap reconciliation.
The account-labelled carrier configuration and bounded namespace census join
the existing token inputs and are reread under the local writer lock. Changed
configuration, entries, candidate, override or watermark requires a new preview.

The census inspects at most 128 immediate directory entries, without recursion
or reading any unexpected file. **Any entry other than `preferences.json`
blocks exchange as `carrier-conflict`**, including an apparent conflict copy
or incomplete temporary publication. An excessive census refuses with
`carrier-namespace-too-large`. Nothing chooses a newer file by mtime, merges
copies, deletes unknown entries or repairs a provider. The conservative
dedicated-namespace rule avoids depending on a particular conflict-name suffix.
It cannot detect a conflict that has not arrived locally.

A missing namespace reports `carrier-unavailable`; a missing document reports
`unavailable`. Both retain accepted local preferences and explicit overrides.
Neither is withdrawal or permission to create/rebind a namespace. A present
malformed, unsupported, unreadable or linked document remains an explicit error.
Only a valid newer complete snapshot can withdraw shared defaults; adopted
native configuration and machine-local choices remain independent.

The version-1 exchange response adds a `carrier` field only for this additive
operation. It reports `binding: configured-not-provider-verified`,
`local_replica_observed`, namespace availability and conflict presence.
The observation describes the exact document read during planning, not a
post-publication readback; `receive` retains `operation: accept`.
`synchronization`, `peer_receipt` and the existing `remote_receipt` remain
`not-observed`, including after a successful local publication. Exit meanings
are unchanged. If confirmation or response output fails, `applied: null` in
the bounded error means acknowledgement is unknown; inspect the local watermark
and exact document before deciding on another operation. Never blindly replay.

Discover this syntax with `preferences carrier --help` on the selected runtime.
Older runtimes reject it and retain their existing explicit exchange path; no
caller migrates automatically. Fresh normal-CLI tests use two fixture-owned local
replicas with an explicit fixture copy between them. They prove file exchange,
not OneDrive synchronization, cloud-placeholder support, authenticated audience,
installed-runtime adoption, Linux qualification or peer delivery. Those lanes
and any real carrier effects retain their independent approval/acceptance gates.

The focused source checks are `tests/test_preference_carrier.py` plus the
existing codec/exchange regression files. Select
`tests/native/test_preferences_consumer.py -k carrier` with the contained
runner's `--allow-explicit-tiers` for the fresh subprocess replica flow and
actual closed-output acknowledgement control.

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
An empty repository/knowledge requirement union reports
`native-defaults-unavailable`: the current native ordinary-default collector
cannot carry machine-local defaults through an empty union. Adoption refuses
instead of skipping those sources or manufacturing a requirement package.

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

## Confirmed retained-telemetry defaults

```text
agent-machines preferences telemetry --state-dir <local-state> --producer agent-bridge --directory <absolute preprovisioned base> [--local-overrides <local.json>]
agent-machines preferences telemetry --state-dir <local-state> --producer agent-bridge --directory <same base> [--local-overrides <local.json>] --apply --expect <preview-token>
```

`agent-dispatch` is the other supported producer. The policy must permit
`telemetry`; values come only from an already accepted snapshot and the
qualified native execution-machine/repository context. Shared, matching
named-machine and explicit local choices retain their existing precedence.
The directory is an explicit local selection, never a portable path.
Its complete `retained` namespace must already exist.

The adapter selects one immutable same-cell peer runtime through the existing
owner launch boundary. Its capabilities, preview and apply use that same pin
and revalidate installation governance on execution. An unavailable context,
legacy/non-pinnable runtime, missing provider, unsupported discovery or a
rolled-back provider without `input_fence: 1` blocks only this optional
operation. Ordinary exchange and existing producer commands remain available.
There is no PATH search, fallback configuration writer, bootstrap or update.
Before selection and every producer call, Machines refuses a selected producer's
nonempty `AGENT_BRIDGE_TELEMETRY_SINK` or `AGENT_DISPATCH_TELEMETRY_SINK`
override. Dispatch adoption also refuses any `AGENT_DISPATCH_TASK_*` environment
entry. The peer launcher's routing cleanup must not erase a custom sink or turn
a restricted task into an unrestricted configuration caller; these checks repeat
at capability discovery, preview and apply, including changes after preview.

For `capture: retained-local`, the native configuration owner validates its
target, preserves custom/legacy sinks and uses its own lock/atomic writer.
The Machines confirmation binds the native preview, pinned executable,
accepted revision, scope, policy and local choices. Digests come from the same
bounded reads that supplied the validated values, not a second file read.
Exact policy, accepted-snapshot and optional local-override bytes are passed
as data-only input fences and rechecked inside the producer's transaction.
Inputs cannot name or alias the native configuration target or its writer lock.
Changed inputs require another preview. This does not claim a cross-file
transaction against arbitrary external editors.

Absent or `disabled` capture choices contribute nothing and contact no producer.
Withdrawal does not erase adopted configuration, disable an existing sink or
remove local overrides. Reporting choices remain stored/displayed with
`reporting_adoption: unsupported`; no native reporting or schedule field is
invented. Per-producer results are independent, not a fleet transaction.

Output uses `agent-machines.preferences.telemetry`, version `1`. Exit `0` means
a successful preview, configuration or no-contribution result; `1` means a
reported native refusal; `2` means invalid/unavailable input or confirmation.
An unknown native write outcome also exits `2`, never success or a known refusal.
`configuration_applied`, `changed`, `activation: not-requested` and
`running_capture: not-observed` are separate facts. A failed native apply with
an unknown write outcome retains `configuration_applied: null`, not proof of no
effects. Inspect current native state before deciding another operation.
No service, sink factory, buffer, scheduled job or carrier is activated.
