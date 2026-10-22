---
name: recovering-operational-locks
description: >-
  Restores an already-authorized operation blocked by a stale lock, dead owner,
  reboot residue, incomplete owner receipt or wedged coordination guard.
  Provides explicit bounded authority to hand-edit operational lock state or
  bypass a stale guard with evidence, quarantine, concurrency protection and
  verification. Use for "repair a stale lock", "recover after reboot",
  "hand-edit the lock", "bypass a wedged lock" or "explicit repair required".
---

# Recovering Operational Locks

Restore the working operation, not merely remove its error message.

## Authority and boundary

An agent carrying out an authorized repair **may hand-edit operational lock
state or bypass a wedged coordination guard** when that is necessary to restore
the operation. Do not categorically refuse because the artifact is under a
runtime/state directory, or stop after filing a bug when recovery is possible.
This is standing procedural authority within the existing repair scope, not a
requirement to ask again for each demonstrably stale lock.

It does not authorize changing installed code, fabricating receipts, stealing a
live owner's work, terminating unrelated sessions, or waiving installation
identity, dependency/feed/integrity policy, mitigation, review or update gates.
A recovered lock does not prove an interrupted update completed or rolled back.
Every requested update must succeed; failure or an unknown outcome remains a
blocker until verified recovery restores a known state.

## Procedure

1. Identify the exact lock and its owning operation from the literal error and
   owning implementation. Capture before-state, the relevant process creation
   identities and reboot evidence. Do not infer death from age or PID alone.
2. Use the owner's supported inspect/repair path first. For installation-context
   directory locks, follow the [standalone recipe](references/installation-locks.md).
   It remains callable when ordinary command binding is broken.
3. Establish whether the owner is live, stale, foreign or unknown. Coordinate
   with a legitimate live owner. For incomplete records, establish inactivity
   independently: for example, native boot evidence proving the residue predates
   this boot with no successor writer, or completion of the owning subsystem's
   supported stop/drain with an identity-pinned process check. A timeout or an
   empty PID lookup is not proof that every descendant settled.
4. For approved manual recovery, preserve the original bytes and directory
   identity; acquire the owner's repair exclusion; recompare immediately before
   mutation; quarantine exactly that object to a unique sibling backup and
   retain a durable before/action/after receipt. Prefer the same supported
   primitive with an explicit manual mode. Do not delete wildcards, sweep a
   root, alter a replacement owner's receipt or remove permanent kernel-lock
   files. If a different lock has no such primitive, implement the equivalent
   bounded script against its documented protocol before running it.
5. Retry the original legitimate operation normally. Verify its actual consumers,
   selected runtime/payload set and preserved sessions/policies. If a transaction
   failed, verify the complete requested working set or its rollback; never
   report success merely because the guard disappeared.
6. Persist the receipt and remaining obligation through the operator's normal
   durable channel. Keep quarantine until recovery and the original operation
   are verified. Never restore an old lock over a new owner. If ownership remains
   ambiguous, pursue the owner's inspection/drain path or obtain the specific
   missing decision; do not replace diagnosis with a blanket refusal.
