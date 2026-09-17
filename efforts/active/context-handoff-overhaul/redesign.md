# context-handoff overhaul — redesign notes

Sibling doc to `README.md` (Phase 1). Captures the adversarial re-embodiment
findings and the concrete architectural decisions this effort executes
against. Read this when working Phases 2+; the README stays the map.

## 1. Method

1. **Isolated derivation.** A separate agent, given *only* the seven stated
   goals and three platform constraints (no repo access, no search, no web,
   no hint of the current implementation), designed a context-handoff
   mechanism from first principles. Full transcript preserved in this effort's
   journal.
2. **Reality audit.** Read, in full: the current `context-handoff` plugin
   source (`extension.mjs`, `handoff-core.mjs`, `cutover-seed.mjs`,
   `thresholds.mjs`, `handoff-tasks.mjs`); the four relevant visions
   (`agent-fabric`, `session-hosting`, `plugins/agent-bridge`,
   `plugins/agent-worktrees`); and three in-flight efforts
   (`handoff-live-cutover`, `handoff-cutover-lifecycle-journal`,
   `handoff-cutover-reload-robustness`), plus the archived
   `bridge-native-context-handoff`.
3. **Three-bin diff** (per the `envisioning` skill's generativity check): every
   place the isolated derivation and reality disagreed was sorted into
   **vision-ahead** (reality hasn't built it yet — feeds this effort's Plan),
   **genuine blind spot** (neither reality nor the pre-existing visions stated
   an intent the derivation surfaced — folded into
   `visions/plugins/context-handoff`), or **spec-level** (mechanism detail
   that belongs in this doc / the eventual implementation, not the vision).

## 2. What's already correct in reality (no action needed)

- The **policy/hosting split** the derivation reinvented as `HandoffCore` +
  `HostAdapter` already exists, just as independent plugins coordinating
  loosely rather than one abstract interface: `context-handoff` owns nudge
  policy and baton content; `agent-worktrees` owns mux/pane choreography and
  launch construction; `agent-bridge` independently owns its own bridge-hosted
  ACP handoff path (`SessionManager.handoff_session()`). The `agent-fabric` and
  `session-hosting` visions already state this split as should-be
  (`persist-before-notify`, `launch-receipts-are-provisional`,
  `retirement-follows-authoritative-takeover`). No blind spot; the isolated
  derivation converged on an architecture reality (and the existing visions)
  already encode.
- The **signal/baton mechanism** (derivation's "signal file") is reality's
  agent-dispatch `proposed`/`handoff` task (or a worktree-state file
  fallback) — a coordinator-backed durable store, a stronger primitive than a
  bare signal file. No change needed.
- **Clean-exit gesture emulation** (derivation's "Ctrl+C twice via the host")
  is reality's mux `send-keys` sequence (`/exit` then Enter, then `C-c`
  fallback), armed on `session.idle` after cutover. Matches.
- **Manual fallback recipe always rendered** (goal 5) — `trigger_handoff`
  already always ends with the final short seed/prompt regardless of outcome.
  Matches.
- **Two-way predecessor/successor lineage** (goal 6) is already a stated
  `agent-fabric` invariant, and `handoff-cutover-lifecycle-journal` has landed
  a durable per-project trace store (PR #2496) with a 13-stage event model.
  This effort extends that work rather than replacing it (see Phase 4).
- **Single-current-session-per-worktree invariant** (goal 7) is already
  stated in `agent-fabric`. The gap is *reliability of realization*, not
  absence of intent — see §3.

## 3. Genuine blind spots folded into the vision

These were stated as should-be in `visions/plugins/context-handoff` because
neither reality nor the existing sibling visions said them, but the isolated
derivation's reasoning exposed them as real intent:

- **Host-independent completion guarantee.** Reality's only answer for "no
  host is present" today is the manual-fallback recipe — there is no
  automatic path to a running successor without *some* host. The derivation's
  "Courier" (a detached watchdog the predecessor spawns to guarantee a
  successor even with zero host) is spec-level as a specific mechanism, but
  the *guarantee* it serves — a handoff reaches a successor or resolves to an
  unambiguous manual fallback, independent of hosting — was a real gap, now
  Behavior "Handoff reaches a successor independent of hosting" in the vision.
- **Perpetuating mandate as explicit, standing content.** Reality's seed
  format carries task framing but doesn't explicitly and durably state "you
  too may hand off, as many times as needed, until the effort is done" as a
  first-class carried element, nor does a *fresh* (non-handoff) session get
  told the mechanism exists. Now Feature "Perpetuating mandate" in the vision.
- **Background-flow continuity as an explicit content class.** Reality's
  envelope/seed content doesn't yet explicitly enumerate long-lived
  watches/polls/scheduled prompts as their own carried category (they'd
  currently just be lost or, at best, mentioned incidentally in prose). Now
  Feature "Continuity of work" explicitly names this class in the vision.
- **Worktree-scoped logical agent for callers.** `plugins/agent-bridge`
  already states "one authoritative serialized conversation stream" for its
  own hosted case; the derivation's framing — callers of *any* hosting
  provider should perceive one continuous, worktree-scoped logical agent, not
  a session-scoped one — generalizes that principle across hosting providers
  rather than just agent-bridge's own. Now Behavior "Seamless cutover" /
  Feature framing in the vision.

## 4. Vision-ahead deltas (this effort's actual work)

Bucketed by the three defining challenges plus the escalation-policy gap.

### 4.1 No force-stop tier exists today (Goal 1)

Reality has soft (55%) / hard (70%) nudges only; there is no forced handoff at
a final threshold, and the 80%-ish auto-compaction point is unrelated to
context-handoff. **This is the sharpest concrete gap.**

Decision: add a third tier to `thresholds.mjs` / the reminder state machine.
On crossing the force threshold:
1. Finalize whatever handoff-content draft exists (reuse
   `generate_handoff_prompt`'s existing fact-gathering, applied automatically
   rather than waiting for the agent to call it).
2. Call `save_handoff_prompt` + `trigger_handoff` automatically.
3. Inject a synthetic tool-boundary message telling the agent no further
   mutating tool calls will be honored this turn (a `preToolUse`-hook-style
   deny is the existing mechanism for blocking tool calls — reuse it rather
   than inventing a second blocking path).
This is additive to `handoff-cutover-reload-robustness`'s existing seed-format
work (bash-first seed) — the force-tier still needs to avoid that effort's
`consume_handoff`-as-first-action race, so the auto-triggered seed must also
be bash-first, not tool-call-first.

### 4.2 No self-`/new`/`/clear`, no self-exit (Challenges 1 & 2)

Already correctly solved in reality exactly as `handoff-live-cutover` decided:
never try to reset in place; always launch a genuinely new process
(`agent-worktrees handoff-cutover`, itself reusing `_build_launch_cmd()` +
`-i "<seed>"`); exit only via host-mediated graceful gesture, with a
best-effort kill + **the host owning stale-lock sweep** as the fallback (per
that effort's Phase 3/§Retirement). No new mechanism needed here — this
effort's job is closing `handoff-live-cutover`'s remaining open Phase 3 items
(armed `session.idle` retirement, non-mux/successor-start-failure fallback)
now that it's landed in this repo, not re-deciding the approach.

### 4.3 Host-agnostic notification when no host is present (Challenge 3)

Today: file/task drop + best-effort ping to agent-worktrees/agent-bridge; if
neither is reachable, the operator gets the manual recipe and nothing further
happens automatically. This satisfies goal 5 (manual fallback always works)
but not the "vision-ahead" completion guarantee identified in §3.

Decision — **do not build the derivation's detached-watchdog "Courier" as a
new standing process.** A permanently-running watchdog per handoff re-adds a
process-lifecycle problem (who supervises the watchdog?) that mirrors the
very problem it's meant to solve, and duplicates supervision `agent-dispatch`
already provides. Instead: extend `agent-dispatch`'s existing coordinator (a
per-host leased task-queue that already reconciles liveness) to recognize a
`proposed`/`handoff` task that has sat unclaimed past a bounded window as a
first-class reconciliation case — the coordinator already polls; give it the
authority to launch a fallback successor itself (a headless
`handoff-cutover --seed ...` invocation) when nothing else claimed the task in
time, using the same bash-first seed `handoff-cutover-reload-robustness`
already hardened. This reuses an already-supervised, already-running
component instead of inventing a new one, in keeping with the
`envisioning` skill's "extend before you regenerate" bias.

**Open question, not yet resolved:** `agent-worktrees handoff-cutover` today
assumes a live mux session to spawn a new pane/window into — it is not
verified to work as a genuinely headless launch with no mux present at all
(the no-host case this section is meant to cover). Phase 3's implementation
work must confirm `handoff-cutover` (or a headless variant of it) actually
supports a mux-less invocation before the coordinator can rely on it as a
fallback; if it can't, the coordinator's fallback path needs its own
non-mux launch primitive rather than reusing this command as-is.

`userPromptSubmitted` hook option (raised separately during this effort's
kickoff investigation): usable as a cheap, deterministic "a real prompt
reached the successor" signal — grep the submitted prompt for the expected
handoff token, no LLM judgment involved. Complementary to, not a replacement
for, the coordinator-reconciliation fallback above: it closes the "did my
launch actually land" observability gap; the coordinator fallback closes the
"nobody ever tried" gap.

### 4.4 Configurability (Goal 4)

Reality has per-repo `.context-handoff/config.yaml` (`soft_percent`/
`hard_percent`). Missing: a `mode` toggle (`auto`/`manual-only`/`off`),
absolute-token thresholds as an alternative to percentages, and a user-level
default layer beneath the repo layer. Additive work in `config.mjs`.

### 4.5 Lineage/diagnostics (Goal 6/7 realization gap)

`handoff-cutover-lifecycle-journal` already built the durable trace store and
13-stage model; its own open items (populate linkage fields, Stage 1 framing,
the `agent-worktrees handoff-trace` renderer, feeding
`health.find_orphaned_handoffs()`) are exactly the realization work the
single-successor-with-recovery goal (7) needs. This effort does not duplicate
that work — it depends on it landing, and Phase 4 below is deliberately thin
(closing the remaining gap between that effort's plan and this vision's
stated Behaviors), not a rebuild.

## 5. Explicit non-decisions (open problems carried forward, not solved here)

Per the isolated derivation's own §10 and this effort's scope: exact
context-usage estimation accuracy, chain-depth fidelity decay beyond "the
mandate is explicitly restated," and adversarial-filesystem correctness for
the durable stores are acknowledged hard problems this effort does not claim
to fully close — they're bounded, named risks for the Validation Plan to
probe, not blockers to landing the additive work above.

## 6. Mux pane lifecycle primitives, isolated (Challenge 4 — added 2026-09-17)

**Trigger.** Live production symptoms on the operator's own machine, reported
after Phase 3 landed: mux panes spawning over each other or not becoming
foreground, handoffs "never picking up," and false-positive pickup signals
fed back into the live session. One concrete cause was found and fixed in
isolation (`fix/handoff-predecessor-retire-infinite-loop`, PR #2826 — the
resident status-monitor's retire sweep had no terminal condition and retried
a dead predecessor forever, ~3,700 no-op attempts across 6 worktrees). But
the operator's diagnosis of the *class* of problem is broader: **repeatedly
patching mechanism inside `context-handoff`/`handoff-cutover`'s existing
control flow keeps making it more fragile, not less** — every fix has to
reason about the whole multi-stage choreography (trigger → store → detect →
spawn → confirm → retire) at once, because the mux create/foreground/
terminate steps were never built as independent, directly-testable
primitives in the first place.

### 6.1 Reality audit

What exists today (`plugins/agent-worktrees/src/agent_worktrees/sessions.py`
+ `__main__.py`'s `cmd_handoff_cutover`):

- **Create.** `mux_new_window()` already does almost the right shape for a
  "bootstrap script confirms before running the payload" primitive: it opens
  + selects (foregrounds) a new window, and when an `initial_prompt` is given,
  the pane-wrapper script writes a receipt file (`"launching"`) that the
  parent polls for before declaring the successor confirmed — a real
  bootstrap-confirms-then-execs-payload flow, just built for exactly one
  caller. It is reachable **only** through `cmd_handoff_cutover`'s spawn mode
  (a single ~500-line function covering CLI parsing, mux-session resolution,
  seed encoding, spawn, AND retire in one call surface), has no standalone
  CLI verb, and the only pre-spawn "recording status of what it's about to
  do" is a `handoff_successor_spawn_started` log call embedded deep in that
  function — not part of the primitive itself, so a caller that invokes
  `mux_new_window()` directly (as any future non-handoff flow would) gets no
  status trail at all.
- **Foreground.** Two independent code paths exist for "make this pane the
  one in front": `mux_new_window()`'s implicit `new-window` (no `-d`) select,
  and `mux_focus_pane()`'s explicit `select-window` for an already-existing
  pane. Neither is unified with the create primitive above.
- **Terminate.** Two independent, subtly different termination code paths for
  what is conceptually one primitive:
  - `graceful_quit_mux_session()` (+ `restart_worktree_copilot()`, the Picker
    Stop / Neuron-Forge Take-over path) — a double/conditional-triple Ctrl-C
    ladder that polls `has_mux_session()` (the whole session gone), never
    reads the pane's actual console output for Copilot's own shutdown
    signature, and falls back to a hard *session* kill.
  - `mux_retire_pane()` (`sessions_pane_retire.py`, the handoff-cutover retire
    mode) — Ctrl-C into one specific *pane* (not the whole session, since a
    session can now hold multiple windows post-cutover), gated on
    `mux_binding_for_session()` process-identity proof, with its own
    stale-lock-file handling.

  Both ladders solve "ask Copilot to quit cleanly, then give up gracefully,"
  but neither reads the pane's live text output to *confirm* the shutdown
  signature the operator is asking for below — both currently infer
  completion only from session/pane liveness polling.

### 6.2 Three-bin diff

- **Already correct, reuse as-is:** the receipt-file bootstrap-confirmation
  idea (`mux_new_window`'s `initial_prompt` receipt), the Ctrl-C-ladder
  approach to graceful termination, and process-identity verification via
  `mux_binding_for_session()` before ever touching a pane. None of this needs
  reinventing — it needs **extracting** into primitives that don't require
  understanding the whole handoff-cutover choreography to use or test.
- **Genuine gap (this challenge's work):** no single, directly-invokable
  "create a pane in worktree X's mux server, log intent first, bootstrap-
  confirm before the real payload execs, foreground it" primitive exists
  independent of `cmd_handoff_cutover`. No single "terminate this pane,
  reading its console output for a real shutdown signature (not just
  liveness polling), escalate on a timeout, clean up its lock file"
  primitive exists independent of the two divergent ladders above. Neither
  primitive has a **dedicated, isolated test/dev harness** that exercises it
  directly against a live mux server without going through the full
  trigger→store→detect→spawn→confirm→retire dance — which is exactly why
  live mux-mutation testing was previously found unsafe to run from inside
  an attached session (`handoff-live-cutover`'s Phase 4 finding) and has
  never been properly exercised since.
- **Vision-ahead (deferred, not this challenge):** a full status-bar/lineage
  UI over pane lifecycle state belongs to `handoff-cutover-lifecycle-journal`
  (Phase 5 above), not here — this challenge only needs the two primitives to
  emit the same `activity.log_event` stage marks that already exist, not a
  new observability surface.

### 6.3 Decision

Per the operator's explicit direction: this is a **sub-division of this
effort** (an added Challenge/Phase), not a new effort — the same
adversarial-redesign-and-reflect flow this effort already uses, applied to a
narrower, previously-unexamined slice of the same mechanism.

Build exactly two primitives, each independently invokable and independently
tested, in `sessions.py` (or a sibling module if size warrants a split):

1. **`pane_create(worktree_id, payload_cmd, ...)`** — (a) identify the
   worktree's current mux server/session (reuse existing resolution), (b)
   `activity.log_event` the intent *before* acting (a new stage mark, not
   buried in a caller), (c) invoke the mux `new-window`/`new-session` via a
   pane-bootstrap script that itself confirms landing (extend the existing
   receipt-file mechanism so it is not gated behind `initial_prompt` alone)
   before exec'ing the real payload, (d) foreground the created pane as part
   of the same call (unify with `mux_focus_pane`'s explicit-select path
   rather than relying on `new-window`'s implicit default). Returns the pane
   id + confirmed/failed outcome, exactly like `mux_new_window()` does today
   — this is substantially an extraction + generalization, not a rewrite.
2. **`pane_terminate(pane_id, ...)`** — reuse the existing Ctrl-C ladder
   shape (double, conditional third, same timing knobs), but read the pane's
   live output (`capture-pane`) between sends for a defined Copilot
   shutdown/exit signature instead of inferring completion from liveness
   polling alone; hard-kill the pane (not necessarily the whole session) on
   a 30-second overall budget; then clean up the stale session-lock file the
   old process left behind. This **replaces** both
   `graceful_quit_mux_session`+`restart_worktree_copilot` and
   `mux_retire_pane` as the one termination primitive both flows call.

Both primitives ship with a dedicated CLI-reachable harness (a diagnostic
subcommand, not routed through `handoff-cutover`) so they can be driven
directly against a real mux server in isolation — closing the gap that made
live mux mutation testing unsafe/impossible before. Only after both
primitives are hardened and independently validated does `handoff-cutover`
(spawn + retire modes) and the Picker Stop/Take-over path get rewired to call
them, collapsing today's two divergent termination ladders into one.
