# Phase 3e — Relocate terminal-profile handling out of agent-worktrees

- **Parent effort:** [`README.md`](README.md) § Phase 3e
- **Tracks:** [#3390](https://github.com/ThomasMichon/copilot-extensions/issues/3390)
- **Governing vision:** [`visions/installer`](../../../visions/installer/README.md)
  §`optional-worktree-agent-control-plane`; the matching Non-Goal in
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md#non-goals--boundaries).
- **Scope of this doc:** the terminal-**profile** system (which launch targets
  a machine's terminal app carries a profile for, and mirroring that
  selection into real terminal-app fragments) — a **separate** capability
  from Mux/AHP session mechanics (Phase 3b), sharing only the general
  "terminal handling leaves agent-worktrees" direction.
- **Status:** Planned — this doc is Phase 3e's first checkbox (the reviewed
  migration plan); no code has moved yet.

## Why this slice, and why it's harder than it first looked

Phase 3d's investigation into the Picker's `_engine_runtime.py` boundary
found `agent_worktrees.profiles`' actual Picker call sites (`TargetSel`,
`load_selection`, `save_selection`, ...) look like pure, dependency-free
logic — structurally identical to the `agent-procutil`/`dropin-registry`
libs #3359 vendored. The natural first read was "vendor a 4th shared lib."
Operator direction overrode that: `profiles` is also agent-worktrees' own
production dependency (a real public CLI surface, not just something the
Picker happens to import), and the actual goal is a full relocation, not a
synced copy — matching the standing Mux/AHP precedent (#2062) and the
longer-term direction of `agent-worktrees update` becoming
`worktree-manager update`.

Once evidence-gathering went past the Picker's own call sites, the real
shape turned out **larger** than `profiles.py` alone:

- `profiles.py` (235 lines) is genuinely small and self-contained: the
  `TargetSel` selection model, its `~/.<project>/config.yaml` persistence,
  and the default-selection rule. This part is a clean, mechanical move.
- `terminal_fragment.py` (1016 lines) is **not** self-contained. Its
  fragment-building core (`build_fragment`, `collect_local_projects`) reads
  agent-worktrees' own **project/repo registry** (`repos.yaml`/
  `projects.yaml`, per-project `machines.yaml`) via inline imports of
  `agent_worktrees.config`, `agent_worktrees.installer`, and
  `agent_worktrees.repos` (terminal_fragment.py:585-587) — a real,
  structural coupling to data agent-worktrees owns, not an accident of
  import order. Separately, `terminal_fragment.py` also owns pure
  terminal-app mechanics with zero agent-worktrees coupling: GUID
  generation/stability (`stable_guid`), Windows Terminal's live installed
  `state.json` diagnosis (`diagnose_wt_state`, `_wt_local_state_dir`,
  `_wt_fragments_dir`), and profile reconciliation against what's actually
  installed (`reconcile_generated_profiles`).
- `plugins/agent-worktrees/scripts/install.ps1` carries its **own**
  PowerShell terminal integration (`Deploy-TerminalScripts`,
  `Sync-TerminalState`, `Get-SettingsProfileGuids`,
  `Clean-TerminalSettingsJson`) that calls into the Python modules above —
  a second-language surface this relocation has to account for, not just
  the Python package.
- `picker_profiles_cli.py` (628 lines) is agent-worktrees' own CLI surface
  (`profiles get/apply`, `terminal-fragment [--explain|--doctor|
  --migrate-selections]`, and the terminal-mirroring half of `repair`) —
  real, documented, user-facing commands, not Picker-only plumbing.
- `picker_support/data_local.py` + `picker_support/profiles_io.py` are
  agent-worktrees' own **bundled legacy Picker**'s copy of the same read/
  write path worktree-manager's transplanted `production_picker/
  profiles_io.py` + `engine_profiles_view.py` already duplicate.

This means the boundary is **not** "one module moves, done" — it splits into
a genuinely self-contained piece (the selection model) and a piece with real
registry coupling (the fragment builder), each needing its own disposition.

## Current-state inventory (evidence, `main` as of 2026-09-23)

| File | Lines | What lives there today | Coupling |
|---|---|---|---|
| `plugins/agent-worktrees/src/agent_worktrees/profiles.py` | 235 | `TargetSel` dataclass; `load_selection`/`save_selection`/`has_selection` (read-modify-write `~/.<project>/config.yaml`'s `terminal_profiles:` key); `default_selection`/`is_default_on`/`seed_selection` (the minimal-per-agent + bare-cross-machine default rule) | **None** — every function takes a caller-supplied `config_path`; no import of any other `agent_worktrees.*` module. Clean relocation candidate. |
| `plugins/agent-worktrees/src/agent_worktrees/terminal_fragment.py` | 1016 | `build_fragment`/`collect_local_projects` (the whole-machine fragment builder, one JSON blob per registered project); `stable_guid`/GUID validation; `diagnose_wt_state`/`reconcile_generated_profiles` (reads/reconciles the live Windows Terminal `state.json`); `migrate_local_selections` (legacy display-name -> machine-key rewrite) | **Structural** — `collect_local_projects` (line 585-587) imports `agent_worktrees.config`, `.installer`, `.repos` to enumerate every registered project + its roster. Also imports `profiles` (line 50, model-only, not coupled). |
| `plugins/agent-worktrees/src/agent_worktrees/picker_profiles_cli.py` | 628 | `agent-worktrees profiles get/apply`, `terminal-fragment [--explain\|--doctor\|--migrate-selections]`, the terminal-mirroring half of `repair` (`_mirror_terminal_profiles`/`_refresh_terminal_profiles`/`_resolve_terminal_install_script`) | Calls both `profiles` and `terminal_fragment`, plus `_core()` (`__main__.py`) helpers for JSON output/error conventions and the worktree-manager handoff (`_exec_worktree_manager`, `_usable_worktree_manager`). |
| `plugins/agent-worktrees/scripts/install.ps1` (`Deploy-TerminalScripts`, `Sync-TerminalState`, `Get-SettingsProfileGuids`, `Clean-TerminalSettingsJson`) | — | The PowerShell half: deploys/repairs Windows Terminal fragment files on disk, drives the same reconciliation `terminal_fragment.py` computes | Calls into the Python CLI (`terminal-fragment`/`repair`) as a subprocess already — this is the one surface that's *already* boundary-clean; it just needs to point at wherever the verb ends up living. |
| `plugins/agent-worktrees/src/agent_worktrees/picker_support/data_local.py` (:74-81) + `picker_support/profiles_io.py` | — | agent-worktrees' own **bundled legacy Picker**'s read/write path for the Profiles grid | Duplicate of worktree-manager's transplanted copy (below); the bundled Picker itself is largely superseded (Phase 6 done) but not yet deleted. |
| `worktree-manager/src/worktree_manager/production_picker/profiles.py` | 6 | `__getattr__` proxy: `engine_module("profiles")` | The Phase 3d call site this relocation ultimately closes out. |
| `worktree-manager/.../picker_tui/profiles_io.py`, `engine_profiles_view.py` | — | worktree-manager's transplanted copies of the Picker's Profiles-grid read/write + render, both importing `.. import profiles as profiles_mod` (the proxy above) | Becomes a **direct** import of the relocated module once it lives in worktree-manager; no more proxy needed. |

## Target end-state

### `profiles.py` moves to worktree-manager unchanged (no coupling to unwind)

Copy `TargetSel` + the load/save/default-selection functions verbatim into
`worktree_manager/` (a new owned module, e.g.
`worktree_manager/terminal_profiles.py` — not a `libs/` vendored copy, since
after the move there is exactly one owner, not two synced copies). No
behavior change: same `~/.<project>/config.yaml` `terminal_profiles:` key,
same default-selection rule. Every existing on-disk selection continues to
read/write identically because the storage format doesn't change, only which
package's code touches it.

### `terminal_fragment.py` splits at its one real seam

- **The GUID/state-diagnosis/reconciliation core** (`stable_guid`,
  `diagnose_wt_state`, `reconcile_generated_profiles`, the live
  `state.json` read/write) has zero agent-worktrees coupling once it
  receives its inputs as plain data (a list of already-resolved
  `ProjectInput`s) rather than assembling them itself. This moves to
  worktree-manager alongside `profiles.py`.
- **`collect_local_projects`** (the part that walks agent-worktrees'
  project/repo registry) is the one genuinely agent-worktrees-owned
  read. Two shapes to choose between (open question below):
  1. Keep a thin, registry-reading function in agent-worktrees that
     enumerates `(project, roster, config_path)` tuples as plain JSON via a
     **new** `--json` CLI verb (e.g. `agent-worktrees projects
     terminal-inputs`), and have worktree-manager's relocated
     `build_fragment` consume that verb's output instead of calling
     Python functions directly.
  2. Give worktree-manager its own read access to the same on-disk
     registry files (`repos.yaml`/`projects.yaml`/`machines.yaml`) the way
     it already reads other agent-worktrees-adjacent config, if those
     formats are already treated as a stable, documented, cross-consumer
     contract elsewhere in this repo (needs confirming against
     `docs/install-contract.md` before assuming either way).

### agent-worktrees' CLI verbs retire, clean cutover

Per the Mux/AHP precedent's "clean, decisive cutover, not an
indefinitely-maintained pair": `agent-worktrees profiles`/`terminal-fragment`
and the terminal-mirroring half of `repair` are deleted once worktree-manager
has an equivalent, proven surface — not kept alive in parallel indefinitely.
`install.ps1`'s `Deploy-TerminalScripts`/`Sync-TerminalState`/
`Get-SettingsProfileGuids`/`Clean-TerminalSettingsJson` repoint at whatever
CLI (agent-worktrees' remaining registry-read verb, or worktree-manager's own
binary) ends up owning each half, mirroring the Mux relocation's own
launcher-script repoint step (Phase 3b Slice 2a).

### agent-worktrees' bundled legacy Picker

`picker_support/data_local.py`/`profiles_io.py` are the bundled Picker's own
copy — Phase 6 already retired the bundled Picker as the operator-visible
default. Confirm whether it still needs a working Profiles grid at all (a
zero-provider fallback path?) before deciding whether this copy is deleted
outright or kept pointed at a still-present agent-worktrees `profiles.py`
compatibility shim during the transition.

## Back-compat: existing persisted selections

No format change — `terminal_profiles:` stays exactly where it is
(`~/.<project>/config.yaml`), read the same way. The only thing moving is
**which package's code** reads/writes it, so no migration script or reader
shim is needed for the selection data itself (unlike Phase 3b's
`session_backend:` -> `execution_leg:` rename, which did change the on-disk
shape). `migrate_local_selections`' existing display-name -> machine-key
rewrite is a separate, already-existing migration concern that moves with
`terminal_fragment.py` unchanged.

## Ordered implementation steps (each independently landable)

1. [x] **Relocate `profiles.py` verbatim into worktree-manager** as an owned
   module (not vendored). Update worktree-manager's `profiles_io.py` /
   `engine_profiles_view.py` to import it directly; delete
   `production_picker/profiles.py`'s proxy shim. No agent-worktrees change
   yet — agent-worktrees keeps its own copy and its `profiles`/
   `terminal-fragment` CLI verbs working exactly as today (two copies exist
   briefly, matching Phase 3b Step 3's transitional shape). **Landed** — PR
   [#3398](https://github.com/ThomasMichon/copilot-extensions/pull/3398).
2. [x] **Resolve the registry-read boundary (direct file read).** Extended
   `harness_state.py` — worktree-manager's existing dependency-free
   `repos.yaml`/`projects.yaml`/per-project `config.yaml` reader — with
   `SshEnvironment`/`RosterMachine` dataclasses, `project_roster()` (a
   verbatim port of `terminal_fragment._load_roster`), and
   `ProjectInfo.wsl_distro`/`wsl_state`/`roster` fields populated in
   `build_projects()`. This gives the relocated fragment-builder core
   (Step 3) everything `collect_local_projects` reads today, through the
   same file-reading contract `harness_state.py` already established.
3. [ ] **Relocate the GUID/reconciliation/state-diagnosis core of
   `terminal_fragment.py`** into worktree-manager, wired to consume Step 2's
   `harness_state.build_projects()` instead of in-process
   `config`/`installer`/`repos` imports.
4. [ ] **Give worktree-manager an equivalent CLI/config surface** for
   `profiles get/apply` and `terminal-fragment [--explain|--doctor|
   --migrate-selections]`, proven against the same scenarios agent-worktrees'
   existing tests cover.
5. [ ] **Repoint `install.ps1`'s** `Deploy-TerminalScripts`/
   `Sync-TerminalState`/`Get-SettingsProfileGuids`/`Clean-TerminalSettingsJson`
   at the new owner, following Phase 3b Slice 2a's launcher-script repoint
   pattern (copy verbatim, hash-verify, repoint, keep a direct fallback for
   the absent-Manager case).
6. [ ] **Delete agent-worktrees' `profiles`/`terminal-fragment` CLI verbs,
   `picker_profiles_cli.py`'s terminal-mirroring code, and
   `picker_support/data_local.py`'s/`profiles_io.py`'s Profiles-grid path**
   (pending the bundled-Picker disposition question above) — the actual
   deletion commit, kept last and separate per the Mux/AHP precedent's
   revertability discipline.
7. [ ] Close out Phase 3d's `profiles` checkbox once worktree-manager's
   Picker call sites import the relocated module directly.

## Validation

- Contract test proving a machine's existing `terminal_profiles:` selection
  reads identically before/after the relocation (byte-for-byte same
  `get`/`apply` JSON shape).
- `diagnose_wt_state`/`reconcile_generated_profiles` continue to pass their
  existing assertions against a fixture Windows Terminal `state.json`, now
  exercised from worktree-manager's test suite.
- Whatever registry-read boundary Step 2 adds gets a contract test the same
  way `docs/engine-picker-contract.md` pins `engine_client`'s existing verbs.
- `install.ps1`'s repointed functions still deploy/repair a fragment
  end-to-end on a real (or fixture) Windows Terminal install.
- `tools/check-version-bump.py`/`check-vendored-libs-sync.py` pass for every
  step's PR.

## Open questions for the operator / design review

1. ~~**Registry-read shape for `collect_local_projects`.**~~ **Resolved
   (operator direction, 2026-09-23): direct file read**, not a new
   agent-worktrees CLI verb — worktree-manager reads
   `repos.yaml`/`projects.yaml`/`machines.yaml` directly, the same way its
   existing `harness_state.py` module already reads `repos.yaml`,
   `projects.yaml`, and per-project `config.yaml` as a dependency-free
   contract (its own docstring: "reads that state from the files the harness
   already writes for its OWN reasons — never by importing plugin code").
   Landed: `harness_state.py` gained `SshEnvironment`/`RosterMachine`
   dataclasses, `project_roster()` (a verbatim port of
   `terminal_fragment._load_roster`), and `ProjectInfo.wsl_distro`/
   `wsl_state`/`roster` fields populated in `build_projects()`.
2. **Bundled legacy Picker's Profiles grid.** Does `picker_support/
   data_local.py`'s copy need to keep working (a zero-provider fallback), or
   is it dead code safe to delete alongside the CLI verbs in Step 6?
3. **Sequencing against Phase 3d.** Since Phase 3d's `profiles` checkbox
   depends on Step 1 here, confirm whether Phase 3e Step 1 should land before
   or independent of Phase 3d's other groups — they don't share a call site,
   so no ordering constraint was found, but flagging in case a reason exists
   to sequence them. **Resolved:** Step 1 landed independently (PR #3398)
   and closed Phase 3d's `profiles` checkbox directly; no conflict found.
