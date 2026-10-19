# agent-plugin-activation

Provides the canonical strict state operations for Copilot plugin inventory and
activation:

- source-qualified installed inventory inspection;
- user-global and repository activation inspection;
- exact, dry-run-first removal of user activation without uninstalling inventory;
- activation snapshots and restoration around inventory bootstrap; and
- duplicate-key, malformed-shape, and wrong-type rejection before mutation.

Activation restoration retries an explicit Windows sharing or lock violation
(`winerror` 32 or 33) once after one second. It retains the original snapshot,
re-reads current documents and preserves refreshed inventory and unrelated
settings. The command is never replayed. Permission failures, malformed state
and other errors are terminal; exhausted restoration still raises its
restoration error. This is bounded in-process restoration, not crash recovery
or proof that a timed-out command's descendants have stopped.

`preserve_activation_durably(identity, home)` is an opt-in preservation context
for process owners that can provide stronger evidence. It serializes
participating operations for one resolved Copilot home through the shared
`OperationLock`, persists the original activation snapshot before launch, and
retains a versioned transaction under `activation-preservation/transaction.json`.
The process owner calls `confirm_quiescent()` only after positive non-breakaway
Windows Job cleanup plus root reap/drain, or `confirm_not_launched()` for a
proven pre-execution refusal. Neither a root exit nor a dead parent provides
that proof. This API does not run commands or choose process policy.

A persisted quiescent transaction can finish restoration after process death,
including interruption between settings and inventory writes. Recovery uses
the original snapshot, re-reads current state, verifies both activation
postconditions, preserves refreshed inventory and unrelated settings, and
clears the transaction only afterward. It finishes before a new capture or
command, even for another plugin. Unknown command settlement, changed/corrupt/
foreign/linked records, denied writes and failed verification stay explicit
blockers with the original record retained. Restoration success never means
the command succeeded or authorizes replay.

The home-wide lock coordinates adopted preservation operations, not unrelated
Copilot or user writes. The record contains only activation fields and
transaction identity, not output, credentials, environments or full settings.
Atomic, flushed publication supports process-interruption recovery; this is
not a power-loss durability guarantee for every filesystem. Ordinary
`run_install_preserving_activation` callers retain their existing in-process
contract until explicitly adopted.

`activation_custody(home)` also admits update-only acquisitions without
capturing a new snapshot. It finishes a prior witnessed install restoration
before the caller selects update versus install or checks remote auth, and
retains custody through the acquisition. An unproven install transaction blocks
either verb. Its `preserve(identity)` context nests an install snapshot under
that already-held custody, without taking a second lock.
Snapshot/witness publication retries only an explicit Windows sharing/lock
error (32/33), once after one second. It revalidates custody and the prior
record before reusing the exact same transition; it never recaptures state or
replays a command. Permission errors and exhaustion remain failures.
The owning updater reserves both the possible witness-publication delay and
the existing final-restoration delay within its original acquisition budget.

It also resolves the machine-wide effective plugin set used by attributed
drop-in registries:

- user-global `~/.copilot/settings.json` plus its local override;
- every project adopted in `~/.agent-worktrees/projects.yaml`, joined to the
  current-platform checkout in `repos.yaml`;
- repo identity verified through Git top-level and normalized `origin` remote;
- strict settings/registry reads whose uncertainty cannot become authoritative
  removal;
- local marketplace roots must converge, and each scope selects its live
  directory root before falling back to an exact installed payload;
- marketplace containment plus exact marketplace/plugin identity at the
  selected root; and
- tri-state and per-source decisions that use the shared drop-in reconciliation
  semantics.

The result maps canonical `name@marketplace` sources to a backward-compatible
preferred root plus explicit live roots grouped by activation scope. This lets
mixed global and project scopes retain installed-only providers without allowing
an installed payload to shadow a project-local directory that the host loads
live. It retains prior values when registry or source evidence is indeterminate
and returns structured findings for missing, mismatched, or ambiguous evidence.
Consumers decide how often to refresh and how to render findings.
**In dev**, most consumers' `pyproject.toml` reference this library through a
`uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1), so there is no per-plugin dev copy to keep in sync.
`customizing-copilot` keeps a full local copy instead: its standalone
`installing-plugins` skill script resolves `state.py` directly by file path
and has no consuming `pyproject.toml` surface the `uv`-editable form could target.
`agent-worktrees` uses canonical editable references on dev; its standalone
release receives real local copies during materialization.

**At release**, `tools/materialize_main.py` rewrites any `uv`-editable
consumers into the same real, promoted copy shape present here -- non-editable,
so every published plugin installs a self-contained source tree.
