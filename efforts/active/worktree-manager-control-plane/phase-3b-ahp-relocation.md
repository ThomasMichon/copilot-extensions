# Phase 3b, Slice 1 — Relocate the AHP session backend out of agent-worktrees

- **Parent effort:** [`README.md`](README.md) § Phase 3b
- **Tracks:** [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
- **Governing vision:** [`visions/session-hosting`](../../../visions/session-hosting/README.md)
  Concepts/*Session-host provider*, *Host-owned execution identity*; the
  matching Non-Goal in
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md).
- **Scope of this doc:** AHP only. Mux launch/reattach/remux is a **separate,
  later slice** of Phase 3b — it is more deeply coupled to agent-worktrees'
  liveness reducers (`sessions.py`, `procs.py`) and to three platform-specific
  launcher scripts, so it is deliberately sequenced after this smaller,
  more self-contained slice proves the pattern.
- **Status:** Planned — not started. This document is the reviewed plan; no
  code has moved yet.

## Why this slice first

AHP landed (#1657 / PR #1998) as an internal `session_backend.kind == "ahp"`
branch inside `agent-worktrees`, not as a standalone provider. It is the more
tractable of the two Phase 3b relocations because:

- it is one mostly-self-contained module (`ahp_backend.py`) plus a narrow,
  already-somewhat-generic persisted-record shape (`SessionBackendBinding`
  already carries a forward-compatible "opaque if unrecognized" fallback);
- its only agent-worktrees-owned coupling is generic locking/finalize-gating
  primitives that already exist and don't need to change shape;
- it has no platform-specific (Windows/POSIX) launcher-script surface of its
  own — the mux wrapper around an AHP session already lives in the launcher
  scripts and is exercised by `tests/test_ahp_launcher_contract.py`, which
  this slice does not need to touch.

## Current-state inventory (evidence, `main` as of 2026-09-04)

| File | What lives there today |
|---|---|
| `plugins/agent-worktrees/src/agent_worktrees/ahp_backend.py` | Whole AHP JSON-RPC/WebSocket client: `AhpController`, `connect_controller`, `ensure_worktree_session`, `dispose_worktree_session`, `account_for_backend`, `AhpSession`, `AhpBackendError`. Imports `agent_worktrees.git_ops` and `agent_worktrees.config` directly. |
| `plugins/agent-worktrees/src/agent_worktrees/config.py` | `SessionBackendConfig` dataclass (`kind`, `endpoint_url`, `github_account`, `protocol_versions`, `auth_resource`, `connect_timeout_seconds`, `is_ahp` property) + `_parse_session_backend()` parsing the global `~/.agent-worktrees/config.yaml` `session_backend:` block. |
| `plugins/agent-worktrees/src/agent_worktrees/config_dropins.py` (~432-481) | Schema validation for the same `session_backend:` block (allowed keys, `kind` enum `direct`/`ahp`, type checks). |
| `plugins/agent-worktrees/src/agent_worktrees/tracking.py` | `SessionBackendBinding` dataclass (typed AHP fields: `endpoint_url`, `session_id`, `protocol_version`, `auth_account`, `state`, `binding_revision`) on `WorktreeRecord`; parse logic (~1952-1990, only understands `kind == "ahp"`, else sets `session_backend_opaque=True`); reconcile-on-save logic preferring the higher `binding_revision` (~2442-2458); serialization (~2595-2603). |
| `plugins/agent-worktrees/src/agent_worktrees/finalize.py` (~62-78) | `_has_live_session()` treats `session_backend_opaque` as "assume live" and `backend.state in {"active","unknown"}` as live — **this part is already generic** and should not need to change shape, only its comment/docstring wording. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py` | `cmd_session_backend()` (~914-1049): the whole `agent-worktrees session-backend <status|ensure|dispose>` CLI verb — config gating, record locking (`tracking._RecordLock`, `fin.FinalizeLock`), calls into `ahp_backend`, persists the binding. `_unsupported_hosted_launch()` (~1057-1075): AHP-specific fail-closed wording for other launch paths. `cmd_embody()` gate (~2475): `if make_new and config.session_backend.is_ahp: return _json_error(...)`. Row/JSON display flags (~693-703): `session_ahp_live`, `session_backend`, `last_session_id`. Dispatch table entry `"session-backend": cmd_session_backend` (~23153). |
| `plugins/agent-worktrees/pyproject.toml` | `websocket-client>=1.8.0` dependency, needed only by `ahp_backend.py`. |
| `plugins/agent-worktrees/tests/test_ahp_launcher_contract.py` | Tests the **mux launcher's** hard-bind/disable-post-exit behavior when wrapping an AHP session — this is launcher (Mux-slice) territory, not AHP-backend territory; it stays in agent-worktrees for now and is unaffected by this slice as long as the launcher can still read whatever generic record replaces `session_backend`. |
| `plugins/agent-worktrees/tests/*` | Any other test asserting `session_backend`/`SessionBackendBinding`/`is_ahp` shape needs updating to the new generic shape (see below); exact list to be enumerated at implementation time via `grep -l session_backend plugins/agent-worktrees/tests`. |

## Target end-state

### agent-worktrees keeps a generic, provider-neutral **execution leg** record

Rename the concept (not necessarily the on-disk key, see back-compat below)
from `session_backend` to **`execution_leg`**, with a shape agent-worktrees
can fully interpret without knowing any provider's semantics:

```yaml
execution_leg:
  version: 1
  provider: ahp            # opaque string id; agent-worktrees does not enumerate values
  state: active            # active | disposed | unknown -- the only field agent-worktrees interprets
  binding_revision: 3       # monotonic; used for the existing higher-revision-wins reconcile rule
  blob:                     # fully provider-owned; agent-worktrees stores and returns it verbatim
    endpoint_url: ws://127.0.0.1:.../
    session_id: ...
    protocol_version: "0.7.0"
    auth_account: ...
    created_at: ...
    last_seen_at: ...
```

agent-worktrees:
- parses `version`, `provider`, `state`, `binding_revision` generically;
- treats `blob` as an opaque mapping it never inspects or validates;
- keeps its existing **unknown-shape-is-opaque** fallback (`execution_leg_opaque`), unchanged in spirit from today's `session_backend_opaque`;
- keeps the existing reconcile-on-save rule (higher `binding_revision` wins) — this logic does not need to change, only the field types it operates over.

This directly satisfies the session-hosting vision's *Host-owned execution
identity* concept ("a provider id plus an opaque blob, never a typed union
with one branch per provider") and closes the matching Non-Goal in the
agent-worktrees vision.

### A new generic CLI verb replaces `session-backend`

`agent-worktrees session-backend <status|ensure|dispose>` is AHP-shaped by
name and by behavior (it calls into `ahp_backend` directly). Replace it with:

```
agent-worktrees execution-leg set --worktree-id <id> --provider <name> \
    --state <active|disposed> --binding-revision <n> --blob-file <path|-> [--if-match-revision <n>]
agent-worktrees execution-leg get --worktree-id <id>
agent-worktrees execution-leg clear --worktree-id <id> --if-match-revision <n>
```

- `set`/`clear` require the record lock and the finalize-lock the current
  `cmd_session_backend` already takes (generic, keep as-is) — the
  finalizing/finalized-worktree refusal and the fencing/optimistic-concurrency
  check (`--if-match-revision`) are agent-worktrees-owned invariants, not
  provider-specific ones.
- **agent-worktrees never talks to an AHP endpoint, mints a GitHub token, or
  validates a `protocol_version`.** All of that moves to the caller
  (Worktree Manager), which calls `execution-leg set` only *after* it has
  itself established/verified the session against the real AHP host.
- `cmd_embody`'s `is_ahp` gate becomes a generic check: refuse `--new` when
  the target worktree (or, for `--new`, "a worktree about to be created")
  would need a non-default execution-leg provider — expressed as "the caller
  must specify a provider-owning launcher; `embody` only understands the
  direct-mux path." Exact wording is an implementation detail, not a design
  decision, since it's just an error message.
- JSON display flags (`session_ahp_live`, `session_backend`, `last_session_id`)
  are renamed to `execution_leg_live`, `execution_leg`, `last_session_id`
  (unchanged) — **breaking rename**, see back-compat below for the transition.

### Worktree Manager owns the AHP provider

`ahp_backend.py` (renamed `worktree_manager/ahp_provider.py` or similar),
`SessionBackendConfig` (moves to Worktree Manager's own config surface, with
its `kind: direct|ahp` split now expressed as **which provider Worktree
Manager launches with**, not a global agent-worktrees config concept), the
`config_dropins.py` validation block, and the `websocket-client` dependency
all move to `worktree-manager/`. Worktree Manager:

1. Decides (per its own config/user choice) whether a launch/resume uses the
   direct-mux path or the AHP path.
2. For AHP: connects, creates/verifies/disposes the session exactly as
   `ahp_backend.py` does today (logic is copied, not rewritten, in this
   slice — no behavior change).
3. Calls `agent-worktrees execution-leg set/clear` to persist the result,
   passing `--provider ahp` and the AHP-specific fields inside `--blob-file`.
4. Still launches a Mux-wrapped pane as a hard-bound client attachment for
   the resulting session, exactly as today — this is the composability point
   from the corrected vision (Mux presentation wraps the AHP backend) and is
   unaffected by this slice.

### AHP remains explicitly opt-in (operator invariant, 2026-09-05)

Unlike Mux — which becomes the default, transparent interactive experience
once Worktree Manager is present — **AHP is never auto-selected**. Today's
config gate (`session_backend.kind` defaults to `"direct"`; a worktree only
gets an AHP-hosted session when a user explicitly sets `kind: "ahp"` plus its
endpoint/account config) is a **behavior this relocation must preserve
exactly**, not merely as an implementation detail carried over by accident:

- Relocating AHP config into Worktree Manager must keep the same default-off,
  explicit-opt-in shape — no new code path may select AHP based on
  Worktree Manager merely being installed, a capability probe succeeding, or
  any other implicit signal.
- The relocated `execution-leg set --provider ahp` call only ever happens
  because the *user* configured AHP for that launch — Worktree Manager does
  not decide to "upgrade" a direct-mux launch to AHP on its own.

## Back-compat: existing persisted records

Worktrees created before this slice ships have `session_backend:` (not
`execution_leg:`) on disk with the old typed shape. agent-worktrees' loader
must keep reading the **old** key/shape as an input format, translating it
in-memory to the new generic shape (`provider="ahp"`, `blob={endpoint_url,
session_id, protocol_version, auth_account}`, `state`, `binding_revision`
carried through unchanged) — this is a pure reader-side compatibility shim,
the same pattern already used for `.agent-worktrees.yaml` vs.
`.agent-worktrees/config.yaml` elsewhere in `config.py`. New writes always use
`execution_leg:`. No migration script is needed; the shim can be retired once
telemetry/`grep` across known worktree state shows no more `session_backend:`
records exist (tracked as a follow-up, not blocking this slice).

## Ordered implementation steps (each independently landable)

1. **Add the generic `execution_leg` read path to `tracking.py`**, additive
   only: parse `execution_leg:` when present, else fall back to translating
   legacy `session_backend:` in memory. Both old and new callers keep working
   unchanged (no behavior change yet). Land + version-bump agent-worktrees.
2. **Add `execution-leg set/get/clear` CLI verbs to `agent-worktrees`**,
   alongside the existing `session-backend` verb (not yet removed). New verbs
   write only the new `execution_leg:` shape. Land + version-bump.
3. **Move `ahp_backend.py` + config + `websocket-client` dependency into
   `worktree-manager`**, updated to call the new `execution-leg set/get/clear`
   verbs via the pinned engine `--json` **subprocess** boundary (not the
   `_engine_runtime.py` in-process import) instead of the direct in-process
   `tracking`/`fin` calls `cmd_session_backend` makes today. Land + version-bump
   worktree-manager. At this point both the old and new paths work; nothing in
   `agent-worktrees` is deleted yet.
4. **Cut Worktree Manager's launch/resume/create actions over** to call the
   relocated AHP provider instead of shelling into `agent-worktrees
   session-backend`. Land + version-bump worktree-manager.
5. **Remove `cmd_session_backend`, `ahp_backend.py`, `SessionBackendConfig`,
   the `config_dropins.py` validation block, and the `websocket-client`
   dependency from `agent-worktrees`.** Keep only the legacy-record read
   shim from step 1 until the back-compat retirement follow-up. Land +
   version-bump agent-worktrees (this is the actual deletion commit — kept
   last and separate so it's trivially revertable if steps 3-4 surface a gap).
6. **Update `test_ahp_launcher_contract.py` and any other `session_backend`-
   asserting test** to the new generic shape/verb names; add worktree-manager
   tests for the relocated provider (reusing the existing AHP contract tests'
   assertions where they test protocol behavior, not location).

Each step lands as its own PR through this repo's normal `create-pr` →
Copilot review → `pr-merge --now` flow, consistent with the "serial,
single-writer" merge norm for this public repo.

## Validation

- Contract test proving `execution-leg set` with a stale `--if-match-revision`
  is rejected (fencing holds).
- Contract test proving a legacy on-disk `session_backend:` record still
  loads and reports the same `execution_leg_live`/state as before the change.
- Worktree Manager test proving the relocated AHP provider talks to
  agent-worktrees only through the pinned `--json` subprocess boundary (no
  `_engine_runtime.py` in-process import for this path).
- **Opt-in invariant test:** a launch/resume/create action with no explicit
  `session_backend`/AHP config present never calls the AHP provider or
  `execution-leg set --provider ahp`, regardless of whether Worktree Manager
  or a reachable AHP endpoint is present.
- `test_ahp_launcher_contract.py` continues to pass unmodified in spirit
  (mux still hard-binds to whatever session the provider established),
  confirming the Mux/AHP composability boundary is unaffected.
- `python tools/check-install-contract.py` and
  `python tools/check-version-bump.py` pass for every step's PR (each step
  touches only one plugin/package, per the ordering above).

## Non-Goals of this slice

- **Not the Mux relocation.** `launch-session.{sh,ps1,cmd}` and `cmd_remux`
  stay in `agent-worktrees` until the follow-up slice.
- **Not a behavior change.** AHP session creation/verification/disposal logic
  is relocated, not rewritten or improved, in this slice.
- **Not the `_engine_runtime.py` in-process-import replacement** for anything
  other than the AHP call path. The rest of `runner.py`'s in-process engine
  usage (worktree list/actions unrelated to AHP) is out of scope here.
