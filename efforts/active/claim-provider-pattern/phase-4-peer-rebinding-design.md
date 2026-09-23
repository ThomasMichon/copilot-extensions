# Phase 4 design note — peer-launch identity rebinding for claim providers

Tracked by: [#3461](https://github.com/ThomasMichon/copilot-extensions/issues/3461)
(items 1/2), Phase 4 of this effort's own README.

## Problem

`agent_worktrees.claim_providers.peer_env()` invokes a sibling plugin's
claim-provider callback (agent-codespaces' `claim-status`/`claim-reclaim`,
agent-containers' equivalents, agent-dispatch's assigned-tasks lookup via
`claims_cli._dispatch_assigned_tasks()`) by resolving its payload-local
binstub and running it with a **stripped** environment
(`COPILOT_EXTENSIONS_CONTEXT`, `COPILOT_PLUGIN_ROOT`, `GH_TOKEN`,
`GITHUB_TOKEN` removed). The sibling's own installation-context resolution
treats an absent context as "not in cell mode" and degrades to a
legacy/ambient resolution path. This is a safe *bounded* mitigation (it
stops the caller's own cell identity leaking into the sibling), but it is
not full peer-launch identity validation: the sibling runs
unauthenticated/ambient rather than as its own validated cell.

Every claim-owning plugin already has a peer-launch boundary that solves
exactly this problem for launches **they** initiate into a peer: it
validates the caller ("owner") against a canonical installation receipt,
revalidates the peer's own installation contract using the peer's own
shipped primitive (not the caller's copy), computes a fully rebound
environment (`peer_environment()`), resolves the peer's own interpreter via
its own `resolve-runtime` script, and launches `python -m <peer_module>
<argv>` directly — never a binstub shell-out.

**This boundary is a single canonical module, not independently
maintained copies.** `libs/peer-launch/peer_launch.py` is the source of
truth; `tools/sync-peer-launch.py` byte-copies it verbatim to every
vendor destination it lists:

- `plugins/agent-bridge/src/agent_bridge/_peer_launch.py`
- `plugins/agent-dispatch/src/agent_dispatch/peer_launch.py` (note: no
  leading underscore — different filename, identical byte content)
- `plugins/agent-codespaces/src/agent_codespaces/_peer_launch.py`
- `plugins/agent-containers/src/agent_containers/_peer_launch.py`
- `plugins/agent-logger/src/agent_logger/_peer_launch.py`
- `plugins/agent-index/src/agent_index/_peer_launch.py`
- `plugins/agent-machines/src/agent_machines/_peer_launch.py`

`--check` mode diffs each destination against the canonical source and is
almost certainly enforced by a packaging/CI guard (see
`libs/peer-launch/tests/test_packaging.py`) — any change to `OWNERS`/`PEERS`
or the module body must edit the canonical file and re-run
`python tools/sync-peer-launch.py`, never hand-edit a vendored copy.

`agent-worktrees` is not a sync destination today and is not listed in the
canonical `OWNERS` set, so it cannot originate a peer-launch. That's the
concrete gap.

## The `worktrees.py` precedent (the direction that already works)

The reverse direction — a claim-owning plugin calling **into**
agent-worktrees over this same boundary — already exists and is the pattern
to mirror: `plugins/agent-codespaces/src/agent_codespaces/worktrees.py`.

- `explicit_context()` — presence check on `COPILOT_EXTENSIONS_CONTEXT`
  (whitespace counts as present/explicit, not absent).
- `validate_context()` — resolves the caller's OWN validated root (via
  `AGENT_CODESPACES_HOME` if set, else parsed from the install-receipt
  pointer) and calls `validate_owner("agent-codespaces", root, context)`
  from the shared `_peer_launch` module. Raises `ContextRefused` on any
  validation failure — this is NOT swallowed into a legacy fallback.
- `run()` — requires `validate_context()` to have succeeded
  (`own is None` raises, it does not degrade); resolves the peer's root
  from `own["cellRoot"]`, and if that peer plugin isn't installed
  (`peer_root` doesn't exist) returns `None` (a genuinely optional-peer
  degrade — the ONLY sanctioned degrade path); otherwise builds
  `launch_prefix(...)` and runs it, re-raising `ContextRefused` on exit
  code 126 or a subprocess failure.

**The fail-closed rule this encodes, and that the new agent-worktrees side
must also encode:** once an explicit context is present, a refusal
(`ContextRefused`, a receipt/governance mismatch, a malformed context) is
final and must propagate — it must NOT be caught and converted into the
legacy stripped-env callback. The stripped-env legacy path in
`claim_providers.peer_env()` is only correct for the genuinely-no-context
case (a caller that was never given an explicit context at all, i.e. a
non-cell/ambient installation) or a validated-absent-peer case (the target
plugin genuinely isn't installed). Silently falling back on an explicit
refusal would defeat the entire point of adding the trust boundary — a
malicious or misconfigured environment could force a downgrade to
ambient/legacy credentials just by making the strict path fail.

## Design

1. **Canonical roster + destination update (single source, then sync).**
   In `libs/peer-launch/peer_launch.py`:
   - Add `"agent-worktrees"` to `OWNERS`.
   - Canonical `PEERS` today is only `{"agent-worktrees": "agent_worktrees",
     "agent-bridge": "agent_bridge", "agent-ssh": "agent_ssh"}` — it does
     **not** already contain `agent-codespaces` or `agent-containers`.
     Add all **three** entries the claim-provider registry actually calls
     into: `"agent-codespaces": "agent_codespaces"`,
     `"agent-containers": "agent_containers"`, and
     `"agent-dispatch": "agent_dispatch"` (the last per
     `claims_cli._dispatch_assigned_tasks()`, which also runs its callback
     through `peer_env()` today).
   In `tools/sync-peer-launch.py`: add
   `plugins/agent-worktrees/src/agent_worktrees/_peer_launch.py` to
   `DESTINATIONS`. Run `python tools/sync-peer-launch.py` (or `--check` to
   verify) so the new vendor copy is created and stays byte-identical to
   canonical going forward — never hand-edit the vendored file.
2. **Add the missing installation-context primitive for agent-worktrees.**
   Every existing vendor directory ships a co-located
   `_installation_context.py` beside its `_peer_launch.py` (loaded via
   `Path(__file__).with_name("_installation_context.py")`). agent-worktrees'
   own primitive currently lives at
   `plugins/agent-worktrees/scripts/installation-context/installation_context.py`,
   a different location/name. Resolve this before wiring anything: either
   (a) vendor/rename a copy of that primitive to
   `plugins/agent-worktrees/src/agent_worktrees/_installation_context.py`
   matching the co-located-file convention every peer expects, checking
   first whether `scripts/installation-context/installation_context.py` is
   itself a synced copy of some other canonical source (grep for another
   sync/packaging guard before assuming it's safe to duplicate), or
   (b) confirm the two files are already identical/interchangeable and
   symlink/generate one from the other. Do not invent a third divergent
   copy of this primitive.
3. **Resolve agent-worktrees' own owner context correctly.** Do NOT derive
   `own_root` from `Path(__file__).resolve().parents[N]` — the installed
   package lives in a versioned venv, not the payload tree, so a
   file-relative path will fail `validate_owner()`'s
   `own_root.parent.name != "plugins"` / cell-root checks in a real
   installation. Mirror `agent_codespaces/worktrees.py::validate_context()`
   exactly: read `COPILOT_EXTENSIONS_CONTEXT`, resolve the payload root
   from the plugin's own equivalent of `AGENT_CODESPACES_HOME` if
   agent-worktrees has one, else parse the install-receipt pointer's
   parent directory the same way — using agent-worktrees' own existing
   registry/payload-root resolution helpers (`claim_kinds_registry`,
   `plugin_activation.resolve_active_plugins()`) rather than deriving a
   path from `__file__`. Add a new
   `plugins/agent-worktrees/src/agent_worktrees/peer_launch_adapter.py`
   (name TBD at implementation time) exposing `explicit_context()`,
   `validate_context()`, and `run(peer: str, *args, timeout=15)` that
   mirrors `agent_codespaces/worktrees.py`'s three functions, parameterized
   over which peer plugin to launch into (rather than one hardcoded
   target), since agent-worktrees needs this for multiple peers
   (codespaces, containers, dispatch).
4. **Wire `claim_providers.py`.** In `_run_callback()` (or a new sibling
   function), when the resolved provider's plugin id is one of the
   registered peers: call the new adapter's `run(peer_id, *argv)`. On
   success, use its stdout exactly as today. On `ContextRefused` (or any
   validation-boundary failure) when an explicit context WAS present,
   **do not fall back** — treat it as the callback's own execution failure
   (degrade to `{"available": false, "reason": ...}` exactly as a genuine
   callback crash does today, never re-run through the stripped-env path).
   Only use today's `peer_env()`-stripped legacy path when
   `explicit_context()` reports no context at all (true ambient/legacy
   caller) — this is the one remaining legitimate use of the stripped-env
   path, not a catch-all safety net for the new one.
5. **Preserve full graceful degradation for the genuinely-optional-peer
   case.** A target plugin that simply isn't installed (mirroring
   `worktrees.run()`'s `peer_root` existence check returning `None`) must
   still degrade to `{"available": false, ...}`, not raise. This is
   distinct from (4)'s fail-closed rule for a validation refusal — "peer
   not installed" is a supported absence, "peer installed but rejected the
   launch" is not something to paper over.
6. **Only after (1)-(5) land**, fix
   `agent-codespaces/gh_account.py::mapped_accounts()`/`_lookup()`/
   `_agent_worktrees_bin()` (issue #3461 item 2): once the peer-launch path
   correctly rebinds `COPILOT_EXTENSIONS_CONTEXT` into the codespaces
   child process, its own installation-context resolution stops falling
   back to `shutil.which("agent-worktrees")`, so this fix is largely a
   confirmation + regression test rather than new logic — but verify at
   implementation time; do not assume without re-reading the current
   `gh_account.py`.

## Security considerations

This touches the suite's cross-plugin trust boundary. The peer-launch
boundary's entire value is that it **cannot be spoofed or silently
downgraded** by an untrusted or misconfigured caller: the owner must
present a real, currently-active installation receipt, the peer's own
contract (not the caller's belief about it) is re-validated before launch,
and — per the fail-closed rule above — an explicit validation failure must
propagate rather than triggering a legacy-credential fallback. The new
agent-worktrees vendor copy is edited only via the canonical
`libs/peer-launch/peer_launch.py` + `tools/sync-peer-launch.py`, never
hand-edited, and must not widen `OWNERS`/`PEERS` beyond what's listed
above or relax any receipt/governance validation. Review this PR at the
same bar as the original peer-launch modules (treat it as security-review
scope, not a routine feature PR).

## Scope boundary for the implementing PR

- Land (1) and (2) together (canonical roster + sync + the missing
  installation-context primitive) — required before anything in (3)/(4)
  can be tested at all, and independently reviewable as pure
  infrastructure plumbing.
- Land (3)+(4)+(5) as the real behavior change, with full unit test
  coverage: peer-launch succeeds and rebinds when the peer is genuinely
  installed and active; explicit-context validation failures propagate as
  callback failures and are NEVER silently downgraded to the stripped-env
  path; the stripped-env legacy path still degrades identically to today
  for a genuinely no-context caller; a genuinely-absent peer plugin still
  degrades to `{"available": false, ...}`.
- (6) is a separate, smaller follow-up PR gated on (1)-(5) merging first
  (its correctness depends on the rebinding actually working end to end).

## Documentation impact

This design note and the effort README's Phase 4 checklist are the only
authoritative docs this note itself touches. The implementing PR(s) should
additionally update: `docs/patterns/a-la-carte-independence.md` (if the
one-way plugin-stack layering rule text needs a note about the peer-launch
boundary now covering agent-worktrees-as-owner), and
`plugins/agent-worktrees/README.md`/`CHANGELOG.md`-equivalent per this
repo's normal per-plugin change-tracking convention, if any exists for that
plugin — check at implementation time.
