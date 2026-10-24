# R1a next qualification: real OS custody and replacement

[Admission](inventory-freshness-admission.md) · [Episode](s3ab.md)

**Proposal only. No native/process/crash execution is admitted or started.**
Keep the pure prototype unwired; no consumer, push, production path/default,
grace, F/S authority, ready/merge or deployment change. F1 retains the first
native test slot. These are correctness tests, not latency/performance samples.

## N1 — real primitives, two owned writers

- Use one fresh isolated test parent supplied/created by the approved harness,
  **before** calling the store. Record its absolute canonical path, plain/
  non-reparse ancestor identities and fresh-empty state. Never use a runtime,
  session-state, knowledge or eligibility directory.
- Pin the source head and actual imported source files. Use the real
  `OperationLock`, its OS locking implementation and `atomic_write_text`;
  do not substitute those primitives or add production test hooks.
- Existing contained/headless harness; two owned Python writers plus the
  coordinator. Record each PID, process start identity and ownership nonce.
  No detached process, new service, external agent, SSH or provider call.
- W1 commits an initial synthetic snapshot E1/revision 1. Both writers read
  the same full version token. W1 then holds the real adjacent operation lock;
  W2's real store commit must return typed busy in one attempt, with unchanged
  data bytes. W1 releases the manually held custody before its next store call.
- Coordinate with bounded IPC barriers, not sleeps: W1 commits revision 2;
  W2's stale revision-1 candidate conflicts without changing bytes; W2 rereads
  and commits revision 3 exactly once. An explicit fixture replacement with
  E2/revision 1 must reject an old E1/revision-1 token (ABA control).
- Real-file precommit refusal cases: wrong source/epoch/revision, corrupt file,
  oversize candidate, relative path, missing parent, directory target and
  linked/reparse paths where the host supports constructing those fixtures.
  Record before/after file identities and byte hashes; no namespace creation,
  truncation, missing-row deletion or mutation on refusal.

## N2 — explicit fault/sharing/crash scope, separately admitted

- **Windows sharing:** an owned data-file handle denying delete sharing causes
  real atomic replacement to fail without changing the old file. The store
  must not claim committed success. With delete sharing permitted, an old
  reader sees the old complete snapshot and a new opener sees the new complete
  snapshot, never torn JSON. Close only fixture-owned handles.
- **Truthful postcommit unknown:** pause W1 immediately after the real atomic
  helper returns but before verification. W2 deliberately replaces only the
  fixture data as a labelled uncoordinated-writer fault, using the real atomic
  helper. W1 must return unknown and preserve W2's bytes, never rollback.
- Deterministic fault barriers may use **test-driver-only tracing of pinned
  Python source boundaries**, if expressly admitted. Shared helpers remain
  unmodified and real; no mocked lock/replacement function. Without an admitted
  deterministic boundary, mark the case unavailable rather than race sleeps.
- Crash points: (a) after custody acquisition/before write, (b) after adjacent
  file flush/fsync/before replace, (c) after replace/before acknowledgement.
  Terminate only the exact owned child after PID/start-identity verification.
  Verify kernel custody release, old-or-new valid file visibility and stale
  token refusal on retry; an unacknowledged outcome is not fabricated success.
- Link/lock-file replacement may be prevented by Windows sharing rules. Record
  the actual prevention result; do not force permissions or call it a tested
  postcommit-unknown path. No power-off/reboot, directory-fsync or storage-device
  durability claim follows from killing a process.

## Artifacts, bounds and cleanup

- Before execution, configure durable raw stdout/stderr and a structured case
  ledger outside the disposable runner sandbox but inside the owned test run
  root. Record argv, source hashes, explicit fixture limits/policy, identities,
  lock transitions, results, version tokens and pre/post byte hashes.
- A JUnit file is evidence only if the approved harness explicitly configures
  its output. The ordinary local plugin runner does not itself request one.
  Do not invent a JUnit path or alter the shared runner without admission.
- Retain failures and crash leftovers for inspection. Keep existing time,
  process and memory bounds; stop and report an exceeded bound, without blind
  timing retries or increasing deadlines.
- Drain/reap only owned children. Record exited/reaped identities, close owned
  handles, and demonstrate final real lock reacquisition/release. Never unlink
  a live lock by age or kill an unrelated process.
- Delete only known fixture files after all owned executions have ended;
  record cleanup success or retained paths. No manual managed-worktree removal.
- Native evidence must distinguish visibility/custody from directory/power-loss
  guarantees. Known precommit refusal preserves bytes; detected postcommit
  uncertainty never claims success or rolls another writer back.
