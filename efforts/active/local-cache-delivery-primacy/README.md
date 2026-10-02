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
is downgraded in framing to being purely the fallback that exists to
handle two cases -- which, in turn, means the precedence *mechanism*
itself needs one real fix (not just reframing) to actually deserve that
trust: a fallback that a stale leftover local file can silently outrank
is not a safe fallback (see Phase 1's stale-sibling item below, caught by
this effort's own plan-review PR). With that fix landed, the mechanism
serves:

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
- [ ] **Design and land a stale-sibling invalidation check** (caught by
      PR #4926's own review, a real gap in the *existing* precedence
      mechanism that this reframing makes load-bearing rather than
      cosmetic): today a local sibling is preferred purely by existence
      (`instruction_projections.py`'s `render_projection` preamble), and
      is only ever reconciled when `render_local_cache()` itself runs
      (`_render_local_cache_locked`'s stale-removal pass, which only
      fires for a *disabled* source, not a merely-outdated one). A sibling
      left by an earlier *successful* render can therefore silently
      outrank a *newer* checked-in projection on a later boot where no
      pre-session render succeeds (hookless boot, a bounded-timeout miss,
      or any other render failure) -- precisely the boot this effort's
      reframing says must fall back safely to the checked-in copy. Design
      direction: embed a render timestamp in **both** the checked-in
      projection's marker and the local cache's own marker (neither
      carries one today), and rewrite the preamble/catch-all directive
      text so the reading agent compares the two markers' timestamps and
      prefers whichever is actually newer -- never "prefer local merely
      because it exists." This is a real code change to
      `instruction_projections.py` (the marker schema and the preamble/
      catch-all template text), not pure prose -- land it as part of this
      phase's own PR, with full regression coverage against every
      existing preamble/marker test.

### Phase 2 -- `agent-bridge` local spawn-path wiring
- [ ] Add an `agent_bridge`-side equivalent of
      `agent_worktrees.local_cache_refresh` (resolve customizing-copilot's
      declared `render-local-cache` CLI the same bounded-timeout,
      never-raising, global-activation-scoped way Phase 7's
      `agent_worktrees.local_cache_refresh` does -- no cross-plugin
      reach-around; see `docs/patterns/a-la-carte-independence.md`).
- [ ] Eligibility is **`target.type == "local"` only** -- never `"command"`
      (that shape also covers Codespaces, containers, elevated relays, and
      other providers, often with no `target.cwd` at all; see
      `session_start.py`/`agent_registry_resolver.py`). Confirm the
      resolved `target.cwd` is a real, local, trusted directory before
      calling the renderer.
- [ ] Call it from `session_start.py`'s `target.type == "local"` branch,
      after `target.cwd` is resolved and before the Copilot CLI process is
      actually spawned, bounded by a **short, explicit latency budget**
      (a few seconds -- sized against this plugin's own existing
      `SESSIONSTART_MAX_TIMEOUT_S`-style precedent, not the create/resume
      path's more generous 30s default, since this sits directly in the
      spawn's own critical path) -- completing the render before spawn
      necessarily gates spawn for up to that bound; it is never
      unbounded, and a timeout or any other render failure is absorbed
      (spawn proceeds against whatever the checked-in floor already has)
      rather than failing the spawn itself.
- [ ] Guard test: a synthetic repo with a stale checked-in projection and
      a divergent installed payload gets its `.local.instructions.md`
      sibling refreshed by a local-spawn (`target.type == "local"`)
      `start_session` call, proven against the real `render_local_cache()`
      call (not a stub), completing within the defined latency budget.
- [ ] Negative-proof test: customizing-copilot not installed, the repo not
      yet trusted, or a render failure must never fail the spawn itself,
      and the render call is proven bounded by the defined latency budget
      (not merely asserted zero-delay, which is unachievable for a
      synchronous pre-spawn render).
- [ ] Negative-proof test: a `target.type == "command"` (or any non-local)
      spawn never invokes the local renderer at all, including the
      specific case of a `"command"` target with no `target.cwd` present.

## Validation Plan

- [ ] `tools/run-plugin-tests.py customizing-copilot agent-bridge` (and
      `agent-worktrees` if its own tests are touched) pass;
      `check-changefile-presence` / `check-version-consistency` /
      `check-docs-consistency` clean on every PR.
- [ ] A clean-room-style proof (matching the methodology
      `ambient-guidance-navigability`'s own Phase 7 used) that a
      local-spawned (`target.type == "local"`) `agent-bridge` session
      actually sees the fresher `.local.instructions.md` content when the
      checked-in copy is stale -- not just that the unit-level render call
      fires -- and that the render's latency stays within Phase 2's
      defined budget.
- [ ] **Stale-sibling-boot negative-proof** (the gap PR #4926's review
      caught): a `.local.instructions.md` sibling rendered from an older
      installed payload, left in place while the *checked-in* projection
      is subsequently updated to a genuinely newer version (the scheduled
      sync worker advanced it independently of this worktree's own local
      render), must be superseded by the checked-in copy on a boot where
      no render runs at all -- proven against the real marker-comparison
      logic Phase 1 lands, not asserted by inspection.

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
- **PR #4926 review caught two real issues, fixed same-session:**
  - This worktree's own `agent-worktrees` lifecycle wiring rendered 7 real
    `.local.instructions.md` siblings for this repo's own consumed
    sources (the exact dirty-tree gap the operator flagged live, in
    conversation, independently of this PR) -- and since this repo had no
    `.github/instructions/.gitignore` yet, `git add -A` picked them up and
    committed them. Untracked them (`git rm --cached`) and added the
    ignore rule here too (also landing separately via #4930, found and
    fixed as its own atomic pre-existing-issue commit the moment the gap
    was identified; both repos this operator-identified gap touched --
    `copilot-extensions` and `aperture-labs` -- now have the rule).
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
