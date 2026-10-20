# Pinned Windows residual ledger

**Status:** Complete pinned ownership census, pending review for the entry
gate in [the reviewed wave plan](windows-pr-waves.md). Mixed-hunk and external
owner blocks remain implementation-admission gates. This ledger is
documentation, not an implementation patch or a qualification. No source hunk
is removed, copied into a runtime, or declared passing here.

## Immutable inputs and coverage

| Input | Commit |
|-------|--------|
| Current Dev comparison point | `442be2d536fe9b77fb8336805f2bbe461199d94d` |
| Original published #5694 head | `339dc00123964d84a117afd55197a192895ebac8` |
| Original published comparison base | `936926d76a7066ac6eb5a1b11a83c91716eddf69` |
| Preserved continuation | `8b7b8778b69e6bd1b6a6f2b0018fa6e1f59d961f` |
| Continuation's historical comparison base | `6e2e59ceed9a5cb822bd02906f5d4e1173aeae71` |

The historical-base-to-continuation diff has **177 paths and 578 unified-3
hunks**: Worktrees non-installer paths 70/330, native/install paths 62/128,
tools/Fleet paths 23/88, and metadata 22/32. The original published diff has
179 paths. Its only paths outside that 177-path set are
`.github/workflows/ci.yml` and `.github/workflows/windows-coverage-rotation.yml`;
the tools ledger includes those separately. Matching path sets do not prove
hunk preservation or that a change has landed.

Hunk identities use `git diff --no-ext-diff --unified=3` between the pinned
inputs, without rename inference changing a path's ownership. A short hash
means the first 12 hexadecimal digits of SHA-256 over the hunk text, beginning
with its `@@` header, ending before the next hunk/file, and using LF endings.
Path plus header plus hash identifies a hunk; a matching count alone is not
coverage. Displayed metadata range headers omit Git's optional section suffix;
the hash includes that suffix and the complete body. Added-file hunks include
the entire added file.

| Ledger | Owned source paths | Purpose |
|--------|--------------------|---------|
| [Worktrees](windows-residual-worktrees.md) | `plugins/agent-worktrees/`, excluding `scripts/` | Immutable fixtures, test moves and fresh per-decision Git contracts |
| [Native/install](windows-residual-native.md) | `libs/`, `worktree-manager/`, other `plugins/`, plus Worktrees `scripts/` | Native transport, pane ownership, attribution, installation and generated copies |
| [Tools/Fleet](windows-residual-tools.md) | `tools/`, `fleet-manager/`, and the two published-only workflow paths | Containment, runner, coverage, fixture preparation and policy compatibility |
| This document | 19 `.changefiles/` entries, `TESTING.md`, `docs/install-contract.md`, canonical effort `README.md` | Paragraph-level documentation and historical declaration disposition |

The preserved checkout also has four uncommitted files: `TESTING.md`,
`tools/test_public_toolchain_source.py`, `tools/test_select_windows_rotation.py`
and `tools/test_verify_promotion_workflows.py`. They are **not** part of
commit `8b7b8778b`. Their policy-correctness work remains separate below and in
the tools ledger; none is discarded or silently counted as committed evidence.

### Published metadata reconciliation

All nonblank lines added by the original publication in `TESTING.md`,
`docs/install-contract.md` and the effort `README.md` remain present in the
committed continuation. Comparing original publication to continuation shows
no change in `docs/install-contract.md`; the effort gains 123 journal lines
without deletion. `TESTING.md` gains 111 lines and removes 18 baseline lines:
those upstream policy/task-contract updates do not remove the original PR's
added documentation. Line preservation is a bounded omission check, not
semantic equivalence or landing evidence.

Four changefiles present in the continuation but absent from the original
publication already belong to the continuation's historical base. They are
not part of its 19 candidate declarations below and are not absorbed by this
effort: standalone install/update output, installer capability probes,
read-only current-task ownership, and generated installation-context adoption.

## Metadata disposition rules

Historical changefiles describe the old combined branch, not the next batch.
Each row below is **proposed retirement of that historical declaration from
future extraction**, requiring review of this ledger. It does not delete the
preserved declaration, reverse its implementation, claim its implementation
landed, or authorize retiring the underlying contract. Each retained plugin
batch must generate a new, narrowly scoped changefile after its actual final
diff is known. In particular, the 13-plugin declaration is not copied to a
single installer or fixture PR.

The `CF` number identifies the declaration, not a new implementation batch.
The owning implementation ledger determines that batch's exact source/test
membership. An old declaration spanning several contracts must be split;
its comment is not evidence those contracts were independently validated.

All paths in this table are under `.changefiles/`; each is an added-file
hunk with header `@@ -0,0 +1,9 @@`, except CF06 (`+1,21`) and CF17 (`+1,57`).

| ID | File | Hunk SHA-256/12 | Owning contracts after declaration retirement |
|----|------|----------------|---------------------------------------------|
| CF01 | `20261007234056-reuse-an-immutable-pr-repository-fixture-1daa10.json` | `7f20457d5523` | Wave 2 copied Git fixtures; separately fresh YAML parsing, reconcile payload fixtures and native ADO stream fixtures |
| CF02 | `20261012035721-synchronize-the-callback-burst-regressio-9ef348.json` | `409bae920727` | Native/install ledger: callback registration/readiness synchronization, not a transport deadline change |
| CF03 | `20261012065055-preserve-native-owner-command-stdin-stdo-121e89.json` | `de3749d04927` | Native/install ledger: windowless peer stdin/stdout/stderr/exit transport |
| CF04 | `20261012065056-isolate-launch-policy-tests-in-their-tem-a6fee7.json` | `3b32e76c64f1` | Native/install ledger: isolated launch-policy profiles and outside-profile refusal |
| CF05 | `20261012111056-reuse-observed-owned-service-identity-fo-991571.json` | `beb52443d2d0` | Native/install ledger: Index observed service identity and bounded cleanup diagnostics |
| CF06 | `20261013123523-preserve-private-pytest-parent-permissio-34e4e2.json` | `7600b1a3ea1d` | Wave 1 private parents, Windows accounting and reader cancellation are distinct contracts; fixture/native changes follow their own ledgers |
| CF07 | `20261013163532-bind-native-pane-ownership-without-launc-d5b588.json` | `4f60e18d3514` | Wave 4 native pane kernel ownership, no launch-time compiler |
| CF08 | `20261014030513-batch-fresh-finalization-object-evidence-b40898.json` | `776857fc9061` | Wave 3 finalization evidence; independent Git-only fixture changes stay in Wave 2 |
| CF09 | `20261014073248-share-genuinely-built-immutable-client-w-0eb5a9.json` | `80f5beb4b47c` | Wave 2 Fleet wheel construction and allocation-database isolation, separate batches |
| CF10 | `20261014091315-derive-fresh-publication-authority-from--e55a08.json` | `f48e166d8b23` | Wave 3 fresh transport URL/settings snapshot and mutation refusal |
| CF11 | `20261014100922-prove-absent-committed-config-candidates-91d8de.json` | `58155a48b556` | Wave 3 literal candidate-absence proof and ordered committed-config reads |
| CF12 | `20261014113002-read-committed-hook-configuration-once-w-5dec18.json` | `ce22040ad8a9` | Wave 3 one protected-branch decision's config, fresh again on the next decision |
| CF13 | `20261014132200-batch-fresh-recovery-objects-and-retention-7ad29c.json` | `b76beb26f3ef` | Wave 3 fresh recovery object batch and atomic create-only retention transaction |
| CF14 | `20261014170100-reduce-fresh-publication-evidence-duplication-bc3a19.json` | `a2a52edda3e4` | Wave 3 rewrite destination snapshot and replay tree evidence, independently scoped |
| CF15 | `20261014194526-resolve-source-qualified-windows-command-1702b2.json` | `88e572c2a181` | Wave 4 source-qualified Windows command binding and explicit refusal |
| CF16 | `20261015015931-reuse-fresh-pinned-replay-parent-rows-wi-f406bc.json` | `036ea190ba1c` | Wave 3 replay parent rows within one verification, never across decisions |
| CF17 | `20261015030100-retain-seasoned-editable-setuptools-metadata-7f9c41.json` | `2d30284928c0` | Wave 4 retained editable metadata, snapshot copying, Vault launcher and fresh registry parsing are separate; optional cleanup is already #7304 |
| CF18 | `20261015041000-share-published-snapshot-rebase-inputs-1905.json` | `ffae2f53cb04` | Wave 2 independent published/rebased fixture copies, actual replay still per case |
| CF19 | `20261015054000-preserve-native-attribution-paths-1905.json` | `9b910b7d0633` | Wave 4 native attribution Unicode paths and real isolated Bash guidance |

## Documentation hunks and paragraph boundaries

Line ranges below refer to the **committed continuation**, not its dirty
working copy. Context lines and blank separators accompany their immediately
adjacent owned paragraph; they are not independently executable changes.
Mixed unified hunks are explicitly subdivided below instead of giving one
large hunk permission to join unrelated contracts in a PR. Retain each
paragraph only with the matching implementation or already-landed contract.

### `TESTING.md` - eight committed hunks

| Hunk header | SHA-256/12 | Disposition |
|-------------|------------|-------------|
| `@@ -498,6 +498,65 @@` | `5785e91d82ef` | Split paragraph spans 501-558 below; no whole-hunk extraction |
| `@@ -688,7 +747,13 @@` | `cae848fbbe14` | 750-753: native catalog module-analysis-cache isolation; 754-756: Worktrees local-cache nonregular stat-field refusal |
| `@@ -710,6 +775,17 @@` | `cd57bfd9c095` | 778-787: native installer helper admission, real Bash marker and hashing-failure refusal |
| `@@ -1150,6 +1226,140 @@` | `98e9e70e22ec` | Split paragraph spans 1229-1362 below; obsolete CI/pre-push claims are not copied |
| `@@ -1182,6 +1392,17 @@` | `223381219a45` | 1395-1398: runtime-only installer payload; 1399-1402: native checkout-audit argv; 1403-1404: portable mention-guard interpreter |
| `@@ -1484,6 +1705,122 @@` | `dfe9e639b078` | Split paragraph spans 1708-1822 below; fixture mechanics and native execution remain separate |
| `@@ -1788,6 +2125,10 @@` | `fb6340a7110a` | Wave 5 coverage producer: 2128-2131 changed-since-baseline whole-file provenance |
| `@@ -1818,8 +2159,10 @@` | `d66357162155` | Same Wave 5 strict consumer: 2162-2165 retired nodes admitted only by explicit nonempty whole-file selection |

| Committed added-line span | Owning behavioral contract |
|---------------------------|----------------------------|
| 501-506 | Wave 1 contained PowerShell module-search isolation, with real native commands and explicit host-state opt-in |
| 508-512 | Optional-cleanup test documentation: reconcile with actual merged #7304, not another cleanup implementation |
| 513-514 | Wave 4 Worktrees source-snapshot byte preservation and nested-bytecode exclusion |
| 516-520 | Wave 2 creation-tail copied Git seed and sibling isolation |
| 522-526 (through "authority read.") | Wave 4 machine-transport safe YAML backend parity and fresh topology |
| 526 (from "Local-cache")-528 | Wave 2 local-cache copied Git seed mutation isolation |
| 529-532 | Wave 3 Worktrees config safe-loader parity and subsequent-call freshness |
| 533-537 | Wave 4 reconcile payload canonical support, restoration and deliberate-override preservation |
| 538-543 | Wave 4 native ADO stream fixture framing and bounded cancellation |
| 544-548 | Wave 4 installed-hook Bash/copy-loop and generation-three registration admission; split these two test contracts before publication |
| 549-552 (through "hosts.") | Existing #6909 installer-copy owner; no private copier or overlay is adopted |
| 552 (from "Vault's")-555 | Wave 4 Vault offline PE construction, unchanged 20-second bound |
| 556-558 | Existing #6909 extraction harness dependency, retaining assembly-corruption and publication assertions |
| 1229-1234 | Wave 3 publication URL/settings snapshot, changed-between-call and malformed/read refusal |
| 1236-1251 | Wave 3 rewrite destination, replay trees and same-verification parent rows; separate those production contracts as the Worktrees ledger requires |
| 1253-1257 | Wave 3 committed-config candidate absence, original ordered reads and budgets |
| 1258-1261 | Wave 3 protected-branch hook one-decision read and next-decision freshness |
| 1263-1272 | Wave 3 recovery objects/atomic retention and actual NUL-boundary encoding regression |
| 1274-1278 | Wave 2 Fleet genuine wheel reuse, per-consumer installation and artifact byte identity |
| 1279-1283 | Wave 2 Fleet independent active-allocation database copies and real retirement/races |
| 1285-1291 | Wave 1 fresh bounded Windows storage enumeration, no stale-size cache or extra pytest workers |
| 1293-1298 | Wave 1 owned deferred cleanup, joined completion and inclusive aggregate time |
| 1299-1308 (through "`os.waitid`") | Wave 1 kernel/process-group reap proof and reporting-descriptor isolation |
| 1308 (after "`os.waitid`")-1311 (through "jobs.") | Proposed retirement: obsolete required native macOS CI claims conflict with current unit-only Source CI; retained native tests do not imply an enrolled lane |
| 1311 (from "If reaping")-1313 | Wave 1 failure and sandbox retention when reaping cannot be proved |
| 1315-1318 | Retain traversal parity with Tools/Fleet `G-DOC`, `G-FEED` and `G-PS-SCAN`, but retire the stale "Pre-push still runs every declared guard" wording under policy-documentation review; no headless-guard optimization is asserted without its actual source membership |
| 1320-1338 | Wave 2 persisted-record vs. real Git fixture families; match each paragraph's family to the Worktrees ledger rather than one blanket fixture PR |
| 1339-1346 | Wave 6 diagnostic/normal rotation output and explicit trace distinction; no revival of a disabled CI job |
| 1347-1354 | Wave 4 captured native pane console and actual kernel kill-on-close regression |
| 1355-1356 | Native/install `W1-BRIDGE-DISCOVERY`: stale/fresh report identity and actual task completion, separate from holder cancellation |
| 1357-1359 | Wave 1 Bridge reader ownership through repeated cancellation |
| 1360-1362 | Wave 4 immutable positive Bridge contract availability, no negative/mutable-ref cache |
| 1708-1710 (through "modules.") | Wave 2 mechanical PR test split; require test/parameter/fixture bijection |
| 1710 (from "Their")-1719 | Wave 2 independent run-local Git seed reuse; storage accounting depends on Wave 1 |
| 1720-1735 | Wave 2 finalization/resource-producer fixtures and explicit absent-host-service declarations, retaining actual teardown/races |
| 1736-1740 | Wave 3 finalization fresh object/creation-point evidence and teardown rechecks |
| 1741-1752 | Wave 2 persisted provider records, lease history and credential configuration; each family retains its named real operations |
| 1753-1760 | Wave 2 published fixture copies; first publication and actual rebase/hook rejection remain real |
| 1761-1774 | Wave 2 recovery/settlement independent copies, actual mutation and publication races |
| 1775-1784 | Wave 2 rebased rewrite/legacy fixture copies and renderer source-only inputs |
| 1785-1787 (through "status.") | Wave 4 windowless native peer command transport |
| 1787 (from "Launch-policy")-1789 | Wave 4 isolated launch-policy profile and outside-profile fence |
| 1790-1793 | Wave 4 Index observed owned-service identity, zero exit and explicit stop errors |
| 1794-1796 | Wave 2 terminal-conclusion Git seed with per-case lifecycle state |
| 1798-1802 | Wave 2 three fixture-owned non-repository handoff anchors only; topology and real leases remain fresh |
| 1803-1806 | Wave 2 context-resolution copied topology and independent administrative links |
| 1808-1822 | Wave 6 retained two-worker/native-Git/trace acceptance recipe, gated by current venue availability rather than asserted hosted execution |

### `docs/install-contract.md` - three committed hunks

| Hunk header | SHA-256/12 | Disposition |
|-------------|------------|-------------|
| `@@ -1517,6 +1517,12 @@` | `3c35bd157456` | **Already merged semantically** in #7304, commit `55a93b92cda57031cc6f00d225da62b719f0de51`: current documentation uses the loaded `Function:` provider, forbids `Get-Command` autoload and preserves outer lock/error behavior. Different wording is not a remaining implementation. |
| `@@ -2610,7 +2616,10 @@` | `19a9c42ff6e9` | Wave 4 retained editable setuptools metadata: private `egg_info.egg_base`, preserved existing configuration and live imports |
| `@@ -2715,6 +2724,11 @@` | `441b4c4b6579` | Wave 4 canonical dependency-policy fanout: both synchronizers and both checks, no stale shipped support copies |

### Canonical effort `README.md` - two committed hunks

| Hunk header | SHA-256/12 | Disposition |
|-------------|------------|-------------|
| `@@ -583,6 +583,43 @@` | `f9cddd65d544` | Retain under Wave 6 parent qualification: 240/900 thresholds and historical hosted timings remain historical evidence. The old schedule/hosted-CI repair item must be reconciled with #7269/current venue ownership before execution, not used to restore retired CI. |
| `@@ -708,6 +745,2436 @@` | `5bb0bb6cf395` | Retain as Wave 6 historical journal evidence, with actual per-batch landing entries copied selectively when their facts become relevant. The 2,430-line historical insertion is not wholesale replacement text for today's effort. The #7338 and #7304 merge entries are facts to journal now; old blockers are chronology, not fresh authority. |

The journal hunk stays recoverable at the pinned source commit. This
disposition does not mark any historical plan/validation checkbox done.
The current effort, not a private validation overlay, remains the parent owner.

## Uncommitted documentation overlay

`TESTING.md` has three dirty hunks after the pinned continuation. They correct
historical CI prose: native macOS reaping remains integration source rather
than required CI; retained coverage-selected dispatch is not current unit CI;
and absent/manual jobs are not execution evidence. Retain these under
Wave 5 **policy documentation compatibility**, together with the tools
ledger's #6237/#1303 workflow-fixture owner coordination. Do not apply the
dirty whole file, recreate retired jobs, fabricate Main lineage, or change
integration assertions into eligible units.

| Dirty hunk header | SHA-256/12 | Retained policy-documentation assertion |
|-------------------|------------|---------------------------------------|
| `@@ -1305,14 +1305,13 @@` | `28a86192c782` | Native ownership is not required CI; operational guards are not misdescribed as pre-push behavior |
| `@@ -2109,15 +2108,15 @@` | `97506e4ac7f6` | Historical coverage-selected integration is not current Source unit CI or a fallback lane |
| `@@ -2188,8 +2187,10 @@` | `61cee4d62cee` | Missing/disabled manual integration jobs do not establish runtime proof |

## Entry and completion gates

The integrated read-only reconciliation verifies **177 paths / 578 committed
hunk keys**, each once in its owning ledger. It separately identifies seven
dirty hunks across four preserved files, seven published-only workflow hunks,
and one stale published installer receipt. The original cached-scandir
conversion is explicitly reconciled to actual merged #7119 in the tools
ledger rather than omitted or republished. No blocked-unclassified row remains;
named mixed partitions and existing-owner blocks are not blanket batch admission.

Worktrees' 308-case mechanical-name inventory preserves the 88 changed-body
identities, decorators and fixture deltas separately. It does not substitute
for collection/parameter/fixture parity, including the explicitly consolidated
cases outside that split. Those remain each implementation batch's gates.

The ledger and each relevant batch still require review. A blocked or mixed
row is not admission to publish a catch-all implementation. Before any batch,
expand its exact owned subranges and dependencies into that PR, refresh its
comparison against then-current Dev, and prove its own regression assertions.
Only an actual merged ref removes a retained contract from the residual.

Nothing here changes the original repeated **complete same-head Windows
900-second** gate, including preparation and cleanup, the **240-second**
maximum per group, **exactly two Worktrees workers**, or the existing
30/300/900-second, 128-process, 4096 MiB memory and 2048 MiB storage limits.
No extra skips, exclusions, waivers, reduced portfolio or stale evidence are
admitted. #5694/#5682 and the canonical effort remain open.
