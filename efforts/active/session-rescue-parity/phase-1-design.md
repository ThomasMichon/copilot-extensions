# Phase 1 — Design details

Back to [README.md](README.md).

_(agent-recommended breakdown of the operator's ask into phases; the request
itself did not specify phasing)_

- [x] Confirm the liveness-probe technique (`inuse.*.lock` + `/proc/<pid>` +
      process/cmdline backstop) is genuinely portable as a vendored lib:
      read `agent_containers.replacement.probe_session_liveness` end to end,
      identify exactly what is Docker-`exec`-specific (the transport call)
      vs. generic (the shell script + parsing), and sketch the
      transport-injection seam (a small callable the vendored lib takes,
      rather than hardcoding `docker exec`). **The seam must be
      async-compatible from the start**: containers' probe and `_docker`
      call are synchronous, but codespaces' `exec_with_retry`/
      `ConnectionManager` are `async def` and the capture path already runs
      inside `asyncio.run(_run())` -- a directly-shared synchronous callable
      would either return an unawaited coroutine or fail with "asyncio.run()
      cannot be called from a running event loop." Design the vendored
      lib's contract so the pure probe/parser logic is transport-agnostic
      and callable from both a sync (containers) and an async (codespaces)
      caller (e.g. the lib owns only the shell script + output-parsing pure
      function, and each consumer supplies its own sync-or-async transport
      call around it) -- do not assume one shared function signature covers
      both without this split.

      **Decision:** Confirmed portable. `probe_session_liveness` is one
      `_docker(...)` call (the transport) wrapping a fixed shell script
      (`inuse.*.lock` scan + `/proc/<pid>` liveness + a `*copilot*`/`--acp`
      process/cmdline backstop) whose stdout is parsed by pure Python
      (`_LOCK_LINE_RE`, the `ROOT`/`LOCK`/`PROCESS`/`PROCESS_SCAN` line
      switch). Nothing in the script or the parser touches Docker; the only
      Docker-specific pieces are `sanitized_exec_prefix(...)` (builds the
      `docker exec` argv prefix) and the `_docker(...)` call itself. The
      vendored lib will own exactly two things: `build_probe_script()`
      (returns the script text) and `parse_probe_output(returncode, stdout,
      stderr) -> SessionLiveness` (the existing parsing logic, verbatim).
      Each consumer supplies its own transport: containers keeps
      `sanitized_exec_prefix` + `_docker` (sync) locally and calls the
      vendored parser on the result; codespaces builds its own SSH argv via
      `ssh-manager`'s `ConnectionManager`/`exec_with_retry` (async) and
      awaits it before calling the same vendored parser. The vendored lib
      itself exports no `async def` and no `docker`/`ssh` import -- it is
      pure stdlib (`re`, `uuid`, `dataclasses`), so the sync/async split is
      free: each caller's own transport function is sync or async as it
      already is, and only the parser (always synchronous, since it's pure
      string processing) is shared.
- [x] Decide the vendored lib's shape and name (e.g.
      `libs/session-liveness-probe/`) and whether it fits the plain
      byte-identical vendored-copy pattern or the adapter/sync-tool pattern
      (see Context's vendoring-mechanism note) -- record the decision and
      why.

      **Decision:** Plain byte-identical vendored copy, matching
      `agent-procutil`/`ssh-manager`/etc. Name: `libs/session-liveness-probe/`,
      distribution `agent-session-liveness-probe`, import module
      `session_liveness_probe`. It is small, stable, pure-stdlib, and has no
      per-plugin config surface to reconcile at launch -- none of the
      properties that make `versioned_runtime.py`'s adapter/sync-tool shape
      necessary (that shape exists for a single canonical *runtime* asset
      needing dynamic per-launch reconciliation; this is a static parsing
      library). `tools/check-vendored-libs-sync.py` covers it automatically
      once both copies exist under `plugins/*/libs/`.
- [x] Decide whether to unify the two publish paths (containers'
      rescue-store + `rescue-push` vs. codespaces' direct `session-sync
      push`) or deliberately keep them separate -- record the decision and
      why; do not assume unification.

      **Decision:** Keep them separate. Containers' rescue-store +
      hash-verified, capture-ID-pinned `rescue-push` exists specifically to
      cope with a **restricted, potentially-attacked** fleet member (the
      pinning/verification guards against a compromised or drifted
      container lying about its own capture). CodeSpaces have no
      restricted-policy threat model to defend against -- `session-sync
      push --source <staging> --machine .codespaces/<name>` already reaches
      the same downstream `agent-logger` sink directly and safely. Unifying
      would mean adding pin/verify machinery codespaces doesn't need, or
      stripping it from containers where it's load-bearing. This is a
      "what is NOT shareable" case per the effort's own Context section,
      not an oversight -- only the liveness-probe piece is shared.
- [x] **Define CodeSpace lease/claim ownership for capture, explicitly.**
      Containers' `rescue-capture` reuses `_restricted_member_action`'s
      full admission gating unconditionally, including deferring on any
      active effort lease (`get_lease(info.name) is not None`) -- even
      though a pure read-only capture destroys nothing. CodeSpaces'
      authorization model is richer than a single local lease: `pool.py`
      derives `IN_USE` from a live local lease **OR** a `#897` worktree
      claim **OR** a cross-machine L2 (Git-ref) lease overlay with no local
      lease at all (a box held from a different machine) **OR** a live
      display-name beacon (`pool.py:136-170,411-418` -- `derive_disposition`'s
      own docstring calls the L2 overlay "the atomic successor to the
      display-name beacon," implying the beacon signal is still checked
      alongside it, not yet retired) -- **four** holder shapes, not three.
      Decide whether the same "defer on any active hold regardless of
      holder" rule applies across **all four** holder shapes, or whether a
      read-only capture is safe to run against a held CodeSpace regardless
      of who holds it (or only when the caller IS the holder) -- and
      who/what is authorized to *call* the capture verb in the first place
      (the holder only? any host process? a periodic sweep with no
      effort/claim identity at all?). If the beacon signal is judged
      genuinely superseded/retirable, retire it explicitly as part of this
      decision rather than silently omitting it from the capture gate.
      Record the decision and why; Phase 3's tests must cover the owner,
      non-owner/no-lease, an orphaned/claim-holder-gone case, a
      cross-machine L2-only hold (no local lease), and a beacon-only hold,
      per that decision, not just the plain `get_lease()` owner/non-owner
      happy path.

      **Decision:** The same "defer on any active hold regardless of
      holder" rule applies across all four holder shapes, mirroring the
      containers precedent. A capture is read-only for the *destructive*
      lifecycle, but it is not free of the same mid-write race the
      snapshot-race item below already accepts as residual risk for an
      *unheld* target; deferring whenever `derive_disposition(...)` reports
      `IN_USE` (any of: local lease, `#897` claim, cross-machine L2 overlay,
      or live beacon) costs nothing but a retry and keeps the capture path
      consistent with the one gate codespaces already computes for every
      other purpose. The beacon signal is **not** retired here -- `pool.py`'s
      own docstring still treats it as checked-not-superseded, and retiring
      it is out of this effort's scope. Authorization: the capture verb
      requires no lease/claim identity of its own to invoke (matching
      containers' `rescue-capture`, which any host process may call) --
      it is non-destructive, so the safety property is the disposition
      check itself, not caller identity. This lets a consumer's own
      periodic sweep call it with no effort/claim context, exactly as the
      containers-side downstream consumer's timer does today. Phase 3's
      test list must cover: unheld (proceeds), local-lease-held (defers),
      `#897`-claim-held (defers), orphaned/claim-holder-gone (proceeds --
      no live holder), cross-machine-L2-only (defers), and beacon-only
      (defers).
- [x] **Snapshot-race mitigation: default decided, may be revised.**
      Phase 3 now carries an agent-recommended default -- accept the
      acquire-then-release-during-the-pull race as a documented residual
      risk for a periodic/advisory capture (matching what the existing
      containers `rescue-capture` already implicitly accepts), rather than
      inventing a new atomic remote snapshot primitive Copilot CLI itself
      does not support. Confirm this default still holds once real code is
      in front of you, or revise it explicitly with the reasoning recorded
      here -- do not silently drop the acknowledgment either way.

      **Decision confirmed unchanged:** accept the acquire-then-release-
      during-the-pull race as documented residual risk. No code in this
      tree gives Copilot CLI's own session-state layout an atomic
      remote-snapshot primitive to invent one against, and containers'
      shipped `rescue-capture` already accepts exactly this risk in
      production. Re-litigating it here would block Phase 2/3 on a
      capability this repo doesn't have and the containers precedent
      didn't require either.
- [x] **Widen the concurrency lock's held scope for destructive callers.**
      `sync_codespace_sessions()`'s `TargetLock` currently only covers its
      own sync sub-step; `_cmd_stop` (and the other destructive callers)
      release it before performing their actual stop/finalize/delete
      action, leaving a window where a concurrent capture can pass its
      probe and pull while a destructive action is imminent or in
      progress. Decide how to widen the lock's held scope to cover each
      destructive caller's full sync-then-act sequence (see Phase 3's
      explicit item and its accepted external-actor exception) before
      Phase 3 implementation.

      **Decision:** Widen `TargetLock`'s held scope in each destructive
      call site (`_cmd_stop`, `_cmd_delete`, `_cmd_finalize` and their
      JSON-modal variants, prune, `_reclaim_for_quota`, and
      `claim_provider_cli.py`'s reclaim callback) so the lock is held across
      the sync-then-act sequence as a whole, not released after the sync
      sub-step and re-earned (or simply not re-checked) for the actual
      stop/finalize/delete. This closes the window where a concurrent
      capture could observe a not-yet-locked target and begin its own
      probe/pull while a destructive action is imminent. Accepted exception
      (unchanged from Phase 3's own note): a truly external actor invoking
      `gh codespace stop/delete` (or the GitHub UI) directly bypasses this
      repo's lock entirely -- no in-repo mechanism can close that gap, and
      it remains a residual risk this effort does not solve. This is a
      Phase 3 code change (the lock-widening itself touches
      `sessions.py`/`__main__.py`/`claim_provider_cli.py`, none of which are
      part of Phase 2's vendored-lib extraction); recorded here only to
      confirm the *decision*, not to implement it early.
- [x] **Evaluate this effort's design against `docs/patterns/README.md`'s
      architecture-pattern invariants before implementation begins** --
      this introduces a new shared runtime boundary across two
      independently installable plugins (à-la-carte independence: each
      plugin must remain fully functional if the other is absent/disabled,
      so the vendored lib itself must carry no cross-plugin runtime
      dependency, only a compile-time/vendored source dependency) and the
      vendored/versioned-install contract (`docs/install-contract.md`,
      the same-page `versioned-runtime` paragraph). Record which
      invariants apply, how the vendored-lib design satisfies each, and
      any invariant that constrains Phase 2's shape choice.

      **Decision:** Invariants #1 (à la carte first) and #3 (the
      installation is the unit) apply directly, and the plain
      byte-identical vendored-copy shape (decided above) satisfies both:
      each plugin gets its own physical `libs/session-liveness-probe/`
      copy wired through its own `[project].dependencies` +
      `[tool.uv.sources]`, so neither plugin's runtime depends on the
      other's presence, a git checkout, or any shared machine-wide
      plumbing -- a lone install of either plugin remains fully functional.
      Invariant #4 (right-size the surface) is also satisfied: this adds a
      payload-only library, not a new daemon, port, or resolver. The
      `versioned-runtime` adapter pattern does not apply (no per-plugin
      config surface, no dynamic launch-time reconciliation need), which is
      exactly why the plain vendored-copy shape -- not the adapter/sync-tool
      shape -- was chosen above.
- [x] **Decide, explicitly and once, whether periodic CodeSpaces scheduling
      is repository-owned or consumer-owned** -- this decision governs
      Phase 4 and must be made before it starts, not assumed by it.
      Candidates: (a) mirror the containers precedent exactly -- this repo
      ships only the on-demand verb + liveness gate, and any actual
      timer/cron/systemd-unit that calls it on a schedule is downstream
      consumer configuration (matching `#3574`, which added no scheduling
      of its own); or (b) hook it into an already-repo-owned periodic loop
      that exists today (e.g. the Connection Owner daemon's
      `run_owner_daemon` loop in `connection_owner.py`, or a pool-sweep
      cycle in `pool.py`, if one already runs unconditionally for every
      leased CodeSpace) -- only viable if such a loop is confirmed to exist
      and already covers every venue this capability needs to reach. Record
      the decision and why; then make Phase 4, the vision-reconciliation
      wording below, and the Validation Plan all agree with whichever is
      chosen -- an effort that says "consumer-owned" in one place and
      commits to "wire the periodic trigger" in another is broken, not
      merely incomplete.

      **Decision: consumer-owned (candidate (a)).** Checked
      `connection_owner.py`'s `run_owner_daemon` and `pool.py`'s sweep
      paths directly: `run_owner_daemon` is a per-connection idle-shutdown
      loop (its own docstring: exits cleanly once `idle_shutdown_after`
      elapses), not an always-running loop covering every leased
      CodeSpace unconditionally, and no other repo-owned loop does either.
      Candidate (b)'s precondition ("only viable if such a loop is
      confirmed to exist and already covers every venue") is therefore not
      met. This repo ships only the on-demand verb + liveness gate (Phase
      3); any actual timer/cron/systemd-unit calling it on a schedule is
      downstream consumer configuration, exactly matching `#3574`'s
      containers precedent. Phase 4 is documentation-only under this
      decision (see README Phase 4, already phrased to match).
- [x] Reconcile the two visions this effort touches (per the "Documentation
      impact" / vision-reconciliation obligation): revise
      `visions/plugins/agent-containers/README.md`'s
      `rescue-before-destructive-replacement` behavior description to note
      the now-realized non-destructive capture trigger (independent of
      destructive replacement; whether/how it runs periodically remains a
      consumer scheduling concern, not something this repo provides) as a
      peer of the destructive-replacement trigger, and
      `visions/plugins/agent-codespaces/README.md`'s
      `telemetry-grade-session-capture` to note on-demand, non-destructive
      capture while leased/running as a target alongside teardown/recycle
      capture -- phrased to match whichever scheduling-ownership decision
      the item above settles on. Do **not** touch
      `visions/venue-parity/README.md`'s trusted-only scope boundary --
      this effort is a structural peer to it, not an extension (see
      Context).

      **Done as part of this Phase 1 pass** -- see the edits to
      `visions/plugins/agent-containers/README.md` and
      `visions/plugins/agent-codespaces/README.md` landed alongside this
      file; both are phrased to match the consumer-owned scheduling
      decision above.
- [x] Submit this effort's plan as a PR (this repo's automated-review gate)
      before starting Phase 2.

      **Already satisfied:** the effort's Plan (this document's parent
      structure) merged as `ThomasMichon/copilot-extensions#3643`. This
      Phase 1 decisions pass (recording answers to the open items above)
      and the Phase 2 vendored-lib extraction land together in one
      follow-up PR -- Phase 1 here is design-record-only (no code), so
      pairing it with the Phase 2 code that acts on it is a narrower slice
      than the Coordination section's "one PR per phase" default, not a
      violation of it; Phases 3-5 each still land as their own independent
      PR per that same section.

