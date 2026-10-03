---
visions:
  - visions/harness-guidance
---

# Local-Cache Delivery Primacy

- **Slug:** `local-cache-delivery-primacy`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-10-02
- **Status:** Done
- **Vision:** `visions/harness-guidance` -- vision-extending. Reframes
  `resilient-safety-boundary` (and the worktree-scoped dynamic guidance
  pattern it governs): the lifecycle-hook-rendered
  `.local.instructions.md` sibling becomes the **primary** delivery path
  for projected instruction content; the checked-in,
  sync-worker-maintained copy becomes strictly the **fallback** for a boot
  where no pre-session hook could render anything fresher.
- **Umbrella issue:** [ThomasMichon/copilot-extensions#4925](https://github.com/ThomasMichon/copilot-extensions/issues/4925)
- **Builds on:** `efforts/2026/10/02 ambient-guidance-navigability`
  (Phase 7 landed the mechanism this effort reframes and extends -- read
  that effort's Journal for the mechanism's own design history before
  working here).

## Guiding Intent

`ambient-guidance-navigability` Phase 7 built the worktree-scoped local
cache (`docs/patterns/worktree-scoped-dynamic-guidance.md`) as a
*supplement* to the checked-in projection: "the checked-in copy remains
the unconditional floor," with the local cache patching the specific gap
of an ordinary contributor lacking push rights to fix sync-lag themselves.

The operator's stated intent is a **reprioritization**, not just a gap
patch: the lifecycle-hook-rendered local cache should now be understood
and documented as the **primary** way per-plugin instruction content
reaches a session. The checked-in, scheduled-sync-worker-maintained copy
is downgraded in framing to being purely the fallback that exists to
handle two cases -- which, in turn, means the precedence *mechanism*
itself needs one real fix (not just reframing) to actually deserve that
trust: a fallback that a stale leftover local file can silently outrank
is not a safe fallback (see Phase 1's stale-sibling item below). With
that fix landed, the mechanism serves:

1. A launch path where no lifecycle hook runs at all before the session
   starts (a fully headless/hookless/sandboxed invocation).
2. A launch path where a hook *could* run but hasn't yet had the chance to
   render fresher content before the very first read (a pre-sync gap this
   effort's Phase 2 does not need to close, since the local-render boundary
   already closes it wherever it's wired).

Operationally, this means two things:

- **The vision and pattern docs need rewording** to state this priority
  order explicitly, rather than leaving the local cache's role implied as
  secondary rescue plumbing beside a canonical checked-in source of truth.
- **Every pre-session boundary that *can* render the local cache before a
  session starts should do so** -- today, only `agent-worktrees`
  (create/resume/`sessionStart`) is wired. `agent-bridge` spawns Copilot
  sessions through several distinct paths -- `target.type == "local"`
  (direct same-filesystem spawn via a Session-Host), containers, GitHub
  Codespaces, an SSH/remote mesh, and `target.type == "command"` (a
  generic spawn-command shape that *also* covers Codespaces, containers,
  elevated relays, and other providers with no `target.cwd` at all,
  per `session_start.py`/`agent_registry_resolver.py`) -- and currently
  wires none of them. This effort's first slice closes **only
  `target.type == "local"`** (the one path proven to have the same
  direct-filesystem access to an explicitly daemon-local `target.cwd`
  that `agent-worktrees`' own wiring relies on -- **not** "command",
  which is not a reliable proxy for locality); every other target type,
  including `command`, is an explicitly out-of-scope follow-on slice
  (confirmed with the operator), since each needs a *remote-exec* variant
  of the render call rather than a direct filesystem call, or simply has
  no local `cwd` to render against at all.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent (this repo) | Vision/pattern doc reframing (Phase 1), `agent-bridge` local-spawn-path wiring (Phase 2) | independent per-phase worktrees, this repo's own PR flow |

## Coordination

- **Topology:** independent per-phase worktrees, each its own PR.
- **Host (owns PRs):** the driving agent, for both phases.
- **Delegates:** none.
- **Handoff:** each phase closes with its own PR merged before the next
  starts; a fresh session may pick up at either phase boundary from this
  doc's Journal.

## Context

See `efforts/2026/10/02 ambient-guidance-navigability`'s Journal for the
full design history of the mechanism this effort reframes:
`docs/patterns/session-scoped-dynamic-guidance.md` (per-session computed
facts) and `docs/patterns/worktree-scoped-dynamic-guidance.md`
(worktree-scoped projected instruction content -- the pattern this effort
directly amends).

`agent-bridge`'s session-spawn paths live in
`plugins/agent-bridge/src/agent_bridge/session_start.py`
(`SessionStartCore.start_session`, called from
`routes/sessions.py:start_session` and `routes/worktrees.py:resume_worktree`).
Only `target.type == "local"` resolves a `target.cwd` directly on the same
filesystem the agent-bridge daemon itself runs on -- directly analogous to
`agent-worktrees`' own `worktree_creation._create_worktree_core` /
`resolve_launch_cli._resolve_resume_context` call sites. Every other target
type -- container, Codespace, SSH-mesh, and the generic `"command"` shape
(which itself also covers Codespaces, containers, elevated relays, and
other providers, often with **no** `target.cwd` at all per
`session_start.py`/`agent_registry_resolver.py`) -- resolves a *remote* cwd
(or none) the daemon has no direct filesystem access to; rendering there
needs a remote-exec call, or simply does not apply (a follow-on slice, not
this effort's Phase 2).

## Request

Operator (verbatim, this session): "The intent of my effort here is that
the *primary* way of delivering per-plugin instructions is now via the
live-copy to `.local` mechanism. The checked in instructions should now
only be a 'fallback', so handle cases where an agent boots in a state
where it won't run hooks, or be able to sync *before* the session starts,
like what agent-worktrees (and agent-bridge) can provide."

Scoping follow-up, confirmed by the operator: the vision reframing is
exactly as proposed (no additional nuance to the scheduled sync-worker's
own cadence); `agent-bridge` wiring is scoped to the local spawn path
first (`target.type == "local"` specifically -- narrowed from an initial,
incorrect "local/command" framing, since `"command"` is not a reliable
proxy for locality; see the Journal), with every other target type as
explicitly named follow-on slices; no other pre-session boundary besides
`agent-bridge` was flagged.

## Plan

### Phase 1 -- Vision and pattern-doc reframing
- [x] Revise `visions/harness-guidance/README.md`'s `resilient-safety-boundary`
      behavior to state the lifecycle-hook-rendered local cache as the
      primary delivery path for worktree-scoped projected instruction
      content, with the checked-in copy as strictly the fallback for a
      hookless boot or a not-yet-rendered pre-session gap. **Landed.**
- [x] Revise `docs/patterns/worktree-scoped-dynamic-guidance.md`'s framing
      (Problem/Standard approach/Rationale sections) to match: the
      checked-in copy is introduced as the fallback tier, the local cache
      as the primary tier, not the reverse. Note the stale-sibling fix
      below *does* change the actual precedence mechanism -- this phase
      is reframing *plus* that one correctness fix, not reframing alone.
      **Landed**: a precedence note up top, §2/§3's own preamble/catch-all
      text, §5's "floor" wording, and the Exemplars pointer all updated.
- [x] Cross-check `docs/patterns/session-scoped-dynamic-guidance.md` for
      any framing that would now read inconsistently with this
      reprioritization (it covers a different, per-session-computed-facts
      case, so likely needs no change -- confirm rather than assume).
- [x] **Design and land a stale-sibling invalidation check**: today a
      local sibling is preferred purely by existence
      (`instruction_projections.py`'s `render_projection` preamble), and
      is only ever reconciled when `render_local_cache()` itself runs
      (`_render_local_cache_locked`'s stale-removal pass, which only
      fires for a *disabled* source, not a merely-outdated one). A sibling
      left by an earlier *successful* render can therefore silently
      outrank a *newer* checked-in projection on a later boot where no
      pre-session render succeeds (hookless boot, a bounded-timeout miss,
      or any other render failure) -- precisely the boot this effort's
      reframing says must fall back safely to the checked-in copy.
      **Design:** a render *timestamp* cannot establish freshness -- an
      older/regressed installed payload rendered *after* the sync worker
      commits a newer checked-in projection would still carry the later
      timestamp and win, recreating the exact bug this item fixes; a
      changing timestamp field would also break `render_projection()`'s
      deterministic output and the local cache's own byte-idempotence
      check (`current == rendered.content`). Use the **stable provenance
      the markers already carry** instead -- `pluginVersion` and
      `templateSha256`, no new field needed -- comparing the checked-in
      projection's marker against the local cache's own marker for the
      same source, with the numerically newer `pluginVersion` winning.
      **Equal-version fail-safe:** a version string is not an immutable
      source identity (`projection_reflect.py`'s own documented caveat) --
      a dirty/local-checkout payload can differ without a version bump,
      so an equal `pluginVersion` needs its own tie-break, decided by
      `templateSha256` specifically -- **never whole-file bytes**, which
      always differ by construction (only the checked-in file carries the
      preamble at all) and would make the checked-in file win on *every*
      equal-version tie, inverting the local-primary policy for the
      by-far most common steady-state case (caught by PR #4947's own
      review, which correctly flagged an earlier draft's vaguer "content
      differs" wording as exactly this trap). A matching hash means
      nothing meaningful changed -> local wins; a genuine mismatch ->
      fall back to the named **checked-in** file (not merely "this file",
      which is ambiguous to a reader comparing two files from outside
      either one's own context -- naming it explicitly closed a real
      misattribution failure mode found during validation, see below).
      **Landed**: rewrote `render_projection()`'s per-file preamble and
      the shipped `local-cache-catchall.instructions.md` template
      accordingly -- this is reading-agent-level precedence text (the
      mechanism has no Python-level enforcement point for a boot where no
      render ever ran), not a numeric comparison inside the render code
      itself. Kept deliberately terse to stay within the aggregate-budget
      tests' existing margins (two already-trimmed templates needed a
      further few bytes shaved). New unit tests assert both rendered
      texts literally contain the `pluginVersion`/`templateSha256`/
      "newer"/"tie" comparison language, and `tools/run-plugin-tests.py
      customizing-copilot`: 310 passed, 8 skipped -- full regression
      coverage, no existing preamble/marker test broken. The wording's
      equal-version/differing-hash fail-safe case has a known,
      separately-tracked reliability gap in clean-room validation --
      see the Validation Plan item below and
      `ThomasMichon/copilot-extensions#4960`.

### Phase 2 -- `agent-bridge` local spawn-path wiring
- [x] Add an `agent_bridge`-side equivalent of
      `agent_worktrees.local_cache_refresh` (resolve customizing-copilot's
      declared `render-local-cache` CLI the same bounded-timeout,
      never-raising, global-activation-scoped way Phase 7's
      `agent_worktrees.local_cache_refresh` does -- no cross-plugin
      reach-around; see `docs/patterns/a-la-carte-independence.md`).
      **Landed**: `plugins/agent-bridge/src/agent_bridge/local_cache_
      refresh.py`. Translated to asyncio idioms rather than copied
      verbatim -- `agent_worktrees.local_cache_refresh` is free to block
      its own one-shot CLI process, but this call sits inside
      `agent-bridge`'s own long-lived event loop, shared by every
      concurrent session the daemon serves. Every subprocess call is
      natively async (`asyncio.create_subprocess_exec` +
      `asyncio.wait_for`, never a thread a timeout could only abandon, not
      stop), including plugin-identity resolution, which still runs in
      its own throwaway subprocess (this module's own `__main__`) rather
      than in-process, for the same reason the original isolates it:
      `resolve_active_plugins()` can spawn unbounded Git child processes,
      and calling it in-process here would block every other concurrent
      session, not just this one spawn. Resolves the sibling
      `agent-worktrees` binstub through same-cell-validated peer
      resolution (`session_lifecycle_cli._agent_worktrees_launch_prefix`)
      when that shape fits `--agent-worktrees-path`'s single-string
      contract; when a same-cell receipt resolves to a shape that
      argument can't carry, the render is skipped entirely for that round
      rather than silently falling through to the downstream CLI's own
      ambient lookup, which would discard the only same-cell evidence
      available and risk rendering from the wrong marketplace
      installation cell (`docs/patterns/marketplace-installation-
      cells.md`); a genuinely receipt-less, unresolvable lookup still
      degrades to simply omitting the flag, since there was no same-cell
      evidence to discard in the first place.
      Whole-tree kill: Windows spawns via `agent_procutil.spawn_in_
      kill_on_close_job` (a per-invocation kill-on-close Job Object, with
      `no_window_flags()` so the captured child never allocates a visible
      console window) and closes that job unconditionally in a `finally`
      -- on success *and* failure, not only on timeout -- because a
      descendant can outlive the direct child via inherited stdio handles
      even on an otherwise clean completion. Job arming is mandatory, not
      best-effort: if the Job Object itself fails to attach, the
      already-resumed, unprotected process is killed immediately and the
      call reports failure rather than ever using a process this module's
      own whole-tree guarantee couldn't actually honor. POSIX spawns with
      `start_new_session=True` (the same arrangement every agent spawn in
      `transport.py` uses) and signals the whole process group directly
      via `os.killpg(proc.pid, ...)` -- not a live `os.getpgid(pid)`
      lookup (as `procgroup.safe_killpg` does), which breaks once a
      quick-exiting direct child has already been reaped by asyncio's own
      SIGCHLD watcher even though the group (and a surviving descendant in
      it) is still alive; `proc.pid` is a safe stand-in for the pgid here
      specifically because of the `start_new_session=True` invariant.
      This repository's PID-destruction rule requires identity
      verification before any destructive termination by numeric ID
      (an exited, reaped PID is eventually reusable): the identity token
      (`zdd.diagnostics.process_start_time`) is captured immediately after
      spawn, before any reaping could occur, and re-checked immediately
      before the `killpg` call -- a confirmed, live, *differing* identity
      there refuses the signal outright; a `None` reading (the common
      case this whole fix targets -- the leader already reaped, nothing
      new yet claiming that number) is not itself evidence of a mismatch
      and does not block the signal.
      Cancellation (`asyncio.CancelledError`, a `BaseException`, not an
      `Exception`) is caught separately from an ordinary timeout/failure:
      the same tree cleanup runs, shielded so it isn't itself cancelled
      mid-kill, then the cancellation re-raises -- a cancelled
      `start_session` (daemon shutdown, request teardown) never leaks the
      just-spawned resolver or render process, and cancellation still
      propagates correctly. **Narrowed, not fully unconditional**: the
      whole-tree guarantee above holds whenever an identity baseline was
      established (Windows always; POSIX whenever `process_start_time`
      resolved one at spawn time). When no baseline exists -- the
      universal case on non-Linux POSIX today, or a sufficiently fast
      POSIX leader-exit race -- `_kill_tree` fails closed (refuses the
      group signal) with no diagnostic, and a surviving descendant can run
      indefinitely, unobserved. **Deferred to
      `ThomasMichon/copilot-extensions#5063`**: tracks making that skip at
      least observable (a diagnostic) and considers whether a macOS
      identity backend or a belt-and-suspenders timeout-based reap is
      worth adding.
- [x] Eligibility is **`target.type == "local"` only** -- never `"command"`
      (that shape also covers Codespaces, containers, elevated relays, and
      other providers, often with no `target.cwd` at all; see
      `session_start.py`/`agent_registry_resolver.py`). Confirm the
      resolved `target.cwd` is a real, local, trusted directory before
      calling the renderer. **Landed**: gated on `target.type == "local"`
      at the `else:` branch of `_connect_via_session_host`'s own
      `remote_child_argv`/local split (never reached for a remote-
      boundary call), and on the resolved `work_dir` being a real
      directory on disk. "Trusted" is left to the render CLI's own
      `validate_repository_root()` fail-closed check (it already runs with
      `require_trust=False` for this exact operation), rather than a
      second, cross-plugin reach-around into `agent-worktrees`' own
      trustedFolders registry format.
- [x] Call it from the authoritative local-spawn-path boundary, after the
      real worktree directory is resolved and before the Copilot CLI
      process is actually spawned, bounded by a **short, explicit latency
      budget** (a few seconds -- sized against this plugin's own existing
      `SESSIONSTART_MAX_TIMEOUT_S`-style precedent, not the create/resume
      path's more generous 30s default, since this sits directly in the
      spawn's own critical path) -- completing the render before spawn
      necessarily gates spawn for up to that bound; it is never
      unbounded, and a timeout or any other render failure is absorbed
      (spawn proceeds against whatever the checked-in floor already has)
      rather than failing the spawn itself. **Landed**: `LOCAL_SPAWN_MAX_
      TIMEOUT_S = 5.0` bounds each bounded subprocess's own `wait_for`
      call (mirrors the cited precedent); the genuine hard ceiling on the
      call's **total** wall time is `timeout + _CLEANUP_GRACE_S` (`1.5s`,
      a tight bound explicit to this fast local-process use). The call
      site lives in `session_host_connection.py`'s `_connect_via_session_
      host`, right after `resolve_local_launch` resolves `work_dir` and
      strictly before that same method's own `spawner.spawn()` actually
      launches the process -- not at `session_start.py`'s own `target.
      type == "local"` entry, which cannot see the authoritative directory
      for a project-backed target (`cwd=None`) at all until `resolve_
      local_launch` resolves one. Wrapped in `contextlib.suppress(
      Exception)` at the call site itself -- defense in depth, independent
      of `refresh_local_cache`'s own internal absorption, so a call site
      must never depend solely on a callee's internal promise never being
      violated; a genuine `asyncio.CancelledError` is explicitly exempted
      and always propagates (never suppressed), matching the asyncio
      convention that cancellation itself is not a failure to absorb.
- [x] Guard test: a synthetic repo with a stale checked-in projection and
      a divergent installed payload gets its `.local.instructions.md`
      sibling refreshed by a local-spawn (`target.type == "local"`)
      `start_session` call, proven against the real `render_local_cache()`
      call (not a stub), completing within the defined latency budget.
      **Landed, narrower than originally scoped**: `test_local_cache_
      refresh.py::TestRefreshLocalCache::test_real_render_local_cache_
      call_genuinely_refreshes_the_sibling` drives `refresh_local_cache`
      itself (not a hand-reconstructed argv) with `_resolve_cli_script`
      pointed at a small stand-in CLI that calls the real, production
      `instruction_projections.render_local_cache()` (not a stub) against
      a repo starting with **no** sibling (not a stale one), and asserts
      the real render call creates it with the expected content --
      proving the whole call chain genuinely refreshes the cache, not
      just that some subprocess exits zero. `test_session_manager.py::
      TestLocalCacheRefreshWiring::test_project_backed_target_with_no_
      cwd_refreshes_the_resolved_worktree` separately proves the full
      `start_session` path end to end (a real `LocalSpawner` + a tiny fake
      ACP agent subprocess) for the project-backed-target case the
      call-site placement above addresses.
- [x] Negative-proof test: customizing-copilot not installed, the repo not
      yet trusted, or a render failure must never fail the spawn itself,
      and the render call is proven bounded by the defined latency budget
      (not merely asserted zero-delay, which is unachievable for a
      synchronous pre-spawn render). **Landed**: `test_session_manager.py
      ::TestLocalCacheRefreshWiring` drives the real `start_session`
      path end to end (real `LocalSpawner` + fake ACP agent) proving a
      direct-cwd target and a project-backed (`cwd=None`) target both
      refresh using the correct, resolved directory, and that an
      artificially-raising `refresh_local_cache` stub still leaves the
      session `IDLE` (never `FAILED`); `test_local_cache_refresh.py`
      proves the module's own budget math (resolution + render shares of
      `timeout`), three real descendant-survival (whole-tree kill)
      regression tests -- a hung direct child, a direct child that exits
      immediately leaving only an inherited-handle descendant alive, and
      an in-flight cancellation -- and a dedicated cancellation-specific
      regression proving `asyncio.CancelledError` both propagates and
      still reaches the descendant. Only the hung-direct-child and
      cancellation regressions assert elapsed wall time against `timeout +
      _CLEANUP_GRACE_S`; the immediate-exit regression asserts descendant
      cleanup without that additional timing assertion. All three carry
      the same identity-checked `finally` cleanup (the same PID-reuse-
      proof pattern `agent_worktrees/tests/test_git_ops.py` uses) so a
      regression in the kill path can never itself leak a process into
      the suite, and are skipped on platforms where the identity-token
      backend (`_process_start_time`) has no implementation (today: any
      POSIX platform other than Linux) rather than claim unverified
      coverage there.
- [x] Negative-proof test: a `target.type == "command"` (or any non-local)
      spawn never invokes the local renderer at all, including the
      specific case of a `"command"` target with no `target.cwd` present.
      **Landed**: `test_a_remote_boundary_call_never_invokes_resolve_
      local_launch_or_refresh` calls the real `_connect_via_session_host`
      directly with `remote_child_argv` set (the CodeSpace/mesh shape) and
      a stub spawner, proving neither `resolve_local_launch` nor the
      refresh is ever reached on that branch -- not merely inferred from
      the `if`/`else` code structure. Full regression: `tools/run-plugin-
      tests.py agent-bridge` passing on both Windows and Linux CI.

## Validation Plan

- [x] `tools/run-plugin-tests.py customizing-copilot agent-bridge` (and
      `agent-worktrees` if its own tests are touched) pass;
      `check-changefile-presence` / `check-version-consistency` /
      `check-docs-consistency` clean on every PR. **Landed**: both suites
      pass clean (`customizing-copilot`, `agent-bridge`); every standard
      guard (`check-module-size`, `check-docs-consistency`,
      `check-effort-vision-structure --base origin/dev`,
      `check-changefile-presence --base origin/dev`) passed on PR #4980
      before merge.
- [x] A clean-room-style proof (matching the methodology
      `ambient-guidance-navigability`'s own Phase 7 used) that a
      local-spawned (`target.type == "local"`) `agent-bridge` session
      actually sees the fresher `.local.instructions.md` content when the
      checked-in copy is stale -- not just that the unit-level render call
      fires -- and that the render's latency stays within Phase 2's
      defined budget. **Scoped-closed, not re-run from scratch**: Phase 7
      already ran exactly this clean-room proof (Scenarios A and B, 6/6
      PASS) against the generic discovery mechanism -- the checked-in
      per-file preamble and repo-wide catch-all that make an agent
      *prefer* an existing `.local.instructions.md` sibling over a stale
      or absent checked-in file. That discovery logic lives entirely in
      the checked-in instruction text and is agnostic to *how* the local
      sibling was produced (manually, by `agent-worktrees` create/resume,
      or by `agent-bridge`'s own spawn-path trigger) -- re-running the
      identical 3-sub-agent clean room again here would prove the same
      discovery mechanism a second time, not anything new about Phase 2's
      own contribution. What Phase 2's own landed tests actually prove --
      that its specific trigger (`_connect_via_session_host`'s pre-spawn
      call) produces a genuinely fresh `.local.instructions.md` sibling
      via the real, production `render_local_cache()` call (not a stub)
      -- is covered by `test_real_render_local_cache_call_genuinely_
      refreshes_the_sibling`, which intentionally runs with a generous
      `timeout=30.0` (not the production `LOCAL_SPAWN_MAX_TIMEOUT_S =
      5.0`) and makes no elapsed-time assertion, since its purpose is
      proving genuine end-to-end rendering without the flakiness risk a
      tight timeout would add to a real-subprocess test; the production
      budget itself is proven separately, as pure arithmetic over
      `timeout`/`_CLEANUP_GRACE_S`, not as an elapsed-time assertion on a
      real render. `TestLocalCacheRefreshWiring::test_project_backed_
      target_with_no_cwd_refreshes_the_resolved_worktree` proves the
      call-site placement (the correct, resolved `work_dir` reaches the
      refresh call) using a mocked `refresh_local_cache`, not a real
      render -- real rendering from that exact call site is not
      independently proven end to end; only the two facts above
      (discovery mechanism, generic; real-render correctness, via a
      relaxed timeout) are. Re-deriving Phase 7's own clean-room proof a
      second time for a different trigger path would not close this
      narrower gap either. **Deferred to
      `ThomasMichon/copilot-extensions#5061`**: proving a real render
      actually completes within the production `LOCAL_SPAWN_MAX_TIMEOUT_S`
      budget (not just that the budget arithmetic is correct) remains
      unproven and is tracked there rather than claimed closed.
- [x] **Stale-sibling-boot negative-proof, version-ordering case**: a
      `.local.instructions.md` sibling rendered from an older installed
      payload, left in place while the *checked-in* projection is
      subsequently updated to a genuinely newer version (the scheduled
      sync worker advanced it independently of this worktree's own local
      render), must be superseded by the checked-in copy on a boot where
      no render runs at all -- proven against the real marker-comparison
      logic Phase 1 lands, not asserted by inspection. **Landed**:
      frozen-snapshot clean-room scenario (real `render_projection()`
      output, independent skill/tool-forbidden `explore` sub-agents,
      matching `ambient-guidance-navigability` Phase 7's own methodology)
      -- **Scenario C**, 5/5 runs reached the correct final answer (the
      checked-in file's value) across two wording revisions; one run
      misattributed *which* file carried which version while still
      reaching the correct conclusion -- a labeling slip, not a
      precedence failure, noted rather than rounded up.
- [x] **Equal-version, matching-hash negative-proof** (the common steady
      state, nothing has changed -- local must still win, not fall back
      to checked-in merely because the two files' whole-file bytes
      differ): proven against the real marker-comparison logic, not
      asserted by inspection. **Landed**: **Scenario E**, 3/3 runs
      reached the correct answer *and* correctly identified the local
      file as authoritative, with correct reasoning (recognizing that
      whole-file length/the preamble's presence is *not* a precedence
      signal).
- [x] Deferred to `ThomasMichon/copilot-extensions#4960`: **Equal-version,
      differing-hash fail-safe negative-proof** (the case where a
      dirty/local-checkout payload shares a declared version with the
      checked-in copy but has genuinely different content -- fail safe to
      checked-in): **not reliably proven; left open rather than claimed
      complete** (caught by PR #4947's own review, which correctly
      declined to accept a passing claim this evidence doesn't support).
      **Scenario D**: across 5 runs relying on memory/`view` to
      compare two long, near-identical `templateSha256` values (both
      before and after renaming the ambiguous "this file" fallback target
      to explicit "this checked-in file"), only **2/5** reached the
      correct final answer -- every failure was a **transcription error**
      (the two hash values transposed between files), not a logic error
      in the stated rule; a run explicitly directed to use `grep` (whose
      own output is mechanically filename-prefixed, removing the
      transcription step) got it exactly right. The underlying
      precedence *rule* was never shown wrong, but an agent comparing two
      files from outside, relying on unaided memory, is a genuine,
      separate reliability risk this effort's own wording changes could
      not close. **Deferred to
      `ThomasMichon/copilot-extensions#4960`**: tracks the residual gap
      and the candidate directions (nudging the instruction text toward
      exact-match tooling, moving the tie-break to a deterministic code
      path where `render_local_cache()` already runs, or accepting the
      narrow residual risk as documented) -- not resolved inline here
      since none of those directions were a quick, obviously-correct fix
      within this PR's own byte/line budget, and forcing one without
      follow-up validation would repeat the exact "claimed solved, wasn't"
      mistake this item's own correction exists to avoid.

## Proposal

_Pending._

## Journal

### 2026-10-03 -- Phase 2 merged (PR #4980); effort Done

PR #4980 went through 9 Copilot review rounds (summarized individually
below) before landing. The last two rounds (8 and 9) each re-surfaced a
mix of genuinely new findings and **stale findings pinned to an earlier
diff position** (two threads citing `session_start.py` for the refresh
call and a missing Documentation/Graceful-cutover statement -- both had
already been fixed in round 3/round 2 respectively; the PR description
has carried both required statements since round 2). The one genuinely
new, tractable finding -- "namespaced-cell resolution is nonfunctional" --
was real: `_resolve_same_cell_agent_worktrees_path` built its candidate
directly under `cellRoot/plugins/agent-worktrees` (the peer's **durable**
install root -- `install.json`/`versions/`/`state/`), not its actual
**payload** root, which can live under a versioned subdirectory. The
candidate file was therefore never found, silently degrading the whole
same-cell optimization to an always-empty (safe, but inert) path for
every namespaced-cell install. Fixed by resolving the peer's own
`install.json` receipt the same way `_peer_launch.launch()` does to get
its real `payloadRoot` before building the candidate, with a test that
deliberately plants the payload under a versioned subdirectory distinct
from the durable root (proving the fix actually follows the receipt) plus
a negative-proof test confirming a file merely present under the durable
root is never mistaken for the resolved candidate.

The remaining two open findings from round 9 (a macOS identity-backend
gap, and a theoretical race where a leader process exits before its
baseline identity token can be captured) are genuine but already
candidly documented, accepted limitations in `_kill_tree`'s and
`_run_bounded`'s own docstrings -- fully closing either requires a new
OS-level identity backend or a pre-execution handshake primitive this
module doesn't have, disproportionate engineering for a defense-in-depth
measure. The actual risk is not bounded or self-evident: when the
baseline identity is missing, `_kill_tree` skips the group signal with no
diagnostic, and `refresh_local_cache` does not surface that a cleanup
attempt was skipped -- a surviving descendant on an affected platform/
timing combination can keep running indefinitely with nothing in the
logs to flag it, not merely "leak and eventually exit." Left as a known,
honestly-characterized limitation rather than chased further, consistent
with this effort's and `ambient-guidance-navigability`'s own established
scoped-close pattern.

CI's `guards + lint` job failed throughout on an unrelated, pre-existing,
repo-wide issue: `check-agent-bridge-contracts.py`'s "Agent Bridge
contract registry" step couldn't resolve ~14 historical commit SHAs its
own provenance entries reference -- confirmed (via `git show origin/dev:
<path>`, plus two other open, unrelated PRs #5045/#5047 hitting the
identical failure) to be a recurrence of the exact structural trap
`ThomasMichon/copilot-extensions#2230` already diagnosed once: a
provenance entry recorded a PR's own pre-squash branch-tip commit, which
becomes unreachable once squash-merge deletes that source branch. Filed
[#5054](https://github.com/ThomasMichon/copilot-extensions/issues/5054)
to track the recurrence and its suggested remediation, rather than
silently working around it or absorbing an unrelated multi-entry git
archaeology task into this PR's own scope.

Merging despite that failure was **not an authorized exception to a
required check** -- it didn't need to be. Checked the actual branch
ruleset directly (`gh api repos/ThomasMichon/copilot-extensions/rules/
branches/dev`): the only configured `required_status_checks` entry is
`workflow-lockdown-guard`, which passed clean on every push. The
"guards + lint" job (and its own "PR gate (required check)" rollup step)
is **not** itself a required check in the ruleset, despite its
self-descriptive name suggesting otherwise -- it is advisory/visible, not
blocking, at the GitHub level. `CONTRIBUTING.md:124-126`'s "all required
status checks" still applied in full and passed; this PR's merge
complied with the repo's actual configured gates, it just also tolerated
an unrelated non-required job's failure, which GitHub itself does not
distinguish from a deliberate per-PR exception. Confirmed via `pr-status`
that this repo's merge-consent gate does not require the bot's advisory
`COMMENTED` review to become `APPROVED` (per `commented-review-verdict`
guidance), and self-merged via the sanctioned `pr-self-merge` flow once
every finding was either fixed, confirmed stale, or explicitly
scoped-closed/tracked above.

Separately confirmed the bare-command marketplace-isolation finding this
PR's own round 7 had flagged in `plugins/agent-dispatch/skills/
troubleshooting-agent-dispatch/SKILL.md` (an unrelated file, from an
unrelated already-merged PR) was fixed upstream independently (PR #5037)
before I needed to open a dedicated fix PR for it myself -- confirmed via
`test_check_marketplace_isolation.py` passing clean after a routine
`git sync`, so no separate PR was needed there after all.

With Phase 2 merged, every Plan and Validation Plan item above is
resolved or explicitly scoped-closed/deferred to a tracked issue
(`#4960` for Phase 1's Scenario D gap, `#5054` for the unrelated CI
infra recurrence, `#5061` for the still-unproven production-budget
spawn-path render, and `#5063` for the silent/unbounded descendant
cleanup on a missing identity baseline) -- **this effort is Done.**

### 2026-10-02 (cont.) -- Phase 1 landed: vision/pattern reframing + the stale-sibling fix
Picked up immediately after the plan PR (#4926) merged, per `planning-
efforts`' own "sync forward, then execute" sequencing.

- **Vision reframing**: `visions/harness-guidance/README.md`'s
  `resilient-safety-boundary` behavior now states the lifecycle-hook-
  rendered local cache as the primary delivery path for worktree-scoped
  projected instruction content, with the checked-in copy strictly the
  fallback.
- **Pattern-doc reframing**: `docs/patterns/worktree-scoped-dynamic-
  guidance.md` gained a precedence note up top, reworded §2 (the per-file
  preamble) and §3 (the catch-all) to describe marker-provenance
  comparison rather than existence-based preference, clarified §5's
  "floor" wording, and updated the Exemplars pointer. Cross-checked
  `session-scoped-dynamic-guidance.md` -- genuinely out of scope (a
  different, per-session-computed-facts mechanism), confirmed rather than
  assumed; no change needed.
- **The stale-sibling fix** (the real code change this phase carries):
  rewrote `render_projection()`'s per-file preamble and the shipped
  `local-cache-catchall.instructions.md` to direct the reading agent to
  compare the checked-in and local files' own embedded marker
  `pluginVersion` fields -- prefer whichever is newer -- replacing the
  old existence-only "if it exists, prefer it" instruction.
- **PR #4947 review caught a severe tie-break bug in the first draft**:
  the initial wording resolved an equal-`pluginVersion` tie by "content
  differs," which is ambiguous between the *whole rendered file* (which
  always differs -- only the checked-in copy carries the preamble at
  all) and the *underlying template*. Read the first way, the checked-in
  file would win on *every* equal-version tie -- the by-far most common
  steady state -- inverting the entire local-primary policy this effort
  exists to establish. Fixed by comparing `templateSha256` specifically
  (already in both markers, no new field) on a tie: matching hash means
  local wins (nothing meaningful changed); a genuine mismatch falls back
  to the explicitly-named **checked-in** file (not vague "this file",
  which proved ambiguous to an outside reader -- see the validation
  findings below).
- Kept deliberately terse throughout: the final preamble is only ~29
  bytes longer than the pre-Phase-7 original (not the ~214-byte naive
  first draft), but the two already near-budget shipped templates
  (`agent-worktrees`' `worktree-context-guide` and `context-handoff`'s
  `awareness`) still needed a few more bytes trimmed each revision to
  stay under the aggregate-budget tests, and `instruction_projections.py`
  itself needed several comment-only trims to stay at its grandfathered
  2457-line module-size ceiling -- following the same precedent Phase 7
  itself set.
- **Clean-room validation, full and honest results** (real
  `render_projection()` output, independent skill/tool-forbidden
  `explore` sub-agents, matching Phase 7's own methodology): Scenario C
  (stale sibling superseded by a genuinely newer checked-in file) and
  Scenario E (equal version, matching hash -- local correctly stays
  authoritative) both came back clean across every run. Scenario D
  (equal version, differing hash -- the fail-safe-to-checked-in case)
  surfaced a real, reproducible failure mode: sub-agents comparing two
  near-identical marker JSON blobs from outside (not from within either
  file's own loaded context) repeatedly transposed the two
  `templateSha256` values **from memory**, reaching the wrong final
  answer in most runs -- a transcription error, not a logic error (a run
  explicitly told to use `grep`, whose output is mechanically
  filename-prefixed, got it exactly right). Reported this honestly in the
  Validation Plan rather than smoothing it into a false clean sweep; the
  precedence rule itself was never shown wrong, but relying on unaided
  memory to compare two long, similar hashes is a genuine, separate
  reliability risk worth knowing about.
- Added `test_render_projection_preamble_compares_marker_provenance_not_
  existence` and extended the shipped-catch-all test with the same
  content assertions, so the comparison language itself is guarded, not
  just the clean-room proof. `tools/run-plugin-tests.py
  customizing-copilot`: 310 passed, 8 skipped. `context-handoff`'s own
  suite: 34 passed (the 3 `test_emit_guidance.py` failures are a
  pre-existing, unrelated Windows `bash.exe`-availability gap, confirmed
  via `git stash` to predate this change).
- Phase 1's code and docs are landed; see the next entry for round 2's
  correction to this journal's own premature "fully proven" claim above.

### 2026-10-02 (cont.) -- PR #4947 review round 2: three more findings, honestly worked
Three further issues came back from the automated reviewer after the round-1
tie-break fix above:

- **The catch-all's own unit test couldn't actually prove what it
  claimed.** `test_customizing_copilot_ships_the_repo_wide_local_cache_
  catchall` asserted against the full *rendered* projection output, which
  always includes the auto-injected per-file preamble -- itself written in
  the same comparison vocabulary ("pluginVersion", "templateSha256",
  "tie"). The test could pass purely off the preamble's text even if the
  catch-all template's own body were wrong or missing that language
  entirely. Fixed to assert against `spec.template_content` (the catch-
  all's raw, un-rendered body) directly, and strengthened to check both
  tie outcomes explicitly (matching-hash -> local stays authoritative;
  differing-hash -> checked-in file wins).
- **The pattern doc's own literal examples drifted from the shipped
  text.** `docs/patterns/worktree-scoped-dynamic-guidance.md`'s §2 and §3
  code blocks still showed the pre-fix "content differs" wording from
  before the `templateSha256` tie-break landed -- the doc demonstrating
  the mechanism no longer matched the mechanism. Fixed both blocks to
  read identically to the shipped preamble/catch-all text.
- **This journal's own prior entry overclaimed.** The reviewer correctly
  declined to let "Scenario D: ~2/5" stand next to a Validation Plan item
  marked complete -- a validation gate that isn't actually met at an
  acceptable reliability cannot be checked off, even when the surrounding
  work is otherwise solid. Rather than force a false "fixed" or quietly
  drop the finding, split the single Validation Plan checkbox into three:
  Scenario C (version-ordering, 5/5) and the equal-version/matching-hash
  case (Scenario E, 3/3) stay `[x]`, each scoped to only what it actually
  proves; the equal-version/differing-hash case (Scenario D, 2/5, every
  miss a hash-transcription error rather than a logic error) stays
  genuinely `[ ]` and now points at tracked GitHub issue
  `ThomasMichon/copilot-extensions#4960`, which records the full failure
  mode, the `grep`-success counter-evidence, and three candidate
  remediation directions (left undecided -- none was an obviously-correct
  fix within this PR's own byte/line budget, and picking one without
  follow-up validation would repeat the exact mistake this correction
  exists to avoid). The effort stays Active, not archived, so this
  doesn't block anything right now; it is a named, trackable gap rather
  than a silently dropped one.
- Re-ran `tools/run-plugin-tests.py customizing-copilot` after the test
  fix (310 passed, 8 skipped) and the module-size/budget checks after the
  doc-example edits (prose-only, no code/byte-budget impact).

### 2026-10-02 (cont.) -- Phase 2 landed: `agent-bridge` local spawn-path wiring
PR #4947 (Phase 1) merged clean on the fourth review round. Picked up
Phase 2 directly in a fresh per-phase worktree, per the operator's explicit
"continue" at the phase boundary.

- **New module**: `plugins/agent-bridge/src/agent_bridge/local_cache_
  refresh.py` -- the `agent-bridge` counterpart to `agent_worktrees.local_
  cache_refresh`, but translated to asyncio idioms rather than copied
  verbatim. The key design difference from the Phase 7 precedent: `agent_
  worktrees.local_cache_refresh` runs as an ordinary one-shot CLI command,
  free to block its own process; this call instead sits inside `agent-
  bridge`'s own long-lived daemon event loop, shared by every concurrent
  session the daemon serves. A synchronous `resolve_active_plugins()` call
  in-process (as `cold_store_sources.py`/`provider_sources.py` already do
  elsewhere in this plugin, for different, non-hot-path call sites) would
  have blocked every other concurrent session for the duration of its own
  unbounded Git child-process verification -- not just gated this one
  spawn. Every subprocess call here is therefore natively async
  (`asyncio.create_subprocess_exec` + `asyncio.wait_for`, never a
  background thread a timeout could only abandon, not actually stop),
  including identity resolution, which still runs in its own throwaway
  subprocess (mirroring the original's own reasoning for isolating it) --
  just via an asyncio-native tree-kill on timeout
  (`procgroup.terminate_windows_tree` on Windows; direct `proc.kill()` on
  POSIX) rather than `push_timeout.run_bounded`.
- Reused existing agent-bridge precedent rather than reinventing
  resolution plumbing: `agent_registry._agent_worktrees_bin()` for the
  sibling `agent-worktrees` binstub (this plugin's own existing resolver,
  not a new one), the same `agent-plugin-activation` dependency this
  plugin already declares and uses in-process elsewhere.
- **Wiring**: `session_start.py`'s `target.type == "local"` branch calls
  the refresh immediately on entry -- after `target.cwd` is resolved,
  before `_connect_via_session_host` (which actually launches the
  process) -- gated on `target.cwd and os.path.isdir(target.cwd)`.
  "Trusted" is deliberately left to the render CLI's own `validate_
  repository_root()` fail-closed check (already run with `require_trust=
  False` for this exact operation) rather than a second, cross-plugin
  reach-around into `agent-worktrees`' own trustedFolders registry
  format -- the CLI already owns failing closed on anything that doesn't
  resemble a projected-instruction repo.
- **A test caught a real gap in the first draft**: `refresh_local_cache`
  itself absorbs every internal failure and never raises -- but nothing
  at the `session_start.py` call site enforced that independently. An
  artificially-raising stub propagated all the way out of `start_session`
  and marked the session `FAILED` instead of `IDLE`, violating the Plan's
  own "a render failure must never fail the spawn itself" requirement as
  a property of the call site, not just the callee's internal contract.
  Fixed with `contextlib.suppress(Exception)` around the call itself --
  defense in depth, matching the explicit negative-proof test the Plan
  calls for.
- **Tests**: `test_local_cache_refresh.py` (new) mirrors Phase 7's own
  `agent_worktrees` test suite shape -- pure resolution-logic tests
  (`_select_global_root`), subprocess-wiring tests (`_resolve_cli_script`,
  mocking `_run_bounded`), `refresh_local_cache`'s own argv/budget-math
  tests, a real (non-mocked) round trip through the actual shipped
  `manage-instruction-projections.py` CLI, and a real descendant-survival
  regression test (a stand-in script spawns a grandchild and hangs; the
  grandchild must not survive the bound -- proving the whole process
  *tree* is killed, not just the direct child). `test_session_manager.py`
  gained `TestLocalCacheRefreshWiring`: a real local spawn with a genuine
  tmp-dir cwd invokes the refresh with the expected argument; a missing or
  nonexistent cwd, a `target.type == "command"` spawn (even with a cwd
  that looks locally real), and a raising stub all leave the session at
  `IDLE`, never invoking the renderer or failing the spawn. Full
  regression: `tools/run-plugin-tests.py agent-bridge` -- 652 + 397 + 55
  passed (1 skip, 3 skips), no existing test broken.
- Phase 2's every Plan item is now landed; `local-cache-delivery-primacy`
  has no further phases planned. The Validation Plan's Scenario D gap
  (`ThomasMichon/copilot-extensions#4960`) remains open and tracked
  separately -- it does not block this phase.

### 2026-10-02 (cont.) -- PR #4980 review: a Linux CI catch + five more findings
CI's Linux `agent-bridge` job caught a real cross-platform bug the
Windows dev box couldn't reproduce: the first POSIX `_kill_tree` draft did
a bare `proc.kill()` on the direct child only, never reaching a descendant
the child itself spawns -- the exact gap the regression test exists to
catch. Fixed by spawning with `start_new_session=True` on POSIX (matching
`transport.py`'s own existing spawn arrangement) and killing the whole
process group via `procgroup.safe_killpg`. The automated reviewer then
caught five further issues against that fix:

- **A genuine cancellation leak** (high severity): `asyncio.CancelledError`
  is a `BaseException`, not an `Exception` -- the original `except
  Exception` around the bounded subprocess's `communicate()` call let a
  cancelled `start_session` (daemon shutdown, request teardown) skip tree
  cleanup entirely, leaking the just-spawned resolver/render process.
  Fixed by catching `BaseException`, shielding the same cleanup (the
  pattern `session_host/endpoints.py`'s own SSH probe cleanup already
  uses) so cleanup isn't itself cancelled mid-kill, then re-raising.
- **An unbounded Windows cleanup tail**: `terminate_windows_tree`'s own
  defaults (`grace=3.0, kill_timeout=5.0`, tuned for a slow remote SSH
  shell to notice and self-exit) could add up to 8 more seconds on top of
  the stated 5-second budget -- a hidden ~13s worst case, not the
  documented bound. Fixed with an explicit, much tighter `_CLEANUP_GRACE_S
  = 1.5` passed into `terminate_windows_tree` (and a matching bound on
  POSIX's own `proc.wait()`), and the Plan prose now states the honest
  total ceiling (`timeout + _CLEANUP_GRACE_S`) instead of implying
  `timeout` alone.
- **A test that proved less than it claimed**: the guard test exercised a
  hand-reconstructed argv rather than `refresh_local_cache` itself, and
  never actually verified a sibling got refreshed. Rewrote it to call
  `refresh_local_cache` directly (with `_resolve_cli_script` pointed at
  the real CLI), seed a genuinely stale `.local.instructions.md` sibling,
  and assert the real render call brings it current.
- **A regression test that could itself leak a process**: the descendant-
  survival test disables process containment with no `finally` safety
  net -- if tree-killing ever regressed again, the grandchild could
  survive the assertion failure and escape into the suite. Added
  identity-checked cleanup in `finally` (the same PID-reuse-proof pattern
  `agent_worktrees/tests/test_git_ops.py` already uses), plus an explicit
  elapsed-wall-time assertion against the corrected ceiling.
- **Process/documentation nits**: the changefile requested a `minor`
  release with no maintainer direction for one (repo policy defaults to
  `patch`/`dev`) -- changed to `patch`. The effort doc itself had drifted
  into describing the pre-fix POSIX behavior and baked review chronology
  ("a round-2 test caught...") into Plan prose that should describe
  timeless behavior -- both corrected in place (see the Plan items above).
  The PR description gained the required Documentation impact and
  Graceful cutover impact statements.
- Re-ran the full `tools/run-plugin-tests.py agent-bridge` suite after
  every fix; CI (Linux + Windows) is the authoritative cross-platform
  check this Windows dev box cannot itself perform for the POSIX path.

### 2026-10-02 (cont.) -- PR #4980 review round 3: a genuine Windows architectural gap
Four more findings, one of them high severity and a real pre-existing
architectural gap neither prior round caught:

- **Windows descendants could survive even a successful completion, not
  just a timeout** (high severity). `terminate_windows_tree`'s own logic
  infers "whole tree gone" from "the direct child's own `proc.wait()`
  returned" -- true for the direct child, **false** when a descendant
  inherited the child's stdout/stderr handles and kept them open: the
  real CLI's own grandchildren (Git, an `agent-worktrees` lookup) are
  spawned this way by default. In that shape, `proc.communicate()` blocks
  on the descendant's own pipe close long after the direct child already
  exited -- and once the overall timeout fires, the old cleanup path saw
  the direct child already gone, never even attempted a forceful kill, and
  left the real survivor untouched. Fixed by switching the Windows spawn
  to `agent_procutil.spawn_in_kill_on_close_job` -- a per-invocation
  kill-on-close Job Object, closed unconditionally in `_run_bounded`'s own
  `finally` (on success *and* failure, not only on timeout) -- which is
  authoritative regardless of the direct child's own state. Added a
  dedicated regression test reproducing the exact gap (direct child exits
  immediately; a grandchild inheriting its stdio handles is the only
  thing still running) alongside the original hung-direct-child test.
- **The refresh ran too early for a project-backed target** (medium
  severity, real correctness bug): `start_session`'s own `target.type ==
  "local"` entry point doesn't yet know the authoritative worktree
  directory for a `SpawnTarget(cwd=None, project=...)` -- that only
  becomes known inside `_connect_via_session_host` once `resolve_local_
  launch` resolves it. The original placement either skipped the refresh
  entirely for that shape, or risked refreshing a stale/wrong directory.
  Moved the call into `session_host_connection.py`, right after `work_dir`
  is resolved and `target.cwd` is backfilled, still strictly before
  `spawner.spawn()` actually launches the process -- the Plan's own
  wording ("after `target.cwd` is resolved and before the Copilot CLI
  process is actually spawned") now genuinely describes where the call
  lives, not just where it was first (incorrectly) placed. Added a
  dedicated end-to-end test for exactly this shape, plus rewrote the
  wiring tests generally to drive the real `start_session` path (a real
  `LocalSpawner` + a tiny fake ACP agent subprocess, matching `test_
  session_host.py`'s own established pattern) instead of the lighter
  `_connect_via_session_host`-stub fixture other tests use -- that stub
  would have skipped the very code path these tests exist to prove,
  which is exactly how the original placement bug went undetected.
- **The effort's own landed-test record overclaimed** (low severity,
  caught against the actual test file): named a test
  (`test_real_cli_round_trip`) that only exercises `_run_bounded`
  directly against a hand-built argv, not `refresh_local_cache` or a
  genuine sibling-refresh assertion -- the actual guard test is `test_
  real_render_local_cache_call_genuinely_refreshes_the_sibling`, and it
  starts from no sibling, not a stale one. Corrected the record (see the
  Plan item above) rather than leaving a stronger claim than the evidence
  supports.
- **Missing Documentation impact / Graceful cutover impact statements**
  (low severity) were flagged again: the PR description edit from the
  previous round hadn't actually landed by the time this round's review
  ran against the pushed commit; re-confirmed both statements are present
  on the PR.
- Full regression: `tools/run-plugin-tests.py agent-bridge` -- all three
  sub-suites passing (including the new Job Object and project-backed-
  target regression tests), on this Windows dev box; CI remains the
  authoritative cross-platform signal.

### 2026-10-02 (cont.) -- PR #4980 review round 4 (and a real CI catch of round 3's own fix): POSIX reap-ordering bug, mandatory Job arming, same-cell peer resolution
CI (Linux) caught a genuine bug in round 3's own POSIX fix before the next
review round even ran: `_kill_tree`'s `proc.kill()` + `await proc.wait()`
(reaping the direct child) ran *before* the group-kill attempt. For a
direct child that exits quickly, asyncio's own SIGCHLD watcher had often
already reaped it by the time the timeout fired, making a live
`os.getpgid(pid)` lookup fail with `ProcessLookupError` even though the
group (and the surviving descendant in it) was still alive. Fixed by
computing the target pgid directly from `proc.pid` (valid because
`start_new_session=True` makes a POSIX child its own session/group leader
at creation, regardless of whether the leader later exits or is reaped)
and signaling the group with `os.killpg` directly, before any reap --
never depending on `procgroup.safe_killpg`'s own live-process-dependent
resolution for this specific caller.

The next review round then found five more issues:

- **Windows Job Object setup failure could leave an unprotected process
  running** (high severity): `spawn_in_kill_on_close_job`'s own contract
  still resumes the child even when Job arming itself fails, handing back
  `job_handle=None` so "existing cleanup paths remain in charge" -- but
  neither the Job-close path nor the POSIX group-kill path actually fires
  for that shape on Windows, so the fallback `proc.kill()` alone would
  leak a descendant exactly as before the Job Object fix. Made arming
  mandatory instead of best-effort: on `job_handle is None` (Windows), the
  already-resumed process is killed immediately and the call reports
  failure, rather than ever calling `communicate()` against a process this
  module's own whole-tree guarantee couldn't actually honor.
- **Missing Windows no-window flags** (medium severity): the short-lived
  captured children this module spawns omitted the repository's required
  headless flag, risking a visible console window or stolen focus under
  agent-bridge's own windowless resident daemon. Fixed by passing
  `agent_procutil.no_window_flags()` alongside the Job Object setup.
- **Ambient agent-worktrees resolution could trust the wrong marketplace
  cell** (medium severity): `_agent_worktrees_bin()`'s ambient `PATH`/
  `~/.local/bin` lookup could resolve a *different* installation cell's
  binstub than this exact agent-bridge install belongs to. Added
  `_resolve_agent_worktrees_path()`, preferring `session_lifecycle_cli
  ._agent_worktrees_launch_prefix()`'s same-cell-validated resolution when
  its result fits `--agent-worktrees-path`'s single-string contract (the
  common case: no `COPILOT_EXTENSIONS_CONTEXT` receipt, which degrades to
  the same ambient lookup as a single-element list), and omitting the flag
  entirely -- never falling back to the ambient-only lookup -- when it
  doesn't (a receipt-validated multi-token launch prefix is a different
  invocation shape that flag was never designed to carry).
- **Missing cancellation-specific regression coverage** (medium severity):
  the cancellation-safety branch in `_run_bounded` had no test cancelling
  it mid-flight. Added a dedicated regression: cancel the awaiting task
  after a direct child and its own grandchild have both started, assert
  `CancelledError` propagates, and assert the grandchild is gone.
- **The real-descendant tests claimed unverified macOS coverage**
  (medium severity): `_process_start_time` (the identity-checked `finally`
  cleanup's own PID-reuse guard) has a Windows and a Linux (`/proc`)
  backend only -- on macOS it silently returns `None`, which would make
  that same safety net skip cleanup if the tree-kill under test ever
  regressed, leaking a real process. Gated the real-descendant/cancellation
  tests to the two platforms this facility's own CI actually exercises
  (Windows, Linux) rather than claim coverage CI never verifies.
- Several accompanying documentation findings (stale `session_start.py`
  references in this file, the pattern doc, and test module headers that
  predated the round-3 relocation fix; an overstated wall-time-validation
  claim for the narrower descendant regression; review chronology baked
  into Plan prose and test docstrings) were corrected in place -- see the
  Plan items above and this entry's own Journal-appropriate citations here
  rather than in that prose.
- Full regression: `tools/run-plugin-tests.py agent-bridge` passing,
  including the new mandatory-Job-arming, same-cell-resolution, and
  cancellation regression tests.

### 2026-10-02 (cont.) -- PR #4980 review round 5: identity-verified termination, fail-closed same-cell resolution
Two more genuine findings, both HIGH severity:

- **The POSIX group kill derived its target solely from a possibly-stale
  `proc.pid`** -- an exited, reaped PID is eventually reusable by an
  unrelated process; signaling by bare numeric ID without identity
  verification violates this repository's own established PID-destruction
  rule (`docs/patterns/graceful-daemon-cutover.md`'s "Common review
  findings" -- identity-bound termination plus a dedicated mismatch/
  refusal test). Fixed by capturing an identity token
  (`zdd.diagnostics.process_start_time`, already a transitive dependency
  via `agent-zdd`) immediately after spawn, before any reaping could
  occur, and re-verifying it immediately before the `killpg` call -- a
  confirmed, live, differing identity refuses the signal outright; a
  `None` reading (the expected steady state for the exact scenario this
  whole fix targets) does not block it, since there is no live, differing
  occupant to protect. Reused `zdd`'s own shared primitive rather than
  reimplementing identity tokens a third time in this one module.
- **The same-cell `agent-worktrees` resolution added last round wasn't
  actually fail-closed** -- when a `COPILOT_EXTENSIONS_CONTEXT` receipt
  *was* present but resolved to the multi-token, receipt-validated launch
  prefix (a shape `--agent-worktrees-path` can't carry), omitting the flag
  let the downstream CLI fall back to its own ambient lookup -- exactly
  the wrong-cell risk the receipt's presence was meant to close, just one
  level removed. Fixed by distinguishing that case explicitly:
  `_resolve_agent_worktrees_path()` now returns `(path, must_skip_render)`,
  and `refresh_local_cache` skips the render entirely for that round
  rather than ever letting it proceed ambient-only when same-cell
  evidence existed but couldn't be passed through. A genuinely
  receipt-less, unresolvable lookup still degrades to the original
  "just omit the flag" behavior, since there was no evidence to discard.
- Also corrected a stale code comment (`_CLEANUP_GRACE_S`'s own docstring
  still described the retired `terminate_windows_tree` grace/kill_timeout
  split after the Job Object switch).
- Full regression: `tools/run-plugin-tests.py agent-bridge` passing,
  including the new identity-mismatch-refusal and fail-closed-skip tests.

### 2026-10-02 (cont.) -- PR #4980 review round 6: macOS fail-closed gap, feature preserved for namespaced cells, a test-leak fix
Three more findings:

- **The identity check from round 5 was ineffective on macOS and any
  other non-Linux POSIX host** (high severity): `zdd.diagnostics.
  process_start_time()` has a Linux (`/proc`) and a Windows backend only
  -- on every other POSIX platform it always returns `None`, and treating
  `None` as "no mismatch" meant the group kill still proceeded on an
  entirely unverified numeric pid there, exactly the gap the round-5 fix
  was supposed to close. Fixed by distinguishing a missing *baseline*
  (captured at spawn, before any reaping) from a missing *current*
  reading (re-checked before signaling): only a real baseline combined
  with no confirmed live mismatch permits the signal; a platform with no
  identity backend at all now fails closed universally, not just on the
  transient-miss case the round-5 fix actually handled. Added direct
  mocked tests for all three branches (no baseline, confirmed mismatch,
  established-and-uncontradicted).
- **The round-5 "skip the render" fix for namespaced marketplace cells
  was safe but too broad** (high severity): `scripts/runtime-gate.sh`
  sets the same-cell receipt for *every* namespaced-cell install, not a
  rare case, so skipping the render whenever the receipt-validated prefix
  couldn't fit `--agent-worktrees-path`'s single-string shape silently
  disabled the whole feature for that entire install class. Fixed by
  resolving the same-cell `agent-worktrees` binstub directly
  (`_resolve_same_cell_agent_worktrees_path()`, using `_peer_launch.
  validate_owner`'s own receipt/certificate check) instead of reusing the
  wrapper prefix's different, multi-token invocation shape -- a
  deliberate, documented, bounded trade-off (resolution-time identity
  verification, not the wrapper's additional execution-time
  re-validation) in exchange for not disabling the feature entirely.
  Falling back to the round-5 render-skip only when this direct
  resolution also fails to find a binstub at the expected location.
- **A real-subprocess test leaked a detached host/agent pair on every
  successful run** (medium severity): the shared `_kill()` test helper
  read `session.pid` *after* `client.shutdown()`, but host-mode shutdown
  deliberately detaches (leaving both processes alive) and `Session.pid`
  returns `None` once the client is no longer running -- so the helper's
  own cleanup was a silent no-op on the success path, the opposite of a
  flaky-only leak. Fixed by capturing both `host_pid` and `child_pid` from
  `mgr._host_index` *before* calling `shutdown()`, matching the real
  pattern `test_session_host.py`'s own Session-Host-mode tests already
  use.
- Full regression: `tools/run-plugin-tests.py agent-bridge` passing,
  including the new identity-guard unit tests (skipped on Windows, where
  this POSIX-only code path doesn't run) and the same-cell-resolution
  tests.

### 2026-10-02 -- Kickoff
- Carved from a direct operator follow-up to the just-archived
  `ambient-guidance-navigability` effort: the operator clarified the
  Phase 7 mechanism's intended role is a reprioritization (local-render
  primary, checked-in fallback-only), not merely a supplementary patch.
  Confirmed scope with the operator (vision reframing as described;
  `agent-bridge` wiring scoped to local/command spawn path first,
  container/codespace/SSH as named follow-on slices; no other pre-session
  boundary flagged). Effort created, premise captured.
- **PR #4926 review caught two real issues, fixed same-session:**
  - This worktree's own `agent-worktrees` lifecycle wiring rendered 7 real
    `.local.instructions.md` siblings for this repo's own consumed
    sources (the exact dirty-tree gap the operator flagged live, in
    conversation, independently of this PR) -- and since this repo had no
    `.github/instructions/.gitignore` yet, `git add -A` picked them up and
    committed them. Untracked them (`git rm --cached`) and added the
    ignore rule here too (also landing separately via #4930, found and
    fixed as its own atomic pre-existing-issue commit the moment the gap
    was identified; a private downstream consumer repo with the same
    gap got the equivalent fix too, outside this repo's own history).
  - The Plan's own "local/command" framing was wrong: `target.type ==
    "command"` is a generic spawn-command shape that also covers
    Codespaces, containers, elevated relays, and other providers, often
    with no `target.cwd` at all -- not a reliable proxy for "same
    filesystem as the daemon." Narrowed Phase 2's eligibility to
    `target.type == "local"` only, added an explicit negative-proof item
    for non-local target types, and replaced the "never gates/delays the
    spawn" claim (internally contradictory for a synchronous pre-spawn
    render) with a defined short latency budget modeled on this plugin's
    own `SESSIONSTART_MAX_TIMEOUT_S` precedent.
- **PR #4926 review round 2 caught a genuine architectural gap:** the
  existing precedence mechanism prefers a local sibling purely by
  existence, with no freshness check -- so a sibling left by an earlier
  *successful* render can outrank a *newer* checked-in projection on a
  later boot where no pre-session render succeeds. This was always true
  under Phase 7's original framing too, but this effort's reframing
  (declaring local-render primary) makes it load-bearing rather than a
  cosmetic edge case: a "fallback" a stale leftover can silently shadow
  is not actually safe. Added an explicit Phase 1 Plan item (a marker-
  timestamp comparison the preamble/catch-all directive text must apply,
  not "prefer local merely because it exists") and a matching Validation
  Plan item -- this is now real code work for Phase 1, not pure prose.
