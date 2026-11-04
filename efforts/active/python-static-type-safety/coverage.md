# Strict enforcement coverage

Canonical objective and completion gate: [README](README.md).
The [project manifest](../../../tools/typecheck-projects.json) owns scope,
exclusions, source views and admission policy; this tracker records progress,
not a second checker configuration.

## Snapshot and counting rules

Snapshot: **2026-11-03**, merged Dev policy
`628738bf021f2bcbadd8e1e06bd940a398843997`, reconciled with
`python tools/typecheck.py --inventory --json`.

- **38 / 68 checker projects (55.9%)** have merged strict admission.
- **351 / 7,816 physical Python files (4.5%)** belong to those strict scopes.
- **30 projects remain pending; none are basic.**
- All 7,816 tracked physical Python paths are classified. Classification,
  annotations, passing consumer tests and a prepared implementation do not
  count as strict enforcement.
- Context Handoff's six files and Dispatch service's six files are included
  after #10192 and #10189 merged with their reviewed exact-head qualification
  and required CI. The new Dispatch-plugin and repository-tool files remain
  pending under their own rows rather than being credited by these admissions.
- Devcontainer's three helpers/tests are included after #10193 merged with
  Python 3.11 qualification matching the existing wrapper minimum.
- Harness Knowledge's nine helpers/tests are included after #10201 merged with
  both analysis targets and native Python 3.10 copied-helper qualification.
- Credential Relay's complete 22-file canonical scope is included after #10310
  merged with both analysis targets, Python 3.10 native/copy qualification and
  fresh exact-input source CI. Its consumer projects remain independently pending.
- The refreshed inventory includes two additional files under the already
  strict Bootstrap Killswitch scope. Other additions remain in their pending
  owners rather than being attributed to Credential Relay.
- Index service's complete 27-file scope is included after #10327 merged with
  both targets, Python 3.10 native/copy qualification and fresh exact-input
  source CI. The separately owned 400-file Index plugin remains pending.
  The 26 newly inventoried files in Dispatch, Index, Worktrees and Worktree
  Manager also remain pending under their respective owners.
- PR service's complete nine-file scope is included after #10332 merged with
  both targets, actual Python 3.10 native/installed-copy qualification and
  fresh exact-input source CI. The latest refresh adds ten pending files:
  five in Dispatch and five in repository tools.

Project percentages are not code-volume or runtime-test percentages. Small
shared-library projects and large plugins have very different sizes. Physical
copies count under their own owners rather than disappearing through
deduplication. Split source/remaining projects count only their separately
owned files; the manifest remains authoritative for their exact boundaries.

| Area | Checker projects | Merged strict projects | Physical Python files | Files in merged strict scopes |
|------|-----------------:|-----------------------:|----------------------:|------------------------------:|
| Shared libraries | 37 | 30 | 726 | 282 |
| Plugins | 20 | 3 | 5,467 | 20 |
| Services and managers | 7 | 3 | 946 | 42 |
| Tools and support | 4 | 2 | 677 | 7 |
| **Total** | **68** | **38** | **7,816** | **351** |

**Locked** means the complete declared project scope is admitted to strict
MyPy on both declared analysis targets through a merged, reviewed change and
required CI. It does not claim installed rollout, arbitrary runtime soundness
or qualification of another project's copies/callers. Reviewed dynamic and
third-party boundaries remain permitted by the effort's terminal policy.
Admission decisions and validation details are retained in the
[canonical journal](README.md#journal).

## Shared libraries

| Project | Python files | Merged gate | Admission record or remaining work |
|---------|-------------:|-------------|------------------------------------|
| `lib-agent-procutil` | 141 | Pending | Production, native API and fixture cohorts; preserve existing SDK owners |
| `lib-bootstrap-killswitch` | 5 | **Locked** | #9964 |
| `lib-config-migrate` | 6 | **Locked** | #9924; declared YAML/stub environment |
| `lib-configuration-layers` | 6 | **Locked** | #10021; deployed-resolution follow-up #10132 |
| `lib-core-delegation` | 5 | **Locked** | #10025 |
| `lib-credential-relay` | 22 | **Locked** | #10222 / merged #10310; both targets, Python 3.10 native/copy qualification and exact-input CI |
| `lib-docker-resource-producer` | 11 | **Locked** | #10045; canonical Windows/Linux fixtures |
| `lib-dropin-registry` | 6 | **Locked** | #9930; consumer copies remain independently owned |
| `lib-endpoint-rendezvous` | 5 | **Locked** | #9933 |
| `lib-fleet-contracts` | 6 | **Locked** | #10026 |
| `lib-fleet-index-adapter` | 4 | **Locked** | #10036 |
| `lib-fleet-vault-adapter` | 4 | **Locked** | #10036 |
| `lib-installation-context` | 51 | **Locked** | #10018; faithful source view #9916 and consumer delivery #10048 |
| `lib-installer-engine` | 28 | Pending | Fragment composition, helper/template and caller qualification |
| `lib-installer-readiness` | 10 | **Locked** | #10044 |
| `lib-lazy-cli-dispatch` | 3 | **Locked** | #9951 |
| `lib-machine-transport` | 19 | **Locked** | #10107; five consumer contracts and native qualification |
| `lib-payload-invocation` | 31 | Pending | Production and fixture cohorts; emitted templates/copies also need qualification |
| `lib-peer-launch` | 3 | **Locked** | #9967 |
| `lib-plugin-activation` | 21 | **Locked** | #10108 / merged #10150; both targets, native fixtures and nine consumer contracts |
| `lib-plugin-resolve` | 23 | **Locked** | #10141 / merged #10165; both targets, native fixtures and ten consumer contracts |
| `lib-process-telemetry` | 17 | **Locked** | #10083; explicit optional-SDK checker environment |
| `lib-remote-login-shell` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-remote-login-shell-remaining` | 1 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-repository-policy` | 6 | **Locked** | #10029; executable guest reader included |
| `lib-session-liveness-probe` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-session-liveness-probe-remaining` | 2 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-single-instance-lease` | 14 | **Locked** | #10084; canonical/native and copy correspondence checks |
| `lib-ssh-manager` | 52 | Pending | Transport/native boundaries and complete fixtures |
| `lib-update-driver` | 99 | Pending | Driver, installer/bootstrap and test cohorts |
| `lib-update-telemetry` | 8 | **Locked** | #10043; owning consumer tests remain separately pending |
| `lib-venue-copilot` | 12 | **Locked** | #10166 / merged #10170; both targets, native Windows/Linux and three consumer contracts |
| `lib-versioned-runtime` | 22 | Pending | #10331; preserve behavior with persistent 3.10/3.11 qualification, native/copy and fixture gates |
| `lib-work-coalescing-singleton` | 8 | **Locked** | #10039 |
| `lib-worktree-identity` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-worktree-identity-remaining` | 1 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-zdd` | 71 | Pending | Lifecycle, native/process boundaries and complete fixtures |

## Plugins

Each row includes all Python owned by that plugin's declared project: source,
tests, installers/hooks and independently owned shipped copies where present.
Passing a shared-library consumer regression does not lock the plugin.
The remaining marketplace entries, `agent-remote-driver`, `visions` and
`wsl-setup`, have no tracked physical `.py` files in this snapshot. They are
explicitly outside the physical-file/project metrics, not omitted pending
Python projects. Any embedded/generated Python still belongs to the
cross-cutting qualification gate below.

| Project | Python files | Merged gate | Remaining work |
|---------|-------------:|-------------|----------------|
| `agent-bridge` | 879 | Pending | Component-sized source, native boundary, fixture and copy cohorts |
| `agent-codespaces` | 279 | Pending | Runtime, venue/install helpers, fixtures and copies |
| `agent-conduct-guidance` | 36 | Pending | Scripts, tests and owned copies |
| `agent-containers` | 195 | Pending | Runtime, providers/install helpers, fixtures and copies |
| `agent-dispatch` | 825 | Pending | Registry/worker/runtime subsystems, fixtures and copies |
| `agent-index` | 400 | Pending | Client/server, scripts, fixtures and copies |
| `agent-logger` | 240 | Pending | Runtime, native installer fixtures and copies |
| `agent-machines` | 173 | Pending | Configuration/requirement engine, scripts, fixtures and copies |
| `agent-mcp` | 220 | Pending | Adapter/auth/runtime contracts, scripts, fixtures and copies |
| `agent-pull-requests` | 210 | Pending | Provider/runtime contracts, scripts, fixtures and copies |
| `agent-ssh` | 159 | Pending | Transport/configuration, native helpers, fixtures and copies |
| `agent-vault` | 167 | Pending | Secret/runtime boundaries, native fixtures and copies |
| `agent-worktrees` | 1,431 | Pending | Model/lifecycle/PR/launch subsystems, fixtures, installers and copies |
| `ai-attribution` | 5 | **Locked** | #10171 / merged #10176; both targets, native policy/hook controls and Python 3.9 helper compatibility |
| `budget-guidance` | 87 | Pending | Reader/configuration/runtime contracts, fixtures and copies |
| `context-handoff` | 6 | **Locked** | #10179 / merged #10192; both targets, native Python/Node controls and Python 3.10 helper qualification |
| `copilot-extensions-harness` | 17 | Pending | Complete Python helper/test admission |
| `customizing-copilot` | 95 | Pending | Authoring/review/projection scripts, fixtures and shipped copies |
| `efforts` | 34 | Pending | Policy/capture/lifecycle helpers and tests |
| `harness-knowledge` | 9 | **Locked** | #10185 / merged #10201; both targets, native Linux and Python 3.10 copied-helper qualification |

## Services and managers

| Project | Python files | Merged gate | Remaining work |
|---------|-------------:|-------------|----------------|
| `fleet-manager` | 217 | Pending | Model/control/runtime subsystems, native boundaries and tests |
| `service-chat` | 29 | Pending | Service/storage/admission boundaries and tests |
| `service-dispatch` | 6 | **Locked** | #10184 / merged #10189; both targets, complete unchanged source and native fixture qualification |
| `service-index` | 27 | **Locked** | #10206 / merged #10327; both targets, Python 3.10 native/copy qualification and exact-input CI |
| `service-pr` | 9 | **Locked** | #10204 / merged #10332; both targets, Python 3.10 native/installed-copy qualification and exact-input CI |
| `service-ssh` | 26 | Pending | Transport/service/native boundaries and tests |
| `worktree-manager` | 632 | Pending | Model/picker/control/entrypoint subsystems and fixtures |

## Tools and support

| Project | Python files | Merged gate | Admission record or remaining work |
|---------|-------------:|-------------|------------------------------------|
| `checker` | 4 | **Locked** | #9831/#9853; typed front door, source views, cache paths and checker tests |
| `devcontainer` | 3 | **Locked** | #10183 / merged #10193; both Python 3.11 targets, native Linux contracts and complete source/fixture correspondence |
| `effort-helpers` | 5 | Pending | Every tracked effort-hosted executable helper |
| `repository-tools` | 665 | Pending | Repository/CI/release/installer tools and fixtures, excluding checker-owned files |

## Cross-cutting lockdown

| Gate | State | Evidence or completion condition |
|------|-------|----------------------------------|
| Physical Python ownership/classification | Established | Current inventory accounts for every tracked physical `.py` path; unknown paths must still fail |
| Required source type gate | Established for admitted scopes | #9853; actual type-regression rejection recorded in the canonical effort |
| Required exact-candidate type gate | Established for admitted scopes | #9862 and the reviewed Main adoption; actual candidate regression rejection recorded |
| All fragments/templates/generated copies qualified | Open | Complete source-map/materialization correspondence and negative drift controls |
| Every plugin, service and tool strictly admitted | Open | All project rows locked; no pending/basic scope or omitted fixture family |
| Final full-portfolio local/CI/release parity | Open | All declared targets, final negative controls and actual full-cohort source/candidate gates pass together |

## Updating after each lockdown

1. After a reviewed admission actually merges, refresh the inventory with the
   existing `--inventory --json` command and inspect the merged project policy.
   Record the merged source snapshot, issue/PR evidence and relevant
   qualification in this tracker and the canonical journal.
2. Reconcile every row against the manifest, including new/split/renamed
   projects. Recompute area totals and physical-file totals from inventory
   ownership; never infer coverage from annotations or a selected test run.
3. Keep prepared work pending until its admission has merged and required CI
   and validation are resolved. Canonical-library locks do not advance a
   consumer/copy row without that row's own complete qualification.
4. Keep the effort index as a pointer to this tracker, not a second stale
   numeric scoreboard. Batch tracker/journal publication with the next
   substantive slice, preserving the repository's PR/review gate.
5. At the final gate, reconcile all rows and cross-cutting conditions again.
   Close #9807 and archive the effort only after its Plan and Validation Plan
   are completely resolved.
