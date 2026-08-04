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
- [x] Split deps into a **light service set** (versioned runtime; no torch) and a
      **heavy engine set** (torch + transformers + sentence-transformers). Decide
      placement of the store (`lancedb`) and chunking (`tree-sitter`) — service
      needs the store to answer search; chunking runs where indexing runs.
      **Done (dev17):** `lancedb` + the tree-sitter grammars
      (`python`/`javascript`/`typescript`/`bash`) moved into base `dependencies`
      (the light, torch-free service runtime chunks + stores); the `engine` extra is
      now **only** torch + transformers + sentence-transformers. `pip install
      agent-index` is a functional torch-free service; `[engine]` adds the heavy
      stack for the durable engine venv.
- [ ] Default query embedding to the daemon (`AGENT_INDEX_SEARCH_IN_PROCESS=0`);
      confirm search stays responsive-when-cold via the daemon path. **Moved to
      Phase 5** — flipping this default only makes sense once a standing engine
      daemon exists (external mode); flipping it before the daemon lands would
      regress a single-venv install. Kept default `1` until Phase 5.

### Phase 3 — Durable engine runtime + persistent daemon
- [x] **3a — daemon manager:** `agent_index/engine/daemon.py` — cross-platform
      management of the persistent engine daemon from a **durable venv**
      (`AGENT_INDEX_ENGINE_HOME`, default `~/.agent-index/engine`, outside the
      versioned runtime): resolve the durable interpreter, start detached +
      persistent, health-probe, PID-track, stop; `run` foreground entry for a
      platform-native task. `agent-index engine {start,stop,status,run}` CLI. The
      daemon serves the stable engine HTTP API, so a service of any code version
      talks to it over `external` mode. 13 tests.
- [x] **3b — installer provisioning:** installer builds the **durable engine
      venv** (`agent-index[engine]`) at `AGENT_INDEX_ENGINE_HOME`, **once** and
      **preserved across service updates** (rebuilt only when the embedding stack
      changes); registers a **persistent platform-native daemon task** running
      `agent-index engine run`. `update` swaps only the versioned service runtime
      and leaves the daemon + venv untouched. **Done** — `install.{ps1,sh}` gained
      `Install-Engine`/`_install_engine` (idempotent, skip-if-present, non-fatal;
      `AGENT_INDEX_NO_ENGINE_DEPS=1` to skip, `AGENT_INDEX_TORCH_INDEX` for CUDA)
      and `Register-EngineDaemon`/`_register_engine_daemon` (Windows scheduled task
      `agent-index-engine` / systemd-user unit `agent-index-engine.service`, warm
      engine never restarted on re-register). A new `engine` action provisions +
      registers explicitly; `install` runs both; **`update` calls neither** (engine
      stays warm). Live-validated on a torch host (Borealis).

### Phase 4 — Role-aware install
- [x] `host` role installs the engine daemon + heavy stack; `client` role installs
      the service/CLI only (or nothing). Role resolved from `~/.agent-index/` or
      `<repo>/.agent-index/config.yaml`. No machine specifics in the plugin.
      **Done** — `config.resolve_role()` (precedence: `AGENT_INDEX_ROLE` env →
      machine-local `<install_dir>/config.yaml` `role:`/`engine:` scalar → default
      `client`) + `agent-index role [--json]` CLI. Installers gained
      `Get-InstallRole`/`_install_role`: the `install` action provisions the engine
      **only when role resolves to `host`** (client installs stay torch-free); the
      explicit `engine` action still force-provisions (role-independent). 10 role
      tests; full suite **115 green** on Borealis; role-gated install decisions
      live-validated (client skips engine, host provisions).

### Phase 5 — Service on the external seam
- [x] Service defaults to `external` engine mode against the persistent daemon.
      **Done** — `ModelProfile.engine_mode` default flipped `auto`→`external`
      (index_config.py); the torch-free service now requires the durable daemon
      rather than trying to spawn/own an engine from its own venv.
- [x] Flip `AGENT_INDEX_SEARCH_IN_PROCESS` default `1`→`0` so query embedding
      routes through the daemon too. **Done** — flipped and converted to a
      `default_factory` (per-instance, honoring env set before construction).
      With both flips, *all* embedding (index + query) flows through the daemon
      and the service venv is fully torch-free.
- [x] A separate, explicit **engine-runtime update** path rebuilds the durable
      engine venv only when the embedding stack changes (decoupled from service
      `update`/cutover). **Done** — new `engine-update` action: `Install-Engine
      -Upgrade`/`_install_engine upgrade` (pip `--upgrade` into the durable venv)
      + `Restart-EngineDaemon`/`_restart_engine_daemon` (the ONE intended restart).
      Live-validated on Borealis: torch-free service embedded a query **through the
      daemon** (`agent-index search` → clean empty result, no torch-in-service),
      config resolved `search_in_process=False`/`engine_mode=external`,
      `engine-update` restarted the daemon (pid changed). Full suite **120 green**.

### Phase 6 — Adoption & onboarding (designate the indexer)
- [x] An explicit adoption/setup flow designates **one machine as the indexer**
      and writes role config; a **single-machine** repo is offered the full local
      stack. Running setup **on the designated machine** configures + (re)starts the
      local service+engine; running it elsewhere installs the client. Realizes
      vision §adoption-designates-one-indexer. **Done** — `agent-index setup`
      (`--single` / `--indexer <machine>` / `--ssh` / `--endpoint` / `--repo` /
      `--yes` / `--json`; interactive prompts on a TTY): records the shared
      **indexer designation** into `<repo>/.agent-index/config.yaml` and this
      machine's concrete **`role:`** into the machine-local config (which the
      installer already reads). Host/client is decided by matching this machine's
      identity (`config.machine_id()`, hostname or `AGENT_INDEX_MACHINE`) against
      the designation. Config is now structured (PyYAML added to base deps; the
      Phase-4 regex scanner kept as a resilient fallback). 8 adoption tests; full
      suite **128 green**; real `setup` flows live-validated on Borealis
      (single→host, remote-indexer→client, designation + role written).

### Phase 7 — Capability-matched engine device
- [ ] Detect **CUDA compatibility + machine specs** (compute, memory) and select
      the engine **device** — GPU when compatible, CPU only above a capability
      floor, flagging an underpowered host. Fold in the **engine CPU-fallback fix**
      (engine defaults `device=cuda` and 500s instead of falling back when CUDA is
      absent — observed on Borealis WSL, Phase 5). Realizes vision
      §capability-matched-engine-runtime.

### Phase 8 — Client routing to the designated indexer
- [ ] Adoption **generates each client's routing config** (endpoint pointing at the
      designated indexer) reaching it over the **SSH port-forward** trusted
      transport; clients carry no model stack. Ensure the search path **degrades
      lexical-first** when the remote daemon is unreachable (honor
      §responsive-when-cold cross-host). Realizes vision §local-first-standalone
      (routing) + §adoption-designates-one-indexer (client side).

### Phase 9 — Validation, parity, tests
- [ ] Cross-platform parity (daemon + role model work without systemd); unit tests;
      installer/lifecycle/adoption coverage; docs; the deferred `docs/patterns/`
      durable-vs-versioned-runtime entry. Then the single dev17->dev18 bump and land
      the branch to `main`.

## Open Design Questions (Phases 6-8 — confirm before building)

1. **Where the adoption flow lives:** a new interactive `agent-index setup`/`adopt`
   command, vs. prompts inside `install.{ps1,sh}`, vs. purely reading an
   operator-authored `<repo>/.agent-index/config.yaml`. (Leaning: a `setup` command
   that writes config, with the installer staying non-interactive/idempotent.)
2. **How the indexer is designated + discovered by clients:** repo-committed
   `<repo>/.agent-index/config.yaml` naming the indexer host + its SSH target
   (shared, version-controlled), vs. machine-local only. (Leaning: repo config names
   the indexer + SSH alias; machine-local overrides.)
3. **SSH port-forward ownership:** does the plugin *establish* a persistent tunnel
   (a client-side systemd/scheduled task), or only point `AGENT_INDEX_ENDPOINT` at a
   forward the operator's SSH mesh already provides? (Leaning: point + document;
   optional managed tunnel later.)
4. **Capability floor:** the CPU-fallback threshold (min cores / RAM) and whether an
   underpowered host is a hard block or a warning. (Leaning: warn + proceed, with a
   sane default floor, e.g. >=8 GB RAM / >=4 cores.)

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

### 2026-08-03 — Phase 6: adoption/onboarding (`agent-index setup`)
- Design confirmed with operator: dedicated `setup` command (installer stays
  non-interactive); indexer discovery via repo-committed `.agent-index/config.yaml`
  + machine-local override; **point-only** SSH (set endpoint, rely on the SSH mesh);
  **hard-block** an underpowered indexer (Phase 7); build one phase at a time.
- Shipped `agent-index setup`: designates one indexer, writes the shared
  `indexer:` designation into `<repo>/.agent-index/config.yaml` and this machine's
  `role:` into the machine-local config; host/client decided by matching
  `machine_id()` against the designation. `--single` = full local stack.
- Config is now structured YAML: added **PyYAML** to base deps (small, torch-free),
  with the Phase-4 regex role-scanner retained as a resilient fallback. New config
  helpers: `machine_id`, `repo_root`, `repo_config_path`, `read_indexer`,
  `write_machine_role`, `write_indexer_designation`.
- 8 adoption tests; full suite **128 green**; live-validated on Borealis
  (single→host, remote-indexer→client, designation + role written). No version bump.
- Next (Phase 7): capability-matched device (CUDA + specs → device, hard-block
  underpowered), folding in the engine CPU-fallback fix.

### 2026-08-03 — Scope extension: adoption / capability / routing (operator directive)
- Operator clarified the intended **onboarding model**: adopting agent-index into a
  harness repo **designates one machine as the indexer**; a single-machine repo is
  offered the full local stack; adoption **detects CUDA + specs** and picks the
  engine device (CPU fallback above a capability floor); the designated machine's
  setup (re)starts the local service+engine, while **every other machine** installs
  the client with **routing** to reach the designated host.
- Vision revised (vision-extending): added **adoption-designates-one-indexer** and
  **capability-matched-engine-runtime**, and sharpened **local-first-standalone** so
  adoption generates each client's routing. Provenance entry added.
- Effort plan reshaped: new **Phase 6** (adoption/onboarding), **Phase 7**
  (capability-matched device — folds in the Phase-5 engine CPU-fallback fix),
  **Phase 8** (client routing), and validation moves to **Phase 9**. Recorded four
  **Open Design Questions** (flow home, indexer designation/discovery, SSH-forward
  ownership, capability floor) to confirm with the operator before building.

### 2026-08-03 — Phase 5: service defaults to the external daemon seam
- Flipped the two standing defaults so the torch-free service routes **all**
  embedding through the durable daemon: `ModelProfile.engine_mode` `auto`→
  `external` and `AGENT_INDEX_SEARCH_IN_PROCESS` `1`→`0` (also converted the latter
  to a `default_factory` so an env set before `IndexConfig()` is honored, matching
  `engine_mode`). Env vars still select `subprocess`/`systemd`/`auto` + in-process
  embedding for a single-venv install.
- Added the explicit **engine-runtime update** path: `engine-update` action
  (`Install-Engine -Upgrade` / `_install_engine upgrade` → pip `--upgrade` into the
  durable venv, then `Restart-EngineDaemon` / `_restart_engine_daemon`). This is
  the ONE place a daemon restart is intended — decoupled from service `update`,
  which still never touches the engine.
- Validated on Borealis: service venv torch-free, yet `agent-index search`
  embedded the query **through the daemon** `/embed` and returned cleanly
  (empty index, no torch-in-service error); config resolved
  `search_in_process=False` / `engine_mode=external`; `engine-update` rebuilt the
  durable venv and restarted the daemon (pid changed). Full suite **120 green**.
- Observed (pre-existing, out of scope): the engine defaults `device=cuda` and does
  **not** fall back to CPU when CUDA is unavailable (`/embed` 500 on Borealis WSL's
  too-old CUDA driver until `AGENT_INDEX_DEVICE=cpu` was set). Candidate follow-up.
- No version bump (batched). Next: Phase 6 — parity/tests + docs/patterns entry,
  then the single dev17→dev18 bump and land the branch to `main`.

### 2026-08-03 — Phase 4: config-driven role-aware install
- `config.resolve_role()` resolves this machine's role — `host` (runs the durable
  engine daemon) or `client` (light, torch-free service/CLI only). Precedence:
  `AGENT_INDEX_ROLE` env → machine-local `<install_dir>/config.yaml` `role:`/`engine:`
  scalar → default `client`. The scalar is read with a dependency-light scanner
  (no PyYAML pulled into the torch-free service). Exposed as `agent-index role
  [--json]`. **No machine names in the plugin** — role is pure configuration.
- Installers gained `Get-InstallRole`/`_install_role`; the **`install`** action now
  provisions the engine + daemon **only when role == host** (a `client` install
  stays entirely torch-free), while the explicit **`engine`** action still
  force-provisions for manual host setup. `update` remains engine-untouched.
- Validated on Borealis: default (no config) → `client`, engine skipped, no engine
  venv; `role: host` in config.yaml → engine provisioned. 10 new role tests;
  **full suite 115 green**; ruff (F,E9) clean. No version bump (batched).

### 2026-08-03 — Phase 3b: installer provisions the durable engine + daemon
- `install.ps1` + `install.sh` now provision the **durable engine venv**
  (`agent-index[engine]`) at `AGENT_INDEX_ENGINE_HOME` (default
  `~/.agent-index/engine/.venv`) and register a **persistent platform-native
  daemon** running `agent-index engine run` (Windows scheduled task
  `agent-index-engine`; systemd-user unit `agent-index-engine.service`).
- Provisioning is **idempotent** (skip-if-present), **non-fatal** (a torch-stack
  failure leaves the light service fully working), opt-out via
  `AGENT_INDEX_NO_ENGINE_DEPS=1`, and honors `AGENT_INDEX_TORCH_INDEX` (CUDA wheel
  index) — default PyPI torch is the CPU wheel. `zdd` (a non-PyPI declared dep) is
  installed from the vendored lib first, mirroring the service venv.
- **Update-safety:** the `install` action runs `Install-Engine` +
  `Register-EngineDaemon`; the new **`engine`** action runs them explicitly; the
  **`update`** action runs *neither* — it swaps only the versioned service runtime
  + junction, so torch is never rebuilt and the warm daemon is never restarted.
  Re-registration also refuses to bounce a warm engine (start-only-if-not-serving).
- No version bump (still `0.1.0-dev17`); batched on the effort branch. Both scripts
  pass `bash -n` / PS parse. Next: live-validate on Borealis, then Phase 4.

### 2026-08-03 — Batched onto an effort branch
- Per operator direction, the plugin (host) work moves off per-phase direct-pushes
  to `main` onto a dedicated **effort branch** (`agent-index-engine-daemon`, its own
  worktree) to stop churning the marketplace version anchor. The version stays at
  the last released `0.1.0-dev17` while phases accumulate; the batch lands with a
  **single** version bump at merge. Phases 1–2 already landed on `main` (vision
  revision; dependency partition) before this switch.

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
