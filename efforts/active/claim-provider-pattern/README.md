# Claim Provider Pattern

- **Slug:** `claim-provider-pattern`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice worktree branch (see the driving worktree's own
  metadata; not recorded here to keep this public artifact generic)
- **Created:** 2026-09-22
- **Status:** Active
- **Vision:** `visions/plugin-services` §Concepts & Components (extends: adds the
  **Claim provider** concept and the explicit plugin-stack layering rule)
- **Umbrella issue:** [ThomasMichon/copilot-extensions#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295)

## Guiding Intent

`agent-worktrees` owns the claims ledger (`claims add|release|settle|sweep|
mirror-status|cleanup|orphans`) but currently reaches **upward** past its own
tier to check claim status: it shells out directly to `agent-codespaces`
(deleting an orphaned CodeSpace) and to `agent-dispatch` (reading assigned/
owned tasks), both higher in the plugin stack than agent-worktrees itself.
This violates the suite's one-way plugin-stack layering rule and duplicates,
per-caller, the same "resolve a sibling's binstub and shell out to it" logic
agent-bridge's existing `providers.d` **bridge-provider** pattern already
solves for namespace resolution.

This effort generalizes that existing provider-manifest sub-pattern
(`docs/patterns/a-la-carte-independence.md`) into an explicit **claim
provider** abstraction: a claim-owning plugin (agent-dispatch, agent-
codespaces, agent-containers) registers its claim **namespace** and a
**status-check callback** into a registry agent-worktrees itself owns and
scans, so agent-worktrees never has to hardcode a call to a higher-tier
sibling's CLI again. It also documents the plugin stack's explicit tier
ordering and one-way dependency rule, which today exists only as operator
intent, not a written pattern.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| the driving Copilot CLI session | sole driver | its own linked worktree checkout |

## Coordination

- **Topology:** single-driver, one PR.
- **Host (owns PR):** the driving session.
- **Delegates:** none.
- **Handoff:** none expected; if context runs out mid-effort, resume via
  the session's own handoff mechanism.

## Context

- Full architectural finding: restated generically in the public tracking
  issue [#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295).
- The existing bridge-provider precedent: `plugins/agent-bridge/src/
  agent_bridge/provider_sources.py` (`providers.d` manifests, namespace
  resolution, `dropin-registry` lib for scan/reconcile/findings).
- The two violation call sites:
  - `plugins/agent-worktrees/src/agent_worktrees/cleanup.py::_run_codespaces`
    -- ambient `shutil.which("agent-codespaces")` + `delete <name> --force`
    during orphaned-CodeSpace reclaim (tier 3 -> tier 8).
  - `plugins/agent-worktrees/src/agent_worktrees/claims_cli.py::
    _inbound_claims` -- ambient `shutil.which("agent-dispatch")` +
    `worktree-status --machine ... --worktree ...`, used only to decorate
    `claims` display with dispatch's `assigned`/`owned` task data (tier 3 ->
    tier 9). Its name/docstring also conflate agent-worktrees' own `claims`
    ledger with agent-dispatch's separate "claimed tasks" concept -- a
    naming defect to fix alongside the layering fix, not a second effort.
  - `plugins/agent-worktrees/scripts/reconcile-machine-settings.sh`/`.ps1`
    calls `agent-machines` (tier 3 -> tier 1): already **downward** and
    already degrades gracefully -- explicitly out of scope, not a violation.

## Request

Per operator direction (see public tracking issue
[#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295)):
introduce a **claim provider** pattern, parallel to agent-bridge's bridge
provider, so Dispatch, Codespaces, and Containers -- each of which owns a
distinct kind of "thing a worktree can claim" -- register their claim
namespace and status-check callback into agent-worktrees' own registry.
`agent-worktrees finalize` (and any other claim-status check) resolves the
provider for a claim's namespace and asks it for status, rather than
agent-worktrees hardcoding calls to specific sibling CLIs. Legacy unnamespaced
claim refs may still need pattern-matching for back-compat; new claims should
always declare a namespace.

## Operating Constraints

- Never let a missing/absent provider break agent-worktrees' own claims
  commands -- a provider is optional, exactly like a bridge provider; absence
  degrades only that namespace's status resolution.
- Follow the existing `dropin-registry` lib and drop-in-registry-hygiene
  pattern (scan authority, findings, warning dedup) rather than inventing a
  new scan/reconcile shape.
- Preserve agent-worktrees' own binstub/runtime ownership: agent-worktrees
  never imports a sibling's package, only invokes its declared CLI callback
  over a process boundary (mirrors the bridge-provider "no re-pointing"
  rule).
- Fix the `_inbound_claims` naming confusion (agent-worktrees' `claims`
  ledger vs. agent-dispatch's "claimed tasks") as part of this same
  conversion, not deferred.
- Cite this effort's own public tracking issue (#3295) in commit/PR text,
  never any private/internal tracker identifier, per this repo's
  public-safe conventions.

## Plan

### Phase 0 — Vision and pattern doc
- [x] Add the **Claim provider** concept to
  `visions/plugin-services/README.md` §Concepts & Components, as a named
  instance of the existing drop-in contribution registry / provider-manifest
  concept, scoped to agent-worktrees' claims system.
- [x] Document the plugin stack's explicit 10-tier ordering and one-way
  dependency rule (a plugin may call downward with graceful degradation,
  never directly upward; upward-owned functionality is exposed via a
  drop-in registry the lower tier owns) in
  `docs/patterns/a-la-carte-independence.md`, alongside the existing
  provider-manifest sub-pattern it generalizes.
- [x] File the public GitHub tracking issue on
  `ThomasMichon/copilot-extensions` restating this work generically (no
  facility/session/persona context), and record its number here.
  ([#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295))

### Phase 1 — Claim-provider registry primitive
- [x] Add a `claim-providers` drop-in registry owned by agent-worktrees,
  discovered directly from the installed-plugins tree (mirroring the
  existing `claim_kinds_registry`/pivot-registry pattern rather than a
  sessionStart-hook-populated config-dir registry -- no separate
  registration step, always current with the plugin's own installed
  version), reusing the `dropin-registry` lib for scan/reconcile/findings.
- [x] Define the manifest schema: `schema_version`, `namespace` (the
  claim-ref prefix it serves, e.g. `dispatch-task:`, `codespace:`,
  `container:`), `status_command`/`reclaim_command` (absolute argv
  templates resolved to the plugin's own payload-local `bin/` binstub --
  never ambient `PATH`), optional `description`. At least one of
  `status_command`/`reclaim_command` is required.
- [x] Define the callback contract: `<status_command...> claim-status
  <ref>` / `<reclaim_command...> claim-reclaim <ref> [--apply]`, each
  returning a small JSON status document; agent-worktrees never depends on
  any other output shape from the provider, and degrades to
  `{"available": false, "reason": "..."}` on any absence/failure.
- [x] Add discovery/resolution helpers (`discover_claim_providers`,
  `resolve_claim_status`, `resolve_claim_reclaim`,
  `split_namespaced_ref`), identity-verified via
  `plugin_activation.resolve_active_plugins()`, plus findings for
  malformed/inactive/duplicate manifests (module:
  `plugins/agent-worktrees/src/agent_worktrees/claim_providers.py`; tests:
  `plugins/agent-worktrees/tests/test_claim_providers.py`, 53 collected
  cases -- verified live via `python tools/run-plugin-tests.py
  agent-worktrees -k claim_providers --collect-only` immediately before
  this entry was written, precisely to stop this count drifting stale
  across review rounds again).

### Phase 2 — Provider registration in claim-owning plugins
- [x] Add a `register-claim-provider.sh`/`.ps1` pair to agent-dispatch,
  modeled on agent-codespaces'/agent-containers' existing
  `register-bridge-provider.sh`/`.ps1`, declaring the `dispatch-task:`
  namespace and a `claim-status` subcommand.
- [x] Add the same to agent-codespaces (`codespace:` namespace) and
  agent-containers (`container:` namespace).
- [x] Each provider's `claim-status` subcommand returns the JSON contract
  from Phase 1 for a ref in its own namespace.

### Phase 3 — Convert the two violation call sites
- [x] Convert `cleanup.py::_run_codespaces`'s orphan-reclaim path to resolve
  the `codespace:` claim provider and drive its declared command instead of
  ambient `shutil.which`.
- [x] Convert `claims_cli.py::_inbound_claims` to resolve the
  `dispatch-task:` claim provider; rename it (and its docstring) to
  something that does not reuse the word "claims" for agent-dispatch's
  separate assigned/owned-task concept (e.g. `_dispatch_assigned_tasks`).
- [x] Preserve complete graceful degradation: an absent provider (dispatch/
  codespaces plugin not installed) must degrade exactly as today (`"agent-
  codespaces binstub unavailable"` / `{"available": false, ...}"`), not
  raise.
- [x] Add legacy-ref pattern-matching fallback for any existing unnamespaced
  claim refs, so this conversion never breaks a pre-existing claim entry.

### Phase 4 — Cross-plugin identity rebinding + codespaces atomic lease fence
(follow-up filed as [#3461](https://github.com/ThomasMichon/copilot-extensions/issues/3461)
after Phase 2/3 review, PR [#3388](https://github.com/ThomasMichon/copilot-extensions/pull/3388);
design note for the rebinding work:
[`phase-4-peer-rebinding-design.md`](phase-4-peer-rebinding-design.md))
- [x] Build a `deploy_hold`-equivalent atomic admission fence for
  agent-codespaces, mirroring
  `plugins/agent-containers/src/agent_containers/lease.py`'s
  `deploy_hold`/`DeployHold`/`_DEPLOY_HOLDS_FILE` mechanism, and wire it
  into `agent-codespaces`' `claim_provider_cli.py::cmd_claim_reclaim` and
  `lease.py::borrow()`'s admission check.
- [ ] Register agent-worktrees as an owner/peer in agent-codespaces' and
  agent-containers' own vendored `_peer_launch.py` (OWNERS/PEERS dicts),
  and add matching wiring in `agent_worktrees.claim_providers.peer_env()`
  so it REBINDS to each target plugin's own validated installation context
  before invoking a sibling claim-provider callback, instead of stripping
  `COPILOT_EXTENSIONS_CONTEXT`/`COPILOT_PLUGIN_ROOT`/`GH_TOKEN`/
  `GITHUB_TOKEN` and letting the sibling fall back to legacy/ambient mode.
- [ ] Once rebinding lands, fix
  `agent-codespaces/gh_account.py::mapped_accounts()`/`_lookup()`/
  `_agent_worktrees_bin()` to prefer the same-cell `worktrees.run()` path
  over the `shutil.which("agent-worktrees")` PATH fallback for this
  cross-plugin call path.

## Validation Plan

- [ ] Unit tests for the claim-provider registry primitive (manifest
  parsing, missing/malformed manifest handling, namespace resolution,
  reconciliation across authoritative/indeterminate scans) mirroring
  `agent-bridge`'s `test_provider_sources.py` coverage shape.
- [ ] Unit tests proving `cleanup.py` and `claims_cli.py` resolve through
  the provider registry and degrade identically to today when no provider
  manifest exists.
- [ ] `check-marketplace-isolation.py`'s `path-sibling-launch` count drops
  by exactly the 2 converted call sites (currently tracked at 42 after the
  most recent marketplace-scoped-installations slice).
- [ ] Full test suites for agent-worktrees, agent-dispatch, agent-
  codespaces, and agent-containers pass on both Windows and WSL/Linux.
- [ ] `check-version-bump.py`, `check-version-consistency.py`, `check-
  module-size.py`, `check-install-contract.py`, and `ruff check --select
  F,E9` all pass for every touched plugin.
- [x] Phase 4: unit tests for the new agent-codespaces `deploy_hold`
  equivalent (acquire/reject-when-held/heartbeat/expiry/cleanup) mirroring
  `agent-containers`' `test_lease.py` coverage shape; `cmd_claim_reclaim`
  proven to hold the fence for the full destructive-reclaim duration.
- [ ] Phase 4: unit tests proving `peer_env()` rebinds (not strips) target
  identity when a registered peer exists, and still degrades to today's
  strip-and-fallback behavior for any plugin that hasn't registered as a
  peer.
- [ ] Phase 4: full test suites for agent-worktrees, agent-codespaces, and
  agent-containers pass on both Windows and WSL/Linux after each Phase 4
  slice.

## Proposal

_Pending._

## Journal

### 2026-09-23 — Phase 4 slice: agent-codespaces atomic deploy hold
- Added an agent-codespaces provider `deploy_hold` fence mirroring
  agent-containers' hold-record pattern (`DeployHold`,
  `_DEPLOY_HOLDS_FILE`, heartbeat thread, fail-closed admission reads,
  expiry/uncertain handling) and wired `lease.borrow()` to reject new
  admissions while a destructive reclaim hold is live.
- Wrapped `claim_provider_cli.py::cmd_claim_reclaim` in that fence so the
  hold spans the full session-recovery + delete window, not just a
  best-effort lease re-check immediately before the delete.
- Extended agent-codespaces lease/claim-provider tests to cover hold
  acquisition, rejection while held, heartbeat + expiry, cleanup of stale
  corrupt state, uncertain hold persistence, and callback ordering proving
  the fence stays live through recovery and deletion.

### 2026-09-23 — Phase 2/3 landed; Phase 4 opened from review follow-up
- PR [#3388](https://github.com/ThomasMichon/copilot-extensions/pull/3388)
  (Phase 2/3: provider registration in agent-dispatch/agent-codespaces/
  agent-containers + conversion of both violation call sites) merged as
  `2019e7b1e` after ~20 rounds of review. Safety-critical findings raised
  during review (cmd.exe injection guards, TOCTOU lease re-checks, an
  atomic `deploy_hold` fence for agent-containers reclaim, strict
  single-CodeSpace status lookups with budget-timeout enforcement,
  cross-account name-collision handling, fail-closed reclaim-chain error
  propagation, exact-token credential threading) were all fixed inline and
  landed with the PR.
- Four related findings were deliberately left open because closing them
  needs new cross-plugin infrastructure, not a bounded fix within #3388:
  peer-launch identity rebinding (`agent_worktrees.claim_providers.
  peer_env()` currently strips identity env vars rather than rebinding to
  each target plugin's own validated context) and an agent-codespaces
  atomic lease fence equivalent to agent-containers' `deploy_hold`. Filed
  generically as
  [#3461](https://github.com/ThomasMichon/copilot-extensions/issues/3461)
  and folded in as this effort's own Phase 4 rather than a standalone
  follow-on effort, since it's a direct continuation of the same
  registry/rebinding work.

### 2026-09-22 — Phase 0-1 landed
- Phase 0: added the **Claim provider** concept and the explicit 10-tier
  plugin-stack layering rule to `visions/plugin-services/README.md` and
  `docs/patterns/a-la-carte-independence.md`; filed the public tracking
  issue [ThomasMichon/copilot-extensions#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295).
- Phase 1: implemented the claim-provider registry primitive
  (`agent_worktrees.claim_providers`) as a live installed-plugins-tree scan
  (mirroring `claim_kinds_registry`/the pivot registry's own pattern,
  **not** agent-bridge's config-dir + sessionStart-hook `providers.d`
  shape) -- a claim-owning plugin ships a static
  `<plugin_root>/claim-providers/<namespace>.json` template in its own
  payload; no separate registration step. Commands resolve only to the
  identity-verified plugin's own payload-local `bin/<command>[.cmd]`,
  never ambient `PATH` -- matching the plugin-stack layering rule this
  effort exists to enforce. 21 new tests pass; full agent-worktrees suite
  run alongside them shows 12 pre-existing failures in `test_doctor.py`/
  `test_context_resolution.py` unrelated to this change (confirmed against
  a clean tree with these changes stashed) -- surfaced to the operator,
  not fixed here (out of scope for this effort).
- Landing Phase 1 as its own PR before starting Phase 2 (provider
  registration in agent-dispatch/agent-codespaces/agent-containers) and
  Phase 3 (converting the two violation call sites) -- the registry
  primitive is independently valuable/reviewable and keeps each PR's diff
  bounded.

### 2026-09-22 — Kickoff
- Effort created from an architecture-audit finding about agent-worktrees'
  upward calls to agent-codespaces and agent-dispatch, restated generically
  in public tracking issue [#3295](https://github.com/ThomasMichon/copilot-extensions/issues/3295).
- Confirmed both violation call sites still exist as described
  (`cleanup.py::_run_codespaces`, `claims_cli.py::_inbound_claims`), and
  that no existing effort or vision text already covers the claim-provider
  abstraction.
