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

This content belongs in a reviewed worktree-scoped artifact, never a
per-session rewrite. Current delivery separates a concise checked-in
`.github/instructions/**/*.instructions.md` selector from its lock-owned,
non-auto-loaded full body in `.github/copilot/context-fallbacks/`; explicitly
declared inline kernels retain their ambient policy. Relying only on the
reviewed body as the source of truth would leave two gaps:

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
   the checked-in selector plus reviewed fallback (or explicit inline kernel)
   remains the usable offline path without cache writes. Other launch paths
   can acquire authenticated current local bodies instead; file existence
   alone does not establish their authority or delivery.

## Standard approach

A third delivery tier, **worktree-scoped** rather than session-scoped or
purely checked-in: a gitignored, locally re-rendered cache of auto-discovered
instruction siblings that lives for as long as the worktree exists, refreshed
at worktree lifecycle boundaries and session start.

### 1. The gitignored auto-discovered local body

Every projection destination
`.github/instructions/<plugin>/<sourceId>.instructions.md` gains a gitignored
body at `.github/instructions/<plugin>/<sourceId>.local.instructions.md`,
**except** a source that opts out via its own declaration's
`skipLocalCache: true` (the repo-wide catch-all in step 3 is the one shipped
example: its own body would join its recovery inventory and get read back,
repeating the identical directive for no benefit). The consumer repo's root
`.gitignore` covers the new body tree and legacy siblings during migration:

```gitignore
.github/copilot/context-locals/**/*.local.md
.github/instructions/**/*.local.instructions.md
```

Nested destination subdirectories are preserved. Local bodies are gitignored
instruction siblings that creation and resume populate before discovery.
Reviewed fallbacks are ordinary Markdown outside instruction roots, referenced
using code spans rather than automatic imports. `local_sibling_destination()`
returns the local instruction sibling path.
The body holds a fresh re-render of the **currently installed** payload --
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

Generated projections are not an authoring surface: change the canonical
template or declaration instead of touching its generated output. Adopters may
commit `overwriteExistingManaged: true` in their existing projection-reflect
consent to repair content edits on already-lock-owned regular files. The default
remains `false`. Ownership/provenance, scope, valid sources and path safety still
must validate; unmarked files, invalid locks and indirection never qualify.
Actual edited selector/inline and fallback bytes are transaction preimages,
not trusted guidance. Live consent is rechecked under the sync lock before each
write; withdrawal or a concurrent edit aborts and rolls back prior writes
when a changed preimage is observed. Authorized managed outputs are
single-authoritative-writer artifacts: unsupported content edits racing after
comparison may be superseded by exact upstream bytes. Put customizations in
repository-owned `AGENTS.md` or separate sidecar instructions, never generated
artifacts. There is no shadow/update-blocking mechanism in this contract.
Absent destinations use exclusive no-clobber publication; a concurrently
created unowned file is never replaced. Consent, lock and output integrity are
rechecked after publication, with best-effort rollback on failure.
Legacy local compatibility reads still authenticate enabled canonical bytes;
legacy markers alone are never authority. After publishing the new canonical
body, migration retires an old auto-discovered local only when its bytes match
the enabled canonical template rendered with its validated recorded version,
tracking confirms it is untracked, and its path
and preimage remain unchanged. Foreign, edited, tracked, or unsafe legacy
files are retained with visible findings. They can remain auto-discovered until
the adopter resolves the finding; relocation never silently deletes them.
Git worktrees must first adopt ignore coverage for the new tree: until
`git check-ignore` confirms coverage, publication and legacy retirement are
skipped with `projection-local-cache-ignore`. Plain non-Git directories remain
supported. Tracking/ignore probes are batched, never one Git process per body.
These checks are not filesystem content CAS or protection against
noncooperating filesystem attackers. Supported local filesystems must provide
exclusive hard-link publication and atomic rename; unsupported filesystems
fail closed, without a clobbering fallback. Fallback retirement still requires
its full integrity proof. This opt-in changes neither the eight inline floors
nor local-cache discovery; pointer-only local-body relocation is separate.

Before dependent/consequential action the reader acquires authoritative
content, or reports a visible blocker without assuming authorization.
`manage-instruction-projections.py resolve-source <repository> <destination>
--from-settings --json` performs read-only exact selection. Both paired and
unpaired locals must match a canonical render of the currently enabled payload;
self-consistent cache markers/hashes/receipts cannot authenticate provenance.
Canonical authentication and local-source admission honor the existing exact
persisted repository-folder trust boundary. Untrusted checkout settings cannot
introduce a same-named directory marketplace as authenticated authority.
Reviewed offline lock/artifact validation remains available without source
freshness or folder trust. Explicit committed-source maintenance sync is a
separate reviewable consent-owned write path, not authentication evidence.
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
auto-discovered local bodies from non-discovered compatibility bodies and reviewed fallbacks.
Actual admission and selective
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
`agent_worktrees.local_cache_refresh.refresh_local_cache()` invokes
`render-local-cache` from **agent-worktrees' own payload**, across a
process boundary. On `dev`, the canonical projection engine remains in
customizing-copilot's scripts directory; `agent-worktrees/packaged-assets.json`
declares its complete seven-file runtime closure. Promotion and release preview
vendor those files byte-identically into agent-worktrees' own
`scripts/instruction-projections/` directory. Canonical changes charge both
the authoring plugin and packaged consumers in the release/changefile guards.
There is one authoring source, not a maintained fork.

A shipped standalone agent-worktrees installation never discovers, imports,
or executes a sibling customizing-copilot payload. Its own installation
identity selects its own engine. Only a verified monorepo-shaped development
checkout may resolve the declared canonical source before materialization.
The engine discovers the selected repository's instruction sources directly,
without importing the full customization scanner or enumerating unrelated
registered repositories just to find the renderer.

Foreground create/resume waits for projection preparation to complete, with
start/end, elapsed-time heartbeats and the actual delivery outcome. The
Manager streams stderr rather than hiding it behind an aggregate plan timeout;
stdout remains the machine-readable plan. The renderer process remains owned
through cancellation. The asynchronous sessionStart backup worker retains
its 120-second process-tree budget; the hook queues that worker without waiting
for delivery. Explicitly bounded callers retain their requested timeout.
`worktree_creation._create_worktree_core` and
`resolve_launch_cli._resolve_resume_context` (skipped on `--dry-run`) both
call it at exactly the point described above. `resolve_cli._resolve_json_mode`
also prepares the selected local base/worktree before returning its launch
command. Dry-run plans never render. Best-effort degradation for
the repo not yet being trusted, an explicitly bounded subprocess
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
current session's own start. `agent_worktrees.__main__._run_session_lifecycle`
calls `local_cache_refresh.sessionstart_diagnostic()` before registration and
later provisioning diagnostics. It queues a headless, detached, bounded worker
rather than waiting for discovery and rendering inside Copilot's hook deadline.
The launch uses the canonical explicit breakaway primitive; contained tests
retain containment. One worker per canonical checkout owns the kernel-released
reservation, and the parent reaps its child off the hook thread. A nested session cwd
resolves to the checkout root. An expired or nearly exhausted hook budget skips
dispatch with an explicit diagnostic, and spawn failures remain visible.

The hook reports **queued**, not installed or loaded. The worker's actual result,
including publication counts or failures, is written to
`<checkout-git-dir>/agent-worktrees-local-guidance/refresh.log` named by that
diagnostic. These are ephemeral checkout artifacts, not a runtime or service
registration root: a linked worktree uses its own Git administrative directory,
so the normal finalization/GC Git worktree removal reclaims its lease and log.
The log holds the latest attempt rather than an append-only history; dispatch
leaves an active worker's log intact and does not start another worker.
Admission holds a short kernel gate across reservation, process launch, and
publication of the exact request nonce plus PID/start-time owner. The child
claims only that request; another hook observes the live pending owner even
before the child starts rendering. No lease or inherited log handle stays open
in the Git administrative directory during rendering. The worker atomically
publishes a latest-result log capped at 8 KiB and its completed receipt.
Snapshot replacement uses a short, bounded retry for transient Windows reader
handles; persistent permission failures still propagate rather than reporting delivery.
Finalization and cleanup's normal liveness checks spare a live queued/running
worker using its workspace-local receipt, without spawning Git per checkout.
Owner birth identity uses native process APIs on Windows and macOS, and
`/proc` start ticks on Linux; macOS admission does not depend on `/proc`.
Creation and resume still perform synchronous primary publication before launch.
The asynchronous backup can race instruction discovery and first-turn reads:
neither a successful spawn nor a completed cache write proves model admission.
Selectors and the catch-all must continue using authenticated existing caches or
reviewed fallbacks while that backup is pending; they must not assume it completed
merely because the hook returned. Files published later are available for
subsequent explicit source resolution and discovery.

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

### 5. Reviewed artifacts preserve the offline acquisition floor

Consented reviewable sync owns the checked-in selector, non-auto-loaded full
fallback and lock as one transaction; permissionless local rendering never
writes those artifacts. A write-incapable or hookless launch can read the
owned reviewed fallback through its selector without installed-source
freshness requirements. Explicit inline kernels, including the recovery
control, remain ambient.

This floor means available verified guidance, not unconditional admission of
every full body. A new source with no reviewed artifacts needs a complete
authenticated local body before dependent action; cache-free controls still
require their static floor. Missing/damaged owned artifacts remain visible
blockers, while valid stale reviewed content remains usable and freshness is
advisory. Native host delivery acceptance is a separate proof obligation.

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
**Historical delivery/proof:** Phase 7 landed full checked-in projections with
per-file "prefer local" preambles, the repo-wide catch-all, and
`agent-worktrees` create/resume + `sessionStart` wiring
(`agent_worktrees.local_cache_refresh`). Its historical Plan/Validation Plan
completion does not certify the current selector/receipt architecture.
The historical clean-room agent-driven proof (3 tool-forbidden `explore` sub-agents per
scenario, given only a frozen snapshot) confirmed the preamble and the
catch-all each independently drive an agent to the fresher
`.local.instructions.md` content over a stale or absent checked-in file
(see the effort README's own Journal for the scenarios and results).
`efforts/2026/10/03 local-cache-delivery-primacy` Phase 1 later replaced the
existence-only precedence this proof covered with the marker-provenance
comparison underlying the current precedence rule, closing the stale-sibling gap that
existence-only check left open. That same effort's Phase 2 landed the
`agent-bridge` local-spawn-path wiring step 4 describes above
(`agent_bridge.local_cache_refresh`), closing the one remaining local-spawn
boundary the Phase 7 wiring didn't already cover.

**Current reference:** `instruction_delivery` renders selectors/full bodies
and receipt bindings; `instruction_delivery_io` authenticates canonical locals
and validates reviewed bytes; `instruction_projections` owns lock migration,
admission, sync and offline scanning. The inline catch-all and literal rendered
argv fixture cover recovery and executable resolver discovery. Historical
preamble proofs do not substitute for current native host acceptance.

## See Also

- Vision: `visions/harness-guidance/README.md`
- [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
  -- the sibling pattern for per-session computed facts; read both before
  choosing where new dynamic content belongs.
- `efforts/2026/10/02 ambient-guidance-navigability/README.md` (Phase 2 --
  `projection-reflect`, the sync mechanism this pattern's checked-in floor
  depends on; Phase 7 -- this pattern's own implementation)
