# Cold-Spawn Hygiene — Session-Start Hooks & Extensions

- **Slug:** `cold-spawn-hygiene`
- **Repo:** copilot-extensions
- **Branch(es):** `effort/cold-spawn-hygiene`
- **Created:** 2026-09-21
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** aperture-labs#7382 (facility Gitea; this repo's own
  issue-linking convention doesn't apply cross-repo — see Context)
- **Sub-issues:** _none yet — file per-phase as scope firms up_

## Guiding Intent

Reduce the number and cost of Windows process spawns triggered by plugin
hooks and extensions at session start. This does not aim to fix the
Windows-only extension-host `ready-timeout` race directly (aperture-labs#7326
— that race is inside the CLI host's own process, outside this repo) — it
aims to reduce the facility's own contribution to concurrent process-creation
load at the exact moment that race is decided, on the theory (not yet
proven) that fewer simultaneous cold spawns means less contention for
whatever margin the host's fixed timeout allows.

## Participants

Single-repo, single-agent effort for now — no cross-machine dispatch.

## Context

- **Origin:** a live investigation into aperture-labs#7326 (Windows-only
  `ready-timeout` mass-reload/never-loaded extension race) led to empirical
  process-spawn diagnostics on a Windows facility machine, which surfaced two
  independently un-optimized flows:
  - **Extensions:** one `node.exe` per enabled plugin extension, batch
    -launched at session start. ~67ms cold-start measured for a representative
    workload.
  - **Hooks:** `hooks.json`'s `"type": "command"` contract mandates an inline
    `"powershell"`/`"bash"` script body per declared hook entry; the host
    spawns a fresh `powershell.exe` (~210ms measured) or `pwsh.exe` (~290ms)
    per entry. Several plugins declare 2+ `sessionStart` entries each.
    `agent-worktrees` additionally spawns a second cold interpreter
    (`python.exe` running `hook_client.py`) from inside that already-heavy
    `powershell.exe` process — though that script is itself a thin, already
    -correct dispatcher to a warm daemon per `work-coalescing-singleton`, so
    the avoidable cost is the process hop, not redundant computation.
  - A synthetic concurrency test showed Windows `CreateProcess` tail latency
    spikes non-linearly under concurrent load: one 5-way simultaneous batch
    hit 282ms vs. a 60-110ms typical range, with in-process logic staying
    flat at ~4ms regardless.
- **New pattern doc:** [`docs/patterns/cold-spawn-latency-budget.md`](../../docs/patterns/cold-spawn-latency-budget.md)
  captures the measured numbers and the standard approach this effort
  implements. Read it before touching any phase below.
- **Prior art this builds on:** [`docs/patterns/work-coalescing-singleton.md`](../../docs/patterns/work-coalescing-singleton.md)
  (the warm-daemon dispatch shape, already implemented for agent-worktrees'
  classify/list accelerator and agent-mcp's multiplexer) and the completed
  `efforts/2026/09/13 plugin-process-hygiene/README.md` effort this
  generalizes to the session-start launch batch specifically.
- **Cross-repo issue note:** this repo's `planning-efforts` skill states
  "only issues in *this* repo may directly link effort files in this repo."
  The facility (aperture-labs monorepo) that operates this machine files all
  tracking issues in its own Gitea instance regardless of which repo the code
  lives in (its `file-issue` skill's Gitea-only filing policy) — so the
  umbrella issue for this effort is aperture-labs#7382, referenced by number/
  URL rather than a direct in-repo link. Sub-issues, if filed, follow the same
  convention.

## Request

Operator (in a facility Copilot CLI session investigating aperture-labs#7326):
> "What seems the most-optimum way to define our hooks and extensions? Stick
> with JS until we feel compelled to hand off to python?"

Followed by, after the diagnostic answer:
> "Yes, do that [write it up], then let's get an effort going to make
> improvements based on these ideas."

## Plan

### Phase 1 — Audit current hook/extension footprint
- [ ] Enumerate every enabled plugin's `hooks.json`: count declared entries
      per event (`sessionStart`/`preToolUse`/`postToolUse`/`sessionEnd`),
      and classify each entry's body as (a) trivial/inline-only work, (b) a
      dispatcher to an existing warm daemon (`work-coalescing-singleton`
      consumer), or (c) real cold work with no daemon path today.
- [ ] Confirm which plugins' extensions currently do any work before
      signaling `ready` (candidates for the "optimize time-to-ready" lever in
      the new pattern doc) — start with agent-bridge, the one plugin already
      flagged for this in #3098.
- [ ] Produce a short table (plugin, event, entry count, classification) as
      the baseline this effort measures improvement against.

### Phase 2 — Merge safe sibling hook entries
- [ ] For plugins with 2+ `sessionStart` entries with no independent-timeout
      or independent-failure-isolation requirement (per the pattern doc's
      merge criterion), combine into one script body / one `hooks.json`
      entry. Candidates from the original diagnostic: `context-handoff`,
      `agent-machines`, `agent-bridge` (each declare 2 today).
- [ ] Re-measure per-plugin `powershell.exe` spawn count at session start
      before/after.

### Phase 3 — Native wire-protocol client prototype
- [ ] Prototype a PowerShell (and bash) client for `hook_ipc.py`'s existing
      wire protocol, scoped to the "check for a warm daemon, dispatch, else
      report unavailable" path `hook_client.py` implements today.
- [ ] Compare cold-spawn cost: `powershell.exe` (mandatory) → `python.exe`
      (`hook_client.py`) → warm daemon, vs. `powershell.exe` (mandatory) →
      warm daemon directly, no `python.exe` hop.
- [ ] If the native client proves out, apply it to `agent-worktrees`' hooks
      first (the one confirmed two-hop consumer today); preserve
      `work-coalescing-singleton`'s invariants exactly (bounded wait, correct
      inline fallback) — do not regress correctness for latency.

### Phase 4 — Re-measure and report
- [ ] Re-run the Phase 1 audit and the original diagnostic harness after
      Phases 2-3 land; quantify the reduction in per-session cold-spawn count
      and total wall-clock at session start.
- [ ] If facility tooling allows correlating session-start spawn load against
      `ready-timeout` frequency over time, report that correlation on
      aperture-labs#7326 — but do not claim this effort "fixes" #7326 unless
      that correlation is actually observed; the host-side race is a
      separate, unconfirmed causal link.

## Validation Plan

- [ ] Phase 1's audit table is checked into this effort (or a linked sub-doc)
      and cited by later phases — no phase proceeds on an unverified count.
- [ ] Each hook merge (Phase 2) keeps existing plugin test suites green
      (`tools/run-plugin-tests.py <plugin>`) and is validated by a live
      session-start check that the merged hook still produces the same
      observable effect (e.g. `instructions/<plugin>/*.instructions.md`
      still gets written) as the two separate entries did.
- [ ] The native client prototype (Phase 3) is validated against
      `work-coalescing-singleton`'s own adversarial scenarios where
      applicable (absent-daemon, daemon-dies-mid-request) — a faster hop that
      loses the correct-fallback guarantee is a regression, not an
      improvement.
- [ ] Phase 4's before/after numbers are captured from the same machine/CLI
      version pairing where possible, to keep the comparison meaningful.

## Proposal

_Pending — Phase 1's audit will inform which plugins are worth touching in
Phases 2-3; not every plugin's hooks are necessarily worth the merge/rewrite
effort._

## Journal

### 2026-09-21 — Kickoff
- Effort created directly off the aperture-labs#7326 investigation and the
  operator's explicit ask to turn the resulting diagnostics into planned
  work. Umbrella tracker filed as aperture-labs#7382. New pattern doc
  `docs/patterns/cold-spawn-latency-budget.md` written and indexed in
  `docs/patterns/README.md` in the same change.
- Scope deliberately kept to the session-start hook/extension launch batch —
  the narrower cross-version-runtime-consolidation idea (aperture-labs#7378)
  is related but not required for this effort's phases and is left as its
  own separate thread.
