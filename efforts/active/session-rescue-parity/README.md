# Session-Rescue Parity (Containers <-> CodeSpaces)

- **Slug:** `session-rescue-parity`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs (see Coordination)
- **Created:** 2026-09-25
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends `visions/plugins/agent-containers/README.md`
  §`rescue-before-destructive-replacement` (generalizing it to an
  on-demand, non-destructive trigger independent of replacement — see
  Context; periodic scheduling itself stays a consumer concern) and
  `visions/plugins/agent-codespaces/README.md` §`telemetry-grade-session-capture`
  (generalizing "captured on teardown/recycle" to "capturable on demand
  while leased/running"); touches `visions/venue-parity/README.md` only as a
  boundary note (see Context — this is deliberately NOT a venue-parity
  extension).
- **Umbrella issue:** [#3642](https://github.com/ThomasMichon/copilot-extensions/issues/3642)
- **Sub-issues:** _TBD_

## Guiding Intent

`agent-containers` just closed a real gap
(`ThomasMichon/copilot-extensions#3574`): its restricted-fleet Copilot
session-state rescue was only ever triggered by destructive replacement
(stop/remove), so a single shared, deliberately-never-recycled container
never surrendered its session evidence at all. The fix added a non-destructive
`agent-containers rescue-capture <fleet>` verb — reusing the existing
admission/liveness gating, callable on demand or on any caller-chosen
schedule. **Note:** `#3574` itself adds only the verb + gating + capture/
publish path in this repository; the periodic systemd timer that actually
calls it on a schedule is downstream consumer configuration (this repo has
no scheduling of its own for it) — see the same clarification repeated at
each Plan/Context reference below.

`agent-codespaces` has the **identical shape of gap**. Its
`sync_codespace_sessions()` (in `sessions.py`) already pulls
`~/.copilot/session-state` non-destructively over SSH and pushes it into the
same agent-logger hub — but every call site is tied to a lifecycle
transition (`stop`/`finalize`/`delete`, the reclaim callback). Since
`pool.py` treats CodeSpaces as a **long-lived, reusable, budget-bounded
pool** (leased, not necessarily recycled per task), a CodeSpace held by a
long session accumulates the exact same "no periodic evidence extraction
until teardown" exposure agent-containers just fixed.

This effort's goal: **align the two providers' rescue behavior** so a
periodic, non-destructive "capture without disturbing the venue" capability
exists in both, sharing what is genuinely common in source (vendored,
byte-identical, per the repo's existing `libs/<lib>` pattern — e.g.
`ssh-manager`, `credential-relay`, `config-migrate`, already vendored into
both `agent-containers` and `agent-codespaces`) rather than two independently
drifting implementations. Each provider keeps what is genuinely
venue-specific (containers: `docker exec` + the restricted fleet's
admission-hold/lease/policy gating; CodeSpaces: SSH + the lease pool's
in-use signal) — see Context for the concrete split.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Effort owner (rotates per phase) | Drives the active phase; see the effort's Journal for current owner and phase | This repo's normal worktree/PR flow — no fixed venue |

_Later phases (per-provider rollout, vision reconciliation) may be split
across further worktrees/sessions as independent per-plugin PRs; see
Coordination._

## Coordination

- **Topology:** independent per-slice PRs (one per phase below), not a shared
  feature branch — each phase is independently reviewable and mergeable.
- **Host (owns PRs):** effort owner for the phase in progress (see Journal).
- **Delegates:** none yet; a later phase may split containers-side vs.
  codespaces-side work across separate sessions once Phase 1's design is
  agreed.
- **Handoff:** each phase's PR merges before the next phase starts; the
  Journal records phase handoffs.

## Context

### Where this came from

Landed same-session as this effort's creation:
`ThomasMichon/copilot-extensions#3574` (`agent-containers rescue-capture
<fleet>`), driven by a downstream consuming project's own containerized
always-on reviewer service hitting exactly this gap. That work reused
`_restricted_member_action`'s exact admission/idleness/policy/liveness
gating with the destructive action step skipped (`action=None`) and added a
`captured: list[str]` result field — this repository's own scope. The
downstream consumer separately wired a periodic host-side timer plus a
paired publish step calling this new verb on a schedule; that scheduling
lives entirely in the consumer's own repo, not here. Full detail on the
`#3574` change itself lives in its own review history, which caught two
real correctness gaps worth re-checking against any codespaces port: never
unpause a paused venue just to capture it, and a non-running/absent venue
must defer with the same message every code path produces, not a second
ad-hoc one.

### What already exists on the codespaces side

`plugins/agent-codespaces/src/agent_codespaces/sessions.py`:
`sync_codespace_sessions()` — pulls `~/.copilot/session-state` (+
`session-store.db*`) over the multiplexed SSH connection via `tar czf |
base64` (sentinel-wrapped, robust to interleaved banner/log text), then
shells out to `session-sync push --source <staging> --machine
.codespaces/<name>` (agent-logger). It is already a clean, reusable,
non-destructive function — genuinely closer to done than agent-containers
was before Phase 3.

Every current call site (`__main__.py`'s `_cmd_delete`/`_cmd_finalize`/
`_cmd_stop` and their JSON-modal variants, the ordinary prune path and
`_reclaim_for_quota`'s total-limit path (both also in `__main__.py`), and
`claim_provider_cli.py`'s reclaim callback) is a lifecycle transition. There
is no standalone "pull now, keep the CodeSpace running" CLI verb, and no
periodic timer.

### The one real safety gap codespaces has that containers doesn't

Containers' `rescue-capture` refuses to capture during an active session: it
probes `~/.copilot`'s `inuse.*.lock` marker files (a **generic Copilot CLI
session-state convention**, not container-specific — see
`agent_containers/replacement.py`'s `probe_session_liveness`, which greps
`inuse.*.lock` under each session's dir and backstops the PID against
`/proc/<pid>` + a process/cmdline check) before ever pulling/capturing.
`sync_codespace_sessions()` pulls unconditionally today — safe for a
destroy-time pull (nothing to preserve after), but risky for a periodic
capture that must not snapshot a session mid-write.

**The portable piece:** the `inuse.*.lock` + `/proc/<pid>` liveness-probe
technique is a property of the Copilot CLI's own session-state layout, not
of Docker. The same script (or a parameterized close relative) can run over
SSH inside a CodeSpace exactly as it runs over `docker exec` inside a
container. This is the concrete candidate for the shared vendored piece
(see Plan Phase 2).

### What is NOT shareable (stays venue-specific)

- **Transport.** `docker exec` (containers) vs. SSH via `ssh-manager`'s
  `ConnectionManager` (codespaces) — already abstracted at the venue-parity
  layer for *trusted* venues, but containers' `rescue-capture` targets the
  **restricted** fleet specifically, which venue-parity explicitly places
  out of its own scope (see `visions/venue-parity/README.md`: "Parity
  applies to *trusted* venues... An untrusted/restricted container is a
  different mode... out of the parity scope by design"). This effort is
  therefore a **peer of venue-parity for this one capability**, not an
  extension of it — noted structurally, not layered inside venue-parity's
  trusted-only contract.
- **Admission model.** Containers' restricted fleet uses a heavyweight
  deploy-hold + `active_session_admissions` + effort-lease + restricted-
  policy-validation gate (`_restricted_member_action`) before even probing
  liveness — necessary because a restricted container's identity/state can
  itself drift or be attacked. CodeSpaces have no restricted-policy surface
  to validate, but their holder model is **not** simply single-tenant: a
  CodeSpace can be held by a live local lease (`lease.py`), a `#897`
  worktree claim, a cross-machine L2 (Git-ref) lease overlay with no
  local lease at all, or a live display-name beacon — `pool.py` derives
  `IN_USE` from any of these four (the L2 overlay is documented as the
  beacon's intended atomic successor, but the beacon signal is still
  checked today, not yet retired).
  Porting the *liveness probe* does not require porting the *admission-hold*
  machinery, but a capture verb's own authorization check must account for
  all four holder shapes (see Plan Phase 1's explicit lease/claim-ownership
  decision), not just `get_lease()`.
- **Destination push mechanism.** Containers publish via `agent-containers`'
  own rescue store (`$STATE_DIR/rescues/`) + `session-sync rescue-push`
  (hash-verified, allowlisted, capture-ID-pinned). CodeSpaces already push
  directly via `session-sync push --source <staging> --machine
  .codespaces/<name>` with no intermediate rescue-store layer. Whether to
  unify these two publish paths (both eventually go through agent-logger)
  is an open design question for Phase 1, not assumed here either way.

### The vendoring mechanism to reuse (not invent)

This repo already vendors shared code byte-identically per consuming
plugin — `plugins/<plugin>/libs/<lib>/`, auto-discovered (no registry file)
and guarded by `tools/check-vendored-libs-sync.py` (`--list` confirmed the
current map, 2026-09-25). Six libs are already vendored into **both**
`agent-containers` and `agent-codespaces` today: `ssh-manager`,
`credential-relay`, `config-migrate`, `agent-procutil`, `venue-copilot`, and
`zdd`. (Three more — `dropin-registry`, `plugin-activation`,
`plugin-resolve` — are vendored into `agent-codespaces` but NOT
`agent-containers`; don't assume they're shared without re-checking
`--list`.) Adding a new shared lib is: create
`plugins/agent-containers/libs/<new-lib>/` and
`plugins/agent-codespaces/libs/<new-lib>/` with identical `src/` trees and
matching `pyproject.toml` versions, then in **each** consuming plugin's own
`pyproject.toml` add **both** the distribution name to
`[project].dependencies` (e.g. `"agent-<new-lib>"`) **and** a matching
`[tool.uv.sources] agent-<new-lib> = { path = "libs/<new-lib>" }` entry --
`[tool.uv.sources]` only controls how an *already-declared* dependency
resolves; without the `[project].dependencies` entry too, the vendored
package is never installed into a standalone marketplace environment and
the new imports fail there even though a same-checkout dev run might not
catch it. **`tools/check-vendored-libs-sync.py` does not validate this
wiring** -- it only discovers `libs/<lib>` copies under `plugins/*/libs` and
checks `src/` byte-identity + declared versions between them; it never
inspects either consumer's `[project].dependencies` or `[tool.uv.sources]`.
A dropped `[project].dependencies` entry can pass that guard while a real
standalone install still fails, so the Validation Plan must exercise an
actual fresh install (not just the sync guard) to catch it -- see Phase 2's
Validation Plan item. `docs/patterns/README.md`'s `versioned-runtime`
paragraph documents the same fan-out pattern for a single canonical source
(`libs/versioned-runtime/versioned_runtime.py`) synced by a dedicated tool
(`tools/sync-versioned-runtime.py`) rather than the plain byte-identical
vendored-copy model most `libs/<lib>` use — worth deciding in Phase 1 which
shape fits a shared liveness-probe script better (a plain vendored lib is
likely the right fit; it is small, stable, and has no per-plugin
config surface, unlike `versioned_runtime.py`'s adapter role).

## Request

_(operator, verbatim, 2026-09-25)_ "Could we reuse any architecture in
common here for agent-codespaces? We could also benefit from a sync-as-we-go
pull model from Codespaces, to match Containers. Codespaces are
similarly-isolated, for the most part." Followed by, once the agent
presented the comparison above: "Yes, carve an effort to align these
behaviors to be vendorable (which can now dedupe in source via the
dev-branch vendoring flow) between containers and codespaces, sharing the
best learnings from each."

## Plan

### Phase 1 — Design: what's shared, what's venue-specific, and how it lands

Full checklist + rationale: [phase-1-design.md](phase-1-design.md). Covers:
the async-compatible liveness-probe transport seam, the vendored lib's shape
and dependency wiring (both `[project].dependencies` and
`[tool.uv.sources]`, for both consumers), the publish-path unification
question, CodeSpace lease/claim-ownership semantics for capture, the
snapshot-race mitigation default (accept as documented residual risk,
confirm or revise), the concurrency-lock widening for destructive callers,
the `docs/patterns/README.md` architecture-invariant check, the
periodic-scheduling ownership decision, and vision reconciliation. Ends
with submitting this effort's plan as a PR (this repo's automated-review
gate) before Phase 2 starts.

### Phase 2 — Extract the shared liveness-probe as a vendored lib
- [x] Extract the portable liveness-probe logic from
      `agent_containers.replacement` into the vendored lib decided in
      Phase 1, with the container-specific `docker exec` call factored out
      behind a small injected transport callable per Phase 1's
      sync/async-split decision. `agent-containers`
      itself switches to consuming the vendored copy -- no *behavior*
      change, but its existing tests directly monkeypatch the pre-extraction
      internals (`test_replacement.py` patches `replacement._docker` and
      `replacement.probe_session_liveness` directly, e.g. around lines
      130-137 and 488-501 in the current tree) and will **not** pass
      unchanged once that logic moves behind a transport-injected vendored
      API. Either preserve a thin compatibility wrapper in `replacement.py`
      that keeps the same monkeypatchable seam (`_docker`,
      `probe_session_liveness` remain real, patchable module attributes
      that delegate to the vendored lib), or update every affected test to
      mock the new seam instead -- decide which explicitly, and confirm no
      test coverage is silently lost either way -- this requires wiring
      **its own** `pyproject.toml`'s `[project].dependencies` **and**
      `[tool.uv.sources]` entries for the new lib too (both entries, per
      `CONTRIBUTING.md`'s vendoring guidance and this effort's own Context
      note -- this is not codespaces-only wiring).

      **Done:** new lib `libs/session-liveness-probe/` (distribution
      `agent-session-liveness-probe`, module `session_liveness_probe`)
      owns `build_probe_script()` + `parse_probe_output()`. Chose the thin
      compatibility wrapper: `replacement.py` still defines a real,
      patchable `probe_session_liveness()` and keeps calling its own
      module-level `_docker` (imported from `.lifecycle`, unchanged) --
      it now calls the vendored `build_probe_script()`/`parse_probe_output()`
      instead of inlining the script/parser, and re-exports `SessionLiveness`
      from the vendored lib so `replacement.SessionLiveness(...)` keeps
      working. Zero test edits needed; full `agent-containers` suite passes
      (`tools/run-plugin-tests.py agent-containers --reinstall`: 495
      passed, 4 skipped, 1 pre-existing unrelated WSL-environment failure in
      `test_worktrees_peer.py` confirmed present on `origin/dev` too).
      `pyproject.toml` wired with both `[project].dependencies` and
      `[tool.uv.sources]` entries.
- [x] Vendor the identical copy into `plugins/agent-codespaces/libs/`, wire
      its `pyproject.toml`'s **both** `[project].dependencies` entry and
      matching `[tool.uv.sources]` entry (per Context's clarification --
      `[tool.uv.sources]` alone does not install the package into a
      standalone marketplace environment), and confirm
      `tools/check-vendored-libs-sync.py` passes.

      **Done:** byte-identical copy vendored into
      `plugins/agent-codespaces/libs/session-liveness-probe/`, both
      `pyproject.toml` entries wired, `check-vendored-libs-sync.py` passes
      (11 shared libs in sync), and a real `--reinstall` build of
      `agent-codespaces` (its own full suite, 1353 passed, 13 skipped)
      confirms the fresh-install path -- not just the sync guard -- actually
      resolves the new dependency.

### Phase 3 — CodeSpaces: non-destructive capture verb + liveness gate

Full checklist + rationale: [phase-3-capture-verb.md](phase-3-capture-verb.md).
Covers: the ported liveness gate (never disturbing CodeSpace state to
probe it), the mandatory non-`Available`-state preflight (never reusing
`sync_codespace_sessions()`'s boot-tolerant defaults as-is), the additive,
non-lifecycle-transition CLI verb (every one of the seven existing
destructive-recovery call sites keeps working unchanged), account binding
that requires an explicit caller-supplied account or a confirmed exact
per-name binding -- never `get_codespace_status_with_account()`'s
no-binding fallback scan, which is itself an ambiguous first-match, not an
authoritative answer -- with fail-closed behavior, coordinating capture with
concurrent lifecycle operations (widening `TargetLock`'s held scope past
just the sync sub-step, with an explicit accepted exception for a truly
external actor bypassing this repo's lock entirely), the post-capture
re-validation and explicitly-acknowledged residual snapshot-race risk, and
the full test list (liveness, state, lease/claim, account-binding, race,
lifecycle-contention, and CLI-dispatch coverage).

**Done:** new `agent-codespaces sync-sessions <name>` verb
(`capture_cli.py`) calling `sessions.capture_codespace_sessions()`. That
function: (1) a hard state preflight via
`get_codespace_status_with_account()` that defers on anything but
`Available` without ever connecting; (2) `_capture_hold_reason()` defers on
any of the four holder shapes (local lease, `#897` claim, cross-machine L2,
beacon) except an orphaned claim (owning worktree positively gone, per
`pool._holder_worktree_gone`), which is not treated as a live hold; (3)
account binding requires an explicit `account` or `account_binding.bound_account()`'s
exact per-name binding, never the generic scan -- fails closed otherwise;
(4) the vendored `session_liveness_probe` lib gates the pull both **before**
(defers if not `idle`) and **after** (discards/defers a became-active
capture rather than staging/pushing it) -- the acquire-then-release-
entirely-within-the-pull race remains an explicitly documented residual
risk, per Phase 1's decision. `sync_codespace_sessions()` gained an optional
`lock=` parameter (widening seam): all seven existing destructive call
sites (`_cmd_delete`, `_cmd_finalize`, its JSON-modal variant, `_cmd_stop`,
prune, `_reclaim_for_quota`'s total-limit path, and
`claim_provider_cli.py`'s reclaim callback) now acquire their own
`TargetLock` up front and hold it across their full sync-then-act sequence,
passing it through so a concurrent capture sees `TargetBusyError` and
defers for the whole transaction -- the external-actor exception (e.g. a
human running `gh codespace stop` directly) is called out explicitly, not
silently, exactly as planned. Tests: `test_capture_sessions.py` (hold-reason
per holder shape including the orphaned-claim exception, state preflight,
account-binding fail-closed/explicit-bypass, pre- and post-pull liveness
gating, `TargetBusyError` handling, and a lock-contention regression using a
genuinely different live pid) and `test_capture_cli.py` (CLI-dispatch text +
`--json`, deferred exit code) -- 20 new tests, full `agent-codespaces` suite
(1373 tests) green via `tools/run-plugin-tests.py --reinstall`, `ruff check`
clean on every touched/new file.

### Phase 4 — Periodic trigger for CodeSpaces
(scope set by Phase 1's scheduling-ownership decision)
- [ ] If Phase 1 decided **consumer-owned** (the containers-precedent
      default): this phase becomes documentation only -- record, in this
      repo's own docs (e.g. a short section in `agent-codespaces`'s README
      or the `codespaces-lifecycle` skill), how a consumer wires its own
      periodic trigger against the Phase 3 verb, mirroring how the
      containers-side downstream consumer did it. No new repository-owned
      scheduling code is written under this branch.
- [ ] If Phase 1 decided **repository-owned** (only viable if an existing
      always-running loop was confirmed to cover every relevant venue):
      wire the periodic capture into that already-existing loop; do not
      introduce a new standalone timer/daemon that duplicates a mechanism
      this repo already runs.
- [ ] Validate end-to-end against a real leased CodeSpace, using whichever
      trigger path Phase 1 chose: a capture picks up a real session,
      publishes it, and the CodeSpace's own state (lease, connection) is
      unaffected -- mirroring the container validation's proof that
      `docker ps` uptime was unaffected.

### Phase 5 — Close-out
- [ ] Confirm both providers' capture/publish result-shape fields are
      documented consistently (README/skill docs on both sides) so a
      consumer reading either doesn't need venue-specific tribal knowledge.
- [ ] Journal the final state; mark Status: Done once every Plan/Validation
      Plan item is resolved or transferred.

## Validation Plan

- [ ] Phase 2: `tools/check-vendored-libs-sync.py` passes with the new lib
      listed in both consumers; agent-containers' full test suite
      (`python tools/run-plugin-tests.py agent-containers`) passes after
      the extraction -- with `test_replacement.py`'s direct
      `replacement._docker`/`replacement.probe_session_liveness` monkeypatches
      either still working against a preserved compatibility seam, or
      explicitly updated to mock the new vendored-lib seam instead (per
      Phase 2's decision) -- confirm no coverage was silently dropped
      either way, not just that the suite is green. **Additionally**, since
      the sync guard does not validate
      `[project].dependencies`/`[tool.uv.sources]` wiring
      (see Context), force a genuinely fresh install for both consumers
      (e.g. a from-scratch venv rebuild rather than trusting a cached one --
      `run-plugin-tests.py`'s own `--reinstall`, or an equivalent explicit
      `uv sync`/install dry-run) and confirm the new import actually
      resolves in each, not only that the sync guard is green.
- [ ] Phase 3: agent-codespaces' test suite
      (`python tools/run-plugin-tests.py agent-codespaces`) passes,
      including the new liveness-gate regression test, the
      non-`Available`-state regression test (no boot/connect attempt for a
      Shutdown/Starting/unknown-state CodeSpace), the lease/claim-ownership
      owner/non-owner/orphaned-claim/cross-machine-L2/beacon-only tests, the
      probe-to-pull race tests (a lock held through the final probe MUST
      always be rejected/retried; a lock acquired-then-released entirely
      during the pull is rejected/retried **only if** Phase 1 scoped in a
      mitigation for it -- given the recorded default (accept as residual
      risk), this case is not required to pass and the test suite must not
      assert a guarantee the plan explicitly declined to make), the
      lifecycle-contention regression test (a capture attempted while a
      destructive caller holds the widened `TargetLock` across its full
      sync-then-act sequence must defer, never interleave), the
      account-binding tests (same-name-across-accounts with no binding
      is deferred; same-name-across-accounts with an exact binding
      succeeds pinned to that account; a binding-lookup failure defers;
      none of these fall through to ambient auth or an ambiguous
      first-match), and CLI-dispatch tests.
- [ ] Phase 4: a real leased CodeSpace is captured and published
      end-to-end (mirroring the container-side end-to-end validation
      already proven for `rescue-capture`) — published session readable
      from the same agent-logger hub tree the CodeSpace's own teardown-time
      capture already lands in, and the CodeSpace's lease/connection state
      unaffected before/after.
- [ ] Both providers' module-size guards (`tools/check-module-size.py`) and
      `ruff check` stay clean on every touched file.

## Proposal

_Pending._

## Journal

### 2026-09-25 — Kickoff
- Effort created directly following `ThomasMichon/copilot-extensions#3574`
  (agent-containers `rescue-capture`) landing and being deployed +
  validated end-to-end downstream. Operator asked whether the same
  architecture applies to `agent-codespaces`; comparison above
  (`sync_codespace_sessions()` already exists but is lifecycle-transition-
  only, exactly the gap containers just closed) confirmed it does, plus one
  real difference (codespaces currently has no liveness gate at all before
  pulling). Operator directed carving this effort to align the two
  behaviors and share what's genuinely common via the repo's existing
  vendored-lib mechanism.

### 2026-09-25 — Phase 1 + Phase 2 (handoff pickup)
- Continued from a cross-repo handoff after the effort's Plan merged as
  `#3643`. Recorded every open Phase 1 decision in `phase-1-design.md`
  (transport seam confirmed, plain vendored-copy shape chosen, publish
  paths kept separate, lease/claim defer-on-any-hold decision across all
  four holder shapes, snapshot-race default reconfirmed, lock-widening
  decision recorded for Phase 3, `docs/patterns/README.md` invariants
  #1/#3/#4 confirmed satisfied, scheduling ownership confirmed
  consumer-owned after checking `run_owner_daemon` is per-connection
  idle-shutdown, not an always-on sweep) and reconciled both visions
  (`agent-containers` `rescue-before-destructive-replacement`,
  `agent-codespaces` `telemetry-grade-session-capture`).
- Extracted `libs/session-liveness-probe/` (new vendored lib,
  `agent-session-liveness-probe` / `session_liveness_probe`) from
  `agent_containers.replacement.probe_session_liveness`, splitting the
  transport (`docker exec`, stays in `replacement.py`) from the pure
  script + parser (now shared). Kept `replacement.py`'s existing
  monkeypatchable seam intact -- zero test edits. Vendored the
  byte-identical copy into `agent-codespaces`, wired both plugins'
  `pyproject.toml` dependency + `uv.sources` entries, added a changefile
  (patch/patch). Validated: both plugins' full suites green via
  `tools/run-plugin-tests.py --reinstall` (agent-containers 495 passed/4
  skipped/1 pre-existing unrelated failure; agent-codespaces 1353
  passed/13 skipped), `check-vendored-libs-sync.py` OK (11 libs), `ruff
  check` clean on all touched/new files.
- Next: open the Phase 1+2 PR, drive it through review/merge, then start
  Phase 3 (CodeSpaces capture verb + liveness gate) in a fresh PR.

### 2026-09-25 — Phase 3 (CodeSpaces capture verb + liveness gate)
- Phase 1+2's PR (#3660) merged this same day; started a fresh worktree for
  Phase 3 per the effort's own per-phase-PR Coordination rule.
- Implemented `agent-codespaces sync-sessions <name>` (`capture_cli.py`) and
  `sessions.capture_codespace_sessions()`: hard non-`Available`-state
  preflight (never connects to a non-running venue), `_capture_hold_reason()`
  covering all four holder shapes with the orphaned-claim exception, fail-
  closed account binding (explicit or exact-bound only), and the vendored
  liveness probe gating the pull both before and after (a became-active
  capture during the pull is discarded, not staged/pushed). Added the
  `lock=` widening seam to `sync_codespace_sessions()` and wired it through
  all seven existing destructive call sites so each now holds its own
  `TargetLock` across its full sync-then-act sequence -- closing the window
  Phase 3's own item called out, with the external-actor exception called
  out explicitly in code comments, not silently.
- Fixed one pre-existing test's stubbed lambda that didn't accept the new
  `lock=` kwarg (`test_claim_provider_cli.py`); otherwise zero regressions
  across the full existing suite.
- Validated: 20 new tests (`test_capture_sessions.py`, `test_capture_cli.py`)
  plus the full existing suite -- 1373 tests total, all green via
  `tools/run-plugin-tests.py agent-codespaces --reinstall`. `ruff check`
  clean on every touched/new file (confirmed a pre-existing, unrelated
  ~17-finding baseline on `__main__.py` is unchanged by this diff).
  Changefile added (minor -- new CLI verb).
- Next: open Phase 3's own PR, drive it through review/merge, then Phase 4
  (documentation-only, per Phase 1's consumer-owned scheduling decision)
  and Phase 5 (close-out).

