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
