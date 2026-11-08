# Strict enforcement coverage

Canonical objective and completion gate: [README](README.md).
The [project manifest](../../../tools/typecheck-projects.json) owns scope,
exclusions, source views and admission policy; this tracker records progress,
not a second checker configuration.

## Snapshot and counting rules

Snapshot: **2026-11-08 (UTC)**, merged Dev policy
`b2b5212289db36992028545841cd296d303dc574`, reconciled with the existing
`tools/typecheck.py` inventory/ownership functions over that pinned Git tree.
The checker and project-manifest bytes match the source used for the count;
unmerged #11265 inputs are not included.

- **41 / 73 checker projects (56.2%)** have merged strict admission.
- **413 / 8,681 physical Python files (4.8%)** belong to those strict scopes.
- **32 projects remain pending; none are basic.**
- All 8,681 tracked physical Python paths are classified; none are unclassified.
  Classification,
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
  source CI. The separately owned Index plugin remains pending.
  The 26 newly inventoried files in Dispatch, Index, Worktrees and Worktree
  Manager also remain pending under their respective owners.
- PR service's complete nine-file scope is included after #10332 merged with
  both targets, actual Python 3.10 native/installed-copy qualification and
  fresh exact-input source CI. A prior refresh added ten pending files:
  five in Dispatch and five in repository tools.
- SSH service's complete 26-file scope is included after #10348 merged with
  both targets, actual Python 3.10 Linux/Windows native qualification and
  fresh exact-input source CI. Its plugin and consumer copies remain pending.
- All five retained effort-helper files are included after #10360 merged with
  both Python 3.11 analysis targets, actual Linux/Windows native controls,
  composed-source qualification and fresh exact-input source CI. Two additional
  Codespaces-plugin files remain pending rather than increasing this admission.
- Payload Invocation preparation #11165 adds two physical files under the
  already strict checker owner: the real catalog SourceView and its unit
  controls. Neither pending Payload Invocation project is admitted.
  Five other additions under existing strict Credential Relay, Plugin Resolve,
  Process Telemetry and Harness Knowledge owners are upstream work, not
  credited to that preparation.

The shared real-import-root/additional-version checker prerequisite
(#10400 / merged #10553) is delivered, independently of the pending Versioned
Runtime (#10331), Copilot Extensions Harness (#10362) and Payload Invocation
(#10371) admissions. The follow-on checker-only guard-alias prerequisite
(#10638 / merged #10866) is delivered, but full-composed-consumer-qualification
remains unmet under #9807 / #10362. The emitted-program binding-completeness
follow-up (#10938) is also delivered without admitting its pending consumer.
Compared with the historical 400/8,424-file, 40/71-project snapshot, this inventory
adds 142 physical files: nine under existing strict owners and 133 pending.
The source/test split adds the pending `lib-payload-invocation-tests` project
without changing its combined 31 physical files; `agent-roles` adds another
pending project. No project level changes in this reconciliation.
The pending `service-vendored-libraries` project has no tracked
physical files: all 73 declared projects count in the project metric, while 72
currently own physical paths. No new strict project admission or credit from prepared
consumer work is inferred. Since the preceding 407/8,503-file snapshot,
63 physical files have arrived: two under the already strict AI Attribution
owner and 61 under pending owners. That snapshot's pending file total was 8,157. Earlier
snapshots remain historical journal evidence.

Agent Roles's three-file Python contract-test project is strictly admitted by
#11485, merged as `0491092e`. Both declared analysis platforms and all 61 original
tests pass; the normal checker rejects an actual incompatible assignment.
This is static test admission, not live reviewer, triage or fleet behavior.
Since the preceding 409/8,566-file snapshot, 115 physical files have arrived.
The four-file strict increase comprises these three newly admitted files and
one upstream addition under the already strict AI Attribution owner. No other
project changes admission level. The current pending file total is 8,268.

Project percentages are not code-volume or runtime-test percentages. Small
shared-library projects and large plugins have very different sizes. Physical
copies count under their own owners rather than disappearing through
deduplication. Split source/remaining projects count only their separately
owned files; the manifest remains authoritative for their exact boundaries.

| Area | Checker projects | Merged strict projects | Physical Python files | Files in merged strict scopes |
|------|-----------------:|-----------------------:|----------------------:|------------------------------:|
| Shared libraries | 39 | 30 | 776 | 293 |
| Plugins | 22 | 4 | 6,118 | 28 |
| Services and managers | 8 | 4 | 1,062 | 78 |
| Tools and support | 4 | 3 | 725 | 14 |
| **Total** | **73** | **41** | **8,681** | **413** |

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
| `lib-agent-procutil` | 143 | Pending | Production, native API and fixture cohorts; preserve existing SDK owners |
| `lib-bootstrap-killswitch` | 5 | **Locked** | #9964 |
| `lib-config-migrate` | 6 | **Locked** | #9924; declared YAML/stub environment |
| `lib-configuration-layers` | 6 | **Locked** | #10021; deployed-resolution follow-up #10132 |
| `lib-core-delegation` | 5 | **Locked** | #10025 |
| `lib-copilot-driver-core` | 27 | Pending | Complete source, fixtures and consumer/copy qualification |
| `lib-credential-relay` | 24 | **Locked** | #10222 / merged #10310; both targets, Python 3.10 native/copy qualification and exact-input CI |
| `lib-docker-resource-producer` | 11 | **Locked** | #10045; canonical Windows/Linux fixtures |
| `lib-dropin-registry` | 6 | **Locked** | #9930; consumer copies remain independently owned |
| `lib-endpoint-rendezvous` | 5 | **Locked** | #9933 |
| `lib-fleet-contracts` | 6 | **Locked** | #10026 |
| `lib-fleet-index-adapter` | 4 | **Locked** | #10036 |
| `lib-fleet-vault-adapter` | 4 | **Locked** | #10036 |
| `lib-installation-context` | 51 | **Locked** | #10018; faithful source view #9916 and consumer delivery #10048 |
| `lib-installer-engine` | 28 | Pending | #10931; local host-options preparation is not merged admission; fragment/native/copy and caller qualification remain |
| `lib-installer-readiness` | 10 | **Locked** | #10044 |
| `lib-lazy-cli-dispatch` | 3 | **Locked** | #9951 |
| `lib-machine-transport` | 20 | **Locked** | #10107; five consumer contracts and native qualification |
| `lib-payload-invocation` | 4 | Pending | #10371; source/catalog/copy preparation merged #11165, not strict admission; complete executed/emitted and consumer gates remain |
| `lib-payload-invocation-tests` | 27 | Pending | #10371; actual Python 3.11 tooling scope; complete executed-program, package/native and source-context qualification remains |
| `lib-peer-launch` | 3 | **Locked** | #9967 |
| `lib-plugin-activation` | 23 | **Locked** | #10108 / merged #10150; both targets, native fixtures and nine consumer contracts |
| `lib-plugin-resolve` | 25 | **Locked** | #10141 / merged #10165; both targets, native fixtures and ten consumer contracts |
| `lib-process-telemetry` | 18 | **Locked** | #10083; explicit optional-SDK checker environment |
| `lib-remote-login-shell` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-remote-login-shell-remaining` | 1 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-repository-policy` | 8 | **Locked** | #10029; executable guest reader included |
| `lib-session-liveness-probe` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-session-liveness-probe-remaining` | 2 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-single-instance-lease` | 14 | **Locked** | #10084; canonical/native and copy correspondence checks |
| `lib-ssh-manager` | 52 | Pending | Transport/native boundaries and complete fixtures |
| `lib-update-driver` | 108 | Pending | Driver, installer/bootstrap and test cohorts |
| `lib-update-telemetry` | 8 | **Locked** | #10043; owning consumer tests remain separately pending |
| `lib-venue-copilot` | 13 | **Locked** | #10166 / merged #10170; both targets, native Windows/Linux and three consumer contracts |
| `lib-versioned-runtime` | 23 | Pending | #10331; preserve behavior with persistent 3.10/3.11 qualification, native/copy and fixture gates |
| `lib-work-coalescing-singleton` | 8 | **Locked** | #10039 |
| `lib-worktree-identity` | 1 | **Locked** | Foundation source admission; canonical journal |
| `lib-worktree-identity-remaining` | 1 | **Locked** | Leaf helper/test completion; canonical journal |
| `lib-zdd` | 71 | Pending | Lifecycle, native/process boundaries and complete fixtures |

## Plugins

Each row includes all Python owned by that plugin's declared project: source,
tests, installers/hooks and independently owned shipped copies where present.
Passing a shared-library consumer regression does not lock the plugin.
The remaining marketplace entries, `visions` and
`wsl-setup`, have no tracked physical `.py` files in this snapshot. They are
explicitly outside the physical-file/project metrics, not omitted pending
Python projects. Any embedded/generated Python still belongs to the
cross-cutting qualification gate below.

| Project | Python files | Merged gate | Remaining work |
|---------|-------------:|-------------|----------------|
| `agent-bridge` | 929 | Pending | Component-sized source, native boundary, fixture and copy cohorts |
| `agent-codespaces` | 303 | Pending | Runtime, venue/install helpers, fixtures and copies |
| `agent-conduct-guidance` | 37 | Pending | Scripts, tests and owned copies |
| `agent-containers` | 197 | Pending | Runtime, providers/install helpers, fixtures and copies |
| `agent-dispatch` | 874 | Pending | Registry/worker/runtime subsystems, fixtures and copies |
| `agent-index` | 402 | Pending | Client/server, scripts, fixtures and copies |
| `agent-logger` | 265 | Pending | Runtime, native installer fixtures and copies |
| `agent-machines` | 202 | Pending | Configuration/requirement engine, scripts, fixtures and copies |
| `agent-mcp` | 234 | Pending | Adapter/auth/runtime contracts, scripts, fixtures and copies |
| `agent-pull-requests` | 252 | Pending | Provider/runtime contracts, scripts, fixtures and copies |
| `agent-remote-driver` | 12 | Pending | Runtime, fixtures and owned copies |
| `agent-roles` | 3 | **Locked** | #11473 / merged #11485; both 3.10 analysis targets, unchanged 61-case contract suite and real type-regression rejection; no live-role qualification |
| `agent-ssh` | 163 | Pending | Transport/configuration, native helpers, fixtures and copies |
| `agent-vault` | 178 | Pending | Secret/runtime boundaries, native fixtures and copies |
| `agent-worktrees` | 1,797 | Pending | Model/lifecycle/PR/launch subsystems, fixtures, installers and copies |
| `ai-attribution` | 8 | **Locked** | #10171 / merged #10176; both targets, native policy/hook controls and Python 3.9 helper compatibility |
| `budget-guidance` | 87 | Pending | Reader/configuration/runtime contracts, fixtures and copies |
| `context-handoff` | 6 | **Locked** | #10179 / merged #10192; both targets, native Python/Node controls and Python 3.10 helper qualification |
| `copilot-extensions-harness` | 17 | Pending | #10362; #10938 is a checker visibility/binding prerequisite only; full composed, native, emitted-copy and promotion qualification remain |
| `customizing-copilot` | 98 | Pending | Authoring/review/projection scripts, fixtures and shipped copies |
| `efforts` | 43 | Pending | #10955; prepared source/fragment qualification is not merged admission; reconcile newer files and complete provider/historical/platform/native gates |
| `harness-knowledge` | 11 | **Locked** | #10185 / merged #10201; both targets, native Linux and Python 3.10 copied-helper qualification |

## Services and managers

| Project | Python files | Merged gate | Remaining work |
|---------|-------------:|-------------|----------------|
| `fleet-manager` | 239 | Pending | Model/control/runtime subsystems, native boundaries and tests |
| `service-chat` | 29 | Pending | Service/storage/admission boundaries and tests |
| `service-dispatch` | 7 | **Locked** | #10184 / merged #10189; both targets, complete unchanged source and native fixture qualification |
| `service-index` | 27 | **Locked** | #10206 / merged #10327; both targets, Python 3.10 native/copy qualification and exact-input CI |
| `service-pr` | 9 | **Locked** | #10204 / merged #10332; both targets, Python 3.10 native/installed-copy qualification and exact-input CI |
| `service-ssh` | 35 | **Locked** | #10211 / merged #10348; both targets, Python 3.10 Linux/Windows native qualification and exact-input CI |
| `service-vendored-libraries` | 0 | Pending | Explicit copied-library owner; no tracked physical files yet, no admission or copy qualification |
| `worktree-manager` | 716 | Pending | Model/picker/control/entrypoint subsystems and fixtures |

## Tools and support

| Project | Python files | Merged gate | Admission record or remaining work |
|---------|-------------:|-------------|------------------------------------|
| `checker` | 6 | **Locked** | #9831/#9853; #11165 adds catalog SourceView/unit controls under the existing strict owner, not library admission |
| `devcontainer` | 3 | **Locked** | #10183 / merged #10193; both Python 3.11 targets, native Linux contracts and complete source/fixture correspondence |
| `effort-helpers` | 5 | **Locked** | #10215 / merged #10360; both targets, Python 3.11 native controls and current source/composed qualification |
| `repository-tools` | 711 | Pending | Repository/CI/release/installer tools and fixtures, excluding checker-owned files |

## Cross-cutting lockdown

| Gate | State | Evidence or completion condition |
|------|-------|----------------------------------|
| Physical Python ownership/classification | Established | Current inventory accounts for every tracked physical `.py` path; unknown paths must still fail |
| Required source type gate | Established for admitted scopes | #9853; actual type-regression rejection recorded in the canonical effort |
| Required exact-candidate type gate | Established for admitted scopes | #9862 and the reviewed Main adoption; actual candidate regression rejection recorded |
| Shared import-root/additional-version prerequisite | Delivered, not consumer admission | #10400 / #10553; four exact-head source/frozen cases and 28 actual MyPy analyses qualify the version/TOML controls on both analysis platforms; consumer aliases, emitted copies and native/runtime gates remain independent |
| Checker-only faithful guard alias | Delivered, not consumer admission | #10638 / #10866; hosted checker and CI-bound packet pass; actual merged-input drift remains explicitly distinguished |
| Emitted-program visibility and binding completeness | Delivered, not consumer admission | #10938; four analysis cells and hosted 4,226-unit CI-bound packet; three actual merged-input differences, no whole-merge equivalence or native/copy claim |
| Payload Invocation typing preparation | Delivered, not library/consumer admission | #11165; source/test split, four catalog witnesses and authorized output preparation; complete ordinary Linux 4,603-unit execution includes every head-manifest ID, with unchanged admission/coverage/budget contracts |
| Full composed-consumer qualification | Open | #9807 / #10362; the merged checker prerequisite does not qualify historical full frozen-candidate failures, actual consumer/copy/emitted/native gates or any consumer admission |
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
