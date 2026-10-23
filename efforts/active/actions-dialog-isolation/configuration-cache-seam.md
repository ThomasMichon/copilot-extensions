# Proposed configuration-cache seam — #7940

Back to [Actions Dialog Isolation](README.md).

**Supplier/locality amendment pending source review.** The exact file-store,
derived-consumer and supplier surfaces below have integration admission.
Locality admission is limited to `data_local.is_local` and immediate
`_resume_decision`/`_decide` handling. The headed-launch bypass guard remains an
explicit extra dependency. These are agent-recommended mechanics, not completed
behavior. The #7940 episode does not take startup ownership.

## Evidence and invariants

The source at the merged #8126 commit
`0327e0e6d30b831b9bd54eeccff62dad4a61d075` still has the same relevant
`production_picker/project_config.py` interface:

- `ConfigCacheSession(ttl)` discards `ttl`; `scope()` is a null context.
- `_cached_project_yaml`, `_cached_repo_yaml` and `_load_machines_yaml`
  use unbounded lifetime memoization. The first two include scalar binding
  and epoch in their keys; the machine cache is keyed only by repository path.
- `clear_caches(reset_binding=False)` advances the scalar epoch and clears
  caches without discarding accepted scalar values.
- `bind_bootstrap` replaces the scalar cache object, isolating old workers.
- `_input_scalar` supplies already-prepared values to the event loop;
  `_scalar` refuses cold event-loop lookup.

Preserve all of those scalar/binding protections. Do not change `_ScalarCache`,
`bind_bootstrap`, `_input_scalar`, `_scalar`, `_project_dir`, `_repo_dir`,
`_machine_name`, their scalar memoizers, or `_install_dir`. Do not change engine
protocols, bootstrap payloads, pending-binding states, setup epochs, telemetry
reporters or authoritative row publication.

## Exact proposed ownership

All paths below are relative to
`worktree-manager/src/worktree_manager/production_picker`.

| Surface | Proposed change | Boundary |
|---------|-----------------|----------|
| `project_config.ConfigCacheSession.__init__`, `.scope` | Retain signatures and yielded session; retain supplied TTL; enter a worker-owned file-snapshot scope | No engine lookup, UI wait or new thread at scope construction |
| `project_config.cached_load_config_scope` | Enter the same implementation with compatibility defaults | No second cache implementation |
| `project_config.clear_caches` | Add one file-cache invalidation notification after existing scalar/epoch behavior | Existing scalar statements, signature and reset behavior unchanged |
| `project_config._read_yaml`, `_cached_project_yaml`, `_cached_repo_yaml` | Route file reads through bounded snapshot entries; distinguish missing, malformed and unreadable input | Preserve public value shapes; no cached error presented as valid empty config |
| `project_config._load_machines_yaml`, `load_machines_yaml`, `machines_yaml_path` | Include binding/project and both candidate paths plus any actual supplier in dependency identity | Preserve merge precedence, alias handling and existing parser/normalizer |
| `project_config.load_config` | Route default-branch registry fallback through the same read scope | No refresh of pinned engine scalar values |
| Focused new cache tests | Fake clock, controlled file replacement, barriers, work counts | No native benchmark or production UI |

`engine_loading._load_config_cache_scope` remains the owner of the session
instance and `60.0` TTL. Its callers already enter the scope on setup/reload and
prewarm workers. No edits there are requested for the cache-storage portion.

## Data and algorithm

1. Replace, rather than layer over, the three indefinitely memoized file stores.
   Use one private store keyed by **scalar binding object identity, captured
   scalar epoch, project, operation and resolved input paths**. Binding identity
   is an opaque key; the file cache never edits it. Add a separate file revision
   and invalidation generation, not another engine/setup epoch.
2. A cache entry holds the last accepted value, dependency paths (including
   absent higher-precedence candidates), content signatures, successful
   validation time, last attempt time, and `ready/missing/error` outcome.
   Internal values are immutable snapshots or defensively copied; callers
   cannot mutate shared state. Bound storage to the current binding and active
   readers; completed obsolete generations cannot accumulate indefinitely.
3. TTL is demand-driven: the next **background read** at or after 60 seconds
   validates the relevant bounded dependency set. It is not a promise of a UI
   update exactly 60 seconds later while idle. A scope pins one revision for a
   logical multi-read pass, so expiry midway cannot mix generations.
4. Before expiry, hits are in-memory only: no stat, Git, engine command, network
   or registry traversal. At expiry, read the known dependency files once;
   compare bytes/content signatures and reparse only changed inputs. Do not
   rely solely on mtime/size: same-size replacement with preserved timestamps
   must still be visible within the TTL. This is bounded YAML-content work,
   never a repository/tree hash or whole-registry materialization.
5. One in-flight validation per logical key/generation: a short metadata lock
   installs a future/event, then the worker performs I/O outside that lock.
   Other background readers join that result. No UI thread waits on its event
   or lock. A cold/expired UI read reports not-ready; it does not perform a
   synchronous reload or silently return stale data as fresh.
6. Publication checks binding identity, captured scalar epoch and file
   invalidation generation again. Manual clear/rebind while blocked discards
   the old completion. Existing scopes may retain their old immutable snapshot
   internally, but cannot publish it into the current generation.
7. Errors are shared by concurrent readers for that validation attempt, with
   explicit outcome and bounded retry timing; they do not reset the successful
   freshness timestamp. Missing optional files are distinct from malformed or
   inaccessible files. Required missing/invalid input uses existing failure
   propagation, not a successful `{}` or authoritative empty roster. Retaining
   old data for presentation requires a visible stale/error state at the
   consumer; without that consumer contract, fail the refresh.
8. `clear_caches()` remains an immediate invalidation, even inside the TTL.
   `reset_binding=True` retains its existing semantics. `ttl=0` compatibility
   means reuse inside one scope only; independent scopes revalidate. Nested
   scopes restore their prior context on exit. Scoped worker access can use
   `ContextVar`, but each existing thread must explicitly enter the shared
   session as it does today; no implicit cross-thread propagation.

## Supplier and template inputs: explicit admission dependencies

The dependency set is not just one `machines.yaml`:

- project config at the accepted `config-dir`;
- both repository config candidates, including a previously absent preferred
  candidate;
- both repository machine-roster candidates, preserving canonical-over-legacy
  merge precedence;
- a selected knowledge-supplier config/anchor and its candidate roster paths
  when fallback is actually used;
- registry files contributing the default-branch or supplier selection.

A changed parent input must recompute its selected dependency set in the same
worker attempt. Supplier switches, newly created preferred files and deletion
of a prior supplier invalidate the derived result. No broad scan is justified
merely because a TTL elapsed.

**Current limitation requiring a decision:** `machines_yaml_path` uses
`harness_state.project_config` plus `harness_state.build_projects` for fallback.
The latter materializes all registered projects and rosters; hiding it behind
a TTL would retain a broad cold path. A complete bounded supplier resolver needs
the existing registry owner to expose or approve a direct selected-entry path
and its input dependencies. Do not copy its selection rules into a new
Manager-owned registry authority. This proposal does not authorize edits to
`harness_state`.

The actual `machine_transport.parse_machines_yaml_file` reads a YAML document
and parses its `machines` mapping; this trace establishes no external template
or include expansion. Therefore do not claim referenced-template invalidation
is solved. If a configuration supplier expands templates, its owner must supply
the expanded dependency list/revision. A template fixture must exercise that
real supplier contract, not invent template syntax in this cache.

## Consumer freshness is a separate admitted seam

A cache-only fix cannot establish that the displayed roster refreshes:
`engine_pivots._machine_key_map` retains `_mkey_map` indefinitely after its first
successful prewarm, and current setup consumers need an existing event/refresh
to request new config.

The proposed cache can satisfy **fresh reads at the next demand**, but full
#7940 closure additionally requires the consumer owner to accept:

- invalidate derived mapping/config views on the existing manual reload and
  successful new file revision, without resetting unrelated selection;
- route renewed demand through existing workers rather than polling or
  creating a service;
- admit completion only for the current setup/source generation, and display
  stale/error/not-ready state when a refresh fails.

Do not edit `engine_pivots`, `engine_runtime` or `engine_loading` under the
Actions-only file ownership. The integration owner must name an executor for
these exact consumer hooks, or retain them as explicit open #7940 work.

## Test matrix and stop conditions

| Case | Deterministic assertion |
|------|-------------------------|
| Warm, unchanged, before TTL | Zero file/stat/engine/subprocess calls; equivalent values |
| TTL boundary | Fake clock at 59.999/60.0; first expired worker revalidates; no wall-clock sleep |
| Atomic/same-metadata replacement | Updated bytes observed at TTL even with same size/mtime; changed input parsed once |
| Concurrent cold/expired access | Barrier-start multiple workers; one read/parse per key and generation, shared outcome |
| Different binding/project, identical paths | No result reuse across binding identity; no cross-source publication |
| Manual invalidation during blocked read | Old completion discarded; new generation wins; unrelated selection retained by owner test |
| Bootstrap rebind during blocked read | Existing scalar epoch/object isolation preserved; no new scalar calls on UI thread |
| Error then repair | Error is explicit, old value not marked current; retry/manual clear accepts repaired input |
| Missing/deleted/preferred file appears | Absence is tracked; precedence/dependency graph changes correctly |
| Supplier switches or changes alone | Derived roster changes without root YAML edit; no all-project discovery |
| Referenced template changes | Pending real supplier dependency contract; cannot mark passed with a synthetic unsupported parser |
| UI reads while worker blocked | No lock wait or I/O; explicit not-ready/stale outcome; Escape remains independent |
| Nested scope/exception/cancellation | Context restored; waiter released; no poisoned in-flight entry |
| Scope expires mid-pass | One coherent revision for the pass; next demand observes expiry |
| Older-engine/fixture compatibility | Scalar fallback and public return shapes retained; `ttl=0` behavior explicit |
| Derived map/manual reload | Owner integration test proves map refreshed and selection preserved; cache unit test is insufficient |

The execution amendment below admits file-store/consumer work after its source
review. Supplier edits remain blocked independently; that does not waive the
production supplier gate or allow #7940 completion. Native/menu/input budgets,
release and installed qualification remain separate. No benchmarks are requested.

## Execution amendment

### Admitted surfaces and preserved contracts

The integration owner admits the file-store methods listed above plus only the
necessary `_machine_key_map` invalidation and existing manual-reload/accepted
file-revision request/completion hooks in `engine_pivots`, `engine_runtime` and
`engine_loading`. This supersedes the earlier proposal's consumer-hook hold.
The later supplier/locality amendment also admits the exact supplier owner API,
while retaining the separately identified headed-launch guard dependency.

Keep `_ScalarCache`, `bind_bootstrap`, `_input_scalar`, `_scalar`, `_project_dir`,
`_repo_dir`, `_machine_name`, `_install_dir` and scalar memoizer APIs/statements
unchanged. `clear_caches` may only add file invalidation after its existing
statements. Do not change startup composition, pending binding, telemetry or
authoritative feed behavior.

The cache key holds the actual binding object, never its numeric `id()`.
Current state retains only bounded current entries; a retired scope retains
only its own immutable generation until exit. Nested scopes restore the prior
context on normal/error/cancel exit. No store keeps a chain of old generations.
Single-flight ownership is per key and generation; all exit paths notify
waiters, including loader exceptions/cancellation. UI access never waits on a
flight or performs a metadata/file/provider lookup. Missing required data is
not-ready; existing `ValueError`/`FileNotFoundError` consumer semantics remain
the error boundary, rather than introducing a new success-shaped empty result.

The 60-second bound is demand-driven, measured with a monotonic clock. A scope
pins its logical generation even when expiry occurs midway through a pass.
`ttl=0` provides reuse only within that scope. At expiry, bounded dependency
bytes are read once, including same-size/same-mtime replacements; unchanged
bytes reuse parsed data. Warm reads do zero file, stat, provider or subprocess
work. Manual clear and rebind reject obsolete completions without clearing
accepted scalar bindings or unrelated UI selection.

Consumer completion remains qualified by the existing setup/source generation.
The derived machine-key mapping is invalidated by manual reload and renewed
when an existing worker accepts a new file revision; no timer, service or
polling loop is added. A stale/error refresh is not installed as an empty valid
map. Tests must prove the real consumer invalidation/completion path, not just
the lower file reader.

### Exact supplier API proposed for separate admission

Owner: `worktree_manager.harness_state`, which already owns repository/project
registration interpretation. Proposed additive API:

```python
@dataclass(frozen=True)
class ProjectAnchorSelection:
    anchor: str | None
    dependency_paths: tuple[Path, ...]

def project_anchor_selection(
    name: str,
    home_dir: Path | None = None,
    *,
    input_roots: ProfileInputRoots | None = None,
    reader=None,
) -> ProjectAnchorSelection: ...
```

`reader`, when provided, is the file-snapshot reader; it does not supply selection
policy or alternate registrations. The default uses the owner's existing YAML
reader. The owner computes the two registry paths using its existing root
rules and returns both as dependencies, including an absent registry. A missing
selected project yields `anchor=None`, not a discovery scan.

Extract two pure selection helpers inside the owner:

1. The existing `build_repos` path rule: selected `_platform_key()` entry, then
   `windows`, `linux`, `wsl` fallback in that exact order.
2. The existing `build_projects` rule: a nonempty resolved repository path wins;
   otherwise use the selected project's string `anchor` override. A repository
   entry alone does not create a project absent from `projects.yaml`.

Make the existing builders reuse these helpers, then have the new API apply
them to the **selected entries only**. The API reads at most `repos.yaml` and
`projects.yaml`; it must not call `build_projects`, `build_repos`, `pr_model`,
`repo_enabled_plugins`, `project_roster`, Git or any unrelated project's config.
Keep ordinary callers' output and precedence unchanged. Do not replace this
rule with `_exact_platform_key`, another platform's invented fallback, or a
Manager-local copy of registry interpretation.

The admitted `project_config.machines_yaml_path` consumer obtains the supplier
name from the accepted project-config snapshot, not a guessed default home.
It calls the proposed owner API, then reads only that supplier's actual
canonical/legacy roster candidates. The dependency graph includes the project
config, both registries and both supplier candidates. A changed supplier name
or registry mapping replaces that dependency graph in the same background pass.
Absent higher-precedence candidates remain tracked.

**Now admitted, after this amendment's review:** the `harness_state` edits above.
Until actual production-path tests pass, #7940 remains open. Do not hide the present
`build_projects` fallback behind a TTL or mock it into a constant and call the
supplier lane qualified.

The registry-root/path calculation stays owner-owned and is reused rather than
copied into the cache. Existing `build_repos`/`build_projects` outputs, sorting,
fallback precedence and existing call behavior remain unchanged. Tests compare
the selected result with actual builders over real synthetic registry files
while separately forbidding builder/provider/Git/plugin/roster calls in the
new selected-entry reader.

### Locality not-ready contract

The source's ordinary public results remain `True` for established local and
`False` for established remote. A not-ready file snapshot propagates an explicit
`RuntimeError`-compatible refusal, not `False` or an unmarked empty result.
There is no new generic three-state action engine or registry authority.

The immediate `_resume_decision` catches that refusal and returns an internal
decision dictionary with a persistent deferred/refusal marker and reason.
`_decide` inspects the marker before assigning `app.result` or calling
`app.exit`, shows the reason and returns. This marker must survive an immediate
Restore caller replacing `decision["action"]`. Known local/remote decisions,
modifiers and ordinary successful exit remain unchanged. Positively established
locality may only be retained while its identity/source generation is valid;
otherwise the operation waits for an existing worker refresh or manual retry.

`resume_prompt_actions` already forwards the decision through `_decide`.
`headed_actions.open_worktree_cli_headed` does not: it reads options and builds
a `LaunchRequest` directly. A one-branch guard there must forward a deferred
decision to `_decide` and return before `_run_bg` or request construction.
That guard is proposed, not silently included in the two-method admission.
Do not publish a locality fix that leaves this bypass able to launch.

Two immediate internal-action paths also require explicit narrow admission.
`engine_worktree_actions._resume_embodied_worktree_cli` must honor refusal in
both its loaded-row and synthetic-row paths: no result mutation or successful
return after `_decide` refuses, with or without an explicit project. Its
synthetic path must not bypass the common not-ready handling.
`_embody_task_cli` has a direct source-locality check before dispatch; it must
return a visible failure without starting the embodiment worker when locality
is unavailable. These are refusal guards, not a new action framework.

Tests must run the real invalidated configured-alias case and immediate
`_resume_decision`/`_decide` behavior, asserting no SSH, exit, launch request or
worker launch for unknown locality. Also exercise Restore's label replacement,
valid local/remote cases, headed launch, loaded/synthetic internal resume with
and without project, and pre-embodiment dispatch once their guards are admitted.

### Template cell: grounded non-applicability

The actual current owner `machine_transport.registry.parse_machines_yaml_file`
opens one YAML file, calls `yaml.safe_load`, validates the `machines` mapping
and delegates to `parse_machines_yaml`; it has no external include/template
expansion. The Manager project reader likewise uses `yaml.safe_load`. YAML
anchors within a file are covered by that file's content revision. Thus an
external-template cell is not applicable to these concrete readers, not a
passing synthetic-template test. If a real additional supplier expands external
templates, stop and request that provider's dependency contract before claiming
coverage.

### Additional validation and completion gates

- [ ] Fake clock at 59.999 and 60 seconds; `ttl=0`, nested scopes and expiry
  midway through a logical pass.
- [ ] Same-mtime/same-size bytes changed; unchanged bytes do not reparse; warm
  path counters show zero reads/stats/subprocesses.
- [ ] One in-flight reader per key/generation with concurrent consumers,
  success/error sharing, repair retry and cancellation releasing every waiter.
- [ ] Manual clear and bootstrap rebind during blocked I/O; old completions
  cannot enter current state; strong binding identity survives old-scope life.
- [ ] Bounded retained entries and obsolete scopes; no id-reuse key collisions,
  generation chains or cross-project/path contamination.
- [ ] Missing/deleted/preferred-file creation, invalid YAML and permission errors
  retain honest missing/error state and compatible exception boundaries.
- [ ] Real supplier switch, registry change and supplier-only content replacement
  use the owner API and read no unrelated project; prove parity against existing
  owner precedence with real synthetic registry files, not a constant supplier.
- [ ] Derived mapping updates through actual admitted consumer hooks; stale
  source/setup completions are rejected and unrelated selection remains intact.
- [ ] Template non-applicability cites the actual readers above; any newly found
  expanding provider requires its real dependency contract.
- [ ] Reviewed source plan, focused tests, required CI and actual source merges;
  original failure/command evidence retained before cleanup. Native timing and
  installed rollout remain separate, unperformed gates.
