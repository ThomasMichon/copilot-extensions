# Update-flow audit

This is the evidence inventory for [Update Flow Observability](README.md),
tracked by #6812. It separates required work, presentation noise, proven
repetition and still-unattributed time. No runtime optimization is claimed
complete by this document.

## Reference run and measurement limits

The reference is one real Windows launch-maintenance run, not a synthetic
benchmark. Its source mapping was inspected at
`2182afbff462df67c7717203381f2a3a8cbb47b1`. The selected installed launcher
and plugin releases can differ from that source snapshot; source hypotheses
must not be presented as exact deployed-command measurements.

The normalized ledger accounts for **all 249 setup-log lines**: five header
lines and 244 timestamped lines. Of the timestamped lines, 212 forward child
installer output. Paths, process IDs, endpoints and operator configuration
are deliberately not reproduced.

The interval from the first launcher message to the pane-command diagnostic
was **2,153.691 seconds (35m 53.691s)**. Copilot startup and initial prompt
execution occurred later and are not silently included in that number.

| Visible interval | Seconds | What this proves |
|---|---:|---|
| Resolve call to resolved plan, lines 9-10 | 47.076 | Resolution wall interval, not a breakdown of its internal operations |
| Waiting for stage to downloaded-payload decision, lines 12-13 | 42.642 | Remaining join wait after overlap with resolution |
| Bootstrap update command to reported failure, lines 18-19 | 11.730 | A failed update, not a successful cheap no-op |
| Bridge first invocation to next installer, lines 23-29 | 69.206 | Time around a reported healthy content-match no-op |
| Codespaces first invocation to next installer, lines 29-33 | 500.396 | Action-boundary interval; completion/status detail is missing |
| Dispatch invocation to next installer, lines 33-198 | 365.114 | Build, activation, cutover and supervisor work together |
| Index invocation to next reconcile plan, lines 198-230 | 385.360 | Client/server build, health, cutover and subsequent planning together |
| Bridge second invocation to next installer, lines 231-237 | 48.454 | A second reported healthy content-match no-op |
| Codespaces second invocation to runtime reselection, lines 237-242 | 495.584 | Another action-boundary interval, not an isolated package-command duration |

Two output-silent gaps alone were **443.929s** after the first Codespaces
"venv ready" line and **451.570s** after the second. Their sum, **895.499s**,
is approximately **41.6%** of the recorded launch interval. These are
inter-line arrival gaps, not proof that either venv construction or a
particular dependency command consumed that time.

There were **six installer invocations**, not eight: Bridge and Codespaces
ran twice; Dispatch and Index ran once. A two-pass policy does not itself
justify repeating a target or prove that the planner failed to recheck it.
The repeated target's actual reason and action outcome must be retained.

## Complete line ledger

Each row accounts for every line in its stated range. Related messages share
a row only where the necessity and disposition are the same; numbered
operations within a row remain distinct. "Keep" means keep the operation or
evidence, not necessarily its current wording or repetition.

| Lines | Normalized content and reason | Disposition |
|---|---|---|
| 1-5 | Log title, start time, launcher process identity, host identity and separator. Needed for local incident correlation, not repeated terminal banners. | Keep durable metadata; omit private values from shared reports and avoid blank terminal records. |
| 6 | Launcher starting. Establishes the outer operation. | Keep one timed start. |
| 7 | Selected runtime root. Explains which installation supplies the executable. | Keep attributable selection once; normal output need not repeat the full path. |
| 8 | Background payload-update stage starts. Hides download latency behind resolution. | Keep stage boundary and ownership result. |
| 9 | Launch resolver starts. | Keep timed operation. |
| 10 | Resolved plan and target. Required launch identity evidence. | Keep completion and duration; do not expose unnecessary command arguments. |
| 11 | Aggregate maintenance finalization starts. | Keep one parent operation, not a second unexplained update. |
| 12 | Join the existing stage, bounded wait. | Keep wait reason and elapsed heartbeat. |
| 13 | Changed payload selected for installation. | Keep decision and selected identity. |
| 14 | Background install starts; selected launcher remains the current launch's code. | Keep explicit owner and current-versus-next-launch semantics. |
| 15 | Bootstrap staleness inspection starts. | Keep while freshness cannot be established from the admitted result. |
| 16 | Bootstrap stale decision. | Keep reason, not merely the word "stale." |
| 17 | Agent-worktrees bootstrap update starts. | Keep only when its predicate still requires it. |
| 18 | Raw command diagnostic. | Keep redacted details in diagnostics; normal progress identifies the operation without repeating argv. |
| 19 | Bootstrap update failed with exit 1. | Keep failure and measured duration; never render as completed successfully. Existing repair owner #6796 remains separate. |
| 20 | Staleness is rechecked after failure. | Keep if needed to decide whether launch can proceed; avoid identical discovery already evidenced by the result. |
| 21 | Still stale; launch continues. | Keep explicit degraded continuation and reason. |
| 22 | First reconciliation plan has four actions. | Keep target count, pass and reasons. |
| 23 | First Bridge action starts. | Keep timed target operation. |
| 24-26 | Blank, Bridge banner, blank. | Replace with the parent-owned start line. |
| 27 | Bridge reports healthy exact content and no restart/rebuild. | Keep a distinct `current` outcome, with actual verification cost. |
| 28 | Bridge deploy manifest published. | Keep when metadata repair was needed; distinguish no-op from changed metadata. |
| 29 | First Codespaces action starts. | Keep timed target operation. |
| 30-31 | Blank and Codespaces banner. | Replace with the parent-owned start line. |
| 32 | Codespaces venv is ready. Does not prove package installation, health or activation completion. | Keep as an intermediate result, not target completion. The following 443.929s needs a named phase. |
| 33 | Dispatch action starts without a visible Codespaces completion result. | Keep Dispatch start; add the missing preceding target outcome and exit status. |
| 34-36 | Blank, Dispatch banner, blank. | Replace with the parent-owned start line. |
| 37 | Immutable durable source snapshot selected. | Keep source identity and staging result; no mutable-payload shortcut. |
| 38 | Python selected. | Keep interpreter admission; compact repeated path display. |
| 39 | Install directories established. | Keep only changed/failed directory work at normal verbosity. |
| 40 | Venv created. | Keep intermediate operation completion and duration. |
| 41-46 | Six declared vendored libraries installed. Each supplies a package dependency. | Keep dependency admission/install work when required; coalesce normal detail and measure commands, not output-line spacing. |
| 47 | Main Dispatch package installed with declared extras. | Keep package-build completion and duration. |
| 48-50 | Three build/metadata paths. | Preserve useful artifact details in diagnostics, not three unlabeled progress lines. |
| 51 | Dispatch runtime activated. | Keep immutable completion/selector evidence and ownership. |
| 52-53 | Ordinary and fast-board command shims published. | Keep command consistency checks; report changes or summarize unchanged bytes. |
| 54 | Deploy manifest written. | Keep provenance/selection consistency. |
| 55 | Picker owns pivot discovery. | Preserve the contract in docs; report only a performed reconciliation action at normal verbosity. |
| 56 | Module import verification succeeds. | Keep health gate and duration. |
| 57 | PATH already contains command directory. | Keep check, report unchanged compactly rather than as installation work. |
| 58 | Coordinator graceful cutover starts. | Keep timed serving-generation operation. |
| 59 | Coordinator cutover completes without killing in-flight work. | Keep verified health, publication and drain result. |
| 60 | Existing service environment preserved. | Keep preservation behavior; summarize unchanged configuration. |
| 61 | Loopback binding policy applied. | Keep security behavior; report only a changed or failed policy at normal verbosity. |
| 62 | Existing coordinator task remains registered and running. | Keep idempotent task reconciliation; use `current`, not an implied reinstall. |
| 63-192 | Alternating blank lines and a pretty-printed retirement diagnostic, every content line prefixed as a warning; JSON reports no errors and successful retirement selections. | Preserve structured evidence and the actual parser/fallback failure. Replace 130 terminal records with one accurate summary and a diagnostic reference; do not prefix machine JSON before parsing it. |
| 193 | Generation inventory failed and native supervisor-tree fallback was used. | Keep actionable warning and literal failure reason. Investigate why the preceding apparently successful object was rejected. Do not alter identity/retirement safety merely to quiet output. |
| 194 | Existing supervisor environment preserved. | Keep preservation; compact `current` status. |
| 195 | Supervisor task refreshed in place. | Keep operation and outcome, distinguishing refreshed from recreated. |
| 196-197 | Blank and Dispatch completion banner. | Replace with one parent-owned final target result and duration. |
| 198 | Index action starts. | Keep timed target operation. |
| 199 | Python selected. | Keep interpreter admission; compact path display. |
| 200 | Directories established. | Keep changed/failed work; suppress routine path echo. |
| 201 | A metadata/build path. | Label and retain in diagnostics rather than as an unexplained step. |
| 202 | Index client runtime build starts. | Keep named timed phase. |
| 203 | Index client package installed. | Keep result; current gap is 122.368s, not a measured isolated build duration. |
| 204 | Index server runtime build starts. | Keep separate named phase; it is not the same client build repeated. |
| 205 | Server venv provisioned. | Keep intermediate completion; preserve distinct server health/activation gates. |
| 206-207 | Build/metadata paths. | Label and retain in diagnostics; no unexplained path-only status. |
| 208 | Index runtime activated. | Keep immutable activation and selector result. |
| 209 | Command shim published. | Keep exact command consistency; summarize unchanged publication. |
| 210 | Deploy manifest written. | Keep provenance result. |
| 211 | Module import verification succeeds. | Keep health gate. |
| 212 | An old runtime is collected. | Keep ownership/retention protection and a compact collection summary; no broad deletion shortcut. |
| 213 | Existing service environment preserved. | Keep behavior; compact `current` status. |
| 214 | Index graceful cutover starts. | Keep timed phase. |
| 215 | Passive process/endpoint created. | Keep identity evidence in structured diagnostics; normal line names the passive generation. |
| 216 | New daemon is healthy. | Keep mandatory serving-health gate. |
| 217 | Routing publication flips. | Keep ownership-fenced publication result. |
| 218 | Old generation drains without force. | Keep drain/forced distinction. |
| 219 | Old daemon shutdown requested. | Keep distinction between request and observed retirement. |
| 220 | Relay adoption result is false. | Keep meaning and reason if relevant; a bare Boolean is not an actionable status. |
| 221 | Cutover reports the new active endpoint. | Keep active-generation outcome; avoid redundant endpoint echoes. |
| 222 | Parent reports service cutover success. | Coalesce with the preceding final cutover result; retain only one authoritative completion. |
| 223 | Service environment checked again. | Verify whether a separate owner requires it; do not delete the check solely because the message repeats. |
| 224 | Existing service is not started twice. | Keep singleton behavior; compact `current` outcome. |
| 225 | Service logon registration checked/published. | Keep durable lifecycle reconciliation; distinguish changed from already correct. |
| 226 | Existing engine environment preserved. | Keep separate engine configuration ownership. |
| 227 | Existing engine is not started twice. | Keep engine singleton behavior. |
| 228 | Engine logon registration checked/published. | Keep independently owned engine lifecycle; distinguish changed from already correct. |
| 229 | Heavy engine runtime is unchanged and not rebuilt. | Keep explicit non-action and boundary; do not turn this into heavy provisioning. |
| 230 | Second reconciliation plan has two actions. | Keep fresh postcondition reasons; do not present all four targets as repeated. |
| 231 | Second Bridge action starts. | Explain its new plan reason before deciding whether it is redundant. |
| 232-234 | Blank, Bridge banner, blank. | Replace with target start. |
| 235 | Bridge again reports healthy exact-content no-op. | Keep evidence; investigate why the replan still selected it. |
| 236 | Bridge manifest written again. | Determine why the first publication did not satisfy the plan; do not suppress the symptom without fixing the predicate. |
| 237 | Second Codespaces action starts. | Explain prior exit status and current plan reason. |
| 238-239 | Blank and Codespaces banner. | Replace with target start. |
| 240 | Metadata/build path. | Retain labeled diagnostics, not path-only progress. |
| 241 | Codespaces venv ready again. | Keep intermediate distinction. The next 451.570s again needs a named phase and outcome. |
| 242 | Active runtime reselected after updates. | Keep: the selected interpreter can legitimately change during maintenance. |
| 243 | Stale runtime argument patched in the already-resolved plan. | Keep matching the plan to the admitted active runtime; do not reuse the old executable as a speed hack. |
| 244 | Knowledge/settings preflight completes without changes. | Keep trust/configuration contract; summarize counts and outcome instead of dumping configuration JSON into ordinary progress. |
| 245 | Retired marketplace-override step reports no-op. | Verify no remaining contract before removing dead work; do not invoke a retired mechanism just to print a no-op. |
| 246 | Optional identity switching is disabled. | Preserve disabled behavior; compact `disabled` outcome without unrelated identity detail. |
| 247 | Existing mux session inspected. | Keep duplicate-session prevention. |
| 248 | New mux session created for the existing worktree. | Keep execution identity; this is not another worktree allocation. |
| 249 | Full pane command echoed. | Keep redacted structured launch evidence; omit routine raw argv and do not expose the prompt. |

## Source operation map

The ledger concerns emitted lines. These source operations explain the work
behind them; a line's necessity and the operation's necessity are separate.

| Owner and source | Required work | Audit focus |
|---|---|---|
| `worktree-manager/bin/launch-session.ps1:14-219,284-427` | Parse passthrough/protocol flags, establish correlation, select a usable runtime. | One line owner; retain stdout protocols and explicit runtime failures. |
| `worktree-manager/bin/launch-session.ps1:483-502,540-780` | Stage payload refresh concurrently, join and apply under the proper owner. | Distinguish completed, failed, locked and ambiguous stage results before avoiding any retry. |
| `plugins/agent-worktrees/src/agent_worktrees/update_stage.py:68-245,254-274,306-424` | Single-flight payload stage and exact fingerprints. | Reuse only proven immutable results, not a time-based stale cache. |
| `worktree-manager/bin/launch-session.ps1:647-730,958-1083` | Bootstrap checks and reconciliation before cold launch; live reattach is different. | Preserve failed/degraded outcomes, remove duplicate planning only with valid same-attempt evidence. |
| `plugins/agent-worktrees/src/agent_worktrees/update_cli.py:225-410,828-831,890-989` | Discover selected/deployed targets, build plans and execute reconciliations. | Retain reasons and per-action exit status; verify what each pass actually rechecks. |
| `libs/versioned-runtime/versioned_runtime.py:305-390,1238-1345,1598-2005` | Admitted interpreter selection, immutable fingerprint/completion, atomic activation, protected cleanup. | Do not remove safety work or mutate a completed conflicting slot. |
| `plugins/agent-codespaces/scripts/install.ps1:438-574,650-734,866-937` | Build/reuse the venv, install declared packages and validate source identity. | Trace caller work after "venv ready"; determine healthy-update dependency work precisely. |
| `plugins/agent-dispatch/scripts/install.ps1:2447,3106-3352,3693-3952` | Runtime construction, coordinator/supervisor ownership and cutover. | Separate build, health, drain and parser/diagnostic formatting; keep process-identity protections. |
| `plugins/agent-index/scripts/install.ps1:2434-2600` | Distinct client/server runtime, provenance and serving-generation transitions. | Keep distinct capabilities; avoid repeated normal-output summaries and measure their actual phases. |
| `plugins/agent-worktrees/scripts/install.ps1:337-613,756-930,2391-2500`; `install.sh:247-504,694-780,2926-2984` | Immutable source staging/watchdog, versioned slots, signed/interpreter selection and full isolated CLI health. | Preserve ownership, completion refusal and full import gates; instrument each boundary rather than timing the whole installer as one opaque step. |
| `plugins/agent-worktrees/scripts/install.ps1:2907-2917,3460-3535,4023+` | Deploy scripts/commands and delegate terminal-fragment ownership to a verified Manager. | Distinguish command publication, Manager admission and terminal deployment; an unavailable delegated owner is not successful deployment. |
| `worktree-manager/src/worktree_manager/core_install.py:108-151` and `self_install.py` / `self_install_payload.py` | Manager core and launcher self-install are separate from agent-worktrees' runtime slot. | Do not count terminal-fragment delegation as reinstalling Manager, or introduce a second core installer. |

## Verified causes and remaining uncertainty

### Fresh replan exists; action results are discarded

`worktree-manager/bin/launch-session.ps1:705-763` owns its own two-iteration
loop. Each pass calls `reconcile-plugins` and parses a **new** plan. The
action execution at lines 737-751 does not retain or test the child exit
code. Re-resolving the runtime between passes is intentional.

This differs from the Python `reconcile-plugins --apply` route:
`plugins/agent-worktrees/src/agent_worktrees/reconcile.py:2133-2193`
replans each pass and retains `service`, `argv`, `ok` and `returncode` in
its result. The normal launcher's shell loop does not consume that result.

The first fix is to retain target exit status, timing and context in the
shell path. Do **not** optimize by caching the first plan or treating exit
zero as proof of a serving postcondition.

### Bridge's local-content no-op can leave serving drift unresolved

`plugins/agent-bridge/scripts/install.ps1:2924-2934` verifies the slot
interpreter and imports. Its update content-match gate at lines 3116-3127
can publish a deploy manifest and return before serving-version
verification/cutover. Similar install shortcuts occur at lines 2398-2407
and 2424-2431.

A later native, read-only `reconcile-plugins --peek` still selected Bridge
with `runtime-running-drift`. This is consistent with the source ordering:
matching local content is not proof that the serving generation is current.
The repair must reuse the admitted runtime and its canonical
ownership/health/cutover path, not rebuild it, suppress the planner or
terminate a daemon by process name.

### Codespaces continues from venv readiness into package installation

The exact PowerShell caller chain is:

1. `Deploy-Venv` reports readiness and returns at
   `plugins/agent-codespaces/scripts/install.ps1:935-936`.
2. `Invoke-Update` continues into `Deploy-Package` at lines 1467-1470.
3. `Deploy-Package` enters `Install-PackageInto` at lines 734-736.
4. That package/dependency path runs uv at lines 657-729.
5. Versioned activation follows at lines 1473-1474, with interpreter,
   module-health, input-fingerprint and completion gates at lines 318-340
   and 542-570.

The installer does not omit that package operation just because a completed
identical venv was reusable. It also explicitly refuses an unhealthy
completed slot at lines 872-877: that refusal is **not** permission for
in-place reconstruction.

The read-only replan still selected Codespaces with
`runtime-version-drift`, from `0.5.59-dev1` to `0.5.60-dev1`. The selected
marker remained on the old version despite a newer slot directory existing.
This establishes an unresolved update, not the precise failed command.
Instrument the package/completion/activation boundaries and preserve stderr
and exit status before attributing the two long gaps or removing work.

**Later source delta, not a second implementation:** #6598 landed after the
reference audit baseline. At `683cc4b06c`, the PowerShell Codespaces
`Deploy-Venv` path returns for admitted `RuntimeConstructionReuse`
(lines 866-897), and `Deploy-Package` also returns immediately for that reuse
state (lines 734-738). New candidates still install packages and retain
health/completion/activation gates. Therefore unconditional package work on
every healthy update is a historical finding, not an unfixed current-source
claim. Verify that existing improvement after promotion rather than
reimplementing the transactional owner. The precise cause of the old silent
gaps remains unproved.

The same bounded delta inspection found that #6598 did **not** add a serving
version check to Bridge's content-match no-op and did not change the
supervisor JSON parser.

### Supervisor diagnostics contain a concrete JSON-framing defect

`plugins/agent-dispatch/scripts/install.ps1:2240-2275` invokes the installed
retirement helper and merges stdout/stderr. Its
`scripts/supervisor-lifecycle.ps1:88-100` parser selects only physical
lines whose trimmed text starts with `{`.

For a pretty-printed object, that retains only its opening brace and
misclassifies the properties/arrays/closing brace as diagnostics. With one
opening brace the line-count check can pass, but parsing that brace alone
cannot produce the complete JSON object. The catch then enters the native
fallback at `install.ps1:2278-2368`.

This explains why an apparently successful structured result can produce
130 warning/blank records and then an inventory-failed warning. Correct the
machine framing/parser boundary; do not merely hide the warnings. Preserve
diagnostic stderr, typed result validation, failure propagation and all
identity-scoped retirement protections.

The existing
`plugins/agent-dispatch/tests/test_supervisor_installer_audit.py:267-286`
mixed-output fixture covers a diagnostic plus compact JSON, not this
multiline object. Extend it with real producer/parser coverage, malformed
input and retained diagnostics.

## Existing output contracts

### Native maintenance renderer cost (isolated source fixture)

The native presentation slice compares the old `setup_log` from the
pre-slice source with its bounded shared-renderer replacement. Each trial
logs the same 256 synthetic messages from Bash into an isolated local log,
on the same checkout and environment. There is no installer, network,
package reconstruction or service activity in this fixture. Three
alternating before/after trials include renderer startup and shutdown:

| Measurement | Before | After |
|---|---:|---:|
| Diagnostic messages preserved | 256 | 256 |
| Added phase-boundary lines | 0 | 2 |
| Per-message `date` processes | 256 | 0 |
| Phase formatter processes | 0 | 1 |
| Median fixture duration | 3.724 s | 2.154 s |

The final lifecycle-repaired before durations were 3.849, 3.724 and
3.626 seconds; after durations were 2.494, 2.154 and 1.934 seconds,
including the independent clock reader, parent-loss handling, explicit
completion receipt and job-owned shutdown. These supersede the earlier
pre-review and intermediate clock-repair figures. This proves removal
of repeated formatting subprocesses and a lower cost in this fixture,
not an end-to-end updater speedup. The remaining phase startup, clock-start
acknowledgments, private spool and shutdown have a real cost; do not
extrapolate these measurements to a quiet or network-bound update.
Runtime rebuild/cutover operation counts and promoted installed behavior
still need their separate scenario proofs.

- `libs/update-telemetry/src/update_telemetry/run.py` already separates UTC
  event timestamps and monotonic run durations, redacts sensitive fields and
  owns the update event lifecycle. Its event/status JSON is not a terminal
  line stream to prefix.
- `plugins/agent-worktrees/src/agent_worktrees/launch_progress.py:1-51`
  already owns launch preparation progress and heartbeats on stderr.
  `test_launch_progress.py:14-88` covers visibility, quiet persistence,
  failures and ordering.
- `worktree-manager/src/worktree_manager/engine_progress.py:1-35` forwards
  engine progress; `test_launch_preparation_progress.py:110-165` covers
  progress before process completion and version-skew detail.
- `plugins/agent-worktrees/src/agent_worktrees/output.py:1-161` owns safe
  stdout/JSON and generic terminal helpers. It must not become a source of
  human prefixes in machine-readable stdout.
- `libs/installer-engine/installer-engine.ps1` and the existing native
  installer/service helpers are the shell-side reuse boundary. Do not call
  Python or write a telemetry event/lock once per human output line.

## Proposed presentation and work-reduction rules

Use one human vocabulary: `starting`, `running`, `done`, `current`, `skipped`,
`disabled`, `warning` and `failed`. Preserve structured outcome/exit-code
semantics even if the renderer uses a shorter display label.

```text
[12:00:01.120Z] update/agent-index/server starting [elapsed 0.0s]
[12:00:04.220Z] update/agent-index/server resolving dependencies [elapsed 3.1s]
[12:00:05.420Z] update/agent-index/server done [duration 4.3s]
```

The timestamp is wall-clock correlation; the suffix uses a monotonic clock.
An arbitrary child line reports the containing operation's **elapsed** time,
not a fictional execution duration for that line. A completion line reports
the measured **duration** of its named operation. Native shell adapters keep
their context in-process; no additional reporting process or JSON rewrite
is spawned for each line.

Only the outer human renderer frames a child line. Canonical child records
must not acquire another timestamp, repeated `reconcile:` prefix or
contradictory severity. Machine JSON is parsed before any human rendering.
Quiet/protocol modes remain clean, and redaction precedes persistence and
display of potentially sensitive diagnostic fields.

Prioritize:

1. Retain per-target reasons, phase boundaries, exit status and completion
   duration so the two Codespaces gaps are attributable.
2. Replace the 130-record supervisor diagnostic expansion with one accurate
   warning/result and accessible structured detail, without hiding a parser
   failure or weakening retirement.
3. Investigate the exact Bridge and Codespaces pass-two predicates before
   removing repeated actions. A successful process exit alone is not proof
   that desired runtime/service/command state is current.
4. Verify #6598's existing admitted-construction reuse in Codespaces rather
   than implementing another reuse mechanism. Reuse same-attempt plans and
   immutable fingerprints only where source identity, policy and health
   evidence still bind.
5. Measure equivalent healthy, changed-version and first-touch runs after
   implementation. Report work avoided, not merely shorter output.

### Later source alignment

The ledger above remains evidence for its named historical source and run,
not an assertion that every old repetition persists. In addition to #6598's
construction reuse, merged #6836 now defers installer-local finishing and
performs one required final project-command/profile integration pass against
the selected payload. Merged #6862 stops runtime and local mutation after
required payload acquisition fails. Preserve and verify those owners' work;
do not duplicate it or count it as a newly implemented optimization here.
Neither a source merge nor a passing formatter fixture measures the installed
flow's execution count or duration.

### Final integration work counts (isolated native source fixtures)

Verified the existing #6836 optimization rather than adding a competing
finishing path. The real PowerShell `Reconcile-Binstubs` function is invoked
twice with the same synthetic runtime callback. With ordinary standalone
installer behavior it invokes that callback twice; with the unified updater's
explicit local-integration deferral it invokes it zero times. This removes
two installer-owned reconciliation subprocesses when those two call sites
are reached; it does not remove the final required reconciliation. The
native Bash fixture independently observes the same two-to-zero count.

The aggregate-update fixture separately records the real orchestration's
collaborator calls on Windows:

| Initial runtime state | Engine installer calls | Final command passes | Final profile passes |
|---|---:|---:|---:|
| Current version | 0 | 1 | 1 |
| Different version | 1 | 1 | 1 |
| No deployed version (first touch) | 1 | 1 | 1 |

All three require the ordering `modules`, `runtimes`, `commands`, `profiles`.
The first-touch case and exact installer-count assertion extend
`test_update_quick_skip.py::test_local_finishing_runs_once_after_runtime_work`.
The native deferral fixture is
`test_terminal_refresh.py::test_native_powershell_installer_defers_both_reconcile_passes`.
Companion native fixtures preserve standalone profile work and require a
verified Manager result; final-integration fixtures retain required command
and profile failures instead of converting them into a successful no-op.

These are invocation-count proofs with external work replaced by synthetic
callbacks, not timings of real command migration, package construction or
profile publication. The two complete orchestration/integration modules
passed 42 cases on Windows in 12.89 seconds and 42 on native Linux in 14.92
seconds. Six selected native PowerShell deferral/compatibility cases passed
in 13.68 seconds; the two native Bash count cases passed in 5.26 seconds.
Those are validation costs, not claimed update savings. No installed updater
or service lifecycle was invoked. Equivalent historical/current end-to-end
durations and promoted installed operation counts remain separate,
unresolved evidence.

### Codespaces admitted-reuse work counts (isolated native fixtures)

`test_installer_reuse_work_counts.py` exercises the actual Codespaces native
venv/package functions, module-launch health probe and canonical attached
construction/retention adapter against a real temporary Python venv.
Admission is not replaced with a Boolean stub. A matching valid completion
marker earns reuse; an unhealthy entrypoint, different payload fingerprint
or malformed marker must refuse without constructing a venv, installing
packages or stamping build information. Completion and selector bytes and
timestamps, along with the interpreter, venv configuration and entrypoint,
remain unchanged in all four cases.

| Healthy admitted reuse | PowerShell | Bash |
|---|---:|---:|
| Venv-construction calls | 0 | 0 |
| Package-install calls | 0 | 0 |
| Build-stamp calls | 0 | 0 |
| UV-acquisition helper calls | 1 | 0 |

The remaining asymmetry is source-proven: PowerShell `Deploy-Venv` invokes
`Assert-Uv` before canonical admission and its reuse return; Bash performs
UV acquisition only after admission has established that construction is
needed. The extra PowerShell helper invocation is a candidate reduction,
not an implemented fix or a measured resolver/network cost. A change must
retain health and canonical admission and respect the existing native-file
reservation.

The new four-state matrix plus the existing fresh-venv SRE retry case passed
five native cases on Windows and five on Linux, with the opposite shell
skipped on each host. Both fresh fixtures exercise real venv construction,
not package installation or activation. Only the Bash branch additionally
verifies explicit incomplete-candidate abort; the PowerShell retry branch
stubs slot admission and does not exercise construction teardown.
The Linux run used the retained prepared interpreter and
reported an `asyncio_mode` configuration warning because its pytest asyncio
plugin was absent; no asynchronous test was selected. Original child,
test-item and suite bounds remain in force.

This verifies #6598's source-level reuse path without reimplementing it.
Promoted-payload identity, full clean-room provisioning, live installed
behavior and equivalent before/after update durations are still open.

### Remove the measured PowerShell UV acquisition

The follow-up moves only `Assert-Uv` below canonical slot admission and the
admitted-reuse return in Codespaces `Deploy-Venv`. The preceding four-state
baseline remains historical evidence: PowerShell's acquisition helper count
changes from one to zero for healthy reuse, while Bash remains zero. Venv,
package and stamp counts remain zero, and health/content/schema refusals
retain their exact reasons and unchanged protected bytes/timestamps.

The strengthened Windows fixture uses the actual `Assert-Uv`/`Ensure-Uv`
functions with a private, explicitly selected fake UV executable. Its log
must remain absent: even the otherwise-required `uv --version` process is
avoided. A new first-touch fixture witnesses the real, live construction
owner before UV acquisition, injects an acquisition failure, and verifies
canonical abort plus candidate reclamation. It failed before the line move
because acquisition preceded admission, then passed afterward.

The final Windows selection passed 11 cases (10 opposite-shell skips),
including all five existing construction retry/fallback modes. Native Linux
parity passed 10 cases (11 unavailable-shell skips). No deadlines were raised.
These establish work avoided and failure ordering, not a measured end-to-end
time improvement or promoted installed proof.

### Remaining read-only plan cost and selected-context validation

A paired Windows experiment exercised the real `build_plan` consumer in the
existing isolated deployment-policy fixtures. Each scenario used the same
payload/helper, interpreter, policy, initial cache and fixture files for four
calls, alternating the earlier site-initialized Python invocation and the
reviewed `-I -S` invocation. Fixture setup and byte/mtime snapshots were outside
the measured plan interval. Complete plan results, probe-action counts and
fixture bytes/mtimes matched in every pair.

| Publication scenario | Fresh helper calls | Median with site (s) | Median without site (s) |
|---|---:|---:|---:|
| Missing policy | 1 | 0.928 | 0.815 |
| Standalone | 2 | 1.235 | 1.215 |
| Managed | 2 | 1.247 | 1.279 |
| Invalid | 2 | 1.093 | 1.193 |
| Unsupported | 2 | 1.403 | 1.283 |
| Foreign owner | 2 | 1.024 | 1.044 |
| Managed, healthy runtime | 1 | 0.617 | 0.543 |

These are two samples per variant and scenario, not a statistically established
whole-update speedup. Several differences reversed. In particular, omitting site
initialization does not by itself explain a minute-long deployed reconciliation.

The explicit cross-plugin context route had another, independently reached
boundary: `_selected_runtime_root` still launched PowerShell to run the
payload-owned `resolve` operation. The original function from
`188915d95e52deaeb1bec3466d0d1e88440614a5` and the changed function were
executed in the same governed-plan fixture in original/current/current/original
order, against identical current payload and receipt bytes. Every invocation
performed a fresh validation; fixture snapshots and root-routing tuples matched.

| Selected-context consumer | Original | Portable preference |
|---|---:|---:|
| Parent-invoked PowerShell resolver commands | 1 | 0 |
| Parent-invoked portable Python resolver commands | 0 | 1 |
| Fresh validator invocations | 1 | 1 |
| Samples (s) | 2.445, 2.497 | 0.408, 0.398 |
| Median (s) | 2.471 | 0.403 |

This removes a measured shell hop, not a policy check. A separate real
same-fixture native/portable resolver comparison retained identical complete
JSON, stderr and exit status. Native-only compatibility, platform-helper
admission, exact-context routing, expected-root constraints and refusal remain
required. Both resolver producers declare UTF-8, so a locale-dependent decoder
must not corrupt a non-ASCII runtime root.

The measurements cover one synthetic, read-only selected-context consumer.
They neither time runtime construction nor establish the deployed planner's
plugin count, repeated-context count or total reconciliation duration. Those
remain separate evidence gates; no authorized host update or repair was run.

### Complete synthetic planner, without an explicit context

After #7996 merged, the actual `build_plan` consumer was measured against
thirteen configured runtime plugins in isolated Windows fixtures. Each
fixture included the current source-owned installation-context assets and
matching or changed synthetic deployment versions. The planner's intentional
Worktrees self-exclusion leaves twelve reconciliation targets; the separate
Worktrees update phase is not included. Eleven eligible `agent-*` targets
run fresh legacy-mode probes. Budget Guidance does not enter that cell-mode
contract.

Each pair used the same fixture files, fixed planning time, empty caller cache,
`save=False`, and no payload refresh or explicit installation context. The
baseline only restored site initialization in the actual isolated probe
subprocess; the current variant retained `-S`. Complete result objects and
every fixture file's bytes and modification time matched after both calls.
No process record, endpoint, production state, runtime installation or service
control was involved.

| Synthetic state | Baseline planner (s) | Current planner (s) | Current probe subtotal (s) | Fresh probes in both | Outcome |
|---|---:|---:|---:|---:|---|
| Matching versions | 5.679 | 4.605 | 4.486 | 11 | No update action or diagnostic |
| Changed versions | 4.624 | 4.253 | 4.135 | 11 | Twelve runtime actions, no diagnostic |
| Invalid cell-mode policy | 5.705 | 4.598 | 4.501 | 11 | Eleven explicit refusals; only the non-cell target remains eligible |

These are single paired samples per state, not statistically established
savings. The current planner spent approximately 97-98% of its measured time
in required fresh probes. The previous selected-context PowerShell-hop change
is not reached in this no-context fixture and must not be credited for these
numbers. Fixture preparation was outside the planner interval.

The first four-sample harness exceeded its original thirty-second item bound
while snapshotting fixture files. Sampling was reduced to one pair and
independent fixture copies were prepared with four bounded workers, retaining
all files, cases, child deadlines, snapshot assertions and refusal checks. No
deadline was raised. An initial expectation of thirteen changed-target actions
was corrected to the source's intentional twelve-target self-exclusion; the
complete changed/refused result sets were then asserted and passed.

This resolves a source-level multi-plugin measurement gap, not the reported
deployed minute-long command. It excludes front-door interpreter startup,
CLI imports, configuration/registry discovery outside this consumer, serving
health and runtime construction. A matching-version fixture is not proof of a
healthy installed no-op. Those boundaries and equivalent authorized live
acceptance remain open.

### Source CLI startup and explicit-context planning

The next contained Windows measurement exercised the real CLI `main` and
its actual adapters, not only `build_plan`. It used an unchanged source
package copied into a disposable payload layout, a private synthetic Git
repository, project configuration and thirteen matching-version payloads.
The child imported the ordinary prepared interpreter's dependencies and ran
`--project fixture pre-launch` or `--project fixture reconcile-plugins
--repo <fixture> --machine fixture --peek`. Profiling began before importing
the CLI module; fixture construction and snapshots were outside that interval.

| Source command | Outer child time (s) | Profiled import (s) | Profiled `main` (s) | Main consumer (s) | Native child operations |
|---|---:|---:|---:|---:|---|
| `pre-launch` | 3.846 | 1.343 | 2.139 | `plan_pre_launch`: 2.017 | Nine local Git reads and one fresh legacy probe |
| `reconcile-plugins --peek` | 5.961 | 1.112 | 4.536 | `build_plan`: 4.415 | One local Git read and eleven fresh legacy probes |

Both commands returned zero, one clean JSON continue result, no diagnostics
or fallback reason, and empty stderr. Every fixture file retained its bytes
and modification time. A child-process guard permitted only local Git reads
within that fixture and payload-owned read-only installation-context helpers;
it admitted no acquisition, installation, service control or execution queue.
The two cases passed within the existing thirty-second item and 300-second
group bounds. The inherited scratch `PYTHONPATH` was removed from the child
to avoid shadowing standard-library `profile`; explicit pytest root and
configuration-discovery boundaries kept unrelated temporary directories out
of collection.

The pre-launch profile found five machine-registry loads totaling 0.708
seconds, three configuration loads totaling 0.612 seconds, and one payload
fingerprint taking 0.578 seconds. These cumulative call intervals overlap;
they must not be added as independent costs. This simple configuration does
not exercise a populated operator registry or bound knowledge overlay.
The source CLI's roughly one-second import cost is measurable, but neither
this fixture nor its configuration work explains a minute-long live command.

A separate complete planner fixture supplied an explicit Worktrees receipt,
created by the actual portable stamping command only during fixture setup.
All other eligible targets retained matching legacy deployments. The current
planner took 8.786 seconds and performed eleven fresh context `resolve`
operations plus eleven fresh legacy probes. It returned no actions or
diagnostics and left every fixture file unchanged; the case passed in
16.10 seconds including setup under the original item bound.

This confirms that the selected-context route is reached once for each of
those eleven eligible targets; the previous no-context measurement did not
reach it at all. It does not supply an equivalent whole-planner old-shell
baseline. The earlier single-consumer shell-hop measurements cannot be
multiplied into a claimed whole-command saving, especially with different
registry, policy, publication and runtime-health state. Actual queued apply,
fresh bootstrap, populated-registry discovery and authorized installed
healthy no-op acceptance remain distinct unresolved boundaries.

### Promoted update and fresh-entry verification

A later, explicitly authorized Windows field run used the normal repository
update entry after release promotion. The first invocation completed with
actual management EOF, exit zero, no event parse errors and a
`verified_current` run. Its Manager duration was 2600.233 seconds; the outer
capture, including entry and management closure, measured 2608.984 seconds.
The initial orchestrator had started before replacing its own payload, so
the older phase-label defect remained visible during this invocation.

| Reached phase | Duration (s) | Interpretation |
|---|---:|---|
| Manager self-update | 164.641 | Changed selected version; active on the next invocation. |
| Initial marketplace authorization | 7.139 | Read-only authorization reporting. |
| Anchor authorization | 7.938 | Separate read-only repository authorization reporting. |
| Worktrees payload refresh | 69.092 | Changed payload; not admitted runtime reuse. |
| Bulk payload refresh, still labeled authorization by the running old engine | 257.297 | Includes the other registered payload acquisitions, not authorization cost alone. |
| Runtime preflight | 30.141 | Observed phase interval, not a count of individual probes. |
| Worktrees runtime update | 327.907 | Changed version; construction and owned monitor cutover. |
| Bridge runtime update | 286.375 | Changed version; construction and owned serving cutover. |
| Other runtime reconciliation | 1403.375 | Eleven other changed runtimes, including Index's existing server role. |
| Anchor synchronization | 5.468 | Safely refused a stale-index fast-forward; not silently repaired. |
| Pivot pruning | 2.875 | Existing integration work. |
| Binstub/profile integration | 35.108 | Existing final integration work. |

All thirteen runtime identities changed. The runtime-update phases account
for 2017.657 seconds, approximately 77.6% of the Manager interval. These
observations locate the cost of this changed-version run; they do not prove
that construction, admission or health work can be removed. Nor do they
establish an equivalent before/after reconciliation speedup.

After management EOF, all thirteen selected, deployment and payload identities
matched; Containers retained its declared provider-exec runtime suffix.
Worktrees `1.24.194-dev1` had identical source, payload and selected-runtime
bytes for `reconcile.py`, `installation_probe.py`, `update_runtime.py` and
`update_acquisition.py`. Independent health reads confirmed Bridge
`0.9.111.dev1` ready and not draining, and Index `0.10.63.dev1` serving its
already-declared role. This proves those installed bytes and serving versions,
not the successful execution of a later no-op planner.

The single authorized verification invocation exercised the new fresh update
entry instead. It advanced Manager from `0.6.77-dev1` to `0.6.78-dev1`, then
its durable request refused engine repair:

```text
Engine repair refused: Engine repair has no captured incumbent ownership
```

The actual incumbent deployment manifest had schema 3 and service
`agent-worktrees`, but no `commandBinding`. The selected Manager's
`update_bootstrap._legacy_binding` requires that dictionary before origin
selection in the no-context route. The owning read-only request-status command
reported a completed request with exit one. Nevertheless, the repository front
door returned zero; its outer completion appeared before the request refusal
was streamed. The aggregate update-status document still identified the first
successful run. Neither that stale document nor caller exit zero proves a
successful verification.

The failed capture lasted 132.250 seconds and reached no normal engine update
phases. All thirteen plugin identities and both independently checked serving
versions remained unchanged. Manager's identity changed, and the requested
operation failed: this is not an equivalent healthy no-op timing sample.

The existing #7743 owner received the
[sanitized native evidence](https://github.com/ThomasMichon/copilot-extensions/issues/7743#issuecomment-6352516328),
including the later engine admission boundary and the separate exit/wait
discrepancy. The selected `runtime_install.py` uses `os.execv` where the outer
PowerShell bootstrap expects a completed native-child result; attribution of
the lost result to that boundary still needs an isolated native regression.
No receipt was invented, fence cleared or additional update run.

Presentation also remains open: the first capture framed every human line but
retained Worktrees and Codespaces decoration; the new fresh bootstrap emitted
unframed progress and a duplicated PowerShell information record in encoded,
redirected capture. These are actual producer boundaries to qualify, not
permission to format protocol output or hide the failed request.

The existing #7743 owner subsequently reproduced the Windows final-handoff
completion loss in a native fixture and merged the wait/stdio repair in
#7987. Its reported native coverage includes two-cycle completion, UTF-8
streams, DEVNULL input, distinct exit statuses and window/focus observation.
That source merge does not retroactively turn the failed field verification
into success; containing release and newly authorized installed suite
completion are still required.

The bounded bootstrap-owned presentation follow-up merged in #8020. Its own
explicit progress, warnings and diagnostics now go to UTC-correlated stderr
text: PowerShell reports elapsed time from an in-process stopwatch without
host-information duplication, while Bash labels elapsed timing unavailable
before a portable provider exists. Actual native capture and local-Git fixture
coverage qualified those producers without running an installed update.
Prerequisite child output and thrown shell errors remain separate producers.
The following Manager `update_bootstrap.prepare_update` repair also precedes
`UpdateRun`. A locally qualified candidate frames that prefix's own messages
and exact repair exit without capturing its children. A redirected native
Windows control also found that the existing `_wait` lost child output while
preserving its exit; explicit existing standard descriptors corrected that
source boundary. The wait loop, flags, source and ownership rules are unchanged.
The focused 88-case cohort passed on both platforms, but additional optional
native-policy cases still exceeded their original item deadline and the
candidate remains unpublished. These are retained source boundaries, not
evidence of completed installed acceptance or permission for another update.

### Native self-stage copy cost

The optional Windows repair-policy timeout was attributed beyond initial
lifecycle admission to the shared PowerShell self-stage `Copy-Item`. A stack
sample reached the native-child wait within one second; native progress then
stopped at copying, with 245 of 684 files staged at the original outer item
deadline. This does not attribute that entire elapsed interval to copying.

A separate bounded comparison used the exact same 684-file input and fresh
private destinations. Provider copying reached its 20-second command deadline
at 659 files. The candidate source's native copier finished all 684 in 2.281
seconds, retaining equal file bytes, modification times and modes. The provider
duration is censored, so no exact completed-run speedup is claimed.

The source candidate uses the existing native-copy pattern in all 13
byte-identical PowerShell self-stage blocks. It retains complete inputs and
uses the old provider for reparse points; native 5.1/7 controls establish
special-file, metadata and error parity. Staging location, re-exec, CWD,
watchdog, ownership and deadlines are unchanged. #5820 still owns the separate
version-slot/whole-lifecycle redesign.

After that correction and native cleanup of the exact private fixture root,
the first actual repair-policy case passed within its original 30-second
limit. The optional two-cycle provision/update case still exceeds that limit;
the full native lane and installed no-op proof remain open. Neither the
bounded copy improvement nor the 35 passing staging/CWD/watchdog cases proves
a faster complete host update, fewer required installation checks or healthy
installed no-op behavior.

### Explicit context-reader hints

The scoped-repair preflight still has to validate the receipt, resolve current
governance and check publication at the same boundaries. Those checks were
re-entering native shell readers even when Manager supplied an exact existing
interpreter for the canonical SDK. POSIX now honors that existing hint before
early reads; Windows uses it through the existing hidden-process transport.
Unsupplied-interpreter readers are retained. No owner, source, activation,
publication or generation assertion is omitted.

The original Linux scoped call exceeded its 30-second item limit; its corrected
call completed in 10.10 seconds and all 20 scoped/routing cases passed. Windows
additionally reused native copying for the structured payload; its first actual
scoped case completed in 26.31 seconds including setup and cleanup. These are
bounded fixture results, not equivalent whole-host measurements. PowerShell
transport controls retain complete arguments, Unicode, native fallback,
diagnostic stderr and exact failure exits without inventing an empty stdout
record.
The complete current Windows scoped cohort passed 28 cases in 196.04 seconds;
the shared staging/CWD/watchdog cohort passed 35 with one platform skip in
136.09 seconds. Neither result relaxes the still-failing legacy two-cycle gate.

The operator retained the native publication gate. The remaining legacy
two-cycle case still exceeds its item budget, including after a rejected
bootstrap-locator forwarding experiment that was reverted. Its original
ordinary native arguments, signed construction-base selection and child-custody
contract remain unchanged. Complete current native qualification and installed
healthy/no-op proof are still required.

### Accepted-source legacy repair and fixture cost

Synchronizing the accepted #8010 capture fix did not clear the Windows
provision/update two-cycle gate. A passive original-command measurement
recorded 5.272 seconds of setup and a 16.563-second first native cycle before
the second exceeded the original item budget. A later passive diagnostic
completed two native cycles at 10.922 and 11.172 seconds, then exceeded the
same item budget during fixture cleanup. The variation is not evidence
that output capture alone causes the gap: instrumented native phases also
expanded across admission, interpreter selection and construction.

Sparse copied-script markers locate work but change the scripts and are
not acceptance measurements. One such trace measured full staging copies
at 2.304 and 1.931 seconds, with prior-stage cleanup at 0.049 and 0.646
seconds. This does not establish an EOF hang, dominant cleanup cost or
permission to remove a repeated admission check. An independent same-input
experiment found directory-info enumeration slower (2.907 versus 2.500
seconds over all 685 files); that alternative was not retained.

Windows fixture preparation has a separately measured, bounded correction:
four native-copy workers copied all 685 files in 0.828 seconds versus 1.968
seconds sequentially, preserving file bytes, times and modes. The fixture
still uses complete separate trees and normal link/ignore/overwrite
semantics, waits for all workers, surfaces native errors and restores
directory metadata after copying. Targeted controls additionally cover
empty directories, linked targets and independent copied files. This only
reduces fixture work. The unchanged two-cycle native gate still fails;
publication and installed healthy/no-op qualification remain blocked.

The accepted source also adds an automatic-cell historical-snapshot case.
Its fixture's remaining ordinary `shutil.copytree` consumed 14.500 seconds
before native repair. Reusing the full-input fixture copier reduced that
operation to 0.719 seconds; the original case passed in 24.34 seconds
including setup and cleanup. This changes only fixture copying, not snapshot
provenance or selection of current executable repair code.

The synchronized Linux repair/scoped/staging selection passed 70 cases with
25 platform skips in 177.60 seconds. Windows staging/CWD/watchdog passed
35 with one platform skip in 160.44 seconds. Its current 30-case scoped
cohort is not fully accepted: an independent run still exceeded the
unchanged item limit during an earlier automatic-cell case's fixture
cleanup. The older 28-case pass precedes the accepted added source cases.
All seven current Windows historical-snapshot cases passed in 137.90 seconds.
The final snapshot/automatic-snapshot selection also passed eight cases on
native Linux in 40.73 seconds.

A separate diagnostic profile of actual scoped runtime work before the
existing construction fence recorded 395,969 calls in 2.043 seconds.
Its nested work included 28 receipt validations, 1,657 canonical path calls
and 3,812 native final-path queries; native final-path lookup accounted for
0.535 seconds of self time. These are instrumented internal counts, not
unmodified wall-clock acceptance, independent governance boundaries or
whole-update savings. They identify further source work to account for;
they do not authorize receipt/path caching, skipped validation, changed
publication custody or a competing admission algorithm.

### Scoped publication cost follows nested custody checks

The follow-up source audit maps that profile to the actual Worktrees
`Governance` consumer rather than treating internal call counts as separate
authority boundaries:

| Reached operation | Required work and boundary |
|---|---|
| `Governance.read`, initially and in `held`'s recheck | Validate the receipt, resolve effective installation mode and compare the selected root, context and install generation; then check publication policy and custody. |
| `Governance.held` before the recheck | Borrow the existing management-owned namespace locks when a publication ticket is present; do not acquire replacement locks or release the borrowed locks. |
| Each `borrow_runtime_publication` | Resolve publication identity before inspecting the ticket; validate both lock owners, matching publication purpose, native process birth and common owner PID; resolve identity again; then recheck owner records and native process birth. |
| Each publication-identity resolution | Resolve effective deployment policy, validate the explicit receipt and root/generation agreement, and resolve payload provenance for the deployment owner. The mode and policy resolvers themselves call receipt/provenance helpers. |

Thus the three profiled custody borrows are the initial publication check,
the held-admission borrow and the later publication recheck. Each borrow's
two identity resolutions surround observations that can change. Receipt
validation inside mode, activation and namespace resolution accounts for
additional nested work. The existing validation scope shares file digests
only within its owned call scope; it does not cache physical path identity,
deployment policy or custody decisions across these boundaries.

The physical-path helpers also perform nested canonicalization, including
canonicalization inside equality and containment checks. That identifies
implementation cost, not proof that a fresh filesystem observation is
dispensable. No publication, receipt, canonical-path or lock algorithm was
changed in this audit. Any shared-SDK optimization needs its owning
foundation work to preserve replacement, reparse-point, receipt-generation
and live-custody refusal behavior. The Windows two-cycle and full current
scoped gates remain blocked; no unchanged native retry was used as evidence
of progress.

A separate disposable Windows comparison tested whether compiling the
ordinary-file traversal would pay for itself at this boundary. It copied
all 684 files and 47 directories selected by the repair fixture's existing
ignore rules, with equal bytes, file times and modes. The retained
PowerShell helper took 1.513 seconds before and 1.329 seconds after the
comparison. Sequential compiled traversal took 1.415 seconds; four-worker
compiled traversal took 0.661 seconds, but cold compilation itself took
0.728 seconds. Its 1.389-second cold cost did not beat the later retained
helper, before adding the unimplemented link/provider compatibility work.
The compiler-based alternative was not adopted. This is a staging
microbenchmark, not native repair qualification or whole-update savings.

### Payload-only startup and fixture-state blockers

The actual interpreter's paired contained startup control measured a
0.500-second median with site initialization and 0.359 seconds without it.
That identifies avoidable work in payload-only admission and canonical SDK
queries, not the cost of an installed runtime. Five new actual adapter
controls failed because site initialization was enabled; the complete
corrected admission/reader selection passed 39 cases on Windows. The
readers retain their original native fallback, arguments, stdout/stderr
and exit status. No-site startup is not applied to installed package
loading or used to bypass interpreter selection or governance checks.

Windows fixture cleanup now joins four native file-removal workers in an
isolated Python child, retaining the previous 10-second child deadline.
Its CWD and temporary directory are outside the deleted tree. Positive
link-target/root-link and negative missing-root/read-only-file controls
exercise the real removal boundary; production cleanup is untouched.

After cleanup, the full Windows scoped selection reached additional cases
and exposed six early failures. Acquisition referred to a temporary
directory inside an earlier, deleted fixture. Environment-variable changes
alone do not reset Python's process-wide temporary-directory cache. Two
ordered regressions failed before the fixture explicitly scoped that cache
to its own existing temporary directory. The containing bootstrap/fixture
selection then passed 36 cases.

The native timing discrepancy remains independent of those corrections.
One unmodified-command scoped case passed in 28.78 seconds: 4.062 seconds
setup, 23.393 seconds call (19.547 seconds native) and 0.947 seconds cleanup.
The corrected full cohort subsequently still reached the original item
limit in native execution. A diagnostic parent profile separates about
4.76 seconds of Python-side test work from a 19.922-second native command;
1.12 seconds of the parent work was the complete acquisition fixture copy.
These figures do not justify dropping the surrounding ownership checks.
The legacy two-cycle gate is also still open. Its copied-script diagnostic
places work in initial CLI admission and native startup, parent and child
admission, complete staging, signed-base selection and the canonical
runtime before the existing construction fence. Neither a dominant EOF
hang nor permission to change custody, signing, deadlines or rollout was
established.

### Replace a duplicate repair lifecycle, not its deadline

The missing-bin legacy repair selected provision followed by update. Both
actions enter the canonical lifecycle, stage the complete payload and call
the runtime handler. The second pass need not rebuild an admitted current
slot, but still repeats admission, staging and runtime admission. Ordinary
update's missing-bin refusal was the reason to run provision first.

The operator approved replacing that two-lifecycle contract with one full
admitted update. A private opt-in from Manager's captured legacy
installation allows the native update to create its missing ordinary bin
after the existing lifecycle checks, retaining the Git prerequisite and
refusing files/reparse points. It does not mint a ticket or relax the
canonical publication policy. Ordinary update without the opt-in remains
refusing, and Manager rejects an exit-zero repair without a runtime bin.
The existing full update body still owns construction, publication and
integration.

The Windows selection passed 11 native controls in 125.29 seconds, including
PowerShell 5.1/7, single-pass routing, malformed/absent admission, wrong
action/context/root, normal-update refusal and substituted-bin protection.
The complete single-pass call took 14.68 seconds. After explicitly carrying
the Git prerequisite forward, its coupled selection passed seven cases in
30.68 seconds, with a 12.88-second single-pass call. The old assertion of
two native lifecycles was intentionally revised with that design approval;
neither item nor group limits changed.

### Pair consecutive read-only transport without caching governance

Windows' hinted initial receipt validation and status read now share one
finite interpreter process. The payload-local adapter calls canonical
`main` with the original validate arguments, then with the original status
arguments, preserving each raw response. SDK validation scopes remain
separate, there is no decision cache, and the first failed action stops the
transport with its SDK failure. The native caller retains its original
receipt/root/status/generation checks and failure classification.
Publication probes, reads across staging and construction, and subsequent
revalidation are not combined or reused. Neither the canonical SDK nor its
custody algorithm changed.

The 24 reader/parser controls passed in 19.56 seconds. They check both SDK
invocations, stop-on-failure, UTF-8, no-site/isolated startup, exact native
errors, existing fallbacks and actual PowerShell 5.1/7 transport. The
complete current Windows scoped cohort passed all 41 cases in 234.85
seconds, with the original 30-second item and 300-second group bounds.
Its slowest policy call was 22.32 seconds; all original source, activation,
generation and publication refusal scenarios remain represented. These
figures qualify that source selection, not a whole-host no-op or installed
release.
