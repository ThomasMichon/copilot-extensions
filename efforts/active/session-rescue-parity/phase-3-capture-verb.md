# Phase 3 — CodeSpaces capture verb + liveness gate: implementation details

Back to [README.md](README.md).

- [ ] **Coordinate capture with concurrent lifecycle operations, not just
      with itself.** `sync_codespace_sessions()` already takes a
      `TargetLock(name, op="session-sync")` for its own sync sub-step, but
      `_cmd_stop` (`__main__.py:3615-3632`) calls that sync, **releases the
      lock when it returns**, and only then calls `stop_codespace()` as a
      separate step -- so a periodic capture's own lock acquisition can
      land in the window between "stop's sync finished" and "stop's actual
      `stop_codespace()` call", pass its liveness probe, and pull while the
      CodeSpace is about to be (or is being) stopped underneath it. Widen
      the lock's held scope so a destructive caller (`stop`/`finalize`/
      `delete`/prune/reclaim) holds `TargetLock` across its **entire**
      sync-then-act sequence, not only the sync sub-step, so a concurrent
      capture attempt sees `TargetBusyError` and defers for the whole
      transaction, not just the sync portion. Add a contention regression
      test: a capture attempted while a destructive caller holds the
      (widened) lock across its sync+act sequence must defer, never
      interleave. **This does not extend to a truly external actor**
      (a human running `gh codespace stop` directly, or GitHub's own
      idle-timeout) bypassing this repo's lock entirely -- that remains an
      accepted residual risk, identical in kind to a container being
      `docker stop`'d by a process outside `agent-containers`' own lifecycle
      code; call this out explicitly rather than silently, so a reader does
      not read this widened lock as closing every actor's race, only this
      repo's own in-process ones.
- [ ] **Default resolution for the acquire-then-release-during-the-pull
      snapshot race** _(agent-recommended default; the Phase 1 implementer
      may revise with new information, but the plan should not ship this
      question fully open)_: accept it as a documented residual risk for a
      periodic/advisory capture, matching what the existing containers
      `rescue-capture` already implicitly accepts (see the parallel
      Phase 1 item and Validation Plan wording, which are already
      conditioned on this exact choice) -- inventing a new atomic remote
      snapshot primitive that Copilot CLI itself does not support is
      disproportionate scope for a periodic evidence-preservation capture,
      not a merge/replace/destructive operation. Document this choice
      plainly in the shipped code's own docstring (mirroring how
      `agent_containers.replacement.probe_session_liveness` already
      documents its own best-effort nature) so a future reader does not
      mistake the two-probe approach for a stronger guarantee than it is.
- [ ] Add a CodeSpace-side liveness check (using the Phase 2 vendored lib,
      transport = the existing SSH `ConnectionManager`/`exec_with_retry`)
      that a new capture-only path calls before pulling, mirroring
      containers' "defer instead of capture mid-write" contract -- including
      the same corrected edge cases `ThomasMichon/copilot-extensions#3574`'s
      review caught: never disturb the CodeSpace's own state to get a
      liveness answer (containers' analog: never unpause a paused container
      just to capture it), and a single consistent deferral message/path
      for "not safely capturable right now" (containers' analog: the
      fleet-level check must delegate to the same per-member helper, not a
      second ad-hoc message).
- [ ] **This capture path must not simply call `sync_codespace_sessions()`
      with its existing defaults.** That function boots a `Shutdown`
      CodeSpace whenever `skip_if_shutdown` is false, and only special-cases
      the `Shutdown` state by name (`lifecycle._SHUTDOWN_STATE`) -- every
      other non-`Available` state (starting, provisioning, failed, or any
      future state) falls through to the same connect-and-maybe-boot path.
      A periodic non-destructive capture must never boot, connect to, or
      otherwise disturb a venue that isn't already `Available`/running: add
      an explicit preflight (list/inspect state only, no connection
      attempt) that defers immediately for any state other than the live,
      connected one -- matching containers' `rescue-capture`, which defers
      immediately on any non-`running` state rather than following
      `stop`/`rm`'s boot-tolerant or stopped-instance-evidence paths. Do not
      reuse `sync_codespace_sessions()`'s existing state handling as-is; it
      is destroy/finalize-shaped, not capture-only-shaped.
- [ ] Add a standalone, non-lifecycle-transition CLI verb (e.g.
      `agent-codespaces sync-sessions <name>`) that calls the gated capture
      without stopping/finalizing/deleting the CodeSpace and without ever
      booting it. **This is additive, not a replacement**: `sync_codespace_sessions()`
      currently has **seven** direct call sites in the current tree
      (`_cmd_delete`, `_cmd_finalize`, its JSON-modal finalize variant,
      `_cmd_stop`, the prune path, `_reclaim_for_quota`'s total-limit path --
      all in `__main__.py` -- plus `claim_provider_cli.py`'s reclaim
      callback). Re-audit `__main__.py`/`claim_provider_cli.py` directly when
      Phase 3 starts rather than trusting this count to still be exact by
      then; every one of them keeps calling `sync_codespace_sessions()`
      exactly as today, unchanged.
- [ ] **Bind the standalone capture to the exact owning account, never
      ambient-fallback and never an ambiguous multi-candidate match.**
      `sync_codespace_sessions()`'s default account resolution (when
      `account`/`token` are omitted) goes through `account_for_codespace()`'s
      best-effort path, which can silently fall back to ambient credentials
      -- safe enough for an interactive delete/finalize call by the owning
      operator, but not for an unattended periodic/standalone sweep,
      where a same-named CodeSpace across accounts or a sweep running
      outside the owning project could pull the wrong venue's sessions.
      **`get_codespace_status_with_account()` alone does not fully solve
      this**: it only returns an unambiguous, authoritative account when an
      exact per-name binding exists (`account_binding.bound_account(name)`);
      with no binding at all, it falls through to a generic multi-candidate
      scan across mapped/bound accounts and returns the FIRST one that
      confirms existence -- which is exactly the same same-name-across-
      accounts ambiguity this item exists to close. The new capture path
      must therefore require **either** an explicit caller-supplied
      `account`, **or** a confirmed exact per-name binding (never the
      generic fallback scan's first match) before minting/passing
      credentials, and **fail closed** (defer the capture) whenever
      neither is available. Tests: same-name-across-accounts **with no
      binding** (must defer, not accept the scan's first match),
      same-name-across-accounts **with a binding** (must succeed, pinned to
      the bound account), and an account-resolution-failure case -- all
      three asserting deferral or exact-match, never an ambient-fallback or
      ambiguous-match connection attempt.
- [ ] **Re-validate liveness after the pull, not only before it.** A single
      preflight probe immediately before `_pull_tar_bytes` does not close
      the window where a Copilot process acquires `inuse.*.lock` during or
      after the tar -- the capture could still snapshot a session mid-write.
      Define an equivalent post-capture check (containers' own flow
      re-probes after the rescue and before treating it as committed) or an
      atomic snapshot/locking protocol, and add a test exercising the
      probe-to-pull race (liveness acquired between the preflight and the
      pull completing) to prove the capture is rejected/retried rather than
      silently accepted. **Acknowledge the residual gap explicitly**: a
      session that both acquires AND releases its `inuse.*.lock` entirely
      within the pull's duration is invisible to a pre- and a post-probe
      alike (both can read idle while the archive was written mid-update).
      Neither this port nor, as far as this effort has confirmed, the
      existing containers `rescue-capture` closes that narrower window
      today -- a true fix needs either an atomic/lock-aware remote snapshot
      mechanism (the CodeSpace side would need to hold its own lock across
      the tar, which nothing currently does) or a detect-and-reject
      protocol (e.g. a watermark/generation check spanning the pull, reject
      and retry on any change). Decide in Phase 1 whether closing this
      fully is in scope for this effort or an explicitly accepted residual
      risk for a periodic/advisory capture (matching the containers
      precedent) -- do not let the two-probe approach read as a full
      guarantee it is not. The regression test must include an
      acquire-then-release-during-the-pull case, not only a lock that
      remains held through the final probe.
- [ ] Tests: CLI-dispatch coverage (text + `--json`, mirroring
      `test_rescue_capture_cli.py`'s shape), a liveness-gate regression test
      (mid-write session is deferred, not captured), a
      non-`Available`-state regression test (a `Shutdown`/`Starting`/
      unknown-state CodeSpace is deferred without a connection attempt),
      and lease/claim-ownership tests covering the owner, non-owner/
      no-lease, an orphaned claim (holder worktree gone), and a
      cross-machine L2-only hold (no local lease) case per Phase 1's
      lease/claim-ownership decision.

