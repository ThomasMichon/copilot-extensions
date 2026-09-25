# Phase 1 — Design details

Back to [README.md](README.md).

_(agent-recommended breakdown of the operator's ask into phases; the request
itself did not specify phasing)_

- [ ] Confirm the liveness-probe technique (`inuse.*.lock` + `/proc/<pid>` +
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
- [ ] Decide the vendored lib's shape and name (e.g.
      `libs/session-liveness-probe/`) and whether it fits the plain
      byte-identical vendored-copy pattern or the adapter/sync-tool pattern
      (see Context's vendoring-mechanism note) -- record the decision and
      why.
- [ ] Decide whether to unify the two publish paths (containers'
      rescue-store + `rescue-push` vs. codespaces' direct `session-sync
      push`) or deliberately keep them separate -- record the decision and
      why; do not assume unification.
- [ ] **Define CodeSpace lease/claim ownership for capture, explicitly.**
      Containers' `rescue-capture` reuses `_restricted_member_action`'s
      full admission gating unconditionally, including deferring on any
      active effort lease (`get_lease(info.name) is not None`) -- even
      though a pure read-only capture destroys nothing. CodeSpaces'
      authorization model is richer than a single local lease: `pool.py`
      derives `IN_USE` from a live local lease **OR** a `#897` worktree
      claim **OR** a cross-machine L2 (Git-ref) lease overlay with no local
      lease at all (a box held from a different machine). Decide whether
      the same "defer on any active hold regardless of holder" rule applies
      across **all three** holder shapes, or whether a read-only capture is
      safe to run against a held CodeSpace regardless of who holds it (or
      only when the caller IS the holder) -- and who/what is authorized to
      *call* the capture verb in the first place (the holder only? any host
      process? a periodic sweep with no effort/claim identity at all?).
      Record the decision and why; Phase 3's tests must cover the owner,
      non-owner/no-lease, an orphaned/claim-holder-gone case, and a
      cross-machine L2-only hold (no local lease) per that decision, not
      just the plain `get_lease()` owner/non-owner happy path.
- [ ] **Decide the acceptable mitigation for the acquire-then-release-
      during-the-pull snapshot race** (see Phase 3's explicit
      acknowledgment): either accept it as a documented residual risk for
      a periodic/advisory capture (matching what the existing containers
      `rescue-capture` already implicitly accepts), or scope in an
      atomic/lock-aware remote snapshot or detect-and-reject protocol as
      part of Phase 3. Do not leave this undecided going into Phase 3.
- [ ] **Evaluate this effort's design against `docs/patterns/README.md`'s
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
- [ ] **Decide, explicitly and once, whether periodic CodeSpaces scheduling
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
- [ ] Reconcile the two visions this effort touches (per the "Documentation
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
- [ ] Submit this effort's plan as a PR (this repo's automated-review gate)
      before starting Phase 2.

