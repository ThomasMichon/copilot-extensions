# Worktree Manager — Out-of-Plugin Control Plane (installer · configurator · picker)

- **Slug:** `worktree-manager-control-plane`
- **Repo:** copilot-extensions (control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-17
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352)
  (remaining Worktree Manager work — adoption/discovery, visual manager, presets)
- **Sub-issues:** [#355](https://github.com/ThomasMichon/copilot-extensions/issues/355)
  (prerequisite provisioning),
  [#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
  [#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)
  (configurator: adoption + per-plugin config),
  [#1478](https://github.com/ThomasMichon/copilot-extensions/issues/1478)
  (manual mux restoration for unreachable active sessions),
  [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
  (relocate Mux + AHP execution mechanics out of agent-worktrees)
- **Vision:** **vision-closing** against three already-stated visions (no
  revision needed to close their delta vs. reality; the recent
  `session-hosting` split narrowed which of these visions govern the
  Mux/AHP items):
  - [`visions/installer`](../../../visions/installer/README.md) —
    §*Features*/`optional-worktree-agent-control-plane`,
    `bare-invocation-launches-configurator`, `visual-configurator`,
    `one-line-bootstrap`, `core-install-via-real-flow`, `self-updating`;
    §*Behaviors*/`out-of-plugin-delivery`,
    `control-plane-is-optional-plugins-are-self-sufficient`,
    `knows-the-plugins-without-coupling-to-them`.
  - [`visions/picker`](../../../visions/picker/README.md) —
    §*Features*/`front-door-entry`, `first-run-onboarding-entry`,
    `decision-support-before-cost`, `programmatic-parity`;
    §*Behaviors*/`render-derive-not-own`, `live-not-snapshot`,
    `graceful-capability-scaling`, `renderable-and-assertable-headless`.
  - [`visions/session-hosting`](../../../visions/session-hosting/README.md) —
    Concepts/*Session-host provider* (Mux presentation and AHP backend as
    composable axes, currently both owned by the Worktree Manager); the
    matching Non-Goal in
    [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
    that agent-worktrees carries no provider-specific config union.
  - Parent: [`visions/agent-fabric`](../../../visions/agent-fabric/README.md).
- **Reality docs:** [`worktree-manager/README.md`](../../../worktree-manager/README.md) ·
  [`plugins/agent-worktrees/docs/engine-picker-contract.md`](../../../plugins/agent-worktrees/docs/engine-picker-contract.md) ·
  [`plugins/agent-worktrees/docs/picker.md`](../../../plugins/agent-worktrees/docs/picker.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../../plugins/agent-worktrees/docs/architecture.md)

## Guiding Intent

Deliver the **Worktree Manager** — the single, standalone, **out-of-plugin** app
that (1) **bootstraps** a bare machine into a working harness, (2) **configures,
validates, updates, and repairs** it, and (3) serves as the **optional worktree-
and agent- control-plane** (picking, launching, and managing agent sessions). The
central architectural move this effort tracks is **extracting the interactive
Picker out of the `agent-worktrees` plugin** and re-homing it in the Manager,
where it belongs — while keeping the plugins **fully self-sufficient without it**.

Why out-of-plugin: a plugin is inert until a session launches, so the code that
must *guarantee* the plugins' prerequisites cannot itself be one of those inert
plugins. The Manager is the one piece that must work **before** the plugins do,
and it is fetched and run as its own payload rather than through the plugin pipe.

The end-state a user should see: running a project's bare binstub with no
arguments **hands off to the Manager's control-plane** when it is installed (the
interactive Picker); when the Manager is **absent**, the binstub shows a
trustworthy **install/onboarding trigger** rather than silently substituting an
in-plugin surface; and any `<project> <verb>` invocation continues to run
**headless** against the plugin engine, unaffected. The Picker lives in exactly
one place — the Manager — and reads worktree state **only** across the process
boundary (`agent-worktrees --json`), owning no worktree logic of its own.

## Context

The Manager already exists and is being built out in phases (see
[`worktree-manager/README.md`](../../../worktree-manager/README.md)): the
out-of-plugin skeleton, a dependency-free plugin-knowledge catalog, and
prerequisite detection + core-install driving are in place, alongside read-only
harness state views (`projects` / `repos` / `worktrees` / `plugins`).

The Picker extraction is underway on **both** sides of the process boundary:

- **Manager side.** `worktree-manager/src/worktree_manager/picker_app.py` is a
  Textual Picker that reaches worktree data **only** through
  `engine_client` → `agent-worktrees … --json`. It imports nothing from the
  plugin, takes an **injected source** so live/fixture/demo data render
  identically, and offers a headless SVG capture for golden checks. This is the
  surface the plugin's still-bundled `picker_tui` is being **retired in favour
  of**.
- **Plugin side.** The bare-invocation **seam** in the `agent-worktrees` binstub
  resolves a no-args launch to: the out-of-plugin Manager when a **usable**
  `worktree-manager` is on `PATH` (gated by a fast `--version` health probe so a
  stale/incompatible stub can never capture the seam), else the **still-bundled**
  Picker while it ships, else the **install trigger** once the bundled Picker is
  retired. The engine ↔ Picker `--json` contract is pinned so the Manager can
  degrade gracefully against an older engine.

This effort is the **single coherent home** that ties the Manager-build issues
(#352 / #355 / #356 / #357) to the Picker-extraction and seam work, and traces
all of it to the `installer` and `picker` visions. It records **delta-closure
state only** — the visions themselves state the target and are not edited to log
progress.

## Plan

Phases are ordered by dependency, not calendar. Checked items are already
realized in `main`; unchecked items are the remaining delta.

### Phase 0 — Out-of-plugin skeleton (Done)
- [x] Standalone `worktree-manager/` payload, delivered **outside** the plugin
      pipe; versioned install slot + `current-version` marker +
      `~/.local/bin/worktree-manager` binstub; one-line bootstrap
      (`bootstrap.{ps1,sh}`); user-level source override (`config.toml`,
      `worktree-manager source`). Closes installer §`out-of-plugin-delivery`,
      §`one-line-bootstrap`, §`self-updating` (bootstrap/self-install slice).
- [x] **Bootstrap prerequisite auto-provisioning (git-optional).** The one-liner
      no longer hard-fails on a bare machine: `uv` is auto-installed user-local
      (no admin) when missing (session `PATH` amended; restart prompted when it
      can't), and `git` is installed best-effort where a package manager exists,
      else the payload is fetched as a **GitHub codeload tarball** so the bootstrap
      never dead-ends without `git`. The same git-optional fallback is mirrored in
      `self_update` (`manager_tarball_url` + `_fetch_via_tarball`), so updates work
      git-lessly too. Closes installer §`prerequisite-provisioning`,
      §`restart-aware`, §`legible-and-consent-driven`, and completes
      §`one-line-bootstrap` (bare machine, no pre-installed harness tooling).
      Shipped in worktree-manager `0.1.0-dev13`.

### Phase 1 — Plugin-knowledge model (Done)
- [x] Dependency-free catalog of the harness plugins (what exists, what a repo can
      enable) with no coupling to the plugins themselves. Closes installer
      §`knows-the-plugins-without-coupling-to-them`.

### Phase 2 — Prerequisites & core install (Done — #355)
- [x] Detect baseline prerequisites, plan/provision the missing ones
      (restart-aware, idempotent), and **drive the harness's own** `agent-worktrees`
      core install by locating and calling its real `install.{ps1,sh}` — never
      reimplemented. `doctor` (read-only) / `setup` (dry-run by default, `--apply`).
      Closes installer §`prerequisite-provisioning`, §`core-install-via-real-flow`,
      §`idempotent-and-re-runnable`, §`restart-aware`.

### Phase 3 — Extracted Picker over the engine boundary (In progress)
- [x] `picker_app.py` Textual Picker reads live worktrees **only** via
      `engine_client` → `agent-worktrees --json`; owns no worktree state; injected
      source (live/fixture/demo); headless SVG capture. First state-view slice
      (`worktrees`) shipped.
- [x] Pin the engine ↔ Picker `--json` contract
      ([`docs/engine-picker-contract.md`](../../../plugins/agent-worktrees/docs/engine-picker-contract.md));
      client tolerates an older engine by degrading a request rather than failing.
- [ ] Bring the Manager Picker to **feature parity** with the bundled Picker:
      full worktree list interaction (filter · sort · select), resume/join/create
      actions, multi-machine at-a-glance, session/PR status columns. Closes picker
      §`front-door-entry`, §`decision-support-before-cost`, §`programmatic-parity`,
      §`render-derive-not-own`, §`live-not-snapshot`.
- [x] Add an engine-owned **manual mux restoration** operation for a worktree
      whose bound Copilot process remains live but unreachable after its terminal
      or mux wrapper disappears. The engine must refuse an existing live mux or
      ambiguous owner, reuse the guarded reclaim path, and resume the same
      persisted session through the normal mux launcher. Expose the operation to
      both Picker implementations over the JSON process boundary; neither UI owns
      process discovery or termination policy. Tracks #1478 and closes
      agent-fabric §`recover-not-lose` plus picker §`programmatic-parity`.
- [ ] Add an optional same-machine **AHP session backend** for create and resume
      actions. agent-worktrees creates the exact managed worktree and remains the
      lifecycle authority; the backend creates or reattaches one durable hosted
      session at that path, records a typed binding, and launches any visible mux
      pane as a hard-bound client attachment. Closing the client detaches without
      ending the hosted session, unavailable or mismatched hosts fail closed, and
      finalization requires confirmed disposal or explicit transfer. Tracks #1657
      and closes agent-worktrees §`explicit session binding` plus picker
      §`explicit-launch-target`, §`render-derive-not-own`, and
      §`programmatic-parity`.

### Phase 3b — Relocate Mux + AHP execution mechanics out of agent-worktrees (Planned — #2062)
- [ ] Move the AHP session backend (`agent_worktrees/ahp_backend.py`, the
      `session_backend`/`is_ahp` config schema, and the branches it threads
      through `__main__.py`, `tracking.py`, `finalize.py`, and
      `config_dropins.py`) out of the `agent-worktrees` plugin. Per the
      corrected [`session-hosting`](../../../visions/session-hosting/README.md)
      vision, AHP is a near-term concern of the **Worktree Manager**
      control-plane app, not a config mode of agent-worktrees and not a
      permanent alternative to Mux — an AHP-hosted session may still be
      Mux-wrapped for terminal presentation. The #1657/#1998 slice shipped the
      right *behavior* in the wrong *location*; this item is the architecture
      correction, not new capability.
- [ ] Relocate Mux launch/reattach/remux mechanics
      (`launch-session.{sh,ps1,cmd}`, `cmd_remux`) from `agent-worktrees` to the
      Worktree Manager, consistent with the same vision. agent-worktrees keeps
      only a bounded, provider-id-plus-opaque-blob execution-leg record; it
      never owns a typed union with one field set per hosting technology.
      Reuses the #1478/#1491 remux design's safety invariants (refuse an
      existing live mux or ambiguous owner) under the new ownership boundary.
- [ ] Update the Worktree Manager Picker to select Mux presentation and/or the
      AHP backend independently per launch/resume/create action, rather than
      assuming exactly one of them.
- [ ] Keep both mechanics fully functional through the relocation — this is a
      location and ownership change, not a behavior regression; existing
      worktrees with a recorded `session_backend` binding must keep resolving
      correctly against the relocated code.

### Phase 4 — Bare-invocation seam & handoff (Plugin side landed; end-state pending)
- [x] Plugin binstub seam resolves a no-args launch to a **usable** Manager on
      `PATH` (health-probed), else the still-bundled Picker, else the install
      trigger; a stale/incompatible `worktree-manager` stub can never capture the
      seam. Realizes installer §`bare-invocation-launches-configurator`,
      §`control-plane-is-optional-plugins-are-self-sufficient`.
- [ ] Onboarding polish for the **absent-Manager** path: the install trigger reads
      as a **guided first-run onboarding**, not an error, and points at the
      trustworthy bootstrap. Closes picker §`first-run-onboarding-entry`, installer
      §`onboards-from-empty-gracefully`.

### Phase 5 — Configurator: adoption, discovery, per-plugin config (Planned — #356 / #357)
- [ ] First-harness-repo adoption + repo discovery/registration; edit config the
      harness already reads (link a knowledge repo, per-plugin config, machine &
      connectivity). Closes installer §`first-harness-repo-adoption`,
      §`repo-discovery-and-registration`, §`machine-and-connectivity-config`,
      §`repo-plugin-enablement`.
- [ ] Visual configurator surface (beyond today's read-only state views). Closes
      installer §`visual-configurator`.

### Phase 6 — Retire the bundled Picker (Planned — the operator-visible end-state)
- [ ] Once the Manager Picker reaches parity (Phase 3), remove the in-plugin
      Textual `picker_tui`. The seam's fallback then flips **automatically**
      (detected by the absence of the `picker_tui` package): with no Manager
      installed, a bare launch surfaces the **install trigger** instead of any
      in-plugin Picker. This is the behavior a user currently expects but does not
      yet get, because the bundled Picker is deliberately retained until parity.

### Phase 7 — Health, updating & presets (Ongoing)
- [ ] `doctor`/validation breadth, plugin updating & alignment, and
      git-referenced presets. Closes installer §`health-doctoring-and-validation`,
      §`plugin-updating-and-alignment`, §`git-referenced-presets`.

### Phase 8 — Reconcile deferred backlog

- [ ] Accept manager and worktree-control candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate.
- [ ] Revalidate accepted technical scope against the current installer, picker,
      and engine contracts; return obsolete or unsafe candidates for explicit
      disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
      extending this plan before implementation when necessary.
- [ ] Keep configuration examples synthetic and repository-neutral.

## Validation

- **Headless render + golden checks.** The Manager Picker's `capture_svg` renders
  with no terminal, so list/interaction states are asserted as fixtures — closes
  picker §`renderable-and-assertable-headless`, §`programmatic-parity`.
- **Contract conformance.** Exercise the `--json` engine verbs the Picker depends
  on against both a current and an older engine to prove graceful degradation.
- **Mux restoration safety.** Prove the recovery verb refuses attached/live-mux
  and ambiguous-owner states, performs no mutation in preview mode, retires only
  a confirmed unreachable no-mux owner, and launches exactly one mux-wrapped
  resume of the selected persisted session.
- **AHP backend lifecycle.** Prove exact-path `target=workspace` creation,
  deterministic hard-bound reattach without an anchor/default session, durable
  zero-client state, fail-closed host/version mismatch, and a finalization barrier
  for live or unknown hosted sessions.
- **Clean-room / fresh-box.** Bootstrap → provision → core-install → bare-launch
  handoff exercised on a disposable fresh machine (the repo's clean-room rig),
  including the absent-Manager onboarding path and the stale-stub health-probe
  rejection.
- **Non-agentic + idempotent.** `setup` is dry-run by default and re-runnable;
  re-running the bootstrap one-liner is version-gated (a no-op when current).

## Coordination

`copilot-extensions` is public and may be driven from more than one private
control repo. **[#352](https://github.com/ThomasMichon/copilot-extensions/issues/352)
is the shared coordination token** for the remaining Worktree Manager work; claim
a slice there (comment/assign) before starting, and land changes serially through
the PR-required `main`. Downstream private plans may **link to** this effort and
its issues; the public artifacts stay self-contained and general-purpose.

## Journal

- **2026-08-17** — Effort authored to give the Worktree Manager rework a single
  coherent home in-repo, tying the Manager-build issues (#352 / #355 / #356 /
  #357) to the Picker-extraction and bare-invocation seam, and tracing both to the
  `installer` and `picker` visions. Recorded current reality: Phases 0–2 landed;
  Phase 3 (extracted Picker over the engine boundary) and Phase 4 (plugin seam)
  substantially in place; Phase 6 (retire the bundled Picker → install-trigger
  end-state) still pending parity.
- **2026-08-17** — Bootstrap prerequisite auto-provisioning (git-optional).
  Reworked `bootstrap.{ps1,sh}` to provision `uv` (user-local, restart-aware) and
  best-effort `git`, with a GitHub-tarball fallback when `git` is absent; mirrored
  the tarball fallback into `self_update` (`manager_tarball_url` +
  `_fetch_via_tarball`). Added derivation + fallback tests (132 green). Closes the
  §one-line-bootstrap × §prerequisite-provisioning × §restart-aware delta at the
  bootstrap entry — the app-level provisioning (#355) was already done; this
  closes the bootstrap-entry tail. worktree-manager `0.1.0-dev13`.
- **2026-08-17** — Clean-room validated the bootstrap on a fresh box. Added the
  `worktree-manager-bootstrap` Tier-P scenario (`tools/clean-room/`): on the
  **pristine** image (no uv) the published one-liner self-provisions uv (0.12.5),
  publishes `current-version` + slot, deploys the `~/.local/bin/worktree-manager`
  binstub, and the binstub runs on a stock login PATH — **7 passed, 0 failed**.
  Turns the unit-tested behavior into a hard fresh-box PASS. The git-absent
  tarball fallback stays unit-tested; a no-git image variant is a noted follow-up.
- **2026-08-31** — Added #1478 to Phase 3: a single engine-owned manual
  mux-restoration operation, surfaced by both Picker implementations. The design
  explicitly restarts/resumes persisted session state instead of attempting to
  reparent an arbitrary live Windows console process into a new ConPTY.
- **2026-08-31** — Implemented the #1478 slice. `agent-worktrees remux` now
  keeps the live `reptyr` adoption path on Linux/WSL and adds a guarded Windows
  reclaim-before-resume path that refuses an existing mux or ambiguous owner.
  Both Picker implementations expose **Restore** when an unreachable bound
  process has a known head session; the standalone Manager prepares recovery
  through the JSON engine boundary, then launches through the installed project
  binstub so the normal mux wrapper is restored.
- **2026-09-03** — Accepted #1657 into Phase 3 as the optional same-machine AHP
  session-backend slice. The reviewed contract keeps worktree and finalization
  authority in agent-worktrees, treats mux clients as detachable presentation,
  and requires exact-path binding plus fail-closed lifecycle handling.
- **2026-09-04** — Corrected course after #1657/#1998 landed the AHP backend
  *inside* `agent-worktrees` (`ahp_backend.py` + a `session_backend.is_ahp`
  config branch threaded through `__main__.py`/`tracking.py`/`finalize.py`/
  `config_dropins.py`). The new `session-hosting` vision (#2054) establishes
  that agent-worktrees is pure durable agency state and never a home for a
  provider-specific config union; a follow-up correction clarified that Mux
  (terminal presentation) and AHP (session backend) are **composable, not
  mutually exclusive** — an AHP-hosted session may still be Mux-wrapped — and
  that both currently belong to the **Worktree Manager** control-plane, not to
  agent-worktrees and not to a brand-new fourth plugin. Added Phase 3b to track
  the physical relocation as an architecture correction (same behavior,
  corrected ownership), filed as #2062.
