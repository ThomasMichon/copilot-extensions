---
visions:
  - visions/harness-guidance
---

# Local-Cache Delivery Primacy

- **Slug:** `local-cache-delivery-primacy`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-10-02
- **Status:** Active
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
is downgraded (in framing, not in mechanism -- nothing about the actual
render/precedence code changes) to being purely the fallback that exists
to handle two cases:

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
  sessions through several distinct paths (local/command, containers,
  GitHub Codespaces, an SSH/remote mesh) and currently wires none of them.
  This effort's first slice closes the **local/command** spawn path (the
  one with the same direct-filesystem access `agent-worktrees`' own
  wiring relies on); container/codespace/SSH paths are explicitly
  out-of-scope follow-on slices (confirmed with the operator), since each
  needs a *remote-exec* variant of the render call rather than a direct
  filesystem call.

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
The local/command path resolves a `target.cwd` directly on the same
filesystem the agent-bridge daemon itself runs on -- directly analogous to
`agent-worktrees`' own `worktree_creation._create_worktree_core` /
`resolve_launch_cli._resolve_resume_context` call sites. Container,
Codespace, and SSH-mesh spawn paths resolve a *remote* `cwd` the daemon has
no direct filesystem access to; rendering there needs a remote-exec call
(a follow-on slice, not this effort's Phase 2).

## Request

Operator (verbatim, this session): "The intent of my effort here is that
the *primary* way of delivering per-plugin instructions is now via the
live-copy to `.local` mechanism. The checked in instructions should now
only be a 'fallback', so handle cases where an agent boots in a state
where it won't run hooks, or be able to sync *before* the session starts,
like what agent-worktrees (and agent-bridge) can provide."

Scoping follow-up, confirmed by the operator: the vision reframing is
exactly as proposed (no additional nuance to the scheduled sync-worker's
own cadence); `agent-bridge` wiring is scoped to the local/command spawn
path first, with container/codespace/SSH as explicitly named follow-on
slices; no other pre-session boundary besides `agent-bridge` was flagged.

## Plan

### Phase 1 -- Vision and pattern-doc reframing
- [ ] Revise `visions/harness-guidance/README.md`'s `resilient-safety-boundary`
      behavior to state the lifecycle-hook-rendered local cache as the
      primary delivery path for worktree-scoped projected instruction
      content, with the checked-in copy as strictly the fallback for a
      hookless boot or a not-yet-rendered pre-session gap.
- [ ] Revise `docs/patterns/worktree-scoped-dynamic-guidance.md`'s framing
      (Problem/Standard approach/Rationale sections) to match: the
      checked-in copy is introduced as the fallback tier, the local cache
      as the primary tier, not the reverse. No change to the actual
      precedence mechanism (the preamble, the catch-all, the render
      functions) -- this phase is reframing, not re-implementation.
- [ ] Cross-check `docs/patterns/session-scoped-dynamic-guidance.md` for
      any framing that would now read inconsistently with this
      reprioritization (it covers a different, per-session-computed-facts
      case, so likely needs no change -- confirm rather than assume).

### Phase 2 -- `agent-bridge` local/command spawn-path wiring
- [ ] Add an `agent_bridge`-side equivalent of
      `agent_worktrees.local_cache_refresh` (resolve customizing-copilot's
      declared `render-local-cache` CLI the same bounded-timeout,
      never-raising, global-activation-scoped way Phase 7's
      `agent_worktrees.local_cache_refresh` does -- no cross-plugin
      reach-around; see `docs/patterns/a-la-carte-independence.md`).
- [ ] Call it from `session_start.py`'s local/command spawn path, after
      the target's `cwd` is resolved and before the Copilot CLI process is
      actually spawned, best-effort (never raises, never gates/delays the
      spawn on a slow or failing render).
- [ ] Guard test: a synthetic repo with a stale checked-in projection and
      a divergent installed payload gets its `.local.instructions.md`
      sibling refreshed by a local-spawn `start_session` call, proven
      against the real `render_local_cache()` call (not a stub).
- [ ] Negative-proof test: customizing-copilot not installed, the repo not
      yet trusted, or a render failure must never block or delay a
      local-spawn session start.

## Validation Plan

- [ ] `tools/run-plugin-tests.py customizing-copilot agent-bridge` (and
      `agent-worktrees` if its own tests are touched) pass;
      `check-changefile-presence` / `check-version-consistency` /
      `check-docs-consistency` clean on every PR.
- [ ] A clean-room-style proof (matching the methodology
      `ambient-guidance-navigability`'s own Phase 7 used) that a
      local-spawned `agent-bridge` session actually sees the fresher
      `.local.instructions.md` content when the checked-in copy is stale --
      not just that the unit-level render call fires.

## Proposal

_Pending._

## Journal

### 2026-10-02 -- Kickoff
- Carved from a direct operator follow-up to the just-archived
  `ambient-guidance-navigability` effort: the operator clarified the
  Phase 7 mechanism's intended role is a reprioritization (local-render
  primary, checked-in fallback-only), not merely a supplementary patch.
  Confirmed scope with the operator (vision reframing as described;
  `agent-bridge` wiring scoped to local/command spawn path first,
  container/codespace/SSH as named follow-on slices; no other pre-session
  boundary flagged). Effort created, premise captured.
