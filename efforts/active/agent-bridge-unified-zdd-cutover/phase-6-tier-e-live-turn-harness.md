# Phase 6 — Tier-E live-turn-survival harness (design)

Sibling design document for Phase 6 of
[`agent-bridge-unified-zdd-cutover`](README.md), per this repo's own
convention to extract substantial phase designs out of the shared effort
README (`efforts/README.md`'s "Section set").

**Goal.** Close Phase 5's Plan item 1 for real: prove, with a genuinely
live Copilot/ACP turn in flight (not a stdlib-simulated stand-in), that
`agent-bridge deploy` (the one canonical cutover path) does not disrupt it
-- no dropped turn, no lost event -- while the daemon fully changes
generation underneath it.

**Why this doesn't fit the harness's standard Tier-E shape.** Every
existing Tier-E scenario (`agent-vault-eval`, `agent-dispatch-hibernate-
eval`, `context-handoff-eval`, ...) judges whether a driven agent
*discovers and correctly follows* a plugin's documented mechanism -- an
LLM judge (`clean-room-judge`) scores literal-mode doc-compliance. This
drill has **no doc-compliance question at all**: it's an infrastructure-
reliability assertion (did the daemon-level mechanism preserve a live turn
across a cutover) that holds or fails independent of what the driven agent
knows or does. The right verdict mechanism is almost certainly a
**programmatic evidence comparator** (before/after transcript + event-log
+ generation-change proof), not an LLM judge.

**Critical topology correction (review-caught, kept documented here so it
is not relitigated).** The obvious-looking approach -- registering the box
as a Tier-E provider via `bridge_register.py` (a `command`-type provider,
`docker exec ... copilot --acp --stdio`) -- is the **wrong** transport for
this drill. Per `session_start.py`: only a **local** target
(`target.type == "local"`) enters `_connect_via_session_host` (goal 1/3's
survivable-child path Phases 2/3 actually built); `ssh`/`command`/spawn
providers route through a separate frontend-owned process path that has
**no host-boundary spawner** and is never reattached across a cutover --
during `deploy`'s drain, such a session either blocks the drain until it
finishes or gets forcibly terminated, but its transport was never tracked
in `HostIndex` at all. A **plain `command`-registered** Tier-E provider
session (`bridge_register.py`'s doc-audit shape) would therefore prove
*nothing* about session-host reattachment -- worse, it could produce a
**false pass** (turn completes fine because it drained normally, not
because it survived a real generation handoff). Note this is narrower
than "only local targets use the Session-Host path" -- `session_start.py`
also routes the dedicated CodeSpace and container branches through
`_connect_via_session_host` (their own remote-boundary spawners); it is
specifically the bare `command`/`ssh` provider shape that bypasses it. The
correct shape for THIS drill is still a **local target** (the simplest
one to stand up in a box, and the one Phases 2/3 most directly built for)
-- but do not generalize the exclusion to every non-local transport when
reusing this pattern elsewhere. Run `agent-bridge` inside the box as its
own daemon (exactly like the existing `agent-bridge-cutover` Tier-P
scenario already does) and create the test session via `agent-bridge
create <agent-name> --target-dir <local-repo-path>` (the checkout path is
supplied via `--target-dir`, NOT the positional `target` argument, which
names an *agent*, not a path -- confirm the exact local-agent name to use
before implementing) against a real local repo/worktree in the box -- this
is the only path that spawns a real Session-Host child and durably
registers it in `HostIndex`. This likely means the Tier-E harness's
provider-registration machinery (`bridge_register.py`) is NOT needed at
all for this drill -- it is closer in shape to the existing Tier-P
`agent-bridge-cutover` probe, just using a real `agent-bridge create`
session (real Copilot, real model calls, real credits) instead of a bare
daemon with zero sessions. Re-evaluate whether this needs the "Tier E"
label at all, or is better framed as a credits-consuming *extension* of
the Tier-P scenario -- resolve this before writing a manifest.

**Concrete design sketch (topology corrected above).**
1. **Box.** A base image with `agent-bridge` installed + provisioned (the
   existing `agent-bridge-cutover` Tier-P scenario's own `scenario.sh` +
   `fixtures/cutover_probe.py` -- there is no separate `setup.sh` in that
   scenario -- is the starting reference for the install/provision +
   real-daemon-subprocess plumbing) -- a real Copilot auth context is also
   needed here (the existing Tier-P scenario's probe never makes a real
   model call; this drill does).
2. **A real, local Session-Host-backed session** -- inside the box,
   with the box's own `agent-bridge` daemon running (`spawn_serve`-style,
   as the existing Tier-P probe already does), create a session via
   `agent-bridge create <agent-name> --target-dir <local-repo-path>`
   against a real local repo/worktree in the box (the checkout path goes
   through `--target-dir`, never the positional `target`, which names an
   agent). `target.type == "local"` is what actually spawns a
   real Session-Host child and registers it in `HostIndex` -- confirm this
   in the created session's own routing/host-index state before
   proceeding, not just by trusting the CLI's exit code.
3. **A genuinely long-running prompt** -- something spanning several
   model round-trips/tool calls (tens of seconds, not one instant reply),
   giving a real window to land the cutover mid-turn. Needs tuning: long
   enough to hit reliably, short enough not to waste credits.
4. **Fire the cutover exactly mid-turn** -- poll the session's status
   (via the CLI/API) until it's confirmed running/mid-turn, then invoke
   `agent-bridge deploy` from OUTSIDE the driven session (a harness-side
   action racing the turn, not something the driven agent itself does).
5. **Capture evidence across the boundary** -- the session's event
   log/transcript spanning before and after the deploy. Assert: the same
   session id throughout, the turn actually completes (a `turn_complete`/
   assistant reply reaches the client), no gap corresponding to a
   dropped or duplicated event, the Session-Host claim was actually
   reattached to the new generation (not merely that the session
   survived by luck), AND (critically) that the cutover *actually
   happened* (a new active endpoint, the old generation's pid gone) -- a
   "pass" where the cutover silently no-op'd or landed outside the turn's
   window is a false pass, not a real proof.
6. **Programmatic verdict** -- a dedicated evidence comparator (not an LLM
   judge): confirms the generation genuinely changed, the session-host
   claim was reattached under the new generation, the transcript shows a
   clean uninterrupted completion, and explicitly fails (rather than
   silently passing) if the timing race missed the window.

**Feasibility / cost notes.** Consumes real AI credits per run (a genuine
Copilot turn) -- treat this as a manually-triggered/opt-in scenario, not a
routine CI pass. Tier E is local-only, never a blocking CI gate today
(gated behind the cheap Tier-P precondition). `runs.max_credits` is
**advisory only** (the transport doesn't expose per-turn usage to the
runner, so it's recorded as intent, not hard-enforced); `runs.aggregate`
(`unanimous`/`majority`) controls how N repeated runs are combined into one
verdict, not cost -- a claim used to gate a change needs `count >= 3` +
`unanimous`, a single green run is evidence, not proof. Requires Docker;
not runnable off-Docker unlike the Phase 5 stdlib probe. The mid-turn
timing race is the hardest part -- likely needs a deliberately slow/instrumented test
workload or a debug synchronization hook ("prompt received, model call in
flight") to land reliably rather than by luck. Given Phase 5's own
abrupt-kill-recovery check took nine review rounds to get honest and
correct, budget comparable iteration here.

**Acceptance criteria (also tracked in the effort README's Validation
Plan).**
- [ ] A real live cutover drill shows a real Copilot turn completes with
  zero observed disruption while the daemon's generation actually changes
  underneath it (same session id, no dropped/duplicated event, confirmed
  generation change -- not a trivial/no-op cutover). Implemented as
  `fixtures/live_turn_probe.py`, wired as scenario phase 4
  (`CR_LIVE_TURN_DRILL=1`); not yet executed for real (needs Docker + real
  Copilot auth + real credits -- see the effort README's Journal).
- [x] The drill's verdict is programmatic/evidence-based, or a documented
  decision explains why an LLM judge is the right mechanism after all. It
  is programmatic: `HostIndex` record reattach, `sessions --json` status,
  `wait --attention turn_complete`, and an `events.jsonl` before/after
  diff -- no LLM judge, confirming this doc's own §"why this doesn't fit
  the harness's standard Tier-E shape".
- [x] The scenario (or bespoke script) is documented in `tools/clean-room/
  README.md`'s catalog, and in `ARCHITECTURE.md`/`TIER-E-EXECUTION.md` if
  it establishes a new "objective-only Tier-E" pattern other plugins could
  reuse for similar infra-reliability drills. Documented in the catalog;
  it does NOT establish a new Tier-E pattern (it deliberately isn't Tier-E
  at all -- an opt-in phase of an existing Tier-P scenario instead), so
  `ARCHITECTURE.md`/`TIER-E-EXECUTION.md` were left untouched.
