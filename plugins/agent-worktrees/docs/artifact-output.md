# Verified user output

The opt-in native producer writes bounded deliverables directly into a newly
created final compartment. It uses the existing
[storage policy](artifact-storage.md#user-output-policy) and
[producer ledger](resource-producer.md#verified-user-delivery).
It does not copy or move existing user files, discover an account, change
configuration, activate a service or maintain a destination inventory.

## Negotiate, resolve, then produce

Use the selected runtime's exact command. First request:

```text
agent-worktrees resource-producer output-capabilities --json
```

Require `schema: agent-worktrees.user-output/v1`, `allocator: 1` and
`verified_delivery: 1`, including `incarnation_bound_reservation: 1`.
An old parser or missing capability is unsupported.
Do not install, bootstrap or retry through scratch or generic claim release.
The generic producer additionally advertises `user_output_delivery: 1` and
the optional `user-deliverable` lifetime.

Other `output-*` operations require `--json`, an exact local durable owner
(explicit `--owner-ref machine/project/worktree`, or the existing current-owner
resolver) and exactly one JSON object on stdin. The normal 16 KiB producer-input
limit, duplicate-key refusal, strict fields and owning-project checks apply.
Capabilities advertise the exact `output-*` operation names. Resolution returns
the destination schema below; production, inspection, recovery and delivery
reuse the version-1 resource-producer success envelope and its stored metadata.

`output-resolve` accepts `topic`, `owner_roots`, and optional `destination` and
`target`. For example:

```json
{"topic":"Report","owner_roots":{"downloads":"/home/example/Downloads"}}
```

Use an absolute native Windows path on Windows. The selected storage provider
must supply roots for the intended account; this producer never infers it from
environment variables. A OneDrive preference is not evidence that an intended
account or root is available. `owner_roots` may be empty when an explicit
destination or exact configured machine root supplies the target.

Resolution is inert. It returns the selected root, relative members, exact
destination and `root_admitted: false`; it creates nothing.
`output-produce` accepts the same fields plus:

```json
{"files":{"report.txt":"UTF-8 report text"},"operation_id":"REPLACE_WITH_A_STABLE_RANDOM_TOKEN_32_CHARS"}
```

Send these fields in **one** object, not as two concatenated objects. Retain the
operation ID before sending. The producer selects at most 16 regular, singly
linked files totaling at most 64 MiB. The JSON CLI is further bounded by its
16 KiB request; the Python `artifact_output.produce_files` API accepts byte
content within the same file/byte limits. Member names are portable, bounded,
case-insensitively unique plain components. Directories, streams, links and
reserved device/control names are not deliverable members in this first producer.

## Allocation and handover

The owner-selected root must already exist and must not overlap a source,
Git, worktree-state or session-state home. The provider pins every directory
without following links or reparse points. It requires a user-owned root
without other-principal write access; read access and ordinary inherited
permissions are permitted for user output. Scratch keeps its stricter private,
protected-permission checks. The provider never repairs either root's permissions.
Cloud-native reparse traversal is explicitly unsupported; a refusal is not
proof that a OneDrive location is usable.

The allocator saves a pending responsibility, arms its fenced attempt, creates
the organization directories and exclusively creates the final compartment.
It binds the real root/directory and worktree incarnation **before** returning
the writable path. Existing final compartments are never overwritten or adopted;
a collision remains an explicit unresolved allocation, not permission to choose
a different destination silently.

The integrated producer ends its own writes, flushes file contents and seals
its bounded `.copilot-output.json` manifest. The registered native provider
then reads the actual declared files, checks exact membership, sizes and hashes,
and verifies the pinned identities. The ledger rechecks the same callback
subject in its serialized transaction before storing the terminal receipt.
The marker is per-deliverable integrity metadata, not a current destination
registry. Its allocation commitment does not expose the creator, local record
token, machine identity, filesystem identity or source paths. The user may
remove it or reorganize the completed folder later.

Local verified handover to a sync folder is **not** cloud synchronization,
remote acknowledgment or a promise of future retention. Delivery authority and
any required account-specific storage policy still belong to the caller.

## Failure and exact retries

`output-inspect` accepts `{"token":"TOKEN_FROM_THE_ORIGINAL_OPERATION"}` and
reads the ledger, not user storage. `output-deliver` uses the same input to retry
that allocation's handover. An incomplete, unreadable, changed or over-budget
output cannot discharge responsibility. No caller-supplied success flag or
manifest hash substitutes for the registered provider's real reads.

`output-recover` may return only an exact never-started allocation whose marker
is still `allocated` and whose payload is empty. It creates nothing. Once writes
may have started, recovery refuses rather than overwriting unknown work.
A failed allocation or unknown reply keeps its original token and obligation;
there is no automatic new operation, deletion or cross-owner handoff.

An exact `output-produce` retry uses its saved original request and allocation,
not a newly computed date or changed configuration. Changed request content is
refused. Once delivered, retries return the saved historical result without
reading, recreating or repairing the destination, even after owner finalization
or a later user move. The existing history retains destination, verification
time, file/byte count, integrity summary and result. Current ownership is empty
for that completed handover; no periodic scan or recipient handshake is added.
Normal checkout retirement, paired retirement and a failed tombstone write keep
that existing historical receipt; plain record deletion cannot discard it.

## Source qualification

The focused ordinary cases are `test_artifact_output.py`,
`test_artifact_output_cli.py`, `test_artifact_output_finalize.py` and
`test_resource_delivery.py` in the Worktrees test suite. They cover the actual
allocator, native filesystem reads, ledger failure/retry and real finalization.
Use the existing contained plugin runner, not an installed runtime.

The opt-in `tests/test_artifact_output_process.py` T3 packet adds an actual child
interpreter through the CLI, including CRLF/empty file integrity and completed
retry after a synthetic user move. Select it with `--allow-explicit-tiers`.
It uses fixture-owned configuration and private roots on the actual test OS;
it does not qualify real account discovery, a cloud provider or installed adoption.
The Windows-only `tests/test_artifact_output_windows.py` T3 packet checks real
read, inherit-only and write access entries on newly owned synthetic directories.
These cases do not enlarge the required unit-only CI packet.
