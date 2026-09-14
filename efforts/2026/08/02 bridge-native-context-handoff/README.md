---
visions:
  - visions/agent-fabric
  - visions/plugins/agent-bridge
last_swept: 2026-09-01
last_reconciled: 2026-09-01
---

> **Migrated from a private facility repo** (`efforts/2026/08/02 bridge-native-context-handoff`)
> on 2026-09-13, as part of reconciling handoff/cutover effort tracking into
> the repo that actually owns the `agent-bridge` mechanism. Original
> frontmatter referenced that repo's `visions/agent-fabric/agent-bridge`
> and `visions/agent-fabric/neuron-forge`; updated above to this repo's flat
> `visions/agent-fabric` and `visions/plugins/agent-bridge` (there is no
> copilot-extensions equivalent for the Neuron Forge side — NF is an
> app hosted in that private facility, and this effort's NF-wiring portion
> remains a legitimate artifact of that private repo even though the
> mechanism it drove lives here). This is a **done/archived historical
> record**, migrated verbatim. **A trailing `†` marks a reference to that
> private repo's own issue tracker — not resolvable here.**

# bridge-native-context-handoff — Context Awareness + In-Place Handoff for Automated Sessions

- **Slug:** `bridge-native-context-handoff`
- **Repo:** copilot-extensions (migrated 2026-09-13 from a private facility
  repo, which originally hosted this as effort home + **neuron-forge**
  wiring) — the `agent-bridge` plugin, which owns the runtime
- **Branch:** `worktree/the-dev-host-wsl-20260801-185734-00fa`
- **Created:** 2026-08-01
- **Status:** **Done / Archived (completed 2026-08-02)** — all items A–G shipped
  and deployed; all issues closed.
  Bridge side (A–E) is agent-bridge 0.4.0-dev209 on the primary facility dev host (primitive,
  `session_handoff` event, control surface, opt-in context-pressure auto-handoff
  + prompt-triggered roll). NF side (F/G) landed via PR #4161† and is deployed on
  the primary facility dev host (Neuron Forge v0.2.1): seeded in-place roll with cold-roll fallback,
  changeover surfaced as a legible seam, and the consumer follows a
  bridge-initiated handoff. End-to-end threshold validation on the primary facility dev host remains
  as optional stretch verification.
- **Umbrella issue:** #4140†
- **Sub-issues:**
  #4141† (B — bridge handoff primitive) ·
  #4142† (C — `session_handoff` event) ·
  #4143† (D — control surface) ·
  #4144† (E/E2 — auto + prompt-triggered) ·
  #4145† (F — NF seed the roll) ·
  #4146† (G — NF surface + seam)
- **Sibling effort:** [`handoff-live-cutover`](../../../active/handoff-live-cutover/README.md)
  — the **interactive-CLI / mux** live cutover (same north star, different
  substrate). This effort is its **automated / bridge-hosted ACP** analogue.
- **Related efforts (historical, from the source repo — not present in this
  checkout):** `live-session-messaging` (SDK `session.send` injection, prior
  art) · `neuron-forge-telemetry` · `agent-bridge-zero-downtime-deploy` (its
  `zdd` drain/cutover primitives were prior art for spin-down/spin-up
  sequencing).

## Guiding Intent

Make a context handoff **continue by itself in an automated session**, the way
`handoff-live-cutover` already makes it continue by itself in an *interactive*
CLI. Today the interactive path lives in the `context-handoff` Copilot extension:
it watches `session.usage_info`, nudges at 55 %/70 %, and — under a mux — spins up
a successor Copilot in place and retires the old pane. **None of that reaches a
session that agent-bridge hosts** (a bridge-owned `copilot --acp` child, which
Neuron Forge and bridge-as-agent callers drive). Those sessions run headless,
with no mux pane and no operator to paste a prompt.

But the bridge **controls the ACP session** — it owns the child process, sees its
context utilization, and owns the event stream every caller reads. That is a
*better* place to do a handoff than a terminal multiplexer. This effort ports the
two halves of the interactive system into the runtime:

1. **Context awareness** — a bridge-hosted session knows its own context-window
   pressure (already tracked; today it only *warns*).
2. **In-place handoff** — when context gets large, the session produces a
   continuation brief, the bridge **retires the ACP child and spawns a successor
   in the same worktree seeded with that brief**, and **announces the changeover
   on the session's event surface** so every caller (the Neuron Forge cockpit, a
   bridge-as-agent host, a CLI reader) is informed and follows the baton in place.

**North star:** a bridge-hosted session that hands off and keeps going —
automatically, in the same worktree, with the caller told about the changeover
rather than left staring at a retired session.

## Request

Port context-aware in-place handoff from interactive mux-hosted sessions into
automated agent-bridge sessions. The bridge should generate and carry a
continuation brief into a successor on the same worktree, expose explicit and
opt-in automatic triggers, announce the changeover as a first-class event, and
let Neuron Forge follow the successor without presenting a dead session. A
prompt sent from the mobile cockpit into a context-saturated session must hand
off first and then deliver that prompt to the successor.

## Reconciliation to Vision (vision-first change)

This effort is **mostly vision-closing** with a modest **vision-extending**
increment; both visions were revised in the same intent PR that carries this
effort:

- **agent-bridge** already stated the *handoff / sunset the head* choice (the
  create-time head guard even prints it) and the *takeover / drain-cutover*
  patterns — but nothing implemented the handoff, and it was not tied to context
  awareness. Added: Feature **"context-aware in-place handoff for hosted
  sessions"** and Behavior **"handoff-carries-context-and-announces-the-
  changeover"** (originally recorded against a nested private-repo vision
  path; now `visions/plugins/agent-bridge/README.md` here).
- **neuron-forge** already stated **usage/context signals** in transcript
  fidelity. Added: Feature **"in-place session handoff, surfaced not swallowed"**
  and Behavior **"follow-the-handoff-dont-mourn-it"** (recorded against
  neuron-forge's own private-repo vision — NF has no copilot-extensions
  equivalent; this bullet is historical record only).

## Current State (what already exists — the substrate)

The port is mostly *wiring existing pieces together*, not greenfield.

**agent-bridge (`copilot-extensions/plugins/agent-bridge/src/agent_bridge/`):**
- `live_representation.py` translates ACP `session.usage_info` →
  bridge `usage_update` events (`currentTokens`/`tokenLimit`).
- `session_manager.py::_handle_usage_update()` persists `context_size` /
  `context_used` / `usage_model` and **already emits `context_warning` /
  `context_critical`** — the critical message literally reads *"consider
  handoff"*. **Nothing acts on it.**
- `session_manager.py` has `start_session()`, `stop_session()`,
  `resume_session()` (incl. seed-an-initial-prompt-on-spawn plumbing), ACP
  session identity, worktree linkage, and an append-only event log.
- `routes/sessions.py::_enforce_worktree_head_guard()` offers `reuse / handoff /
  sunset` choices on create — **"handoff" is described but unimplemented.**
- `worktree_head.py` derives the worktree's asserted head from
  `agent-worktrees head-session` (the ground-layer owner; bridge keeps no rival
  pointer).
- `db.py` stores per-session status/usage/worktree linkage/delivery cursors but
  **no successor linkage**.
- `libs/zdd/` (cutover/breadcrumb/routing) — prior-art spin-down->spin-up
  sequencing (for *daemon* redeploy, not ACP-child handoff).

**neuron-forge (`services/neuron-forge/`):**
- `server/core/session_bridge.py::new_session()` **already rolls in place**:
  stops the old bridge session, starts a new one in the *same worktree*, updates
  the context in-place, and emits `session_rolled` + `session_created`
  (`rolled_from`). **But it carries no handoff seed** — it abandons context
  blind, so the successor opens cold.
- `client/src/ts/components/nf-chat-view.ts` already renders `usage_update`
  (`context_used`/`context_size`) in the intent bar.
- The UI follows a session by stable `worktree_id`, and already handles
  `session_rolled` / `session_created`.

## Design Decisions

These are defaults recorded for review; the operator may redirect.

1. **Handoff-prompt generation — prompt the child.** The bridge asks its *own*
   ACP child to emit a structured handoff via the existing `context-handoff`
   skill/template (the child already carries it), captures the reply as the
   handoff text, then seeds the successor's opening turn with it. This reuses the
   *exact* interactive template — no divergent handoff format — and keeps the
   brief agent-authored (richer than a bridge-synthesized summary of tool
   events). Fallback if the child cannot answer: a bridge-synthesized brief from
   session-side state (first prompt, files modified, turn count).
2. **Trigger — explicit + opt-in auto.** _(confirmed)_ Expose the handoff as an
   **explicit operation** (bridge CLI verb + HTTP endpoint + an NF cockpit
   action) any caller can invoke, **plus** an **opt-in policy** to fire it
   automatically at the `critical` threshold for a session no operator is
   watching. Auto is **off by default**; interactive/observed callers keep
   control.
   - **Mobile is the forcing function.** On a phone (the NF cockpit through the
     gateway) there are **no slash commands and no manual session creation** —
     no `/new`, no `/clear`, no `/handoff`. The *only* way an operator on mobile
     continues past a full context window is a handoff that the runtime performs
     **smoothly in response to their next prompt**. So the on-demand path must be
     reachable as (a) a plain cockpit affordance/button, and (b) implicitly —
     when the operator sends a prompt into a session already at critical, the
     runtime hands off and delivers that prompt to the successor rather than
     failing or silently truncating. The opt-in auto-trigger and the
     prompt-triggered handoff together make mobile continuation possible without
     any terminal gesture.
3. **Changeover notice — a first-class event (contract), open to an ACP rider.**
   _(confirmed)_ A new `session_handoff` event (payload: `rolled_from`,
   `rolled_to`/successor id, worktree id, handoff summary) is part of the
   **bridge's event contract** and rides the existing SSE/event surface. NF
   renders a "handed off — continuing here" seam and follows the successor;
   bridge-as-agent CLI callers see the changeover in their event stream.
   `session_rolled` stays for NF's own roll; the new event is the
   *bridge-originated* changeover. Where a downstream/upstream ACP peer should
   also learn of the changeover, the same notice may additionally be surfaced as
   an **ACP extension rider** (a protocol-level session notification) so the
   changeover is legible to ACP clients that never touch the bridge's SSE
   surface — the bridge contract is the floor, the ACP rider an optional reach.
4. **Identity & continuity.** The successor is a new ACP session id; the worktree
   **head repoints** to it (via the ground layer — bridge derives, never
   duplicates); the changeover event names both ids; the DB records the
   successor linkage so the chain is legible after a restart.
5. **Safety — drain before retire.** The retire->spawn follows the existing
   `drain-before-letting-go` behavior: capture the handoff, let the final turn
   settle, then stop the old child and spawn the seeded successor. A failed
   successor spawn must not orphan the worktree (mirror NF `new_session()`'s
   rollback-to-error).

## Cross-Repo Ordering

Per the facility cross-repo rule (PR-gated intent before unreviewed push):

1. **the private facility repo PR (this repo, PR-gated) — FIRST:** this effort README +
   the two vision reconciliations. The reviewed intent clears the Intelligence
   Dampener ahead of the implementing PR.
2. **copilot-extensions (agent-bridge plugin, owner repo):** the completed
   runtime primitive + CLI/HTTP + `session_handoff` event + opt-in auto-trigger
   historically landed by direct pushes to `main`, as recorded in the journal.
   Any future follow-up must use the repo's current PR-required
   `pr-self-merge` profile.
3. **the private facility repo PR (neuron-forge):** seed the roll with the handoff, surface
   per-session context utilization + a handoff affordance, render the changeover
   seam, follow the successor.

## Work Items

- [x] **A — Effort + vision reconciliation** (this repo). This README + the two
      vision edits. _(intent PR #4147† merged)_
- [x] **B (#4141†) — Bridge handoff primitive** (copilot-extensions). Generate handoff via
      the child -> drain -> stop child -> spawn successor in the same worktree
      seeded with the brief -> persist successor linkage -> repoint head. Core in
      `session_manager.py`; reuse `start_session`/`stop_session` + seed plumbing.
      _(shipped: agent-bridge 0.4.0-dev207; `handoff_session()` + schema v15
      two-way succession links; spawn-then-retire; deployed the primary facility dev host.)_
- [x] **C (#4142†) — Bridge `session_handoff` event + caller notification**
      (copilot-extensions). New event type emitted on changeover; carried over
      SSE (`routes/sessions.py`) and the CLI/bridge-as-agent event stream
      (`client.py`, `__main__.py`). _(shipped with B: emitted on BOTH the
      predecessor's and successor's event logs; CLI feed render added in D.)_
- [x] **D (#4143†) — Bridge control surface** (copilot-extensions). CLI verb (e.g.
      `agent-bridge handoff <session|worktree>`) + HTTP endpoint; wire the
      create-guard "handoff" choice to the real operation. _(shipped: agent-bridge
      0.4.0-dev208; `handoff` CLI verb (session|worktree), POST
      /sessions/{id}/handoff + /worktrees/{id}/handoff, `session_handoff` feed
      render; deployed the primary facility dev host. Create-guard "handoff choice" wiring deferred
      to ride with E's policy layer.)_
- [x] **E (#4144†) — Opt-in auto-trigger** (copilot-extensions). _Done (dev209).
      `AutoHandoffPolicy` (enabled=False, unwatched_only=True) on
      `ServiceConfig.auto_handoff`; crossing `critical` in `_handle_usage_update`
      marks a handoff owed and fires it on turn-settle (idle), honoring
      `unwatched_only` so a streamed session is never rolled out from under.
      Off by default (fail-safe). Queued follow-ups migrate to the successor.
      Single-checkout agents excluded._
- [x] **E2 (#4144†) — Prompt-triggered handoff** (copilot-extensions). _Done (dev209).
      A prompt into an already-`critical`, opted-in idle session (in
      `submit_or_queue_prompt`) hands off first, then delivers that prompt to the
      successor — ignores `unwatched_only` (sender is explicitly asking). The
      mobile "continue by sending the next message" path. 8 tests in
      `test_auto_handoff.py`; deployed the primary facility dev host._
- [x] **F (#4145†) — NF: seed the roll** (the private facility repo). _Done. `session_bridge.new_session()`
      now prefers a brief-carrying in-place handoff (via the bridge's
      `POST /sessions/{id}/handoff`) so the successor opens *warm*, with a plain
      cold-roll fallback when the session is ineligible (mid-turn / single-checkout
      agent / spawn failure) — the operator's "new session" request never fails.
      `bridge_client.handoff_session()` + `BridgeHandoffUnavailableError`; 3 tests._
- [x] **G (#4146†) — NF: surface context + changeover** (the private facility repo). _Done.
      Per-session context-utilization already renders (`ctx N%` in the status bar);
      added a "hand off" (↻) composer action that triggers the seeded roll — the
      essential mobile path (no /new or /clear on phones). Renders the
      `session_handoff` changeover as a legible "↻ Handed off — continuing here"
      transcript seam (never an error/dead session), and the consumer now **follows
      a bridge-initiated handoff**: on a `session_handoff` frame it re-points the
      context to the successor and reconnects on its stream, de-duping the seam.
      1 server test + TS typecheck/lint/build clean._

## Validation Plan

- **Bridge unit/integration** (`copilot-extensions/plugins/agent-bridge/tests/`):
  a handoff on a hosted session stops the old child, spawns a successor in the
  same worktree seeded with the brief, records the successor linkage, and emits
  `session_handoff` with both ids. Failure-to-spawn rolls back without orphaning.
  Auto-trigger fires once at `critical` only when opted in.
- **NF** (`services/neuron-forge/tests/`): a `session_handoff` event re-points the
  UI context to the successor on the same worktree route and renders the seam;
  the successor's transcript opens with the seeded brief.
- **End-to-end (the primary facility dev host):** drive a bridge session to the critical threshold,
  observe an automatic in-place handoff, and confirm the cockpit follows the
  baton and the successor continues the original work.

## Machines

| Machine | Role in this effort | Reached via |
|---------|---------------------|-------------|
| the primary facility dev host | Primary dev + bridge/NF host for e2e validation | local (this worktree) |
| a secondary facility host | Secondary bridge host (fleet validation, stretch) | agent-bridge / SSH alias |

## Journal

- **2026-08-01** — Effort opened. Mapped the substrate on both sides (agent-bridge
  already tracks context usage + warns "consider handoff" but never acts; NF
  already rolls in place via `new_session()` but carries no seed). Reconciled both
  visions (agent-bridge: context-aware in-place handoff Feature + carries-context
  Behavior; neuron-forge: surfaced-not-swallowed Feature + follow-the-handoff
  Behavior). Recorded the design decisions (prompt-the-child handoff generation;
  explicit + opt-in-auto trigger; first-class `session_handoff` changeover event;
  head-repoint continuity; drain-before-retire safety). Next: file umbrella +
  sub-issues, land this intent PR, then implement B–E in copilot-extensions.
- **2026-08-01 (cont.)** — Operator confirmed all three design forks
  (prompt-the-child generation; explicit + opt-in-auto trigger; first-class
  `session_handoff` event, optionally an ACP extension rider). Added the
  **mobile forcing function**: phones have no slash commands / manual session
  creation, so a smooth prompt-triggered handoff is *essential*, not optional —
  new work item **E2** (hand off on a prompt into an already-critical session,
  then deliver the prompt to the successor). Next: file issues, open intent PR.
- **2026-08-01 (cont.)** — Intent PR **#4147†** (effort + both vision
  reconciliations) approved by the Intelligence Dampener, consent applied,
  **merged** to master; worktree reconciled forward. Filed umbrella **#4140†** +
  sub-issues **#4141†–#4146†**. Planning/intent phase complete. **Next phase =
  implementation**, starting in `copilot-extensions` (owner/public repo): item B
  (#4141†) the handoff primitive, then C (#4142†) the event, then D/E (#4143†/#4144†)
  — each a short serial PR to `main`, reconciled to *that* repo's agent-fabric/
  agent-bridge vision + `docs/patterns`, version-bumped, and filed under a
  **sanitized** public coordination issue on `ThomasMichon/copilot-extensions`
  (generic-tool language; no facility/cockpit-product/persona names). Then F/G
  (#4145†/#4146†) land as neuron-forge PRs back in the private facility repo.
- **2026-08-02** — **Items B, C, D shipped and deployed** (copilot-extensions,
  `direct` push to `main`; sanitized public issue **#112**). Vision-closing:
  advances `visions/plugins/agent-bridge` here (context-aware in-place handoff
  Feature + carries-context Behavior) and the agent-fabric
  `handoff-orchestrated-above-primitives` / `single-current-session-per-worktree`
  behaviors — a bridge-hosted headless session that previously *no-op'd* on
  context pressure now has a real in-place handoff. **B** (agent-bridge
  0.4.0-dev207): `SessionManager.handoff_session()` — prompt-the-child brief
  authoring (with a session-state fallback), spawn-then-retire successor in the
  same worktree, `stop_session`-based predecessor retirement (STOPPED/resumable,
  not deleted), plus DB schema **v15** two-way `predecessor_id`/`successor_id`/
  `handoff_at` succession links (`link_succession()`) — and fixed a latent
  fresh-install bug where the context-usage columns were migration-only.
  **C** (with B): first-class `session_handoff` event emitted on BOTH event
  streams so every connected caller follows the baton. **D** (0.4.0-dev208):
  control surface — `agent-bridge handoff <session|worktree>` CLI verb (session→
  worktree fallback), `POST /sessions/{id}/handoff` + `POST /worktrees/{id}/handoff`
  (the worktree-handle path is the mobile affordance), and a `session_handoff`
  CLI feed render. 22 new tests (`test_handoff.py`, `test_handoff_routes.py`,
  `test_handoff_cli.py`); full agent-bridge suite green (1200 passed). Deployed
  zero-downtime on the primary facility dev host (drain + `KillMode=process` child survival);
  verified dev208 active, all prior sessions survived, endpoints live. **Next =
  E/E2** (#4144†) — the one **vision-extending** increment: revise the
  copilot-extensions agent-fabric vision to state context-pressure-*driven*
  auto-handoff, THEN add the opt-in policy flag + `_handle_usage_update()`
  auto-trigger + prompt-triggered handoff in `submit_prompt`. Then F/G
  (neuron-forge).
- **2026-08-02 (cont.)** — **Item E/E2 shipped and deployed** (copilot-extensions,
  `direct` push to `main`, agent-bridge **0.4.0-dev209**; sanitized public issue
  **#112**). This is the effort's **one vision-extending** increment, landed in
  two ordered pushes per the cross-repo rule: **(1)** revised
  `visions/agent-fabric/README.md` to state the new intent — Feature
  `handoff-under-context-pressure` + Behavior `context-pressure-drives-handoff`
  (a session at its own context ceiling is a first-class *reason* to hand off;
  opt-in, off by default; prompt-into-saturated continues by handing off first
  then delivering to the successor; the phone forcing function) — pushed as the
  intent commit; **(2)** the implementation. `AutoHandoffPolicy`
  (`enabled=False`, `unwatched_only=True`) on `ServiceConfig.auto_handoff`.
  **Proactive:** crossing `critical` in `_handle_usage_update` marks a handoff
  owed and fires it on turn-settle when idle, honoring `unwatched_only` so a
  human streaming the session is never rolled out from under; durably-queued
  follow-ups migrate onto the successor. **Prompt-triggered (E2):** a prompt into
  an already-`critical`, opted-in idle session (`submit_or_queue_prompt`) hands
  off first, then delivers the prompt to the fresh successor — ignoring
  `unwatched_only` (the sender explicitly wants the next turn). Single-checkout
  (command/CodeSpace) agents excluded (retire-before-spawn deferred). Wired
  through `session_manager_from_config`. 8 new tests (`test_auto_handoff.py`);
  full suite green (**1208 passed, 3 skipped**). Deployed zero-downtime on
  the primary facility dev host; dev209 active, config picked up the additive `auto_handoff` field.
  Closed **#4144†**. **Bridge side (A–E) complete. Next = F/G** (#4145†/#4146†),
  neuron-forge, landing as PR-gated the private facility repo PRs.
- **2026-08-02 (cont.)** — **Items F/G (neuron-forge) implemented.** **F (#4145†):**
  `session_bridge.new_session()` now prefers a brief-carrying in-place handoff via
  the bridge (`bridge_client.handoff_session()` → `POST /sessions/{id}/handoff`),
  so the successor opens *warm*; falls back to a plain cold roll on
  `BridgeHandoffUnavailableError` (mid-turn / single-checkout / spawn failure) so
  a "new session" request never fails. **G (#4146†):** per-session context
  utilization already renders (`ctx N%`); added a composer "hand off" (↻) action
  that triggers the seeded roll (the essential mobile path — no /new or /clear on
  phones); render the `session_handoff` changeover as a legible
  "↻ Handed off — continuing here" transcript seam (never an error); and the SSE
  consumer now **follows a bridge-initiated handoff** — on a `session_handoff`
  frame it re-points the context to the successor and reconnects on its stream,
  de-duping the twice-emitted seam. Server: 4 new tests + full NF suite green
  (**241 passed**); ruff + mypy clean. Client: TS typecheck + eslint + build
  clean. Next: PR-gated the private facility repo PR (Intelligence Dampener), then close
  #4145†/#4146† and deploy NF on the primary facility dev host.
- **2026-08-02 (F/G merged + deployed)** — PR **#4161†** approved by the
  Intelligence Dampener, merge consent granted, merged to master as `fa1ad7abd`.
  **Neuron Forge deployed on the primary facility dev host** (v0.2.1, healthy on :8090); verified
  the deployed server (`session_bridge.py` / `bridge_client.py`) and the built
  client JS both carry the F/G changes. Closed **#4145†**, **#4146†**, and the
  umbrella **#4140†** — every sub-issue (#4141†–#4146†) resolved. **Effort complete:**
  a saturated session now continues warm in a fresh successor on the same
  worktree, surfaced as a legible seam rather than a dead session — including the
  mobile path where continuing is just sending the next message. Optional stretch:
  drive a live session to the critical threshold on the primary facility dev host and observe the
  automatic in-place handoff end to end.

### 2026-09-01 — Archived by CAB reconciliation
- **Disposition: Done / Archived.** PR #4161† shipped and deployed the remaining
  Neuron Forge work, PR #4165† recorded completion, and umbrella #4140† plus
  sub-issues #4141†-#4146† are closed. The optional end-to-end threshold stretch
  does not hold the completed campaign open.
- Archived under the effort's 2026-08-02 completion date; no intent or live work
  was retired.
