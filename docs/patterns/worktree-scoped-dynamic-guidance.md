# Worktree-Scoped Dynamic Guidance

**Serves:** Vision `harness-guidance` (feature `concise-context-kernel`;
behaviors `resilient-safety-boundary`, `ambient-delivery-fails-open`,
`resume-stable-context`).

> **Precedence note (`local-cache-delivery-primacy`):** the gitignored
> local cache this pattern describes is the **primary** delivery path for
> worktree-scoped projected instruction content -- it reflects the
> currently installed payload, not a sync-lagged approximation of it. The
> reviewed checked-in body below is strictly the **fallback**: the floor a session
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

Every projection destination
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

### 2. Selectors, reviewed bodies, and exact authority

Declarations explicitly choose `deliveryMode: "selector"` or `"inline"`.
Undeclared mode remains legacy inline for compatibility; the shipped suite
declares every source. A selector replaces the checked-in full body at the
existing `.github/instructions/<plugin>/...instructions.md` destination.
Its complete reviewed body is owned separately at the derived literal path
`.github/copilot/context-fallbacks/<plugin>/...md`, preserving the destination's
subdirectories. This path is outside instruction discovery, has no instruction
suffix, and is referenced using code spans rather than auto-expanding links.

The version-2 lock owns both artifacts, their digests and byte counts. Sync
validates legacy version-1 lock/preimages before migrating; fallback, selector
and lock participate in the same compare-before-replace rollback transaction.
Changing a selector back to inline retires only its integrity-verified owned
fallback in that transaction; rollback restores it. Foreign edits block
retirement, and unrelated/orphan files are never swept automatically.
Missing/malformed reviewed content, foreign ownership and unsafe paths block
resolution. Source-update freshness remains an advisory distinct from those
integrity failures. A new enabled source without reviewed artifacts blocks as
`projection-missing` until its complete enabled canonical local body is verified;
partial, forged or absent bodies do not qualify. A required cache-free inline
control kernel still needs its static delivery floor. Authenticated complete
local delivery permits advisory `projection-source-update` for pending reviewed
freshness; valid stale reviewed guidance remains advisory even without local
refresh. An unowned existing destination or missing locked artifact still
blocks. Adopted, consented
deterministic maintenance normally refreshes reviewed projections once daily;
ordinary work does not require synchronous checked-in resync. This does not
assert that installed payloads or running systems have been updated.

Before dependent/consequential action the reader acquires authoritative
content, or reports a visible blocker without assuming authorization.
`manage-instruction-projections.py resolve-source <repository> <destination>
--from-settings --json` performs read-only exact selection. Both paired and
unpaired locals must match a canonical render of the currently enabled payload;
self-consistent cache markers/hashes/receipts cannot authenticate provenance.
Without enabled canonical proof, paired resolution uses the reviewed fallback.
Newer `pluginVersion` wins; equal
version/equal `templateSha256` favors local; equal version/different hash favors
reviewed content. Owner identity includes marketplace, plugin, source ID,
destination and `applyTo`. Local content must reconstruct its canonical
template hash, including legacy bodies. A malformed local is rejected with a
diagnostic while the valid reviewed fallback remains usable.

For local authority, supply `--from-settings` (and the attributable
`--agent-worktrees-path` where required): the utility validates the enabled
declaration/canonical template rather than trusting arbitrary cache files.
Without canonical payload verification, retain reviewed fallback authority;
metadata comparisons alone are insufficient. Neither timestamps nor file
existence establish authority.

The resolver is a script, not a bare executable subcommand. Selectors expose
its exact argv and instruct the agent to invoke `reviewing-customizations`;
the tool-returned skill base locates the owning payload without a global PATH
entry or installed-directory scan. An absolute Python interpreter and checkout
root complete the three explicit argv placeholders. Catalog-based marketplace
resolution may additionally require `--agent-worktrees-path`. A fresh-fixture
test executes the actual rendered argv with an empty PATH, exercising settings
discovery and canonical paired-cache selection rather than mocking the parser.

#### Inline decision kernels

The explicit inline allowlist retains existing policy prose intact:

- `agent-conduct-guidance`: `process-hygiene-fallback` governs every spawn;
  `delegation-fallback` governs the initial research/delegation decision;
  `scratch-space-fallback` governs every ad hoc write;
  `secret-masking-fallback` governs credential-shaped construction.
- `ai-attribution:publication-safety` governs publication before it happens.
- `copilot-extensions-harness:contribution-boundary` governs contribution intake.
- `efforts:completion-gate` governs termination and completion claims.
- `customizing-copilot:local-cache-catchall` is the recovery control kernel.

This is a source-owned declaration list, not a substring classifier. Other
sources are acquisition-gated procedures/pointers. No policy prose is slimmed
to fabricate savings; inline kernels can remain duplicated when both copies
are discovered, because their identified decision-point contract requires
ambient retention.

### 3. Per-source body evidence and late-source recovery

Full modern bodies carry opening provenance (`deliveryKind: "body"`,
`bodySha256`) and a closing `copilot-guidance-body-end:v1` receipt whose
`bindingSha256` hashes the canonical marketplace/plugin, source ID, version,
template hash, scope and body hash. This compact binding covers every identity
field without repeating the metadata corpus at each boundary. The selection
utility exposes the exact binding; never compare it by freehand transcription.
Compact version-2 selectors retain source identity, scope, version/hash,
destination and rendered size; full template provenance remains in the lock.
Selectors contain no body receipt. The pure envelope validator detects omitted
middle bytes, truncated bodies, receipt-only summaries and quoted envelopes.
It verifies byte completeness, **not model admission or compliance**.

The catch-all remains unconditionally inline and opts out of its own local
cache. It inventories after startup because a bare launch can discover
instructions before a hook writes local bodies. It covers late, partial and
unpaired sources without treating a global marker as whole-corpus coverage.

A read may be skipped only if the complete selected body and matching opening/
closing provenance are directly visible in the current applicable context.
Filesystem existence, quoted examples, omitted ranges, truncated tool output,
compacted summaries and earlier-context claims are insufficient. Legacy
receipt-free bodies remain readable but cannot license skipping. On uncertainty
read the full selected content. Recompute selection/coverage after source
changes, resume or reconstruction; persist no loaded-state. No global coverage
file is required.

The scanner separates automatically discovered selectors, inline kernels and
local bodies from reviewed on-demand fallbacks. Actual admission and selective
reads remain unknown without runtime evidence; character/4 estimates are only
heuristics. Sandbox fake prompt/CLI tests establish ordering and envelope
contracts, not native CLI/App/no-hook/resume/new-context behavior or provider
credit savings. Those native acceptance cases remain **not verified** by
structural tests.

### 4. Refresh at worktree lifecycle boundaries, not every session

The render step is triggered by `agent-worktrees` at worktree **create,
resume, and local JSON launch planning** -- before the first (or next) session in that worktree even starts,
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
`plugin_activation.resolve_active_plugins(include_projects=False)` -- the same identity-verified
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
performs bounded global identity discovery without visiting unrelated registered
projects. Process-tree containment still protects its filesystem I/O.
`worktree_creation._create_worktree_core` and
`resolve_launch_cli._resolve_resume_context` (skipped on `--dry-run`) both
call it at exactly the point described above. `resolve_cli._resolve_json_mode`
also prepares the selected local base/worktree before returning its launch
command. Dry-run plans never render. Fully best-effort: customizing-
copilot not being installed, the repo not yet being trusted, a subprocess
timeout, or any other render failure never gate create/resume itself, but
return attributable outcomes and log degradation rather than disappear.
Outcomes distinguish ready (installed/unchanged), partial safe delivery,
unavailable renderer, timeout, failed render and deliberately skipped work.
Renderer JSON counts generic warnings separately from blocking findings; warnings
include budget audits, ownership handoffs and retained-file protections. An
over-budget installation is still ready.
The renderer's additive `written` and `removed` JSON lists distinguish delivery
from stale-cache cleanup; legacy `changed` remains the complete mutation list.
Consumers count only written/unchanged paths as installed guidance and put
blocking safety findings first when bounding diagnostic details. An older
renderer missing this accounting contract is reported as degraded, never
guessed to have installed files it might have removed.

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
backup step before registration and later provisioning diagnostics. It resolves
a nested session cwd back to the checkout root and includes the refresh
outcome in the lifecycle diagnostic stream. Its subprocess call is bounded by a timeout derived from the
hook's own remaining decision-deadline budget (capped at
`local_cache_refresh.SESSIONSTART_MAX_TIMEOUT_S`), and skipped entirely once
too little budget remains, with an explicit skipped diagnostic -- so it can never itself cause the resident hook
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
The bridge awaits completion before spawn and logs unavailable, partial,
failed, timeout and audit-warning outcomes. Discovery and rendering share the
actual remaining elapsed-time budget rather than reserving unused discovery
time.

Remote Session Host providers do not run this host-local renderer against a
remote path. Target-side worktree creation/resolve and installed session-start
hooks own their corresponding refreshes. A provider bypassing those boundaries
needs target-side preparation; host-local success is not evidence of remote
installation.

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

The checked-in `scan`/`sync` path also reports aggregate excess as an
attributable `projection-aggregate-budget` warning, never as an admission
gate for updated or newly enabled plugin instructions. Its per-file bounds,
configuration validation, ownership checks and reviewed transaction policy
remain enforced. A deterministic sync worker retains these aggregate warnings
for audit without routing them as conflicts or opening repeat no-diff PRs.

Use the existing periodic audit to balance the aggregate: `scan --json`
reports total bytes, the configured budget and the overrun; the customization
scanner's `--from-settings --context-budget` inventory attributes context to
sources and categories. Budget debt is remedied by trimming or moving detailed
guidance on demand, not by withholding an otherwise-safe re-projection.

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
