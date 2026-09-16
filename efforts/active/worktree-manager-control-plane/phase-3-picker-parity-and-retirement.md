# Phase 3/6 — Reconcile the diverged Picker implementations, then retire the bundled one

- **Parent effort:** [`README.md`](README.md) § Phase 3 (parity) / § Phase 6 (retirement)
- **Tracks:** [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) (coordination token,
  slice claimed here) · aperture-labs #6764 (duplicate-implementation architecture decision,
  **recorded 2026-09-12**: Worktree Manager is the canonical owner of the Picker/Mux UI;
  `agent-worktrees` keeps worktree lifecycle only) · aperture-labs #6914 (two-way hook-contract
  follow-up, out of scope here) · [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117)
  (the smaller NF5-5 opt-out-toggle cleanup this supersedes)
- **Governing vision:** [`visions/picker`](../../../visions/picker/README.md) §Behaviors/
  `programmatic-parity`, `renderable-and-assertable-headless`; the already-recorded Phase 6
  end-state in the parent README.
- **Origin:** the `agent-worktrees` plugin's bundled Picker (`plugins/agent-worktrees/src/
  agent_worktrees/picker_tui/`) was transplanted into Worktree Manager's `production_picker/`
  in **#1244**, carrying forward the full NF1–NF5-5 native-focus migration (native modals,
  focus bridge, native `OptionList` data body with sticky headers + clickable checkboxes —
  see `plugins/agent-worktrees/docs/architecture.md`'s NF sections for the original design
  record). Since that transplant, **both copies have kept receiving independent commits** —
  exactly the failure mode aperture-labs #6764 was filed against (that issue's own example:
  an env-sanitization fix landed only on the Worktree Manager side via #2359/#2384, later
  parity-restored by #2391). The decision on #6764 makes Worktree Manager canonical, but
  does not by itself guarantee every agent-worktrees-only fix actually made it across before
  the bundled Picker is deleted.
- **Status:** Planned — audit below is a snapshot from `git log` on 2026-09-15; re-run it
  before executing Step 1 in case more commits have landed on either side since.

## Step 0 — Audit: what is old-only / new-only since the transplant

Compare `plugins/agent-worktrees/src/agent_worktrees/picker_tui/engine.py` against
`worktree-manager/src/worktree_manager/production_picker/picker_tui/engine.py` (and the
sibling files in each `picker_tui/` directory — this audit only inspected `engine.py`;
repeat for `data_local.py`, `data_ssh.py`, `roster.py`, `capture.py`, etc., where #6764's
example bug actually lived in `data_ssh.py`/env-sanitization, not `engine.py`).

As of 2026-09-15, `git log --oneline -- <path>` on each `engine.py` shares a long common
history back through the transplant point, then diverges. Commits touching the
**agent-worktrees** `engine.py` with no equivalent on the Worktree Manager side (verify by
title/intent, not path, since paths differ):

- `4b759a20a` — Preserve cached Picker rows during stream refresh (#1938)
- `cc43049d4` — fix silent steer Confirm/Save click misses (#2453/#2499)
- `906d735f5` — fix crash + None env label when local identity is uncached (#2589)
- `5bd8bd149` — Port agent-worktrees picker card draft semantics (#2590) — note the
  direction: this is itself a cross-port, so check whether it duplicates something the
  Worktree Manager side already has under different history.

Commits touching the **Worktree Manager** `engine.py` with no equivalent on the
agent-worktrees side:

- `7c7663d4b` — Add AHP Picker launch option (#2355) — likely fine to leave unported:
  AHP is explicitly Worktree-Manager-only territory per Phase 3b/the session-hosting
  vision, not a capability the retiring bundled Picker needs.
- `39e9b8cc0` — give Confirm/Save/Reset their real distinct semantics (#2586) — **check
  whether this supersedes `cc43049d4` (#2453/#2499) above** rather than needing it ported;
  they may be two different fixes to the same underlying steer-form interaction bug, in
  which case the newer/more complete one wins and the other is moot.

**Do this step first, freshly, before anything else** — commits will have moved on since
this snapshot. For each old-only commit that is NOT superseded by newer Worktree-Manager-side
work, port its fix (adapted to the Worktree Manager engine's structure, which reads over the
`--json` engine boundary rather than owning data directly — this is not always a verbatim
copy the way the NF5 transplant was).

## Step 1 — Close the remaining Phase 3 parity checklist item

Per the parent README, Phase 3's only unchecked box is:

> Bring the Manager Picker to feature parity with the bundled Picker: full worktree list
> interaction (filter · sort · select), resume/join/create actions, multi-machine
> at-a-glance, session/PR status columns.

Audit which of these are already covered (the NF5 transplant likely carried the native list
`filter · sort · select` interaction model wholesale) versus genuinely still missing. Update
this checklist item to `[x]` only once verified true end-to-end (manual soak + the Worktree
Manager Picker's own golden/capture tests), not merely because the code exists.

## Step 2 — Execute Phase 6: delete the bundled Picker

Once Step 1's parity box is honestly checked:

1. Delete `plugins/agent-worktrees/src/agent_worktrees/picker_tui/` (the whole package) and
   its test tree (`plugins/agent-worktrees/tests/test_picker_tui.py`,
   `test_picker_capture.py`, `tests/goldens/picker/`, `scripts/picker-snapshot/` if it isn't
   also used by anything else).
2. Confirm the Phase 4 bare-invocation seam's documented behavior actually holds with the
   package absent: no Manager on `PATH` → install trigger (not a crash, not a silent
   fallback to dead code). Add/keep a regression test asserting this.
3. Remove now-dead config surface: the `AGENT_WORKTREES_PICKER_NATIVE_LIST` env var and any
   other picker-only toggles/docs in `plugins/agent-worktrees/docs/` (`architecture.md`'s NF
   sections become historical record — do not delete the doc, but add a closing note that
   the code it describes has moved to `worktree-manager/` and was later deleted from here).
4. Update `docs/tools.md` / `external-repos.yaml` / any facility docs (in `aperture-labs`,
   cross-repo) that still describe launching a Picker via `agent-worktrees` directly.
5. Close [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117) — its
   opt-out-toggle scope is now moot; the whole module is gone, not just its toggle.

## Step 3 — Validate

- Full `agent-worktrees` plugin suite green with the package gone (no stale imports).
- Full `worktree-manager` plugin suite green (its own picker_tui tests + the ported fixes
  from Step 0).
- `ruff check --select F,E9` clean on both.
- `python tools/check-install-contract.py` (agent-worktrees side) still reports the correct
  plugin count with `picker_tui` gone.
- A live bare-invocation smoke test on a machine with no Worktree Manager installed:
  confirm the install-trigger path, not a crash.

## Notes for whoever executes this

- This repo now uses **`pr-self-merge`**, not direct push (confirmed 2026-09-15 via
  `copilot-extensions get pr-profile`) — land each step as its own reviewed, self-merged PR,
  not a direct push to `main`.
- Re-check aperture-labs #6764/#6914 for any updates before starting — the hook-contract
  design in #6914 may have implications for exactly when it's safe to delete the bundled
  Picker (e.g. if anything still depends on the old binstub-seam behavior during a
  transition window).
- Per the parent effort's Coordination section: **re-confirm no one else has claimed this
  slice on #352 before starting**, and post progress there as you go.
