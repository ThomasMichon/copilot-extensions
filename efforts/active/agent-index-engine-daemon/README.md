# agent-index — Durable, Persistent Embedding-Engine Daemon

- **Slug:** `agent-index-engine-daemon`
- **Repo:** copilot-extensions (plugin home; direct-push `main`)
- **Branch(es):** `main`
- **Created:** 2026-08-03
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends [`visions/plugins/agent-index`](../../../visions/plugins/agent-index/README.md)
  (§*The embedding engine*, §self-contained-service, §local-first-standalone) —
  **vision-extending**: the durable-daemon intent is new and must be written into
  the vision first (Phase 1).

## Guiding Intent

Make agent-index's **embedding engine a durable, persistent, warm daemon that is
decoupled from the versioned service runtime**. The heavy embedding stack (torch +
transformers + sentence-transformers) is expensive to install and slow to load, so
it must live **outside the swappable versioned runtime** and **only on the machine
that hosts the indexing service**: a routine plugin update swaps the light service
runtime via the junction and **never rebuilds torch or restarts the warm engine**.

All embedding — index-time *and* query-time — flows through the persistent engine
daemon, so the **versioned service runtime stays torch-free** and light. A machine
that only *consumes* search reaches the service over the existing trusted transport
(the vision's `local-first-standalone` SSH port-forward) and installs **no embedding
stack at all**. Which machines host the engine is **never encoded in the plugin**;
role is resolved from machine-local (`~/.agent-index/`) or source-repo
(`<repo>/.agent-index/config.yaml`) configuration. The model is the agent-bridge
**session-host** analogue: a warm, long-lived worker the light coordinator talks to.

## Context

`agent-index 0.1.0-dev16` shipped the foundation — configurable engine separation
modes (`AGENT_INDEX_ENGINE_MODE` = `subprocess` | `systemd` | `external` | `auto`).
**`external`** mode — the service never manages the engine, only requires it to be
reachable — is exactly the seam a persistent, externally-owned daemon plugs into.
This effort builds the **durable engine runtime + persistent daemon + role-aware
install** on top of that seam. It is generic: no machine names live here or in the
plugin.

Locked design decisions (operator):
- **All embedding via the daemon** — the service venv is torch-free; in-process
  query embedding is off by default (query embeds through the daemon too).
- **Role by config** — resolved from `~/.agent-index/` (machine-local) or a source
  repo's `<repo>/.agent-index/config.yaml`; the plugin ships no machine list.
- **torch only on the host** — client-role installs carry no embedding stack.

## Plan

### Phase 1 — Intent (vision + patterns)
- [x] Revise the agent-index **vision** (vision-extending): the embedding engine is
      a durable, persistent warm daemon decoupled from the versioned service
      runtime; heavy stack lives on the host only; all embedding flows through the
      daemon; role is config-resolved. Touch `§The embedding engine`,
      `§self-contained-service`, `§local-first-standalone`. **Done** — extended
      `§The embedding engine`, added the **warm-durable-engine** behavior, sharpened
      `§self-contained-service` + `§local-first-standalone`, and logged a Provenance
      entry (2026-08-03).
- [ ] Add/adjust a `docs/patterns/` entry for the **durable-runtime vs
      versioned-runtime** split (heavy, persistent state outside the swappable
      runtime), if the existing patterns don't already cover it. **Deferred** to
      when the implementation (Phase 3) establishes the pattern — `docs/patterns/`
      documents *established* practice, not aspiration.

### Phase 2 — Dependency partition
- [ ] Split deps into a **light service set** (versioned runtime; no torch) and a
      **heavy engine set** (torch + transformers + sentence-transformers). Decide
      placement of the store (`lancedb`) and chunking (`tree-sitter`) — service
      needs the store to answer search; chunking runs where indexing runs.
- [ ] Default query embedding to the daemon (`AGENT_INDEX_SEARCH_IN_PROCESS=0`);
      confirm search stays responsive-when-cold via the daemon path.

### Phase 3 — Durable engine runtime + persistent daemon
- [ ] Installer provisions a **durable engine venv outside the versioned runtime**
      (alongside the durable index data), installed **once** and **preserved across
      service updates**; rebuilt only when the embedding stack itself changes.
- [ ] A **persistent, platform-native engine daemon** runs from that venv and stays
      **warm** (model loaded). `update` swaps only the versioned service runtime and
      leaves the daemon + its venv untouched (no torch rebuild, no restart).

### Phase 4 — Role-aware install
- [ ] `host` role installs the engine daemon + heavy stack; `client` role installs
      the service/CLI only (or nothing). Role resolved from `~/.agent-index/` or
      `<repo>/.agent-index/config.yaml`. No machine specifics in the plugin.

### Phase 5 — Service on the external seam
- [ ] Service defaults to `external` engine mode against the persistent daemon.
- [ ] A separate, explicit **engine-runtime update** path rebuilds the durable
      engine venv only when the embedding stack changes (decoupled from service
      `update`/cutover).

### Phase 6 — Validation, parity, tests
- [ ] Cross-platform parity (daemon + role model work without systemd); unit tests;
      installer/lifecycle coverage; docs.

## Validation Plan

- [ ] A routine plugin `update` swaps the service runtime **without rebuilding
      torch or restarting the engine daemon** — the engine stays warm (model loaded)
      across the update.
- [ ] The versioned **service venv contains no torch**, yet indexing **and** query
      both succeed through the daemon.
- [ ] A **client-role** install carries no embedding stack and reaches the service
      over the trusted transport.
- [ ] The **durable engine venv survives** a service update/rollback; the engine
      runtime changes only via its explicit update path.
- [ ] Recover/rollback leaves both runtimes consistent; the durable index is
      untouched by either runtime swap.

## Journal

### 2026-08-03 — Kickoff
- Effort created from the operator's clarified intent: the embedding engine must be
  a durable, persistent, warm daemon (session-host analogue), decoupled from the
  versioned service runtime, with **torch only on the service host** and **all
  embedding routed through the daemon** (torch-free service). Role is config-driven
  (`~/.agent-index/` or `<repo>/.agent-index/config.yaml`); no machine specifics in
  the plugin.
- Foundation already in place: `dev16` engine-separation modes (`subprocess` /
  `systemd` / `external` / `auto`); `external` is the seam this effort builds on.
- Next: claim the effort (agent-dispatch), then Phase 1 — write the intent into the
  agent-index vision (vision-extending) before implementing.
