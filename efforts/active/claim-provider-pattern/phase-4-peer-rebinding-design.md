# Phase 4 design note — peer-launch identity rebinding for claim providers

Tracked by: [#3461](https://github.com/ThomasMichon/copilot-extensions/issues/3461)
(items 1/2), Phase 4 of this effort's own README.

## Problem

`agent_worktrees.claim_providers.peer_env()` invokes a sibling plugin's
claim-provider callback (agent-codespaces' `claim-status`/`claim-reclaim`,
agent-containers' equivalents) by resolving its payload-local binstub and
running it with a **stripped** environment (`COPILOT_EXTENSIONS_CONTEXT`,
`COPILOT_PLUGIN_ROOT`, `GH_TOKEN`, `GITHUB_TOKEN` removed). The sibling's own
installation-context resolution treats an absent context as "not in cell
mode" and degrades to a legacy/ambient resolution path. This is a safe
*bounded* mitigation (it stops the caller's own cell identity leaking into
the sibling), but it is not full peer-launch identity validation: the
sibling runs unauthenticated/ambient rather than as its own validated cell.

Each claim-owning plugin (agent-codespaces, agent-containers, agent-bridge,
agent-machines, agent-logger, agent-index) already vendors an identical
`_peer_launch.py` module that solves exactly this problem for launches
**they** initiate into a peer: it validates the caller ("owner") against a
canonical installation receipt, revalidates the peer's own installation
contract using the peer's own shipped primitive (not the caller's copy),
computes a fully rebound environment (`peer_environment()`), resolves the
peer's own interpreter via its own `resolve-runtime` script, and launches
`python -m <peer_module> <argv>` directly — never a binstub shell-out.

`agent-worktrees` does not vendor a copy of this module and is not listed in
any existing copy's `OWNERS` roster, so it cannot originate a peer-launch
today. That's the concrete gap.

## Design

1. **Roster addition (mechanical, low-risk).** Add `"agent-worktrees"` to
   the shared `OWNERS` set in every existing vendored `_peer_launch.py`
   copy (agent-codespaces, agent-containers, agent-bridge, agent-machines,
   agent-logger, agent-index). This is metadata-only: it does not change
   any file's own peer-launch behavior for existing owners, and an absent
   `agent-worktrees` entry is exactly why no peer can currently be
   originated *from* agent-worktrees.
2. **Vendor a new copy into agent-worktrees.** Add
   `plugins/agent-worktrees/src/agent_worktrees/_peer_launch.py`, byte-for-byte
   the same shape as the existing copies (same `validate_owner`/`launch`/
   `peer_environment`/`_peer_python`/`main` bodies), with:
   - `OWNERS` = the same roster as every other copy, now including
     `agent-worktrees`.
   - `PEERS` = `{"agent-codespaces": "agent_codespaces", "agent-containers":
     "agent_containers"}` — only the two claim-owning plugins
     agent-worktrees' claim-provider registry actually calls into today.
     (Not `agent-dispatch`: that plugin does not use the `_peer_launch.py`
     pattern for its own claim-status callback — confirm at implementation
     time; if it does, add it too.)
3. **Resolve agent-worktrees' own owner context.** Because agent-worktrees
   ships `installationContext: required`, `COPILOT_EXTENSIONS_CONTEXT` is
   already present in its own process env on every real invocation. Add a
   small helper (mirroring how `_peer_launch.py`'s own `validate_owner`
   expects `own_root`/`raw_context`) that resolves agent-worktrees' own
   payload root (`Path(__file__).resolve().parents[2]`, i.e. its own
   `plugins/agent-worktrees` cell-relative root — verify the exact
   depth against an existing owner's own self-invocation, e.g. how
   agent-codespaces resolves its own `own_root` when it acts as an owner)
   and reads `os.environ["COPILOT_EXTENSIONS_CONTEXT"]` as `raw_context`.
4. **Wire `claim_providers.py`.** In `_run_callback()` (or a new sibling
   function), when the resolved provider's plugin id is a key in the new
   `PEERS` map AND agent-worktrees' own installation context is present
   and valid, build the peer-launch argv via the new vendored module's
   `launch_prefix("agent-worktrees", own_root, raw_context, target_plugin)`
   and append the original claim-provider argv
   (`claim-status <ref>` / `claim-reclaim <ref> [--apply]`) after it,
   instead of resolving+running the plain binstub with `peer_env()`'s
   stripped environment. Capture stdout/stderr exactly as `_run_callback`
   does today (same timeout, same JSON-parse-or-degrade contract).
5. **Preserve full graceful degradation.** Any exception from the
   peer-launch path (`ContextRefused`, `ValueError`, governance mismatch,
   missing receipt, peer plugin not installed, `agent-worktrees` own
   context absent/malformed) must fall back to exactly today's behavior —
   resolve the plain binstub and run it with `peer_env()`'s stripped
   environment — never raise up into the claim-status/claim-reclaim caller.
   A provider must remain fully optional.
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

This touches the suite's cross-plugin trust boundary. Each vendored copy's
raison d'être is that a peer-launch **cannot** be spoofed by an untrusted
caller: the owner must present a real, currently-active installation
receipt, and the peer's own contract (not the caller's belief about it) is
re-validated before launch. The new agent-worktrees copy must not weaken
this anywhere — no new bypass, no widened `OWNERS`/`PEERS` beyond what's
listed above, no relaxed receipt validation. Review this PR at the same
bar as the original `_peer_launch.py` modules (treat it as security-review
scope, not a routine feature PR).

## Scope boundary for the implementing PR

- Land (1) and (2) together (mechanical roster sync + new vendored file) —
  low risk, easy to review in isolation, and required before (4) can be
  tested at all.
- Land (3)+(4)+(5) as the real behavior change, with full unit test
  coverage: peer-launch succeeds and rebinds when the peer is genuinely
  installed and active; falls back to strip-mode identically to today for
  every failure mode listed in (5); never regresses the "no context at
  all" ambient/legacy path this registry already supports.
- (6) is a separate, smaller follow-up PR gated on (1)-(5) merging first
  (its correctness depends on the rebinding actually working end to end).
