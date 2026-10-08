# Worktree-Scoped Dynamic Guidance

**Serves:** Vision `harness-guidance` (feature `concise-context-kernel`;
behaviors `resilient-safety-boundary`, `ambient-delivery-fails-open`,
`resume-stable-context`).

> **Precedence note (`local-cache-delivery-primacy`):** the gitignored
> local cache this pattern describes is the **primary** delivery path for
> worktree-scoped projected instruction content -- it reflects the
> currently installed payload, not a sync-lagged approximation of it. The
> checked-in copy below is strictly the **fallback**: the floor a session
> falls back to only when no pre-session hook could render anything
> fresher, or hasn't yet had the chance to. Precedence between the two is
> decided by comparing their own embedded marker `pluginVersion` (§2),
> never by the local file's mere existence -- a stale leftover must not be
> able to outrank a genuinely newer checked-in copy.

## Problem

[`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
gives every plugin a proven delivery path for genuinely per-session
**computed** facts. Its own §2a rule is explicit about what does *not*
belong there: "the session-folder file carries only the values that had to
be computed this session... everything else... is a second, ordinary static
projection... checked in and reviewed like any other static fail-safe."

Projected static instruction content -- the rendered body of a plugin's own
`instructions/*.instructions.md` template, synced into a consumer repo via
`projection-reflect`/`instruction-projections.json` -- is exactly that
"everything else" case. It is:

- **too large and too stable** to regenerate on every `sessionStart` (that
  would rewrite identical text into a fresh file every session, burning
  budget and producing content that can't be reviewed or diffed as checked-in
  guidance, the precise cost §2a exists to avoid); and
- **not session-specific at all** -- the same rendered content applies to
  every session in a given worktree, changing only when the plugin's
  installed payload changes or the consumer repo's enablement changes.

So this content is correctly modeled as belonging in the **checked-in**
`.github/instructions/**/*.instructions.md` copy the sync worker maintains --
never in a per-session file. But relying on the checked-in copy alone as the
*only* source of truth surfaces two real gaps:

1. **Sync-lag is user-facing, and the fix requires rights an ordinary
   contributor may not have.** The checked-in projection is only as fresh as
   its last merged `projection-reflect` sync PR, and landing that PR requires
   push/merge rights on the consumer repo. A session-start mechanism that
   tries to perform (or force) that sync itself, on behalf of whoever is
   running the session, fails outright for a contributor who lacks those
   rights -- an environment/authorization problem masquerading as a guidance
   bug.
2. **Some launch paths have no hook and no session-state folder at all** (a
   fully headless, sandboxed, or cloud-hosted agent invocation). For those,
   the checked-in copy is -- correctly -- the only thing that can ever be
   present. But every *other* launch path, which could easily have something
   fresher, currently settles for that same floor too.

## Standard approach

A third delivery tier, **worktree-scoped** rather than session-scoped or
purely checked-in: a gitignored, locally re-rendered cache that lives beside
the checked-in projection for as long as the worktree exists, refreshed at
worktree lifecycle boundaries rather than every session start.

### 1. The gitignored sibling file

Every checked-in projection destination
`.github/instructions/<plugin>/<sourceId>.instructions.md` gains a gitignored
sibling at `.github/instructions/<plugin>/<sourceId>.local.instructions.md`,
**except** a source that opts out via its own declaration's
`skipLocalCache: true` (the repo-wide catch-all in step 3 is the one shipped
example: its own sibling would match its own
`**/*.local.instructions.md` scan glob and get read back, repeating the
identical directive for no benefit). The consumer repo's
`.github/instructions/.gitignore` (or an equivalent
recursive rule) covers the whole tree:

```gitignore
**/*.local.instructions.md
```

The sibling holds a fresh re-render of the **currently installed** payload --
never a network fetch, never a marketplace check, purely a local, offline
render from whatever plugin payload already sits on disk. Keeping installed
payloads themselves current remains the scheduled `projection-reflect` sync
worker's job (its own `update --force` step); this cache only answers "given
what's already installed right now, what would the correct projection look
like," which is cheap, safe to run unprompted, and requires no repository
write permission of any kind.

### 2. The checked-in file defers to its local sibling by provenance, not existence

The checked-in projection template gains a short, literal preamble ahead of
its rendered body:

```markdown
> If `<sourceId>.local.instructions.md` exists here, compare
> `pluginVersion` and prefer whichever is newer. On a tie, compare
> `templateSha256`: matching means prefer local; differing means
> prefer this checked-in file.
```

This is the common-case path: once a source has been synced in at least once,
its checked-in file self-directs to its own fresher sibling with no
additional lookup -- but the comparison is by **declared version**, not mere
presence, so a stale sibling left over from an earlier render (a boot where
nothing re-rendered it since) can never outrank a checked-in copy that has
since moved ahead. A render *timestamp* cannot serve this role: an
older/regressed installed payload rendered *after* the checked-in copy
advances would still carry the later timestamp and win, recreating the exact
staleness bug this comparison exists to prevent -- and a changing timestamp
field would break this render's own byte-determinism. The markers' existing
`pluginVersion` (and `templateSha256` as the tie-break for an ambiguous equal
version with differing content, since a version string is not an immutable
source identity) are what `render_projection()` already stamps into every
rendered file, so no new field is needed.

### 3. A repo-wide catch-all for sources with no checked-in file yet

The per-file preamble above cannot help a source that has **never** been
synced in -- there is no checked-in file yet to carry it. A single,
repo-wide, unconditionally-loaded static projection closes that gap:

```markdown
---
applyTo: "**"
---

Check `.github/instructions/**/*.local.instructions.md` for any files
present now and read each one. When a checked-in `.instructions.md` file
exists for the same plugin and source, compare both files' embedded
marker `pluginVersion` fields and prefer whichever is newer; on a tie,
compare `templateSha256` instead of whole-file bytes (which always
differ -- only the checked-in file carries the preamble) -- prefer the
checked-in file only if that hash differs too, otherwise the local file
stays authoritative. With no checked-in file yet for that path, the
local file is authoritative on its own. Their absence is not an error.
```

This file is the one thing every launch path -- hooked or hookless, worktree
or anchor, App-forked or CLI-forked -- loads unconditionally, because it is
ordinary checked-in content like any other `.instructions.md` file. It never
depends on a hook having run.

### 4. Refresh at worktree lifecycle boundaries, not every session

The render step is triggered by `agent-worktrees` at worktree **create and
resume** -- before the first (or next) session in that worktree even starts,
so the harness's own directory scan (if it reads live files rather than a git
index) may pick it up with zero reliance on the catch-all at all. This
mirrors the existing manual "an anchor-repo user restarts to pick up a
freshly-synced set" behavior, made automatic and moved earlier. **Landed**:
`agent_worktrees.local_cache_refresh.refresh_local_cache()` locates
`customizing-copilot`'s installed, declared `render-local-cache` CLI
operation (`manage-instruction-projections.py`) and invokes it as a
bounded-timeout subprocess (via `push_timeout.run_bounded`, which kills the
invoked CLI's whole process tree on a stall, not just its direct child) --
never importing that plugin's Python package directly, per
[`a-la-carte-independence.md`](a-la-carte-independence.md)'s "no
cross-plugin reach-around" rule. The sibling's root is resolved through
`plugin_activation.resolve_active_plugins()` -- the same identity-verified
active-plugin evidence `claim_providers.py` uses for its own sibling
callbacks -- rather than trusting a directory merely because it
self-declares the expected name in a `plugin.json`, per
[`marketplace-installation-cells.md`](marketplace-installation-cells.md)'s
"plugin name alone never selects a runtime" invariant; only the plugin's
**global** activation scope is trusted (never a project-scoped override,
which could otherwise let one repo's local dev override of
customizing-copilot execute against an unrelated repo's worktree), and
missing or ambiguous provenance (zero, or more than one, matching active
plugin) fails closed. This resolution step itself runs in its own
bounded subprocess (`python -m agent_worktrees.local_cache_refresh
<home>`) rather than in-process or on a bare thread, since the resolver
can spawn Git child processes verifying registered projects that only a
real process-tree kill can guarantee don't outlive a timeout.
`worktree_creation._create_worktree_core` and
`resolve_launch_cli._resolve_resume_context` (skipped on `--dry-run`) both
call it at exactly the point described above. Fully best-effort: customizing-
copilot not being installed, the repo not yet being trusted, a subprocess
timeout, or any other render failure are all silently absorbed, never
gating create/resume itself.

The plugin's `sessionStart` hook repeats the same render as a backup, to
catch payload drift accrued between a worktree's creation/resume and the
current session's own start. Because the render is a pure side effect (never
`additionalContext`), the timing race that makes hook-written content
unreliable for *this* session's preloaded instructions does not apply here:
the catch-all instruction from step 3 drives an explicit, first-turn tool
read, which always executes strictly after the hook has finished -- a
guarantee that depends on this backup refresh running **synchronously**
within the hook's own request handling, never dispatched to a background
thread. **Landed**: `agent_worktrees.__main__._run_session_lifecycle` (the
real `sessionStart` handler `hook_client.py`'s thin client dispatches to)
calls `local_cache_refresh.sessionstart_diagnostic()` as this synchronous
backup step, alongside its existing anchor-hygiene and provisioning
diagnostics. Its subprocess call is bounded by a timeout derived from the
hook's own remaining decision-deadline budget (capped at
`local_cache_refresh.SESSIONSTART_MAX_TIMEOUT_S`), and skipped entirely once
too little budget remains -- so it can never itself cause the resident hook
server to miss its own response deadline.

`agent-bridge`'s own `target.type == "local"` spawn path is the one
remaining local-spawn boundary `create`/`resume`/`sessionStart` above don't
already cover -- a dispatched agent launched through the bridge rather than
an interactive worktree session. **Landed**:
`agent_bridge.local_cache_refresh.refresh_local_cache()` is the
`agent-bridge` counterpart, called from `session_host_connection.py`'s
`_connect_via_session_host`, right after `resolve_local_launch` resolves
the authoritative local `work_dir` and before `spawner.spawn()` actually
launches the Copilot CLI process -- not at `session_start.py`'s own
`target.type == "local"` entry, where a project-backed target's real
directory isn't known yet. Translated to asyncio idioms rather than copied
verbatim --
`agent_worktrees.local_cache_refresh` is free to block its own one-shot CLI
process, but this call sits inside `agent-bridge`'s own long-lived event
loop, shared by every concurrent session the daemon serves, so every
subprocess call here is natively async (never a synchronous call or a
background thread a timeout could only abandon, not actually stop).

### 5. The checked-in copy remains the unconditional floor

Nothing about this pattern adds a second write path to git. The scheduled
`projection-reflect` sync worker remains the only writer of the checked-in
projection, unchanged, still the durable and reviewable record. A
write-incapable launch path (one that cannot write even a gitignored local
file) simply never populates the local tier and falls through to exactly
what it gets today -- no regression, and no session-facing error either way.
"Floor" here means the guaranteed-present fallback, never the *preferred*
tier when something fresher is actually available (see the precedence note
above) -- a floor a stale local artifact can silently stand on top of is not
a floor at all.

### 6. Local delivery and context auditing are separate

The local cache installs complete content from every safely resolved enabled
source even when its template, rendered file, or aggregate exceeds the
configured guidance budget. These excesses are warning findings with source
and byte-count attribution, not admission failures or reasons to erase existing
guidance. Invalid or unreadable budget configuration also warns and retains
default size accounting; it does not prevent a safe local render.

The renderer retains separate safety limits for bounded input/provenance reads,
safe paths, source identity, tracked destinations and foreign files. Oversized
local guidance remains refreshable and reconcilable within those safety bounds.
Those protections are not context-budget enforcement.

The checked-in `scan`/`sync` path keeps its independent budget findings and
reviewed transaction policy. Existing periodic audits can report budget debt
without making a worktree's immediate guidance delivery depend on that audit
or on a projection-sync PR.

## Rationale

This does not compete with
[`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md);
it fills the gap that pattern's own §2a rule deliberately leaves open. Both
patterns share the same core insight (a checked-in static pointer plus an
agent-initiated file read beats hook-emitted `additionalContext`
composition), applied at a different granularity: per-session for genuinely
computed facts, per-worktree for large/stable projected content that a
consumer repo cannot always guarantee is freshly checked in.

Separating "keep the canonical, reviewable record accurate" (the sync
worker's job, privileged, PR-gated, unchanged) from "make sure *this*
worktree's operating instructions are current" (this pattern, permissionless,
purely local) also directly fixes the reported failure mode: no session,
regardless of the acting identity's repository permissions, ever needs to
attempt a privileged sync merely to see current guidance.

## Exemplars

`customizing-copilot:reviewing-customizations`'s
`instruction_projections.render_local_cache()` and
`local_sibling_destination()` are the reference implementation of this
pattern's render side, landed as part of
`efforts/2026/10/02 ambient-guidance-navigability` Phase 7
([ThomasMichon/copilot-extensions#4674](https://github.com/ThomasMichon/copilot-extensions/issues/4674)).
The per-file "prefer local" preamble (step 2), the repo-wide catch-all
projection (step 3, opted out of its own local cache per step 1's
exception), and the `agent-worktrees` create/resume + `sessionStart` wiring
(step 4, via `agent_worktrees.local_cache_refresh`) have all landed --
Phase 7's **Plan and Validation Plan are both complete -- Phase 7 is Done.**
A clean-room, agent-driven proof (3 tool-forbidden `explore` sub-agents per
scenario, given only a frozen snapshot) confirmed the preamble and the
catch-all each independently drive an agent to the fresher
`.local.instructions.md` content over a stale or absent checked-in file
(see the effort README's own Journal for the scenarios and results).
`efforts/2026/10/03 local-cache-delivery-primacy` Phase 1 later replaced the
existence-only precedence this proof covered with the marker-provenance
comparison §2/§3 above describe, closing the stale-sibling gap that
existence-only check left open. That same effort's Phase 2 landed the
`agent-bridge` local-spawn-path wiring step 4 describes above
(`agent_bridge.local_cache_refresh`), closing the one remaining local-spawn
boundary the Phase 7 wiring didn't already cover.

## See Also

- Vision: `visions/harness-guidance/README.md`
- [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
  -- the sibling pattern for per-session computed facts; read both before
  choosing where new dynamic content belongs.
- `efforts/2026/10/02 ambient-guidance-navigability/README.md` (Phase 2 --
  `projection-reflect`, the sync mechanism this pattern's checked-in floor
  depends on; Phase 7 -- this pattern's own implementation)
