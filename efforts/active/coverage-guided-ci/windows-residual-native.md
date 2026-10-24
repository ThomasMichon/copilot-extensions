# Windows residual census: libraries, native integration and installer scripts

Read-only reconciliation for the reviewed wave plan in
[windows-pr-waves.md](windows-pr-waves.md), introduced by
ThomasMichon/copilot-extensions#7338. This is an inventory, not implementation,
batch publication, retirement approval, or native/performance acceptance.
The coordinator owns integration with the other census reports.

## Immutable inputs and scope

- Continuation: `8b7b8778b69e6bd1b6a6f2b0018fa6e1f59d961f`.
- Continuation base: `6e2e59ceed9a5cb822bd02906f5d4e1173aeae71`.
- Original published head: `339dc00123964d84a117afd55197a192895ebac8`.
- Published merge base: `936926d76a7066ac6eb5a1b11a83c91716eddf69`.
- Current Dev comparison: `442be2d536fe9b77fb8336805f2bbe461199d94d`.
- Scope: changed `libs/`, `worktree-manager/`, and `plugins/` paths, excluding
  `plugins/agent-worktrees/` except its `scripts/` subtree. Tools/Fleet and
  Worktrees non-script work have separate owners.
- Exactly **62 paths / 128 unified-3 hunks** in the continuation census below.
  A key is the path, full hunk header, and first 12 hexadecimal SHA-256 digits.
  Hash input starts at `@@`, includes all context/addition/deletion lines and
  each terminal LF, and stops before the next hunk or file header. CRLF is
  normalized to LF. Identical copied hunks intentionally share hashes; path
  remains part of their key.

At census creation, the source operating policy and complete wave plan were read.
No source
files, Git state, installed payloads, or dependencies were changed; no tests,
builds, native launches, or network operations were performed for this census.
Evidence is model-neutral; no model registry or eligibility is inferred.

## Single-disposition vocabulary

Each ledger row has exactly one code. A retained code identifies one narrow
contract, not permission to combine every contract in its wave.
An indivisible census key may identify a mixed source hunk: its one blocked
code then owns an explicit subspan partition, not permission to retain the
whole hunk. Subspan decisions do not add or duplicate census keys.

### Actual merged references

- **M7304 — actual merged:** ThomasMichon/copilot-extensions#7304,
  `55a93b92cda57031cc6f00d225da62b719f0de51`. The inspected commit changes
  optional cleanup discovery to
  `Test-Path -LiteralPath 'Function:\Stop-RuntimeConstruction'`, retaining the
  cleanup call and surrounding finally/lock release. Dev contains that stronger
  spelling in all nine affected installers, including both Index finalizers.
  It is semantically the source hunk's loaded-function probe, not an exact
  textual replay of `Test-Path Function:\...`. Do not republish it.
- **M7283 — actual merged:** ThomasMichon/copilot-extensions#7283,
  `36d30f31bf03db78d5f8be256c6bb7cfe577b03e`. Its inspected Vault patch already
  makes that same LiteralPath cleanup correction; Vault was consequently not
  changed by #7304.
- **M6707 — actual merged:** ThomasMichon/copilot-extensions#6707,
  `7519c4113c84c00b7831d3fc146dab455013097f`. `git log -S` finds the actual
  `Copy-StampDirectory` `__pycache__` exclusion, and pinned Dev blame attributes
  the exact line to this commit. This is not an ancestry-only assertion.

For human-output receipts assigned a merged code, Dev's receipt was parsed and
its `entrypoint_sha256` checked against the Git blob bytes of Dev's installer;
all nine checked receipts match. Path-specific history identifies #7304 or
#7283 as their actual latest generator-output change. Historical checksum
literals are not to be replayed. Receipts for installers with still-blocked
copier changes instead belong to B6909, not an already-merged claim.

### Subsequent actual-merge settlement

At Dev `0ccade9ed65dcd4ff3797df3bfbb5ec5c0281e75`, the following ten
independent contracts have actually merged. Their **62 exact census keys**
now carry the corresponding merged code below, not their former retained
batch label. The linked merged PRs retain their scoped implementation, review,
checks and validation context. This is a semantic contract disposition, not
an assertion that historical patches were replayed byte-for-byte.

| Code | Former batch | Keys | Actual PR and merge commit |
|------|--------------|------|----------------------------|
| M8249 | W4-TRANSPORT | 5 | #8249, `f0f6122a92cc3a4f555a1310c6bee1da1fde7dd0` |
| M8252 | W4-PANE | 3 | #8252, `a8c2a87e788cfe7b41eaa8a5217d32e0ce3c98a5` |
| M8255 | W4-ATTR-ENCODING | 4 | #8255, `505dd427d48d15b73052f487bab7a42a10d5424f` |
| M8258 | W4-WATCH-BURST | 2 | #8258, `77ef5e78ee99b155a1af95d7de17776e146adcfd` |
| M8310 | W4-VAULT-COMPILER | 1 | #8310, `d3965f75c949ff388c7ae117d80315970d0e0b36` |
| M8425 | W4-VAULT-NOSERVICE | 2 | #8425, `cf2cfd14ca9fb9129bfc4dbc15e3c24691d23bdd` |
| M8437 | W4-INDEX-SHUTDOWN | 4 | #8437, `1f711668a1950a0cdbbafc85243d33fd57b116b7` |
| M8444 | W4-BRIDGE-STDIO | 1 | #8444, `0f5b38374525c7dfbc11b47212a7a9905a9b6ec8` |
| M8470 | W4-BIND | 12 | #8470, `abe1c6a7b0ae52807a9d41db5a4efa3a37d0797d` |
| M8487 | W4-DEPENDENCY-METADATA | 28 | #8487, `0ccade9ed65dcd4ff3797df3bfbb5ec5c0281e75` |

The batch descriptions below preserve their original scope, not permission to
republish settled work. In particular, the pane tests moved into the existing
Windows-guarded argv module without adding skips; Index retains real services
and adds explicit shutdown failure controls; Bridge's mocked forwarding
assertion is accompanied by actual isolated native pipe/exit controls.
Vault's compiler and NoService contracts remain separate, with their failed
diagnostics retained rather than converted to timing qualification.
No other retained, blocked or retirement-proposed key changes disposition.
The 22 carried-forward retained keys are custody labels, not newly verified
findings that every associated behavior is absent from current Dev.
The parent remains #5682; the operator-closed #5694 is preserved history,
not an open publication objective.

### Subsequent admission: W4-BIND

M8470 settles the original twelve path/header/hash keys under the binding
README, generator, tests and generated Worktrees PowerShell emitter. The
generator and emitted bootstrap implement the historical single-interpreter,
isolated-helper and stderr-only trace contract. Current tests retain the
stronger initialized-host outcome/snapshot fixture rather than replaying an
older fixture. The user-site launcher forwards the real bootstrap output;
unavailable Python, admitted helper failure and empty response are separate
controls. Failure and empty response forbid fallback discovery after admission.
Environment-level trace removal plus ordinary-emission silence checks realize
the original no-trace control. A changed foreign receipt between real emissions
requires immediate refusal without writes, rather than cached ownership.

The linked PR retains the complete 143-pass/24-existing-skip native consumer
run and the earlier unexplained worker-loss diagnostic. Neither the passing
rerun nor its small before/after launch sample qualifies complete same-head
timing. No other disposition changes, no new skip or budget increase, and no
installer-owner or retirement acceptance is implied.

### Subsequent admission: W4-DEPENDENCY-METADATA

M8487 settles the two historical metadata hunks under each of the canonical
library and thirteen plugin paths: **28 exact path/header/hash keys**, not two
keys total. The current implementation keeps the policy call at the retained
static-setuptools layout rewrite and moves ConfigParser into the existing
configuration companion. Its import and rewrite realize the historical
contract semantically rather than replaying the former run_uv_install hunk.
Both existing generators synchronize the current canonical-plus-15-derived
policy/configuration pairs; the additional current consumers do not add
historical census keys.

The rewrite preserves raw values and case-distinct option names while setting
egg_info.egg_base to the private retained project. Malformed configuration and
read/write errors fail explicitly before installation. The existing real
setuptools regression requires private egg-info, unchanged original files and
an editable .pth targeting the live source. Lightweight controls cover missing
and authored configuration, percent text, unrelated build options, case-distinct
data-file keys and failure cleanup. Case-distinct names are retained without
case folding or duplicate-key rejection. The unchanged module-size
guard also passes through the existing companion, not a raised cap.

Complete native policy, cleanup, compile and sync suites passed 380 tests with
one existing platform skip, exactly two workers and unchanged item/containment
limits, in 226.81 seconds including preparation and cleanup. The existing
six-mode real-UV live-edit/cleanup controls remain intact. This does not settle
the tools ledger's mixed S076 or S087 hunks: their assertion-level partition,
real installed-setuptools consumer obligations and separate layout/immutability/
support dependencies remain with that ledger. No #6909 copier or private
overlay is adopted, and no complete-plugin performance qualification,
retirement approval, rollout or parent completion is implied.

### Blocked existing owner

- **B6909 — blocked by the existing ThomasMichon/copilot-extensions#6909
  installer owner:** canonical `Copy-InstallSnapshotItem`, its Bridge,
  Codespaces, Dispatch and Index callers, directly dependent extracted-function
  fixture wiring, and selected/unselected malformed-slot recovery assertions.
  Do not absorb or publish the owner's copier/overlay work. Its private
  source/materialized comparison is diagnostic only, not landed evidence.
  Four mixed installer receipts must be regenerated from the owner's eventual
  reviewed source rather than copied from this continuation. No alternative or
  competing installer fix is authorized. The owner must reconcile any
  overlap before these hunks can enter a batch.
- **B-CODESPACES-SPLIT — blocked by the canonical effort's integration
  coordinator pending explicit split and retirement review:** the mixed
  `test_unsaved_guard.py` hunk keyed `7c269d084096` contains both retained
  portability subspans and a prohibited added-skip subspan. Its partition is:
  - **Retained W4-CODESPACES-PORTABLE subspans:** import/use `_resolve_bash`
    for host-native Bash selection; frame `workspace_root` and every extra
    directory as POSIX paths; build the audit argv with `shlex.split`; bind
    its executable to the selected native Bash; and use checked subprocess
    execution with the existing timeout and no-window flags.
  - **R-BASH-SKIP — proposed retirement requiring review, not retired:** the
    added `if bash is None:` branch and its
    `pytest.skip("requires a host-native Bash, not a WSL launcher")` call.
    These two added lines create a skip and therefore cannot enter the
    retained batch under the unchanged parent gate. Retire this addition
    only after review; preserve a failing, non-skip prerequisite when native
    Bash is unavailable. Merely excluding the skip from passing evidence
    would not satisfy the gate.
  The original full header/hash remains one blocked census key until the
  coordinator reviews this partition. No source edit or skip waiver is
  authorized by the proposed split.

### Named narrow batch contracts

- **W4-PANE:** compiler-free native pane Job Object binding.
  `worktree-manager/bin/pane-wrapper.ps1`, its `bin/README.md`, and
  `tests/test_pane_wrapper_job.py` stay together. Assertions: actual kernel
  extended-limit ABI is 144/112 bytes, kill-on-close flag is `0x2000`,
  no unintended process/memory limits, no Add-Type compilation, native child
  starts and is reaped when the real wrapper terminates. Keep ownership of the
  job handle and child deadlines. No dependency on the installer copier.
- **W4-BIND:** single-process isolated Python command-binding bootstrap.
  `libs/payload-invocation/generate.py`, its README and command-binding tests
  own all three generated Worktrees `emit-command-catalog.ps1` hunks,
  including its declaration hash. Assert `py -3` reports the interpreter and
  executes only the addressed helper in the same process, unavailable
  interpreter falls back without WindowsApps aliases, helper failure is not
  retried, empty output fails closed, changed installation ownership is
  re-read/refused, and boot tracing is stderr-only and opt-in. Regenerate
  the script/hash from the generator; never patch the generated script alone.
- **W4-TRANSPORT:** fresh safe YAML parsing with the available safe backend.
  `libs/machine-transport/README.md`, `registry.py`, and `test_registry.py`
  share this contract: C safe loader when available, Python safe fallback,
  replacement observed on each call, malformed/unsafe tags and missing-file
  failures preserved, aliases/merges/quoted scalars preserved. This does not
  cache machine identity, authority, or registry contents.
- **W4-ATTR-ENCODING:** native attribution Git UTF-8 output capture and
  caller encoding restoration. Attribution README, `emit-policy.ps1`, and
  `test_emit_policy.py` own Unicode repository-path assertions under available
  PowerShell implementations. It is separate from guidance fixture work.
- **W4-ATTR-FIXTURE:** native-compatible attribution Bash guidance fixtures.
  Only `test_write_session_guidance.py`: real-tool launchers retain DLL lookup,
  explicit native Python is available only where intended, POSIX paths and
  isolated home are correct, and warm cache works with no Python fallback.
  Do not weaken cold-cache or bounded session-file assertions.
- **W1-BRIDGE-HOLDER:** namespace resolver timeout remains in its owning task.
  Bridge README, Python-3.10 backport dependency, resolver source, and first
  roster-cache hunk stay together. Preserve cancellation-resistant provider
  ownership across repeated cancellation, replacement and shutdown; release
  test waiters in finally. New dependency still owes seasoning and normal
  manifest/release gates; the census does not establish its age.
- **W1-BRIDGE-DISCOVERY:** deterministic abandoned-discovery report rejection.
  Only the latter two roster-cache hunks: synchronize worker entry/call count,
  distinguish stale/fresh reports, require advancing generation, ensure the old
  worker's eventual result is never applied, and always drain the caller/cache.
  Do not merge with holder changes merely because both touch one file.
- **W4-BRIDGE-STDIO:** native Worktrees-owner launch regression assertion:
  exact argv/environment, inherited stdin/stdout/stderr, no-window flags,
  and returned exit status. Only `test_worktree_owner_launch.py`; this census
  does not treat that mocked assertion as real native-launch evidence.
- **W4-CODESPACES-PORTABLE:** isolated native profile and audit fixture
  portability, in `test_launch_policy.py` and only the explicitly retained
  audit subspans partitioned under B-CODESPACES-SPLIT. Keep Path.home
  isolation, host-native Bash (not a WSL launcher), POSIX extra-directory
  framing, checked subprocess failure, and a failing rather than skipping
  unavailable-Bash prerequisite. The mixed audit hunk cannot be extracted
  wholesale; its added skip is excluded from this retained contract and
  awaits the separate R-BASH-SKIP review.
- **W4-CODESPACES-PREP:** immutable installer payload preparation only.
  Recovery-test helper/template and uninstall fixture hunks share staged
  immutable runtime materialization but make a private copy per test, excluding
  nested test suites rather than runtime dependencies. Selected/unselected
  recovery behavior remains B6909 and must not slip into this fixture batch.
  Depends on the actual current materializer contract and, if used, its
  separately merged owner changes; a private overlay cannot satisfy it.
- **W4-INDEX-SHUTDOWN:** only `test_installation_cells.py` hunks.
  Retain per-instance shutdown token from successful observations, skip already
  exited processes, capture control failures, require explicit shutdown success
  and graceful zero exit, and report timeout/control errors rather than silently
  swallowing them. No production service or installer lifecycle rewrite.
- **W4-WATCH-BURST:** only Pull Requests `test_watch_delivery.py`.
  Hold fetch until all subscriptions register, prove no early callback,
  retain bounded eight-way callback concurrency/no lost events, and release
  both gates in finally. Independent of native pane and installer work.
- **W4-VAULT-COMPILER:** only the offline UV compiler-construction hunk
  `dac8b6718c26` in Vault `test_install_snapshot_ps1.py`.
  Compile the offline UV fixture with the available .NET Framework compiler,
  retaining compiler failure diagnostics. This contract changes fixture
  executable construction, not scheduled-task controls.
- **W4-VAULT-NOSERVICE:** only the two provisioning-harness hunks
  `bfddebeed21e` and `def46d35da51` in that same Vault test file.
  Prove NoService provisioning cannot mutate scheduled tasks using explicit
  throwing controls, invoking the wrapper with the actual installer/root
  arguments and forwarding its exit status. This contract does not change
  how the offline UV executable is compiled.
  The compiler and NoService contracts are independently scoped candidate
  PRs; sharing a file does not justify combining them. No code dependency
  between them is established by this census. If a consumer requires one,
  name and land that prerequisite separately rather than merging the scopes.
  Neither contract authorizes a global runtime repair.
- **W4-DEPENDENCY-METADATA:** canonical
  `libs/installer-engine/installer_dependency_policy.py` and all thirteen
  plugin copies. Preserve existing setup.cfg data/interpolation behavior while
  locating retained setuptools egg-info in the private retained input, fail
  explicitly on configuration/filesystem errors, and leave the full dependency
  graph, seven-day seasoning and resolution/retention checks intact.
  Copies are derived from the canonical library, not independent patches.
  Settled as M8487 at the retained-input preparation/helper seam, not the old
  run_uv_install line coordinates. The separately owned
  `tools/test_installer_dependency_policy.py` assertions must accompany the
  canonical contract at integration, with their own census disposition; the
  mixed S076/S087 tools keys are not wholly settled by this native disposition.
  This batch is not permission to take #6909's snapshot copier or overlay.

Each batch above also needs the reviewed plan, its own newly scoped
changefile, current-head review, required CI, and the actual consumer
validation specified by the plan. The cross-scope tools tests and
changefiles are coordinator-owned integration dependencies, not silently
assigned implementation work by this report. Unresolved ownership or failed
reconciliation blocks admission rather than expanding a batch.

### Proposed retirement requiring review

- **R-IMPORT — proposed retirement, not retired:** the Codespaces audit
  import-order-only hunk moves an already-present Path import; pinned Dev
  already imports Path (actual introduction in
  ThomasMichon/copilot-extensions#7155,
  `cc6aa177e44a864551f036e5e76ed84622651cd1`, confirmed by patch/blame),
  but does not contain the proposed reorder. It carries no distinct runtime or
  assertion contract; review may omit that cosmetic change while retaining
  W4-CODESPACES-PORTABLE. This is not an already-landed reorder claim.
- **R-ROUNDING — proposed retirement, not retired:** the reader-observation
  hunk only adds an explanatory floating-point rounding comment beside an
  existing pytest.approx assertion. No assertion changes. Review may omit the
  comment rather than manufacture a new telemetry batch. No telemetry
  correctness or native CPU evidence is inferred.

## Continuation hunk ledger

### Subsequent admission: W4-WATCH-BURST

Merged in #8258 as M8258 for only `9de716138927` and `8e164df76d48` in the
original
Pull Requests `test_watch_delivery.py`. The fixture holds its snapshot fetch
until all 20 subscribers are registered, asserts that no callback has started,
then releases the burst. The existing eight-worker limit, exact peak of eight,
20 unique delivered identities and empty subscriber registry remain asserted.
Both gates are released in `finally`, including on a registration failure.
No runtime, timeout, test selection, skip or containment policy changes.

The complete existing delivery module passed 88 tests with no skips on native
Windows, exactly two workers, 12.70 seconds including preparation and cleanup.
It retained the 30-second item limit, 240-second diagnostic group bound and
default process, memory and temporary-space caps. This exercises the real
daemon, durable registration and callback scheduler, not a mocked scheduling
proxy. There is no installer/bootstrap or fresh-machine behavior change, and
no external venue is needed for this local threading contract. Existing plugin
usage documentation remains accurate; this ledger records the test setup
change. Review and required CI passed before the actual merge. Wider residual
disposition
and repeated complete-plugin timing qualification remain open.

### Subsequent admission: W4-ATTR-ENCODING

Merged in #8255 as M8255 for only `f4aafb2aa54f`, `978cd04361f6`, `ba6c4547c0e7`
and `4e163bc20889`, under the original attribution README, policy script
and policy-test paths. The four keys retain their original semantic contract:
the normally invoked PowerShell entrypoint temporarily selects UTF-8 for native
Git output capture and restores the caller's output encoding in `finally`.
Dot-sourced helper behavior and all attribution-policy decisions are unchanged.
There is no session-writer, Bash-fixture or command-binding change.

The existing Unicode-payload regression remains independently collected for
both available PowerShell implementations. Its stronger driver sets an ASCII
caller encoding before invoking the real script against an accented/emoji
repository path, then proves the encoding was restored. It also checks the
malformed-payload early-exit path preserves `{}` and its diagnostic while
restoring that same caller encoding. Both implementation cases failed against
the original script's empty policy result and pass with UTF-8 capture.

The complete policy module passed 99 tests with three unchanged POSIX-specific
skips, native Windows, exactly two workers, 147.81 seconds including preparation
and cleanup. The run retained the 30-second item limit, 240-second diagnostic
limit and all default process/memory/temp bounds. Touched-Python lint and
whitespace checks passed. A new changefile names only `ai-attribution`.
The real local Git/PowerShell consumer is exercised directly; no install,
bootstrap or runtime change requires a fresh-machine or separate external
venue here. Review and required CI passed before merge; this is not full-plugin
or parent timing qualification.

### Subsequent admission: W4-PANE

Merged in #8252 as M8252 for only the W4-PANE source keys `818365a959b9`,
`6eced593549d`, and `ea2912aeeebb`, plus their directly dependent native
validation and release metadata. The README and wrapper retain their original
paths. Both original Job Object test names move from the proposed new
`test_pane_wrapper_job.py` into the existing `test_pane_wrapper_argv.py`;
its three argv tests and existing Windows platform guard stay unchanged.
No new skip is added, and missing PowerShell fails the new ownership tests.
Each ownership test additionally exercises the real promotion materializer's
Worktrees fallback, comparing its wrapper bytes to the canonical source.
The new changefile therefore names both Manager and Worktrees.

The native binding uses P/Invoke metadata rather than C# compilation. The
zeroed extended-limit buffer retains the Windows 144/112-byte ABI, flag
offset 16 and kill-on-close flag `0x2000`. It adds no process/memory limits,
breakaway permission, child launch flags or lifetime escape. Wrapper handle
ownership, existing fail-open behavior and all native deadlines remain intact.
The two native regressions failed against the old compiler path, then passed
with the retained binding; the actual-wrapper test observes child startup and
kernel-signaled termination after force-killing its own wrapper.

The existing opt-in PSMux lane initially exposed a test-environment isolation
failure: an inherited `PSMUX_SESSION` prevented either new private test session
from starting. The directly coupled fixture now removes only that inherited
session marker from its copied environment, preserving the real host environment
and private PSMux data directory; a regression asserts both properties.
Both complete pane modules then passed all 41 cases with the real PSMux lane
enabled, exactly two native workers and no skips, in 51.00 seconds including
preparation and cleanup. Both producers exercised two cycles with zero owned
visible windows, foreground transitions or leaked descendants. These checks
also exercised canonical and fresh promotion-materialized fallback payloads.
Touched-Python lint, install-contract, headless-launch and whitespace guards
passed. No runtime was installed or updated; the available live local consumer
was tested directly, rather than provisioning a separate external venue.
This is functional/native consumer acceptance for this residual, not normal
group or complete-plugin performance qualification. Review and required CI
passed before merge; all wider census and parent timing obligations remain open.

### Subsequent admission: W4-TRANSPORT

Merged in #8249 as M8249. The pinned keys remain historical; their dispositions
and totals below include this settlement. This implementation carries only the
five W4-TRANSPORT keys:
`96d6672d87b3`, `d6741aa0c62f`, `fcc53c6c01ac`, `50309299da22`, and
`2bf4983b2622`, under their original three paths. The accepted candidate uses
the available safe C loader without caching parsed topology or changing
identity/authority lookup. The original file-parser cases run against both
the Python and preferred safe loaders; replacement, malformed/unsafe YAML,
missing files, aliases, merges and quoted scalars retain explicit assertions.
A separate isolated-module import additionally proves the fallback when
`yaml.CSafeLoader` is absent, including refusal of an unsafe tag.

Both required consumer inventories identify `agent-bridge`, `agent-machines`,
`agent-ssh`, `agent-worktrees`, and `worktree-manager` as canonical editable
consumers, with no physical machine-transport copies to patch. A newly scoped
patch changefile names all five. No installer, command-binding, control-plane,
runtime installation, test selection, skip, timeout or containment policy is
changed. Review and required CI passed before the actual merge.

Native Windows validation used exactly two workers and unchanged 30-second
item, 240-second diagnostic and default process/memory/temp bounds. The complete
library suite passed 347 cases (5.64 seconds including preparation and cleanup).
The complete Worktrees `test_config.py`, `test_machines_yaml_location.py`, and
`test_machine_identity.py` selection passed 192 cases (30.78 seconds inclusive),
exercising real filesystem-backed consumers. Install-contract, vendored-library
consistency, touched-Python lint and diff-whitespace checks passed. These are
functional checks, not a complete same-head plugin replay or a claim that any
normal group or the parent 900-second budget is qualified. No fresh-machine or
external venue was provisioned: the change neither installs nor bootstraps
anything, and the relevant native consumer is exercised locally.

### `libs/installer-engine/installer-engine.ps1`

- `@@ -17,6 +17,27 @@` — `a26347235fb1` — B6909

### `libs/installer-engine/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `libs/machine-transport/README.md`

- `@@ -5,6 +5,11 @@ identity, and local-vs-SSH transport resolution, vendored (shared, not` — `96d6672d87b3` — M8249

### `libs/machine-transport/src/machine_transport/registry.py`

- `@@ -31,6 +31,8 @@ import yaml` — `d6741aa0c62f` — M8249
- `@@ -139,7 +141,7 @@ def parse_machines_yaml_file(` — `fcc53c6c01ac` — M8249

### `libs/machine-transport/tests/test_registry.py`

- `@@ -4,7 +4,9 @@ from dataclasses import replace` — `50309299da22` — M8249
- `@@ -157,7 +159,48 @@ def test_legacy_guest_hostname_and_display_metadata_do_not_alias_native_identity` — `2bf4983b2622` — M8249

### `libs/payload-invocation/README.md`

- `@@ -65,10 +65,16 @@ the real Worktrees CLI retain the complete copied runtime and staged dependencie` — `82efa7836d8b` — M8470

### `libs/payload-invocation/generate.py`

- `@@ -462,14 +462,39 @@ def render(` — `861a8d2296c7` — M8470
- `@@ -480,19 +505,27 @@ def render(` — `e9bbf0de1d0d` — M8470

### `libs/payload-invocation/tests/test_command_binding.py`

- `@@ -449,9 +449,8 @@ def test_command_binding_catalog_disables_user_site_startup(` — `559f0578fc59` — M8470
- `@@ -642,6 +641,7 @@ def test_command_binding_real_catalog_read_only(` — `f2caae713082` — M8470
- `@@ -671,11 +671,14 @@ def test_command_binding_real_catalog_read_only(` — `999c4dc5154f` — M8470
- `@@ -687,7 +690,13 @@ def test_command_binding_catalog_python_launcher_avoids_app_alias(` — `8efa6a7c1482` — M8470
- `@@ -698,12 +707,12 @@ def test_command_binding_catalog_python_launcher_avoids_app_alias(` — `d4d416365954` — M8470
- `@@ -717,11 +726,28 @@ def test_command_binding_catalog_python_launcher_avoids_app_alias(` — `7bdbb1eebf76` — M8470

### `libs/versioned-runtime/tests/test_source_install_transactions.py`

- `@@ -49,8 +49,9 @@ def _bash() -> str | None:` — `7001e9edeef8` — B6909
- `@@ -190,6 +191,7 @@ function Invoke-VersionedActivate {{` — `1b219328bb62` — B6909

### `plugins/agent-bridge/README.md`

- `@@ -508,6 +508,10 @@ reaping. Sampling is provider-local, off-loop, at most once per second, with a` — `d67d4250cb30` — W1-BRIDGE-HOLDER

### `plugins/agent-bridge/pyproject.toml`

- `@@ -16,6 +16,7 @@ authors = [` — `1c2b3254c62b` — W1-BRIDGE-HOLDER

### `plugins/agent-bridge/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `f6229a828873` — B6909

### `plugins/agent-bridge/scripts/install.ps1`

- `@@ -2174,7 +2174,7 @@ function Materialize-SnapshotVendoredLibs {` — `936748ef55f3` — B6909
- `@@ -2259,7 +2259,7 @@ function Invoke-Stamp {` — `a7174256bab1` — B6909
- `@@ -3457,7 +3457,7 @@ try {` — `0bffccc5f470` — M7304

### `plugins/agent-bridge/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-bridge/src/agent_bridge/agent_registry_resolver.py`

- `@@ -5,10 +5,16 @@ from __future__ import annotations` — `2cefb12fff81` — W1-BRIDGE-HOLDER
- `@@ -860,7 +866,9 @@ class AgentResolver(_ProviderDiscoveryMixin):` — `61869aca9ce8` — W1-BRIDGE-HOLDER

### `plugins/agent-bridge/tests/test_agent_roster_cache.py`

- `@@ -312,25 +312,29 @@ async def test_provider_replacement_keeps_cancel_resistant_scan_owned_until_shut` — `c63b05502de2` — W1-BRIDGE-HOLDER
- `@@ -1368,16 +1372,24 @@ async def test_abandoned_scans_report_is_never_applied_to_the_registry():` — `ae5f24cac116` — W1-BRIDGE-DISCOVERY
- `@@ -1389,26 +1401,40 @@ async def test_abandoned_scans_report_is_never_applied_to_the_registry():` — `495027e6740a` — W1-BRIDGE-DISCOVERY

### `plugins/agent-bridge/tests/test_reader_observation.py`

- `@@ -678,6 +678,7 @@ async def test_native_cpu_receipt_and_telemetry(` — `add84909fe89` — R-ROUNDING

### `plugins/agent-bridge/tests/test_worktree_owner_launch.py`

- `@@ -77,6 +77,25 @@ def test_native_windows_never_launches_a_bash_or_wsl_fallback(tmp_path, monkeypa` — `dc3ef0c9e847` — M8444

### `plugins/agent-codespaces/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `cd1cb67cf519` — B6909

### `plugins/agent-codespaces/scripts/install.ps1`

- `@@ -1564,7 +1564,7 @@ function Materialize-SnapshotDependencies {` — `df3fb54ccbc6` — B6909
- `@@ -1618,7 +1618,7 @@ function Invoke-Stamp {` — `fbe98687edee` — B6909
- `@@ -1684,7 +1684,7 @@ try {` — `ce017de3cbaa` — M7304

### `plugins/agent-codespaces/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-codespaces/tests/test_install_ps1_recovery.py`

- `@@ -204,15 +204,17 @@ def _slot(home: Path) -> Path:` — `5667e5189491` — W4-CODESPACES-PREP
- `@@ -226,9 +228,26 @@ def _copy_plugin_tree(dst: Path) -> None:` — `56cc8b3bc1b2` — W4-CODESPACES-PREP
- `@@ -266,7 +285,8 @@ def test_uv_only_construction_bootstrap_is_fail_closed(tmp_path: Path, mode: str` — `3775871f4c7d` — B6909
- `@@ -275,11 +295,14 @@ def test_uv_only_host_refuses_selected_malformed_slot(tmp_path: Path) -> None:` — `b8e98d0b5ee5` — B6909
- `@@ -290,10 +313,21 @@ def test_uv_only_host_refuses_selected_malformed_slot(tmp_path: Path) -> None:` — `4a57b51b5f12` — B6909
- `@@ -334,7 +368,8 @@ def _seed_healthy_slot(home: Path) -> Path:` — `e99b2881c433` — B6909
- `@@ -343,24 +378,32 @@ def test_install_refuses_selected_slot_with_incomplete_metadata(tmp_path: Path)` — `07996adab79e` — B6909
- `@@ -462,6 +505,7 @@ def test_first_use_propagates_provision_failure(tmp_path: Path) -> None:` — `2d1148191189` — W4-CODESPACES-PREP
- `@@ -473,7 +517,7 @@ def test_marketplace_staged_uninstall_removes_runtime_and_binstubs(` — `5e8175042e04` — W4-CODESPACES-PREP
- `@@ -494,6 +538,7 @@ def test_marketplace_staged_uninstall_removes_runtime_and_binstubs(` — `5874a69a50c0` — W4-CODESPACES-PREP
- `@@ -505,7 +550,7 @@ def test_marketplace_staged_uninstall_waits_without_holding_runtime_cwd(` — `e00c0c86f5a1` — W4-CODESPACES-PREP

### `plugins/agent-codespaces/tests/test_launch_policy.py`

- `@@ -6,6 +6,7 @@ import argparse` — `77b67744e9a7` — W4-CODESPACES-PORTABLE
- `@@ -19,6 +20,7 @@ def _align_isolated_profile(monkeypatch, tmp_path):` — `a03cdbb259e0` — W4-CODESPACES-PORTABLE

### `plugins/agent-codespaces/tests/test_unsaved_guard.py`

- `@@ -4,10 +4,10 @@ box and force only that refusal when all of them are verified clean."""` — `74ca42dc78e4` — R-IMPORT
- `@@ -133,16 +133,20 @@ def _repo(path, remotes):` — `7c269d084096` — B-CODESPACES-SPLIT

### `plugins/agent-containers/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-dispatch/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `b50875609eb0` — B6909

### `plugins/agent-dispatch/scripts/install.ps1`

- `@@ -1159,7 +1159,7 @@ function Materialize-DispatchSnapshot {` — `23152754aa94` — B6909
- `@@ -1333,7 +1333,7 @@ function New-PluginBuildSnapshot {` — `28eb36281086` — B6909
- `@@ -4189,7 +4189,7 @@ switch ($Action) {` — `69e4e888bdb4` — M7304

### `plugins/agent-dispatch/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-dispatch/tests/test_install_ps1_build_snapshot.py`

- `@@ -42,6 +42,7 @@ import pytest` — `41369215d2a6` — B6909
- `@@ -58,10 +59,10 @@ _skip_pwsh_race = pytest.mark.skipif(` — `19c13fc75305` — B6909
- `@@ -82,8 +83,8 @@ def _harness_script(extra_script: str) -> str:` — `4f2a86c0f8cf` — B6909
- `@@ -94,6 +95,8 @@ def _harness_script(extra_script: str) -> str:` — `4e124468775a` — B6909

### `plugins/agent-index/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `d42cdae9b364` — B6909

### `plugins/agent-index/scripts/install.ps1`

- `@@ -1843,7 +1843,7 @@ function Install-Engine {` — `2830dc56b3c0` — M7304
- `@@ -2494,7 +2494,7 @@ function Materialize-IndexSnapshot {` — `9ac443858cc8` — B6909
- `@@ -2526,7 +2526,7 @@ function New-IndexSnapshot {` — `51b54d38f912` — B6909
- `@@ -2716,7 +2716,7 @@ switch ($Action) {` — `50dbb18a706c` — M7304

### `plugins/agent-index/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-index/tests/test_installation_cells.py`

- `@@ -4432,6 +4432,8 @@ def test_two_cell_local_services_bind_distinct_os_assigned_ports(` — `81f4541aef45` — M8437
- `@@ -4536,20 +4538,19 @@ def test_two_cell_local_services_bind_distinct_os_assigned_ports(` — `2af627149899` — M8437
- `@@ -4560,17 +4561,22 @@ def test_two_cell_local_services_bind_distinct_os_assigned_ports(` — `1515dab41eb1` — M8437
- `@@ -4579,21 +4585,25 @@ def test_two_cell_local_services_bind_distinct_os_assigned_ports(` — `98155104f7ca` — M8437

### `plugins/agent-logger/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `c58d567d566a` — M7304

### `plugins/agent-logger/scripts/install.ps1`

- `@@ -1534,7 +1534,7 @@ switch ($Action) {` — `73537df63674` — M7304

### `plugins/agent-logger/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-machines/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-mcp/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-pull-requests/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `66c150752e4d` — M7304

### `plugins/agent-pull-requests/scripts/install.ps1`

- `@@ -851,7 +851,7 @@ try {` — `5b9d094d7f39` — M7304

### `plugins/agent-pull-requests/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-pull-requests/tests/test_watch_delivery.py`

- `@@ -445,6 +445,7 @@ def test_unchanged_snapshots_do_not_rewrite_registry(make, monkeypatch):` — `9de716138927` — M8258
- `@@ -463,16 +464,23 @@ def test_callback_burst_has_bounded_concurrency_and_no_lost_events(make):` — `8e164df76d48` — M8258

### `plugins/agent-ssh/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `9411dd332ebc` — M7304

### `plugins/agent-ssh/scripts/install.ps1`

- `@@ -1228,7 +1228,7 @@ Write-Host '  Try: agent-ssh version' -ForegroundColor DarkGray` — `6ca2e9f412e1` — M7304

### `plugins/agent-ssh/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-vault/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `468883502314` — M7283

### `plugins/agent-vault/scripts/install.ps1`

- `@@ -1341,7 +1341,7 @@ try {` — `ae3c109dc679` — M7283

### `plugins/agent-vault/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `plugins/agent-vault/tests/test_install_snapshot_ps1.py`

- `@@ -265,19 +265,22 @@ public class OfflineUv {` — `dac8b6718c26` — M8310
- `@@ -574,6 +577,17 @@ def provisioned_snapshot(stamped_snapshot):` — `bfddebeed21e` — M8425
- `@@ -581,10 +595,8 @@ def provisioned_snapshot(stamped_snapshot):` — `def46d35da51` — M8425

### `plugins/ai-attribution/README.md`

- `@@ -107,7 +107,9 @@ grammar, keys, precedence, authority boundaries, and diagnostics.` — `f4aafb2aa54f` — M8255

### `plugins/ai-attribution/scripts/emit-policy.ps1`

- `@@ -651,6 +651,13 @@ function Invoke-Policy {` — `978cd04361f6` — M8255

### `plugins/ai-attribution/tests/test_emit_policy.py`

- `@@ -91,10 +91,11 @@ def _run(` — `ba6c4547c0e7` — M8255
- `@@ -252,11 +253,21 @@ def test_payload_cwd_is_authoritative_when_process_cwd_differs(` — `4e163bc20889` — M8255

### `plugins/ai-attribution/tests/test_write_session_guidance.py`

- `@@ -5,6 +5,7 @@ from __future__ import annotations` — `f8533d1361b1` — W4-ATTR-FIXTURE
- `@@ -399,6 +400,23 @@ def _bash_prereqs():` — `09672a182c8d` — W4-ATTR-FIXTURE
- `@@ -411,9 +429,9 @@ def test_bash_wrapper_writes_bounded_session_file(tmp_path):` — `ffc654b37c1e` — W4-ATTR-FIXTURE
- `@@ -430,10 +448,10 @@ def test_bash_wrapper_writes_bounded_session_file(tmp_path):` — `ab4ef1d4e855` — W4-ATTR-FIXTURE
- `@@ -465,6 +483,14 @@ def test_bash_wrapper_serves_warm_cache_without_spawning_python(tmp_path):` — `25aa8c1db2a3` — W4-ATTR-FIXTURE
- `@@ -475,11 +501,12 @@ def test_bash_wrapper_serves_warm_cache_without_spawning_python(tmp_path):` — `fc0d183330d5` — W4-ATTR-FIXTURE
- `@@ -505,9 +532,9 @@ def test_bash_wrapper_falls_back_on_cold_cache(tmp_path):` — `6923abfa91d6` — W4-ATTR-FIXTURE

### `plugins/budget-guidance/scripts/install.ps1`

- `@@ -1134,7 +1134,7 @@ Write-Host '  Try: budget-guidance --version' -ForegroundColor DarkGray` — `78aa29ab1419` — M7304

### `plugins/budget-guidance/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

### `worktree-manager/bin/README.md`

- `@@ -12,6 +12,10 @@ of the `worktree-manager-control-plane` effort.` — `818365a959b9` — M8252

### `worktree-manager/bin/pane-wrapper.ps1`

- `@@ -54,31 +54,43 @@ function Set-AwPaneKillOnCloseJob {` — `6eced593549d` — M8252

### `worktree-manager/tests/test_pane_wrapper_job.py`

- `@@ -0,0 +1,180 @@` — `ea2912aeeebb` — M8252

### `plugins/agent-worktrees/scripts/emit-command-catalog.ps1`

- `@@ -1,6 +1,6 @@` — `2041e504e346` — M8470
- `@@ -26,11 +26,28 @@ $catalog = [ordered]@{` — `12e7feef63a7` — M8470
- `@@ -40,16 +57,24 @@ if (-not $bindingPython) {` — `da9969913718` — M8470

### `plugins/agent-worktrees/scripts/install.human-output.json`

- `@@ -2,7 +2,7 @@` — `b9e8b3f1c3ce` — M7304

### `plugins/agent-worktrees/scripts/install.ps1`

- `@@ -3712,6 +3712,7 @@ function Copy-StampDirectory {` — `6a84464cc634` — M6707
- `@@ -4241,7 +4242,7 @@ switch ($Action) {` — `e61fec4a55c2` — M7304

### `plugins/agent-worktrees/scripts/installer_dependency_policy.py`

- `@@ -8,6 +8,7 @@ Captured settings may contain credentials and must never be printed.` — `7c3a2f974941` — M8487
- `@@ -876,6 +877,19 @@ def run_uv_install(` — `d498636ff61a` — M8487

## Published-only residual reconciliation

The original merge-base-to-published diff changes the same **62 scoped paths**;
there are **zero published-only scoped paths**. This was not inferred from
path overlap alone: published final contents were compared with continuation
contents, then original unique-change hunks were inspected for the five paths
whose final contents differ.

- `libs/payload-invocation/README.md`: original binding behavior additions are
  retained verbatim; unrelated intervening fixture documentation shifts hunk
  context. Its W4-BIND contract is now settled as M8470.
- `libs/payload-invocation/generate.py`: both original changed hunks are
  retained; other intervening generator content differs. No original binding
  behavior disappeared.
- `libs/payload-invocation/tests/test_command_binding.py`: all six original
  changed assertion/setup blocks remain in the continuation, with shifted
  coordinates. Their W4-BIND contract is now settled as M8470.
- `plugins/agent-worktrees/scripts/install.ps1`: both original changed blocks
  remain in the continuation; their independent current-Dev dispositions are
  M6707 and M7304 above. Larger intervening installer reconciliation is not an
  additional unlanded published feature.
- `plugins/agent-worktrees/scripts/install.human-output.json`: the original
  literal checksum differs from both continuation and Dev, because it binds a
  different installer input. This is the one published-only literal residual,
  not an extra continuation hunk:
  `@@ -2,7 +2,7 @@` — `b5c129139808` — **R-PUBLISHED-RECEIPT
  (proposed retirement requiring review)**. Rationale: preserve the original
  Git/review artifact, but do not replay a receipt for an obsolete installer;
  current merged installer receipts must be regenerated from actual inputs.
  This disposition does not claim that the original checksum itself landed.

The other 57 published final paths equal their continuation counterparts.
There are therefore no uncovered published-only behavioral hunks in this
scope; the explicit stale receipt proposal above still requires review.
Original published variants are not double-counted among the 128 continuation
keys.

## Completion boundaries and mechanical verification

The report claims census coverage and the explicitly named merged contracts, not
completion of the native wave or parent effort. Every continuation
path/header/hash must occur exactly once, with one disposition, and be
mechanically compared with the pinned unified-3 diff. Published-only receipt
handling is separately counted. No unspecified hunk is silently retired.

Mechanical self-check passed against freshly read immutable Git objects:
**62 distinct paths, 128 distinct exact keys, zero missing/extra/duplicate
keys, and one disposition on every row**. Updated totals are **79 actual-merged,
25 blocked (24 B6909 and 1 B-CODESPACES-SPLIT), 22 retained, and 2
proposed-retirement** continuation hunks. The blocked mixed key additionally
partitions retained portability subspans from the R-BASH-SKIP review proposal;
these are not additional full-hunk census rows.
The separately identified published-only receipt contributes one additional
review-required proposal, not a 129th continuation hunk. No
blocked-unclassified row remains; the explicit existing-owner block remains
binding. A public-safety check found no absolute machine paths in this report.

The parent remains open: repeated complete same-head native runs, each at or
below 900 seconds including preparation and cleanup, every group at or below
240 seconds, exactly two Worktrees workers, and unchanged 30-second item,
300-second runner-group, 900-second plugin, 128-process, 4096 MiB memory and
2048 MiB temporary-storage limits. Native child deadlines and mark semantics
are unchanged. No acceptance, performance improvement, restored CI lane,
skip waiver, external-venue result, or installer-owner completion is claimed.
Root workflow ownership under #6237/#1303 is outside this report.
