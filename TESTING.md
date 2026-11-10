# Testing copilot-extensions

The manual foundation portfolio separates contained functional checks from
Windows production caller-Job loss. After the ordinary runner prepares its
test environment, `tools/qualify_foundation_lifetime.py foundation` or
`first-use` (or its `vault-command` / `vault-bootstrap` service packets) uses
the existing native test owner, renewable daemon leases and
the unchanged 900-second aggregate boundary. Only these explicit lifetime
fixtures permit durable children to leave their caller Jobs; each packet also
runs the queued-installer restrictive-Job negative and bounded-child cleanup.
Ordinary runner containment remains unchanged. Contained hook fixtures retain
their caller Job until owned work settles and do not claim Job-loss survival.
The native Windows owner samples only its own instance registry in the existing
resource-poll loop and retains birth-matched process handles through strict
daemon cleanup, within the original process-count budget. This prevents a
released fixture PID from being reused before cleanup; it neither signals a
process nor relaxes unknown or reused-identity holds for unpinned records.
Default-service acceptance uses the explicit leased native profile too:
an ordinary contained install cannot prove that its service outlives the
constructor's cleanup Job. Service checks read the selected generation's
actual ping endpoint, not the optional Scheduled Task registration.
The manual `diagnostic_stacks` input instruments only copied Windows service
fixtures with one passive traceback capture and installer line tracing. Those packets always remain
diagnostic failures, never native acceptance; rerun without it for qualification.
Diagnostic execution stops after 180 seconds; normal acceptance keeps its
unchanged 900-second aggregate. Both hold the same environment-scoped admission
lease through daemon settlement and sandbox cleanup.
The explicit `foundation-services` residual selection runs only the two Windows
service packets with those same owners and limits. It is not a pass of the full
seven-job `foundation` portfolio.
Linux daemon-scope qualification uses the actual disposable user service
manager and its bus, not a fake bus; only a manager created by that job is
stopped afterward. That does not admit a private-HOME default-service venue:
the Linux Vault default-service cases remain explicitly unqualified under
issue #9448 F9 until the venue owner supplies matching HOME/unit search paths,
bus/cgroup lifetime and authoritative cleanup. Their separate
`UPDATE_DRIVER_NATIVE_LINUX_SERVICE=1` selector is not proof of those conditions.

## Select the evidence first

This page is a reference catalog, not a sequential checklist. Read the lane
needed for the changed behavior; optional packets below do not all become
prerequisites for one contribution.

| Question | Start here |
|----------|------------|
| What does required PR/promotion testing cover? | [Unit packet](#unit-only-promotion-packet), [portfolio invariants](#test-portfolio-invariants), and the bounded Windows Resume promotion exception below |
| How do I run a focused local selection? | [Turn-key runner](#the-turn-key-runner); select the affected files/contracts rather than the whole portfolio |
| Can I obtain Windows evidence without running tests on my machine? | [Owner-only hosted Windows escape hatch](#owner-only-hosted-windows-escape-hatch); focused, explicit and independent of required CI |
| What should I do after a run times out? | [Per-test diagnosis and residual runs](#per-test-diagnosis-and-residual-runs); inspect retained progress before repeating or enlarging a run |
| How do I isolate portable integration? | [Devcontainer isolation](#optional-devcontainer-based-isolation-linux) and the declared periodic inventory in `tools/periodic-integration-inventory.json` |
| What proves a native or external consumer? | Its explicitly selected packet below, with the actual platform, launch mode and prerequisites; a portable or synthetic pass is not equivalent |
| What are the source checks? | [Lint and contract gates](#lint--contract-gates-before-a-push), scoped by the contribution policy |

Before a substantial run, name the unmet criterion, selected source/tests and
the result that would change the next action. After a timeout, identify whether
preparation, collection, test execution, a native child, cleanup or the outer
observer hit its limit. A shell read returning "still running" is not a failed
test and does not call for a duplicate launch.

If comparable attempts stop at the same boundary, profile or isolate that
boundary before repeating the broad packet. Preflight fixture inputs, platform
eligibility and faithful consumer selection before expensive qualification.
Reuse supported prepared environments only where doing so preserves the
cold-start, concurrency or lifetime property being tested.

Keep functional correctness, performance budgets and environment suitability
separate. A supported diagnostic profile or narrower selection can supply
useful evidence without passing the original full acceptance gate. Preserve
that remaining gate, real assertions, cleanup and resource bounds. Required
CI and review remain unchanged; a shared native quiet window still needs its
affected owners' agreement.

### Selected native ADO discussion resolution

The focused Worktrees packet `--test-file tests/test_ado_thread_resolution.py
--test-file tests/test_ado_pr_remediation.py --test-file tests/test_pr_readiness.py`
exercises the actual parser, claimant/target admission, native identity and effective
permission evaluator against synthetic ADO responses. It retains a real
frontend/owner process seam and concurrent consumed-ledger owners. The dedicated
frontend packet is `--test-file tests/test_native_thread_resolution.py --test-file
tests/test_native_reviewer_request.py --test-file tests/test_native_cli.py`.
Use the existing contained plugin runner; these are source controls, not live
provider or host-runtime operations.

The controls cover selected status-only PATCH, independent PR state, full
candidate/thread movement, incomplete evidence, refusal, response loss, late
completion, crash/restart, conflicting keys and read-only recovery. New/old
runtime negotiation preserves unsupported results and never calls bulk or raw
forge fallback. Required unit-only CI is unchanged. Native Windows,
installed/account/HTTP and complete three-provider lifecycle acceptance remain
separate; a synthetic pass and platform skips do not qualify them.

Selected reopening adds `--test-file tests/test_ado_thread_reopening.py` to
that Worktrees packet and `--test-file tests/test_native_thread_reopening.py`
to the frontend packet. The shared resolution fixture retains both actual
frontend/owner process compositions and concurrent ledger owners. Reopening
controls cover every supported resolved state, delivered policy, old receiver
refusal through a real process, strict numeric/boolean status evidence,
original unknown/confirmed outcomes after later active/reclosed observations
and unchanged legacy status rejection. These remain opt-in source controls,
not an expansion of required unit-only CI or live-provider qualification.

## Owner-only hosted Windows escape hatch

`windows-qualification.yml` is an explicit manual lane on a disposable
GitHub-hosted Windows image. Only the individual repository owner may submit
or rerun it, and its controller must run from the protected GitHub default
branch. Organization-owned repositories are not admitted. It is not a
Maintainer-role ACL supplied by GitHub, required PR CI, release authority,
self-hosted execution, or proof of a productive desktop/native application.

Use the source-owned client with the exact `agent-worktrees` command path
from the current session catalog. The candidate must be a commit in a checkout
whose `origin` names the same public GitHub repository. Uncommitted changes
are not test input. For example (substitute the actual command path and repo):

```text
python tools/windows-qualification.py --worktrees <catalog-argv0> --repo <owner/repo> submit --plugin agent-bridge --test-file tests/test_venue_path_length.py
python tools/windows-qualification.py --worktrees <catalog-argv0> --repo <owner/repo> submit --tools --test-file tools/test_windows_qualification.py
python tools/windows-qualification.py --worktrees <catalog-argv0> --repo <owner/repo> wait --request-id <id> --seconds 600
python tools/windows-qualification.py --worktrees <catalog-argv0> --repo <owner/repo> result --request-id <id>
```

Add `-k <expression>` to focused plugin/library or tool files. A named plugin
uses the turn-key runner's existing target resolver, including its library
aliases. `--allow-explicit-tiers` retains existing eligibility/prerequisites;
it never opts into host credentials or changes a packet's deadlines.
Omitting files is an error unless `--full-suite` explicitly selects the named
plugin/library suite or tool suite. Broad runs are an escape hatch, not the
default after a timeout.

`submit --packet bridge-http52 --source <full-commit>` invokes the candidate's
canonical capture with `--write`, a separate same-source no-write capture,
the independent contract verifier and the canonical artifact exporter.
Each capture retains its own existing aggregate clock, including managed test
environment preparation; the workflow timeout does not enlarge it.
Returned files are data: the client never applies the generated outputs.

`submit --packet native-worktrees-embody` selects only the installed public
Worktrees detached caller. The candidate must supply `test_public_embody` in
`tests/native/test_native_seed_application.py`; controller support does not
provide or qualify that source fixture. The protected controller prepares hash-pinned,
disposable Copilot 1.0.105 and PSMux 3.3.8 binaries, checks their actual versions,
and allows at most two transient download retries. One 120-second cancellation
budget per archive covers connection, response body, output and retry delays;
an individual transfer is not cut short by a separate 30-second clock.
Server retry-after instructions stay inside that same cancellation budget.
The shared PowerShell downloader permits only shorter deadlines, uses no
credentials and caps buffered archive content at 128 MiB, covering both pins.
Failed acquisition retains a request-bound
failed prerequisite result without executing the candidate's native caller.
The promotion Resume lane uses the same bounded pinned-download policy.
It checks out the helper from its protected workflow revision, independently
of the selected Dev candidate; older candidates need not contain the helper.
The controller requires anonymous access to the configured non-public HTTPS package feed
from that hosted runner. The repository owner explicitly configures
`WINDOWS_QUALIFICATION_INDEX_URL` as an Actions secret containing only the feed
URL, without credentials, query or fragment. No endpoint is committed here.
It is injected only for this named packet, not generic tests. Captured logs,
JUnit and result files redact URLs and the configured feed hostname before
upload; a separate protected post-execution step repeats this sanitization
after worker interruption. Unsafe or oversized text is omitted and cannot
qualify. Console masking alone does not sanitize artifact contents.
The packet retains the public caller's 900-second functional case,
1200-second plugin aggregate, separate 420/300/180-second preparation/native
bounds, startup limits and exact cleanup. Missing prerequisites, empty or
skipped tests and a different actual caller never qualify. This is evidence
for the recorded native versions, not the operator's installed build, attached
or new-window use, or survival across caller cleanup Jobs. New packet support
must be reviewed and promoted to the protected controller before submission.

`submit --packet native-driver-auth-wait` selects only the Driver receiver's
`test_opt_in_receiver_auth_wait_and_same_session_resume` in
`tests/native/test_native_headed_auth_wait.py`. The candidate must supply that
fixture; packet support neither supplies it nor enables production renewal.
It uses the same disposable hash-pinned binaries and actual-version checks,
but receives no configured feed secret and performs no private-feed probe.
The canonical plugin runner prepares the Driver's source dependencies and
public registry dependencies under its normal seasoning policy. The fixture
uses an isolated loopback model provider, not a productive account or model.
Its 120-second functional deadline and 180-second pytest deadline remain
unchanged, inside a 300-second group and 420-second plugin aggregate.
Only that exact passed case, its pinned prerequisites and all required phases
qualify; missing, skipped, foreign or extra cases do not. Evidence sanitization
and interruption handling are the same as the Worktrees packet. This does not
qualify other callers, pending-work retirement or positive-renewal activation.

`submit --packet native-driver-inflight` separately selects
`test_opt_in_receiver_inflight_expiry_preserves_hold` from the same
candidate-supplied fixture. Its private model response stays blocked while
the source grant expires. The exact native processes must retire, interrupted
input must not succeed or replay, and the receiver must retain a hold that
refuses renewal. It uses the same pins, public preparation, source checks,
120/180-second functional/test bounds and 300/420-second group/plugin limits,
without a configured feed. Passing the steady recovery packet does not qualify
this negative case, all pending-work sources or productive enablement.
The protected admission owner derives native preparation and sanitization
from its known packet registry, never a caller-supplied flag or name prefix.

`submit --packet native-driver-observation-loss` separately selects
`test_opt_in_receiver_sdk_observation_loss_preserves_hold` from that fixture.
After the isolated model provider observes the actual controlled request, the
fixture closes only its authenticated SDK/control stream, not the public
broker connection. The original observation-loss hold must survive source
expiry and exact native retirement; a fresh lease must not create another
incarnation or replay the interrupted input. This uses the same pins, public
preparation, sanitization and 120/180/300/420-second limits, without a feed.
The in-flight packet alone does not qualify loss of observation. None of these
packets enables productive renewal or qualifies unrelated pending-work sources.

The separate `native-driver-startup-seed`, `native-driver-startup-no-seed` and
`native-driver-startup-cold-resume` packets each select one exact candidate case
in `tests/native/test_headed_extension.py`. The seed case delays observation
of the actual SDK startup event while the isolated provider holds the original
response. The real retained Bridge client must observe pending readiness
without sending, then accept one controlled enqueue after the original seed is
observed and before its turn is idle. No-seed and cold-resume cases separately
prove that an absent opening seed is not represented as idle or restoration.
All retain exact history/session identity and fixture cleanup, the same native
pins, public-only preparation, sanitization and 120/180/300/420-second limits.
None inherits qualification from a steady recovery packet, qualifies all
caller modes or changes productive activation authority.

Before any staging or dispatch, the client reads the selected protected
controller's `tools/windows-qualification-support.json`. Missing/unsupported
request, result or selection support refuses without publishing source.
Caller package versions do not establish receiver support. New controllers
reach the default branch through normal Dev review, CI and promotion.

Already-readable candidate commits need no staging. Local-only commits are
retained through one reserved append-only ref, `refs/qualification/windows-inputs`,
not per-request branches. An envelope retains the prior transport tip and
the exact candidate; its tree equals the candidate tree. Normal fast-forward
updates and bounded contention retry never force shared history, alter the
caller's branch/index/tree, or merge staging into source/default branches.
An unrelated existing ref is refused. This history intentionally retains
submitted public source; there is no automatic ref cleanup. Provider rejection
of this namespace is an explicit unsupported transport, not permission to
create a different ref silently.

The fixed-ref envelope push uses the native mediated-push marker in that
child's environment only, so older worktree PR hooks admit the transport
without disabling local hooks. Its command boundary rejects branch, force
and deletion refspecs and suppresses automatic tag following. No installed
hook, shared Git configuration or ordinary source-push policy is changed.
Rejected pushes retain their bounded diagnostic with the original request.
An exact Git contention rejection permits same-request recovery after an
interrupted reconciliation read; each replacement envelope retains the prior
attempt's identity and diagnostic. An absent or uncertain effect is not
permission for automatic replay, even when the shared ref has advanced.
Recovery carries the original history requirement into every append read
and contention retry; a deleted or rewritten ref is not a new transport.
For first-ref contention, the observed competing tip becomes that requirement;
recovery never treats a missing competing tip as permission to recreate it.
The first history anchor is saved before its reconciliation fetch and retained
across interrupted operations and later resumes, not just retries in one process.
Pending observations are saved before fetching and reconciled before another
remote read. The retained anchor advances only to verified descendants or the
confirmed envelope, so rewinding to an earlier retained ancestor is also refused.

The client emits a request ID before effects and stores its state under the
current worktree's Git metadata (`windows-qualification/<id>`). `status` and
`wait` resolve that same run, not the latest successful workflow.
`resume --request-id <id>` may finish a confirmed original transport and its
not-yet-attempted dispatch. It never repeats a possibly accepted dispatch.
An original transport that remains unobserved, or an unknown dispatch with no
run yet, stays unresolved; do not submit a replacement ID to hide uncertainty.
Local state operations use a crash-releasing lock for that request only;
another live operation reports busy instead of duplicating a resume. Wait
releases it between polls. Independent requests still use shared-ref CAS.
Preserve needed records before normal worktree retirement.

`result` downloads the uniquely named run/attempt artifact, validates request,
source, trusted control, selection, Windows platform and phase identity, and
returns the manifest. Actual test identities, failure/skip reasons, phase
progress/timing, available JUnit, bounded logs and named-packet outputs are
retained in Actions artifacts for seven days. Empty, skipped, failed, timed-out,
unsupported or partial selections never become unconditional qualification.
Python child standard streams use UTF-8 regardless of the ambient Windows
console encoding, so non-ASCII failure diagnostics retain their phase logs.
The result command exits zero only for qualification, one for a returned
failed/incomplete result, and two for an unavailable or invalid result.
Artifacts are immutable run evidence, not patches to execute or apply.

Tool execution imports the selected candidate's canonical containment and
environment helpers before pytest, keeping its sandbox roots aligned with
that source's test policy even when the protected controller is older.
A tool pytest exit 5 with an actual empty JUnit report returns `incomplete`
with `incomplete_reason: empty-selection`. Missing JUnit and other failing
commands remain `failed`; neither outcome returns qualification success.

## Per-test diagnosis and residual runs

A timeout is not a default request for one more run with twice the allowance.
Read the existing run log first: each named test's outcome and setup/call/
teardown duration, the last started but unfinished item, and separate preparation,
collection, native-child and cleanup costs. Do not rank tests by a whole-packet
duration or infer CPU contention without contemporaneous measurements.

Identify the costly cases and make them smaller while preserving their actual
assertions and consumer properties. Common targets are repeated fixture
construction, copying unrelated source, repeated large inputs, and real sleeps
used only to test deadline arithmetic. Keep real OS, cold-start, concurrency
and lifetime behavior where those are the property under test; replacing them
with mocks would answer a different question.

It is valid to exclude confirmed passing independent tests from another run
and execute the failed, unfinished or unstarted remainder within the existing
bound when relevant source, fixture and environment inputs still match.
Use the runner's supported selectors, check the resulting test identities,
and retain the original selection plus passed/remaining coverage. A pass needs
its required phases, including teardown. Anonymous dots or a summary count
cannot establish which tests are safely reusable. Changed inputs need an impact
assessment, not automatic reuse or an automatic full rerun.

Do not claim that disjoint runs prove one stateful installation sequence,
ordering/stress property, cold aggregate budget or other indivisible flow.
Such tests need shorter internal preparation/operations or a separately
justified design change. Required exact-source CI and acceptance gates still
apply; a residual diagnostic is not a fabricated monolithic pass.

Inspect named progress during a long run instead of waiting many minutes for
its final exit. Retain timing/progress through the existing runner/log owner
so a timeout leaves useful evidence. The turn-key runner's `--junit-dir`
retains requested per-group JUnit, but a killed run may never finish that XML;
do not promise incremental phase data it did not record. If durations, named
outcomes or the last-started item are absent, report that precise logging gap
and arrange those records for the next justified run, rather than inventing
numbers or repeating the same blind packet. Keep preparation and cleanup out
of test-call timing while retaining their total cost.

Use concrete action names such as "measure fixture copying" or "test Windows
Resume", not an unexplained "qualification round". These are ordinary
within-scope decisions, not a new per-command approval ceremony. Preserve
explicit attempt/resource limits. A genuine indivisible cost may justify
requesting a revised bound, but only after this diagnosis; larger deadlines
must not substitute for bounded tests and timely CI feedback.

## Focused packet catalog

The manual `installation-context-full.yml` portfolio `mux-continuity` runs the
native Resume consumer packet on disposable hosted Windows with digest-pinned
PSMux 3.4.0. It covers cold Open, reattachment to the same pane/child,
independent attachment preservation and fresh Driver/Worktrees/Manager
listings. JUnit retains consumer timing properties. `resume-pane` retains its
PSMux 3.3.8 compatibility packet; neither lane authorizes shared-host probes
or installation. Dispatch at the exact published candidate ref and retain
the run's source SHA, not merely its green name.

For contained Windows post-tool entry diagnosis, set
`AGENT_WORKTREES_PROFILE_ENTRY=1` and select `prepared_post_entry_stage_profile`
in `tests/test_post_tool_shell.py`. For a fixed-fixture A/B/A comparison, select
`prepared_post_entry_baseline_comparison` with `AGENT_WORKTREES_PROFILE_BASELINE`
set to a full lowercase commit ID and `AGENT_WORKTREES_PROFILE_BASELINE_REPO`
to its readable source checkout. Only private fixture copies are swapped;
the baseline checkout is read through `git show`, never modified. Both shells
retain real receiver/count assertions and the ordinary test deadlines.
Stage mode reports each library dot-load and per-call samples for conversion,
membership, bounded reads, reminders and sending. Nested function spans are
inclusive; do not sum them. Select `prepared_json_engine_retains_shell_semantics`
for JSON value parity and the older-engine command fallback without enabling
profiling. Neither these diagnostics nor their finite timings
establish installed or whole-roster throughput.
The stage profile, baseline comparison and prepared cold-process tests emit
their reports with ordinary pytest capture enabled; no `-s` or `-rP` is needed.

The Windows pre-tool packet selects `native_entry or json_engine_parity` in
`tests/test_pre_tool_native_entry.py`, with `COPILOT_TEST_HOOK_PRE_PYTHON`
naming the contained runner's dependency-ready interpreter. Its admitted
policy fixtures retain the complete managed JSON module but isolate membership;
`registered_pre_tool_entry` in `tests/test_managed_session.py` separately
exercises the unchanged registered command and actual prepared membership,
raw-input forwarding and revocation. The PS7 decoder comparison reports
cmdlet/native/cmdlet first-use samples after assembly-only admission preparation,
without utility-cmdlet preloading. It also rejects JSON cmdlet use by the
production decoder and verifies structured-value and malformed-input parity.
Child/item/group limits remain 10/30/300 seconds. These finite synthetic
measurements are not installed, composed-hook or productive-throughput proof.

For prepared Windows file-local writes, `prepared-membership-native.yml` runs
`registered_file_local_write_reuses_original_guard_facts` from
`tests/test_managed_session.py` in disjoint safe/admission and fallback packets.
The controls use actual membership and guard-map publication for legacy and
installation-cell scopes, retain protected-write decisions, and cover current
metadata, unsupported shapes, incomplete maps and changed guard/reader sources.
The safe/admission packet also covers complete prepared all-read batches using
the same native envelope validator as writes. It checks legacy and selected-cell
entries, mixed read kinds, batch-over-single precedence, working-directory
context, 128/129 calls, later protected/unknown/shell/malformed members and
extra keys that shadow dictionary metadata. Missing/older prepared evidence
and missing or changed normalizer contracts retain byte-exact policy input.
Real-worker comparisons cover the read no-op and a later protected-write deny;
the original child/item/group bounds remain unchanged.
Native batch cases prove all-call admission with mixed reads/writes, per-call
arguments, batch-over-single precedence, the 128-call bound and a later
protected/foreign member defeating an earlier safe write. The separate
structured packet covers the direct-plus-entry alias union, JSON arguments,
deduplication and 128-entry/target limits, with late protected/foreign/nested
targets, incomplete entries and fragmentary patch fallback. It also compares structured
writes with the real original policy in legacy and selected-cell fixtures.
The disjoint positive and protected write-name packets cover each original
non-patch write alias, its protected-state target, JSON/structured arguments
and heterogeneous batches. Real-policy comparisons in legacy and selected-cell scopes run in
their own packet, retaining the same child/item/group limits.
Disjoint patch-positive, protected, fallback and real-policy packets cover
object/JSON/freeform input, every original Add/Update/Delete/Move header,
CRLF, all-field/direct/structured union and mixed native batches. They retain
both move endpoints, byte-exact whole-event fallback and real protected-state
denials in legacy and selected-cell contexts. Exact 131072/131073-character
field and 128/129-target boundaries, combined-union overflow, malformed,
fragmentary, targetless, non-object JSON, foreign/nested/anchor and protected
freeform cases run without changing the existing child/item/group limits.
The commented-JSON regression preserves strict-original/freeform behavior
when a permissive shell decoder would otherwise discard a protected header.
Fixture-only phase markers report shell command entry, script entry,
membership, native proof and policy entry on a native-write child timeout;
they use the already-captured stderr pipe instead of opening a file per stage.
Only complete, recognized marker lines are removed from successful stderr;
genuine errors, blank lines, malformed markers and truncated output remain.
Registered entries assert that membership-marker opening precedes marker JSON
decoding, so a similarly shaped content reader cannot stand in for that stage.
They never retry the child, extend its ten-second bound or classify the
unobserved stalled phase as a known flake.
Invalid
envelopes and read-only batches retain original policy; installation-cell
positive and protected batches use the same actual preparation.
The `real-policy` cases compare the same registered entry and fixture with the
new contract and an older read-only contract, using the real caller-owned policy
worker rather than a timing stub; they also require a real protected-state deny.
These are finite source samples, not latency distributions or the 1-2 second
whole-hook/throughput acceptance. Child/item/group limits stay 10/30/300 seconds.
The `batch-read-only-attribution` cases retain the unchanged packaged contract
while comparing phase-free/read-attributed/phase-free calls and an attributed
later protected write through the real worker. Both Windows shell engines run
in legacy and selected-cell fixtures. Successful output records the executable
hash and actual child version, registered-command/source hashes, outer elapsed
time and same-occurrence monotonic command, script, admission and read/source
proof phases. Only fixture copies are instrumented; the existing fixture runtime
selector and context-validator substitutes remain, so this is not installed
runtime qualification. Phase times include their instrumentation, and the
outside-command startup/exit envelope is not isolated startup. Ordered controls
do not establish exact subtractive attribution or a latency distribution.
The boolean-only admission controls retain bounded command-entry, helper,
input, admission and output phases; a timeout reports the partial phases
without retrying the child or relaxing its original limit.

`python-selection-native.yml` supplies real Python 3.9 and supported Python
side by side on a disposable hosted Windows runner. Its `TEST_PYTHON39` input
names the existing 3.9 executable explicitly and is version-checked before
selection; an invalid supplied prerequisite fails instead of skipping.
The existing Worktrees first-touch admission test reaches its isolated binding
receipt through PS5.1 and pwsh. The Procutil `real_python39` cases use the
unchanged daemon helper with actual PATH discovery, covering old-first/
supported-later selection and unsupported-only refusal, native Job adoption
and original helper cleanup. Both groups must produce all six passing JUnit
cases without skips. The synthetic version controls remain separate.
This is source-consumer qualification, not an installed runtime, public PATH
repair, a Python-free OS, production daemon launch or full setup acceptance.
The ordinary focused hosted controller does not provision this old interpreter.

Dispatch's `--test-file tests/test_pool_startup_client.py --test-file
tests/test_ordinary_pool_startup.py --test-file tests/test_workspace_continuity.py
--test-file tests/test_pool_placement_client.py --allow-explicit-tiers` packet
checks the ordinary startup observation budget through governed preparation.
Its actual HTTP regression delays the original relationship owner beyond the
baseline ten-second read timeout and requires one allocation and bound request
before native creation. Other controls preserve longer/disabled reads, legacy
preparation and unrelated request budgets. Owner/native responses are fixtures;
this is not installed-cohort or full running-lifecycle acceptance.

Dispatch's `--test-file tests/test_submission_consumer.py --test-file
tests/test_submission_preparation.py --test-file tests/test_submission_readiness.py
--test-file tests/test_submission_advancement.py --test-file
tests/test_submission_resubmission.py --allow-explicit-tiers` packet exercises
ordinary protected completion, exact proposal custody, nonfinalizing readiness,
rollback refusal and rejection/resubmission. It covers the actual task HTTP route
and registered HTTP MCP completion operation, concurrent calls, status-change
fences and retained manual-assignment incompatibility. Its real HTTP client
control delays the owner observation beyond the baseline ten-second read budget,
while separate controls preserve longer selected and unrelated request timeouts.
Worktrees observations
remain synthetic owner boundaries; this packet does not qualify an installed
writer cohort, stopped-native recovery or permanent finalization.
The ten controls in `tools/promotion_unit_tests/test_submission_consumer.py`
separately admit actor-free retry/order/failure logic with production coverage;
database, thread and transport controls remain outside required unit-only CI.

Worktrees' opt-in `--test-file tests/test_post_tool_shell.py --test-file
tests/test_hook_ipc.py --test-file tests/test_status_monitor.py
--allow-explicit-tiers -k "native_mutation or mixed_batch or
registered_post_hook_reuses_original_prepared_membership or
registered_read_demand_is_send_only or handoff_wake_kind_actually_wakes"`
packet exercises real registered Windows PowerShell 5.1/7 entries with original
prepared membership, interpreter/resident traps, structured-edit and patch-move
invalidation, missing/old/stale/closed discovery and a non-replying peer.
Native batches additionally cover complete-batch admission, original event/
target normalization, shared target overflow, read-only idle demand and
unsupported batches retaining their original registered path.
Original IPC controls cover exact generation/schema/deadline/wire bounds and
the running monitor's real invalidation wake and unadmitted refusal. These are
isolated source consumers, not installed, complete shell-command or throughput
acceptance; required exact-source unit CI remains separate.

The same packet's `shell_mutation` selection covers original argument and
shell/Git parsing, while `registered_native_mutation_is_send_only` includes
`shell-` cases for single-shell notifications. Real PowerShell 5.1/7 entries
exercise JSON/raw/fallback arguments, read versus mutation, fast-forward pulls,
unknown command shapes, missing/old/wrong-generation/closed discovery, wire
overflow and a non-replying peer. Running-monitor controls prove shell read
demand does not trigger a mutation sweep and unadmitted events have no effect.
The portable unit packet additionally exercises the original send-only handler
and mutation-only parser with synthetic inputs; it is not native evidence.

The same packet's `mixed_batch` controls prove complete validation before
classification or invalidation, original native/shell semantics, aggregate
target overflow, read-only no-wake and live managed admission. The registered
consumer's `mixed-` cases cover targeted writes beside shell reads,
conservative shell writes/unknown arguments, separate capability rollback,
wrong-generation/closed/missing discovery, wire overflow and a non-replying
peer. Unit-only CI adds twelve in-memory mixed-notification boundary cases and two
original-normalizer aggregation/refusal cases;
real shell/IPC/monitor execution stays in the explicit packet.

The separately path-gated `prepared-membership-native.yml` lane also selects
`tests/test_post_tool_shell.py -k registered_prepared_cell` on Windows.
Actual stamped cells, original membership/binding/current-row publishers and
the registered PowerShell 5.1/7 entry compose read/native/batch/single-shell/mixed
notifications. It checks installation-scoped discovery, changed-byte refusal,
old membership routing, missing rows, live reminder opt-out, late guidance
and non-replying peers. Warm interpreter/client and synchronous requests are
trapped; incompatible membership explicitly retains its original admission
lookup. Original 30-second item, 10-second child and 300-second group limits
remain unchanged. The Windows production sender shares a 100-ms allocation/
connect/write ceiling, subtracting elapsed time from both waits. Controlled-clock
and transport-adapter tests execute the real sender/encoder to prove the
remaining connection/write allowances and refusal after budget exhaustion;
these are deadline arithmetic controls, not native delivery/performance samples.
The uninstrumented cold-process tail cases run three separate shell processes
per group across four fresh fixtures per shell, retain every real receiver/
accounting assertion, and report entry and complete-process durations.
These 24 observations are not a p99 or worst-case distribution. These are isolated source consumers,
not installed, cold/directory/POSIX/recovery, composed or throughput acceptance.

The path-gated, manually dispatchable legacy-monitor native lane also runs
`test_anchor_write_guard.py -k braced_parameter_boundaries_match_native_bash`
on Linux and Windows. Nine native Bash controls compare actual expansion and
command boundaries with pre-tool anchor denial and post-tool invalidation;
the Git function only prints a marker, so no real commit is attempted.
Quoted/escaped braces, nested command/arithmetic/parameter forms, backticks,
ANSI quotes and a read-only control are included. The explicit lane requires
native Bash rather than WSL or skipped prerequisites. The original 30-second
item, 180-second group and five-second Bash-child bounds remain finite.
This is source consumer/guard parity, not installed or throughput acceptance.

Worktrees' opt-in `--test-file tests/test_companion_pr_associations.py
--test-file tests/test_worktree_status_bundle_cli.py --test-file
tests/test_pr_read_context.py --test-file tests/test_pr_candidate_snapshot.py
--test-file tests/test_pr_observation_events.py --test-file
tests/test_pr_config_foreign_repo.py --test-file tests/test_pr_related_binding.py
--test-file tests/test_worktree_status_compute.py --test-file
tests/test_lazy_dispatch.py --allow-explicit-tiers` packet exercises the
declared optional association fact through actual private tracking files and
current/frozen-prior parser processes. It covers canonical empty versus
unknown/omitted evidence, scope/incarnation/reassignment, typed record state
(no recorded lifecycle or PR claim qualifies provider openness),
shared fresh configuration acquisition, independent cached core facts,
three-provider bindings and unchanged strict parsing. The existing passive
snapshot supplies a separate effective endpoint; no test acquires real credentials,
contacts a real forge or starts a real resident. Cleanup remains caller-owned and
verified. This is isolated source/process qualification, not installed/native
performance, UI point-read or full live topology acceptance. Required unit-only
CI is unchanged.
The focused endpoint regression extension also selects
`tests/test_pr_observation_credentials.py`, `tests/test_pr_observation.py` and
`tests/test_providers.py` for actual provider construction over synthetic native
responses. Duplicate-key private YAML/registry, whitespace-normalized bindings,
fetch-captured host selection and actual emitted stdout budgets are covered.
The four publication-fixture gaps tracked in #10408 remain separately scoped;
their explicit exclusion does not establish an unfiltered provider-suite pass
or weaken required CI.

Manager's opt-in `--test-file tests/test_companion_head_observation.py
--test-file tests/test_mux_companion.py --test-file tests/test_engine_client.py
--allow-explicit-tiers` packet checks optional expected-head declaration/codec
admission, actual private Worktrees file/task receipt reads through the selected
process, independent durable/Picker facts and old-caller/new-provider compatibility.
Real Textual controls cover Refresh/reopen, narrow rendering, separately retained
lineage, source failures, rollback and late-selection fencing. A POSIX
signal-0 poll witnesses exact blocked optional-child exit on Escape (the managed
test interpreter may lack `os.pidfd_open`); Windows is skipped, and that skip is
not native coverage. These are isolated source/process controls, not
installed, native performance or the original live manual-cutover acceptance.
The required unit-only packet is unchanged.

Manager's `--test-file tests/test_companion_pr_panel_ui.py` packet checks the
recorded-PR panel at a 40-column viewport (controls stay on screen; wrapped
rows keep provider text literal plus the point-read state and record caveat)
and retirement of a blocked real point-read child on Escape, Refresh and
deselection with no engine cleanup failure. It uses the same POSIX signal-0
witness and skips Windows; it is source/process evidence, not installed or
native job-object coverage.

Manager's `--test-file tests/test_companion_pr_compat_process.py` packet runs
the real Companion association/point-read caller through the engine-client
subprocess path against the real Worktrees entry point (tracking location,
status daemon, configuration and provider fetch replaced) and a frozen prior
owner. It covers old/new caller-owner pairs, rollback before any snapshot, and
actual snapshot payload construction through the real codec for open, landed,
closed-unmerged, merged, pending, foreign, malformed, provider-error and
oversized replies, with canonical writes and credential acquisition forbidden.
It is POSIX source/process evidence, not installed or live acceptance.

Worktrees' opt-in `--test-file tests/test_handoff_head_observation.py --test-file
tests/test_lazy_dispatch.py --test-file tests/test_session_lifecycle.py
--allow-explicit-tiers` packet checks the passive expected-consumer comparison
over current private tracking and delivery files. Actual JavaScript file/task
consume functions retain independent identity when head promotion fails;
parser/dispatch, legacy/malformed/ambiguous/incomplete evidence, changed sources,
bounded non-creating reads, and separate durable-head/Picker choices are covered.
The receipt-directory discovery is limited to one exact owning worktree;
session-state reads remain exact registered IDs. These are isolated source
controls, not installed, native performance or live manual-cutover acceptance.
The required unit-only packet is unchanged.

The Windows Resume consumer packet is
`python tools/run-plugin-tests.py worktree-manager --test-file tests/native/test_resume_pane_startup.py --allow-explicit-tiers`.
It requires PowerShell 7, real PSMux and the Windows .NET Framework compiler,
without fallback or prerequisite skips on Windows. A private executable adapter
adds the runner-owned `-L` namespace to the launcher's PSMux calls; fixture
control and cleanup calls use that same namespace. The adapter restores the
backend's original PATH. A separate native executable carries the synthetic
Python owner's literal argv, stdin and exit status; it is not a PowerShell
script substituted for a Python executable. The actual hosting-admission
function is also checked before and after a real private mux probe, retaining
BOM-free JSON input despite console-encoding changes. Two small native controls cover direct and adapted
private mux startup. Four concurrent admitted
Resume plans traverse the unchanged production launcher, copied pane assets,
argument receipt and startup reader,
real ConPTY attachment, detach and owned child retirement. Cold Resume starts
with PSMux prewarming enabled and disabled carry a realistic JSON environment and literal
33-argument commands. Private engine resolution and finite Copilot setup
substitutions isolate user worktrees, credentials and model sessions. This is
native consumer qualification, not warm-server adoption, a reproduction of every field failure,
installed-runtime or authentication/inference acceptance. The source release
controller requires it for every frozen candidate, separately from unit coverage;
live enforcement depends on the independently governed Main controller.

Installed Manager TTY qualification is a separate, positively selected native
contract, not part of unit coverage:

```powershell
python tools\run-manager-runtime-acceptance.py --contract installed-tty --root <fresh-private-root> --candidate-commit <candidate-sha> --candidate-tree <candidate-tree> --source-commit <dev-source-sha> --main-before <pinned-main-sha> --run-id <run-id> --run-attempt <attempt> --output <result-json>
```

The candidate must actually be installed with a representative generic adopted
project. Its generated project binstub traverses the installed engine, registered
Manager and real terminal admission in native cmd and PowerShell ConPTY sessions.
The witness requires rendered TUI output, a visible keyboard-driven response and
clean owned exit, plus an install-success/launch-failure negative control.
Version output, process existence and synthetic ready files are insufficient.
The result binds the frozen candidate and execution attempt, with declared
source provenance kept distinct from transformed candidate bytes. Missing,
skipped, failed, timed-out, incomplete or stale results refuse promotion.
Fixture diagnostics and cleanup remain privately owned and bounded; no operator
installation, credentials, sessions or service registrations are borrowed.
Source workflow wiring alone is not activation: the preserved Main controller
requires its own reviewed adoption and exact-candidate native run.

The console fixture's `tests/test_terminal_console_fixture.py` Manager packet
checks the Toolhelp snapshot-to-handle race. Only `OpenProcess` error 87 for a
PID absent from a fresh process snapshot permits omission; access failures,
still-present PIDs and failed observations remain failures. The same file's
Windows-only control deliberately releases a snapshotted child before pinning,
using the strict fixture-owned Job. Portable controls are not native evidence.
Live descendant handles, creation-time checks and installed TTY assertions
remain unchanged.

Image attribution has a separate retained-handle exit boundary. After a failed
image query, it waits at most 250 milliseconds, capped by the remaining shared
work deadline, for that same handle to become signalled. Only confirmed exit
permits omission; unreadable live processes still fail, with the descendant
PID and snapshot name retained in the exception note. No PID is reopened and
no installed-process, input or cleanup witness is waived.
The portable `process_image_attribution` controls cover that distinction and
the deadline cap. The same console-fixture file's explicitly selected T3
`native_image_query_exit` control uses a real owned Windows process and retained
handle with an injected image-query failure; it is not proof of the precise
lifetime of a historical failing PID.

The installed-core Manager lifecycle witness is explicitly selected separately:

```powershell
$env:COPILOT_NATIVE_TEST_EXECUTABLE = '<native-Copilot-executable>'
$env:PSMUX_TEST_BIN = '<native-PSMux-executable>'
$env:COPILOT_DRIVER_HEADED_MUX_E2E = '1'
python tools\run-plugin-tests.py worktree-manager --test-file tests\native\test_manager_installed_lifecycle.py --allow-explicit-tiers --timeout 900 --plugin-timeout 900 --test-timeout 900
```

This case uses the existing 720-second execution/cleanup owner, including its
30-second cleanup reserve, inside the native portfolio's 900-second runner
bounds. PowerShell 7, the Windows .NET Framework compiler, actual Copilot and
PSMux executables and the constructor's governed feed are prerequisites; no
operator Copilot state or inference token is borrowed. Only inference uses a deterministic
loopback endpoint. Real installed project entry, Manager keyboard controls,
Git worktree creation, native process incarnation, conversation history and
private mux attachment participate. The case checks parallel Open, preserved
work after detaching the first view, normal Copilot exit and a fresh Manager's
Resume, plus the existing unusable-installed-entry negative control.
Completed turns require durable conversation history and the reply in the
terminal stream or the actual bound private pane's rendered capture. Removing
escape sequences from a redraw stream is not itself a rendered-screen witness.
The stateless harness has a real private knowledge repository, registered and
bound before New; the installed coordination-readiness command must resolve it.
Before constructing runtimes, the case requires an empty, confirmed native
mux-server cohort. PSMux liveness checks cover the machine-wide server census;
a private namespace and data directory on a shared mux host cannot guarantee
complete registration coverage. Existing or unconfirmed servers fail this
preflight, not skip qualification. Never borrow host registrations, fabricate
sentinels or stop shared servers to make the test pass.
If source preparation exhausts the fixed budget on the default temporary
volume, the runner supports a private, source-external `AGENT_SCRATCH_ROOT`.
Record the actual placement and timing; changing volumes is not permission to
increase deadlines or reuse an installed fixture.

The candidate source snapshot retains sibling plugin inputs needed by canonical
invocation materialization, but this core case constructs only Manager and
Worktrees. It cannot satisfy complete selected-suite, actual Machines readiness,
ordinary baseline-to-candidate update or required promotion-result acceptance.
Those remain separate obligations in the
[installed lifecycle contract](efforts/active/main-owned-workflow-control/installed-manager-lifecycle.md).
The whole-suite replacement fixture remains disposable-hosted-Windows-only:
its real service and Scheduled Tasks effects must not be enabled locally.
No native result is counted as unit coverage or inferred from this invocation
documentation.

The existing manual `installation-context-full.yml` workflow accepts
`portfolio=manager-lifecycle` for this installed-core case on a fresh
GitHub-hosted Windows VM. Dispatch the published source branch and optionally
provide its same exact commit as `source_sha`; a mismatched request fails.
The job reuses the bounded pinned downloader and the existing Copilot 1.0.105 /
PSMux 3.3.8 prerequisite pins, verifies checkout identity, and preserves the
900-second runner and 720-second case limits. Its 30-minute job envelope also
covers checkout, tooling, prerequisite acquisition and artifact retention.
This changes no existing portfolio, schedule, required PR CI or release gate.
The evidence checker rejects absent, skipped, failed, duplicate, stale,
TTY-only and incomplete lifecycle results. Passing this development lane still
does not establish complete-suite, actual Machines, authentic ordinary-update
or required frozen-candidate promotion enforcement.

Worktrees' opt-in `--test-file tests/native/test_uninstall_windows_owner.py
--allow-explicit-tiers` packet exercises the emitted Windows census script in
Windows PowerShell and PowerShell 7. A controlled census verifies the real
native token/creation-time query, compilation-unavailable and native-access
CIM fallbacks, and hard refusal of an incarnation mismatch without fallback.
It does not qualify desktop census completeness or runtime removal. Existing
`tests/test_native_uninstall_footprint.py` entrypoint controls preserve their
original deadlines and the explicit unrelated-consumer refusal classification.
The manual `installation-context-acceptance.yml` `runtime_removal` selection
also runs this packet on Windows at the dispatch SHA and requires all eleven
controls, including the three positive entrypoints, to pass without skips.
Native capture uses the existing caller-owned root-exit/cleanup boundary,
with 64 KiB per stream for token probes and 8 MiB for entrypoint captures.
The original ten-second probe and per-item entrypoint deadlines are unchanged.
Required unit-only CI is unchanged.

Bridge's opt-in `--test-file tests/test_owner_membership_budget.py --test-file
tests/test_owner_membership.py --test-file tests/test_db.py --test-file
tests/test_db_custody_schema.py --allow-explicit-tiers` packet checks indexed
exact-owner membership capture, old unindexed VM cutoffs, read-only non-preparation
and ordinary writable database bootstrap. Large unrelated owned, relationship
and represented histories retain one physical query budget. The manual
`installation-context-full.yml` `mux-companion` Windows lane additionally selects
the two indexed lookup/bootstrap controls alongside its unchanged actual-client
packet. This qualifies SQLite behavior, not cold-read scheduling reliability or
a wall-clock deadline increase; required unit-only CI is unchanged.

Worktrees' opt-in `--test-file tests/test_resident_recovery.py
--test-file tests/test_tracking_write.py --test-file tests/test_effort_focus.py
--test-file tests/test_status_monitor.py --allow-explicit-tiers` packet checks
maintenance holds (including holds appearing with recovered readiness),
brief discovery backoff, ordinary ensure/wake, no-wake
callers, compatibility fallback and non-replay after possible send. The additional
`--test-file tests/test_follow_ups_cmd.py --test-file tests/test_lazy_dispatch.py`
controls exercise parsed write refusal envelopes, including lifecycle commands'
default JSON. Recovery needs the canonical OS profile, not WSL registration,
and compatible live publishers retain their bounded readiness wait. A private
resident server executes the actual canonical claim transaction and publishes
its captured persisted-mutation evidence. Additional
`--test-file tests/test_handoff_cutover.py` controls retain the pane/process
result and retirement event when maintenance refuses subsequent ledger repair;
the shared test home also isolates the canonical passwd lookup. These are isolated source controls,
not an installed daemon wake, rollout or complete history guarantee; required
unit-only CI admission remains separate.

The opt-in `--test-file tests/native/test_resident_recovery_process.py
--allow-explicit-tiers` packet exercises actual normal ensure/spawn and an
owned leased POSIX resident, PID/birth/route/health, captured canonical mutation,
warm reuse, maintenance/no-spawn and exact process/instance cleanup. It waits
only for declared initial roster admission; direct fallback is forbidden.
This is private source-process qualification, not installed or Windows proof.

The additional `--test-file tests/test_launch_execution.py --test-file
tests/test_launch_seed_state.py --test-file tests/test_launch_attempt.py` packet
checks maintenance refusal before effects and during post-spawn observations
or seed cleanup: the launcher still owns exactly one child wait, preserves its
actual result, retains unconfirmed receipts, and never repeats the launch.

Worktrees' `--test-file tests/test_embody_receipt.py --test-file
tests/test_embody.py --test-file tests/test_embody_resume_seed.py --test-file
tests/test_copilot.py
--allow-explicit-tiers` packet covers additive ordinary state receipts, fresh,
resume and reuse semantics, incompatible/stale heads, native uncertainty,
bounded late registration/timeouts, one census per compatible live candidate,
strict parser handling and a frozen prior no-wait syntax subset.
The attended attachment consumer also captures the real additive receipt.
`tests/native/test_embody_receipt_native.py` adds opt-in finite Python CLI
process/stdout controls, with synthetic launch/session evidence. These prove
the real parser/stdio seam, not a real mux, Copilot/model startup, cold-machine
installation, installed runtime, other-platform qualification or completed
recovery. No native/model lane is implied by unit fixtures; required unit-only
CI is unchanged.

Bridge's opt-in `--test-file tests/test_portable_mux_joined.py
--allow-explicit-tiers` packet composes the real CLI, CodeSpace detached
provider, support archive/receiver, extracted Bridge and standalone-driver
extensions, SQLite retained handoff and driver HTTP route. It proves one
admitted/consumed send and same-conversation rejoin, with a duplicate-loadout
refusal. Provider process/SSH/forwards, the native mux, SDK and the
POSIX-to-private-scratch carrier remain synthetic. Separate focused enrollment,
readiness and producer controls preserve ordinary remote refusal, original
reservation/attempt and predecessor CAS, readiness-only polling, operation-bound
startup admission and the no-legacy-seed window before enrollment. Historical
schema tests construct the original guard rather than mislabeling a current
trigger. This is contained source composition, not native Copilot/TMux,
installed, clean-room or external-venue qualification. Required unit-only CI is
unchanged.

Manager's opt-in `--test-file tests/test_companion_tool_statistics.py
--test-file tests/test_companion_current_requests.py --test-file
tests/test_companion_bridge_session_source.py --allow-explicit-tiers` packet
exercises the optional owner count through the real selected CLI/private route,
strict codec and Textual app. A 5,201-record source exceeds the latest page and
default display capacity; ordinary/narrow app controls use a smaller display
budget to force eviction with two history pages. A separate actual native
5,202-record burst reaches the widget's default bounded window and evicts rows
without changing the qualified owner count. Controls cover independent
old-CLI/provider rollback, one shared declaration, exact prequery refusal versus
parser failure, malformed counters/ranges, unknown/zero, duplicate admission,
history separation, rebuild, hide/reopen and one coalesced callback for both
current projections. Full qualifications remain in Evidence; the compact label
does not add an extra status paragraph. Fourteen existing SVG baselines remain
unchanged. These are isolated source/private-process controls, not installed or
native-performance acceptance; required unit-only CI is unchanged.

Manager's additional opt-in `--test-file tests/native/test_companion_joined_tmux.py
--test-file tests/native/test_companion_app_tmux.py --allow-explicit-tiers`
packet composes the private Worktrees resident, managed Agenda and actual Bridge
semantic route/EventLog with the source app and an attached native tmux client.
Root selection/discovery, the Bridge loopback carrier and the inert CLI remain
explicit fixture substitutions. Client input selects the session through Evidence,
then verifies sparse full/narrow views, independently paged owner history,
live purpose/count updates without exposing the new work while browsing,
Follow-live and exact reader/pane retirement. The default tmux status row is
preserved: 110x40 and 60x24 client sizes provide 110x39 and 60x23 app areas.
Fixture-only, change-written geometry/focus/history snapshots distinguish a
received request from an applied page; server-buffer captures retain plain and
ANSI views. No source owner is replaced with a canned observation response.
These are source/native-client composition controls, not headed pixels,
frame/input-ready latency, installed or Windows qualification, real SDK actors
or original manual/head/Relay acceptance. Use the bounded host runner on an
explicitly enrolled POSIX tmux host. Required unit-only CI is unchanged.

Bridge's selected `tests/test_events.py` and `tests/test_live_runtime.py` packet
checks the internal tool-start range projection across append, SQLite hydration,
captured tails, rebuild and represented restoration. It covers distinct replay/
nested occurrences, duplicate represented delivery, qualified zero versus
unknown, exact-integer exhaustion, ancillary failure, atomic append/rebuild
races and visibility before persistence. A 5,000-record work-count control
forbids retained-history iteration during bounded reads. These are isolated
source controls, not negotiated wire/Manager consumption, lifetime/concurrency
totals, installed acceptance or native performance. Required unit-only CI is
unchanged.

Manager's additional opt-in `--test-file tests/native/test_companion_popup_tmux.py
--allow-explicit-tiers` packet opens the actual unmodified Companion through a
private `display-popup -E` binding and an attached native tmux client PTY.
Nine cases preserve Ctrl+C's quit-hint/non-exit behavior, then exercise Escape,
Ctrl+Q or keyboard activation of Exit against ready, blocked and failed owner
reads. Clean app exit and held-reader retirement return input to the original
inert CLI without changing its pane/PID or session incarnation. The fixture
uses client terminal input, not `send-keys` to the underlying CLI pane.
Every owner invocation is logged before dispatch or rejection; an additional
negative control confirms a rejected cutover attempt still appears in that log.
JUnit properties retain the selected path and evidence boundary. These are
isolated POSIX source controls, not installed/native Windows, headed pixels,
frame/input-ready performance, real Copilot sessions or manual-cutover
acceptance. The original Companion/Relay owners and remaining gates stay
separate; no production launch, binding, runtime or cleanup policy changes.
Use the bounded host runner on an explicitly enrolled tmux host. Required
unit-only CI is unchanged.

Worktrees' opt-in `--test-file tests/test_plugin_preparation.py --test-file
tests/native/test_plugin_preparation_native.py --allow-explicit-tiers` packet
checks strict enabled-payload selection, stale inputs, source conflicts and
partial results. The initial two native cases run the real CLI and
activation-preservation process boundary with a finite synthetic acquisition
child. Those cases do not install
real plugins or qualify provider authentication, runtime/service readiness or
cold-machine onboarding. The native file stays outside default required CI.
With `NATIVE_COPILOT_TEST_EXE` explicitly selecting an existing native executable,
its additional three-profile consumer case uses private homes/caches and an
anonymous loopback Smart HTTP Git fixture. It proves real payload acquisition
from a nondefault source ref for a pre-trusted repository or user-declared source,
and preserves refusal for
an untrusted repository-only source without granting trust. The fixture first
qualifies shallow Git transport independently. It disables client updates and
uses no provider credentials or model session; trust establishment, cold-machine
construction and live credential/relay acceptance remain separate.

Bridge's opt-in `--test-file tests/test_portable_receiving_policy.py
--test-file tests/test_repository_hosting_receivers.py
--test-file tests/test_codespace_spawner.py
--test-file tests/test_container_session_host.py --allow-explicit-tiers` packet
checks the selected portable policy receiver through isolated Python, existing
provider stdin transports and the actual shipped Session Host bundle. It
covers sparse/incompatible guest-suite fixtures, selected overlay denial,
malformed/unreadable policy, late denial and no-wake versus launch activation.
Resume-level Shutdown controls retain the original stop fence across provider
activation, including a newer stop, changed venue, provider rollback and a
fresh denial after activation. CodeSpaces' `tests/test_cli.py` exercises the
actual `start` parser/dispatch with owner-authentication, launch-policy and API
boundaries substituted, including exact account-binding collisions and failed
bound lookups. A real delayed activation provider proves repeated cancellation
retains recovery admission until the original process settles, an existing
drain refuses before activation and a later drain waits. Retained legacy-session
controls adopt the current receiving contract while pinning the actual checkout.
The same real provider process crosses the HTTP start route to prove its own
drain/cancellation admission. Cross-repository resume supplies the retained
repository/remote and tests real leading `cd` selection, including refusal of a
changed checkout. Unmapped first-use activation identifies the authenticated
login, revalidates its exact venue and keeps identity/lookup/token failures
pre-effect.
Explicit cross-repository selections exercise the same leading-`cd` guard.
Separate controls enter the real remote-host preparation method for explicit,
ordinary provider and managed bindings, proving a late checkout change cannot
update the retained target/database or reach child preparation. Matching paths
retain the original ACP identity; the final ACP connection is substituted.
Available-venue legacy controls negotiate current receiving support before the
first policy read, retain an unsupported prior adapter's refusal and preserve
the original stop fence. Another case keeps retained portable inspection
available without a current namespace owner. A real admitted-resume control
pauses provider refresh, records an actual durable stop while lifecycle custody
is held, then makes the venue Shutdown: refresh cannot activate it, prepare a
new Host or clear that newer stop.
HTTP container controls use the real namespace/parser and hosting pipeline with
substituted inspect/exec results. Both a stopped member and a running member
with a repository denial refuse before `ensure_ready`, plugin staging or session
creation; the CodeSpace activation shortcut cannot admit container readiness.
CodeSpaces' opt-in Windows desktop check sets
`CODESPACES_ACTIVATION_WINDOWS_E2E=1` and selects
`agent-codespaces --test-file tests/native/test_activation_windowless.py
--allow-explicit-tiers` with the bounded runner. It uses the canonical Manager
Win32 observer from the full source snapshot and a shared contained Job. Two
cycles invoke the actual activation helper from base `pythonw.exe`, with four
real native `gh --version` children replacing identity/start backend requests
without changing the production capture/headless kwargs. It checks observed
visible windows, terminal acquisitions, child focus and descendant cleanup.
Account/state/activation responses are synthetic; no credentials, live venue
activation, installed service or native Copilot is qualified.
Containers' `tests/test_runtime_drivers.py` sends the
base64 archive through the real runtime-proxy, external-driver process and
isolated canonical reader, proving both allow and deny without Docker.
A real bundled Host drives a mock ACP peer through one prompt and reconnects
to the same child/conversation. These are source/private-process controls,
not native Copilot, installed services, live remote transports or muxed-mode
acceptance. Required unit-only CI is unchanged.

Manager's opt-in `--test-file tests/native/test_companion_app_tmux.py
--test-file tests/native/test_companion_pane_tmux.py --allow-explicit-tiers`
packet distinguishes the original observer-stub pane test from the actual
Companion app. The latter runs unmodified `mux_companion.py` in a privately
constructed source prefix, with synthetic wire-valid owner data and an inert
CLI process. A foreground, caller-contained private tmux server and an attached
client PTY exercise keyboard history/live navigation, pushed purpose updates
while browsing, terminal resize, the Return-to-CLI button, observer reuse and
Escape dismissal without replacing the CLI pane/PID or native incarnation.
Owning feed retirement and exact fixture server/client cleanup are checked.
JUnit properties retain server-buffer browsing/resize captures and the evidence
boundary. These are native tmux/client source controls, not installed acceptance,
headed pixels, measured frame/input-ready latency, real Copilot/SDK sessions or
manual cutover. Native Windows and the parent gates remain separate. The hardened
container skips this packet when its image lacks tmux; that skip is not a pass.
Use the bounded host runner on an explicitly enrolled POSIX tmux host for this
native fixture. Required unit-only CI is unchanged.

Bridge's opt-in `--test-file tests/test_codespace_admission.py
--test-file tests/test_codespace_transport.py
--test-file tests/test_codespace_spawner.py
--test-file tests/test_decoupling_seams.py
--test-file tests/test_session_manager.py --allow-explicit-tiers` packet checks
responsive claim, relay resolution and rollback/release. Caller-contained
synthetic provider processes overlap the real loopback health route and serving
watchdog; repeated cancellation must settle the original attempt and owning-loop
receipt before releasing custody. Failed release retains the durable session and
exact claim identity. Registered-provider context, legacy JSON, explicit opt-out,
failure-before-preparation and cold recovery have focused controls. The new
process packet is explicitly selected integration work, not an addition to
required unit-only CI. These isolated source controls do not qualify installed
services, live credentials, SSO or a real external venue.

Manager's opt-in `--test-file tests/test_companion_timeline_scale.py
--test-file tests/test_companion_timeline_layout.py
--test-file tests/test_companion_timeline_widget.py --allow-explicit-tiers`
packet generates only the selected 5,000-event window of a synthetic
1,000,000-event logical source. It checks display/payload/strip bounds,
viewport-only paint without I/O, retained integrity proofs, 40 live updates,
wide-character resize and expansion, and short/scrollable wheel-up demand.
Pure work-count controls require unchanged geometry reuse and no repeated
immutable-ID construction, with changed clock/width/relationship invalidation.
The scale case remains explicitly selected T3 work, not required unit-only CI.

To retain raw observations, use the bounded runner with a fresh owned
`--junit-dir "$ARTIFACTS"` and extract the `sidecar-scale-observation` property.
It includes exact source-file hashes, workload/cache conditions, 40 individual
monotonic wall/current-process CPU/RSS samples for each stage, expected/completed
counts and nearest-rank p50/p95/p99/max. Uncached visible painting is measured
separately from applying a live delta through Textual Pilot idle; the latter is
not a native visible-frame/input-ready boundary. RSS covers the whole pytest
process, not isolated widget allocation or child CPU/memory. These source-fixture
observations neither enforce wall-clock thresholds nor qualify native tails,
installed performance or the complete repaint acceptance target.

Manager's selected `tests/test_companion_bridge_session_codec.py`,
`tests/test_companion_timeline_layout.py`, `tests/test_companion_timeline_widget.py`
and `tests/test_companion_visual.py` packet also covers bounded output
presentation. Actual Bridge EventLog/semantic frames pass through the codec and
Textual widget: a 40-chunk burst becomes one expandable output group, and one
explicit backward page restores the preceding tool occurrence without moving
the live boundary. Cancellation, source loss, raw detail inspection, reported
actor/work/unknown-kind boundaries and narrow anchors remain checked.
Pure controls cover group split/merge, eviction aliases, 64-record/16-group
limits and geometry reuse over a 5,000-record window. Fourteen deterministic
SVG comparisons include two sparse output states; resize clamps retain live
following instead of creating history demand. These are source controls, not
installed/native-frame or performance qualification. Required unit-only CI is
unchanged.

Manager's opt-in `--test-file tests/test_companion_joined_lifecycle.py
--test-file tests/test_companion_bridge_source.py
--test-file tests/test_companion_bridge_session_source.py
--test-file tests/test_companion_bridge_session_reconcile.py
--test-file tests/test_companion_current_requests.py
--test-file tests/test_companion_visual.py --allow-explicit-tiers` packet joins
the actual private Worktrees resident, managed Agenda publisher, selected Bridge
route/CLI and Textual app. Slow backward pages overlap live events, committed
purpose/agenda updates and actual waiter clearing while the browsing viewport,
selection and focus remain fixed. Separate cases retire an outstanding page on
hide or selected-head loss, or expire its actual requested retention boundary
and recover through explicit Refresh. Birth-qualified owning processes and exact
subscriber cleanup are checked. The selected Bridge page remains byte-bounded;
the tests do not assume that a requested 64-entry maximum yields 64 rows.
Full-app JUnit properties retain browsing, gap and reconciled progress screens;
the separate visual packet compares eight curated SVGs, including retained
history after a source gap. These are isolated POSIX source/private-process
controls, not installed, native-client or performance qualification. No new
effectful cases enter required unit-only CI.

Manager's opt-in `--test-file tests/test_companion_current_requests.py
--test-file tests/test_companion_bridge_session_source.py
--test-file tests/test_companion_bridge_session_codec.py
--test-file tests/test_agenda_json_document.py --allow-explicit-tiers` packet
joins actual parked ACP callbacks, the production observation-route generator,
the owning CLI and real Textual display through private fixture pipes. Its
test runner explicitly prepares the Bridge closure and fingerprints its
transitive local sources outside Manager's production dependency metadata;
runtime dependencies and installed sibling-runtime boundaries are unchanged.
It checks empty-history live input, unchanged event cursors during state-only
updates and paging, selected executable/provider rollback, malformed declarations,
no retry after an unknown invocation, independent revision qualification and
owned retirement. Four full-app progress renders are retained as JUnit properties.
The separate visual packet compares eight curated SVGs, including pending,
cleared, partial and unknown input alongside retained historical requests and
an explicit retained-history gap.
These are source fixtures, not SDK/model actors or installed/native acceptance.
The CLI-pipe fixture cases are explicitly POSIX-only; importing their fixture
does not imply native Windows support. Separate finite-document controls remain
portable. Tooling regressions cover test-only preparation, cache invalidation
and actual Manager-manifest release materialization.

Bridge's opt-in `--test-file tests/test_acp_current_requests.py
--test-file tests/test_session_observations.py
--test-file tests/test_session_observation_transport.py
--test-file tests/test_contract_registry.py --allow-explicit-tiers` packet checks
the optional current-request HTTP/CLI expansion, current-state-only delivery and
capture-before-wait races on the existing held reader. It covers exact producer/
session replacement, cancellation, strict schemas, unchanged history, reserved
frame bytes, independent shared-log followers, optional-follow heartbeats and
actual private ASGI/Future joins. The existing HTTP-52 capture
owner separately includes those source controls without changing the generation,
historical HTTP-51 evidence or required unit-only CI. Manager and installed/native
acceptance remain separate.

The same bounded observation/transport packet also checks optional
`bridge-session-tool-statistics-v1` provider expansion: exact range counters,
unknown versus qualified zero, captured gaps, an append after the atomic cut,
page-independent native heads, duplicate delivery, malformed counter/coverage
claims, independent current-request rollback and the actual parser/ASGI join.
A 5,000-record source projects only the selected two event records. HTTP-52's
owning capture includes these explicit controls and the new semantic owner
modules, without refreshing older generations or claiming consumer/native
acceptance.

The manual `installation-context-acceptance.yml` selector `wheel_inputs`
includes copied-application bootstrap acceptance in the existing Linux,
Windows and macOS source-profile jobs. The bounded canonical contained packet
runs the absent-legacy-helper normal/repeat/repair case and the source-unavailable
copied application case with real UV, offline wheel inputs and isolated roots.
These fixture controls do not establish portfolio adoption, service survival or
host rollout; each platform's actual result remains separate.

Bridge's opt-in `--test-file tests/test_acp_current_requests.py
--test-file tests/test_acp_client.py --test-file tests/test_attention_current.py
--test-file tests/test_attention_wait.py --allow-explicit-tiers` packet checks
actual local callback/Future lifetime, bounded candidate/output counts, privacy,
manual-versus-forwarded/automatic permission, connection/process/session
replacement and failed initialization. Delayed old callbacks never qualify as
the new producer; overlapping/reset permission slots remain partial until their
omitted callbacks settle. Completion invalidations do not append history. It starts
no SDK/model actor and does not qualify optional wire, Manager, installed or
native-client behavior; required unit-only CI is unchanged.

The optional `installation-context-acceptance.yml` selector `pr_artifacts=true`
also runs Fleet's `test_gateway_host_distribution.py` packet on native Linux
and Windows. It builds a noneditable selected-Gateway application from actual
first-party wheels and an offline fixture of installed dependency bytes, then
exercises stage, known no-child and post-creation listener refusals with retry, first install,
source-independent repeat, failed candidate
preflight, a distinct generation update and confirmed native stop. The fixture
does not qualify release-feed acquisition, provider authentication, reboot
startup or fleet adoption. Required unit-only CI is unchanged.
The Gateway jobs keep each complete installed Gateway and SSH lifecycle case
in its own sequential 180-second runner, separate from the independent consumer
controls. No lifecycle sequence is split; the six-minute job limit and peak job
concurrency are unchanged.

Its further dependent normal-setup jobs prepare the engine's declared test
dependencies and run the repository continuity module's `normal_ssh_setup or
setup_status` controls, including real Git source qualification and required
request-pin capability. They then run `test_ssh_setup.py` and the whole
`test_repository_ssh_setup_installed.py` sequence under the same 180-second
runner/six-minute job limits. The latter builds the engine wheel with its
packaged Source API and drives the actual Manager/engine handoff into synthetic
TLS enrollment and the complete Source application installer, repeat,
wrong-scope preservation, selected update and exact native stop. The Manager
and engine dependency support remain fixtures; this is not complete installed
product-cohort, provider, fresh-adopter or rollout proof. These jobs are
sequenced after the retained explicit continuity cases, not parallel replacement
coverage. Local helper/ordering controls and a packaged-API refusal probe do
not substitute for their actual native outcomes.

Only those normal-setup jobs select `--ephemeral-native-ssh-setup`. Admission
requires an explicitly selected Fleet lane on a GitHub-hosted disposable runner,
with its owned sandbox beneath `RUNNER_TEMP`; ordinary hosts, self-hosted
runners and nested contained callers refuse before preparation. The native
envelope retains the original bounded-worker limits and runner-renewed daemon
lease, but permits explicit application-daemon escape from caller cleanup
Jobs/process groups. Bounded descendants remain caller-owned; held daemon
cleanup fails and retains the sandbox. Ordinary tests still suppress daemon
escape. Held cleanup retains the sandbox even when the controller raises,
preserving the original failure as the exception cause. A teardown exception
also retains the sandbox when no cleanup results were recorded; a controller
failure followed by confirmed cleanup keeps its original error.
This distinction matters because the real Source constructor closes
an inner cleanup scope before the engine checks the selected host's readiness.
On Linux, a caller cleanup unit also requires the current user's already-running,
owned user-manager bus. The native envelope selects that fixed local bus endpoint
without moving `XDG_RUNTIME_DIR` or application state out of the sandbox; it
does not start a manager, install a service or use an inherited bus address.

Efforts' optional `--test-file tests/test_agenda_export_contributors.py
--test-file tests/test_agenda_export.py --test-file tests/test_agenda_export_cli.py
--allow-explicit-tiers` packet checks complete verified contributor provenance,
nullable/noncanonical identities, counts beyond the presentation-page ceiling,
same-connection read-only projection, post-projection corruption refusal and
whole-result count/byte/deadline limits. The actual CLI preserves the original
capability list and export/verify manifest contract while separately advertising
the new command. These isolated source controls do not enable a logging consumer,
scheduled collection, native Windows qualification or actual archive acknowledgment.
Required unit-only CI is unchanged.

Worktrees' optional `--test-file tests/test_worktree_data_store.py
--test-file tests/test_worktree_data_cli.py --allow-explicit-tiers` packet checks
passive project-wide retained-descriptor inventory, explicit count/byte
overflow, unsafe/partial state and registration-change refusal, and the actual
root CLI after canonical-record retirement. It preserves the original exact
read/allocation flow, including Efforts' existing capability reader and admitted
project-root aliases. A cell-qualified absent-root control retains valid receipt
validation and rejects missing, foreign or corrupt receipts without creation.
A work-count control requires one registration-root scan
plus two descriptor-membership scans per worktree, without traversing domain
contents. These isolated source controls do not qualify installed collection,
native Windows inventory, remote latency or durable archive acknowledgment.

Worktrees' optional `--test-file tests/test_retirement_obligations.py
--test-file tests/test_resource_producer_finalize.py --allow-explicit-tiers`
packet covers checkout-local agenda preservation across ordinary/forced cleanup,
raw removal fallbacks, partial/linked/unreadable homes and state appearing during
a failed Git removal. Its actual isolated Git finalization controls distinguish
completed source work with a retained legacy checkout from removed checkouts whose
managed data/descriptors remain discoverable outside them. The supported
checkout-local format-2 control invokes the actual Efforts initialization,
complete export, verification and readback commands before finalization.
Original identity, records, metadata and source bytes survive, and a verified
rescue copy does not release the checkout hold or create a managed locator.
No archive provider is required for that separation. This is source/private-fixture qualification, not
real-data rescue, native Windows durability or persistent-data deletion authority.

The manual `windows-coverage-rotation.yml` selector `related_resolution=true`
runs Worktrees' configuration-graft and execution-context consumers through the
canonical hardened Linux container. Supply `ref` for the published candidate;
execution records its immutable checkout, selector, container profile and actual
outcome. Focused selectors are mutually exclusive. This packet checks targeted
source/topology work counts, configuration forwarding, precedence, failure and
separate-invocation freshness. It neither expands required unit-only CI nor
qualifies installed runtime, native Windows, ACP or whole-hook throughput.

The focused Worktrees `tests/test_reconcile_deployment.py` launch-freshness
packet exercises real planning, staging, native execution-admission and the
durable launch consumer with isolated metadata. It preserves newer installed
and serving versions, independent same-version sources, payload refresh and
forward standalone updates, and refuses changed cached actions without runtime
writes. Its existing policy matrices may be selected separately with
`-k 'stage_refreshes or prelaunch_retains'` to retain the ordinary group deadline.
The actor-free `tools/promotion_unit_tests/test_launch_freshness.py` cases
substitute only file/source observations and belong to the unit-only promotion
packet. Neither lane qualifies an installed service, external rollout or every
first-use adapter; #7030 retains those acceptance boundaries.

The same focused packet separately exercises preexisting selector/manifest
disagreement, actual canonical last-known-good/newest-complete recovery, named
and packaged helper layouts, unchanged valid-current selection despite a newer
unselected slot, constructor/helper-only changes, external transitive local
dependency changes or loss (including cyclic declarations), and same-package
repair serving. Generated dependency environments do not invalidate cached work.
External dependency controls exercise both the stdlib TOML parser and the real
backport with `tomllib` unavailable, preserving the declared Python 3.10 floor.
Completion/interpreter records in these lookup fixtures are synthetic observations;
the real canonical resolver is executed, not the selected fixture interpreter.
They do not certify package health or installed serving. Own successful acquisition
is replanned through the ordinary next pass; genuinely stale external actions
still refuse. Runtime package normalization is checked against the canonical
source/repair identity function, preserving profile and other package qualifiers.

Fresh application-root privacy is covered by the six explicitly admitted
`test_windows_lease_acl.py` root-creation cases in the ordinary promotion-unit
packet. Portable controls observe both canonical call boundaries; on POSIX the
same packet takes actual construction/retention kernel leases, checks `0700`
and the strict SSH private-state owner, and refuses an unchanged existing
`0755` root. These tests never change the controller's umask; the actual Linux
mask is recorded from `/proc/self/status` (an observed `0022` in Linux CI is
evidence, not a local Windows projection or an assumed result).

`tools/test_installer_closed_wheels.py -k standard_posix_umask` is a separate
integration case: a bounded caller-owned, single-threaded child sets its own
umask to `022` before imports/threads and exercises the actual first-touch
filesystem and kernel-lease paths. It is not admitted as an eligible unit,
because that packet deliberately forbids child-process effects. Likewise the
Linux-only `services/ssh/tests/test_local_control_native.py -k linux_` cases use
actual private Unix sockets and native peer-pidfd authentication and preserve
unexpected directory entries. They require the existing standalone/native lane,
not the unit packet; Windows skips are not native Linux proof. No classification
exemption or unit budget is widened to run these integration controls.

The manual `windows-coverage-rotation.yml` selector `post_tool_fallback=true`
runs only Worktrees' post-tool shell, status/bind reminder, late-guidance and
guard-publication consumers plus ordinary pre/post-hook idle-retention and
late-demand/idle-drain and interruption-safe retirement controls
through the canonical hardened Linux container. Supply `ref`
to select a published candidate; execution records its immutable checkout,
selector, container profile and actual result. It cannot be combined with
`launch_maintenance`. Required unit-only CI and the scheduled full portfolio
are unchanged. The packet proves request-local lookup counts, reminder
accounting/cooldowns, separate cwd inputs, callback freshness and actual Python
entry output; skipped PowerShell/native-host cases are not passes. It does not
qualify whole-hook latency, ACP/model admission, installed state or throughput.
The guard-publication controls call the original in-repository config loader
and actual map producer with absent, empty, invalid and unreadable sources.
Confirmed optional absence must publish without losing guarded targets;
invalid/unreadable inputs must still withdraw the affected views. The actor-free
acquisition packet separately retains child uncertainty and non-reusability;
the existing strict-read disappearance control keeps an all-absent report absent.

Procutil's explicit `tests/test_caller_owned_native.py -k
controller_imports_do_not_write_bytecode` packet copies the package into a
disposable source tree and executes the real POSIX controller. It checks that
parent `-B`/environment settings do not substitute for controller-owned bytecode
suppression, while the requested command retains its own interpreter flags.
This source-boundary control does not expand required unit-only CI or qualify
an installed rollout.

Logger's opt-in `--test-file tests/test_worktree_state.py
--test-file tests/test_worktree_state_coexistence.py
--test-file tests/test_worktree_state_preservation.py
--test-file tests/test_worktree_state_contributors.py
--allow-explicit-tiers` packet checks typed local collection admission,
creation-time privacy, fleet coexistence, corruption/partial-publication refusal
and complete efforts export/readback
through real isolated Python processes. It does not enable scheduled collection
or qualify native Windows, an installed archive destination, remote durability
or reclamation. See [the collection boundary](plugins/agent-logger/docs/worktree-state.md).
The contributor cases preserve the complete optional owner projection separately
from canonical session references, refuse a v1 receipt as richer acknowledgment,
and verify actual retained metadata afresh. The separate-process owner test
negotiates the selected payload's optional capability before using new syntax;
original export/readback calls remain covered without it.
Logger's separate opt-in `--test-file tests/test_worktree_state_providers.py
--test-file tests/test_scaffold.py --allow-explicit-tiers` packet checks passive
provider discovery and unchanged organization-config behavior. A disposable
native `-m agent_worktrees` process selects the admitted checkout despite a
foreign ambient project, including every retained store after canonical-record
retirement. State bytes remain unchanged. Actual selected Efforts `info` calls,
effective project/global activation and prior/malformed provider controls
qualify negotiation without enabling exports, collection or a scheduler.
Real checkout-origin controls preserve case-sensitive owner paths and meaningful
ports, reject foreign case/port variants, and retain GitHub/default-port equivalence
without changing the older aggregate-policy identity.
The same-cell adapter reuses the already-qualified canonical peer launcher;
its shared native two-cell matrix remains owned by the peer-launch portfolio.
Logger's opt-in `--test-file tests/test_worktree_state_exporter.py
--test-file tests/test_worktree_state_pipeline.py --allow-explicit-tiers` packet
composes the actual selected Worktrees and Efforts owners for a synthetic retired
managed placement. It checks admission before discovery/staging, unchanged
source bytes, the actual exported revision, complete unknown/noncanonical
provenance, changed scope/placement refusal and exact temporary-member cleanup.
Failed or uncertain exports retain their evidence. A native Linux pidfd witness
requires an export timeout to settle its console descendant. Process commands
and collection copying share the caller's absolute execution budget.
The same native fixture also exercises actual filesystem policy loading and
the default-off existing sync tick, collecting both retained stores across two
passes while preserving ordinary missing-source failure. The separate
`--test-file tests/test_worktree_state_tick.py --test-file tests/test_aggregate.py
--test-file tests/test_sync.py --test-file tests/test_sync_protection.py` packet
checks shared budgets, fair deferrals, independent failures, cursor refusal and
unchanged default-off/dry-run/accounting/protection behavior. No packet enables
installed collection or qualifies native Windows, whole-pass/remote-filesystem
latency or remote archival.
The preservation cases exercise actually eligible old sessions and a verified
ordinary archive containing no worktree collection; neither justifies erasing
marked nested evidence.
Raw and redacted copy refusals compare directory membership as well as file
bytes, and the real efforts owner verifies the retained export afterward.
Renamed markers remain protected, and deterministic interleavings qualify both
claim-first and ordinary-publication-first namespace locking.
Disposal beginning below a retained marker preserves bytes, directories and
permissions; a marked `logs/` destination refuses ordinary process-log writes.
Collections renamed to qualified or legacy provider-group names are excluded
before expansion, preserving the source-discovery budget for ordinary peers.
Invalid group markers report per-entry failures while preserving healthy inventory
peers; actual group-enumeration budget failures still abort traversal.
Actual directory-open counts at three path depths require one pinned ancestry
walk per admission, and a newly written marker must be observed on the next call.

The opt-in Manager `tests/test_companion_visual.py` packet compares actual
Textual timeline renders against synthetic live-following and history-browsing
SVG baselines, plus a selected-session view with visibly paused membership and
unknown pre-compaction qualification. It preserves current captures as JUnit properties for inspection
without silently updating the expected screens. See
`worktree-manager/tests/visual_baselines/README.md` for the bounded invocation,
baseline-review procedure and rendered-fixture versus native-client limits.

Generated application staging materializes the known POSIX Python interpreter
aliases as regular files before inventory preparation or stage completion.
Only links to the running interpreter may be converted; foreign targets,
indirect environment directories and linked interpreters in completed slots
are refused. Regular files and hardlinks remain unchanged. Windows keeps its
ordinary interpreter-file path without requiring symbolic-link privileges.
Materialization uses a nonblocking, per-environment native directory lock;
concurrent preparation refuses without changing links, and a later retry
observes the finished files rather than restamping their inventory identities.
`tools/test_runtime_application_binstubs.py` covers these boundaries and checks
the actual UV stage entry; its native POSIX controls are separate from Windows
execution and the required unit-only CI packet.

The focused `bootstrap-update-native-acceptance.yml` workflow runs on demand
and for relevant PR paths on Linux and Windows. Its native/PATH local packet is
`python tools/run-plugin-tests.py worktree-manager --test-file tests/native/test_bootstrap_update_native.py --test-file tests/test_update_python_path.py --allow-explicit-tiers`.
Hosted acceptance runs complementary behavioral and full-retention selections
in separate jobs on each platform; no case is removed from acceptance. The
behavioral jobs also run `tests/test_update_python_path.py` with fake registry
state to check waited completion, excluded observer modes and publication failures.
They select the source-anchor-loss dispatch controls and standalone prepared-source
acquisition controls from `test_bootstrap_dispatch.py` and `test_install_source.py`
without adding those files' unrelated cases to this acceptance packet.
They also check first-touch private queue directories, addressed reuse for
unshared source inputs and distinct real pending submissions using the same
retained controller. The permissions control reads actual POSIX modes under
umask `022` or protected Windows DACL flags. Old UUID-named snapshots are not
deleted or migrated, and storage is not claimed bounded across distinct inputs.
Every job retains its five-minute ceiling, and the runner's 300-second packet ceiling
is unchanged. Local workers may use smaller serial selections when the complete
packet exceeds their available capacity; that is not a full-packet passing claim.
Manual dispatch with `qualify_promotion_copies=true` additionally runs an
opt-in Linux job against the exact selected source revision. It composes the
full repository through the real promotion owner, freezes and imports that
candidate, verifies every declared Invocation output and Installer mirror,
and checks direct/transitive Update Driver copies and unchanged source bytes.
It uses a private Git repository, never pushes or installs a runtime, and
retains the same five-minute job ceiling. This is not required PR unit CI or
a replacement for the actual release-candidate gates.
The real shell acquisition, canonical freezing, queue, finite worker, runtime
adapter and borrowed participant remain intact; only constructor and suite
bodies use finite fixtures with a minimal declared source graph and a real
native venv. It covers equivalent pending coalescing, separate active arrivals
and arguments, changed SDK inputs, preserved nonzero whole-operation results and birth-pinned loss of the
waiting Python caller. A prepared legacy payload without bootstrap scripts
separately exercises the actual native shell entry, current-controller
compatibility and a saved acquisition budget. Explicit prepared inputs are
application data: the shells keep their executing controller when its full
payload is available, otherwise acquire the controller through their normal
source path, rather than execute bootstrap code from the prepared application.
The prepared fixture carries the actual prior, pre-anchor CLI and source locator.
The selected application starts in the saved submission directory when it still
exists, so that prior CLI's relative-source check matches the current controller's
admitted source. If that directory disappears before process creation, dispatch
falls back to the retained installation root without losing the source-anchor
environment. Real `_finish()` subprocess controls cover current relative sources,
prior absolute sources and deletion at the process-creation boundary. A missing
interpreter with a live directory is not retried. A prior CLI's relative-source
resolver still requires its original directory; it cannot honor the newer anchor
environment, and that unsupported combination is not claimed as passing.
The standalone prepared-source shell control launches two concurrent callers,
verifies distinct per-invocation controller checkouts and
preserves unrelated shared staging. It exercises normal controller acquisition
without a sibling payload; only the selected interpreter body is a finite fixture.
That actual-shell control observes queue admission for up to 20 seconds, then
suite startup for up to another 20 seconds, within the unchanged 90-second case.
The caller-loss case uses a relative configured source through real
acquisition, participant validation and the suite's source locator.
An independent case verifies that detached acceptance removes only that caller's acquisition
checkout while the retained worker continues to its recorded final result.
The detached control runs twice and observes native Windows windows and focus
changes without weakening the enclosing test lifecycle. Its first result is
nonzero and its second succeeds; neither the detached observer nor its participant
may run successful PATH aftercare. Only a successful waiting caller finalizes
using its original observer arguments, with publication failure propagated.
The ordinary two-request control records exactly one such finalization.
The detached control's first acceptance wait allows 60 seconds; the second
remains 20 seconds. Behavioral cases retain
their 90-second limit, including fixtures and teardown. Bounded stderr
breadcrumbs retain fixture-setup costs and queue-stage elapsed times even when
a hard pytest timeout prevents normal teardown diagnostics.
After the first pending receipt, equivalent and distinct
callers acquire concurrently within the unchanged 40-second finite suite hold.
The pending callers mix explicit wait and detached JSON observation. Observer
modes remain local, while the durable installation recipe contains only
forwarded installation arguments and a normalized selected acquisition budget.
Source fixtures initialize one committed template, then use independent copies
and Git indexes per case. Generated tool caches are excluded; the selected
preparation source must still pass the real pristine-source check.
An ordinary two-request control delays only the second executor entry until
the successful first caller has removed its acquisition checkout, then runs the
canonical executor and verifies retained process-utility imports and both results.
Its second request uses the actual detached bootstrap to acquire changed SDK
inputs while the first worker continues from its original retained controller.
It observes committed admission for up to 60 seconds before its unchanged
20-second suite-start observation, within the original case and job limits.
The second detached caller also has a 60-second exit wait; consumer waits and
the case and job limits remain unchanged.
A separate inherited-anchor case distinguishes the configured relative source
from an equally named repository under the shell's working directory.
Its caller wait allows 60 seconds within the unchanged 90-second case and
five-minute job limits; production timeouts remain unchanged.
Actual installed-CLI admission and queue draining separately preserve a
caller-relative active hold across differing source/worker directories, allowing
manual work while inhibiting automatic work. Real directory-link-plus-parent
anchors qualify filesystem normalization where supported; unavailable native
Windows symlinks are explicit skips, not passes.
Authentication inspection retains valid caller-stdout JSON without
installation, and request controls preserve an interrupted fence until explicit
recovery outside the install transaction. A POSIX-only non-update entry resolves source dependencies without creating a
queue. Runner-owned containment
and daemon-instance leases retain cleanup. This is source-boundary evidence,
not full-graph performance, escape from a caller-owned cleanup Job, actual
runtime installation, installed rollout or migration of an existing queue.
A separate full-repository retention control records real input count, bytes
and blocking retention time as test properties and hosted log output. Its fixed
180-second case ceiling is separate from the 90-second behavioral limit. The
five-minute retention job still includes setup and cleanup, and a ceiling
overrun remains a failure requiring diagnosis, not automatic budget growth.
Calibration of a 3,330-file, approximately 50.8 MB closure completed in 102.58
and 113.92 seconds on a CPU-contended Windows control. Inventory took about a
second; first source and retained-copy reads dominated, while later reads were
substantially faster. These measurements justify headroom for the complete
verification cost, not a production latency guarantee or a security-scanner
attribution. Source growth or repeated overruns require rechecking input volume,
phase costs and host capacity before changing the limit.
It exercises canonical
freezing/readback against the complete declared Manager/SDK graph, not the
full acquisition/installation latency of an ordinary update.
The source-preparation control removes its disposable acquisition checkout,
then executes the retained controller's real preparer against a clean selected
Dev source and validates the resulting payload and source identity.
Required unit-only CI does not gain these process controls.
The actor-free bootstrap packet additionally submits a caller-relative Manager
source, removes the submitting directory, and validates/acquires its locator
from the retained controller directory. The saved source anchor does not change
the runner's retained working directory; changed source policy still refuses.
Installed CLI admission separately exercises an inherited source anchor distinct
from both the submitting directory and the worker's directory.

The optional `agent-worktrees --test-file tests/test_update_write_activity_native.py
--allow-explicit-tiers` packet uses private synthetic Windows writers under the
real strict Job and finite stream owner. It distinguishes useful silent writes,
a later stall, read-only work, continuous writes at the absolute ceiling and
accounting failure, retaining native root/descendant handles through confirmed
exit. Output parity and the unchanged output-only path remain separate controls.
Actor-free accounting and clock controls are admitted through
`tools/promotion_unit_tests/test_write_activity.py`; the native packet does not
enter required unit-only CI. Neither packet proves installed updater adoption
or a supported consumer-first bootstrap.

Worktrees' focused `tests/test_status_monitor.py` pending-request and bounded-retry
packet checks that idle, unarmed, confirmed and empty-token queues do not read
handoff history or claims. Armed requests retain the existing pre-spawn retry
cap, ambiguous-spawn refusal and durable-trace suppression. These are isolated
source controls, not installed-monitor or live-cutover evidence.

Bridge's `test_wmi_broker_spawn.py`, `test_watchdog.py` and
`test_service_process_identity.py` packet covers delayed replacement, argument
filtering and strict daemon recognition. Its Windows native control invokes the
real attributed launcher and service-entrypoint admission under a private,
renewed test lifecycle, replacing only the serving body with PID/argv evidence.
It checks same-process module execution, `__main__` identity and settled instance
cleanup without binding a service port or touching existing sessions. This is
source-boundary admission evidence, not installed watchdog recovery or rollout.

The explicit `agent-worktrees --test-file
tests/native/test_resident_prompt_receipt_integration.py --allow-explicit-tiers`
packet exercises a real Node consumer and private resident receipt server.
Without `RECEIPT_CLIENT_ROOT`, its test-only client fixture selects the sibling
`context-handoff` source payload; an explicit fixture override remains supported.
Its finite Node peer borrows the bounded runner's process scope with an absolute
execution/cleanup budget, retaining the strict no-helper assertion on resident
code. Deliberate persisted nonce replacement uses raw fixture YAML and verifies
the new incarnation before testing refusal. This qualifies the isolated receipt
boundary, not installed services, whole task-lifecycle acceptance or rollout.

The explicit `agent-worktrees` native packet
`--test-file tests/test_python_runtime_install.py -k real_installer_returns_before_resident_owned_monitor_drain --allow-explicit-tiers`
contains the three compatible routed/lock-layout controls plus one legacy-peer
control. The latter removes the old process's asynchronous-retirement capability,
keeps an admitted request blocked, then invokes the real retained-slot installer.
It requires forced native predecessor exit, interrupted request reporting and an
authenticated ready published successor, without invoking generic stale-runtime
cleanup. It uses disposable roots and real UV construction, not installed-host
rollout or a package-version simulation.
The installer must first return while the legacy predecessor and its admitted
request remain alive, with the ready successor's durable five-minute grace
record. The fixture then expires only that private record and wakes the real
resident through its existing promotion seam; the resident, not the installer,
performs native retirement. This tests consumer wiring without waiting five
minutes or changing the production grace interval.
The actor-free
`tests/test_runtime_monitor_legacy.py` and focused installer controls separately
cover owner/route/birth changes, absent cutover custody, unconfirmed exit and
failed candidate activation. Required unit-only CI does not gain a native matrix.
Windows and Linux use the shared pinned external-retirement primitive; macOS
force-legacy support remains explicitly unavailable rather than PID-only signaling.
The separate `Legacy monitor native acceptance` workflow selects only the
live compatible and `legacy-protocol` cases on Linux and Windows. It runs on
demand and is path-gated on its workflow, owning lifecycle source and native
fixture changes on PR branches. Both jobs also run the focused
retirement/preparation/legacy custody and grace/handover/completion controls.
These include early predecessor exit and retryable fallback-route release;
custody remains until guarded route cleanup settles.
Direct cleanup after route-release failure and exit between resident passes
exercise the same canonical gate. Reconcile-before-arm controls cover direct
and resident cleanup, failed writes, route races, missing active selection and
an unrelated fallback without fabricating a grace record or sending a signal.
Incoming preparation-health controls
distinguish grace support from asynchronous retirement alone.
Windows additionally runs the eight finite Job/native-identity grace and
asynchronous-installer cases; Linux native expiry is exercised by the real
installer case above, not inferred from skipped Windows controls. The finite
native grace controls assert authenticated health-handler entry before crediting
old-peer timeout. The native transition cases include a newer controller and
pre-grace successor, as used when restoring a retained release, with bounded
native predecessor cleanup and settlement instead of unsupported deferral. All packets
retain their 180-second subsuite, 600-second aggregate and existing item bounds.
The real-installer packet and twenty-minute job bounds are unchanged.
This is a focused smoke lane, not an expansion of required unit-only CI.
Existing serving-slot retention selectors remain unchanged.

Dispatch's actor-free `test_dispatch_runtime_resolution.py` promotion packet
checks flat/nested physical slot selection, unchanged fallback tiers, BOM
markers, nested precedence and redirected-environment refusal. The separate
on-demand `agent-dispatch --test-file tests/test_procutil.py -k
"resolve_runtime_python or resolve_own_runtime_python or
sibling_launch_executes_selected_physical_environment"` packet includes fresh
real venvs and two actual sibling module invocations per layout, retaining data
and selectors. It does not qualify whole installation, resident lifetime,
desktop visibility, graceful cutover or rollout.

The Windows daemon-status native packet is
`python tools/run-plugin-tests.py worktree-manager --test-file tests/native/test_daemon_observation_native.py --allow-explicit-tiers`.
It reads a real caller-contained process's census membership, literal command
line, token SID, creation time and root through native APIs with subprocess
probes forbidden. The process carries synthetic daemon arguments; this does not
start a central daemon or prove installed cutover. Portable reader/refusal
controls remain in `tests/test_windows_daemon_observation.py` and the existing
status/CLI suites. The existing manual `mux-companion` portfolio also selects
these status/CLI and native files on its Windows runner; its job and test
budgets remain unchanged. No native packet is added to required unit-only CI.

Blocking ordinary JSON-list admission is covered by
`agent-worktrees --test-file tests/test_worktrees_client.py --test-file tests/test_worktrees_feed.py`
through the contained runner. Real authenticated socket controls begin without
a resident endpoint and delay first publication beyond the old recovery budget;
JSON and one-shot stream calls return only the complete current publication.
A real caller-owned subprocess timeout settles a still-waiting CLI with no
premature stdout. The existing manual `worktrees-git-probes` Linux/Windows
portfolio includes both files, retaining its current deadlines and isolation.
Explicit saved-preview bootstrap remains covered separately.

The explicitly selected Windows Picker-handoff packet is
`python tools/run-plugin-tests.py worktree-manager --test-file tests/native/test_terminal_launch_native.py --test-file tests/native/test_terminal_launch_mux_native.py --allow-explicit-tiers`.
It requires native PowerShell 7 and PSMux. A caller-contained, headless ConPTY
pins every preparation descendant, including interpreter trampolines, before
checking their exit at attachment. It covers the actual Worktrees forwarder,
user and attributable PowerShell entry scripts, generated Manager CMD/PowerShell
entries and standalone Manager channel creation. Engine discovery, runtime
selection answers and the selected interactive command remain synthetic;
the production launcher's refusal path is exercised separately.
The generated CMD entry is also exercised with project and resident-picker
arguments, not only an empty invocation; both preparation interpreters must
exit before the selected terminal command is attached.
Return-path checks restore process-environment additions, removals and changed
values before a caller reuses its PowerShell session, including callers with
inherited native-error-as-exception preferences.
Two simultaneous real PSMux sessions cover rendering, input, Ctrl+C delivery,
resize, detach/rejoin and independent child exit. Strict fixture Jobs and
isolated PSMux roots retain cleanup custody. This is native source-boundary
qualification, not a full engine/Copilot launch, installed rollout, live-session
migration or shared Manager-daemon inventory. The packet does not enter required
unit-only CI.

The same packet now checks actual selected-runtime capability responses,
explicit legacy execution without an offer, malformed/unavailable queries,
stale inherited offers and current Worktrees forwarding to a legacy Manager.
Native queries use the existing finite owner, never the selected target's
possibly older process utility. The separate
`agent-procutil --test-file tests/test_native_owned.py --allow-explicit-tiers -k transient_native_query`
packet pins real query targets across success, output overflow and deadline
expiry on PowerShell 5.1 and 7. Its transient helper leaves the original
shell's global instance guard untouched.
Cold/warm terminal-query measurements report real elapsed time and birth-pinned
observed process names; the external sampler is an honest lower bound, not an
exhaustive count of short-lived interpreter trampolines. Query helpers and
targets are transient, and attachment still requires zero persistent Python
forwarders. These are source-boundary controls, not installed rollout proof.

The manual `Installation context full` workflow's `komodo-recovery` portfolio
runs one bounded Linux job at the exact dispatch SHA. It exercises the existing
native subprocess protocol fixture, real physical-owner locks, immutable
state/profile/outcome files and SQLite publication across lost failure
publication, recovered health, pending attachments and native/repair
entry-point switches. Missing or corrupted immutable outcomes refuse both
UNKNOWN and already-published terminal recovery without clearing the original
attempt; restoring exact evidence permits the original receipt to finish.
Compose 2/5 protocol cases retain their original native
process custody and verify no replayed apply. The job fails rather than crediting
skipped or empty test selection, and uses the standalone runner's environment
lease, sandbox and whole-process cleanup.
This is opt-in source qualification, not a required unit-CI expansion, actual
Docker/official-image startup, installed-service adoption or rollout proof.
The same opt-in portfolio includes read-only generation-5 node-journal snapshot
controls. These deserialize bounded owner-supplied bytes into memory, preserve
the original manager intent (including its legacy `instance` parameter), receipt
history and plan/step bytes, and retain ordinary current-writer refusal.
They do not qualify live database capture, migration or repaired-writer execution.

The optional agenda integration packet connects the actual efforts transaction,
postcommit publisher, authenticated agency resident and held CLI relay to a real
Manager/Textual widget. Run
`test-supervisor -- python tools/run_tests_in_devcontainer.py worktree-manager --test-file tests/test_agenda_end_to_end.py`
after staging the source/test closure. The test uses a disposable canonical
tracking record and redirects only fixture project/endpoint discovery; it does
not normalize observations or replace the movement validator. It covers
commit-before-delivery, pushed updates without reload, offline pending delivery,
unchanged idempotent history, current-head invalidation and owned-reader cleanup.
The owning standalone/provider/consumer packets remain separately selectable in
their plugin and Manager docs. These synthetic process/socket tests do not enter
required unit-only CI or qualify native Windows, installed rollout or migration
of an active agent. Same-home legacy adoption additionally requires quiescence
and the efforts-owned POSIX recovery/fencing checks.

The optional Manager `tests/test_companion_bridge_source.py` packet adds real
Bridge database membership commits, the owning membership parser/handler and
bounded SSE/CLI pipes to the actual worktree-resident/Agenda/Textual join.
Select it with `--allow-explicit-tiers`; the Bridge HTTP carrier and root CLI
entry remain separately qualified by the Bridge suite. It checks passive
capability refusal, unrelated-owner heartbeat continuity, retention gaps,
independent cancellation, retained rows and selected-head retirement without
starting an SDK actor. `test_companion_bridge_codec.py` and
`test_companion_bridge_discovery.py` cover normalization and payload selection.
The discovery packet invokes the real passive payload gate with an inherited
foreign-plugin receipt and root and checks no provisioning. Replacement controls replay
64 old members to 64 new members across bounded pages without transient current
facts. Retained-range controls reject stale bootstrap positions, expired replay
bases and contradictory pagination without imposing contiguous owner-local event
numbers. An actual canonical two-owner source verifies global sequence gaps and
empty caught-up reads without manufacturing local events.
The joined fixture exhausts Bridge evidence, refreshes the baseline, and
checks the other providers' rows, proofs, purpose, browsing anchor and next
Worktrees delta. Tokenless head invalidation and a blocked Bridge preparation
separately check retirement and independent rich Agenda admission, including a
new Agenda revision during exhausted-Bridge reconciliation. Model controls
exhaust another provider's proof, cursor and byte partitions without starving
an empty Bridge baseline. Malformed relationship IDs fail as contract errors;
terminal cleanup failures retain their diagnostic without rescheduling cleanup.
The rich Agenda retention packet exhausts both Worktrees and Agenda, reconciles
their actual fresh baselines in both completion orders, preserves healthy Bridge
bindings/proofs and accepts the next Agenda revision. Repeated head-change
notifications cannot replace a latched cleanup-failure diagnostic.
These controls do not establish native performance, rollout or known-session
event/history consumption, and do not expand required unit-only CI.

The optional `uv_deployment_compatibility` native-acceptance selector runs the
focused retained-dependency policy, Codespaces cold/warm installation,
MCP/Dispatch caller compatibility and full coupled Logger batch/binstub packets
on Linux and Windows at the exact dispatch
SHA. It covers canonical editable mappings frozen only for deployment, original
manifest preservation, explicit editable refusal and unchanged developer APIs.
Codespaces and the native Logger batch controls execute actual UV; Logger's
offline first-use package-effects/backend fixture remains synthetic.
MCP/Dispatch controls retain source-closure, reinstall, extras, cutoff, retry
and exact-exit checks through their fixture UV capture.
The thirty-minute jobs and twenty-minute Logger packet allowance include existing
preparation and cleanup costs, not a production installer deadline or speed
claim. Pair this selector with `source_cache=true` for the ordinary constructor's
hash-pinned real-backend controls. Neither selector qualifies installed-host
rollout or expands required unit-only CI.
The Windows source-cache job has a ten-minute outer guard: its completed
64-pass packet and action cleanup reached the previous six-minute limit on a
native runner. Constructor/item deadlines and the Linux job are unchanged.
The `dependency_compile` selector's existing source-build rows distribute
ordinary constructor profiles, source-input refusal controls and normal
constructor/cache reuse across `profiles`, `refusals` and `normal`. Their
selections preserve all 75 constructor/cache cases; the standalone
`source_cache` selector still runs the complete selection. A separate `toolkit`
row carries all copied-admission and fresh-interpreter witnesses, leaving
profile/refusal selection to the source-build and cache files. The five rows
retain the same six-minute source-build limits and unchanged Manager row.
Profile and refusal constructor controls use the same hash-pinned real backend
as the normal row; the toolkit row does not acquire that unrelated fixture.
The Logger Linux fixture explicitly qualifies its daemon-supervision interpreter
through the shipped custody checks, rather than weakening those checks when a
hosted toolcache interpreter is writable.
The source-phase regression advances20,000 controlled completed-unit notifications
beyond the quiet allowance, retaining a flushed byte and deadline renewal for
each unit while detailed diagnostics coalesce below64 KiB. Terminal counts,
unchanged stdout and subsequent silent expiry are checked independently.

The focused `owned-budget-native-acceptance.yml` manual workflow runs
the bounded borrowed-parent budget packet on
native Linux and macOS. It records the exact dispatched source, uses isolated
test roots, and pins descendant exits across accepted delegation, sequential
lookup timeout and enclosing caller death. Two cycles retain the same shared
deadline; the packet does not activate a runtime, service or agent session.
Its separate dispatch preserves all existing installation-context selectors
within GitHub's 25-input limit.
PR-branch changes to this workflow or its packet tests also run the same two jobs, allowing the
workflow itself to qualify before its first default-branch registration.
Changes to Machines' repair-process owner also select this optional workflow.
Its Linux job additionally runs only
`test_outer_required_read_timeout_reaps_nested_probe` through the contained
plugin runner: a required-module read is cut off while its probe and grandchild
are live, and both must exit before the outer owner returns. This is native
process-lifetime evidence, not clean-machine onboarding or credential receiving,
and does not enlarge the required unit-only CI packet.
The legacy-wrapper control separately removes only the parent watch to
reproduce a retained supervisor after real Bash caller death. The corrected
control confirms pinned exits without reporting unconfirmed SDK cleanup as
settled. Fixture cleanup retains the original unreaped wrapper's group identity.

The optional `update_diagnostics` lane runs the native installer inactivity
packet separately from the focused constructor packet on Linux and Windows.
Real children emit stdout/stderr without newlines beyond their quiet window;
quiet stalls, failed output sinks, retained guardian settlement, native byte
and channel preservation, unchanged receipt judgment and incumbent health
remain independent controls. Source-phase units also advance through verified
file batches. Focused source-phase controls also retain an actual fresh source
graph through native UV settings inspection, copying and fingerprinting. A
productive copy outlives the real quiet window; a silent copy is stopped through
the same process owner, preserving source bytes and confirmed writer exit.
An uneven-duration copy sequence also proves every completed local renewal
reaches the native observer before a subsequent near-allowance copy finishes;
no periodic or fixture heartbeat keeps that productive sequence alive.
Deterministic controls separately refuse late completed work and changed source,
and closed-wheel deadline controls retain their absolute bounds. These checks
do not qualify every native self-stage watchdog or an installed whole-update
footprint.

The same lane runs the real cold/warm cache control through native PowerShell
on both Linux and Windows, including the generated payload shim and Worktrees'
actual forwarding function. Cache files must remain beneath the durable root
and reuse unchanged bytes and identity, while original slot snapshots and caller
environment stay unchanged. Windows retains its extended-path form; POSIX keeps
an ordinary absolute cache path. The workflow uses a native `pwsh` step, so a
missing PowerShell prerequisite cannot be silently credited as POSIX acceptance.
These are scoped cache-entry controls, not full dispatcher/adoption or macOS
qualification.

`libs/versioned-runtime/tests/test_runtime_source_native.py` separately exercises
stderr-only streaming through both native PowerShell hosts. Actual no-newline
output outlives the quiet allowance while captured stdout, diagnostics, stdin
and nonzero exit results remain unchanged. Native byte relays preserve BOM,
invalid UTF-8 and line-ending bytes independently of the original text decoder.
A failed native sink must finish the original producer and relay Jobs' real
empty-process checks before returning an error. The actual Worktrees source
wrapper advances through delayed hashing and snapshot copies; a silent copy
is stopped by that same owner without changing the source or publishing a
candidate. Snapshot units independently refuse a failed progress sink and
reacquire the original publication lease after cleanup. Containers' fresh stamp
uses its declared `python` installation-context prerequisite and creates only a
snapshot and front door, not a runtime. Separate launcher-only probes still
cover source selection without a direct Python command. The Windows Git Bash
resolver fixture has a 30-second outer guard for cold native discovery; other
hosts and production allowances are unchanged. Git Bash with Windows Python
does not qualify native POSIX behavior or an installed whole-update footprint.
An open undrained pipe must not starve supervision. A slow draining pipe
separately proves acknowledged native writes renew the delivery allowance after
installer exit. Legacy log/observer failures occur only after owned writers have
settled; native relays retain original byte channels and explicit error receipts.
Dispatch's opt-in `tests/native/test_construction_completion.py` packet joins
its actual PowerShell completion functions to the shipped construction-session
helper. Run `python tools/run-plugin-tests.py agent-dispatch --test-file
tests/native/test_construction_completion.py --allow-explicit-tiers` on Windows.
PowerShell 5.1 and 7 each accept a synthetic packaged pywin32 path hook without
executing it, reject missing or modified bootstrap inputs with the original child
diagnostic, and confirm helper exit and preserved prior slot, selector,
configuration and deployment metadata. The complete retained helper bundle is
required, just as in the actual caller. An external sentinel must remain absent
even when the rejected candidate is removed; a caught import exception is not
proof that a path hook never ran. The T3 tier keeps all six controls skipped
without explicit opt-in. This is a completion-boundary check,
not package-feed, full installer, selected Dispatch CLI or affected-host recovery
qualification; no runtime or service is installed.
Dispatch's `tests/test_supervisor_scheduled_admission.py` separately exercises
the actual scheduled-entry and on-demand ensure paths in contained Windows
fixtures. Both PowerShell hosts share one native lane during first and
overlapping admission. The marked-master control changes interval and extra
arguments in the same version, then observes the real successor's completed
cycle and absence of profile markers in its worker environment. Its fixture
retains the predecessor until cooperative release because the contained SDK's
kill-on-close Job deliberately owns that successor. This proves configuration
handoff and readiness, not survival after predecessor cleanup or non-admin
installed service lifetime. Containment and the original deadlines remain
unchanged.
The MCP retirement packet adds native Windows controls for one birth-checked
incomplete-census refresh, completed processes and retained live-unknown/identity
refusals. An actual native parent exits during either ownership inspection or
metadata refresh while its child stays alive; snapshot ancestry must retain
that child and the foreign-owner refusal. Its actual fresh-settlement fixture owns its complete child scope with
the existing 60-second child bound; that case's 90-second outer guard allows
owned timeout cleanup and reporting instead of preempting them.

The optional `update_diagnostics` lane includes Manager's actual scoped
engine handoff under a no-window captured parent. It verifies repeated stdout
and stderr, the unchanged native nonzero result and unaffected unscoped
passthrough controls. This proves stream delivery, not installed full-update
success or native publication settlement.

MCP's separate explicit physical-generation packet is
`python tools/run-plugin-tests.py agent-mcp --test-file tests/test_generation_handover.py --allow-explicit-tiers --timeout 240 --plugin-timeout 260 --test-timeout 180`.
It constructs private immutable source runtimes using the installer engine,
then exercises a prestarted keeper and the internal launcher's real startup.
The original conduit and upstream survive a physical backend change,
authenticated outgoing retirement and supported rollback. Two clients share
one keeper; actual interpreter prefix, module path, backend PID, mutation and
initialization counts are asserted. Native Windows and POSIX source runs pass.
The fixture uses contained,
leased instance ownership and exact cleanup, not host credentials, installed
services, desktop observation or a production-lifetime claim. It is not part
of required unit-only CI and does not activate the ordinary forwarder.

The separate owner-only `mcp-caller-lifetime` hosted Windows packet selects
`test_custody_native_lifetime.py::test_native_caller_jobs_and_session_owner_lifetime`.
It prepares the canonical MCP test environment, then uses the existing native
test owner, private physical runtimes and renewable daemon leases with a
600-second case and unchanged 900-second aggregate boundary. Actual caller-Job
closure, bounded-child termination, restrictive shared-outer refusal, shim exit,
post-replacement traffic and exact owner-death settlement are distinct checks.
The original stdin writer stays open through owner death. Cleanup belongs to
that native owner; a failed settlement cannot qualify the packet.
No Copilot/terminal binary, credentials, desktop observer or visible control is
required. Ordinary local/plugin tests remain contained and cannot opt into this
lane by setting its fixture flag. Controller support must first reach the
protected default branch before submitting `--packet mcp-caller-lifetime`.
Packet preparation or a platform skip is not qualification or activation.

The optional `update_diagnostics` lane also runs MCP's focused
`test_source_install_transactions.py -k footprint_routes` controls and
`test_source_build.py` operational-identity and
`test_bootstrap_stamp_selection.py -k post_repair` session-readiness checks,
plus MCP's actual source/repair manifest serializers, through the
canonical contained plugin runner on native Linux and Windows. They exercise
the actual attached constructor and both native shell adapters with compatible,
missing-deployment and corrupt-completion fixtures, source identity preservation
and unchanged incumbent bytes. The POSIX FIFO adapter requires native POSIX
Python; Git Bash feeding a Windows Python is not qualified. These controls do
not perform full installation, old-owner retirement or installed-host rollout.

The same optional lane separately executes MCP's `test_reap_versions.py` and
procutil's `test_process_retirement.py` and `test_retirement_custody.py` on Windows and Linux. A real synthetic
tree (including Windows-owned headless console hosts) must exit through retained
kernel identities while its original parent survives. Retirement uses two
complete censuses; measured Windows owner queries made steady progress
but exceeded the default 30-second item guard. That native tree fixture uses
the same 90-second guard as fresh settlement, retaining its 60-second children,
full census, identity checks and owned cleanup. Production deadlines, unit-only
packet policy, non-Windows item and aggregate runner budgets are unchanged.
Native pause/rollback and both shell adapters' fresh-failure propagation are
distinct controls. The Linux writer-exit observer treats a disappearing proc
stat file as completed exit, but still propagates an unreadable observation;
the hard-owner-death controls retain their original process settlement bounds.
Abrupt caller death after its first native pause must resume the original target,
settle the finite custodian, and leave the observing parent alive. A separate
hosted Linux control in `tools/retirement_custody_acceptance.py` closes an actual
caller process group twice, with the custodian outside that group.
These process witnesses do not qualify disposable-state reset, old-code removal,
critical transport survival, full installed acceptance or rollout.

The opt-in Windows consumer lane (`windows_broker_consumers=true`, retained
selector name) now uses normal native creation, not Task adoption. It proves
two nested permissive caller-Job closures, bounded controls, cleared escaped
metadata and the different-inner/shared-restrictive-outer negative before
constructing the actual Worktrees and Manager runtimes. Only fixture Jobs
explicitly launching durable work permit breakaway; bounded tools stay strict.
No COM, WMI, Scheduled Task or UAC activation is exercised.
The same selector runs native PowerShell admission/post-caller work and POSIX
regressions as independent bounded jobs. A failure in any job remains a failed
qualification; it does not prevent the actual Manager consumer from reporting
its own result. Disposable Windows profiles set both forms of the home path
consistently, including `HOMEDRIVE`/`HOMEPATH`.
The lane constructs the actual Manager
runtime through its canonical installer in the credential-free disposable
profile. Two concurrent current-roster
reads start from a stopped resident through the normal installed Worktrees
front door, retaining the loader's existing bounded startup/reconnect behavior.
Both caller Jobs close before repeated resident health checks;
authenticated canonical shutdown and native exit confirmation finish each of
two cycles. This is the installed Manager roster-consumer and resident recovery
boundary, not a full interactive Picker/desktop-session launch or installed
host rollout. The legacy broker activation lane remains a separate explicit
compatibility test, not a prerequisite or evidence for normal native startup.

The separate `PROCUTIL_NATIVE_INSTANCE_LIFETIME=1` opt-in runs
`libs/agent-procutil/tests/test_daemon_shell_lifetime_native.py` through the
canonical contained runner. Its 60 cases cover both native PowerShell hosts,
normal/test-venv PATH composition, two cold lifetime cycles, original-shell
identity, expiry, nested/final guard close, helper/caller loss, production
non-expiry and pre-application startup refusals without compiling interop code.
The version refusal forces the real bootstrap guard rather than claiming an
uninstalled older interpreter was exercised; the legacy-refusal fixture aliases
an existing framework type, without emitting one. Process exit is observed on
birth-verified pins before fixture cleanup. This lane remains outside required
unit-only CI; continuous desktop observation, source/release and installed
acceptance are separate evidence.

The finite runner's `test_native_owned.py` exercises ordinary completion,
descendant waiting, timeout cleanup, path refusal and original Job-scope
publication on both native PowerShell hosts. Separately,
`PROCUTIL_NATIVE_OWNED_LIFETIME=1` selects
`test_native_owned_lifetime_native.py`: two cycles of caller loss, helper loss
and runspace cancellation under normal and test-venv PATH Python. Its closed
publications bind the original shell, actual helper, optional venv launcher,
writer and leaf to native process births. Every unique pin must exit before
fixture cleanup; a helper-loss transport failure remains unconfirmed rather
than becoming successful cleanup evidence. Neither matrix belongs in required
unit-only CI.

`test_native_owned_powershell.py` separately uses real shells with an inert
dispatcher and an in-memory Job owner. It proves import/legacy refusal,
argument and Boolean-cleanup contracts and actual runspace-stop `finally`
behavior without native process effects. `test_windows_owned_stdio.py` checks
the two unique inherited stdio handles through synthetic native calls and
owned file descriptors. The admitted `tools/promotion_unit_tests/test_native_owned.py`
packet covers finite outcome/retirement logic with explicit native-boundary
doubles; none of these portable checks replaces the native/desktop matrix.
The same real-shell facade packet extracts the actual `Invoke-FoundationChild`
consumer function and exercises child failure through the finite adapter. Only
Boolean cleanup evidence is accepted, an outer false dominates an inner true,
and the original failure must survive. It does not execute or qualify the
remaining compiled foundation bootstrap or its opt-in acquisition lanes.

Update-driver's `test_native_lifetime.py -k queued_installer`, admitted with
`UPDATE_DRIVER_NATIVE_LIFETIME=1`, separately exercises the real queued executor
launching a disposable durable daemon. It pins the native identities before
allowing installer completion, proves ordinary-child cleanup and post-Job
daemon work, and retains the restrictive outer-Job negative. This is the queued
installation lifetime boundary, not a substitute for complete Manager
installation or installed-host health.

Worktrees' `test_command_lifecycle_recovery.py` covers read-only inspection,
evidence-bound pending-owner qualification, preservation of unrelated manifest
and runtime state, changed source/evidence and policy refusals, and live
publication-lock custody. Its Windows subprocess control runs the shipped helper
against disposable payload/runtime roots before ordinary lifecycle publication;
it is not installed-host rollout or service-convergence evidence.

`tools/test_runtime_profile_bundle.py` checks the repeatable exact-profile
transport producer, actual command invocation, canonical consumer readback,
determinism, source independence, aggregate storage and refusal/cleanup paths.
Run it with `tools/test_installer_wheel_snapshot.py`; these producer integration
controls are not enrolled automatically in required unit-only CI.

Agent-worktrees' `test_hook_policy_lifetime.py` is an explicit contained
Windows lane (`AGENT_WORKTREES_NATIVE_RECOVERY=1`). It exercises the real
resident policy adapter through a venv launcher, pins the descendant's kernel
handle and birth identity, and verifies termination after both the fixed
policy timeout and Node caller death before fixture cleanup. Native controls
stay outside the required pure-unit packet. They do not prove unattended
command-hook handback, full installation or live fleet activation. The
PowerShell 5/7 entry fixtures separately cover singleton-array rejection,
inherited non-UTF-8 settings and strict/legacy response compatibility.

`COPILOT_TEST_HOOK_PRE_PYTHON` is required for those entry fixtures and can
also select an absolute, existing policy interpreter for the lifetime lane.
Without that override, lifetime controls use the managed runner's interpreter.
The PowerShell fixture resolver returns that exact path; an unrelated ambient
Python trap verifies that PATH lookup cannot substitute another executable.
This allows qualification against an already-selected installed SDK without
changing or provisioning it. Results apply to that selected runtime, not to
cached hook activation or a different machine's installation.

`tools/promotion_unit_tests/test_artifact_license_notices.py` checks that the
existing source-bound wheel verifier refuses omitted or changed source notices,
accepts modern and legacy license locations, and preserves historical source
layouts. The real artifact/native lane exercises ordinary backend inclusion
from each independently packaged project's `LICENSE`; no package code or legal
approval engine is added by the pure notice controls. copilot-extensions

How to run the plugin test suites, explicit development checks, and the
**opt-in end-to-end smoke tests** that require real infrastructure.

The manual `installation-context-full.yml` `foundation` portfolio runs its
five Windows cold first-use modules in independent packets. They retain the
existing 900-second runner aggregate and 25-minute job caps instead of sharing
one aggregate across multiple independent cold installations. All previous
module selections remain covered; required unit-only CI is unchanged.

Linux cold first-use fixtures keep Python and uv out of the product's native
tool PATH. The runner-owned daemon lease separately selects an existing trusted
system interpreter for test lifecycle accounting. That control-plane selector
does not supply the product bootstrap interpreter or bypass lease enforcement;
these cases prove Python-free product admission, not an interpreter-free pytest
harness.

Dispatch's reversible-maintenance Windows packet uses the existing
`installation-context-full.yml` manual workflow with
`portfolio=dispatch-maintenance`. Dispatch the reviewed source branch; each
job checks out and verifies the immutable dispatch SHA. Three bounded packets
exercise actual policy/overlay behavior, explicit preparation, strict
supervisor-preserving adoption, installer cleanup/retained-runtime
assertions and the native background-worker lifecycle under the canonical
runner's disposable roots and process containment. Read the actual outcomes
and skips: WSL PowerShell and a skipped Windows-only case are not native Windows
evidence. This focused opt-in lane neither expands required unit-only CI nor
reenrolls the unavailable full Windows rotation.

Dispatch's admission-bundle and conditional-publication POSIX packet uses the
same manual workflow with `portfolio=dispatch-publication`. Its Ubuntu job
verifies the exact source SHA and runs `test_conditional_runtime_publication.py`
and `test_install_sh_activation_guard.py` through the bounded plugin runner.
This includes ownership-aware rollback cases skipped on Windows. It does not
expand the required unit packet or replace native Windows/whole-suite evidence;
read its actual exits, containment cleanup and skips separately.
The installer fixture keeps a 524,288-character combined-output bound so genuine
per-unit source progress is retained. A real-child regression verifies progress
above the former 64 KiB bound and confirms excessive output still fails; the
standalone publication-helper bound and production inactivity renewal stay
unchanged.
The conditional failure/healthy-selection integration case uses a 60-second
item guard: measured healthy installation plus rejected installation/rollback
can consume the default 30 seconds before its final installed `--version`
probe. All rollback and final-health assertions, the 90-second child bound and
other item guards remain unchanged. Parent command durations are reported even
when capture is interrupted.

Fleet-manager's `test_komodo_*` packet exercises strict declaration/current
grant checks, real caller-owned synthetic Docker protocol processes, physical
bridge and exact-container custody, preserving adoption versus managed fixed
configuration, UNKNOWN/restart reconciliation, failed candidates, current
authenticated loopback readiness and receipt-owned retirement/reinstallation.
`test_komodo_gateway.py` traverses the actual TLS Gateway and original JWT/
enrolled-PoP caller boundary without a service connector.
`test_komodo_installed.py` installs a fresh noneditable SDK, denies checkout
reads and invokes the public bootstrap with a real machine-key verification
result. Its repair controls retain an original UNKNOWN created by a separately
built pre-repair committed SDK (the pinned revision in
`test_continuity_installed.py` must already exist in the test checkout),
discover unsupported repair from that actual
installed control module without native effects, then use a distinct current
noneditable SDK and fresh machine identity to settle the exact original grant.
The same seven repair cases run with the baseline whole-operation permit and
the explicit checkpoint producer. The checkpoint lane also builds the retained
pre-checkpoint repair SDK and refuses required checkpoint behavior from its
actual installed control module before owner construction or native work.
Fresh machine proofs bind each status/terminal action, while one advancing
resolver/native/health clock makes complete status and terminal work exceed
30 seconds within the original fixture's 180-second deadline. No individual
permit is extended. Grant/transition/executor skew refuses; interrupted state
and journal publication resume in a fresh process with unchanged immutable
original evidence, one native apply and one terminal row. Baseline fixture
deadlines and whole-permit behavior remain unchanged.
These are contained component/installed-SDK controls, not actual Docker,
official-image, automatically constructed controller or production evidence.
Native/network/build tests remain outside the required no-actor unit packet.
`test_komodo_incumbent.py` adds original named-volume/file materialization,
batched real subprocess readback, authenticated preserving-only adoption,
unchanged repeat/status and lost-terminal-receipt controls. Its platform-neutral
composition fixtures mock Linux stat/native observations and the state lock;
they do not qualify Linux custody. `test_komodo_owner.py` separately adds
real Linux protocol/lock adoption, volume-drift and update-refusal cases on
Compose 2 and 5. Neither packet proves official-image startup or live adoption;
the existing fixed-profile and broader lifecycle proofs remain distinct.
The original-attempt repair packet adds distinct candidate/executor and current
transition admission, exact same-journal settlement, immutable prior-failure
history, process/key drift and interrupted publication controls.
`test_komodo_repair.py` retains four focused component smoke cases; the remaining
matrix is marked `client_distribution_full` for explicit full/manual runs.
Its late-publication controls withdraw transition/request authority, expire the
permit or reconnect/disconnect the same container after immutable-evidence
reads inside the original terminal transaction. Both success and failure must
retain the exact UNKNOWN receipt, original attempt and two journal rows.
Interrupted success/failure publication controls replace a container on the
same bridge and require pending status and success/failure retries to refuse
native attribution before any credentialed HTTP request. Successful and failed
terminal retries have the same replacement-container control, retaining the
exact terminal state and journal rows. Restoring the retained container resumes
the same original decision without another native apply.
Use the existing standalone runner with a `komodo_repair` selector and
`-m "not client_distribution_full"` for its focused smoke/record controls;
omit that marker filter for the full packet. Required unit-only CI is unchanged.

Agent-worktrees' `test_mux_creation_admission.py` covers Windows gate and
join-result semantics without claiming or typing a seed. Its explicit native
`tests/native/test_mux_creation_overlap_native.py` packet overlaps the shipped
PowerShell/Python creation boundaries in both directions against private
synthetic mux protocol processes. It checks same-target joining, different-target
overlap and actual kernel-lease release while the original creation client is
still alive. It does not exercise complete Picker/embody preflight, actual
PSMux, repo lifecycle fencing, POSIX parity or installation removal.
Run it with `python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_mux_creation_overlap_native.py --allow-explicit-tiers --timeout 180 --plugin-timeout 600 --test-timeout 60`.
The synthetic mux is a small executable built with the system C# compiler,
not a Python interpreter proxy; fixture startup never justifies extending the
production two-second query cap. An unavailable compiler is an explicit skip,
not Windows acceptance evidence. Actors finish importing/loading their owners
before a ready/go barrier admits native creation, matching the production
creation timer rather than charging another actor's Python startup to it.
This is not a cold whole-launch performance measurement.
The same opt-in Windows job runs Manager's focused published-creation failure
control, which executes the shipped failure/retry tail and retains argument,
receipt and token handoffs after discovery without another attempt.

Agent-worktrees' `test_worktree_lifecycle.py` holds the actual native launch
fence in a child process, checks same-target retirement exclusion,
independent different-target lifecycle leases and retention of the legacy
repository barrier, and proves release on normal exit and process termination. Cleanup and
managed-removal controls refuse an active target before fresh record reads or
removal, including forced cleanup. These are lifecycle-fence controls, not
whole-launch parallelism, full Picker, real mux or installation-removal acceptance.

Agent-worktrees' optional `tests/native/test_native_startup_prompt.py` runs a
real CLI against an offline loopback synthetic provider, in an empty workspace
and a separate `COPILOT_HOME` without inherited credentials or plugins.
Set `COPILOT_NATIVE_TEST_EXECUTABLE` to the exact absolute CLI executable and run
`python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_native_startup_prompt.py --allow-explicit-tiers`.
The control checks literal initial-prompt delivery, exact resumed conversation,
retained history and two completed turns. No terminal input is injected and no
installed service or retained conversation is changed. This is native CLI
startup qualification, not real-mux, physical keyboard, installed rollout or
whole managed-launch acceptance; platform lanes are qualified independently.
On Windows it also runs the shipped normalized PowerShell setup with synthetic
forwarding and batch candidates ahead of the selected standalone executable.
The same exact user-turn/history assertions qualify PATH selection and literal
multiline startup/resume transport without relying on process creation alone.

Customizing-copilot's `tests/native/test_client_request_capture_transport.py`
exercises exact raw-body persistence, streaming/nonstreaming replies and
failure/timeout cleanup using synthetic local clients. Select it with
`python tools/run-plugin-tests.py customizing-copilot --test-file tests/native/test_client_request_capture_transport.py --allow-explicit-tiers`.
The optional `test_selected_native_cli_captures_synthetic_harness_only` case
uses `COPILOT_NATIVE_TEST_EXECUTABLE` for an actual offline CLI invocation in a
fresh synthetic home/workspace. It checks that a synthetic `AGENTS.md` marker
reaches the saved request without reading the operator's harness or publishing
the raw artifact. Windows and POSIX qualification are separate; neither test
claims hosted-provider prompt equivalence or OS egress isolation.

The same plugin's `test_snapshot_defaults.py`, `test_snapshot_inventory.py`
and `test_snapshot_wire.py` cover current-preference precedence, the 300-second
default, private companion outputs, settings compatibility, content matches,
bounded evidence, metadata/search surfaces and explicit unknown attribution.
`tests/native/test_snapshot_report_cli.py` exercises the shipped offline
entrypoint against synthetic artifacts without launching Copilot or contacting
an MCP server. Select the focused files through the same contained runner;
native entrypoint files require `--allow-explicit-tiers`. Private operator
snapshots and inventories must never become public test fixtures.

Agent-worktrees' `test_session_shell.py` checks canonical Windows setup copying
for cold `embody`, without taking over its separate seed/admission owner.
Its native PowerShell control invokes the actual copied wrapper/setup with
literal arguments and removes only disposable source fixture files from inside
the synthetic backend before returning its real nonzero exit. `test_embody.py`
checks copy-before-spawn and failure-before-spawn ordering with seed delivery
still owned by the existing completion path. This is consumer/asset lifetime
evidence, not supported product uninstall, real Copilot/TTY or host rollout.
The setup-copy/actual-pane-construction control counts the underlying Manager
probe on both healthy and timeout paths: copying adds none and construction
performs its existing single probe.

The same packet checks launch-owned Windows pane manifests outside removable
runtimes, retained literal argv after disposable runtime removal, exact 24-hour
startup grace, matching-launcher cleanup and foreign-path refusal. Manager's
`test_pane_args_file.py` executes both legacy and launch-owned producers through
the shipped PowerShell consumer, including consumed receipts and fresh retries.
These controls do not establish supported uninstall or same-process interactive
survival.
`test_pane_args_projects.py` exercises the monitor's actual sweep entry point
with explicit registered-project roots, unset and unrelated ambient context,
two projects, an unregistered negative control and independent project or
inventory failures. It does not start a resident monitor.

`test_startup_manifest.py` covers final-only serialization of copied canonical
Windows setup, unchanged custom/older-host commands, nested failed-launch
cleanup and reuse of the existing expiry. The explicit native packet
`tests/native/test_startup_manifest_native.py` runs the copied command/setup
after disposable source removal, checking literal arguments, consumed files
and process argv without setup-time installation roots. Its same-host control
also checks pane-appended interactive arguments. Malformed manifests are
consumed with sanitized refusal before a backend. With the reconciliation
marker unset, direct and serialized setup are compared in normal and recovery
mode against a failing synthetic restore, preventing extra wrapper effects.
Run both with the contained
runner and `--allow-explicit-tiers`. These are native consumer controls, not
real mux/Copilot interaction, supported uninstall or installed rollout evidence.

`libs/agent-procutil/tests/test_shell_caller_owned_native.py` is an on-demand
native SDK-lifetime packet, not a required PR actor lane. It uses the actual
PowerShell adapter, private Python worker and canonical source-only lifecycle
archive. Two cycles each cover callback completion, abrupt callback exit,
original PowerShell death and runspace cancellation. Child/grandchild identities
are pinned before release; all must be retired before observer cleanup can
establish a pass. These controls are distinct from source/argument compatibility,
interpreter prerequisite, and desktop visibility qualification. Native platform
skips do not qualify another platform. Actor-free admission/framing and
pipe-bound Job controls live in `tools/promotion_unit_tests/test_shell_caller_owned.py`.
The eight native offer cases create their file-backed capture, cold-child gate
and process/I/O fixtures before taking the parent-handle baseline. Those fixture
resources stay held through the SDK operation and exact Job-handle balance checks;
there are no `communicate` reader threads in that measured interval. The child
cannot invoke the SDK until the baseline gate opens. Existing permission/ACK/abort
bytes and case identities are unchanged; no count offset or tolerance is applied.
`test_offer_fixture_contract.py` separately checks that sequencing, all eight
branches and leak/noninheritance refusal with native dependency doubles, plus a
bounded Linux gate control. It does not qualify Windows.
The separate Linux-only `test_shell_completion_native.py` checks actual
returned and abrupt exits with both zero and nonzero statuses. It requires the
callback-return witness independently of the reaped status and does not
repeat the desktop matrix or qualify Windows behavior.
Separate cell-caller cases in the same native test file exercise original-shell
loss and the shared deadline while payload hashing is blocked and while a real
nested installer owns a descendant. A transport case returns non-ASCII stdout
and stderr despite a legacy Python output environment. These are not added to
the opt-in 44-case SDK desktop roster and do not replace its qualification.
The cell hash's actual bytecode-cache and `.git`-named synthetic-file controls
remain in the on-demand packet, not promotion units: the latter's existing
bytecode-attribution and Git-storage effect refusals stay intact.
`plugins/agent-machines/tests/test_cell_construction_inputs.py` separately runs
the actual attached construction protocol against a copied SDK snapshot with
real namespace/snapshot/slot receipts, then checks every selected byte. Its
offline UV build runs the real shipped dependency-policy CLI and a backend that
writes build/egg-info artifacts, proving those writes stay in the canonical
retained-input copy and its cleanup settles. These are native process tests,
not admitted promotion units or Windows desktop qualification.
The fixture's identity report is admitted only after a separate marker confirms
the producer completed its closed-file publication. A failed admitted read stays
an error; the fixture does not suppress permissions failures or retry them as a pass.
The daemon shell-lifetime fixture uses the same admission boundary: `ready.closed`
is published after the `ready.json` rename returns, and its reader does not open
the report before that marker. The original wait and identity-pinned cleanup
bounds remain unchanged.
`tools/promotion_unit_tests/test_daemon_fixture_publication.py` supplies five
admitted Python reader/wait controls. Its separate producer-order assertion reads
the PowerShell fixture and runs through the bounded source-check path, not the
promotion packet's Python/build-data input contract.

The separate opt-in desktop witness is
`libs/agent-procutil/tests/sdk_visibility.py`, with its co-located pure observer
and Win32 bindings. It is not a pytest-discovered test or a daemon. Its visible
mode requires explicit single-control consent, a source-bound request, a normal
local unelevated desktop and the admitted outer native Job owner. Job membership
alone does not establish that producer's limits; do not invoke the visible mode
directly or bypass a hosted-only fixture's guard.

On an actively used workstation, do not require the operator to stop using the
desktop so an unattended qualification can pass. The visible calibration
deliberately activates a window, and whole-desktop quiet capture can reject
ordinary operator activity. Neither is appropriate for an active workstation
without a separately arranged desktop-testing window.

The `functional` mode runs the same exact, source-bound native packets through
the same private parent handshake, finite workers, cleanup and JUnit checks,
without creating a calibration window, querying the foreground or requesting
foreground activation. Its explicit `functional-worker` path checks the
existing outer Job, local-console session, token, station and input-desktop
prerequisites without reading a foreground HWND or window owner. The ordinary
`worker` used by desktop qualification retains its complete foreground
visibility and same-session checks; there is no fallback between these paths.
Its request has the probe's source and packet fields but **no**
`visible_controls`; it also rejects `--allow-visible-control-once`. It writes
`functional-result.json` with `kind: sdk-functional`,
`visible_controls_attempted: 0` and `desktop_qualified: false`, never a
`visibility-result.json`. This is process/protocol/lifetime evidence only:
it does not certify the absence of unintended windows or focus changes.
Desktop qualification remains a separate gate; do not relabel functional
results, normal operator observations or a failed calibration as desktop proof.
Both modes retain the existing whole deadline and 150-second worker cutoff,
complete before/after source hashes and exact literal packet identities.
Functional source hashing must finish before native admission. Each packet
rechecks the original worker cutoff immediately before constructing its child;
the shared child rechecks its unchanged request deadline after publishing the
request and immediately before native process creation. Slow hashing, parent
queries, earlier packets or request-file writes cannot launch a late worker.

The explicit `process-safety` and `process-safety-worker` modes go further:
they do not load `user32`, query a window station or input desktop, inspect
foreground/windows, activate controls, or synthesize input. Their shared
`sdk_native_win32.py` binding retains native birth-pinned process handles,
original-parent pipe authentication and owned-only retirement. Both the
coordinator and worker require an existing outer Job and an unelevated token;
the outside owner still proves that Job's identity, resource limits and final
settlement. An arbitrary Job-membership result is not that proof. No console
or desktop admission is borrowed, and no fallback selects another mode.
The same exact source-bound SDK, offer and startup packets, stream bounds,
worker deadline, cleanup and JUnit checks remain in force.
The process binding's command-polling `pump()` deliberately dispatches nothing:
it has no desktop message queue. The desktop subclass retains its actual bounded
message pump. Real command/generation-boundary and worker-handshake controls
exercise this interface without mocking past the command helpers; pipe EOF,
framing, nonce, stale-generation and deadline failures remain explicit.

Process safety accepts no `visible_controls` or original-window fields and
rejects `--allow-visible-control-once`. Its separate
`process-safety-result.json` has `kind: sdk-process-safety`,
`desktop_observed: false`, `desktop_qualified: false` and
`visible_controls_attempted: 0`. A successful process packet does not prove
zero popups/focus changes, cold construction, the separate finite-writer
helper-loss/runspace matrix, or installed runtime behavior. Those acceptance
rows remain distinct. Source preparation does not authorize native execution,
and the older console-admitted functional fixture is not a no-desktop fixture.
`tools/promotion_unit_tests/test_sdk_process_safety.py` exercises the actual
process factory and binding bodies with native actors substituted at their
boundaries; `test_sdk_process_commands.py` additionally executes the real
worker command protocol and preserves the desktop dispatch override.
Targeted unit invocations use the canonical packet's explicit
repository root and importlib import mode, preserving one helper/error identity.

The explicit outside entrypoint is `tools/native_process_qualification.py`.
It requires `--execute-accepted-scope`, a four-field receiver request, an
already-created empty ordinary `--runtime-root` and the original monotonic/UTC
arguments. It invokes the existing containment owner exactly once, with its
unchanged limits and fresh 8 GiB available-memory admission. It never uses a
temporary-directory context to delete retained evidence.
`tools/native-process-qualification.ps1` forwards the 19 accepted arguments to
`tools/native_process_stage.py` through setup-provided PATH Python. This stage
inherits containment; it never creates another harness Job or calls the owner.
It launches the existing process-only fixture, waits for its actual exit,
checks all four worker cleanup results and independently verifies all 44
literal JUnit identities before publishing work-completion evidence.
The process-only request carries its original monotonic origin, so source
verification and adapter startup cannot restart the 150-second worker cutoff.
No optional origin field is admitted by the old functional or desktop modes.

This adapter uses a distinct version-1
`native-qualification.process-preparation` manifest at
`tools/prepared-manifest.json` in an ordinary source-tree snapshot. Its exact
fields are `schema`, `version`, `files`, `nonce` and `case_packets`.
Each `files` row has `name`, `bytes` and `sha256`; names are canonical
source-root-relative paths, not launcher-relative basenames. The complete
adapter/owner, procutil package and SDK fixture closure must be included.
The prepared nonce and four literal case packets are data bound by the accepted
manifest hash. They neither grant execution permission nor replace explicit
outside-owner scope acceptance. Flat historical functional manifests and their
evidence remain unchanged; an old preparation is not silently upgraded.
The prepared tree contains exactly those files plus its manifest, no bytecode
cache or indirect entries. Runtime evidence stays outside that immutable tree.

The new terminal starts with `PROCESS SAFETY START` and explicitly reports
`process-safety-only`, never `functional_positive`. Its complete artifact
index covers the candidate request, report, JUnit and captured raw streams.
Only the existing receiver's actual exit and the outside owner's final
Job/handle/lifecycle/environment checks can produce an
`original-owner-settled-v1` result. Candidate failure, changed source, expired
original clock or uncertain cleanup retains files and stays nonpositive.
Actor-free wiring controls live in `test_native_process_driver.py` and
`test_native_process_stage.py` under `tools/promotion_unit_tests/`; these are
source qualification, not permission to arm or execute the native fixture.

`tools/native_qualification_receiver.py` supplies an explicitly selected,
source-managed result receiver when the command transport cannot witness final
native exit and post-capture timing. It adds one process inside the existing
caller Job, never another Job or a larger deadline. Its immutable request has
exact `command`, `scope`, `cwd` and `manifest_sha256` fields. The command is the
accepted PowerShell launcher invocation with no profile, interaction or shell
command substitution. The receiver, shared phase contract and launcher belong to the complete
hash-bound prepared manifest.

The receiver requires separate `--original-start-monotonic`,
`--original-start-utc` and `--runtime-root` arguments from its outside owner.
They do not add fields to the four-field request or change the 13 base
PowerShell arguments. It forwards the supplied origin as `OriginalStartMonotonic`
and `OriginalStartUtc`, and the already-created root as `OriginalRuntimeRoot`.
These six suffix arguments are separate from the unchanged 13 base arguments;
the launcher and inherited stage must validate and reuse that original root,
not create independent GUID roots. No entrypoint may replace the supplied
origin with its own start time.
The existing 165-second work, 150-second worker and 180-second whole-bound
checks include setup, source verification, capture and publication.

`tools/plugin_test_containment.py` accepts an optional, one-shot
`Qualification` observation sink from `tools/native_qualification_contract.py`.
The existing outside driver calls this owner once, upstream of the receiver.
The original assignment/readiness worker starts the receiver only after Job
assignment; receiver, PowerShell, non-owning controller and candidate workers
inherit that same Job and consume its unchanged process, memory and TEMP limits.
The downstream stage never calls `run_contained` or creates another harness Job.
Ordinary callers retain their existing relative-limit API.

`tools/plugin_test_environment.py` owns the shared environment isolation and
pure registry-value comparison. The containment module re-exports its public
environment API in both package and standalone import modes; the registry
reader, refusal errors and native Job lifecycle remain in the original owner.
Preparation resolves UV configuration before redirecting user directories,
using the dependency owner's configuration reader, precedence merge and private
snapshot writer. Existing UV credential inputs remain available without
restoring host service roots or changing feed admission policy.
Preparation additionally requires the **selected UV executable** to support
`uv auth dir`. The preparation owner uses the dependency owner's executable
and environment qualification to capture one UTF-8 absolute
authentication-directory path before home isolation; an explicit
`UV_CREDENTIALS_DIR` must be absolute and agree with that metadata.
The read-only metadata operation receives only authentication-location and
native execution inputs, not host service/installation participants or ambient
GitHub tokens. It has a 20-second owned bound including a five-second cleanup
reserve, with 32 KiB stdout and 16 KiB stderr ceilings. Unconfirmed cleanup
retains its native custody and preparation sandbox. Unsupported, missing,
malformed or failed metadata refuses **before installation**, with an actionable
selected-tool requirement; it never guesses UV defaults or retries authlessly.
Select compatible UV in the invocation's existing tooling context or correct
the explicit directory input. No OS/user tool upgrade is automatic, and this
is a test-preparation capability contract, not a global installer version floor.

Only UV preparation receives the captured location through `UV_CREDENTIALS_DIR`;
capture never reads or copies credential contents. Actor environments reset
that variable to their own private directory, including host-state opt-ins.
An independently qualified private fixture may supply its own synthetic
authentication input after isolation. Configured resolver/keyring settings
remain within the existing dependency owner, but external keyring state is
**unqualified**: this does not claim to restore file-backed or native keyring
contexts. The opt-in
`COPILOT_EXTENSIONS_UV_AUTH_QUALIFICATION=1` control in
`tools/test_preparation_auth_context.py` uses only an offline synthetic store;
its selected-UV result is independent native qualification, not promotion-unit
actor admission.
The same preparation boundary retains the qualified **netrc location**, not its
contents. UV's [documented rule](https://docs.astral.sh/uv/concepts/authentication/http/#netrc-files)
is explicit `NETRC`, otherwise `~/.netrc`; the existing native-feed owner uses
original `HOME` on POSIX and `USERPROFILE` on Windows for that home. Preparation
requires an absolute explicit `NETRC` or absolute original home, and refuses
unqualified locations rather than guessing an OS fallback. A missing file is
not manufactured or populated. Actors always receive a private `NETRC` file
location, including Windows case aliases and host-state opt-ins. The opt-in
netrc fixture uses only an owned loopback index, synthetic credentials and
selected UV resolution without installation or external network access; it
checks default/explicit preparation success and actor refusal.
Windows Job teardown reuses procutil's pinned-member native-exit barrier.
Zero active-process accounting alone does not prove that native exit or pending
I/O has completed. Unconfirmed retirement retains the same Job witnesses on
`UnreapedProcessError.job`; it cannot publish successful settlement flags.
An expired whole bound still requests termination of that exact owned Job,
without starting another verification interval or claiming settlement.
Unavailable preliminary accounting remains unclean, but cannot skip the exact
owned cleanup attempt within the same original bound.

The qualified stage publishes source/scope/origin-bound work-completion evidence
only after actual candidate and worker exits within the original work cutoff.
That evidence permits report-only retirement until the original whole cutoff,
not another 15-second allowance. Missing, changed, failed or late evidence
cannot extend work. Readiness setup, worker handshake, Job close, reaping,
lifecycle locks/cleanup and monitor settlement all consume the supplied
absolute bound. A synchronous operation can still overrun; late completion
is a failure, not a hard-real-time return guarantee.

An inside-Job terminal is explicitly `descendants-settled-v2`, with
`original_job_drained: false` and `original_owner_settlement_required: true`.
The receiver observes actual launcher exit, settles both bounded streams,
closes its process handle, verifies the stable descendant-evidence index and
records fresh post-capture/post-verification times. It cannot observe its own
Job becoming empty while it remains alive.
The receiver rechecks its complete prepared source after native exit, preserves
raw captured bytes and refuses an expired bound,
uncertain cleanup, mismatched scope, changed source, duplicate evidence or
unindexed file. It never kills, retries, creates a Job, inspects the foreground
or upgrades functional evidence to desktop acceptance.

Only the outside original owner can combine that provisional report with
actual receiver exit, bounded raw capture, worker reaping/handle closure,
zero-active Job observation **before** Job-handle closure, settled lifecycle
cleanup/monitor and an unchanged environment audit. Forced drain, held cleanup,
live readers or missing observations remain nonpositive. The receiver's two
raw streams remain capped at 1 MiB each; outside stdout capture allows the
derived 4 MiB + 64 KiB envelope for their base64 representation and terminal
copy, without increasing any process-tree allowance. Final retained-file
indexing and output/flush/exit observation remain within the original whole
bound and must be supplied by the reviewed outside driver/transport.

Pure/fake-process controls do not qualify Windows. The new owner/receiver
binding and upstream driver/stage preparation require fresh source review and
separate native execution; historical owner bindings and their evidence are
retained unchanged, never relabeled as the extension's qualification.

The `passive` mode creates no window and changes no foreground state: it checks
the native bindings, complete raw-event/poll capture and private-pipe identity
handshake. After independent source review, the separately authorized visible
mode consumes one control attempt, requires raw show/foreground/original-
restoration/retirement witnesses, then observes 44 cases inside one uninterrupted
quiet epoch: the exact 32-case SDK lifetime matrix in two disjoint 16-case
packets, eight real Windows Job-offer protocol controls, and startup receipt-EOF
and malformed-nonempty-receipt unwinding on both PowerShell hosts. The latter
passes an actual canonical lifetime offer through deliberately malformed JSON,
requires refusal before the callback and rejects unknown native settlement.
The offer controls cover absent/invalid
permission, explicit unadopted abort, receipt-write failure, absent/invalid ACK
and normal adoption, including exact parent handle-table settlement. They test
the native protocol, not every possible shell cancellation instruction.
The probe request's `case_packets` supplies all 44 literal parametrized node
IDs in that order. `sdk_visibility_cases.py` rejects broad function selectors, overlapping
packets, source escapes and incomplete host/composition/mode/cycle coverage
before any native binding or visible attempt. Each isolated worker runs at
most 16 cases, retires inside that same observer and writes its own exact
literal JUnit; all four reports must cover the declared matrix. Calibration is
never repeated to partition tests, and the original whole deadline is not
rearmed between workers.
Each foreground request calls `SetForegroundWindow` once and retains its return
separately from the observed outcome. Within a one-second sub-deadline capped
by the existing control deadline, it pumps only its own messages and verifies
the visible target's HWND/PID/TID/query-pinned birth at the actual foreground
endpoint. A nonzero return without that endpoint cannot pass; an already
matching endpoint does not by itself replace any raw calibration witness.
The deadline is rechecked after pinning and native queries immediately before
the foreground request, and after parent queries before visible creation.
An expired prerequisite cannot start a late foreground or window effect.
The [Win32 contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
does not document extended `GetLastError` information, and
[activation can be asynchronous across input queues](https://devblogs.microsoft.com/oldnewthing/20161118-00/?p=94745).
No input-queue attachment, simulated input, lock bypass or system setting change
is used. The same pinned checks cover restoration and failure cleanup, without
rearming the original whole/control deadline. Raw show, foreground, exact
original restoration and retirement remain independent required witnesses;
failed or missing evidence never becomes a passing qualification.
The canonical arguments explicitly retain all four pytest temporary sessions,
including failed cases, so the fourth packet cannot prune the first SDK
packet's closed publications before final reconciliation. The policy is
`tmp_path_retention_count=4` and `tmp_path_retention_policy=all`, passed as
literal command options rather than inherited configuration. The cold-source
regression runs four real pytest sessions over synthetic cases with one shared
temporary root and verifies that all earlier evidence survives. It is not
native SDK or desktop acceptance.
Each worker sets `--rootdir` to the verified SDK tree while keeping its empty
configuration file in the output directory. Without that explicit root,
pytest can assign an empty filesystem node ID to an individually selected
external file and emit an empty JUnit `classname`. The strict source-identity
check requires the exact dotted classname derived from the source path under
that SDK root, including its complete prefix. Empty, missing or suffix-matching
unrelated class names are refused; the gate is not relaxed to fit
a failed artifact. The separate cold metadata regression reproduces that
failure and verifies the explicit-root form using synthetic, actor-free cases.
It preserves the original 20-ms poll, 0.5-second coverage/delivery
ceiling, bounded raw stream and same-thread hook cleanup. Unknown/unrelated
foreground events remain evidence, never a process-name-filtered zero.
Foreground polling independently captures a directly visible HWND, PID, TID
and query-pinned process birth before and after each enumeration. Microsoft
documents that `EnumWindows` enumerates top-level desktop-app windows, not every
foreground window; an observable foreground can therefore be absent even from
a successful enumeration. The trace preserves that absence without inserting
a fabricated enumeration member or applying a window-class allowlist.
Null, invisible, exited, changed or unqueryable foregrounds, failed query-handle
closure, enumeration failures and conflicting enumerated ownership still refuse
qualification. Enumeration and direct captures are non-atomic endpoints, not
proof that no intervening change occurred: the same unfiltered raw WinEvent
stream, calibration witnesses and original coverage ceilings remain required.
Its portable policy controls are in
`tools/promotion_unit_tests/test_sdk_desktop_observer.py`; actual desktop proof
is a different lane and must never appear in required unit-only CI.
The distinct scoped-root foundation is documented in
`libs/agent-procutil/README.md` under pre-resume observation admission.
`libs/agent-procutil/tests/test_windows_observation_spawn.py` checks the existing
owner's opt-in barrier and original cleanup with synthetic native calls.
`libs/agent-procutil/tests/test_sdk_scoped_observer.py` uses synthetic USER32
filters and portable pump threads to check scoped acquisition, source/epoch
binding, visibility/focus violations and unqualified coverage/cleanup failures.
These threaded controls are outside the admitted actor-free promotion packet.
They acquire no native desktop and do not qualify opaque CMD descendants,
terminal attribution or calibration. The independent SDK case is
`libs/agent-procutil/tests/test_caller_owned_native.py::test_scoped_observer_managed_root`.
It uses the original SDK finite owner and requires its exact source-admitted
enrollment; no application-specific selector or compatibility alias is accepted.
Its native driver integration and actual execution are independent SDK gates,
not MCP prerequisites. Actor-free collection and its default skip are not native
qualification; the whole-desktop self-profile cannot enroll it.
Integrated synthetic launch controls also cover successful observer admission
followed by native resume failure, independent original Job cleanup failure,
same-exception propagation and retained hook/pin custody. Context exit and
explicit close do not replay uncertain native closes or qualify an unfinished
observation.
The source manifest must include the runner helpers and the explicit SDK
closure declared in `sdk_visibility_sources.py`, all beneath the selected
SDK root. It must match that exact closure, not mix in separately located
runner/observer copies. The running runner and every already-loaded helper
must also originate at their declared locations in that same SDK tree.
Both caller and isolated worker refuse a mixed-root request before native
binding or any visible attempt and verify the source hashes; the worker
binds its source-only procutil import before pytest, never an ambient installed
copy. `test_sdk_visibility_source.py` is the separate cold `-I` subprocess
regression, including real pytest collection, not a promotion unit.
Owned-child retirement and incomplete-control restoration stay inside their
capture epochs, including error paths. Raw witnesses require a strictly newer
generation tick than the private command boundary; the canary waits for that
tick before acting. Same-tick delayed callbacks cannot qualify restoration.
Each worker attempt is recorded before construction, including attempts that
fail before readiness. After bounded reader retirement inside the capture
epoch, the report retains the actual exit status, partial stdout/stderr,
exact base64-encoded stream bytes, reader errors and cleanup status. A cleanup
failure is recorded without replacing an already-raised primary failure.
An exited child with drained readers cannot turn a literal prerequisite error
into an unexplained admission timeout.

Agent-worktrees' `test_launch_seed_native.py` covers the finite backend and setup
provider: retained one-time seed claims, literal argv, existing prompt-free
execution observations, PID/birth and incarnation refusals, definite-spawn
failure restoration, uncertain cleanup and terminal no-replay behavior.
These controls do not qualify terminal/Job/install-removal behavior. Setup
controls retain the existing host's PID/birth, keep the prompt unclaimed until
backend dispatch and release admission-only preparation before the child lifetime.
The same packet checks default JSON compatibility, the finite provider's
read-only contract declaration, counted NUL preparation replies, literal
argument/environment fidelity, invalid-format/callback refusal before effects
and held-seed/unknown handling for an unrepresentable reply. Empty-executable
controls refuse preparation before seed claims or process queries. A real Bash reader
control decodes the NUL fields without evaluating injected shell text. Git Bash
execution is a framing control, not native Linux/macOS, terminal or production
POSIX launch qualification.
`test_native_seed_reply_shell.py` executes the shipped Bash reader,
checking literal arrays without environment mutation or command evaluation,
inherited versus empty replacement environment, malformed/truncated/trailing
frames, stale-output clearing and prompt-free failures. The malformed cases
share one Bash process with `nocasematch` both on and off, reject uppercase
identities/markers and preserve the caller's option and locale. A separate
xtrace control checks private-field suppression and restoration after success
and refusal. EOF-status fault injection covers interrupted and timeout reads;
actual closed-descriptor controls exercise refusal with nounset on and off,
and before the header or after actual header/environment reads.
A native POSIX control prewrites the complete frame into a nonblocking pipe
while retaining a live writer, requiring refusal rather than accepting EAGAIN
as EOF. The fixture executes Bash in place under the finite process owner.
`test_session_shell.py` checks complete copied POSIX
setup assets and refusal before partial publication when the reader is absent.
The real-process reader controls are not marked as fast structural guards;
the explicit native acceptance jobs select the file directly.
The existing callback inventory/publication controls cover its installer-owned
copy scope. These are framing/copy controls, not finite callback execution,
foreground terminal, PID/birth or native installation-survival qualification.
`test_native_seed_phase_shell.py` executes the copied finite transport against
synthetic selected-runtime providers. It checks exact pipe fields and argv,
closed provider stdin, retained caller stdin/descriptors/cwd/environment,
actual nonzero producer exit after a complete frame, and current-slot
reselection without fallback. Completion-field controls refuse a provider's
forged trailer and keep the supervisor's per-call field out of the provider
environment even with caller `allexport`; the standalone reader's default frame
is unchanged. Malformed-header and malformed-completion controls keep the
provider hung after its invalid output and require it to be gone before the
caller receives refusal, not merely after the outer test owner cleans up.
Native POSIX controls additionally check the
ten-second deadline, settled provider groups and descendants holding
the reply pipe. An escaped-session writer arms a hard 12-second lifetime before
escaping, publishes readiness only afterward, and retains stdout until expiry.
Both valid and malformed frames must refuse within the original work deadline
plus settlement grace; the fixture separately observes the writer retired
before ending. This is bounded I/O qualification, not cross-group custody.
The copied test helper's private status-only control file must be removed after
every returned call, and a missing writable control path refuses before the
provider starts. Caller `noclobber` must survive a successful call; an actual
control-write error must refuse within the bound without exposing its path.
Reader controls reject noncanonical deadline arithmetic and
require all fields and EOF to share one deadline.
Synthetic shell providers do not qualify real bootstrap
startup performance, capability discovery or native backend ownership.
`test_launch_seed_contract.py` checks exact selected-slot descriptor discovery
and legacy selection for missing, old, malformed, failed, changed or
custody-unknown providers.
The POSIX cases in `test_session_shell.py` check conditional native selection
and copy-only relocation without taking over cold prompt/admission custody.
Admission-only/native-resume plans retain the legacy exclusion owner.
Shell controls also bind discovery/preparation to one selected generation,
reject cross-slot descriptor caching during an A-to-B-to-A selector race,
chain or clear repository EXIT traps without losing the setup return callback,
and warn on observer status 3 without another mutation or a changed backend code.
`tests/native/test_native_seed_setup_posix.py` constructs one real disposable
runtime and invokes the actual copied normalized setup, including its wrapped
entry. No-seed controls retain the legacy owner; new-seed and resume-seed
controls check literal argv/environment, stdin/stdout/stderr, direct Bash
parentage, one-time seed completion and nested actual return codes.
Real priming controls preserve exported Python overrides (including macOS
Python's consumed launcher variable), empty/unset/unexported states, the new
backend's execution hint and repository trap status, while intercepting any
environment value passed to `env` argv. Losing the selected runtime before setup must leave the seed
unclaimed with no execution or fallback replay.
The real PTY control retains all three terminal descriptors and foreground
process-group ownership, keeps the backend alive beyond the finite-provider
deadline, then sends terminal input and Ctrl+C. Bash must retain its original
PID and finish both return observations with the backend's actual interrupt
code. These controls do not establish real Copilot/mux survival, supported
uninstall, installed rollout or the parent effort's completion.
The existing optional installation-context acceptance workflow's `native_launch`
input also selects this focused reader/phase/copy/consumer packet on Linux and macOS, using
`/bin/bash` and explicitly requiring stock macOS Bash 3.2. It stays outside
required PR CI; the Windows terminal packet selected by that input is unchanged.
The same optional Linux/macOS jobs select Manager's
`test_launch_session_unwrap.py -k shell_pane_environment` component controls.
They execute the production export/flag-building blocks and observe literal
tmux argument delivery for empty values, quotes, multiline values and unchanged
AHP credential exclusions. They do not qualify a live tmux server, complete
Manager launch or installation survival.
The copied POSIX consumer packet also feeds those actual Manager-produced
environment arguments into the unmodified native setup and requires a direct
Bash parent, literal backend environment, retained input and completed receipts.
It does not replace that component path with a renderer-admission mock.
It executes the production command-array/interpreter-refresh blocks and covers
quoted-empty initializer behavior. Native host-preservation controls execute
the matching Bash and PowerShell refresh blocks, and JSON resume coverage
checks direct native eligibility without an explicit no-mux override.
That packet also explicitly selects `test_launch_seed_native.py` so the
setup-only provider birth query exercises the canonical native OS helper,
not a Bash ABI substitute. It covers caller-provided identity compatibility,
provider-query JSON/NUL reply and seed-custody parity, pre-effect refusal of
unreadable/invalid identity, and PID/birth replacement during preparation becoming
unknown. Backend spawn callbacks still refuse omitted birth tokens; the actual
read-only descriptor advertises the setup capability without preparing a launch.
The real PowerShell
seed-delivery consumer uses the existing finite process-tree owner so a timeout
settles descendants rather than blocking on inherited capture pipes.
The Windows-only `test_native_seed_shell.py` packet substitutes the finite
provider but executes a real child through the native adapter. It checks
literal argv, retained PID/birth, exit, OS start refusal, uncertain receipts,
absent end and exact redirected stdin/stdout/stderr. It is not interactive TTY,
Ctrl+C, pane-Job or installation-survival evidence. Whole copied-setup controls
exercise the actual parameter binder, literal argv (including date-shaped
strings), child ownership, correlated returns and absent end. POSIX native
integration remains separate.
The optional native-launch Linux/macOS jobs also select
`tests/native/test_native_seed_exec_posix.py`. It invokes the dormant
exec-replacement phase beneath stock Bash, checking the same PID and canonical
birth across exec, exact literal argv/stdin/streams, actual success/nonzero/
signal exit, closed exec-pipe descriptors, no observer child under the backend,
definite exec-error restoration, fast-exit uncertainty and a bounded observer
timeout without replay. Actual SIGINT and encoding-loss controls keep the
pre-exec helper alive long enough to disprove success-shaped EOF.
An interrupt followed by an otherwise successful exec must refuse that exec,
not depend on a following encoding error to preserve unknown custody.
Non-Python `/bin/sh` controls send each available interpreter-ignored signal
to the actual exec-replaced process and require signal termination rather
than a following exit. Failure/interruption controls also require restoration
of the helper's original signal handlers on unwind.
A separate actual SIGINT during failed-exec handler restoration requires
non-success publication, closed pipe ownership and retained definite-failure
seed restoration, not a false spawned receipt.
It uses the real seed
and execution owners in disposable state, not a production launcher or host
installation. These provider controls do not establish copied POSIX consumer
activation, physical-terminal Ctrl+C, real Copilot or supported uninstall
survival. Required unit-only PR CI remains unchanged.
Closed stdout/stderr reader controls exceed pipe capacity and require both
actual child completion and the correlated return. Separate native controls
split PowerShell's selected location from the OS process directory and assert
the finite helper's and backend's actual cwd.
The explicit Windows `tests/native/test_native_seed_console.py` packet uses a
real windowless ConPTY and the canonical pane Job, both directly and under the
outer pane wrapper. It tests a first interrupt without exit, a synthetic
backend's own rapid-repeat termination policy, actual exit propagation and
kernel child teardown. The synthetic count/window is not Copilot's policy.
Control events are delivered by a bounded caller-owned helper attached only
to the test's pinned ConPTY, not through the test runner's different console.
This tests the native control-event path without depending on a synthetic
stdin reader's scheduling; physical keyboard input in a real mux remains open.
The fixture excludes pytest's captured std handles before creating the fresh
terminal and checks native console handles, not `isatty()` alone (which accepts
Windows `NUL`). The six adapter/outer-pane controls substitute finite policy replies.
Full copied-setup admission controls instead use an actual completed runtime
and check executable and same-host PowerShell-script backends through one
interrupt, child survival, nonzero exit and the correlated return receipt.
Neither lane is actual Copilot, real installer removal or live mux
detach/reattach acceptance.
The existing manual acceptance workflow's `native_launch` input selects only
the focused Windows copied-setup and mux-admission packets at the immutable dispatch SHA. It is
default-off, uses contained test roots without host-state opt-in, and does not
expand required PR CI or select unrelated derived-diagnostic matrices.

The explicit `tests/native/test_native_seed_runtime.py` lane constructs a real
source-attributed runtime in a disposable installation and invokes its current-slot bootstrap from copied
native consumers without replacing the finite provider. It checks backend
and copied-setup lifetimes without a seed and with staged new/resume intent,
literal arguments, real PID/birth and exit observations, one-time seed
completion, nested operation correlation, prompt-free audit records and
refusal of a different installation root. The fixture supplies no mux and
uses a synthetic backend; it is not interactive Copilot, real mux, host rollout
or uninstall/reinstall qualification.
Its independently committed fixture omits test suites from the runtime source
payload; it does not qualify full-repository source-retention latency.
The finite provider's subprocess import-graph guard also keeps preparation and
seed-daemon dependencies out of return-only callback startup. Monitor-path
controls retain already-loaded CLI overrides without importing the full command
surface merely to resolve the existing receipt owner's lock paths.
Finite seed/receipt direct fallbacks import their declared canonical writer
instead of the complete mutation roster. Registry controls verify owner identity,
unchanged invalid-request fencing, unknown-verb discovery and ordinary daemon
registration; no alternate mutation implementation or protocol version is added.
The native phase watchdog control measures the approved ten-second cutoff
against an actual stalled child, without retries or a resident waiter.
Run the expensive current-runtime matrix explicitly on Windows with
`python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_native_seed_runtime.py --allow-explicit-tiers --timeout 1000 --plugin-timeout 1600`.
The existing `native` discovery exclusion keeps it out of ordinary unit CI.

For the actual Worktrees Python launch owner, select
`tests/native/test_headed_worktrees_copilot.py` with
`COPILOT_DRIVER_HEADED_MUX_E2E=1`, `COPILOT_NATIVE_TEST_EXECUTABLE` naming an
existing native Copilot executable, and an existing PSMux/PowerShell installation.
Run `python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_headed_worktrees_copilot.py --allow-explicit-tiers --timeout 300 --plugin-timeout 600 --test-timeout 180`.
This focused lane uses the managed test environment, a loopback-only model
provider, disposable Copilot state and the runner's private mux namespace.
It checks literal startup input, fresh launch and same-session native resume,
broker prompts, real child PID/birth receipts, seed non-replay and exact
fixture retirement. A second control sends literal native terminal input,
checks that the old broker refuses further prompt delivery, then explicitly
stops through the original owner and resumes the same conversation through a
new broker. Each control checks the exact user history and provider request
count, with no replay of the rejected prompt. A third `manager-no-mux`
control invokes the actual Manager `launch-session.ps1` and Worktrees resolver,
using an unmodified private copy of the Manager scripts rather than a synthetic
resolved plan, through the explicit no-mux path. It checks
the same native history, broker, PID/birth and original-owner exit receipts.
The `manager-mux` control invokes that same Manager launcher without
`--no-mux`, from a separate private caller pane. It requires the caller to
return while the Manager-created pane and native broker remain alive, then
invokes the real caller from a newly named private outer pane before each
fresh/resume broker prompt. Manager reuse must
preserve the mux incarnation and native process births without another mux
creation or startup-input replay. Native execution completion is read from
the backend receipt, not inferred from the earlier successful caller return.
These controls use ordinary unseeded admission and an explicit native initial
prompt; they do not claim staged-seed qualification. The existing real
PowerShell cold-resume consumer instead verifies Windows refusal before the
backend starts, with its seed retained, when mux absence is unconfirmed.
Discovery uses an adapter to the real private mux, not the host's
default server or fabricated successful replies. Its only writes are creation
and session options/configuration for the fixture's UUID-named Manager target.
Outer fixture panes explicitly propagate their private PATH/profile with mux
environment flags and verify selection before invoking Manager; inherited
profile variables alone can be rewritten by PSMux. Cleanup pins both caller
and Manager session incarnations. The fixture preserves the
runner's registry/lifecycle context while scrubbing Copilot settings and
credentials. Its raw pane entrypoint uses the physical Python interpreter
with the same managed test packages, avoiding the
Windows venv redirector and ambient Python site initialization. The fixture
disables the wrapper's post-error display hold, not native execution or its
required process/exit receipts.
It does not install a runtime or qualify copied setup, Manager's externally
attached/new-window paths or the full embody planner, guarded-head or auth-wait
recovery, physical keyboard input or survival
after caller cleanup Jobs. Those remain separate acceptance lanes.

Driver's explicit `tests/native/test_headed_extension.py` lane uses the same
canonical private mux routing, with raw pane argv separated from mux options
by `--`. Two finite pane controls check actual script entry, PID and exit with
default and null fixture configuration, always using a private data directory
and rejecting inherited config/data overrides. PID and owner-result markers
are published atomically. The SDK/broker control selects `direct` and
`manager-wrapper` pane owners. The latter runs the actual Manager PowerShell 7
pane wrapper with its normal pane Job enabled and a private home without an
installed Worktrees runtime. Both owners preserve one native conversation
across WebSocket and TCP prompts and check exact pane, CLI and extension exit.
Run just these two controls with `-k native_headed_sdk`; this does not qualify
the full Manager launch-session planner or survival after caller cleanup Jobs.
The coordinator controls still invoke Python directly.
Unit isolation controls reject default,
foreign and whole-server routing before spawn, without mistaking child flags
after `--` for mux routing.
The focused `joined_session_reports_pending_read` control queries the executing
CLI's `session.rpc.tasks.list()` and `session.rpc.schedule.list()` without a
permission grant, checking the native `tasks` and `entries` arrays in the same
conversation. Missing/failed/invalid reads do not produce an empty snapshot.
This is SDK read-support qualification, not complete background-agent evidence,
active-work/schedule mutation coverage or automatic-idle retirement.

Manager's focused `tests/test_pane_startup.py` controls also execute its actual
persisted AHP-binding and final startup-serialization blocks against copied
launch commands. Inline and split resume arguments must be replaced, not duplicated;
literal drive paths and prompt text survive the final consumer. Companion
controls refresh the inner runtime before serialization, including recovery
commands. The Bash inner-owner refresh also recognizes the existing
`env BASH_ENV=` prefix and leaves native executables unchanged.
Its runtime-refresh and AHP grammar checks use native Bash on POSIX or Git
Bash on Windows, disabling MSYS argument translation only in that Windows
fixture. Git Bash coverage is not native POSIX lifecycle qualification.
The producer's Windows capability-absence
control leaves older callers on their legacy path. These are isolated contract
controls with a synthetic binding/token, not an authenticated AHP connection.
They preserve selector-shaped interactive/model/agent values and reject
runtime replacement of a native executable whose literal arguments resemble
the Python owner. Matching native-Bash AHP controls live in
`tests/test_launch_session_unwrap.py -k shell_ahp_binding`.
Worktrees' `tests/test_session_shell.py -k headed_bash` checks effective
`BASH_ENV` selection before copying and explicit empty-value pinning.
On POSIX, the same selection executes real Bash exit/exec primers against
refused admission; neither primer may run before the outer owner.
The AHP controls also retain the option after a bare `--resume`.
Both launchers use the bundled `bin/ahp-command.py` read-only adapter for this
rewrite. Its actual command/argument transport is exercised through both final
consumers: native positional tails are preserved, authoritative options precede
their terminator, and copied-wrapper/inner-owner separators remain intact.
The adapter travels with the launchers' existing runtime assets and uses the
existing Worktrees option table, not a new installed-peer capability. The
coupled `test_ahp_launcher_contract.py` pins this delegation and the existing
credential/post-exit boundaries. Environment prefixes retain assignments,
unset/chdir flags and env's own terminator before locating native options.
The supported env grammar uses the standard option parser, including attached
short-option values and combined flags; incomplete options emit no command.
Opaque env split-string forms are refused, not interpreted or silently rewritten.
Driver's `tests/test_headed_launcher.py -k native_validator` checks
selector-shaped values and missing-value refusal. The existing
`tests/test_session_host_core.py -k retained_headed_executable` control
passes a literal `--log-dir` value through the real retained archive and
verifies native exit. The Worktrees copied-wrapper controls call the same
validator on the delivered native argv; no native Copilot or rollout is
implied by these finite-process checks.
The `retained_headed_extension_options` controls execute the archive and
observe the real child's argv: extension flags precede the option terminator,
selector-shaped values remain literal, and only an actual experimental option
suppresses injection.
The `headed_selection` controls also keep a selector-shaped split `-C` value
from inventing a conversation, while preserving a real selected conversation.
The optional Worktrees `tests/native/test_native_seed_application.py` constructs
and installs the real wheel application through the canonical build/profile
owners in a private home, removes its disposable source inputs, and checks
current-runtime native admission. Disposable build inputs retain application
Python source, operational scripts, top-level build metadata and dependency
sources, not unrelated plugin surfaces or non-shipped test trees. Runtime
assets and notices remain in the real wheel.
Existing local `libs` are preserved on materialized release checkouts; a focused
copy/materializer/resolver regression covers that layout without building wheels.
It also builds the real installed CLI's launch
command and invokes the packaged finite native provider after removing source
inputs. Both scripts must belong to the installed nested environment, and the
completion marker must remain unchanged. Its private project registry is empty and
resident startup remains suppressed by the real pytest-owned test override.
This first-touch check is distinct from full Manager/Copilot launch qualification.
The focused `test_launch_cmd.py`, `test_session_shell.py`,
`test_native_seed_shell.py` and `test_native_seed_phase_shell.py` controls retain
legacy selection and literal argument transport, cover application-owned copying,
and refuse flat-interpreter fallback for an incomplete application. The Bash
controls use Git Bash on Windows; that is not native POSIX lifecycle evidence.
Set `COPILOT_NATIVE_TEST_EXECUTABLE` for the real offline CLI through the installed
command builder, wrapper and default setup. It reuses the startup/history
control's literal two-turn fresh/resume assertions and synthetic local provider.
Setup recovery suppresses host reconciliation; this does not qualify Manager,
headed-broker admission, setup hooks or survival across caller cleanup Jobs.
The admission case builds the application only once, whether or not native checks
are explicitly enabled; construction and consumers share its existing
300-second case budget. Without that prerequisite it qualifies only admission,
asset selection and the finite provider, not native Copilot history.

With the same native executable, existing PSMux/PowerShell prerequisites and
`COPILOT_DRIVER_HEADED_MUX_E2E=1`, select the separate installed broker case:
`python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_native_seed_application.py --allow-explicit-tiers -k owns_native_broker --timeout 480 --plugin-timeout 600 --test-timeout 480`.
It builds and installs once, deletes source inputs, and runs the Worktrees
execution owner from that actual installed interpreter/root without adding
the managed test environment's site-packages or copying source launch assets.
Both Worktrees and Driver Core imports must belong to the installed environment.
The real native fresh/resume flow checks one conversation, literal startup input,
broker prompts, PID/birth and terminal exit receipts, seed non-replay and exact
fixture retirement. Construction, install, source removal and packaged admission
must finish within 300 seconds; the complete native consumer runs in a separate
caller-owned child with the existing 180-second ceiling. The composed case has
a 480-second bound, not a claimed combined 300-second pass. The original
admission case retains its 300-second bound and the plugin aggregate stays 600.
This is installed execution-owner evidence, not the public Worktrees command,
Manager integration, desktop attachment or survival across caller cleanup Jobs.
The existing managed-environment controls remain separate and unchanged.

Select `-k public_embody` with the same installed-broker prerequisites and
900/1200/900 runner bounds for the public detached caller. That case builds and
installs both real applications, deletes their disposable source inputs, and invokes
the installed `agent-worktrees.exe --project <fixture> embody` against a private
project configuration and worktree record. Worktrees must discover the installed
current Manager's native pane wrapper; a Worktrees-only fixture does not
qualify this public Windows launch path. The packaged default setup selects
the native executable; recovery mode suppresses unrelated host reconciliation.
The fixture declares its private mux adapter through `session_path` and uses
the normal user-home `.copilot` layout. With no installed sessionStart hook,
it explicitly registers the observed native session through the installed
`bind-session` command and verifies that it becomes the worktree head;
this is not evidence for automatic hook adoption.
Fresh launch uses a literal explicit seed, cold resume selects the existing
conversation without a supplied resume argument, and live reuse must preserve
the mux incarnation without creating another attempt or replaying a seed.
The same broker, history, process-birth and exact retirement checks remain.
This checks the public detached-command boundary, not attached `copilot`, desktop
new-window behavior, setup-hook adoption or survival across caller cleanup Jobs.
The functional public-caller case has separate 420-second Worktrees preparation,
300-second Manager preparation and 180-second caller-owned native bounds.
Measured wheel/profile preparation can consume most of the original admission
case's clock before this distinct consumer even starts; this case is not a
300-second Worktrees construction-performance check. The existing 300-second
admission case and 300+180 installed execution-owner case remain unchanged,
as do the native startup and cleanup limits.

The adjacent `tests/native/test_manager_application_launch.py` builds and
installs the real Manager wheel application through the canonical standalone
source/profile owners, removes disposable source inputs, and exercises the
actual Worktrees launcher-discovery consumer. Manager's special installer
starts its resident: the fixture supplies a private home, mux-data root and
renewable native daemon-test lifecycle, and verifies settled cleanup receipts
even when the body fails. A successful body must have started an owned
resident. The 300-second case deadline is shared with cleanup, not extended.
The installed Manager supplies its packaged launcher through the existing
retention bootstrap and actual CLI version check; no synthetic health response,
runtime marker or legacy payload directory qualifies this flow.
`test_manager_canonical_runtime.py` preserves legacy paths and refuses missing
or foreign application resources without recovery-generation fallback.
Manager's focused `test_self_install.py -k app_only_self_install` and
`test_update_bootstrap_activation.py` controls retain foreign-root/package,
qualification, activation and startup-outcome refusals while accepting native
path aliases. These are distinct from full native Copilot launch, desktop or
all-caller-cleanup-Job qualification.
With both `COPILOT_NATIVE_TEST_EXECUTABLE` and
`COPILOT_DRIVER_HEADED_MUX_E2E=1`, the application fixture selects separate
`manager-no-mux` and `manager-mux` cases through its actual packaged launcher.
Run each selector in a separate contained invocation; construction and native
calls share each case's unchanged 300-second deadline, not a combined budget.
Fresh and resumed offline Copilot conversations must
expose the common broker, retain one history and publish exact native
process/exit receipts after disposable Manager source inputs are gone.
The mux case also reads the installed Manager's registry through its own
interpreter: each live CLI pane must match the private mux incarnation and
become an exact exit-code tombstone before the next conversation resumes.
The private adapter embeds its sole allowed write target and log path, and
reads the current namespace from a fixture-owned file; callbacks and residents
need not inherit routing variables from a caller that has already returned.
The constructed cases omit additional warm-reuse launches; the copied-script
mux control retains that separate coverage. Without the native prerequisites,
the fixture still constructs only one launcher-discovery case.
The Worktrees resolver remains the managed test runtime; this does not qualify
an installed Worktrees application, resident status painting, desktop attachment
or caller cleanup-Job escape. Manager's focused
`tests/native/test_pane_boundary_native.py -k validation_cancels` controls
preserve both native-command and script exit codes when the real pane reader
enters its wrapper in a nested PowerShell scope. Bash already reads its
child's `$?`; the automatic-variable shadowing correction is Windows-only.

Manager's `tests/test_shutdown_exit.py` also runs the application probe in
bounded children with the CPython 3.13 shutdown-workaround branch armed.
The real callable CLI version handler must finish and the resource JSON must
be emitted before the final hard exit; a failed version result emits no
resource report. This covers exit ordering, not native CPython 3.13 installation.

The optional native runtime-removal portfolio includes
`tools/test_installer_engine_uninstall.py`,
`tools/test_installer_engine_commands.py`,
`tools/test_worktrees_uninstall_custody.py`,
`tools/test_installer_engine_stop.py`,
`tools/test_runtime_uninstall_process.py`, CodeSpaces'
`tests/native_uninstall/`, Machines' `test_legacy_runtime_uninstall.py` and
`test_legacy_publisher_transaction.py`, Containers'
`test_containers_legacy_runtime_uninstall.py`, Index's
`test_native_runtime_uninstall.py` and `test_legacy_transaction_admission.py`,
and Vault's cross-adapter `test_native_uninstall_adapters.py`.
They use disposable roots and real native removal/custody boundaries; service
registration adapters remain isolated from host tasks and units. Controls cover
durable/unknown-byte preservation, retained lock identities, idle retry,
construction/retention/publication contention, default nested-engine removal,
managed/foreign-root refusal and live/reconnect transport negatives.
The command packet includes the recorded pre-queue Logger global-command
family, with exact recognition, edited main/auxiliary programs, a foreign
root and a missing PowerShell peer. Worktrees' existing completion-publication
packet additionally drives a real exited construction helper on PowerShell
5 and 7: original stderr/exit survives both completion and final cleanup,
an unfinished candidate is reclaimed, and a completed incumbent is unchanged.
These native controls remain in the on-demand portfolio, not required unit CI.
The command packet derives current native writer bodies from trusted sources
and covers exact owned forms, custom/foreign roots, comments and modified
programs, recorded unavailable publisher paths, independently executable cmd
forms and delegating-peer refusal. Common removal controls include finite nested
helpers and their own bytecode with unknown siblings retained, CodeSpaces'
root resolver modules, and explicit pre-mutation consumer/custody admission.
The Worktrees custody packet runs actual publisher/remover contention in both
directions against the existing whole-action command lifecycle, not a second
root-lock authority.
Worktrees' `test_native_uninstall_footprint.py` drives the complete native
entrypoints, staged typed/unattributed source custody, cold/unknown retry,
generation contention and pre-mutation refusal. A literal unrelated-module
refusal in a non-quiescent host namespace is a qualification skip only after
all fixture bytes and inodes are verified unchanged; it is not positive native
Windows evidence. Run the actual positive packet in an isolated process
namespace. Partition the Windows cases by removal/retry, refusal and remaining
candidate/custody controls rather than increasing their finite runner deadline.
Worktrees' `test_uninstall_project_commands.py` uses a real completed fixture
environment to exercise selected-source interpreter resolution without runtime
construction, exact owned-project command retirement and foreign-command
preservation. Copied account lookups are isolated before publishing ownership.
Explicit-name and actual-CWD inference controls cover missing-runtime refusal,
stamp-only cleanup, explicit precedence and completed source-qualified cleanup
through staging.
`tools/test_installer_engine_uninstall_darwin.py` covers bounded libproc/sysctl
inspection, incarnation/ownership/ABI/partial-read refusal and literal arguments,
including empty strings. Its native live-process case requires actual macOS.
The manual `installation-context-acceptance.yml` `runtime_removal` selection
runs that backend and the complete Worktrees entrypoint/project-command packets
on macOS at the dispatch SHA, under finite process containment. Mocked native
API controls on another OS are not native macOS qualification.
The hosted lane creates an ephemeral unprivileged account and an exact tracked
source copy, verifies its real/effective/saved credentials, and runs both packets
under that account. The runner account's mixed-privilege services remain outside
that census; production ownership refusals are unchanged. Elevated setup is
limited to the disposable hosted VM, whose teardown reclaims the account/copy.
Darwin resource polling uses the stdlib libproc sampler in
`tools/plugin_test_containment_darwin.py`, not a spawning `ps` observer that
can introduce a privileged candidate into the uninstall census. Unknown or
partial observations still fail explicitly; memory/process limits and owned
group cleanup remain enforced. Its focused controls include a real 32 MiB
resident child, independent RSS comparison, and unreaped-zombie versus
live-member accounting. Other POSIX adapters retain their existing behavior
through `tools/plugin_test_containment_posix.py`.
The native STOP packet derives daemon commands from actual unit writers,
preserves original admission across service-stop/PID-reuse controls, and runs
real Linux pidfd termination and birth-mismatch refusal against owned fixtures.
Index's transaction packet exercises the real admission and mutex code across
separate simulated elevation processes, without UAC or host task registration.
CodeSpaces' `test_cold_native_transaction.py` accepts `NATIVE_UNINSTALL_BASH`
for actual Bash 3.2 qualification. It checks read-only status without lock
capabilities, Python-free cold stamping, same-inode contention, attached-owner
EOF and the exact declared first-party snapshot-library closure.
Its native/Python gate controls also create private cold roots under a
group-writable caller umask while refusing unsafe pre-existing roots unchanged.
Machines/Containers native removal controls reject unsupported preview,
check, force and context/profile options before mutation, including trailing
options on their private adapters.
`tools/test_uninstall_path_candidates.py` exercises activated runtime venvs
ahead of eligible external interpreters, broken/aliased candidates and explicit
no-external refusal across native metadata callers. The common finite-removal
packet also checks that malformed deployment/binding/lifecycle/source records
refuse before any cleanup and retain every recovery/state byte.
Its Dispatch/Index packaged-removal controls run the materialized finite
entrypoint with no repository library tree, including actual POSIX runtime
deletion and preservation of configuration, unknown state and the lock inode.
`tools/test_legacy_transaction_interop.py` verifies Machines/Containers
publisher and removal admission against opposite native/Python lock backends,
retained legacy receipts and release on failure/EOF. Its real Linux kernel
controls also run under actual Bash 3.2 when that shell is selected on PATH.
The Windows `tests/native_uninstall/test_windows_uninstall_candidates.py`
packet covers actual broken-PE launches, later candidates, physical/base
closure, actual finite invokers and retained state on PowerShell 5/7.
Run its candidate/cleanup and invoker/base-closure partitions separately for
bounded execution. Its unsupported-version fixture verifies isolated-venv
`sitecustomize` version reporting; it is not Python 3.8 interoperability proof.
`tools/test_budget_packaged_installer.py` additionally starts the actual
materialized standalone Budget status entrypoints without a repository library
tree; this detects a packaged caller that still sources repository-only helpers.
Pull Requests' `test_install_lock_fallback.py` additionally exercises cold
materialized staging on Windows PowerShell 5 and 7, including source hashing
without the optional `Get-FileHash` command. Native platform skips are not
acceptance evidence for those platforms. These tests do not qualify abrupt
caller death, whole-suite removal around a running Copilot mux, fresh SSH/Gateway
reconnection or deployment, and do not enlarge the required unit-only packet.

## Optional owned Windows jump probes

The manual `installation-context-acceptance.yml` selection `ssh_headless=true`
runs the focused Linux portable suite and Windows ARM64 lane, with native ARM64
and emulated x64 Python parents, when other selectors remain false.
The Windows lane downloads the pinned public OpenSSH 10.0p2 ARM64 archive, verifies
archive and client hashes plus Microsoft signing, and runs the bounded
`agent-ssh` `test_headless_probe_windows.py` packet. It installs no SSH service,
enrolls no keys and contacts no SSH host.

Each console/GUI parent drives the actual probe through two resolved SSH jump
hops to an inert console-subsystem proxy. Two cycles cover transport deadline
and cancellation. Native Job members are pinned before teardown; evidence
requires their retirement, zero visible terminal windows or focus changes,
and no inherited descriptor metadata in the proxy. An intentionally visible,
test-owned canary must first be observed and retire, so an unavailable desktop
cannot pass as zero windows. Its window owner must match the spawned canary
PID; an unrelated terminal cannot validate observer sensitivity.
The fixture refuses shared hosts before native
work. Run no desktop reproduction of an unqualified path.
Each native parent also edits one controlled profile in place while retaining
the original cached config. The real selected client's metadata must observe
the new hostname and jump route without mutating that cache or dialing a host.
The same real metadata boundary also preserves explicit programmatic proxy
fields/options while allowing a CLI proxy selection to override them.

Mocked route/probe contracts are available through the contained `ssh-manager`
and `agent-ssh` targets. This lane establishes only its exact source/client/
parent combination, not authenticated remote reach, a released payload or an
authorized installed rollout.

The opt-in `installation-context-acceptance.yml` dependency-compile selection
also runs independent registered-marketplace provider and inventory partitions
on Linux and Windows, each retaining a six-minute job ceiling.
`tools/test_runtime_marketplace_source.py` uses real registered catalogs,
installed ledgers, independent Git clone/fetch and canonical preparation.
A test-owned Git transport maps synthetic declared remotes to independent local
repositories; no production origin or feed is contacted. Controls reject a
cache-created commit absent from upstream, preserve an older installed commit
when Main advances, refuse source/version/registration/ledger changes and
ambiguous caches, and separate two marketplace owners.
`libs/plugin-resolve/tests/test_native_git_inventory.py` preserves existing
ordinary inventory behavior and qualifies the reusable registered-cache proof.
These fresh isolated fixtures qualify the explicit provider, not normal
bootstrap, the production dependency closure, installation speed or rollout.

Bridge's on-demand `tests/test_venue_path_length.py` exercises ordinary
preparation and registration with an independent synthetic native installer.
Run `python tools/run-plugin-tests.py agent-bridge --test-file tests/test_venue_path_length.py --allow-explicit-tiers`.
The Windows packet reproduces native `WinError 267` at the older nested home,
launches a real Python child from the compact replacement, preserves older
bytes and unrelated settings, reuses the completed home without another
installation, and separates plugin/source/revision identities. Native boundary
controls cover 258-unit success, 259-unit refusal, UTF-16 surrogate pairs and
already-completed reuse; a genuinely deeper root refuses before installation
or settings publication. Portable controls retain POSIX layout. It uses no
live Copilot, feed, service, runtime deployment, OS policy or shared cache.
Native cases skip outside Windows; this is not hosted unit-only CI admission.

The opt-in `installation-context-acceptance.yml` admitted-source jobs also run
`tools/test_manager_source_install.py` and `tools/test_manager_source_native.py`.
Profiles, refusal controls and Manager reuse run in disjoint partitions on
each host, retaining the existing six-minute job ceiling rather than combining
every native source scenario into one growing serial job.
Its separate `normal` partition exercises
`tools/test_installer_source_build.py` against the real, hash-pinned
Setuptools 84.0.0 wheel. The workflow acquires that fixture before the
loopback-feed tests; the tests themselves make no external requests.
For local qualification, `TEST_SOURCE_WHEEL_REAL_BACKEND` names an already
acquired copy of that exact wheel. Without it, the same native constructor
controls use a small synthetic backend. Neither variant installs a host runtime
or changes a service. Controls cover per-project hits across fresh retained
workspaces, library-only source changes, explicit unsupported-contract misses,
pre-completion archive/content refusal, backend source/tool mutation and clean
retry. POSIX source-mode cases remain explicit skips on Windows.
Changing request witnesses or Windows Job ancestry must not change a wheel's
build identity. Job ancestry still reaches the owned build children, and other
build-environment changes continue to invalidate reuse.
Distinct build-requirement groups use independently admitted tool environments;
the lower-bound control also runs with the real backend. A synthetic
conflicting-pin control checks each actual backend's installed version and
fresh-slot wheel reuse without unioning incompatible requirements.
The phase controls prove that all selected groups finish acquisition before
offline construction, then remove the fixture feed's routes and require no
additional requests while the real constructor completes. Deterministic clock
controls give acquisition and offline work independent allowances, identify
the expired phase with its original deadline cause and preserve non-timeout
integrity refusals. Phase diagnostics stay on stderr; unsupported fallback and
the caller's subsequent installation are outside offline timing.
Every verified work unit renews its phase deadline, while transport emits only
at the existing bounded heartbeat interval and phase boundaries, not per file.
`test_manager_source_native.py` exercises the real standalone Manager adapter and immutable
constructor with a complete synthetic Manager/process-owner wheel closure:
offline construction, retained reuse, changed binding, missing first-party
input and failed-candidate selector preservation. A configured synthetic Git
source additionally traverses real native feed admission, source builds and
Manager `self_install`; a fresh Python process reuses its completed generation
with the feed unavailable and construction forbidden. The completion marker
and receipt remain unchanged, and the feed observes no additional requests.
Both invocations test an absolute selected UV with no UV on PATH, and a UV
command selected from PATH. Changing the authenticated feed account or native resolver mode subsequently
requires ordinary source admission instead of reuse; corrupt completion
evidence still refuses before cold admission. The same hosted Manager jobs
also alter a receipt URL, an installed archive digest and installed origin
JSON; each requires an integrity refusal, not cold repair or another feed
request. A real installed-module health failure still requests source repair. They
run package-version parsing and actual update-check regressions for source,
source-profile and repaired generations.
This does not qualify the production Manager dependency closure, default
marketplace bootstrap or performance. Fast adapter and self-install
contracts remain in the ordinary `worktree-manager` suite.

Native feed-capture controls exercise account changes and password rotation
through named environment, embedded URL and netrc credentials. They compare
the real keyed opaque identities, retain stable unchanged captures, and make
no feed request or machine credential-key write.

The same file's `test_native_capture_first_touch_is_stdlib_and_payload_local`
copies only canonical installer resources into an isolated payload and runs
the actual native capture in a fresh `-I -S` process. Repository and site
packages are absent; poisoned ambient imports cannot supply Packaging or
profile/build tools. Real retained UV projections preserve named authentication
bytes and capture identity, and retain offline, unsafe-index and insecure-TLS
refusals without an HTTP request. This qualifies the shared capture mechanism,
not admission-tool installation, normal-consumer enablement or a live feed.

`libs/payload-invocation/tests/test_runtime_admission.py` exercises the actual
opt-in SDK generator and copied toolkit in an isolated process. Its complete
entry-module imports, configuration and native-template checks reject repository
Manager/CLI/publishing adapters and assert payload-local first-party locations.
`tools/test_runtime_admission_toolkit.py` then exercises real source acquisition,
canonical preparation, native feed/lock capture, wheel admission, source building,
offline immutable construction, health and completed-generation reuse using only
that copied toolkit. A separate witness admits the actual dependency-free
Packaging seed, imports every toolkit entry in that interpreter and validates
both a complete profile and an incomplete-closure refusal. Its installed
distribution inventory contains only the selected Packaging wheel.
That cold interpreter also imports the toolkit's actual `agent_procutil` and
executes both canonical aggregate-wheel retention APIs. Exact selected bytes
survive retention; a changed transport digest or insufficient byte budget
refuses without changing the caller's input. No alternate extractor or
fixture-injected process helper supplies those paths.
These run in the source-build `toolkit` partition on Linux and Windows
(`dependency_compile=true`), separately from the source/profile and constructor
cohorts. This preserves every witness within the unchanged six-minute job
limits without expanding required unit CI.
The credential-free fixture retains the existing deterministic opaque-identity
seam; keyed authentication and credential-key ACLs have independent controls.
This is fresh toolkit-delivery evidence, not first-tooling admission,
normal-consumer enablement, production feeds or performance qualification.

`worktree-manager/tests/test_runtime_source.py` copies the actual Manager
package, process helper and runtime resources into both installed layouts:
retained slot payloads and wheel-owned `_runtime_assets`. Fresh `-I -S`
processes resolve a real local source while forbidding the Manager installer
and CLI imports. A preloaded private retained-runtime alias cannot satisfy
the public lease import; its actual loaded file must belong to that installed
layout. Both cases reproduced the missing-helper failure before the lookup
was routed through the existing read-only resource owner.

The existing Linux/Windows Manager source-build partition now uses the canonical
owned test boundary and isolated homes rather than bare pytest or a forged
contained marker. It retains the original admission/reuse files and separately
runs the source-acquisition file, including both installed layouts. The
six-minute job limit is unchanged; child packets retain explicit 300/120-second
bounds and fail on unsettled cleanup or a failing child. Integrity tests retain
all tamper refusals even when completion rejects changed evidence before the
detailed input owner. The freeze-deadline witness expires only after a real
buffered-file copy, not through an unused digest seam.
The acquisition packet explicitly binds Manager and its declared first-party
helper sources; validator tooling does not install Manager. A site-free child
checks the actual module origins through the same owned workflow boundary.
Acquisition fixtures use the existing compact `src` temporary-directory pattern
to keep nested local Git paths within Windows limits without changing runtime
paths, global Git settings, test selection or deadlines.

`tools/test_runtime_profile_bootstrap.py` also runs runtime source-policy and
actual wheel inspection in a fresh isolated Python process with the Manager
installer, promotion and publisher modules explicitly refused. Pure owner
controls preserve the single semantic inspector, byte/metadata refusals and
promotion's additional first-party source check. These are narrow import and
validation contracts, not complete toolkit packaging or admitted-tooling
consumer qualification.
Manager inventory/update fixtures select the shared configuration root through
`WORKTREE_MANAGER_ROOT`, not an attribute patch on the installer re-export.
The unsupported-source case reads that actual policy before forbidding engine
submission, retaining the refusal contract across configuration-owner changes.

`tools/test_runtime_source_reuse.py` covers bounded persisted evidence admission,
changed current selections, missing completed receipts, source/receipt corruption, current digest denials
and consume-time invalidation. Nonordinary selected slots, including dangling
links, refuse rather than becoming cold-source availability. The same constructor must refuse to construct
from a reuse-only receipt. Pure caller controls distinguish typed installed
health repair (which releases custody and reacquires real source inputs) from
integrity failures (which never trigger fallback). Prepared-source publication
also tests bounded Windows transient-lock retries without retrying permanent
errors or extending the original deadline.
Expired source-lock consumption retains an active typed subprocess timeout
and its diagnostics; successful consumption still checks the outer deadline.

## Optional stock Windows bootstrap building blocks

Select `bootstrap_file_guard`, `bootstrap_file_trust`, `bootstrap_path_guard`,
`bootstrap_drive_map` or `bootstrap_canonical_path` in `agent-codespaces`
through the bounded plugin runner. These repository-only helpers are not
distributed or enabled by the existing bootstrap selector. Native tests use
64-bit Windows PowerShell and PowerShell Core for the file guard and WinTrust.
Unsupported Core binding shapes must refuse before native library loading.
Linux runs the portable source contract with
explicit native skips.

The trust packet requires an existing official PSF-signed Python executable.
Set `EVALUATOR_BOOTSTRAP_TEST_PYTHON` to that file if the test interpreter's base
executable is not suitable. Tests copy it into a disposable fixture and never
execute the copy. They query the actual retained file's signature, including
unsigned/modified and mismatched pathname/handle controls, repeated state
closure, error cleanup and native-module identity refusals. Each captured,
windowless PowerShell child has a 20-second timeout within the runner's finite
Job/process boundary. No compiler, interpreter acquisition, foreign DLL load,
production registration or desktop positive control is part of this packet.

Core trust controls observe the actual typed record passed to the existing
by-reference WinTrust method. The native output's state handle must reach
cleanup even when a test proxy throws after the real verification call.
Additional release-error controls throw only after the actual owned library
reference is freed. Both cases must emit zero success objects and release
their temporary guard references before the shell exits.
Signer/provider success does not establish native dependency-search policy.
The loader controls pass through to the actual stock wrappers while checking
the System32 path, null reserved argument, `0x800` value and edition-specific
integer type. Free must receive that exact owned module reference; completed
load/free counts must agree before the consumer observes success.
The public NativeLibrary load API is not a supported replacement for the
flag-preserving CoreLib wrapper.

The guard packet runs sharing, no-follow, identity and failure/release controls
on both shell editions. Core-only final-path controls exercise the existing
managed `char[]` wrapper on the exact retained file/directory handle with a
bounded buffer. Synthetic native-error/length/termination controls test error
propagation and zero output; they are not additional native-call qualification.
Closing the exact handle in the same child must succeed before that host exits.

The ancestor guard retains the drive-root directory and every addressed
component, with a 128-component limit checked before opening anything. Its
controls cover file/directory leaves, ancestor rename exclusion, junction
refusal, reverse-order release and complete cleanup after an open or release
failure. Both guard forms reject non-root trailing separators before native
binding, including existing file/directory inputs; the drive root remains a
valid directory leaf. This is retained ancestor custody, not canonical-name,
short-name, drive-mapping or source admission.

The canonical-path candidate consumes one current mapping only when it names a
direct physical volume, then uses internally derived `GLOBALROOT` addresses
for every retained open, fixed-drive query and long-name check. It never uses
the mutable DOS address again. Existing handles reject reparse points and
wrong kinds; the actual long name must match every component, including
ancestors. Reserved device names and wildcards refuse before bindings.
The stock find record is copied into bounded owned memory because Core's name
getter returns a non-boxable span. Its `SafeFindHandle` uses `FindClose`; both
search and memory resources close before any name is returned. Tests distinguish
actual physical opens and short-name refusals from synthetic mapping changes,
malformed records and release errors. The pass-through memory observer tracks
live allocations, not all previously freed addresses; a deterministic recycled
address control retains double-free and unknown-address refusal. An actual short-name control skips
explicitly when the fixture volume has no short names; that is not qualification
of short-name refusal on that host. Stock normalization can expand an existing
alias and reject the original input before opening anything. Separate controls
retain that full-path refusal and isolate the post-open long-name defense with
an unexpanded-input validation control; the actual alias and physical lookups
remain native. That injected normalization case is not an actual DOS-remap race.
This unused helper does not establish
interpreter startup, source admission or a complete bootstrap handoff.

The current-drive query calls existing stock declarations without defining or
removing a DOS mapping. Windows PowerShell uses the existing StringBuilder
declaration, whose marshaler exposes the current first string but not the
native MULTI_SZ tail. Core passes owned UTF-16 buffers through the existing
raw-pointer declaration using reflection's IntPtr conversion, without emitting
an interop type or delegate. Its raw thunk does not capture native last error;
zero results must report that diagnostic limit, not a stale error code.
Controls check the fixed 32768-code-unit bound, actual pointer/argument
forwarding, current-versus-prior mapping semantics and release before any
consumer-visible result. Synthetic failures cover malformed output, partial
allocation, invocation and post-free errors. Malformed drive inputs, including
trailing newline, NUL and Unicode line-separator data, must fail before binding
and emit no result. A current mapping is a snapshot,
not proof that a drive is canonical or unchanged.

```text
python tools/run-plugin-tests.py agent-codespaces -k bootstrap_file_trust --timeout 600 --plugin-timeout 600
```

These controls do not establish the full prerequisite-to-helper admission,
canonical path/drive/source closure, complete PowerShell Core prerequisite
support, production headlessness or any compiled-caller replacement.

## Materialized source-owner SDK readiness

`plugins/agent-worktrees/tests/test_source_portfolio_owners.py` materializes the
current required-context Python runtime roster and invokes each installed owner's
actual source validator against a separate prepared artifact. It checks canonical
lifecycle SDK bytes and unchanged fixture selector, config and SQLite file
identity. The fixture snapshot uses the canonical materializer's ignore policy
and omits bytecode-only directories, retaining working-tree additions without
requiring a platform-native Git worktree. Run this integration lane through the
bounded test runner with short Windows and Linux-native temporary paths.

This is SDK-readiness evidence with synthetic source receipts, not Git admission,
native construction, full collected-portfolio execution, production data-format
compatibility or serving qualification. The legacy version-1 command manifest is
not converted. Generator controls retain its required-context/vendored-primitive
guard and unchanged command/catalog rendering. No required unit-only CI packet
is expanded.

## Optional non-serving collected-source updater witnesses

Set `COPILOT_TEST_NATIVE_SOURCE_SIMPLE=1` and select
`plugins/agent-worktrees/tests/test_source_simple_native.py` through the bounded
test runner. SSH, Machines and MCP exercise real materialized native provisioning,
the collected source updater and canonical installed-owner lifecycle admission.
The witnesses check installed module/interpreter/distribution identity, original command
binding, default fixture config/SQLite identity, exact selected-source
currentness, completion-preserving reuse and global help after prepared inputs
become unavailable. Machines and MCP retain their CMD-only Windows global launchers.
MCP also checks its source-qualified operational module version separately from
the installed distribution's package version. Its fixture explicitly sets
`AGENT_MCP_NO_CUTOVER=1` and `AGENT_MCP_NO_VERSION_REAP=1`, checking that the
collected step retains both opt-outs. This qualifies construction/publication,
not live daemon replacement, stale-process cleanup or serving readiness.
The original owner is built with a higher package label than the selected source
to distinguish publisher delivery, actual installed version and exact source
selection without imposing version ordering. The helper code remains current;
this is not a claim that arbitrary older helper ABIs are supported.

Use short Windows and private Linux-native temporary paths. The Windows witness
also compares persistent User PATH fingerprints; contained installers may update
only their child process environment. Source receipts and account homes remain
synthetic fixtures, not Git admission, production data-format/serving proof,
service-role adoption or a full eligible-portfolio rollout. No required unit-only
CI packet is expanded, and none of these witnesses starts a resident service.
Companion controls in `tools/test_native_source_manifest_publication.py` exercise
the actual native publication blocks with a refusing SDK child, retaining prior
manifest bytes and the original-owner argv without a success-shaped result.

## Optional native source-profile updater witness

Set `COPILOT_TEST_NATIVE_SOURCE_PROFILE=1` and select
`plugins/agent-worktrees/tests/test_source_profile_native.py` through the bounded
test runner. The two native-shell cases exercise base and provider-exec source
construction through the actual collected updater and canonical lifecycle
helper, selected-profile currentness, installed code/global CLI help, retained
config and SQLite file identity, and unchanged completion reuse in disposable
homes. Native source calls do not pass a profile override. The fixture reuses
the existing copied-account adapter on POSIX; production OS-profile resolution
is unchanged. Its synthetic source receipt is not merged-source provenance.

The 270-second item and finite child/install bounds remain inside the existing
bounded runner. Short fixture names avoid Windows native-extension DLL path
limits; no production root or system setting changes. This is opt-in, not a
new required CI matrix, fleet/role enablement, full portfolio rollout, production
data-format compatibility or performance qualification. Companion unit controls
are Containers' `test_runtime_source_target.py`, Worktrees'
`test_update_runtime_target.py` and Manager's `test_runtime_source_update.py`.

> This is the canonical testing guide. [`AGENTS.md`](AGENTS.md) links here and
> keeps only a short inline summary; the per-plugin release/versioning rules live
> in [`docs/pipelines.md`](docs/pipelines.md).

## Optional promotion-control and original-source witness

The manual `installation-context-full.yml` portfolio `promotion-controls` runs
the actual-Git retirement cases, their workflow guard and two full-source
reconstruction cases on an ephemeral Ubuntu runner, with a 100-second packet
deadline and a five-minute setup/job envelope. It records the exact checked-out
commit and parents, uses read-only permissions and no persisted credentials,
and does not expand required PR CI or the promotion unit portfolio.
The cases prove repeated deletion/rename preservation through real composition,
ordinary declassification, pinned-Main selection and artifact recomposition.
Recorded ownership cannot be removed to reactivate a deleted Dev workflow.
The source cases retain every raw Git blob and mode despite export attributes;
the original-build case invokes the actual helper CLI without a qualification
receipt, while the qualified-source case preserves its existing contract.
It is not a live controller, artifact-reuse or late-merge race qualification.

```bash
gh workflow run installation-context-full.yml --ref <published-branch> \
  -f portfolio=promotion-controls
```

## Optional repository PR sweep qualification

The manual `installation-context-full.yml` portfolio `pr-sweep` runs the
focused provider, selection, original-root and native-process packet on Ubuntu
with Python3.12. It adds no schedule or required PR matrix. Its ten-minute job
and existing60-second item deadline contain only synthetic state and processes;
no live forge, remote host, credentials, installation or rollout are needed.
The recorded checkout SHA identifies the candidate actually exercised.

```bash
gh workflow run installation-context-full.yml --ref <published-candidate-branch> -f portfolio=pr-sweep
```

The same packet runs locally through `tools/run-plugin-tests.py agent-worktrees`
with the six explicit test files named in that job. Native Windows and POSIX
shell cases skip on the other OS; inspect actual outcomes instead of crediting
skips as qualification. It exercises the real CLI in a synthetic registered
context, known read-only claims adapter, exact quoted catalog paths,
unsupported-old-client refusal and owned descendant timeout.
The same manual job separately runs the publication blob/identifier guards
through bounded pytest tooling, including native POSIX quoted-command
and finite-reader timeout proof. Windows additionally qualifies pipe-holding
descendant retirement and command-binstub quoting. These remain optional
native checks, not an expansion of required CI.
Live read/coverage evidence remains separate and never requires lifting a
held route or installing the candidate into another owner's runtime.

## Optional Dispatch scoped-stamp binding

The manual `installation-context-full.yml` portfolio `dispatch-binding` runs
`test_dispatch_scoped_publication.py` through the isolated `payload-invocation`
runner on Ubuntu and Windows, with the existing 60-second test deadline.
It does not expand required PR CI.

The packet runs actual custom-root and active-cell stamps with and without
pre-existing legacy primary/board launchers. Stamps must preserve every global
launcher byte-for-byte or leave them absent while retaining selected-root helpers
and healthy payload invocation. Ordinary-shell calls through existing globals
must still reach the healthy legacy root or the active cell selected by the
attributable publisher, never acquire an unselected custom root. Whole-home
snapshots constrain healthy retained-command writes to their existing boot trace.
Equivalent trailing-separator and dot-segment legacy roots must still publish
the global commands. Distinct case-sensitive POSIX roots must preserve them;
both Bash and PowerShell exercise that distinction.
These controls do not qualify native scoped staging, service registration,
runtime construction, host rollout or performance; cold scoped provisioning
retains its explicit unsupported-readiness outcome.

## Optional native pane argument delivery

The separate Worktrees opt-in
`--test-file tests/native/test_generic_mux_startup.py --allow-explicit-tiers`
packet, with `PSMUX_FILE_LAUNCH_E2E=1`, exercises the generic creation owner
against `PSMUX_TEST_BIN` or the explicitly opted-in installed PSMux.
Fresh private profiles cover clean and inherited
nesting environments, with and without pane-print formatting. The actual
file-based consumer must run, consume its manifest and retain its literal argv;
the caller environment must remain unchanged, with no owned visible windows
or foreground focus. A positive control pauses sampling until a creation
client exits, then requires its detached child to be observed from retained
launch roots. Payloads wait for fixture release within a finite lifetime; the
45-second readiness allowance matches the existing native pane packet.
The fixture removes the suite-wide headless initializer so only production
and explicit fixture launch flags govern the observed native processes.
The manual `installation-context-full.yml` portfolio `generic-mux` selects
only this Windows packet and its verification/classification controls, using
a digest-pinned disposable PSMux 3.3.8. The 3.3.5 ambient-tool baseline remains
partial after intermittent creation failures; this does not change production
compatibility admission or the installed mux. These contained source controls
do not certify a real
Copilot prompt, productive runtime deployment or escape from caller cleanup.

The manual `installation-context-full.yml` portfolio `pane-argv` runs the real
PSMux direct-child control and both file-based pane producers twice on a fresh
`windows-latest` runner. It downloads PSMux 3.3.6 into disposable job storage
with a pinned archive digest, records the actual checked-out source and Python,
and retains kernel containment and the existing client/query/teardown bounds.
The opt-in two-cycle cases have a 180-second item budget and 45-second
PowerShell setup/payload-readiness limits. Measured native readiness exceeded
the earlier eight-second fixture wait; these functional-integration limits do
not change ordinary unit budgets, production deadlines or performance gates.

Dispatch against the published candidate branch with `-f portfolio=pane-argv`.
This lane qualifies only native argument delivery and owned teardown. It does
not prove interactive Copilot, selected-host desktop visibility, real
uninstall/reinstall survival, handler adoption or rollout. It does not expand
required PR CI or the default scheduled portfolio.
The pinned test tool does not adopt a new production compatibility floor or
upgrade the operator's installed PSMux. The older 3.3.5 baseline also exhibited
intermittent immediate exits and is not qualified by this lane.

## Optional Bridge scoped-stamp binding

The manual `installation-context-full.yml` portfolio `bridge-binding` runs
`test_bridge_cell_stamp_preserves_global_ownership` through the isolated
`payload-invocation` runner on Ubuntu and Windows. It retains the 60-second
test deadline and does not expand required PR CI.

The packet stamps an actual selected cell with and without existing legacy
global commands. Scoped stamping must preserve their exact bytes or leave them
absent. A healthy cell runtime proves payload dispatch before ordinary-shell
controls remove the incoming context. Existing globals may reach the active
cell through its attributable publisher; publisher loss, pointer loss and
activation-CAS deactivation must never turn that cell into a legacy
resolver-only fallback. Synthetic distinct cell and legacy modules identify
the runtime actually executed; no native runtime construction, service
installation, live host adoption or performance-budget claim follows.

## Optional POSIX selected-runtime bootstrap controls

Run `python tools/run-plugin-tests.py runtime-source --test-file
tests/test_bootstrap_selected_readiness.py` with the normal containment and
deadlines. The actual three adopting hooks and vendored shell resolver execute
against fixture-owned runtimes and harmless installer witnesses. Controls cover
completed marker-only/source/repair selections, legacy links, invalid selections
and completion, fallback, changed identities/bytes, first-touch and publication
refusal, exact structured output and read-only trace behavior. A before/after
control records actual installer child counts and hook elapsed time; it does not
assert an invented speed threshold or substitute for installed startup evidence.
Windows skips native POSIX controls explicitly without invoking WSL. Deterministic
host controls still execute the hook's real identity probe and the family sync
contract; they are not shell qualification. Use the isolated Linux integration
fixture for native evidence and full-plugin first-turn validation separately.
No required unit-only CI packet is expanded by this on-demand integration file.

## Chat container image qualification

`chat-container-qualification.yml` runs a disposable Linux Docker proof for
Chat-path pull requests, or manually with an immutable `candidate_ref` and an
explicit digest-pinned `python_base`. The checked-in default is a seasoned
qualification fixture, not an admitted release base. The source SHA and actual
image/base/platform are recorded; no image is pushed or deployment selected.

After the existing standalone runner prepares its declared tooling, the lane
runs `tools/qualify_chat_container.py --python-base <digest-pinned-base>` from
that prepared interpreter. It constructs a dedicated offline wheel/lock context,
starts non-root with read-only application content and explicit fixture limits,
then exercises retained-only startup refusal, explicit storage creation,
authenticated health, independent participants, competing-writer refusal,
SIGTERM and retained restart/deduplication. Resources have unique fixture names
and are removed on exit.

Native Python tests and `tools/test_chat_container_image.py` cover host and
recipe contracts but do not substitute for this image proof. The lane does not
qualify Gateway connectivity, Komodo application, cross-host storage fencing,
production capacity or release admission. See
[`services/chat/container/README.md`](services/chat/container/README.md).

## Optional historical Bridge source proof

The manual `installation-context-full.yml` portfolio `bridge-contracts`
runs only the canonical Bridge evidence checker, HTTP-48 archive byte controls,
HTTP-48/49 publication guards and HTTP-47 history-preservation cases on Ubuntu.
Shared immutable-input preparation has its own bounded qualification control;
each publication case still creates a fresh consumer fixture.
It retains the ordinary runner's containment and test deadlines. This is
source/publication evidence, not SDK, native Windows or installed qualification.
Historical seed preparation indexes each immutable packet's source identity
once, instead of rereading every packet for every source. Named progress and
slow-case timings identify unfinished work; JUnit outcomes are retained on
failure without changing the 30/300/900-second item/group/plugin limits or test
selection. The controlled HTTP-52 capture uses the same guard step.
Historical guards precede canonical validation and export, so stale current
metadata does not suppress their diagnostic execution and no qualified
current artifact is exported before their success.
The runner partitions the HTTP-48/49/50/51 publication module into sequential,
disjoint generation cohorts, with untagged and future controls in the catch-all
cohort. Other selected modules remain present. Every cohort retains the
300-second group bound and shares the original 900-second plugin deadline;
no parallel workers, larger resource limits or omitted cases are introduced.
`tools/plugin_test_selection.py` owns that cohort policy; the runner retains
containment, execution, cleanup and aggregate deadline enforcement.
Each admitted capture interpreter binds its source-name suffix index once;
lookup specificity and refusal boundaries remain exact. Per-execution source
hash/code/origin checks, role/launch verification and durable receipts are not
cached or relaxed. Three pure locator controls supplement the original
historical packet without widening its or the current capture's deadlines.
Use a fresh canonical-history-only clone to check proof retention across a
squash; a long-lived clone may hide missing objects. Windows archive identity
controls are independent of the heavier Git-backed publication packet.

## Optional native worktree-state durability

The manual `installation-context-full.yml` portfolio `worktree-state` selects
the native Windows allocation/exact-retry control and the final project-root
barrier-failure control from Worktrees' `test_worktree_data_store.py`, followed
by Efforts' `test_agenda_export.py` native complete-export/readback control.
The source-stamped job uses only private temporary homes and real filesystem
barriers. Allocation retries retain the exact descriptor; an injected failed
acknowledgment refuses until the actual final barrier succeeds. Export preserves
all declared table rows, original accepted requests, immutable records and
delivery status across three revisions and a separately copied readback.
An actual Windows pass is required: Linux skips and API doubles do not qualify
this boundary. It does not prove hardware power-loss recovery, unsupported
filesystems, live relocation, installed collection or remote archive durability.
This explicit five-minute job does not expand required unit-only CI.

## Optional native Mux Companion navigation

The manual `installation-context-full.yml` portfolio `mux-companion` runs
`tests/native/test_companion_pane_tmux.py` on Ubuntu 24.04 with tmux 3.4 and
`tests/native/test_companion_pane_native.py` on Windows with digest-pinned
disposable PSMux 3.4.0 against the dispatch's recorded immutable source.
It verifies actual native option targeting, a fresh
synthetic constructed-prefix invocation, same-window reuse and the unchanged
CLI process in an isolated socket. It is not a required PR lane or a production
runtime rollout. Both use the explicit plugin runner with
`--allow-explicit-tiers --test-file <path>`. For local Windows qualification,
`PSMUX_TEST_BIN` selects a disposable binary without changing the installation;
without it the fixture uses the existing PSMux on PATH.
The same optional portfolio also runs the bounded root-attention provider and
actual fresh-home CLI subprocess contract on Linux. This does not qualify a
live pending question, human input, native status chrome or a deployed runtime.
The optional attention client and real Textual packet additionally exercise
provider rollback, head changes, hidden/unknown visibility, failure backoff,
single-flight, stale-result rejection and actual process exit while an
attention child stays blocked. Native pane fixtures verify the observer's
own pane environment and that detached sessions are not considered visible;
these are not proof of rendered or real-client input/click acceptance.
The Windows packet separately runs `test_companion_app_psmux.py`: the actual
PSMux client starts inside the existing strict caller-owned ConPTY Job and
launches the unmodified Companion from a private constructed prefix. An inert
CLI and wire-valid synthetic owner isolate actual client keyboard dispatch,
older-page browsing, live/unread behavior, resize, Follow-live and Escape.
The fixture retains native process handles and CLI pane/PID/session birth,
requires reader/observer exit and closes its strict Job even on failure. Its
private profile and disabled warm server prevent discovery of existing sessions;
it never relaxes Job breakaway restrictions to make a run pass.
Linux skips do not qualify this Windows boundary. Source/commit-stamped Windows
execution is required; server captures and ConPTY bytes are not headed pixels,
window/focus-stealing observations, real owner composition or frame latency.
The same Windows packet separately selects `test_companion_joined_psmux.py`
over the actual private Worktrees resident, managed Agenda, Bridge semantic
route/EventLog and callback-derived current-input projection. It uses the
shared source selection/bootstrap and loopback carrier substitutions, not a
production service or SDK actor. Actual ConPTY keyboard input selects a known
session, browses independent history, retains sparse full/narrow views and
observes live purpose/count updates without leaving older work. Escape requires
all three birth-qualified reader processes and the observer to exit while the
original CLI pane/PID/session remains live and accepts further input. The strict
caller-owned Job and retained handles govern failure cleanup. Linux skips are
not Windows qualification; exact source-stamped native execution is required.
Its change-only geometry/focus snapshots are fixture observations, not headed
pixels, native-frame or input-ready performance measurements.
`test_companion_joined_fixture.py` deterministically checks that this observer
retries only Windows sharing/lock violations on a later tick without advancing
its published value; other permission failures remain visible errors.
Its private Bridge prerequisite also requires an actual owning membership-route
heartbeat after the snapshot, so a one-frame carrier that starves healthy
readers cannot masquerade as native integration failure.
Pre-app startup failures additionally record a bounded read-only thread-state
query for the already-pinned inert CLI. That diagnostic never resumes, stops or
repairs the process and does not change the original startup deadline.
The manual packet also selects `test_companion_python_startup.py`: three
stdlib-only controls compare the prepared virtualenv interpreter directly in
ConPTY, that interpreter inside private PSMux/ConPTY, and its base interpreter
inside private PSMux/ConPTY. Each requires a script receipt and banner within
the same 15-second deadline, retains process identities and strict Job/handle
cleanup, and uses the existing private profile and no-warm policy. These
controls isolate startup layers; they neither fix nor waive a client failure
and are not Companion, installed or startup-stability qualification.
Portable fixture controls execute the native polling code with timely,
exact-deadline and late observations; success is checked after blocking polls,
so a query started before the deadline cannot admit a late result.
The Companion portfolio now pins disposable PSMux 3.4.0; the separate pane-argv
portfolio retains 3.3.6. Under 3.3.6, the direct virtualenv/ConPTY startup
control passes while both PSMux controls (virtualenv and base Python) fail
before the script. Its upstream ConPTY implementation requests inherited cursor
state; 3.4.0 removes that startup handshake, with an upstream first-pane
regression control. This narrows the failure to the additional PSMux/ConPTY
layer, not a virtualenv-only failure or Companion behavior. The version change
does not erase the failed 3.3.6 runs or qualify 3.4.0 without actual execution.
All three startup controls pass on 3.4.0, exposing a separate target-parser
refusal: `session:%id` is treated as a window by its client validator.
Companion and the three native wrappers select exact, explicitly session-scoped
pane IDs using the selected executable's `-V` release: `session:%id` for 3.3.x
and `session:.%id` for 3.4.x. The latter is not sent to 3.3.x, whose parser
silently drops that pane ID. Other or unrecognized release lines refuse pane
actions before mutation rather than guessing or replaying effects. The bounded,
headless discovery also reads the selected live server's `#{version}`, session
name and incarnation. A 3.4 client with a retained 3.3 server refuses before
pane effects; a 3.3 client retains its old syntax against either server.
Discovery is cached per executable filesystem identity and verified session
incarnation, not per event; replacement or a new incarnation invalidates it.
Bare registered executable names resolve through PATH before discovery and
execution. POSIX tmux targeting remains unchanged. Portable controls cover the
compatible pairs, mixed-version refusal, replacement, bare names, stale native
identity and the unchanged operation deadline; they are not native integration
qualification.
The next reviewed Windows run passes all startup controls, the synthetic-owner
client and pane-lifetime control, and reaches the actual joined client's
post-Escape exit wait. That wait remains a failure: its original diagnostic
queried the already-departed observer pane and obscured which cleanup condition
was unmet. A phase-specific diagnostic now reports retained observer/reader
handles, live birth-qualified owner commands and Bridge subscription counts
without querying a dead pane. The unchanged cleanup assertions and 15-second
deadline remain the acceptance gate.
The diagnostic run confirms the observer and all three retained reader handles
are signaled and Bridge has no subscribers or held connections, while the
PID-existence-only owner check still sees the two exited source commands.
Windows retains process objects while another process holds their handles.
The native exit check now uses a matching retained handle's signaled state;
unretained commands still require disappearance or a known changed birth,
and unknown birth stays unsettled. All source commands, readers and observer
must still exit before further input is sent to the original CLI. The same
run's separate 50 ms membership-seed setup failure is retained under #8811,
not waived or hidden by this exit-check correction.
The manual Windows Companion packet enables the fixture-only
`COMPANION_MEMBERSHIP_PROFILE=1` diagnostic. It profiles the existing initial
`source_for` call, reports total wall and current-thread CPU time plus the top
25 cumulative call costs with directory paths stripped, and preserves the
original result or exception. It does not retry, prewarm, replace an owner,
change the 50 ms/VM budgets or make a failed read pass. Other runs remain
unprofiled unless explicitly selected. Portable controls preserve one call
and exact result/exception identity in both modes. Profiled timings include
instrumentation and are diagnostic evidence, not native performance acceptance.
The same optional boundary brackets each original private budget's creation and
checks with `perf_counter`, reports both elapsed-clock implementations/resolutions,
and retains at most eight budgets with their last check, unchanged limits and VM
steps. `elapsed_min_ms`/`elapsed_max_ms` bracket the check's elapsed instant;
`monotonic_after_ms` uses its own monotonic creation sample, independent of the
clock chosen by the production deadline.
Instrumentation still counts toward the original deadline. A native Windows
control compares both clocks around sixteen bounded delayed checks, distinguishing
an observed early refusal from true elapsed exhaustion or a non-reproduction.
It also checks the actual production budget's creation against its high-resolution
elapsed brackets and rejects any refusal proven earlier than the unchanged limit.
This is a clock hypothesis control, not proof of a historical failure's cause,
a CPU-clock substitution, a retry, or a production budget change.
The fixture's state reader also opens its Windows handle with read/write/delete
sharing, matching atomic publication rather than temporarily denying its
publisher's DELETE access. A native control holds the publisher's DELETE-access
handle: the old ordinary reader refuses while the shared reader obtains complete
JSON. After the publisher closes, replacement and a complete new read succeed.
This does not claim that `MoveFileEx` can replace a still-open destination.
Only a missing publication is deferred. Access, sharing and malformed-JSON
errors remain failures; there is no permission-error retry or deadline reset.
The blocked attention-refresh controls wait for the actual successor observation
and single-flight completion, not `wait_for_complete()` over the intentionally
cancelled predecessor worker. They still release and observe the late old read
and assert it cannot replace the new head or its idle result. This avoids a
test-only `WorkerCancelled` race without changing production cancellation.
Actual keyboard selection waits for the fixture-observed menu expansion and
highlight before committing the session choice, rather than batching keys across
asynchronous widget transitions. Expansion, highlight and source admission share
the original single 15-second deadline. These observations do not set widget
state or replace real client input.
The older PSMux 3.3.5 baseline has exhibited immediate session exits and later
CLI/directory loss in this fixture. Those observations remain an unqualified
stability gap under #8811, not a reason to skip a failed test or claim that
3.3.6 qualification proves 3.3.5. The fixture does not retry past failures,
weaken native identity checks or modify any installed mux.

## Optional native PSMux launch-directory control

On Windows with PowerShell and PSMux already installed:

```text
python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_psmux_home_directory.py
```

The control uses fresh private profiles/registries and unique namespaces. It
compares legacy and home-scoped launches, verifies the actual pane directory
and caller restoration, and removes the retired checkout while its standby
still runs. On affected PSMux 3.3.5, the legacy case must reproduce the lock.
Both cycles observe owned windows/foreground activity and preserve pre-existing
mux process identities. Cleanup targets only the test's own namespace and
birth-checked processes; no default-namespace shutdown or installed update is
performed. This is an explicit native lane, not additional required PR CI.

## Optional immutable Worktrees runtime qualification

The manual `installation-context-acceptance.yml` selector
`immutable_runtime=true` runs `tests/test_python_runtime_dev_live.py` through
the canonical isolated `agent-worktrees` plugin runner, preceded by the focused
installation-context descriptor-metadata regression. That regression retains an
open file's identity across path replacement without trusting its fdescfs entry.
The legacy lifecycle fixture declares its disposable directory marketplace and
verifies that the original command binding survives removal of the source.
Its four native matrix
cells use two explicit jobs with literal Linux (`ubuntu-latest`) and macOS
(`macos-latest`) runner routes, each with a `legacy`/`namespaced` mode matrix
and fail-fast disabled. Python and `UV_PYTHON` are 3.11,
matching the supported first-adopter floor. No host-state opt-in, live
installation or service deployment is used.

Namespaced installation management requires Bash 4.4+. The macOS namespaced
cell selects Homebrew Bash and records its actual version;
it does not weaken the guard or skip the case. Legacy macOS keeps stock Bash.
The Worktrees installer carries its already-selected Bash into context queries,
staged children and helper publication instead of rediscovering a different
`bash` after a PATH change. Missing supported native tooling fails qualification.
Only that disposable optional macOS namespaced cell installs a missing Homebrew
Bash, without Homebrew auto-update or installation cleanup. Existing Bash is reused;
legacy macOS and all other cells never install it. Canonical test-dependency
seasoning and the Bash version guard remain unchanged. Copied POSIX
fixtures redirect the SDK's current `canonical_path_into` account-home seam;
they leave the source SDK and the runner's actual account profile unchanged.
The copied Python fixture follows the typed passwd accessors; its existing
POSIX/Windows regression checks quoted homes and unchanged original SDK bytes.
The PowerShell transport uses the already-redirected Python SDK resolution.

`tools/test_installer_dependency_policy.py` checks that copied configuration
loads its sibling argument grammar even with a foreign ambient module, and
refuses a missing sibling. The real application handoff and changed-wheel,
installed-content and origin controls in `tools/test_local_install_source.py`
exercise that companion loading through staged nested environments.

The same file's `application_adapter` refusal controls check the explicit prepared
downstream composition API. Four controls stop changed selection,
missing application, mismatched construction and Manager-native ownership
before construction. `tools/test_application_adapter_journey.py` retains the
contained synthetic application journey: real UV
construction, setup/start, unchanged repeat, changed-declaration refusal before
callbacks, a failed-start selected generation with unchanged last-known-good, distinct repair
with the new declaration and the published stable command.
The `application-adapter` contract in `tools/run-manager-runtime-acceptance.py`
explicitly selects the ordered source-input, application-preparation, selection, install, repeat, refusal,
failed-start, retry and physical-command items for both plugin and standalone
forms. Each form retains one disposable source/application/runtime across its
phases. Every item has 30 seconds, including its fixture work; one contained
300-second group includes all application preparation, both forms and cleanup.
Source freezing and application-profile construction remain separate bounded
items; both retain the complete original dependency graph and production checks.
The existing managed `agent-worktrees` test environment must already be prepared;
this contract never bootstraps tooling or prepares an application outside that group.
It is a changed per-phase allocation, **not** an original single-30-second pass.
The runner supplies exact node order and requires all 16 unique, successful
JUnit cases before accepting the journey. A later phase without its predecessors
refuses; an independently selected phase reports only partial coverage.
Function-scoped environment restoration and canonical group cleanup retain
custody on failure and timeout. Native child and routing deadlines are unchanged.
The source receipt and application are fixtures,
not authenticated release/deployment evidence. Native Windows qualification
does not qualify Linux, resident-service readiness or an adopter's host rollout;
these cases do not expand required unit-only CI.

```bash
gh workflow run installation-context-acceptance.yml --ref <published-workflow-branch> \
  -f immutable_runtime=true -f immutable_runtime_ref=<full-40-hex-candidate-commit>
```

Publish the candidate normally first; the dispatch branch must contain this
workflow definition. `immutable_runtime_ref` accepts only a full immutable
40-hex commit, or empty to select `github.sha`. Branch names, PR refs and short
SHAs are refused before checkout. Checkout does not persist credentials; each
job verifies and records the actual SHA, parents, interpreter and platform.
The workflow definition's dispatch ref is distinct from the source SHA tested.

Each matrix cell runs:

```bash
python tools/run-plugin-tests.py installation-context \
  --test-file tests/test_digest_cache_hardening.py -k darwin_metadata \
  --timeout 120 --plugin-timeout 240
python tools/run-plugin-tests.py agent-worktrees \
  --test-file tests/test_python_runtime_dev_live.py -k <legacy-or-namespaced> \
  --timeout 2500 --plugin-timeout 2700
```

The 50-minute job envelope covers the existing 2700-second plugin suite plus
setup. It does not raise the 2400-second test-item cap or the 480-second
production deadline. The plugin runner prepares its own isolated, seasoned
tooling. Selecting this lane suppresses the default diagnostics, while all
other explicit selectors keep their routes. It adds no schedule, required CI
gate or Windows rotation coverage.

Workflow contract tests and CI enablement are not native qualification. Inspect
all four cells' actual selected test outcomes and skips against the recorded
candidate SHA; only real Linux/macOS execution can establish native PASS.

## Optional native update diagnostic contracts

The manual `installation-context-acceptance.yml` selector
`update_diagnostics=true` runs the complete native auth, durable queue and fixed
launch-request contracts on isolated Linux and Windows runners. Each platform
also exercises its actual selected-phase shell front doors, including missing
engine refusal without repair downloads or implicit provisioning.
The same packet exercises manual versus explicit `--auto` requests through the
real Manager CLI/worker and cold-bootstrap entry, native launch producers,
automatic schedule definitions, per-repository forwarding and inner queued
reconciliation. Held automatic requests must start no installer; manual admission
must preserve ordinary construction arguments. Legacy record semantics,
independent-cell scope and distinct manual/automatic identities remain covered.
This does not migrate installed schedules, upgrade an already-running worker or
qualify a full runtime construction or machine rollout.
The packet also routes the real outer runtime planner/executor through Bridge's
native completed-slot installer functions and shared slot evidence, with
healthy-current, older-serving, refused-probe and failed-transition controls.
It verifies one invocation, no build/install fallback and unchanged completed
content. Windows exercises available PowerShell 5.1 and PowerShell 7 hosts;
POSIX exercises Bash. Side-effecting service seams are synthetic: this does not
qualify a live installed daemon or fleet preservation.

```bash
gh workflow run installation-context-acceptance.yml \
  --ref <published-candidate-branch> -f update_diagnostics=true
```

Both jobs check out and record the immutable dispatch SHA without persisting
credentials. The canonical plugin runner resolves its disposable temp parent
before creating the private sandbox, including Windows short-path aliases;
daemon-registry indirection checks remain enabled. Independent test families
still run after an earlier assertion failure when setup succeeded; any failure
keeps the job red. The existing five-second auth budget, twenty-second worker bound,
thirty-second test-item limit and runner aggregate limits remain unchanged.
The fifteen-minute job envelope includes governed tooling preparation, not a
production deadline exception. Selection suppresses default diagnostic-copy
jobs and leaves other explicit selectors unchanged. This opt-in packet adds no
schedule, required PR gate, host-state opt-in or deployment.

Inspect actual selected test results and platform skips against the recorded
SHA. Ordinary unit CI, a collected test or a skipped platform case is not native
qualification.

## Optional macOS guardian follow-through

The manual `installation-context-acceptance.yml` selector
`guardian_followthrough=true` runs a bounded packet on a fresh `macos-latest`
runner, outside required CI. It uses Python 3.11 (supported by the shared
library's Python >=3.10 floor and matching an originally affected interpreter)
and preserves existing test/runner timeouts. `guardian_ref` accepts only a full
40-hex commit; empty means the immutable dispatch SHA, never a moving branch.
Validation precedes checkout, credentials are not persisted, and the job records
the actual checked-out SHA, its parents, Python version and OS.

```bash
gh workflow run installation-context-acceptance.yml --ref dev \
  -f guardian_followthrough=true -f guardian_ref=<full-40-hex-commit>
```

For a pending change, first prepare an immutable merge-preview commit containing
the candidate and current development baseline, then pass that commit's SHA.
Do not pass a mutable PR merge ref or modify the contributor's branch merely to
run qualification. The workflow definition comes from the dispatch ref; the
recorded checkout SHA identifies the source actually tested.

The packet runs all shared-library `test_caller_owned.py` cases (including
Darwin census positive/negative controls when present in the selected source),
all Worktrees `test_installer_guardian.py` cases, the exact companion-lifecycle
guidance/concurrent-embody node, the explicit inline-allowlist guard, and the
Windows-command-shim simulation family. Library tests use the existing
process-containment harness; plugin tests use the existing isolated runner and
seasoned dependency preparation. No host-state opt-in, deployment, live profile
adoption or elevation is used. Selecting this packet suppresses the default
diagnostics; other explicit selectors retain their existing behavior.

Each family has its own logged step. After tooling succeeds, later families
still run following an earlier assertion failure, but that failure keeps the
job red. Setup failure or cancellation is not a pass. Inspect native skips and
individual outcomes, not just collection: Linux/Windows-only controls may
legitimately skip on macOS. Local workflow-schema tests, Windows/Linux results,
or adding this lane do not constitute native macOS PASS. A successful packet
qualifies only its recorded source/interpreter/OS and selected contracts, not a
full plugin corpus, performance benchmark, installation rollout or fleet.

Worktrees' `test_terminal_conclusion.py -k "lifecycle_lock or stale_pid_claim"`
also covers retained live-owner refusal and PID-reuse reclamation with both
integer and macOS `seconds:microseconds` birth identities. The native
companion-lifecycle node above exercises that same lock through concurrent
creation recovery and launch, rather than replacing it with a test double.

## Optional Bridge selected-source snapshots

The manual `installation-context-full.yml` portfolio `bridge-snapshots` qualifies
Bridge's source-qualified selected legacy snapshot for the fixed `provision`
recipe on native Windows and Ubuntu. It uses the actual clean-source
materializer and Bridge stamp, removes the installed publisher/source checkout,
then exercises Python-free admission, caller loss, ordinary queue results and
installed CLI/module/distribution consumers. Same-package source generations
remain distinct; failed candidates preserve the previous completed selection.
Separate bounded runner roots cover admission/freeze refusal matrices, native
construction, installed-source recipe regressions, shared Logger behavior and
canonical-installed-only bootstrap publication. A native launch acknowledgement
is not durable request acceptance.
The portfolio also runs canonical source-preparation regressions and native
daemon wait-status controls, preserving the same job and per-run resource bounds.

Linux fixtures explicitly construct the disposable-daemon native support with
its canonical build helper before cold admission. The synthetic source commit
includes that support before source materialization and identity publication;
no launcher compiles code or mutates an accepted source assembly.
The Python-free Bridge construction PATH retains the platform C compiler,
assembler and linker for the source package's explicit native build; it still
exposes no host Python or UV. This is not compiler-free host qualification.
Acknowledged owners and foundation/queue/kernel custody settle before fixture
cleanup, including failure paths. Registration/start effects alone use isolated
synthetic adapters; no host task/unit or live bridge is launched. This portfolio
does not adopt ordinary command publishers, unreceipted copies, explicit
installers, cells/custom roots or macOS. Exhaustive native qualification stays
manual, outside required PR CI.

## Optional MCP fixed recipes

The manual `installation-context-full.yml` portfolio `mcp-recipes` selects
the MCP prerequisite on native Windows and Ubuntu, separately from ordinary
publisher adoption. Three bounded packets exercise init-backed queue protocol,
Python admission refusals and native-shell refusals. Each keeps the
300-second group limit and explicit 60-second native item bound. Materialized
stamp and Windows missing-setup-Python refusal use a separate 180-second item
packet; cold construction/installed consumers use the existing 900-second item
and 1080-second group envelope. Process, memory and 2 GiB storage caps remain
unchanged; no matrix is added to required unit-only CI.
Three queue/native-process controls separately verify canonical UV handoff for
provision, acquisition failure before init and acquisition-free deferred stamp.
Only their acquisition helper and installer bodies are synthetic; the cold
materialized case retains actual acquisition and construction.
Two additional Windows materialized controls call the actual native installer
admission from a synthetic initializer while retaining runner containment.
They require the original authenticated queue owner at both default and
explicit execution roots, with no second request or fallback queue. A focused
shared-receiver packet retains direct/scoped participant checks and refuses
malformed, oversized, missing-root and foreign-root witness metadata.

The cold case retains the runner's containment, setup-provided Python
prerequisite, private foundation/queue and actual MCP initializer. It loses the
original caller process before a follower, then checks concurrent provision,
immutable reuse, a real incoming module-health failure, preserved configuration/
selection and installed command/module/distribution use after payload removal.
The preserved synthetic configuration lives in MCP's actual `bridges` directory
and is read through the installed configuration loader without starting its
declared server or consulting an authentication provider.
Persistent-environment effects use the installer's existing contained-test
adapter. No MCP server, backend, credentials or host service is exercised.
Caller-process exit is not cleanup-Job escape; the latter remains a separate
#10499 acceptance requirement, not an inference from this portfolio.

## Optional Bridge command adoption

The manual `installation-context-full.yml` portfolio `bridge-commands` qualifies
the actual materialized primary/payload commands on native Windows and Ubuntu.
Independent bounded packets cover Python-free original caller Job/group loss,
concurrent ordinary callers, failed candidates and selected-source execution
with the installed publisher unavailable. Windows also exercises the `.cmd`
front. A launch acknowledgement is not a committed ordinary receipt; the
original request must finish before any replacement submission.

The separate cold-wait transport case installs an explicitly synthetic
application protocol through the real queued Bridge recipe, without rewriting
a completed slot. It exercises held-open binary frames, quoted multiline raw
prompt input, nonzero exit/stderr and Windows PowerShell object input while
callers observe the same cold owner. Product CLI/module/distribution health is
qualified separately using unmodified Bridge sources. Service registration/start
effects alone use the existing isolated adapters, never host tasks or a live
bridge.

Four refusal packets retain complete materialization and source/runtime/queue
no-write comparisons. Publication controls are partitioned by scope/entry into
separate runner roots; warm native transport and canonical snapshot admission
remain separate controls. Existing 35-minute job, 900-second native test,
1080-second runner and 2 GiB temporary-storage bounds are unchanged; publication
cases retain their own 60-second bound. A skipped platform or explicit native
case is not acceptance. This portfolio does not qualify cold cells/custom roots,
unreceipted snapshot migration, explicit installers, macOS, serving or rollout.

## Optional Bridge bootstrap adoption

The manual `installation-context-full.yml` portfolio `bridge-bootstrap` qualifies
`test_first_use_bridge_bootstrap.py` on native Windows and Ubuntu. Separate
bounded runner roots cover materialized foundation/refusals, actual cold hooks,
and retained fixed-recipe regressions. The hook proof measures the real
15-second deadline and exact `{}` stdout with no caller Python/UV/Manager,
caller Job/group loss, concurrent sessions and ordinary durable results.
Drift selects `install`, not prepared Windows `provision`; native task effects
assert S4U/AtStartup while ordinary recipe tests retain Interactive/AtLogOn.
Linux effects inspect the actual unit and intercepted systemctl operations.
No fixture launches a real bridge or creates a host task/unit.

The portfolio also exercises source-aware selected/completed readiness through
the actual POSIX hook, read-only healthy controls, malformed/incomplete selection,
failed-candidate preservation, installed CLI/module/distribution consumers after
payload removal, historical reconcile-file preservation, and the independent
45-second guidance/catalog hook. Join acknowledged owners and settle foundation,
queue and kernel custody before fixture cleanup. Synthetic native effects are
not live serving, remote-session, deployment or rollout evidence. Exhaustive
native integration remains explicitly manual, not required PR CI.

## Optional MCP bootstrap adoption

The manual `installation-context-full.yml` portfolio `mcp-bootstrap` selects
the actual MCP session-start adapter on native Windows and Ubuntu.
Windows batching/admission controls use distinct `HOME` and `USERPROFILE`
values. Both SDK Copilot and durable roots must stay bound to the receiver-
validated Windows profile, not the unrelated POSIX-style home. The native
Windows caller-loss fixture retains the same distinct-home case.
`test_bootstrap_driver.py` exercises fixed action handoff, contract skew,
malformed metadata, acknowledgement validation, deadline and historical-record
preservation with a synthetic receiver. Source/repair readiness remains covered
by `test_bootstrap_stamp_selection.py` and the canonical selected-readiness
packet; a healthy fallback cannot substitute for the selected source.

`test_first_use_mcp_bootstrap.py` separately exercises the real packaged
receiver's refusal paths and complete canonical materialization. Its cold
functional packet measures the actual 15-second hook and exact `{}` output,
ordinary receipt/result, equivalent provision admissions, installed
command/module/configuration consumers, healthy repeat, incoming-health failure
preservation and source independence. Windows missing setup Python refuses
before supervisor launch. No MCP server, backend, credentials, host service or
existing queue is exercised.

The functional packet preserves the canonical runner's containment, lifecycle
lease and native owner settlement. It proves caller-process exit, **not**
production caller-Job/group destruction.
Equivalent requests whose durable acceptance/execution intervals overlap must
share one identity. Distinct identities are accepted only when durable timestamps
prove the first request completed before the next was accepted; sequential
non-overlapping calls are not credited as positive pending-coalescing evidence.

The same manual portfolio selects `tools/mcp_bootstrap_lifetime.py
--ephemeral-ci-consent` on disposable GitHub-hosted Windows/Linux runners,
outside the ordinary contained runner. The entry refuses inherited containment,
self-hosted execution and absent consent before creating a fixture; it never
strips a contained-test marker. Its fixed `test_first_use_mcp_lifetime.py` packet
reuses the real materialized hook and existing native lifetime owner. A held
ordinary execution lock keeps acknowledged supervisors pending while both tracked Windows
caller Jobs close, or an alive pidfd-pinned Linux caller group is killed.
Bounded controls must die and the same acknowledged owners must survive before
the lock releases, without another submission. Two stamp/provision cycles
retain the same acceptance-time coalescing checks, installed consumers, a renewed
instance lease, exact cleanup receipts and empty final inventory. Windows also
retains the existing restrictive shared-outer negative and qualified desktop
witness. Those generic owner controls are recorded separately from actual MCP
hook outcomes.
The cold hook establishes its own foundation and queue. A separate ordinary
request is added only in the later provision cycle, after that foundation is
ready; pre-creating a queue without a foundation would exercise the separate
existing-queue recovery boundary. Failed foundation state or an acknowledged
owner exiting without an ordinary receipt fails promptly. Bounded owner-log
tails are retained before completion waits and at teardown; Linux's signal
timeout permits normal finally/cleanup diagnostics within the unchanged item
deadline.

When the Linux host has a systemd caller unit, the native fixture retains the
real `systemd-run` prerequisite and the host's configured runtime/bus inputs
without exposing host Python as a product prerequisite. It does not install a
user manager or substitute inherited lifetime when the native scope is
unavailable. The normal cold contained fixture remains unchanged.

Native qualification is credited only from the exact candidate's successful
platform results, not from local refusal tests or this lane's presence. The
900-second item, 1080-second step and 35-minute portfolio bounds remain intact.
Only receipts and diagnostic logs are uploaded, not the constructed runtime
tree. Neither packet expands required unit-only PR CI or authorizes rollout.

## Optional Logger bootstrap adoption

Logger's corresponding manual `installation-context-full.yml` portfolio
`logger-bootstrap` runs `test_first_use_logger_bootstrap.py` and the retained
`test_first_use_logger.py` recipes on native Windows and Ubuntu. It measures the
actual 15-second hook and exact `{}` stdout, cold and drift callers,
caller loss, concurrent submissions, ordinary durable receipts/results, complete
packaged foundation and native raw-checkout snapshot mappings. Drift must run
`install` (including isolated task/timer reconciliation), not service-less
`provision`. Healthy no-op, failed-candidate preservation and six installed help
consumers remain covered. The 10-second cold-store provider and 45-second
guidance hooks run independently after bootstrap refusal, preserving exact
payload binstub identity without configuring a host bridge or processing
personal sessions. This is an explicit manual integration lane, not required PR
CI, live timer/serving-worker proof, selected-snapshot recipe admission or rollout.

The cold consumer fixture keeps the canonical runner's containment and daemon
lifetime context. On Windows, both actual Logger consumers supply the documented
`python` prerequisite for caller-owned admission; this is not Python-absent
machine qualification. On Linux, only the nonproduction lifecycle supervisor
receives a private, test-owned copy of the runner interpreter; product `PATH`
still contains no Python.
The installed foundation's SDK home must be inside the private fixture, proving
it did not borrow that lifecycle interpreter. These are bounded consumer
controls, not proof of escape from a caller cleanup Job.

Windows runs recipe refusals, actual bootstrap hooks and the concurrent cold
consumer in three separate contained packets. A filtered invocation combines
its selected files, so each packet names only its own file. All three keep the
canonical storage cap and retain every selected test.
The Windows cold-hook packet streams bounded failure diagnostics before the
observer waits for native owner settlement; that wait and its custody are unchanged.
Fixture staging moves the already-materialized private payload into its installed
location rather than copying the complete graph again.
Bounded phase markers distinguish fixture readiness, owner acknowledgement,
foundation observation and ordinary-result waits; diagnostic paths are fixture-relative.
Terminal foundation failures, interrupted requests and owner exit without a
receipt fail the observer immediately rather than consuming its remaining wait.
After observing owner exit, re-read its receipt before declaring it missing.
The contained Windows Logger hook fixture proves original caller-process exit,
not cleanup-Job escape: it retains its strict test-owned Job until native owner
settlement and closes it in final cleanup. Separate native controls retain
strict-Job production refusal and contained-child exit on Job closure. Actual
production Job-survival acceptance remains a separate unresolved requirement.

## Optional CodeSpaces bootstrap adoption

Independent Windows evaluator admission is a separate native packet:
`plugins/agent-codespaces/tests/test_bootstrap_python.py` covers the selector,
while `libs/payload-invocation/tests/test_evaluator_bootstrap_consumers.py`
covers both adopters' dispatcher routing, cleanup, complete SDK parity and
terminal refusals.
Use the existing bounded process-containment harness. Set
`EVALUATOR_BOOTSTRAP_TEST_PYTHON` to an explicit existing signed console CPython
base for actual native admission; a supplied but unfit fixture fails rather
than skipping. The consumer's real-admission case skips without this explicit
fixture. ABI stubs and platform/fixture skips are not native trust qualification.
These subprocess/registry/signature cases are not required pure-unit CI.

The manual `installation-context-full.yml` portfolio `codespaces-bootstrap`
qualifies the actual materialized legacy hook on fresh Windows and Linux
runners. It enforces the existing 15-second deadline and `{}` response,
Python-free cold stamp, overlapping cold supervisors, caller Job/group loss,
ordinary receipt/result, own-installed-payload drift, failed-candidate selection
preservation, healthy no-op and source-independent installed CLI/module/
distribution identity. The foundation must come from canonical payload
materialization, not a fixture's separately supplied source tree.
Windows additionally stamps the actual unmaterialized checkout shape, removes
that source, validates the retained complete local dependency graph and provisions
the real installed CLI from its snapshot. This protects the added driver
dependency; it does not replace #6909's separate copy/runner/native matrix.
POSIX legacy stamping retains its original payload pointer, so that Windows-only
snapshot regression is not relabelled as a POSIX contract.

No-write controls cover publication policy, no-self/pinned/nested invocation,
unsupported source/context and missing/skewed support. Provider/guidance hooks
are exercised independently after bootstrap refusal. Isolated adopted
disabled-Owner configuration and native enabled/disabled/unknown registration
adapters do not qualify live host registration, serving behavior or a CodeSpace
connection. Selected snapshots, command publishers, explicit installers, cells,
custom roots and #6909 acceptance remain separate.

The non-download subset uses `tools/run-plugin-tests.py update-driver
--test-file tests/test_first_use_codespaces_bootstrap.py`; the actual cold
construction test requires the existing platform-specific genesis opt-in.
The plugin smoke packet selects `test_bootstrap_driver.py`,
`test_bootstrap_check_reconcile_always_on.py`, `test_bridge_provider_manifest.py`
and `test_write_session_guidance.py` with the same runner. Heavy native matrices
remain outside required PR CI.

The on-demand Codespaces generation-repair packet uses
`--test-file tests/test_install_ps1_recovery.py --allow-explicit-tiers`.
Select `normal_entry_repairs` and `test_source_entry_` in separate bounded
invocations. The latter shares one disposable source installation across
initial-repair and repeat-reuse assertions; it does not raise the existing
120-second child, 180-second item or 300-second group limits. Both invoke the
real native install/update entry with private fake UV and real venvs, then
check the selected module and generated command, original slot/configuration,
completion, deployment identity and no repeated construction. Separate
`failed_repair_preserves` and `malformed_completion_state` selections cover
incoming failure and malformed incumbent evidence. Actor-free selection and
exact-source-receipt controls live in `tests/test_runtime_generation.py`.
These Windows source controls do not qualify live service registration,
CodeSpace connectivity, POSIX execution or installed rollout, and do not enter
the required unit-only CI packet.

The same packet's `install_stage_failures` selection checks actual nonzero
`install` and `provision` process exits for venv/package failure, with no
current selection or deployment manifest. The `install-incomplete-True`
normal-entry case covers `-Force` repair and healthy repeat reuse.
`marketplace_staged_uninstall_removes` constructs a real temporary venv through
the marketplace payload, invokes its generated command, then checks runtime,
selector and binstub removal while configuration, unknown state and the
original marketplace entry/manifest remain unchanged. Uninstall receives a
real external base interpreter, not only construction's fixture CMD wrapper.
UV package delivery and host-service
effects remain fixture boundaries; this is not full package or serving proof.

The on-demand Worktrees `tests/test_update_codespaces_recovery.py` packet joins
the normal collected update orchestration, real currentness probe, bounded
installer process/receipt owner and guarded Codespaces producer. Run
`python tools/run-plugin-tests.py agent-worktrees --test-file
tests/test_update_codespaces_recovery.py --allow-explicit-tiers` on Windows.
A matching manifest with an incomplete selected slot must produce a usable
repair and generated command; repeat must skip the now-current runtime without
dependency writes. Failed incoming preparation must preserve the prior slot,
configuration and selectors. Machine participation, acquisition, the unrelated
engine and final host integration are synthetic boundaries. The test reuses
the Codespaces private-profile/fake-UV fixture and real disposable venvs, not a
mock successful installer. It does not qualify repository-shell fresh bootstrap,
real feeds, services, other portfolio members or installed rollout, and does
not add native cases or increase budgets in required unit-only CI.

`tests/test_installer_generation_shell.py` executes the real POSIX selection
and activation functions under Bash nounset, checking absent/present source
receipts, argument boundaries and GC with no keep arguments. It uses Git Bash
on Windows or the available POSIX Bash; `CODESPACES_TEST_BASH` pins an explicit
interpreter for stock macOS Bash 3.2 qualification. These are bounded shell
controls, not package construction or native macOS runtime qualification when
executed with another Bash version.

The shared predecessor-retention prerequisite uses the `runtime-source` target
with `--test-file tests/test_runtime_references.py`. Strict ordinary retention,
missing/regular-file predecessor pinning, cleanup exclusion and completed
candidate rejection remain separate assertions. The existing Bridge
`test_runtime_retention.py` and `test_failed_construction.py` packets cover
native competing holders, handoff, process death and failed-candidate custody.
Codespaces' `normal_entry_repairs and file` selection exercises normal update
and repeat from an actual regular file at the selected slot name. These
on-demand packets retain their existing bounds and do not widen required CI.

## Optional SSH installation recipes

The manual `installation-context-full.yml` portfolio `ssh-recipes` runs only
the fixed SSH stamp/provision prerequisite on fresh Windows and Linux runners.
It uses the existing isolated runner, canonical materializer, native foundation
and ordinary queue. The packet covers no-write admission refusals, actual
service-less installation from its exact selected legacy snapshot with the
installed payload unavailable, failed-snapshot candidate preservation, source-independent
installed CLI/module origin and caller Job/group loss during Python-free cold
acquisition. Bootstrap controls additionally refuse snapshot/copy publication.
It does not adopt ordinary command publishers, exercise remote SSH, qualify
network-backed retained-source #5379 or cells, or enroll the operator host.
Run the non-download subset locally with `tools/run-plugin-tests.py update-driver
--test-file tests/test_first_use_ssh.py`; the native cold cases require their
existing explicit Windows/Linux genesis opt-ins.

## Optional SSH bootstrap adoption

The separate manual `installation-context-full.yml` portfolio `ssh-bootstrap`
qualifies the actual materialized legacy hook on native Windows and Linux:
its unchanged 15-second deadline, exact `{}` response, Python-free first stamp,
caller Job/group loss, ordinary receipt/result, manifest-selected drift and
healthy reentry. No-write publication/source/nested/skew refusals and independent
hook/deadline controls run alongside it. Initial runtime preparation uses the
already-qualified fixed recipe, not an unadopted SSH command. This lane does
also exercise checkout stamping and dependency-policy validation after removing
the checkout, to retain the new dependency closure without a network install.
That packaging check does not qualify the separate retained-source timeout.
The lane does
not qualify retained-source #5379, command publishers, cells, explicit installers
or host rollout. The non-download subset is
`tools/run-plugin-tests.py update-driver --test-file tests/test_first_use_ssh_bootstrap.py`.

## Optional SSH command adoption

The manual `installation-context-full.yml` portfolio `ssh-commands` separately
qualifies actual materialized legacy primary/payload commands on fresh Windows
and Linux runners. It exercises Python-free first use, caller Job/group loss
across the whole stamp/snapshot/provision request, concurrent callers, retained
source with the installed payload unavailable, healthy argument/exit forwarding
and no-write cold refusals. Healthy cell dispatch remains covered independently.
This does not exercise remote SSH, explicit installers, custom-root/cell cold
publication or the network-backed #5379 acceptance. The non-download subset is
`tools/run-plugin-tests.py update-driver --test-file tests/test_first_use_ssh_command.py`.

## Optional SSH session snapshot

The manual `installation-context-acceptance.yml` selector `ssh_snapshot=true`
runs snapshot, session writer, mesh and fragment-registry tests on Linux and
Windows. The native writer control uses a ready test interpreter and the actual
CLI on fresh/resumed session files; it does not qualify installed-runtime
readiness. It verifies replacement, the 4 KiB bound, preserved catalog, and no
profile/runtime creation. Dispatch's existing real-venv peer fixture also
exercises SSH-to-Worktrees same-cell selection and foreign receipt refusal.
These process boundaries stay outside required unit-only CI.
The Windows job explicitly enables `AGENT_SSH_WINDOWS_VISIBILITY=1`: a native
`pythonw.exe` parent drives the actual writer/contributor/CLI chain through two
cycles and forced timeout while the shared Win32 observer checks visible
windows and foreground transitions. The flag is otherwise off. Resolver
timeout coverage also checks that its nested runtime exits independently of
the writer's outer timeout.

For a focused local run, select `tests/test_session_snapshot.py` and
`tests/test_write_session_guidance.py` with `tools/run-plugin-tests.py agent-ssh`.
The separate real peer control uses `tools/run-plugin-tests.py agent-dispatch
--test-file tests/test_procutil.py -k "ssh_snapshot or (namespaced_sibling_resolution
and agent-ssh)"`. No production credentials, remote hosts or capacity are needed.

## Optional foreground completion custody

The manual `installation-context-acceptance.yml` selector
`interactive_completion=true` runs `libs/agent-procutil/tests/test_interactive.py`
and `test_interactive_session_native.py` on Linux, macOS and Windows. It
exercises real finite commands, normal-exit
descendant cleanup, abrupt caller death, POSIX interruption and controlling-PTY
input/resize/foreground restoration. Windows also generates a real SIGINT in
the owned caller while its command remains blocked, then verifies descendant
exit; it does not broadcast signals to an external console. Job refusal and
cleanup-failure confirmation remain deterministic unit controls. This is
completion custody evidence, not a resident Picker startup benchmark or an
installed-runtime rollout, and does not expand required unit-only PR CI.
The separate-session native cases use a real interactive Bash with background
job groups, preserve redirected JSON stdout and check that an explicitly
managed detached workload remains live. Managed-detachment cases skip inside
the contained local runner rather than bypassing its suppression; they run in
the explicit disposable native workflow.

The same Linux job also runs Manager's
`tests/native/test_warm_picker_lifetime.py` through its isolated plugin runner.
That packet uses an unmodified warm worker on a private tmux TTY, hard-kills
its birth-pinned owner before and after project binding, and verifies UI,
engine-child, mux-session and slot retirement. Its audited engine contract
double records every child state root; the fixture allowlists the environment
and isolates home, app-data, provider, Manager, temp and mux paths. This is
not full engine/daemon or performance qualification. Run that selector locally
on Windows with psmux and PowerShell available; platforms without supported
warm-worker birth identity retain the cold path and skip this packet.
The Windows fixture also verifies the creation's private namespace against a
different ambient observer selector and carries a parent teammate-mode flag
through the caller-to-worker admission boundary.

## Optional daemon-instance lifetime acceptance

`tools/promotion_unit_tests/test_daemon_instance_creation.py` covers atomic
private inventory creation with Windows FFI doubles and a real portable
directory/lock/record/cursor roundtrip. It keeps existing owner admission,
borrowed runtime anchors, binary descriptor handoff and lazy native imports
unchanged. These admitted units are not native Windows qualification.

The contained Windows selectors
`libs/agent-procutil/tests/test_daemon_instances_native.py::test_created_private_inventory_entries_use_token_user_owner`
and `::test_created_private_file_preserves_binary_bytes_and_noninheritance`
check actual new-entry TokenUser ownership and raw bytes/noninheritance.
The same contained file's
`test_private_creation_survives_concurrent_cold_layout[directory]` and `[file]`
force simultaneous cold layout misses and interleaved creation in two native
threads, retaining actual ownership and descriptor checks.
Run them through the existing bounded process-tree owner with fresh test
storage. A same-user parent/child control whose TokenOwner differs must also
exercise actual reservation through the retained Git-Bash Manager callback;
the standalone creator tests do not replace that consumer boundary.
No existing-path ACL repair, installed-state migration or desktop observation
is needed for these ownership controls.

`tools/clean-room/scenarios/leased-daemon-instances` is a credential-free Tier-P
source/native consumer proof. Use a fresh disposable Linux container, a
read-only candidate and its generated Python source archive, with compilers
absent. Its README defines the source publication and actual process/filesystem
assertions; neither construction nor normal startup compiles a native helper.

Run `python -m pytest tools/test_native_daemon_artifacts.py` for the artifact
contract controls. On a supported, prepared Linux build host, explicitly select
`PROCUTIL_NATIVE_ARTIFACT_ACCEPTANCE=1` and
`test_real_pure_wheel_and_source_archive_need_only_selected_python`
for pure-wheel construction/source binding and lifecycle qualification through
the actual installed interpreter. Its shell controls have no compiler or Python
command on PATH and explicitly select the supported interpreter. Installed tests
retain the canonical per-test inventory fixture; outer-tool imports are not
substitutes for the installed interpreter. This lane remains opt-in rather than
enlarging required unit-only CI.

`python -m pytest tools/test_cleanroom_daemon_fixtures.py` checks finite
Gateway-fixture custody and environment restoration on success/failure, plus
host refusal for the synthetic production-control probe. The existing
process-hygiene scenario retains its full six-scenario flood thresholds; its
daemon roles inherit shared test custody while transient clients remain light.
Supply the installed shared package or an explicit candidate source through
`CR_PPC_PROCUTIL_SOURCE`/`CR_HARNESS_MOUNT`.

Run both `tools/check-daemon-coverage.py` and `tools/check-headless-launch.py`;
visibility flags and lifetime attribution are separate contracts.

`tools/on_demand_tests/test_scratch_copy_boundaries.py` is an explicit filesystem
packet for the bounded, isolated development-test lane. It checks scratch
exclusion before source/test copying and strict link refusal in unfiltered
build contexts. It is not enrolled in the eligible unit packet.

## Optional candidate dependency profiles

The installation-context Python `runtime-profile-policy` reader has no
site-package dependency. `libs/installation-context/tests/test_runtime_profile_policy.py`
exercises a fresh configured adopter with no installation, profile or approval
file, real owner-bound overrides and an actual `-I -S` CLI process.
`test_runtime_deployment_adapters.py` preserves matching native policy parsing
and publication holds, including malformed profile fields. Pure selection,
owner-binding and CLI dispatch controls live in
`tools/promotion_unit_tests/test_runtime_profile_intent.py`; only these pure
controls join required unit CI. These checks prove configuration-policy input,
not a permitted CPU wheel closure, authenticated profile release or native
engine construction. The manual `runtime_profiles=true` acceptance selector
runs the actual readers on Linux and Windows.
The Windows lane enables test-only PowerShell phase markers on stderr.
On a subprocess timeout, the failure retains the parent start timestamp and
child entry, native compilation, path, installation and deployment-reader
markers to distinguish cold startup from reader work. The deadline remains
30 seconds; tracing does not retry or admit a timed-out decision.

`python tools/run-plugin-tests.py agent-worktrees --test-file
tests/test_python_installer_engine.py` covers the canonical candidate engine,
including explicit declared extras. Its offline synthetic packages exercise real
UV construction, installed-origin health, immutable profile reuse and refusal of
an unavailable extra dependency before completion. They also retain the original
base fingerprint and preserve an existing active selector on failure. These are
on-demand construction controls, not additions to the eligible-unit packet.
`tests/test_installer_content_reads.py` in the same development runner exercises
real threaded content comparisons: four-reader/pending-work bounds, ordered
errors, main-thread shutdown cancellation, caller-thread progress, progress-sink
failure settlement, deadline settlement, mutation refusal and cleanup. The canonical
`installer_retained_content.py` helper is synchronized through the existing
installer materializer; source construction retains its current progress contract. Its
case-distinct filename control requires a case-sensitive native filesystem.
The promotion packet's retained-input fixtures use a deterministic serial
scheduler only for their existing overlap/remapping contracts; those unit
results do not qualify concurrency.
The existing native candidate-profile lane also runs
`tests/native/test_retained_worktrees_construction.py`: a real materialized
Worktrees graph, an empty private UV cache, installed provenance preparation,
full installed health and settled retained-source cleanup under the original
480-second cold-construction watchdog. It never publishes a runtime selector.
The candidate-engine file also exercises explicit `local_packages` role closures against real
UV and fresh interpreters: client-only omission, a service's transitive local
closure, complete source retention, immutable reuse without construction tools,
receipt drift and excluded installed-distribution refusal. Incomplete direct or
transitive selections must fail even when prereleases are enabled; they must
not complete a candidate or change the active selector. The caller supplies
role policy; these controls do not add a second dependency resolver.
The manual `installation-context-acceptance.yml` selector
`candidate_profiles=true` runs those focused real-UV profile controls on fresh
Linux and Windows runners without enlarging required PR CI.
They do not prove a production extra graph, hosted feed access, a native support
matrix or required release enforcement. The
[feed-construction effort](efforts/active/feed-construction-release-gate/README.md)
retains those open acceptance gates.

The independent `chat_construction=true` selector runs both the 180-second
acceptance and 600-second complete contained Chat lanes on the same candidate
on Linux and Windows with Python 3.10 and 3.11. Preparation explicitly binds
`UV_PYTHON` to the setup step's interpreter rather than allowing an unrelated
managed Python to change the advertised floor. Python 3.11
constructs the actual client role from independent Chat/library source
distributions using the wheel's unchanged canonical engine, verifies the exact
installed closure and immutable reuse, then invokes the slot-local canonical
retention launcher after deleting source inputs. Python 3.10 retains core/client
qualification and explicitly refuses construction before creating a runtime.
Foreign import scopes and attempts to prepare a completed slot must refuse.
This optional lane does not publish a selected installation, create a host,
prove a governed feed or qualify native subscription/adoption.
`tools/test_standalone_qualification.py` checks its explicit opt-in, literal
hosted runner routes, retained default-lane exclusions, interpreter binding and
exact acceptance/full commands. Deterministic workspace clock-boundary controls
also require forwarded owner timeouts to stay within the original 60-second
budget; no floating-point tolerance is added to that assertion.

## Persistent reproducer boundary controls

`tools/test_reproducer_lab_boundary.py` exercises the pure
[`tools/reproducer-lab/`](tools/reproducer-lab/README.md) declaration and static
Docker-observation controls. Run that selector through the caller's bounded
test supervisor and selected Python/pytest environment. It invokes no Docker,
service, network, dependency installation or resource lifecycle operation.
Exact integer budget boundaries, forbidden/missing exposures, foreign or
duplicate mounts, minimal environment and physical-identity fields are tested.
Even a clean synthetic observation retains explicit unavailable measurement,
egress, exclusive-custody and guest/runtime qualification requirements; this
is not an admission result or persistence proof.
`tools/test_reproducer_lab_monitoring.py` adds storage-only threshold, reserve,
cadence, lifetime, loss/resume and failed-refresh controls, mocked finite
Docker-reader seams and local synthetic guest-traversal files. Run both selectors
through the same bounded supervisor. The actual reader reuses `agent-procutil`
for bounded caller-owned Docker clients; its guest probe has a separate deadline.
The controls invoke no real Docker or service action and do not qualify
persistence, egress, exclusive custody, native capture or strict pre-effect
support. Ordinary-volume thresholds are monitored, never hard quotas. Those
separately authorized native experiments remain open. No unconditional promotion
or platform matrix is added.

`tools/test_reproducer_lab_network.py` adds bounded synthetic observations of
the exact internal bridge network, explicit subject/peer IDs, complete endpoint
membership, external attachment refusal, physical replacement, retained resume
selection and the final command's deadline. Run all three reproducer selectors
together through the bounded supervisor. Static boundary controls also require
the exact private `/tmp` and `/run` flags and declared byte sizes.
These are mocked network inspections and static tmpfs configuration assertions,
not actual egress, guest mount-table, peer-runtime or custody qualification.

`tools/test_reproducer_lab_source.py` adds in-process immutable-source metadata
and mocked materializer controls. The separately selected
`tools/reproducer-lab/source_native_tests.py` uses actual local Git against a
fresh synthetic repository to prove one-commit source closure, dirty/untracked
exclusion and no copied source config/hooks or older history, using actual
SHA-1 and SHA-256 materialization. Pure command controls require system/user
attribute-policy exclusion separately from Git configuration exclusion.
Run either through the bounded supervisor; the native filename is outside
default discovery and does not join required PR/promotion CI.
Neither lane builds an image, creates Docker resources, installs dependencies
or qualifies an executing guest's source or service runtime.

`tools/test_reproducer_lab_mounts.py` covers the opt-in effective volatile-mount
reader using synthetic tables, mocked Docker and local directory-descriptor
fixtures. It checks bounded table bytes/records, read-only root, exact private
tmpfs capacity/flags/owner/mode, distinct devices, nested/shadowed mount refusal,
retained physical selections and failed/expired observations. Run it with the
other pure selectors through the bounded supervisor. It queries no real guest
or host mount table and does not qualify the complete guest mount policy,
executing source/runtime, egress, custody or persistence. No native lane or
unconditional CI matrix is added.

`tools/test_reproducer_lab_offline.py` covers the operator-selected single-container
offline path: compatible v1 internal defaults, explicit `network_mode: "none"`,
kernel byte/record limits, up/loopback flags, route/address checks and before/after
configuration drift. Fake sockets require the actual no-route error for both
families and reject success, refusal, permission errors, timeout or unsupported
families without fallback. Mocked Docker pins resources around the finite probe.
Run it with the other pure selectors through the bounded supervisor. No real
socket, host kernel inventory, bridge, resource creation or service is exercised;
actual offline qualification and remaining custody/runtime gates remain open.

## Wheel-input inspection

`tools/test_installer_dependency_compile.py` covers both ordinary metadata-only
exports and the explicit retained source-graph mode. Native dated loopback-feed
controls freeze source/configuration inputs, export hashed seasoned transitive
dependencies without local distributions, and refuse missing upload-age
evidence without replacing an existing output. Graph and effective-omission
refusals cover inactive markers/extras and recursive requirements, while native
controls preserve root-metadata registry pins and recursively mapped local
dependencies. Context-only argument/environment/ambient project mappings refuse
for direct, recursive and project-metadata inputs. Ordinary compile keeps its directory-dependency boundary. These are
candidate-export controls, not source-origin approval or real-feed admission.

`tools/test_runtime_source_lock.py` composes the real installation-owned planner,
existing source receipt validator and retained compiler. Fresh offline sources
prove the empty third-party case; a dated synthetic loopback feed proves a
nonempty exact hashed candidate. Independent selection mismatch, source mutation,
wrong interpreter target, malformed/inapplicable output, byte-limit exhaustion,
child failure, timeout and cancellation refuse and clean up. Retained inventory
coverage and original digest comparison reject a transient source edit even if
the original is restored before resolution returns. Native candidate mode
rejects universal/platform overrides, including environment configuration.
The caller's deadline covers retention entry, both UV settings probes and
per-chunk source copying as well as compilation and consumption. The same optional
`dependency_compile=true` Linux/Windows lane runs these controls, not required
unit CI. They do not qualify private-feed policy, wheel admission or deployment.

The explicit `retain_source_wheel_lock` path additionally consumes native
`pylock.toml` output, retaining actual filenames/hashes and the compiler's held
configuration. A native export can contain multiple compatible files; this
initial path refuses that ambiguity rather than implementing another wheel
ranking algorithm. Ordinary requirements exports remain unchanged. Real
multi-wheel and source-only feed controls refuse before acquisition or a
third-party backend request. Compressed wheel-tag alternatives pass through the
existing bounded canonicalizer before filename parsing; a regression repeats
each alternative 128 times and asserts the parser receives only one combination.

`tools/test_runtime_source_authentication.py` composes actual installation-owned
fallback intent with the existing configured-runtime-source acquisition and
canonical preparation paths. Real local Git repositories exercise exact
origin/ownership, fetched merged commit/tree, source-policy changes, prepared
byte changes and cleanup. Manager self-update preferences do not authorize
runtime source; its default marketplace choice remains unsupported by this
explicit provider. No artifact index, manifest or caller-provided source receipt
authenticates these inputs.

`tools/test_runtime_source_feed.py` continues that real source selection through
native exact-file resolution, the same held native feed configuration and the
existing bounded TLS acquisition worker. Feed capture must not run another
resolver or fabricate a release selection. A public test-only server leaf,
signed by the existing fixture CA, lets both UV and the worker verify the chain;
the older CA-as-leaf fixture alone is not acceptable to UV.

`tools/test_runtime_source_build.py` extends that path through a single native
runtime/build union lock, byte-inspected build dependencies, an independent
immutable build-tools environment and the existing offline first-party build
frontend. A real PEP 517 backend builds authenticated sources, including the
canonical process-ownership implementation, and imports a declared build-only
registry requirement that must be locked, acquired and installed only in the
tools environment. Actual wheel metadata must match
static runtime declarations and selected index Python hints. The final runtime
contains only its reachable runtime closure, not build tools; installed health
and held-input offline reuse exercise the existing constructor. Negative
controls refuse corrupt RECORDs, direct build URLs, metadata drift and source
policy revocation; a native timeout must settle an actual backend descendant.
The composition exercises the existing `retain_source_profile` owner with
owned offline build custody and its exact local-package selection. Foreign
build roles, changed tool versions/files and an online build request refuse
before the backend runs; ordinary promotion-toolchain controls remain intact.
Fresh and reused tools slots validate their installed interpreter against the
selected marker environment before any backend runs. Native regressions query
real tools interpreters, inject a mismatched target result and require refusal;
the success control still proves unchanged immutable reuse. Offline source
builds use the existing source-freezing helper to keep backend working paths
compact on Windows while checking the original and compact source hashes.
Native settings-only capture additionally proves stable configuration identity,
changed seasoning identity, no lock creation and zero feed requests. A current
build-wheel denial must refuse before tools installation or backend execution.
The exact admitted offline lane also caches locally built wheels in the
installation owner's `.source-wheel-cache/inputs`. Reuse binds authenticated
source content and Git tree (including modes), the complete selected backend
wheel identities, target/profile,
executor/tooling bytes and the actual sanitized build environment. Retained
temporary paths and package versions alone never identify a build. Cache
publication uses the existing per-key kernel lease and an atomic completed-entry
rename; corrupt or foreign completed entries refuse explicitly. Wheels
remain unpublished through finite Windows sharing-lock retries. Native controls
hold a real wheel handle across the first rename, then release it; persistent
denial, changed candidates, destination collisions and expired allowances still
refuse without publishing or removing another owner's entry. Retries retain the
original deadline and reverify the same wheel and receipt. Cached bytes are
copied and hashed into caller-owned output before the ordinary wheel
inspection and installed-content checks. The cache is not source/feed approval
and does not enable another consumer. `tools/test_runtime_source_wheel_cache.py`
covers failure, corruption, input invalidation and concurrent construction;
the real source-build packet additionally checks zero repeated backend builds
across fresh retained workspaces and source-body/mode-only invalidation. Compact
source content and modes are checked before a newly built wheel can be
published, so a backend-mutated source refuses without poisoning a later retry.
Source-mode configuration refusals additionally forbid effectful compiler
execution and require zero package requests for implicit public indexes and
unsupported index, credential, TLS and proxy settings.
These tests do not qualify persistent offline discovery, activation, ordinary
consumer enablement or live private-feed operations. The build checks have
their own six-minute Linux/Windows jobs under `dependency_compile=true`, rather
than extending the existing compile job's budget or required unit CI.

The same optional `dependency_compile=true` selector also runs independent
six-minute Linux/Windows installer-latency contract jobs. They check the
shared signed-venv helper's preserved seeded default and explicit UV-owned
`-WithoutPip` mode. Windows additionally verifies real signed copied interpreters,
absence of pip/setuptools and actual fixture-wheel installation by UV.
The canonical Codespaces runner selects only the batched closure/retry,
signed recovery and native cold/warm source-edit cases. Every explicit local
root and reinstall/editable selection is preserved in one retained transaction;
the real native case checks canonical editable source mappings overridden by
normal non-editable installation, all nine installed distributions and
refreshed same-version source. These are disposable correctness checks, not a
whole-update speedup claim, and do not expand required unit-only PR CI.

The selector also runs independent six-minute Logger Linux/Windows batch jobs.
`tests/test_install_batch.py` keeps its native cases in explicit T3: canonical
and materialized payloads must make two governed calls, retain every local
root and named reinstall, stop before activation on either transaction's
failure, and skip package work for a reused construction. Real UV installs
fixture build prerequisites and nine local distributions into a fresh
unseeded slot, then refreshes a same-version source edit. Build isolation and
seasoning remain enabled. The existing offline full-provision fixture checks
the same batching against the real Logger package, health and publication flow.
Its snapshot-only first-use control removes the original payload and libraries
before invoking the compatibility wrapper. The UV emulator supplies private
package effects and cache discovery, copies the fixture backend into a synthetic
setuptools package, and invokes it directly through Python. It does not use the
declared setuptools/wheel distributions or qualify native UV/backend execution.
The actual foundation
constructor, source/wheel verification, native custody and runtime health remain
enabled; successful first use must include a recorded wheel build. Native stamp
uses the base's 90-second Windows allowance (60 seconds on POSIX); first use
retains its 420-second subprocess limit.
The outer observation window is 630 seconds on Windows and 600 on POSIX,
retaining 120 seconds of setup headroom above both child limits for release-payload
materialization and synthetic backend staging before those individually bounded calls;
it does not extend either native subprocess or constructor phase allowance.
Logger's existing Windows retry fixtures allow 180 seconds for their native
subprocess and 210 seconds per item: three/four-attempt cases exceeded the old
20-second subprocess cap on a slow host. Exact attempt counts, mocked 3/6/10
backoff, failure propagation and production deadlines are unchanged. Run these
separately from the full-provision case to retain the aggregate runner budget.

`tools/test_installer_wheel_acquisition.py` uses a public test-only localhost TLS
certificate/key and real owned workers to check exact file/redirect trust,
credential isolation, file policy, inclusive byte/storage limits, blocked DNS,
trickling headers/body and cleanup. `tools/test_runtime_feed_configuration.py`
composes the actual owner resolver and native UV preflight with TLS download,
complete-profile inspection and a real offline numbered runtime. It verifies
actual installed distributions/health and acquisition-policy receipt binding,
with poisoned ambient CA/proxy/resolver/cache settings. Source selection,
supported native configuration, stable policy identity and unsupported-provider/
constraint refusals are distinct controls. Release-verifier backend responses
remain synthetic; no live-feed or production approval is claimed.
Native capture additionally compares equivalent ordered/duplicated certificate
exports and proves that actual certificate additions/removals or changed
transport verification flags still invalidate policy identity. Pure transport
canonicalization controls live in `test_source_profile_derivation.py`; no
machine certificate-store write or identity override is involved.
The existing opt-in `wheel_inputs=true` Linux/Windows jobs include both suites;
their six-minute budget and required unit-only CI roster are unchanged.
Native acquisition controls additionally prove overlapping TLS transfers,
selected-order evidence, shared inclusive request/redirect/metadata/wheel-byte
budgets, and peer settlement plus private-input cleanup after a digest failure.
Pure worker-ceiling, budget and orchestration controls live in
`tools/promotion_unit_tests/test_wheel_acquisition_budget.py`; its executor
boundary is synthetic and does not qualify real overlap or native settlement.
The retained-wheel controls exercise a real local-TLS metadata pass without a
wheel download, exact-byte and storage limits, first-owner refusals, link
trust, custody mutation and deadlines. Actor-free controls separately cover
the selected-file decision; neither test approves a profile or runs an update.
A simulated pre-header reset followed by a real local-TLS request exercises the
bounded retry; pure controls cover shared budgets, deadline, peer cancellation
and redirect credential isolation.
Native deployment-reader cases also cover `downloadOrigins` parity. A real
constructor timeout control proves the shared deadline settles its child and
lease, removes partial inputs and preserves the selected healthy prior.
Windows controls retain a wheel whose path exceeds 260 characters and observe
two real console-worker cycles from a `pythonw.exe` parent, with zero surfaced
windows or foreground transitions. Storage thresholds measure file-content
bytes, including control/helper copies; filesystem allocation overhead and
enabled budgets still require the parent effort's separate measurements.

`tools/test_runtime_source_wheels.py` continues the retained selection through
real first-party/helper wheel builds, complete metadata/extras validation and the
existing offline immutable constructor. The source profile is frozen before the
constructor creates its first slot. Its receipt binds the selected source,
fallback trigger, policy, target, exact complete profile and locked build tools.
The constructor uses the selected owner's runtime root and source generation,
not an alternate caller-supplied destination. Changed source, altered/missing
third-party lock inputs and dropped helper extras refuse. Unit-only graph and
build-deadline controls live in
`tools/promotion_unit_tests/test_source_profile_derivation.py`.
That packet also covers explicitly opted-in native file matching against the
recorded target's ordered tags, mandatory SHA-256 with valid auxiliary hashes,
applicable package markers, tied-file refusal and unchanged strict defaults.
Exact first-party exclusion controls admit only the independently selected
retained constraint file; other constraints, overrides and workspace inputs
still refuse. Compile spelling and extended Windows file-URI controls are pure
regressions in the same packet. Native closed-wheel tests exercise long retained
paths and installed-origin parity; acquisition controls expose only the refused
origin, never its path or query. These controls do not enable a default consumer
or approve a production feed.
Built wheels must preserve the retained source's dependencies, extras and
Python requirement. Native regressions alter actual wheel metadata, refuse
nonordinary inputs before opening, bound compressed filename tags before
expansion and reject oversized WHEEL metadata before an unbounded read.
Deadline-bound builds use the existing installer process-tree supervisor;
a real backend's delayed writer must exit before staging custody is released.
Both absolute and PATH-only UV selection reach real construction using the
same selected absolute executable throughout building and assembly.
An installed-target mismatch must refuse before the constructor writes its
completion marker. Third-party RECORD entries accept the runtime verifier's
SHA-256/384/512 policy; built first-party wheels retain SHA-256-only records.
Marker probes bypass site initialization: a real venv control proves that
an installed `.pth` hook executes normally, but not during target inspection.
The existing `wheel_inputs=true` opt-in also runs a separate six-minute native
source-profile packet on Linux, Windows and macOS. This does not extend the existing
wheel-inspection job's deadline. These are synthetic source/byte and fresh-root
construction controls, not private-feed acquisition, production CPU/accelerator
profiles, persistent reuse admission or deployment. Callers still own permitted
third-party acquisition and retain the source-lock and wheel contexts through
health; no new package approver or feed exception is introduced.
CI-scope inventory checks execute all four actual pytest selectors in one
bounded child, sharing interpreter imports but not collection results. Every
partition still has its original exact-inventory assertions; the aggregate
child retains the original twenty-second cap. The macOS source-consumer lane
does not qualify standalone-service supervision or new engine deployment parity.
A clean `-I -S -B` caller also denies native-loader operations during consumer
import. Feed-governance native adapters load only when their operations run;
the no-actor packet neither preloads them nor permits their effects.

`python -m pytest -q tools/test_installer_wheel_inputs.py` exercises real
synthetic wheel archives, streamed digest/RECORD checks, independent selected
metadata mutations, ambiguous/linked/encrypted members, compressed metadata
limits, deadlines and source mutation/replacement. A real isolated `-I -S`
interpreter proves the canonical inspector needs no site packages. The
five byte-only cases in `tools/promotion_unit_tests/test_wheel_inputs.py`
join the required no-actor packet alongside complete-profile checks in
`tools/promotion_unit_tests/test_runtime_profile_wheels.py`. These cover
SHA-256/384/512 RECORD policies, SHA-256 source facts, complete-set budgets,
additional inspector limits and the shared deadline. Native filesystem/bootstrap
checks remain in the development suite. Existing promotion and
standalone artifact suites exercise the shared verifier's trusted-source
import closure and source/toolchain checks. The optional
`installation-context-acceptance.yml` selector `wheel_inputs=true` runs the
native inspector, complete-profile verification, existing exact-profile controls, promotion wheel refusals
and trusted CLI import-closure check on Linux and Windows. It does not expand
the required unit-only CI packet to include native processes.

These are inspector-component tests, not proof of authenticated profile/feed
admission, retained offline assembly or installed runtime activation.

`tools/test_installer_wheel_snapshot.py` covers exact-set byte custody through
`installer_wheel_snapshot.retain_wheel_inputs`. The helper reuses the installer's
private temporary-input owner, copies only ordinary unlinked files, bounds
wheel count, per-wheel/aggregate bytes and temporary archive storage, and shares
the caller's deadline through streamed copying and custody rechecks. It yields
read-only filename/path and filename/stream maps for the existing complete-profile
verifier. Call `snapshot.verify()` before a dependent publication boundary;
normal context exit also rechecks custody, then closes streams and removes only
owned copies. Caller files need not remain unchanged after the snapshot yields.
The expected SHA-256 map is byte evidence, not authenticated profile/feed policy.
Windows controls additionally prove read-sharing permits independent readers
but denies writes/replacement, handles access-time-only metadata updates without
a false byte-mutation refusal, and preserves extended-length input paths.

Fresh/cache parity, exact inclusive budgets, pre-open replacement, mutation of an
earlier source, growth during copying, read/write errors, final-EOF deadline,
retained mutation, cancellation cleanup and an actual `-I -S` bootstrap child
are exercised in the same optional `wheel_inputs=true` native lane. No resolver,
backend, venv constructor or production profile consumer is enabled by this API.

`tools/test_installer_closed_wheels.py` extends that same optional native lane
through the actual existing candidate constructor. Fresh independent venvs
consume an inspected transitive wheel closure without ambient configuration,
network, cache, dependency resolution or builds. The tests check installed
health and exact distribution/origin receipts, PEP 440 version aliases,
unchanged cached-byte reuse without reconstruction, changed binding refusal,
installed receipt/closure corruption, and preservation of the selected prior
after health failure or cancellation. These are real local first-touch and
consumer-contract controls, not an authenticated approval/feed adapter or
production consumer activation.
The large-closure control constructs and reuses 97 actual wheels: its complete
receipt exceeds 32 KiB, while each real health-child command stays below
16,000 characters using digest-bound evidence read from the owned receipt.

`tools/test_runtime_profile_release.py` exercises the release authority adapter
and pre-signing validation through the existing promotion index and attestation
verifier. It checks exact producer/workflow pins, full manifest/first-party
bindings, wrong selection facts, forged approval origins, byte limits,
replacement races and private-input cleanup after failure or cancellation.
It joins the same optional native lane, not the required no-actor packet.
Synthetic attestation responses test the actual verifier's command boundary;
there is not yet a published runtime-profile subject or a production consumer.
These controls do not qualify adopting-policy defaults, governed acquisition,
source fallback or live release approval.

`tools/test_runtime_profile_policy_plan.py` composes the real installation-owned
policy resolver with that release verifier in the same optional native lane.
It covers a fresh no-file baseline, explicit/retained backend intent, disabled
fallback, invalid/foreign/pending owners, and failure-class separation. A fresh
isolated Python caller can request source fallback without artifact infrastructure.
Supplied subjects still authenticate when their artifact is absent; malformed,
ambiguous or unauthenticated subjects never become availability outcomes.
The result is a frozen policy plan, not source verification, an exact source
lock, feed admission or permission to bypass a publication hold.

## Optional Dispatch service replay

`tools/run-plugin-tests.py agent-dispatch --test-file tests/test_delegated_replay_consumer.py
--test-file tests/test_delegated_replay_binding.py
--test-file tests/test_delegated_replay_transport.py` exercises the finite replay
consumer and actual TLS/PoP Gateway/connector with isolated SQLite and replay
stores. Dispatch's dev preparation selects the same checkout's PR and Fleet
packages; default Dispatch installs remain independent. The existing manual
`installation-context-acceptance.yml` `dispatch_delegation=true` selector runs
these alongside the local callback contracts on Linux and Windows. No new job,
production credential or fleet placement is added. This does not qualify the
unimplemented installed consumer lifecycle or production fleet budgets.
Scoped controls retain coexisting v1/v2 rows without conversion, reject changed
bindings and preserve older v1 client construction. Both versions exercise
the actual watcher-to-Dispatch nonevent/restart/lost-ACK path and lost committed
register/ACK/cancel responses through real TLS/PoP and both disk owners.
The programmatic `server.build_app` control exercises the selected scoped
factory through the real front door's queue, active-route checkpoint and
lifespan/drain. Default-import protection covers that front door with neither
optional PR nor Fleet package admitted.

The same manual packet includes `test_delegated_replay_credentials.py`, a T2
integration over the actual watcher, scoped Dispatch consumer, native GitHub/ADO readers
and typed TLS credential exchange. Per-request renewal must override poisoned
ambient credentials; provider refusal across a drained watcher restart must
retain original waits without a wake even when the synthetic PR has merged.
Restored authorization settles one wake per wait without persisted credentials.
The fixture substitutes GitHub forge CLI responses or ADO HTTP responses after
the actual native request/header construction, not credential acquisition or
native snapshot construction. The ADO control uses bearer authentication and
the configured observation reader, not the legacy `az` snapshot path; it does
not certify basic-auth or broader ADO conformance. It is not a live provider,
forge TLS-transport or deployment test.
Its two service lifetimes and nested native commands have a 180-second Windows
and 120-second non-Windows item watchdog; production deadlines and the existing
ten-minute manual jobs remain unchanged. This does not add native work to the
required unit-only portfolio.

The real watcher nonevent/restart/lost-ACK case has a 120-second item watchdog
on Windows. Its two service lifetimes, native subprocess observations and
durable recovery measured 20.90/23.46 seconds locally for v1/v2, while hosted
Windows exhausted the ordinary 30-second item budget. This functional
integration allowance leaves its readiness, request and drain deadlines,
assertions, non-Windows item limit and runner/workflow budgets unchanged.

The shared-maintenance cardinality case in `test_waiter_maintenance.py` retains
real cold SQLite schema creation, 40 durable waiter preparations and a bounded
32-row replay cycle. It has a 120-second item watchdog on Windows: hosted
acceptance exhausted the default 30 seconds during the initial schema commit,
before waiter preparation began. Other items, non-Windows item limits and the
runner/workflow budgets are unchanged; this is not a production database change.

## Optional PR service lifecycle

The existing `pr_service=true` manual Linux/Windows lane also runs
`services/pr/tests/test_image_inputs.py` through the standalone runner. It
checks the repository-only offline-context consumer with real retained
synthetic wheels, a fresh offline hash-enforced installation and tampered-wheel
refusal. It neither contacts a feed nor invokes Docker. Profile/feed/base
approval, real image construction and container lifecycle remain separate
acceptance tiers; this lane does not substitute for them.

The independent `dependency_compile=true` selector exercises the canonical
retained lock-export API on Linux and Windows. `tools/test_installer_dependency_compile.py`
uses a synthetic loopback package index: ordinary UV selects a recent transitive
wheel, while the retained export selects the seasoned version with exact hashes,
ignores stale output pins, survives mutations of original project/requirement/
configuration files through canonical and absolute-dot input/include paths, and refuses
missing upload timestamps. Directory requirement edges and local source mappings
refuse before export; metadata-only project inputs still produce registry-only
hashed output after the private snapshot is removed. The same bounded
lane retains the existing transitive/isolated-build installer age cases.
Root-working-directory coverage prevents a canonical retained include from
being remapped a second time when its original path remains a retained suffix.
Pure option, effective-output and resolver-drift controls run alongside those
native cases. This does not resolve a governed feed or approve a target profile;
the subprocess/network cases remain outside required unit-only CI.

The separate manual selector `pr_artifacts=true` exercises real first-party
wheel construction and verification on Linux and Windows through
`tools/smoke_public_toolchain.py --standalone`. It shares one seasoned-public
build-toolchain lock with the existing plugin builds, validates PR/Fleet
standalone bundles against their source/resource closure, and installs only
those built wheels offline into a fresh environment outside the checkout.
It also exports both standalone projects through a real nested promotion
scratch worktree with a simulated reuse-catalog miss, validates the canonical
source bundles and admission identity, and checks cleanup.
This is packaging evidence, not runtime dependency admission or image proof.
`tools/test_standalone_python_artifacts.py` retains the fuller synthetic
builder/bundle refusal matrix; pure identity, inventory, exact-hook and export
controls in `tools/promotion_unit_tests/test_standalone_artifacts.py` join the
required no-actor unit packet without adding native builds to required CI.

The manual `installation-context-acceptance.yml` selector `pr_service=true`
runs the focused PR serving/observation lifecycle on Linux and Windows:
`test_watch_replay_store.py`, `test_watch_service.py`, `test_watch_gateway.py`,
`test_watch_gateway_transport.py`, `test_watch_observer.py`,
`test_watch_replay_client.py`, `test_watch_replay_binding.py` and
`test_watch_replay_client_transport.py`, through the
existing contained plugin runner. It exercises real locks/disk/native children,
TLS/PoP reconnect, restart and drain, with synthetic issuer/provider inputs.
V1/v2 client controls cover expected scope, post-discovery remapping,
legacy-provider rollback without downgrade, original-ID reconciliation and
credential/channel loss before lookup or during an already-started commit.
It adds no required-PR process matrix and does not qualify a production
credential provider, installed artifact or fleet placement.

The same manual selector also runs `tools/run-standalone-tests.py services/pr
--prepare --timeout 180`. Its independent environment installs this checkout's
PR/Fleet dependencies and the standalone wheel without Copilot. Tests build a
normal source-independent wheel, validate the existing reference-only
`container.service` declaration, and drive an installed foreground service in
a separate process over Fleet's real TLS/PoP Gateway fixture. Provision,
duplicate writer refusal, register/observe, token-file reread on reconnect,
durable restart and exact ACK use synthetic identities/provider snapshots.
Windows uses the public embedding stop event; Linux exercises CLI SIGTERM.
Windows passive-reader rename sharing is tested at the actual file boundary.
These controls do not qualify a live provider, built container or fleet
placement, and do not close the remaining production-performance/update gates.
The same standalone lane discovers `services/pr/tests/test_local_foreground.py`,
`test_local_lifetime.py` and `test_local_recovery.py`. They select the independent
Linux `repository-local/v1` profile with no running Core/Gateway: both synthetic
providers populate before subscriptions, actual packaged CLI processes query
and consume continuing events, and original-root restart preserves each
consumer's original IDs and ACK. Separate embedded foreground controls hold
exact native/disk cleanup and inject post-replacement persistence uncertainty.
These process fixtures remain in the manual standalone lane, not required
unit-only PR CI. Use a short private runner scratch root: the selected Unix
socket is an absolute child of its dedicated state root and is limited to
100 encoded bytes. The local runtime is Linux-only; strict typing separately
analyzes both Linux and Windows.
The focused observer/service tests additionally exercise a four-read admission
bound, rolling progress past a blocked target, distinct subscriber identities,
real native-child completion order and retained writer custody until every
parallel read settles. These source controls are not a fleet latency benchmark.

The same store/service and installed-host selections exercise durable
fleet/Gateway/slot binding on every disk transaction, cross-mode and wrong-volume
refusal without mutation, original-ID recovery after uncertain commits, and
same-bound-volume restart/replay/ACK. Installed host processes try each changed
identity after clean drain and must refuse without native reads or replay writes.
This is a disk-schema/update safeguard, not an image build, automatic migration,
cross-host writer fence or completed update/rollback lifecycle.

## Optional SSH service and native relay

The separate `plugins/agent-ssh/tests/test_gateway_sshd_native.py` source
packet has an opt-in `AGENT_SSH_VAULT_NATIVE=1` / `-k vault-pair` case.
Select the current first-party agent-ssh and agent-vault sources with prepared
test dependencies, actual KeePassXC CLI and native OpenSSH server/client/keygen
under the canonical contained runner. It reuses the PID-owned disposable Vault
fixture, public-only preparation and pinned protected installation, then uses
that exact pair for the existing listener's command/context, authorization,
preservation and teardown assertions. Locked new setup refuses while the
materialized pair remains usable; no system agent or production Vault is used.
`-k 'not vault-pair and not encrypted-pair'` preserves the independent
synthetic-key/outer-shell packet.
On Linux, retain the disposable-account/host-state prerequisites below.
This is current-source first-touch consumer proof, not installed setup,
encrypted-key signing or user-logon lifetime. Default runs skip the Vault case.

The same packet's Windows-only `AGENT_SSH_ENCRYPTED_NATIVE=1` /
`-k encrypted-pair` case generates an encrypted disposable peer key. An
already-available .NET Framework compiler builds a GUI-subsystem askpass
fixture; no compiler or signer is installed. Its captured stdout is checked
before the native consumer runs. BatchMode must refuse without invoking the
helper, refused fixture unlock must not authenticate, and explicit fixture
unlock must complete the existing pinned listener's command/context,
refresh/withdrawal, forwarding-refusal and teardown controls. Private-key
bytes/timestamp remain unchanged. Select this opt-in under the same contained
runner and60s item/180s aggregate limits. The helper substitutes a synthetic
human response; it is not an attended operator unlock, selected production
signer, installed user-logon setup or lifetime proof. Default runs skip it.

The independent `ssh_service=true` selector runs the contained standalone
`services/ssh` suite on Linux and Windows. It covers installed foreground
Gateway registration, later-machine enrollment/notifications, original-request
recovery and writer drain. Its disposable environment installs the complete
dependency graph non-editably through the existing guarded preparer. The
source-free consumer witness uses isolated Python without startup `.pth` files,
checks installed distribution custody, refuses checkout reads and external
source imports, and reuses the real connected/restart convergence scenario. An
intentional checkout import must fail under the same guard. This qualifies an
installed runtime, not release-artifact provenance, a selected image or managed
installation/adoption. The Linux lane additionally selects native
acceptance in its disposable runner account: real OpenSSH listener custody,
two distinct relay keys, a loopback reverse forward, a peer's permitted local
forward and an unauthorized reverse-bind refusal. It never invokes a remote
shell, exec, PTY or subsystem. Native target-login credentials remain distinct.
The fixture owns one finite temporary directory in that disposable account's
actual home so production `StrictModes` remains enabled; it does not read or
modify existing SSH configuration. Do not select
`SSH_SERVICE_NATIVE_ACCEPTANCE=1` in a production account.
The Linux job also runs both target-login native fixtures in that disposable
account. Their finite home-directory fixtures require
`AGENT_SSH_NATIVE_DISPOSABLE=1` and the plugin runner's explicit
`--allow-host-state --allow-explicit-tiers`; default local runs skip them rather
than writing to an ordinary account's home or relaxing `StrictModes`.
The Windows lane remains control-plane-only and explicitly skips Linux-native
cases. These tests do not qualify a built native container image, normal Fleet
placement, provider/Vault admission or a preserving production migration.

The same suite covers optional explicit peer approval: fresh/restarted pending
state, exact public fingerprints, authenticated active approvers, self/stale/
unauthorized refusal, distinct confirmed recovery and original-decision retries.
Its real TLS/JWT/DPoP test starts the installed CLI with three synthetic caller
identities, proves that pending enrollment cannot notify/admit, and restarts
that owner against retained state. The Gateway fixture follows real time for
that native restart instead of weakening proof freshness around a frozen unit
clock. SDK tests additionally prove old/missing support and policy rollback
refuse before a required-policy request can admit anything. These remain
contained standalone/explicit-lane tests, not additions to required unit CI.
The maximum-binding regressions assert the actual 32 KiB response boundary:
compact original decisions remain retrievable as the directory grows,
oversized response construction refuses, capacity failure rolls back the whole
decision transaction, and every long-identifier candidate remains reachable
through byte-bounded pending pages.
The exact-limit short-principal control exercises a 32,769-byte terminal
response through the actual client/service route, then proves both cursor
representations are budgeted and every candidate remains reachable.
Native restart consumers share the Gateway fixture's realtime clock. Invalid
local writer scopes also refuse before credential-runtime imports, with the
installed CLI's existing fifteen-second refusal deadline unchanged.
Policy-round-trip controls also prove that a retained observation is durable,
stable across later directory growth, and remains readable as superseded after
a generation change. New identical request aliases preserve the original
candidate decision; a withdrawn status caller cannot commit receipt repair.

## Optional diagnostic telemetry

Manager's `tests/test_launch_update_requests.py` native lane checks the user-local
automatic-maintenance veto in both Windows shells and POSIX Bash, including
malformed/unreadable policy and preserved launch continuation. Its real-driver
control separates pending-request coalescing from a distinct project and keeps
each native handoff's 20-second bound. The enclosing integration item has a
120-second aggregate budget for up to three handoffs, a cold-owner handshake,
and canonical drain; this is not a production timeout or required-unit CI
expansion. Run it with `tools/run-plugin-tests.py worktree-manager --test-file
tests/test_launch_update_requests.py`.

Manager's source-native consumer lane is documented in
[`worktree-manager/docs/source-observation.md`](worktree-manager/docs/source-observation.md).
Select its `test_performance_observation.py`, `test_performance_native.py` and
production Picker `test_launch_trace.py` with the existing bounded runner.
The native process/window controls are explicit integration work, not additions
to the required pure-unit packet. Its report distinguishes real source refresh/
input callbacks from unavailable headed paint and production row authority.

The shared `libs/process-telemetry` contract is exercised by its real Bridge
consumer. Run the bounded runner for `agent-bridge` with
`--test-file tests/test_process_telemetry.py --test-file tests/test_telemetry.py`
and for `agent-dispatch` with `--test-file tests/test_telemetry.py`.
Add `--test-file tests/test_spool_append.py` to Bridge for the shared synchronous
append contract: native thread/process contention, Unicode paths/bytes,
one append-only Windows handle per record, and failed/short-write cleanup
without suffix retries. All inputs are synthetic and use runner-owned files.
These tests prove bounded producer behavior and compatibility, not native
startup latency or OpenTelemetry export. Threaded blocked/throwing sink controls
belong to the on-demand integration portfolio, not required unit-only CI.
The [reviewed effort](efforts/active/performance-telemetry/README.md) retains
native boundary, actual SDK/local receiver and overhead qualification.

For the optional export lane, explicitly prepare the isolated test environment
with `agent-process-telemetry[otlp]` using the source library and the same
seven-day dependency policy, then select `tests/test_otlp_telemetry.py` in the
Bridge runner. It uses an owned port-zero loopback receiver and synthetic
protobuf file readback, never a shared collector. A missing extra skips the
module and is not passing wire proof. Normal producer installs remain SDK-free.

For the closed source Logs/Metrics adapter, add
`--test-file tests/test_otlp_signals.py` to the Bridge selection. Its pure models
run without the SDK; genuine protobuf cases explicitly skip when the optional
extra is absent. The floor and currently seasoned SDK/proto/proto-common
resolution are 1.45.1. Required unit CI is not widened into the native portfolio.
The owned port-zero fixtures cover actual public SDK objects, official protobuf,
partial/unsupported/malformed ACKs, total-batch slow-drip deadlines, per-signal
loss accounting and trace-only compatibility. No SDK-private encoders, global
providers, environment mutation or extra producer workers are used.
Manager's source-observation selection includes the same-writer SDK/readback
control; its receiver is a finite test fixture, not the shared collector.
Actual native Logs/Metrics receiver qualification and installed enrollment stay
separately authorized in the [reviewed plan](efforts/active/performance-telemetry/structured-signals.md).

## Static SSH plugin preparation

Bridge's `tests/test_ssh_plugin_preparation.py` executes the streamed stdlib
receiver against isolated synthetic checkouts, without installing Bridge or
starting Copilot. It covers target-native marketplace authority, disablement and
missing-payload refusal; a payload larger than Windows argv limits; UTF-8/spaced
paths; hidden Windows Python with concurrent stdout/stderr drains; and bounded
installer-descendant cleanup, including early worker success/error and loss of
the supervisor while the worker remains active beyond its configured deadline.
Nested execution directories retain their productive cwd while registration
and compiled-context validation use the verified Git root. The
session-qualified controller-owner controls verify both a matching session and
refusal of a mismatched one.

Two live SSH controls additionally require
`AGENT_BRIDGE_TEST_SAME_FILESYSTEM_SSH=<configured-profile>`. Select an existing
profile that reaches the test runner's own filesystem and OS; the fixture checks
access to its isolated temporary checkout before proceeding. It never changes
SSH profiles, installs a remote runtime, starts an agent or changes global
plugin preferences. The controls prove actual SSH streaming and cancellation
quiescence, not cross-machine native plugin discovery. Leave this opt-in unset
for ordinary contained test runs.

## Targeted launch-maintenance acceptance

Manually dispatch `windows-coverage-rotation.yml` with the candidate `ref` and
`launch_maintenance: true` to run Worktrees, Manager and shared-driver launch-maintenance
selections through the existing hardened, network-isolated Linux container
wrapper. The resolved source SHA, exact selector, wrapper invocation and actual
exit status are recorded in each execution artifact. The ordinary daily
nine-plugin portfolio is unchanged. A targeted run never files the shared
portfolio failure issue, even when it targets `dev`.

This exercises native Bash handoff, canonical driver coordination and explicit
failure behavior, not Windows broker activation, installed rollout or every
integration family. The disabled Windows rotation stays disabled; local
contained Windows evidence is recorded separately. Required PR CI and promotion
retain their existing unit-only policy.

The Worktrees selection also includes `test_standalone_provisioning.py`.
It checks zero foreground optional-planner work, both opt-outs, unchanged
default preview and resident scheduling, and return while a real finite
reconciliation child remains blocked. That child is a synthetic downstream
worker, not an installer or serving runtime. The packet does not establish
full cold-start latency, installed adoption or Windows/Linux interchangeability.

The native launcher status packet submits the current canonical `update --auto`
request twice within one launch and requires exactly one child invocation.
Missing, error, legacy-lock, in-progress and completed legacy staging observations
remain byte-for-byte unchanged; they are not update-admission or completion
authority. Windows shells and native Bash exercise their own launcher bodies.

## Construction-source inventory

With Python 3.11 or newer, `python tools/construction_inventory.py` reads the
tracked Python projects,
canonical marketplace and `standalone_consumers.py` roster without constructing
or launching any runtime. It records declared package/Python requirements,
optional dependency group names and script bindings. Products and optional
extras are not assumed to be separate environments; canonical and identified
vendored libraries remain construction inputs.

`--root` binds every ownership declaration to that requested checkout. Its
standalone roster is read as an immutable literal tuple, never imported or
executed; mutable, missing, computed or ambiguous declarations are refused.

Plugin-owned Python projects below a plugin root are declared with
`additionalPythonProjects` in that plugin's manifest, using plugin-relative
`pyproject.toml` paths. Missing, duplicate or escaping declarations are refused.
This inert source-owner declaration does not select or install an environment.
Resolved project locations must stay within their owning plugin/product root
and cannot acquire multiple owners through path aliases.

Exit `1` reports unclassified Python projects, including nested projects and
fixtures that have no established source-owner classification. Exit `2` reports
invalid, missing, duplicate or escaping source declarations. Exit `0` means only
that source-owner classification is complete: `runtime_qualification` remains
`not-performed`. Actual environment composition, selected production profiles,
native interpreter matrix, canonical construction/launch actions and bootstrap/
build closure still require classification and execution. This diagnostic is
not a configured-feed CI or promotion gate.

Run `python -m unittest discover -s tools -p test_construction_inventory.py -v`
for its local contracts. The real CLI control creates a private Git fixture and
checks the actual tracked-tree invocation and nonzero unknown-owner result.
That native subprocess control is separate from eligible-unit promotion
evidence; this change does not add it to the admitted unit packet or alter CI.

## Construction-result contract

`tools/construction_qualification.py` is a pure result-admission library, not a
runner or an active release gate. Its `assess_qualification(expected_plan,
report, target_policy=...)` API requires independently derived expected inputs.
The version-two
`copilot-extensions.construction-plan` binds the source commit, exact composed
candidate digest, effective package-policy and native-target-policy digests and nonempty environment
matrix. Each row names its source project, profile, normalized sorted extras,
exact native OS/interpreter patch/architecture/ABI/hardware class, and digests of its canonical
construction and installed-launch specifications. Those opaque specification
digests bind the existing owners' actions; they are not a new execution format.
`plan_digest` canonically orders rows without making different profiles,
constructors, launch actions or native targets interchangeable.

A version-two `copilot-extensions.construction-results` report names that plan digest and
one result per row. Every result records the observed native identity, elapsed
seconds, terminal status and five check records: `construction`,
`installed_origin`, `installed_launch`, `feed_policy` and `cold_environment`.
Each check needs `passed` and its evidence digest. Missing/extra rows, stale
bindings, another native interpreter, failed/skipped/timed-out/unavailable rows
or incomplete evidence yield `qualified: false` with explicit reasons.
Malformed schemas, duplicate rows, nonfinite/negative durations and invalid
digests raise `QualificationError`; callers must surface these as failures.
There is no successful skip or empty-matrix qualification.

The authoritative native-target policy is `tools/construction-targets.json`.
It requires x64 and ARM64 Windows, Linux and macOS, plus actual Raspberry Pi 5
hardware running 64-bit Linux/ARM64. Generic Linux ARM64 evidence is not a
Pi 5 hardware observation. Each environment/profile/extra selection must cover
every target on every enumerated Python minor; a report with exact coverage of
a reduced plan still refuses the missing target rows. Native policy digest
changes, duplicate target/minor coverage and nonstandard interpreter/ABI
substitutions also refuse qualification.

The qualification floor is Python 3.11; existing package compatibility metadata
is unchanged. The policy currently enumerates standard GIL-enabled CPython
3.11 through 3.15, the stable minors listed by the
[Python Developer's Guide](https://devguide.python.org/versions/) as checked on
2026-10-23. Future stable minors require a reviewed policy update. The trusted
plan producer must independently resolve the latest stable patch per minor and
record each exact version/ABI; that resolver is not implemented here. The
hardware target follows the
[Raspberry Pi 5 product specification](https://pip.raspberrypi.com/documents/RP-008348-DS-3-raspberry-pi-5-product-brief.pdf).
Capacity or compatible-wheel unavailability is failed qualification, not
authority to trim the policy. WSL observations do not prove native Pi hardware.

**Trust boundary:** matching self-reported hashes do not authenticate a producer
or prove execution. The future trusted consumer must derive the complete plan
from authoritative source/profile/platform declarations, bind actual policy and
candidate inputs, acquire authenticated evidence from the expected producer and
verify referenced artifacts before calling this library. It must not accept an
expected plan or native-target policy supplied by the result producer. No complete environment catalog,
feed resolver, native executor, artifact verifier, CI check, promotion consumer
or publication authority is introduced here. Existing source-owner inventory
and installer-readiness declarations alone are not that complete plan.
Version-one plans/results are not accepted as version-two hardware/policy
evidence; there is no inferred generic-hardware or target-policy default.

Run `python -m unittest discover -s tools -p test_construction_qualification.py -v`
for the in-process contract controls. These tests perform no installation,
network or native-process work and are not yet added to the admitted unit
packet. Hosted feed, native construction/launch and required negative
CI/promotion enforcement remain open in the
[feed-construction effort](efforts/active/feed-construction-release-gate/README.md).
The full 35-target synthetic matrix and its reduction/Pi-hardware/ABI controls
are in-process data tests, not 35 native executions.

## Explicit Windows broker experiment

`installation-context-acceptance.yml` has a manual `windows_broker_probe` input.
It selects only the credential-free probe on a disposable hosted Windows
runner, with no package installation, persistent trigger, elevated principal
or shared-host change. The PowerShell entrypoint refuses without explicit
ephemeral-CI consent. Its newly created S4U task has a one-minute execution
limit, a unique owned name and no saved password; cleanup stops only its own
instances and deletes only that registration.

Two activations compare the scheduler instance's engine PID with the native
named-pipe creator and birth-pinned broker process. Each attempts suspended
child breakaway, refuses any remaining Job membership and checks actual child
execution/control after finite broker exit. `result.json` records unsupported
outcomes and positive cleanup separately; a skipped or unavailable mechanism
is not a pass.

This is an API/capability experiment, not a production broker, authenticated
IPC, nested caller-Job survival, interactive-desktop visibility or installed
lifecycle qualification. The private synthetic pipe carries no credentials.
The reviewed broker contract still owns those separate acceptance gates.

After that capability probe, the same explicitly selected disposable job runs
`windows_broker_protocol_acceptance.py`. It copies the canonical broker and
transport sources into an owned immutable fixture directory without installing
packages. The fixture uses the production registration, native pipe protocol
and native handle transfer twice, closes two nested caller cleanup Jobs after
adoption, and checks the actual resident's argv, environment, cwd, split logs,
own-console visibility, continued execution and native retirement.
The installed caller uses public `spawn_daemon`, not a direct-only transport
shortcut. A separate shared execution-driver control calls actual `ensure_worker`
and `drain`: startup must acknowledge before a synthetic installer gate opens,
the worker must survive closure of both submitting Jobs, equivalent pending
and active requests must coalesce, and distinct project contexts and unsets
must complete sequentially before the finite worker exits. These are synthetic
products, not a real Manager update, maintenance rollout or controller activation.
An incomplete worker phase cannot report confirmed overall cleanup.
The same adopted fixture runs `retirement_custody_acceptance.qualify`:
two actual caller Job deaths after native pause must resume the original
external target and settle the outside-caller-Job custodian. Queries name both
nested caller Jobs; the helper's separate disposable expiry Job remains enabled.
A contained-mode negative control must keep the helper inside both caller Jobs. The existing desktop
witness covers all three cycles; none qualifies an installed-host teardown.
It also terminates a separate publishing interpreter without Python cleanup
after Task replacement and after configuration publication. Each control must
leave durable intent, refuse ordinary launch admission, and restore the prior
Task/configuration through explicit installer recovery before continuing.
The action generations use canonical runtime retention helpers. Between each
interruption and recovery, real GC must remove an unrelated control slot while
preserving both recovery generations after the publishing process has died.
After recovery, the old action directory is collected before the second
activation; the independent fixture controller remains available. Uninstall
must release the settled registration's durable references.
`protocol-result.json` reports this result and cleanup independently of the
earlier capability probe; either failure rejects the job. Ordinary contained
units never register a task. This lane is still synthetic source qualification,
not permission to adopt or update a broker on a shared host.

The separately selected `windows_broker_consumers` lane has a 15-minute cap
and constructs actual Worktrees, SSH and Manager runtimes in a credential-free
disposable profile. It exercises automatic maintenance, equivalent-request
coalescing, distinct project delivery and manual exclusion through the real
installer. After stopping the resident, two actual installed Manager
`LiveLoader` readers recover concurrently; both caller Jobs close before two
resident health observations. Two cold cycles must pass, followed by canonical
resident shutdown and all three installers' owned cleanup. This is the actual
Worktrees pivot data path, not a full interactive Picker run or shared-host
rollout. Admitted cold recovery and an authenticated resident's valid `unready`
response without an advisory roster must remain retryable, including the
initial read of a concurrent caller. The live caller does not launch another
resident; malformed live peers and refused launches do not gain that retry.

For native daemon qualification alone, dispatch
`installation-context-acceptance.yml` with `native_daemon_exit=true` on the
candidate ref. This selects only `native-daemon-exit-windows`, not broker,
Worktrees/Manager recovery or the default diagnostic matrix. It exercises both
native daemon test files in a disposable Windows profile: two cycles of signed,
positive, throwing and successful target statuses require application `finally`
cleanup before observing exit; refusal, original Job custody and pre-acknowledgement
cleanup controls remain active. Worker completion, outer subprocess and test
deadlines remain 15, 30 and 60 seconds respectively. The 15-minute job includes
tooling preparation; its canonical runner has a 600-second suite bound and
720-second aggregate bound. This is source qualification, not installed-host
reconstruction, whole-product acceptance or rollout.

## Native file-link prerequisites

`tools/test_dependency_uv_executable_native.py` qualifies both absolute and
named Windows UV commands through real guarded SDK/Python-owned installs.
Named controls use an owned child's fixture-only PATH; external `*-system`
qualification retains the target's actual PATH and verifies it is unchanged.

`tools/test_installer_dependency_policy.py` also exercises real Windows UV
configuration probes beyond the native subprocess cwd limit. Plain cwd,
relative/inline `--directory` and `UV_WORKING_DIR` retain the selected project's
stricter cutoff while UV starts from a short native cwd.

Repository-tool installer-sync and vendor-guard link controls use the shared
`native_file_symlink` fixture. Only Windows error 1314, raised by actual file-link
creation, produces an explicit missing-privilege skip; other failures propagate.
Supported POSIX and provisioned Windows hosts still execute the real linked-file
refusal assertions. No test enables Developer Mode or elevates the process.
`tools/test_symlink_capability.py` checks that classification independently.

## Client frontdoor native acceptance

The Fleet test helper gives **wheel construction only** a 60-second budget,
separate from its 30-second native-command and source-distribution
budgets. This covers cold setuptools import, canonical resource checks and file
copying, not a product/runtime or publication deadline. The local-Gateway
distribution proof installs the real wheel offline and runs independent
60-second SDK probes for both supported identity profiles and normal/UNKNOWN
recovery. This probe-only allowance includes cold cryptographic imports; the
shared native-command default remains 30 seconds and product/effect deadlines
are unchanged. Each probe uses a minimal pytest-free consumer with real TLS,
the SDK caller and native owner, not another test collector. On slower hosts,
select each matrix case separately under the 120-second qualification
envelope. A prepared qualifier with `pytest-timeout` may instead share the
session's immutable wheel across the module, enforcing 120 seconds per case
(including setup/teardown) inside the existing 600-second component envelope.
Every installed consumer remains a fresh process and target; this is artifact
reuse, not cached execution evidence. No fixture or source callback is readiness proof.

The preparation Gateway's real-Git functional fixture selects the existing
supported 30-second acquisition configuration for repeated source pulls.
Production defaults to 15 seconds and caps selection at 30. Separate
controls assert the default and a real authenticated deadline refusal;
this does not relax the source owner or Gateway's execution limits.

`fleet-manager/tests/test_client_frontdoor_native.py` separates the real
construction prerequisite, concurrent independent registration owners, and
platform command/exact-rerun behavior. The constructed client remains the real
offline installed distribution; no construction, health, publication or
registration primitive is mocked. Per-case 90-second, wheel-build 60-second,
registration-child 60-second and command 20-second bounds remain unchanged.
Each registration case uses actual owned command withdrawal at teardown so a
different test's directory cannot become its registration authority.

On slower native Windows hosts, run each native case as a separate
`PYTEST_ADDOPTS=-k` selection through
`tools/run-standalone-tests.py`, retaining its 120-second caller bound. Use
`construction_prerequisite`, `owners_serialize_and_reuse` and
`commands_and_exact_rerun` in separate invocations. Each registration/command
case still constructs its real prerequisite through the shared fixture; the
separate construction case asserts the prerequisite's own contract. An owner
selection may instead include `construction_prerequisite or owners_serialize_and_reuse`
so the first test owns construction's existing 90-second clock before the
independent-owner case's own 90-second clock begins. That pair still has the
same 120-second caller bound; it is not a function-only timeout exemption. A missed
stage deadline is still a failure, not a reason to omit health, skip an
assertion or extend the timeout. Ordinary ownership/fault cases remain in
`test_client_frontdoor.py`; the periodic standalone suite discovers both files.

`fleet-manager/tests/test_client_recovery_native.py` separately exercises the
real construction prerequisite, installed `recover-retained` CLI, actual
platform commands/exact repeat, and concurrent independent installed owners.
The CLI uses independently signed synthetic evidence valid at the real wall
clock; no health, constructor, publication, clock or registration primitive is
mocked. Existing build/constructor/owner/command/test/caller deadlines remain
unchanged. On slower native hosts, select `construction_prerequisite` together
with one of `cli_transition`, `commands_and_exact_rerun` or
`independent_installed_retained_recovery_owners` per bounded invocation.
`test_client_recovery.py` contains separate real-trust/physical-proof behavioral
and fault controls, using explicitly labelled unit health stubs rather than
claiming native proof. Split large behavioral selections into disjoint bounded
groups when needed; an earlier timeout remains a failure, not a waived gate.
The synthetic frontdoor, withdrawal and retained-recovery fixtures copy only
the verified launcher resources. Native interpreter import-policy installation
is a separate required constructor step, not an implicit probe in copy-only
fixture preparation.

`fleet-manager/tests/test_client_generations.py` exercises lineage initialization,
exact legacy-floor import, request/generation reservation, physical root intent,
no-replace publication and refusal/retry using real SQLite and native owners.
Reservation controls forbid artifact-content reads under central writer custody
and change current trust, expiry, target and stage retention at acquisition.
Committed competing requests still fence stale floor/request/generation input;
the private in-memory authentication scope cannot become reusable evidence.
Independent-process reservation races commit one current request; exact retry
cannot reclaim it. State-only selected-owner fixtures exercise conservative
retirement refusal, not successful publication. Tombstone commit interruptions,
malformed fences and delayed generation holders exercise durable refusal without
waiting for old generation custody or deleting runtime/configuration bytes.
`test_client_generation_construction.py` separately exercises actual installed
generation health and direct commands while predecessor custody remains held.
For bounded native selections, run the initialized-predecessor case on its own;
run `installed_predecessor_preparation` together with
`real_installed_replacement_starts_with_legacy_construction_and_retention_still_held`.
The explicit prerequisite owns its existing 90-second clock before replacement
startup's separate 90-second clock; retain the 120-second caller bound and
45-second constructor-child bound. Use the canonical environment admission,
isolated HOME and process containment for exact file/node-ID selections.
For forward retry/reinstall, select `forward_generation_preparation` with exactly
one of `same_generation_retry` or the `forward_reinstall` parameters `same`,
`higher` and `failed-health` per bounded invocation. Direct commands must leave
the completed fingerprint and file inventory unchanged without relying on
inherited bytecode-suppression flags. Failed installed diagnostics must preserve
the usable predecessor and newly admitted floor.
The same-release native case also retries the older constructed generation
without reclaiming current authority. Run `retired_generation_stays` separately
to prove a retained immutable runtime remains directly usable after its
non-destructive lifecycle tombstone, cannot reconstruct, and does not block
successor startup.
The factory-owned ordinary roots avoid adding a test function's name to every
native runtime path; this is not qualification of overlong Windows executable
or working-directory paths.
The separate `test_client_construction_native.py` selector
`native_launcher_uses_private_short_temporary_path` keeps an overlong candidate
home while exercising two actual retained-supervisor launches and native
temporary-file use. Only Windows temporary paths move to the ordinary private
runtime namespace; home isolation and existing deadlines are unchanged.

`test_client_generation_frontdoor.py` exercises actual installed default commands,
native copy proofs and durable selection independently from construction.
Run individual outcome selectors on Windows rather than accumulating several
runtime constructions under one caller clock. Controls include exact object
reuse, same/higher-release cutover, actual failed startup, before/after intent
and acknowledgement commits, pending health, canonical copy rollback proofs,
superseded acknowledgement, a bound default directory and foreign/same-byte
commands. Implicit publication still refuses corrupt predecessor proof without
blocking independent successor startup. Explicit incoming target observations
instead prove actual default selection with missing and malformed old copy
receipts, preserving the damaged evidence. Wrong installation/reservation
snapshots and changed physical targets, including same-byte replacements,
refuse before command effects. Original-observation retries also cover both
acknowledgement durability boundaries, native copy recovery over an existing
baseline and foreign same-byte replacements. Before-effect retries retain
fresh-target admission even when the interrupted attempt's proof is lost.
The installed register-console case repeats the original
`--replacement-targets` file rather than silently switching modes.
`test_client_generation_publication_native.py`
pauses an independent publisher after real default health: another process can
construct the successor while targets remain held; resumed acknowledgement
refuses without rollback.
`test_client_generation_withdrawal.py` uses real published commands and canonical
native removal for exact-attempt withdrawal, partial effects, before/after
selection commits, foreign objects, pending publication, same-generation
republishing and delayed predecessor cleanup.
Empty, partial and complete-but-unpublished writes to each withdrawal metadata
document leave commands untouched and permit exact retry. Separate final-name
collision controls preserve foreign metadata through native no-replace
publication. Its independent child pauses before native removal while a
successor reserves, constructs and runs directly,
then proves old cleanup cannot remove the successor's later publication.
`test_client_generation_removal.py` removes actually constructed runtimes
through the existing native remover, including exact retry, partial deletion,
retained-intent corruption, changed root/owner/input, canonical reference
protection, selected-generation refusal and withdrawal-before-deletion.
Pending predecessor commands, recovery-restored baselines and same-byte foreign
successor commands must refuse before runtime deletion. An independent child
pauses native old-runtime cleanup while the parent constructs, directly runs
and publishes a successor, then verifies that publication survives cleanup.
`test_client_generation_legacy.py` separates real legacy preparation from the
delayed effect, with registered/unregistered lanes. Its legacy process is
already inside the version-1 writer before the real copy producer starts; a
forward winner must remain unchanged when that producer resumes. Use exact
lane selections with their preparation prerequisite and keep the existing
90-second item, 120-second caller and 45-second constructor-child bounds.
These are shared-publication controls, not complete delayed cleanup fencing,
selected-command withdrawal, retirement/removal, installer or live-rollout proof.

`test_client_generation_cli.py` checks explicit generation arguments, read-only
reservation recovery, strict separation from version-1 state and legacy-origin
import. A real SQLite fixture with 514 synthetic retained reservations and
retirement records exercises actual CLI page serialization, complete traversal,
exact interrupted-request lookup outside the first page and command-target
inspection. It preserves all database bytes and existing JSON bounds; this is
state-inspection evidence, not 514 runtime installations.
An additional valid SQLite fixture grows beyond 4 MiB and retains exact
inspection, new reservation, exact retry and retirement access while a separate
oversized metadata file still refuses. Target-inspection cases exercise actual
nonordinary command objects plus injected native lease/read failures through
the CLI, requiring the normal refusal without a traceback. Native symlink
creation is explicitly platform-qualified or skipped, not credited implicitly.
Independent actual installed-console cases cover construction/retry,
default publication/retry, exact withdrawal/retry and exact runtime removal/
retry with current independently signed artifacts and real direct diagnostics.
Missing state is never initialized by construction; selected-runtime removal
refuses before retirement. These cases retain the 90-second item, 120-second
caller and 45-second child bounds; they do not prove full installer integration,
whole-installation retirement or live rollout.
Each native case uses short factory-owned and lineage root names to stay below
the native Win32 console-launcher path limit. The hosted Windows fixture's
longer temporary prefix otherwise made its console path 263 characters while
its interpreter remained within the limit. This is not arbitrary
long-executable-path qualification.

The manually selected `fleet-client-generations` portfolio in
`installation-context-full.yml` runs these consumers on native Linux and Windows
at the exact dispatch SHA. Admission and each of the four generation transitions
use separate canonical standalone-runner invocations with the existing
120-second caller, 90-second item and 45-second child bounds. The existing
strict six-consumer oracle checks the combined reports, refusing missing,
duplicate, skipped, errored or failed cases. Reports are retained per platform.
This optional portfolio does not expand required unit CI or qualify a complete
installer, fresh-OS scenario, controller or live adopter.
Positive roots and staging directories use the existing native private-state
creator rather than treating POSIX mode bits as Windows ownership evidence.
The installed consumers exercise explicit owner-private database and generation
metadata creation without weakening the existing ACL or hot-journal checks.
The Windows removal consumer also introduces an explicit public-read directory
grant, requires refusal with the runtime retained, then removes only that
fixture-introduced grant before exact removal/retry. Runtime directories use
the same canonical held-handle current-principal proof as runtime files plus
the existing strict private-grant allowlist; roots and metadata retain their
TokenUser-owner requirement.

The same optional native portfolio separately runs seven real installed
`test_actual_installed_console_managed_lifecycle` cases: exact install/retry,
same-release forward replacement with missing or malformed predecessor proofs,
selected higher-release update, exact uninstall/retry and same-byte foreign
command preservation, plus explicit legacy-origin import/install and exact retry
after predecessor disappearance. The legacy case also refuses a different
origin without altering the selected commands or lineage. Each remains a separate 120-second caller with the
unchanged 90-second item and 45-second child limits. Its distinct `--managed`
oracle requires exactly those seven non-skipped cases; it does not replace or
weaken the original six-consumer oracle. The owner console runs outside the
checkout, and incoming direct/default diagnostics use the actual constructed
wheel. The fixture retains user configuration and checks the monotonic floor.
These are private first-touch installation roots, not fresh operating systems.
`test_client_installation.py` separately covers durable original target
observations, actual producer-copy recovery, interrupted real default health
and refusal to recreate a missing incoming observation after publication.
Its managed legacy-install controls prove imported lower-release and
same-sequence content-conflict refusals before construction, with the old
database and imported floor preserved, plus changed-origin refusal.
Its heavy recovery cases should be selected separately under the same caller
bound. None of these packets qualifies release acquisition, automatic
maintenance, complete host setup, whole-lineage retirement or live adoption.

The agent-free `fleet-manager-client-admission` clean-room scenario includes
those four actual generation consumers alongside admission/revocation/reopen
and missing-resource refusal. Its strict six-case JUnit oracle rejects missing,
duplicate, unknown, skipped, errored or failed cases, including an old
admission-only report. `tools/test_fleet_client_admission_clean_room.py` tests
that oracle; an oracle unit pass or fresh SDK is not a fresh-OS scenario pass.

`libs/versioned-runtime/tests/test_receipt_header_publication.py` exercises
unpublished empty/partial/synced header attempts, abrupt producer death, a
concurrent foreign final-name winner, identity-checked append reopening and the
pre-effect callback boundary. `fleet-manager/tests/test_client_first_publication.py`
checks that initial failures leave no pending owner and that an interrupted
later header cannot poison a previously pending selector retry. Existing strict
receipt/parser and pending-withdrawal suites still cover malformed legacy proof;
the new constructor must not repair it.

## Unit-only promotion packet

The actor-free `test_health_composition.py` packet executes the existing domain
age projection, normal report/parser and SVG composition. Substituted storage
and service owners retain stale/unknown evidence, independent unavailable
inputs and unchanged old report selection. It starts no observer, writer or
service. The selected Machines native observation packet separately proves
actual loopback -> confirmed local slot -> normal report/chart, while the
configured measurement/sink packet proves valid telemetry remains usable when
an explicitly selected health store is unavailable. Neither qualifies a native
scheduled job, installed publisher or carrier.

The same packet admits the hourly-envelope validator, write-boundary component
preservation and explicit collection orchestration with substituted clock,
storage, admission and native providers. Current health and historical service
results keep separate absolute hours and one inherited collection budget.
The selected Machines `tests/native/test_health_collection_consumer.py` packet
separately exercises actual retained Dispatch events, private listener,
confirmed slot transactions and report/chart, plus competing native processes
and normal/crash run-lock release. Required unit CI starts none of those actors;
fixture-resolved installation admission is not deployed inventory or scheduling.

The same actor-free composition packet exercises policy-approved `health run`
preview and the shared in-memory transaction planner/writer using substituted
storage, admission and native owners. Approval drift, independent target facts
and late confirmed commits remain explicit. The native collection packet also
executes one approved invocation through a real fixture listener and retained
Dispatch export into both slots, then injects a second-target failure while
preserving the first confirmed commit and normal report/chart. It does not
enroll a job or qualify an installed runtime/carrier.
Interruption controls retain an unknown transaction before writer entry;
in-process cleanup controls recover the original retry owner and partial
result. Native controls additionally exercise the durable fence after CLI exit,
delayed second requests, prompt fixture cancellation and both payload entry
points' refusal to provision a missing one-shot runtime.
Final post-admission interruption retains already confirmed commits and any
outstanding native cleanup owner; pre-plan cancellation fabricates no result.
The native selected-runtime fixture copies the actual early CLI/export closure,
not the unrelated coordinator tree, keeping preparation outside the operation
budget and inside the unchanged test deadline.

The same actor-free packet renders inert Windows/Linux native job definitions
and refuses unsupported platforms or unsafe definition values; it never
contacts a scheduler. Agent Machines' selected `tests/test_health_jobs.py`
packet additionally exercises the normal preparation parser and unchanged
store/payload state. `tests/test_health_job_payload.py` creates fresh private
interpreter fixtures and drives the shipped PowerShell/Bash entries through
missing, preview/apply-only, capable and rolled-back declarations. Unsupported
providers must never execute. `tests/test_configuration_payload_readonly.py`
also covers missing-runtime preparation without bootstrap. Select these with
the managed runner's repeatable `--test-file` and `--allow-explicit-tiers`
options. Bash on Windows is a script compatibility check, not native Linux
scheduler qualification. Actual scheduler registration, two-cycle headlessness,
late wake, deadline/storage and cleanup qualification remain explicit opt-in
gates before enrollment; the preparation packet does not execute them.

The actor-free `test_hourly_contributions.py` packet executes the canonical
service contribution codec and collection policy with substituted native owners
and stage/outcome vocabularies. Exact
source/node admission covers owner/hour/unit/population refusals, delayed native
evidence, missing duration statistics and strict JSON/version compatibility,
plus task-authority preservation, independent namespaces, de-duplication and
unsettled native custody. Bounded source-selection and temporary-file snapshot
cases preserve active/current records under saturated history, reject partial
or oversized suffixes, and retain cleanup inside shared absolute deadlines.
Duration cases call the actual service span adapter and shared statistical
primitives, covering invalid spans, completion-hour boundaries and
successful-only p50/p95/maximum with mixed outcomes, not prebuilt summaries.
It starts no service or native actor. Dispatch's `tests/native/test_hourly_export.py`
separately exercises producer-retained events, selected fixture package metadata
and the normal Machines report/chart. Machines' selected
`tests/native/test_service_report_cleanup.py` retains actual deadline, inherited
pipe, output-limit, UTF-8 and native-settlement controls. Fixture installation
admission is not deployed inventory, installed adoption or activation evidence.

The project-routing controls distinguish the explicit `pr-watch diff` read
from an unknown similarly named command. They exercise only the existing
in-process routing predicate, with exact source/node admission; actual
provider/CLI subprocess qualification remains in the selected plugin packet.

The peer-environment packet reuses the canonical library assertions. Vault
route rebinding substitutes only its native suffix calculation; platform and
checksum refusals remain actor-free. These three units do not load a native
library or start a process. Actual native suffix/environment parity and retained
same-cell invocation stay in the opt-in `vault-activation` portfolio.

Five binding-projection controls in `test_related_acquisition.py` exercise the
actual synthesis body with logical paths and substituted canonical-registry
acquisition. They preserve metadata provenance, knowledge/legacy-worktree
classes and explicit uncertainty without importing the native registry owner
on unconfigured paths. These actor-free models do not qualify real filesystem,
registration, pairing or lifecycle effects; the opt-in native reader portfolio
retains those separate boundaries.

The runner explicitly preserves full parameter IDs, including on pytest 9.2,
so collection continues to match the exact admitted node inventory.

Packet input readers use bounded chunks while retaining the original byte cap
and overflow sentinel; a tiny file must not allocate the full evidence limit.
`test_hook_demand.py` exercises the original IPC handler with in-memory streams:
completed tool replies and capability-qualified send-only read demand preserve
the same monotonic idle grace. Generation, admission, malformed and expired
notifications cannot renew it or invoke policy. Actual sockets, the final
idle-check/drain interleaving and Windows shell delivery remain in the explicit
Worktrees consumer packet, not this actor-free inventory.
The same in-memory handler fixture covers optional `worktreeChanged` delivery:
current generation/admission accepts the wake, while stale, closed, legacy,
malformed and expired frames neither reply nor invoke tool policy. Associated
application-marker units supply a synthetic marker environment; dependency
evaluation must not discover the test host's identity.
The PowerShell SDK and Bridge resume fixtures compile immutable admitted source
once per test session, then execute that code in fresh per-case module globals.
They do not share module state, results, provider observations or qualification
receipts between tests. Source fingerprints, per-call production coverage,
effect checks and the whole-packet deadline remain independently enforced.
The workflow validator uses PyYAML's C-backed base loader when available and
otherwise the Python base loader. Both preserve scalar strings, including
`on`, booleans, numbers and dates; neither enables object construction.

The eight tool-profile units in `test_related_acquisition.py` use the existing
synthetic acquisition fixture and actual Worktrees repository builder, parser
and native merge. They cover absent/base/exact/null selection, malformed shapes
and exclusion from ordinary portable preferences. Only unrelated acquisition
and PR-deadline owners are substituted. Their existing closed source/import
cohort and production coverage targets are positively admitted; no Git, child,
workspace mutation, tool staging or native launch is represented by these units.
Those effectful counterparts remain in the bounded Worktrees development packet.

The units in `tools/promotion_unit_tests/test_automatic_update_policy.py`
exercise the actual shared automatic-policy function bodies without importing
the native execution owner. Their source AST, strict automatic/manual identity,
queued-policy predicate, optional legacy parser dispatch, independent-cell scope
and actual read-only Manager controls are source-bound in the same packet.
The early bootstrap control binds its explicit addressed root rather than an
ambient default and preserves the mandatory veto with an older policy reader.
Controller discovery is inert in these units; no worker, shell, installer or
desktop actor starts. The separate Manager
`test_installation_maintenance_control.py`,
`test_installation_automatic_updates.py` and production Configuration tests
exercise real TOML persistence, canonical switch-controller effects, payload
delivery, confirmation/cancellation and refresh in private fixtures. The existing
`test_launch_update_requests.py` matrix executes each available native shell
against a synthetic engine, retaining `--auto`, strict unsupported-flag refusal,
single submission and separate no-update behavior. Platform skips are not native
qualification, and the consoleless desktop observer remains a separate opt-in.
Queued launch controls also distinguish the maintenance veto from a software
hold during intentional `--no-update` reconciliation, while borrowed Manager
execution retains each request's selected override, including an empty default.
Real config-writer subprocess interleavings cover source/runtime-source edits
and stale maintenance confirmations under one bounded native lock.
`libs/bootstrap-killswitch/tests/test_mutation_process.py` checks actual
Bash/PowerShell/Python writer lock contention, release and recovery, plus exact
audit-byte conditional mutations. Available Linux PowerShell evidence is not
Windows PowerShell or Windows byte-lock qualification; run the same selected
native controller controls through each supported Windows host separately.
The controller and Manager packets also cover `missing/../hold.json` with an
absent intermediate directory, path-bound confirmation and linked-leaf refusal.
The actor-free automatic-policy controls bind a relative Manager root to the
retained caller cwd, refuse missing/relative cwd for automatic admission, and
keep explicit requests exempt. The durable queue counterpart changes policy
after enqueue and drains from a different directory before synthetic effects.
Borrowed Manager/engine controls retain the caller directory through the second
policy check, including absolute, relative and current-user-home roots, while
restoring the worker's original policy/hold selection afterward. Direct Manager
checks retain their own invocation directory.
The retained lifecycle deadline and guardian-pair units substitute their native
Windows path-owner/file-open boundaries, retaining actual cleanup/deadline and
byte-reader logic. Native ownership and file-sharing custody are not unit evidence.

`tools/promotion_unit_tests/test_komodo_repair_authorization.py` exercises the
repair phase controller with synthetic verified-identity records and the real
common admission owner. It counts one producer and six current resolutions per
completed phase, checks the unchanged individual permit ceilings and retires
only the retained exact handle. Final common completion follows the closed
custody phase and contributes two further current resolutions. It starts no actor and needs no cryptographic
or HTTP tooling. This is work-count evidence, not signature verification.
The separate Fleet Manager authorization tests use real machine proofs; its
opt-in `client_distribution_full` repair matrix retains actual subprocess,
HTTP, original journal, interruption and late-publication witnesses. Advancing
signature/admission/native clocks cover full status and terminal composition
over 30 seconds inside the original selected deadline. Actual installed and
cold/warm adopter qualification remain separate from these synthetic controls.

`tools/promotion_unit_tests/test_git_config_transaction.py` exercises the
canonical Machines Git-configuration declaration, consent digest, pre-effect
refusals, lock identity, rollback receipt and bounded Git adapter with synthetic
file/process observations. Its native file/lock/backup/rollback witnesses use
only the audit-scoped temporary filesystem and start no child actor. The
separate Machines `test_git_config_transaction.py` and
`test_resource_git_config.py` run real Git and the manifest/authority/restore/JSON
and parser boundaries in isolated homes, including exact rollback, stale consent,
competing ownership, backup/verification failure and read-only native Windows
controls. A link fixture skips only when the host cannot create it. These are
synthetic policy/file tests, not live profile application, authentication health,
shared updater consumption, two-machine qualification or deployment evidence.

`tools/promotion_unit_tests/test_gh_account_prerequisite.py` exercises the
canonical Machines account-prerequisite evaluator with synthetic CLI responses
and a substituted owned-process boundary. It checks explicit stored/environment
sources, healthy inactive accounts, malformed/ambiguous/unhealthy evidence,
safe error projection, `--no-refresh`, execution/output bounds and preservation
of unconfirmed native custody. The exact nodes, source and production coverage
target are admitted by `tools/promotion-unit-manifest.json`.
The separate Machines `test_resource_gh_account.py` covers actual manifest,
authority, resource, restore and JSON integration. Its native companion runs
the real CLI against an empty isolated configuration and contained synthetic
CLIs for argument fidelity, deadline and output limits. These development
witnesses require no live account or profile and do not expand required CI.
They are not sign-in, policy application, two-machine or installed-updater
qualification.

`tools/promotion_unit_tests/test_resume_cli_contract.py` exercises the real
Bridge resume/version declarations, target dispatch and HTTP client against
retained prior parser declarations and synthetic current/prior daemon responses.
Only external transport, discovery,
server/logging plumbing and unrelated entrypoint glue are substituted.
Exact-target cases cover parser refusal before discovery, session/singleton
collisions, ACP/CLI identity and full response preservation, hosting/protocol
fences, unconfirmed responses and no effectful replay. Positional routing and
plain version output retain their compatibility controls; executing-CLI
disclosure is independent of daemon availability. The manifest positively
admits these exact nodes, source closure and production coverage targets.
This is in-process contract evidence, not installed CLI, native conversation,
process-exit, transport availability or deployment qualification.

`tools/promotion_unit_tests/test_retained_acquisition_writer.py` exercises
the real retained writer owner with synthetic journal rows and substituted
writer selection/process responses. It checks original-prefix recovery,
incompatible serializer refusal, missing-binding non-recapture, exact workspace
scope, required lineage error propagation and pre-effect CLI refusal.
The executing-session controls also check capability-gated original-scope
arguments, locked receiving-incarnation refusal and scope reset after failure.
The additional journal control retains the original successor, native identity
and seed through synthetic database responses, with full-sync intent and
replacement refusal. Real stopped-session refresh and post-spawn handoff
recovery are covered by the separate acquisition/runtime packet.
The exact nodes, source and coverage target are positively admitted in the
unit-only packet. The separate Bridge acquisition/runtime and lineage controls
retain actual SQLite and finite source-process effects; their patched build
identity and synthetic ACP transport do not prove installed/native or closed
writer-cohort qualification. Required CI starts no new actor.
The opt-in HTTP-52 capture additionally binds every finite Worktrees fixture
launch to its committed entrypoint and full source closure. A reachable
ordinary-verb-compatible resident receives the legacy control but no required
registration, binding or succession. Those commands reject changed identity,
nonce, project and path. Capture instrumentation is restored explicitly in the
fixture after the real owner environment scrub; production routing is unchanged.
Creation controls in that same actor-free packet distinguish the original
workspace journal from acquisition attempts, retain strict resume and successor
seed custody, and requalify exact-session inspection without writer reselection.
The separate `test_creation_worktree_writer.py` packet covers SQLite schema
preparation, passive refusal, real HTTP negotiation, frontend rehydration,
concurrent replay and registration dormancy. Its finite source-process control
preserves the protected claim and creation nonce through registration, binding
and succession while the unqualified resident remains reachable. These probes
are enrolled in the opt-in HTTP-52 capture, not the required actor-free packet.

`tools/promotion_unit_tests/test_namespace_contract.py` joins the actual
Containers `namespace-list` CLI and `ContainerResolver.list_specs` output to
Bridge's `CliNamespaceResolver._enumerate_once` and `NamespaceAgentInfo`.
Synthetic inventory/configuration/lease/hold inputs and substituted thread and
process transport keep both production endpoints in the same no-actor unit
execution; unrelated command integrations are not exercised. The cases protect
field fidelity, ordered multi-agent and empty rosters, repository fallbacks,
ready/stopped/unknown/draining states, and visible refusal of malformed,
partially invalid or schema-drifted JSON and failed provider enumeration.
The actual CLI must emit no success JSON on failure; Bridge must neither cache
nor accept that result as an empty roster. These exact nodes and their source
closure are positively admitted by `tools/promotion-unit-manifest.json`.
This does not qualify native process transport, task/join ownership, Docker,
installed runtime compatibility or either provider's other operations.

Complete-source units in `test_complete_source.py`,
`test_complete_source_publication.py` and `test_source_objects.py` under
`tools/promotion_unit_tests/` use synthetic blobs, Git protocol responses and
release metadata. They prove full-tree reconstruction, immutable generation
bindings, fresh exact-subject admission and publication refusal; no Git
storage, network or child actor is used. Separate controller contract tests
inspect the proposal's prepare/attest/publish/PR ordering. They are not packet
inputs: independently owned Main workflows need not match a Dev proposal.

The separate `tools/test_complete_source_native.py` witness is opt-in:
set `COPILOT_EXTENSIONS_SOURCE_ARCHIVE_INTEGRATION=1` and run that file through
bounded development validation. It creates a fresh temporary Git repository
with export attributes, executable and symlink blobs, and actual CRLF PowerShell
and LF shell checkouts, then verifies both source profiles reconstruct the
original Git bytes without rewriting those checkouts. It is not enrolled in required CI
or the unit-only promotion packet, and does not prove live release publication
or Main activation.

For commit-bound qualification, use the pinned raw-object reader in
`tools/promotion_source_archive.py` and its complete-source archive verifier.
`.gitattributes` intentionally gives `.ps1` files CRLF checkout bytes and `.sh`
files LF, including in a WSL checkout; Git's normalized blobs are a different
representation. Compare a snapshot's complete inventory, modes and bytes with
the selected Git objects, not raw working-tree files. Do not normalize a
checkout, change Git settings or weaken byte verification to reconcile them.
Working-tree cleanliness is a separate admission question. An ordinary
`git archive` may also apply export attributes, so it is not a substitute for
the existing complete-source reader when every tracked blob is required.
Run Git-object acquisition on the source-owning OS. A consumer on another OS
receives the canonical archive and inventory and verifies them against the
pinned tree with `verify_tree_archive`; it need not open the producer's Git
directory over UNC or add a `safe.directory` exception. Byte verification does
not transfer execution authority: source preparation and a native test attempt
retain their separate resource owners and admission limits.

The three retained-input cardinality/remapping controls live in
`tools/promotion_unit_tests/test_installer_retention.py` and are shared with
the on-demand dependency-policy suite. They exercise the real retention
transaction with synthetic files inside the packet's owned temporary root;
only UV settings/executable selection and native privacy/path adapters are
substituted. They prove one comparison per overlapping file pair, remapping
work independent of metadata scalar count, and preservation of nested native,
URI and escaped paths. Native privacy, installer subprocesses and real package
construction remain separate development-suite controls.

The exact-profile wheel cases in
`tools/promotion_unit_tests/test_runtime_profile_wheels.py` inspect complete
synthetic ZIP/RECORD bytes in memory, including executable-looking payloads
that must never execute. They cover actual metadata/digest disagreement,
closed-set refusal, inclusive closure-wide byte budgets, malformed archives,
mutation during inspection and preservation of first-party generator checks.
These units qualify the repository-only byte verifier, not an approved feed,
real CPU/accelerator closure, installed constructor or production reuse receipt.

Portable preservation units substitute synthetic file reads/replaces for the
Windows native shared-handle adapter. The registry packet runs its original
FIFO refusal assertions with a synthetic FIFO stat result instead of creating
a platform-specific FIFO. Original library native tests remain unchanged and
outside this packet; neither native rename sharing nor FIFO creation is
qualified by these unit adapters. The effect observer and zero-skip rule remain
unchanged on Windows and POSIX.

`tools/test_runtime_profile_bootstrap.py` separately runs a real fresh caller
with `-I -S` and an import guard denying `packaging`. The builder's public
filename parser and first-party wheel inspector must both work without that
caller dependency. Runtime PEP 440 validation still uses the runtime tooling's
declared `packaging`; no package is installed by the parser. Run this native
child check under the bounded development supervisor, not the no-actor packet.

`tools/promotion_unit_tests/test_core_profile_candidate.py` admits the core
profile producer's exact-manifest, external-pin, metadata, root/extras,
reachability and target decisions with actual wheel bytes. Only retained
custody is substituted at its existing owner boundary. The separate
`tools/test_core_runtime_profile.py` suite exercises real retained files and
the producer CLI, including an independently launched child without ambient
repository import paths and no-clobber output. That child belongs outside
the no-actor packet; candidate assembly qualifies no feed or release approval.
The same controls cover additive index composition, preserving previous
profiles/source/artifacts, duplicate refusal and exact new manifest bindings.
The native-facing suite additionally runs `--artifact-index` through the
existing artifact-set validator; the emitted subject is still not approved.

`tools/test_core_native_delivery.py` checks the combined source projection,
retained wheel and material custody, refusal paths and candidate delivery.
Its native integration uses a disposable Git source, a selected native lock
and a local TLS acquisition feed, including deep Windows output paths; it
skips if Git or UV is unavailable. Separate controls verify that selected-lock
mode does not resolve, while an inspected held pool resolves only offline and
then uses the same guarded feed. They exercise the actual pool CLI, altered and
malformed pool refusal, retained local file identity, first-party exclusions
and an explicitly restricted redirect that must not be contacted.
`tools/test_core_native_delivery_index.py` also checks the optional combined
delivery/index handoff, exact release-state binding, input mutation during
final guarded verification and the actual CLI. It does not authenticate the
input index or approve the changed subject.
`tools/test_core_release_inputs.py` exercises the existing source-release
discovery and core attestation hand-off into the producer, including missing,
untrusted, oversized, changed and unsafe inputs. Its release service and
attestations are controlled fixtures; they do not approve a profile. Its
reconstruction cases compare the original core ZIP with retained delivery
wheels, exercise held-subject attestation, and reject mismatched or over-budget
inputs. The actor-free packet separately checks canonical member ordering and
early refusal of unselected reconstruction inputs.
`tools/test_core_release_delivery.py` connects those retained inputs to the
real native producer using a committed Git fixture. It checks source identity,
foreign Git selectors, bounded failures, material and release-input custody,
staged-output integrity, no-clobber publication and sanitized CLI timeouts.
The admitted actor-free `tools/promotion_unit_tests/test_core_release_delivery_contract.py`
separately covers committed release-state selection and publication ordering
with substituted Git, intake and producer effects, plus timeout sanitization.
`tools/test_core_native_pool_index.py` injects an unlisted wheel during the
real offline resolver call and verifies it cannot change the selection; it
also checks deep Windows pool inputs and bounded, private failure diagnostics.
`tools/test_parallel_artifact_hash.py` checks deterministic bounded parallel
source hashing and settlement after a worker failure; the core producer still
revalidates source changes before exposing a delivery.
`tools/test_core_guarded_feed.py` exercises the opt-in guarded-feed CLI and real
UV against local TLS fixtures: transitive selection, seasoning, withdrawn files,
invalid historical tag and native-compatible candidate parity with UV,
single-project histories above the offered-candidate cap, bounded multi-project
indexes and retained unsafe/tag-budget refusal,
configured-feed redirects, credential isolation, explicit redirect refusal,
late index and source rechecks, retained inputs,
one-download selected wheels, tampered retained bytes, shared budgets, sanitized
admission refusals and Windows owned-worker timeout cleanup. Actor-free
projection, candidate-witness, recheck, safe resolver-stop and isolated-worker
deadline controls (including pre-launch expiry) live in
`tools/promotion_unit_tests/test_guarded_core_contract.py`.
`tools/test_core_public_feed_native.py` compares real local-TLS producer outputs
across separate machine-local keyed identities and source containers, and checks
that credentials or a mismatched feed are refused before any feed request.
Actor-free controls in `tools/promotion_unit_tests/test_core_public_feed.py`
cover stable anonymous inputs and refusal of credentials, unsafe URLs and
changed source policy.
The fixture excludes inherited Git selectors, and Linux projection refuses
the active Manager wheel mapping rather than silently changing its origin.
The fixture feed and source do not qualify a production feed, release or
installation.

The reviewed [promotion burndown](efforts/active/dev-branch-release-pipeline/promotion-burndown.md)
supersedes the historical full-suite promotion requirement below. Source CI
and the independently governed Main control consume the unit-profile contract.
A local packet result alone does not qualify Main or prove candidate reuse.

Source CI resolves impact and, when selected, executes this packet within one
hosted job, parallel with source guards. Narrative-only PRs do not install test
tooling or run units. Pushes leave release-unit execution to promotion and
omit the unit job before runner allocation or checkout; the independent source
guards still run. Manual CI and unknown event types allocate the unit job and
select units conservatively. The required
aggregate checks both job results and the real unit step outcome, so a skipped
selected step or an absent/invalid selection fails rather than becoming a
success-shaped result.

The report-only marketplace-isolation inventory remains in push/manual source
CI. Omitting its duplicate PR execution removes no test or strict policy gate.

The checks job uses a separate conservative pinned-diff selector for the
tracking-write, headless-launch and asyncio-timeout AST scans. Known narrative
PRs and changes limited to direct Python files in `tools/promotion_unit_tests/`
plus `tools/promotion-unit-manifest.json` omit these scans: none of the three
scanners consumes those inputs. This does not omit the unit packet. Other
paths, nested/lookalike packet paths, production, scanner/selector/workflow
source, allowlists and mixed changes retain the complete scans. Empty evidence,
pushes, manual and unknown events also run them. Diff/encoding/admission errors
fail the checks job instead of emitting a skipped-success selection. All other
guards, including the ungated workflow-policy guard, remain unchanged.

Selected AST work runs through `tools/run-source-ast-guards.py`: three fixed
workers invoke the original full tracking-write, headless-launch and
asyncio-timeout scanners, each with a 300-second deadline. The runner joins
every scanner and emits its complete labelled output in fixed order; a failed
exit, missing interpreter or timeout fails the step. The six-minute step and
ten-minute job budgets remain bounded, with no additional runner job or cache.
Native subprocess regressions live in `tools/test_source_ast_guards.py`; they
prove real three-process overlap and joined failure diagnostics rather than
admitting process effects into the unit-only release packet.

The asyncio scanner still reads and parses every selected source file. Each
mode enumerates matching calls once in breadth-first order and builds a parent
map only for files containing those calls. This changes no file selection,
handler classification, diagnostic ordering or read/path-error behavior.
`tools/test_check_asyncio_timeout_handlers.py` covers both verification and
listing, including no-call files and invalid inputs.

`tools/run-promotion-unit-tests.py` executes only the exact nodes declared by
`tools/promotion-unit-manifest.json`, in one Python process with coverage.
The packet lives in `tools/promotion_unit_tests/`, independent of mixed
integration test modules and their conftests. The manifest binds source and
test bytes, the expected parameter nodes, production coverage targets and a
whole-packet budget. Any missing, duplicate, unexpected, failed or skipped node
or phase rejects the result. One pytest directory collector visits only admitted
files, once each in manifest order, without repeated sibling-directory scans.
The catalog correspondence controls also bind the canonical catalog declaration
and eleven templates as exact, hashed data inputs. These registered data paths
cannot be selected as test nodes or Python coverage targets; an unregistered
template or changed input still rejects admission.
An exact node whitelist deselects other cases before execution and restores manifest
order. Missing or duplicate admitted nodes reject collection before execution.
Nine execution-acquisition controls in `test_related_acquisition.py` exercise
the original selection and absence paths, fatal source/presence failures,
suppressed registry failures, self-restoring callbacks and argument-time
rebinding, including an opaque absent-registry fallback with a self-restoring
resolver. Synthetic source, resolver and registry boundaries retain probe
order and call counts without actor effects. Full reports keep their opaque
execution-owner frontier; these controls do not qualify machine-registry
acquisition completeness, reuse or installed behavior.
Eight machine-registry controls in the same packet borrow parser, merge,
normalization and config-source boundaries. They check original acquisition
order, alias filtering, type-only fatal/suppressed failures and selected callback
identity through self-restoration or argument-time rebinding. These actor-free
controls do not qualify delegated document/config-source frontiers or reuse.
The plugin's `test_machines_yaml_location.py` separately exercises the real
file/parser/merge/report path and empty/overlay cases; execution-identity
fixtures retain an explicit registered knowledge-path boundary.
Fourteen original config-source controls use the real selector with synthetic
Git, machine-root, state-root and file boundaries. They preserve registry versus
ordinary precedence, default-root fallback, fatal exception types and negative
native-probe uncertainty without calling the separate diagnostic reader.
Same-globals, nested-request and self-restoring replacements retain uncertainty.
The registry consumer retains its selected source callback across argument-time
rebinding.
Two real-file registry consumer cases separately prove one selected document
read and one state-root call, including a self-restoring source callback; these
are contained plugin tests, not actor-free required CI. Deeper child frontiers,
stable witnesses and memo consumers remain unqualified.
The runner selects pytest's `parametrize_long_str_id_strategy=legacy` to
preserve the manifest's full-value parameter IDs across pytest 9.1 and 9.2.
This keeps exact node admission; it does not truncate, rename or omit long
parameter cases when the framework's default changes.
Admitted Python sources are precompiled once into the private packet directory
using Python's hash-checked bytecode format. Imports still validate source bytes;
per-case modules and globals remain fresh, and source verification before and
after execution is unchanged. Bytecode preparation and cleanup share the same
whole-packet budget.
Its actor-free PowerShell SDK controls compare Windows physical-path spellings
through a synthetic resolver boundary, including DOS/UNC aliases and unrelated
roots. Native path reads and concurrent first publication remain separate
integration controls.
`test_materialized_local_sources.py` supplies bounded synthetic closure controls
for missing/editable/escaping local sources, literal conditional paths, standalone
manifests, and root/nested requirement declarations. Real Git promotion and
native symlink/aliased-parent controls remain in the existing integration suites.
The runner rejects process, network, native-loader,
Git-storage, thread and sleep effects, including attempted effects caught by a
test. Test writes are confined to synthetic temporary storage.
Packet JSON rejects nonfinite numbers during decoding, including nested values
and exponent overflow, without serializing another evidence-sized buffer.
Local packet and qualification reads use at most one-MiB input chunks instead
of reserving their full file allowance for every small source read. The existing
file-boundary regression retains full-size archive/readback coverage and checks
small-file allocations and growth across the unchanged byte limit.
The retained client-resource units substitute fixture-confined artifact handles
and directory identities at their native boundaries. Resource framing, hashing,
path checks and retention remain real;
these units do not qualify Windows read-sharing fences.
Guardian cleanup checks its operation-owned module namespaces while preserving
pre-existing modules; legitimate lazy standard-library imports are not treated
as leaked private modules.
The existing local-launch boundary fixture loads the core-owned native
value-option table and Worktrees re-export through restored synthetic package
namespaces. Both source files are explicitly hash-bound; moving the table does
not add unit nodes, coverage targets, application imports or actor permissions.

`tools/promotion_unit_tests/test_cell_installer.py` admits the original thirteen
actor-free Machines release-payload and watchdog controls. The owned process
runner and fingerprint thread executor are substituted at their actor boundaries;
canonical file selection, framing and hashing still execute. It excludes the original module's real Linux
descendant-retirement test. The separate snapshot-provenance suite exercises
actual POSIX payload replacement during a held provisioning lock, both adapters'
copy-before-publication checks, and a native PowerShell cell-lock deadline on
Linux. These process tests remain outside promotion units and do not establish
Windows Job or desktop qualification.

Release-helper qualification additionally resolves the canonical repository
import closure from controller-discovered commands and registered executable
build inputs. Candidate verification includes both the preserved Main control
and its development recipe. Every release-tool decision module must be enrolled
and have positive test-call hits inside executable decision-function bodies;
hash binding, module initialization or import-only coverage cannot substitute.
The native C source
is an explicitly admitted data input, including source-bound materialized
copies, never a selected unit or Python coverage target.
`test_release_helper_contract.py`, `test_release_helper_decisions.py` and
`test_release_build_admission.py` under `tools/promotion_unit_tests/` exercise
the admission, reuse, materialization, resource and native verification rules.
Compiler, Git transport and setuptools superclass actors are substituted only
at their boundaries. These tests do not qualify real compiler/native execution.

`tools/test_release_controller_contract.py` uses the recorded development
proposal for source-contract tests, even inside generated Main with an
independently preserved controller. Its explicit-path tests never substitute
that source recipe for the supplied Main control. It checks
immutable Main/bootstrap and admitted-source/helper selection, the existing
signer qualification dependency, required CLI inputs and complete-source
producer/attestor/publisher ordering. The PR-time compatibility guard reports
live Main separately from prepared compatibility; a source-only pass does not
activate independently owned Main controls. Warning-only migration applies
only to the explicit legacy pinned-helper controller. An already source-coherent
Main that lacks a required input fails before a source merge.
Invocation regressions isolate repeated calls and exclude inputs mentioned only
by comments or unrelated commands; the current indirect promoter is tested with
its own base argument array, not the composition step's arguments.
The command-line compatibility regression also supplies a helper used only by
Main, proving that active Main is checked against its own implementation
inventory. File-loader regressions retain imported-alias provenance through
lexical scopes and shadowing; a renamed dynamic import is not qualification.
File-loaded decision bodies outside `tools/` also require positive call
coverage, including bodies visited before their dynamic edge is discovered.
The native build `exec` exception admits only its exact source-bound load site
and declared input bindings; an additional executable load is not exempt.
Captured helper calls are refused rather than silently borrowing a later
invocation's arguments. Literal helper-path assignments and the supported
runpy heredoc trampoline remain distinct supported forms.

Use the bounded test supervisor around:

```text
python tools/run-promotion-unit-tests.py --source-sha <full-source-commit> --output-dir <new-evidence-directory>
```

For explicitly selected native Windows qualification, add `--windows-native`
and give the bounded supervisor an eleven-minute outer deadline. This selects
the distinct `copilot-extensions.windows-native-units/v1` profile with a
ten-minute whole-packet budget, including preparation, coverage and verification.
It preserves the exact development inventory, coverage and effect restrictions.
It refuses non-Windows and composed-candidate execution. The ordinary command,
required PR CI and promotion remain limited to five minutes; their packet
consumers reject the native profile. Portable native-packet inspection requires
an explicit `verify_packet(..., allow_windows_native=True)` opt-in.

The exact-node packet uses pytest 9.1.1 in both PR and promotion CI so an
unreviewed framework release cannot silently change admitted collection IDs.
Upgrade that framework only with collection/packet qualification; never relax
the inventory check to accept whichever nodes a new version happens to collect.

The command requires the supported `pytest==9.1.1`, `coverage>=7.7`, `packaging` and
`PyYAML` test dependencies, matching the source CI/promotion jobs.
On Windows, pytest's existing Colorama console adapter is initialized before
unit observation starts. Unit-side native-loader attempts remain rejected;
`tools/test_promotion_unit_windows_io.py` exercises that distinction in a fresh
native process outside the admitted unit packet.
Actor-free POSIX watcher controls supply their synthetic signal constant along
with their mocked OS calls, so collection/execution does not require the host
platform to expose POSIX-only signal constants. This does not qualify native
POSIX process retirement on Windows.

`tools/test_promotion_version_allocation.py` exercises actual Git composition
from landed Main: unlanded aliases do not change versions, dependency/checkout
changes retain distinct source content, exact retries preserve the candidate,
and a newly landed Main version advances the next changed plugin normally.
Its Chat cases also compose first-release and Main-seeded standalone versions
through the real promoter, retain every enrolled Chat Python input byte, and
prove that an actual candidate code mutation still fails the unchanged
equivalence verifier. Source-removed Chat wheel tests separately exercise both
the source version and a different promoted metadata version on native hosts.
It also proves a malformed unrelated plugin-version alias cannot block source
composition; current-source tag and artifact validation remain independent.
Run this existing development cohort under the bounded supervisor, not
unit-only promotion. The retired reservation-selector units are not admitted
after removal of that selector; all other unit identities and bounds remain.
Open-flag cases use semantic node IDs rather than platform-dependent constant
values; the descriptor mock preserves an opaque extra bit without requiring a
POSIX-only flag on Windows.
Their interpreter environment must live outside the checkout: dependency
sources inside a repository-local venv are correctly treated as unadmitted
local imports. The runner scopes pytest collection to the declared source root
as well as selecting exact nodes; unrelated ancestor directories are not
collection inputs, including on mounted filesystems.
It emits a profile-bound `unit-proof.json` and contextual `coverage.json` from
that same execution. It neither starts general plugin suites nor widens
selection on unknown inputs. Enrollment changes require review of the exact
test/import/fixture closure and refreshed declared hashes; tier labels and
test names alone do not establish eligibility. Real-Git, installer, native,
multi-process and end-to-end cases remain source-preserved for periodic
containerized validation, not fallback promotion tests.

Coverage preserves file/line/context attribution directly from coverage.py.
Call contexts start at the actual Python test-function frame and end when it
returns; observer/reporting bookkeeping cannot keep a completed call active.
Redundant per-function/class expansions are intentionally omitted, not unit
outcomes or line evidence. Candidate packets also omit the redundant stored
development projection: qualification reconstructs it from the complete raw
hits and verified byte-bound mapping. The qualification cases cover both this
form and legacy expanded packets, including refusal of missing/forged mappings,
unknown contexts and altered stored projections. Packet verification retains
the 192-MiB per-file evidence bound and requires every admitted unit to
contribute production call coverage.
The runner applies `promotion_unit_packet.py` to its actual three-file output
before returning success. An in-process adversarial collector fixture proves
that a passing assertion without production calls is refused by both paths;
synthetic packet fixtures alone are not collector acceptance.

The explicit `source-units-v2` candidate receipt reuses this execution only
after immutable Dev/candidate byte equivalence and independent provider
run/attempt/job/upload/archive checks. Main's metadata-only CI supplies no test
proof. Historical `legacy-ci-v1` verification remains separate and is never an
implicit fallback for missing unit evidence.

`tools/promotion_unit_tests/test_legacy_version_evidence.py` independently
protects that historical raw-source boundary: stamped version-only changes,
code or newline drift, relabeled hashes and post-test source rewrites remain
refusals. Identical source bytes remain reusable. These unit controls do not
claim that raw-Dev execution qualifies a transformed candidate; candidate-first
execution and attributable coverage retain their separately owned implementation.

For the composed-candidate controller proposal, invoke the runner from the
imported frozen candidate, adding `--development-root <immutable-Dev-checkout>`
and `--candidate-tree <full-frozen-tree>`. These arguments are a pair. The
runner validates the original Dev inventory, derives the candidate-specific
manifest without adding test nodes, and observes the composed import closure.
Declared uv vendor copies are admitted only for already-admitted origins and
must match their composed canonical bytes.

Candidate packets retain the same three filenames but use
`copilot-extensions.promotion-candidate-units/v1` and require explicit
`candidate-units-v3` qualification. `coverage.json` preserves raw candidate
`files` and adds `development_mapping` plus `development_projection`. The
packet verifier recomputes both from authoritative Dev and candidate bytes;
self-declared hashes or path relabeling do not qualify them. Version-stamped
statements and unsupported/generated regions remain explicitly unattributed.
The projection retains per-copy hits before combining mapped Dev coordinates
and never claims branch/function coverage or raw-Dev execution.

Focused regression files are `test_candidate_units.py`,
`test_frozen_candidate.py`, `test_development_map.py` and the v3 cases in
`test_qualification.py` under `tools/promotion_unit_tests`. They use synthetic
bytes and mocked Git/provider boundaries. `test_candidate_workflow.py` separately
checks the real controller proposal's dependencies and explicit profiles; it is
not a second runtime test gate. Actual composition/transport exercises belong
to bounded development validation, not the required in-process unit packet.

Prepared-controller rehearsals in `tools/test_promotion_merge_wait.py` and
`tools/test_release_publication_boundary.py` select the declared v2 or v3 unit
profile and dependency graph explicitly. Stage the controller together with its
matching `ci.yml` and admission workflow when testing a historical snapshot.
The existing bounded process-tree supervisor contains those local rehearsals;
they do not become additional promotion or required-PR test dependencies.

The coupled source-checkout, artifact-validator and readiness-clock regressions
also follow the frozen path. Git-based Windows development fixtures need a short
private scratch root and explicit short pytest base directory; deeply nesting
their synthetic Git stores below a worktree can exceed Git's path limit.

Source PR CI has a separate exact-input reuse boundary in
`tools/ci_pr_unit_reuse.py`. It binds the current source/merge/base, workflow,
manifest/source closure, hosted image, Python identity, actual installed
distribution file bytes and sealed child environment before looking up prior
proof. A fresh PR child gets only the declared noncredential environment plus
fixed Python isolation settings; GitHub event IDs, credentials and unrelated
ambient variables are not unit inputs. Toolchain binding is bounded, and the
read-only provider search has an eight-second total budget.

An eligible prior run must have a successful exact-attempt unit job and fresh
upload, the matching context-derived artifact identity, immutable archive
bytes and the original canonical three-file packet. Every phase, source hash,
effect refusal and actual production call-coverage requirement still applies.
The aggregate records `executed` versus `reused` with the original run/attempt
and packet digest; a reused packet is not uploaded again as a fresh producer.
Producing-run/attempt artifact names avoid retry collisions. The aggregate
preserves GitHub's successful-unit-job retention on failed-job-only retries
of the same immutable run, without accepting future-attempt evidence.
Unknown or incompatible evidence retains fresh execution. The provider
inventory includes failures, pending runs and retry revisions in a complete
bounded qualification epoch. A later retry of a lower-ID run changes that
epoch rather than hiding behind ID ordering; incomplete history cannot reuse.
Reruns that invoke the helper always execute fresh units, and PR checkout
validation remains mandatory when toolchain binding is unavailable. The pure synthetic
tests in `tools/promotion_unit_tests/test_ci_pr_unit_reuse.py` exercise provider,
attempt, archive, coverage, dependency-byte and environment refusals without
network, Git or child actors. Real CLI/provider execution is complementary
integration evidence, not an admitted unit effect. This does not alter the
release packet's format or independent execution for every Main release.

The first runtime-regression cohort in
`tools/promotion_unit_tests/test_runtime_regressions.py` exercises actual
project-routing, mux-result, anchor-guard and parent/sub-agent turn decisions.
Its closed production imports and relevant production calls are admitted
explicitly in the same manifest; it is not a generic plugin-suite invocation.
Synthetic repository-state evidence does not create or access Git storage.
Real front-door, missing-socket, installed-hook, SDK-forwarding and JavaScript
delivery counterparts retain their existing periodic ownership. No Python
classifier assertion is treated as proof of a native or shell boundary.

`tools/promotion_unit_tests/test_peer_environment.py` re-exports all six exact
functions from `libs/peer-launch/tests/test_peer_environment.py`. The original
module already loads the canonical flat `libs/peer-launch/peer_launch.py`;
the adapter does not preload production, rewrite imports or copy assertions.
Replacing only the original file prefix maps each unit to its packet identity.
All three files are hash-bound, and each unit must contribute actual
`peer_environment` call coverage under the unchanged observer and budget.
The eleven separate packaging tests remain unqualified; this cohort does not
prove native peer launch, vendor synchronization or the complete library census.

The shared lazy-dispatch cohort re-exports the three original classes from
`libs/lazy-cli-dispatch/tests/test_dispatch.py` through
`tools/promotion_unit_tests/test_lazy_dispatch.py`. Its fourteen class/method
suffixes map one-to-one to the original identities; no assertion body is copied
or moved. The adapter loads the canonical library source and original test
module directly, without its path-only conftest or an installed library.
All three files are hash-bound in the same manifest and every aliased case must
contribute actual library call coverage under the unchanged effect observer.
This bounded mapping does not establish the unfinished full-library census.

`tools/promotion_unit_tests/test_fleet_contracts.py` re-exports the exact
functions and parameterization from both original `libs/fleet-contracts/tests/`
modules. Replacing either original module's file prefix with the adapter path
maps each case to its release identity; their disjoint function names make the
mapping injective, and a duplicate name fails collection. The original tests,
canonical package initializer and both production modules remain hash-bound.
The path-only conftest is not executed and no assertion body, import, effect
rule or collection-root restriction is rewritten. Every admitted case must
contribute actual library call coverage under the same observer and budget.
This qualifies that bounded library cohort, not the whole historical census.

`tools/promotion_unit_tests/test_pr_target_probes.py` re-exports the nine
original in-process controls from Worktrees' `test_pr_target_probes.py`.
Their source, adapter and canonical import closure are hash-bound. Each case
must contribute actual `pr_publish` call coverage under the unchanged effect
observer and unit budget. The controls preserve fresh destination authority
and lock coverage while rejecting redundant target enumeration and nested
lock resolution. Git and bounded-process modules load native spawn helpers
only in their actual process callers, not when importing target policy.
Real-Git missing-remote, repoint and lock-count counterparts
in `test_pr_remote_evidence.py` remain outside the unit packet. This enrollment
does not establish native latency or complete Windows qualification.

Credential replay and native identity controls in Worktrees'
`test_update_auth.py` and `test_update_auth_probe.py` remain native tests,
outside that unit enrollment. Their isolated helper resolves the candidate's
explicit immutable module roots, not another editable checkout or site hooks.
The rendered Git helper retains `-I -S -B -X utf8`, the selected interpreter
and the five-second deadline. A timed-out owned child reports no successful status,
even if its recorded exit code is zero. Import-closure controls keep installer
cleanup and command-rendering dependencies out of the helper's startup path;
native process, routing and cleanup controls still qualify the actual behavior.
The cold policy-import control uses a 45-second child watchdog and a 60-second
item guard for native filesystem/import contention. This is a functional
import-boundary check, not latency qualification; authentication deadlines,
two-worker execution and the 240/900-second packet limits remain unchanged.
Registered-only find and strict-list first-touch calls use independent cases,
preserving each real CLI launch rather than accumulating two child deadlines
in one item. These calls and native strict-inventory ancestor controls use the
same 45-second child watchdog and 60-second item guard after native cold starts
exceeded twenty seconds. These functional allowances do not qualify startup
latency or change the original performance-packet limits.
The manual `Installation context full` workflow's `worktrees-git-probes`
portfolio runs the complete twelve-file Git/credential/observation packet on hosted Linux and
Windows at the exact dispatch SHA through the canonical contained runner.
It retains two actual workers, 240 seconds per group and 900 seconds including
preparation and cleanup. It is opt-in source qualification, not required PR CI,
installed rollout or completion of the broader publication-performance effort.

`tools/promotion_unit_tests/test_worktree_identity.py` re-exports the eight
original codec test functions and their unchanged parameterization from
`libs/worktree-identity/tests/test_worktree_identity.py`. Replacing only the
original module prefix maps its 72 cases injectively to release identities.
The original tests, canonical initializer and adapter are hash-bound; every
case must contribute actual codec call coverage under the unchanged observer,
root and budget. No original assertion body or production behavior is copied
or modified. This bounded mapping does not complete the historical unit census.

`tools/promotion_unit_tests/test_config_migrate.py` re-exports the original
config-migration functions and unchanged parameterization from
`libs/config-migrate/tests/test_config_migrate.py`. Replacing only the file
prefix maps all 29 original cases to their release identities. Its synthetic
temporary-file cases retain the actual atomic rewrite, backup, interruption
and idempotency assertions. The original tests and all four canonical
production modules are hash-bound, without executing their path-only conftest
or changing the observer, collection root or packet budget. Each case must
contribute real production call coverage. This is one bounded library mapping,
not complete historical-census or native-platform qualification.

`tools/promotion_unit_tests/test_dropin_registry.py` re-exports all nineteen
original functions from `libs/dropin-registry/tests/test_dropin_registry.py`.
Replacing only the original file prefix maps their identities to release nodes.
The original test and all four canonical package modules remain hash-bound;
every case must contribute actual production call coverage under the unchanged
observer, root and budget. Temporary-file, link and FIFO assertions execute on
the Linux release host without skips. The original platform availability rules
remain intact: a missing capability cannot become positive packet evidence.
This bounded mapping does not qualify the complete historical or Windows census.

`tools/promotion_unit_tests/test_plugin_resolve.py` re-exports the 29 original
local-resolution functions and their unchanged parameterization from
`libs/plugin-resolve/tests/test_plugin_resolve.py`, mapping 33 cases by replacing
only the file prefix. The original namespace-import regression starts a real
subprocess and remains outside this cohort, alongside the separate preparation
and execution suites. The original module and all five imported package modules
are hash-bound; every selected case must contribute actual production call
coverage under the unchanged observer, collection root and budget. No original
body or production behavior is copied or modified. Linux qualification includes
the original symlink-escape assertion. This is a bounded mapping, not a completed
historical or native-platform census.

`tools/promotion_unit_tests/test_docs_consistency.py` re-exports all 29 original
functions from `tools/test_check_docs_consistency.py`, replacing only the file
prefix for release identities. The original test and production guard remain
hash-bound; every case must reach an actual production call context under the
unchanged observer, root and budget. Paths, exact casing, headings, reference/
HTML links, file-versus-directory and real symlink-escape assertions execute
against synthetic temporary trees. No Markdown parser, count regex, original
test body or platform rule changes. This Linux mapping does not qualify the
whole historical census, native Windows or the separate metadata/Git portfolio.

`tools/promotion_unit_tests/test_activation_preservation.py` re-exports all
original functions and unchanged parameterization from
`libs/plugin-activation/tests/test_preservation.py`, mapping its 29 cases by
replacing only the file prefix. The adapter loads the canonical activation,
single-instance lease, drop-in registry and resolution packages directly,
without running path-only conftests. The exact imported source closure and
original test remain hash-bound; each case must contribute actual production
call coverage under the unchanged observer, root and budget. Synthetic original
snapshots, refused transactions, restoration, injected contention and real
symlink assertions execute on Linux without changing production or test bodies.
The same adapter additionally re-exports only the six explicit
`test_project_identity_memo_unit_*` functions from `test_plugin_activation.py`
(fifteen cases, including parameterization). They call the canonical registered-root resolver
with synthetic registry/input evidence and a substituted Git boundary, proving
zero Git probes for an unchanged memo, fresh work on a miss/input change, and no
positive publication for failed or uncertain probes. A synthetic source-file
fixture checks file/directory/ancestor A-to-B-to-A write-metadata invalidation and
legacy-origin refusal without creating Git storage or querying native ownership.
Native write-version change/refusal and handle cleanup use substituted API calls,
not the actual Windows kernel. The original source and
new import closure are hash-bound and the root resolver is a production coverage
target. Real Git, ownership queries, cross-process persistence and CLI composition
remain in the separately contained development portfolio; they are not admitted
unit effects. This qualifies no command/descendant process settlement, native
Windows or complete historical census.

The activation package's remote-identity compatibility export imports the
canonical pure-stdlib parser from `plugin_resolve.remote_identity`; both source
files are hash-bound in that existing import closure. This adds no unit nodes,
coverage targets, effects or budget, and does not enroll SSH dispatch tests.

`tools/promotion_unit_tests/test_remote_login_shell.py` re-exports five original
functions and their unchanged parameterization from
`libs/remote-login-shell/tests/test_remote_login_shell.py`. Replacing only the
file prefix maps thirteen callable cases to release identities. The original
test, canonical production module and adapter are hash-bound; each admitted
case must contribute actual production call coverage under the unchanged
observer, root and budget. No shell process is launched. The fourteenth original
case, `test_posix_login_shells_vocabulary`, asserts only a constant and has no
production call context. It remains an explicit unmapped policy residual, not
deleted coverage, a relaxed admission rule or a completed historical census.

## Tracking-write source guard

The source tracking-write guard still parses every sibling-plugin Python file,
including unchanged files, and fails closed on read, encoding and syntax errors.
It reuses one per-file breadth-first AST enumeration and assignment list across
its existing alias fixed-point passes. This is traversal reuse, not a changed-path
filter or a persistent cache; the denylist, reflection checks and diagnostic
ordering remain unchanged. The focused regression entrypoint is
`python -m pytest -q tools/test_check_no_sibling_tracking_writes.py`, run through
the bounded test supervisor.

## Copilot Code Review setup staging

The dedicated `copilot-code-review.yml` setup stays on literal `ubuntu-latest`
until an explicit isolated-capacity activation. Its only job is
`copilot-setup-steps`, with no setup token permissions, repository checkout or
dependency install. Copilot performs its checkout after setup. Manual dispatch
can exercise the setup once the definition is on the default branch; it is not
proof that an official agentic review completed or that an ARC worker is ready.
The five-minute timeout belongs only to the setup command step, not the job.
No job-level timeout overrides the official review's execution budget.

`tools/check-trusted-ci.py` and `tools/test_check_trusted_ci.py` retain the
existing validator authorization and global custom-route restrictions. The
dedicated review contract accepts only the exact minimal setup with literal
`ubuntu-latest` or `copilot-extensions-review`; a different file, job, label,
permission, trigger, extra step or job-timeout override is rejected. The setup
and prepared-control fixture modules keep the cases independent of production
expectation constants, and the existing required guard test entry point runs
them without adding a CI job.

Prepared-control fixtures exercise the real inline admission body and unchanged
preservation helper with old and four-workflow allowlists, including rejected
candidate self-authorization, foreign sources, missing/unsafe blobs, noncanonical
metadata and unrelated paths. They do not activate a Main policy. Before any
Main bootstrap, separately run the exact old Main guard/tests with only the
proposed workflow overlay; passing Dev tests is not that compatibility proof.
A future ARC activation additionally needs the compatible guard to reach the
default branch through qualified delivery, plus operator-owned network and
ephemeral-worker qualification.
Do not use a readiness expression or a hosted-label alias to select private
capacity. Local pytest commands remain wrapped by the bounded test supervisor.

## Trusted metadata event partition

The focused metadata entrypoint covers actual source routing for
Dev synchronize, closed/retarget-away, body/label edits and unrelated bases:

```text
python -m pytest -q tools/test_required_source_attribution_workflow.py
node --test .github/actions/required-pr-attribution.test.cjs tools/test_copilot_review_gate.cjs
```

The metadata wiring test checks the current unit-only CI profile through the
canonical trusted-CI validator, including its ungated policy check, admitted
unit execution and required aggregate. It does not require the retired
dispatched-snapshot or exhaustive metadata-test steps to return to source CI.
The Python/Git and JavaScript metadata suites above remain outside the admitted
release-unit packet; their periodic enrollment is still an open portfolio
obligation, not evidence supplied by a passing required PR check.

The 72 event/base cases, job-boundary checks and real-Git admission shapes reuse
the existing bounded fixtures, with no extra CI job. Source declarations come
from the checkout under test; the separate admission body comes from immutable
`HEAD` or an explicitly selected `METADATA_PREFLIGHT_SHA`. A selected available
full Main commit qualifies that control's admission shape, not live routing or
promotion. See [required source metadata](docs/pipelines.md#required-dev-source-metadata)
for trusted-default activation and measurement boundaries.

## Bridge protocol validation and startup performance

Current retained-inbox evidence captures re-read their complete committed source
closure inside each selected probe. Prewarm the isolated dependency environment
before invoking a capture from an independent interpreter:

```powershell
python tools\run-plugin-tests.py agent-bridge --prepare-only
python tools\capture_bridge_http51.py --source-commit <committed-source-sha> --write
```

The Windows capture portfolio permits 120 seconds per test and 600 seconds for
the owned runner process, allowing full source-proof I/O before the ASGI checks.
Other platforms retain their existing runner defaults and 240-second process
bound. Source, interpreter and observation checks remain mandatory; these bounds
do not permit synthesizing observations or rewriting historical authority.

Protocol correctness and startup speed have separate on-demand flows. Run the
native transfer and import-boundary contracts normally:

```powershell
python tools\run-plugin-tests.py agent-bridge --test-file tests\test_runtime_transfer.py --test-file tests\test_startup_imports.py
```

The native fixture permits 15 seconds for cold bootstrap, before its existing
shared post-connect protocol deadlines begin. That module has a 60-second item
watchdog; the 300-second group limit is unchanged. The child still shares its
original ten-second protocol deadline, and the deliberate 100 ms refusal test
still requires that deadline to expire. Import/export correctness has a separate
15-second child watchdog, not a five-second speed assertion. A bootstrap or
cleanup timeout fails; the fixture closes its listener and reaps its owned
process rather than leaving cleanup to a later test.
The runner gives the native runtime/transfer family and both startup files
independent sequential groups. This retains every selected test and each
unchanged group watchdog, avoids cross-module fixture/import state in the
ordinary native flow, and preserves complete failure diagnostics instead of
losing them when a mixed-process batch exhausts its aggregate deadline.
Explicit filtered collection keeps the runner's existing selection semantics.

The performance flow starts five fresh interpreters through the real
`python -m agent_bridge start` CLI, with fresh empty databases, isolated config,
no peers, a disabled credential relay and dynamically reserved loopback ports.
It verifies the published process identity and observes real `/health`
responses through full HTTP readiness. It does not import pytest fixtures in
the measured child or replace readiness with an import-only proxy. Windows
uses the actual executing interpreter rather than measuring a venv redirector;
interpreter spawn is included, launcher overhead is not. OS filesystem caches
are not purged.

```powershell
$env:AGENT_BRIDGE_STARTUP_PERF = 'report'
try {
    python tools\run-plugin-tests.py agent-bridge --test-file tests\test_startup_performance.py
} finally {
    Remove-Item Env:AGENT_BRIDGE_STARTUP_PERF
}
```

`report` is explicitly measurement-only: it records `baseline: not-established`,
`budget_gate_enabled: false` and whether the provisional target was met. A
successful measurement is **not** performance qualification. Every sample is
retained; no failed or slow sample is retried or dropped. `BRIDGE_STARTUP_PERF`
JSON lines contain the complete timings, Python/platform identity, source
fingerprint, observation-conflict count and final median/maximum. Recognized
Windows publication sharing conflicts remain inside the same wall-clock sample
and are counted; corrupt routes and other errors fail immediately. Incomplete
samples, startup errors and unsuccessful cleanup fail even in report mode.

For a separate explicit budget check, use `AGENT_BRIDGE_STARTUP_PERF=qualify`
with the same command. Its provisional thresholds are a five-second median and
15-second maximum across all five samples. Neither mode changes these
thresholds. Both retain a 30-second startup watchdog per sample, a 210-second
benchmark-item watchdog and the runner's unchanged group/process containment.
Startup measurement stops at readiness; shutdown is outside that measurement
and must succeed separately within its own bounded request and exit waits.

Establish a reviewed blocking baseline on controlled capacity before making
this a publication or promotion gate. A local `qualify` result alone does not
prove controlled capacity or establish that baseline. The performance case is
skipped unless explicitly selected by its environment variable; its budget and
observer unit tests run normally. No required-PR job, matrix or release gate is
added for this periodic/manual benchmark.

## Retained installer guardian: native Windows opt-in

The retained-child desktop regression requires a quiescent interactive Windows
desktop and an installed real `pythonw.exe`. It is outside the fast required lane:

```powershell
$env:COPILOT_EXTENSIONS_NATIVE_WINDOWS = '1'
try {
    python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests\test_installer_guardian_windowless.py
} finally {
    Remove-Item Env:COPILOT_EXTENSIONS_NATIVE_WINDOWS
}
```

It observes the actual windowless parent image, two console-child/descendant
cycles, visible and Default Terminal windows, and foreground transitions. Its
timeout and owner-cancellation cases require pinned process handles to signal
exit and canonical cleanup custody to become available. Breakaway remains
refused; no resident service is installed or escaped from test containment.

The focused generated-parent startup case in `test_installer_guardian.py`
launches an isolated interpreter, without inherited future-annotation flags,
and loads a sibling through the actual generated prelude. Desktop observation
remains the separate opt-in check above.

The existing `test_python_installer_engine.py` teardown-fault regression injects
its first accounting failure inside the guardian, not through parent-only
monkeypatches. It checks frozen inputs, construction/retention exclusion,
descendant settlement and refusal to publish after the recovered teardown.
`test_installer_guardian.py` also launches from a real owning interpreter beyond
Windows' executable-token `MAX_PATH` limit.

Normal Fleet preparation retains its canonical runtime and both process-build
companions. `tools/test_run_standalone_tests.py` exercises that actual preparation
entry point while changing the original resources, then verifies the retained
wheel's exact bytes. The full development extra remains broader than the
dependency-free client; selected source distributions can still require a
platform's native build prerequisites.

## Fleet host probes: native Windows opt-in

`agent-worktrees fleet` launches a local interpreter child and a real `ssh`
client per host. `tests/test_fleet_cli.py` checks their argv and kwargs with a
mocked `subprocess.run`; the desktop regression observes the actual launches
and stays outside the required lane:

```powershell
$env:COPILOT_EXTENSIONS_NATIVE_WINDOWS = '1'
try {
    python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests\test_fleet_windowless.py
} finally {
    Remove-Item Env:COPILOT_EXTENSIONS_NATIVE_WINDOWS
}
```

From a real windowless `pythonw.exe` parent in a kill-on-close Job, it runs
two cycles of each probe path with its production kwargs. The local path
substitutes a real console `python.exe` for the `list` run. The SSH path runs
the real `ssh` client through an isolated `-F` config whose `ProxyCommand` is
a real console `python.exe` that closes the connection, so no remote host is
contacted. It checks observed visible windows and foreground transitions; the
SSH case is skipped when no `ssh` client is on `PATH`.

## Optional busy-host Codespaces functional qualification

The manual `Installation context full` workflow's `codespaces-installer`
portfolio runs this complete packet on hosted Linux and Windows: materializer
and runner integration controls, snapshot-copy/support contracts, the full
canonical installer module, normal committed-HEAD materialization, and the full
materialized installer module. Windows retains all six disjoint installer
partitions. It records exact source identity, profile, outcomes and cleanup
inside one 7200-second functional envelope; required PR CI is unchanged.

For sustained host contention, explicitly select the test-only
`busy-host-functional` profile on the existing isolated runner:

```bash
python tools/run-plugin-tests.py agent-codespaces \
  --test-profile busy-host-functional \
  --test-file tests/test_installer_engine_adoption.py
python tools/run-plugin-tests.py agent-codespaces \
  --test-profile busy-host-functional \
  --test-file tests/test_snapshot_copy_contract.py \
  --test-file tests/test_installer_test_support.py
```

The devcontainer wrapper accepts the same profile and file selectors. It strips
both only from dependency preparation and preserves both for the offline test
pass; preparation still runs without importing tests.

The first command is run against both canonical and normally materialized
source for complete installer qualification. Keep the combined sequence,
including materializer integration, preparation and cleanup, within a finite
7200-second caller-owned containment envelope. The profile is opt-in and logged:
the adoption and copy modules' default item hang guards are 300 seconds instead of 60;
their 45-second native subprocess hang guards become 135 seconds; group and plugin
aggregate guards become 900 and 2700 seconds instead of 300 and 900.
The runner's unmarked-item default remains 30 seconds. All other fixture
deadlines remain unchanged.
Existing explicit 180-second item markers remain 180 seconds in both profiles;
300 seconds is the module default, not a blanket override. Explicit native-call
limits also remain unchanged.
The default item guard includes fixture setup and multiple separately bounded native
calls, so it is larger than the individual native-call guard.

This profile accepts only the three named test files, without keyword/portfolio
filters, host-state opt-ins or limit overrides. It preserves the complete
selected inventory, assertions, admission/refusal and immutable-input checks,
six Windows partitions, and 128-process/4096-MiB-memory/2048-MiB-temp caps.
No repository hash or load probe is a prerequisite. The runner passes a
test-only profile value to these fixtures and clears inherited profile state
for ordinary runs. Functional runs also clear inherited `PYTEST_ADDOPTS`, so
ambient collection-only or keyword options cannot silently replace execution.

A profile PASS is **functional evidence only**. It is not a PASS under the
original wall-time budgets, a performance or baseline qualification, or a
replacement for required CI. Record the profile, source commit, actual selected
outcomes/skips, elapsed times and cleanup result; retain failed standard-budget
runs separately. Default CI, production deadlines, unrelated test suites and
actual performance assertions are unchanged.

### SSH snapshot split gates under load

The same opt-in profile also accepts only `agent-ssh`'s complete
`tests/test_snapshot_runtime.py` and `tests/test_snapshot_windows_visibility.py`
files, separately or together:

```bash
python tools/run-plugin-tests.py agent-ssh --test-profile busy-host-functional \
  --test-file tests/test_snapshot_runtime.py \
  --test-file tests/test_snapshot_windows_visibility.py --junit-dir <reports>
```

Set `AGENT_SSH_WINDOWS_VISIBILITY=1` on native Windows to execute the real
windowless-parent observer. It requires the active accessible input desktop,
not a focused window. Without that opt-in/platform, its skips are not native
visibility evidence.

The functional positives use test-only 30-second optional-peer and 60-second
contributor envelopes, a 60-second fixture-compilation guard, a 180-second GUI
observer and 300-second native item guards. The disposable driver invokes the actual payload bootstrap, completed
SSH/Worktrees runtimes, admission/refusal checks and process-owner primitives;
it changes only the test's positive-call deadlines, not deployed source.
The profile keeps the complete selected inventory and the same runner resource
caps and aggregate bounds described above. No load probe or quiet host is required.

Qualification has separate gates: native functional assertions (including
zero surfaced windows/focus transitions and refreshed external-state reads),
strict production-deadline degradation/cleanup controls, and latency evidence.
The strict controls still require actual `TimeoutExpired` at the original
5-second optional-peer and 10-second contributor limits; optional-peer resolution
and execution descendants must both be reaped. An unrelated error or a missing
PID is not timeout/cleanup evidence. Those controls are never widened by the
functional driver.

A functional PASS plus strict-control PASS may qualify the bounded functional
change when explicitly selected as its acceptance contract; neither establishes
original-budget availability, cold/tail latency or the composed two-second
target. Keep those unresolved under the existing latency objective
[#7935](https://github.com/ThomasMichon/copilot-extensions/issues/7935), retain
failed standard-budget results, and report each gate separately. Production
5/10-second deadlines, ordinary runs, required CI and fresh review are unchanged.

`tools/promotion_unit_tests/test_client_guardian_resources.py` exercises the real
client-to-guardian caller boundary with process/native-import value doubles and the actual
stdin bootstrap. It replaces each admitted executable member after snapshot,
checks the private runtime/procutil namespaces and preserved uncertain custody,
and refuses incomplete, malformed or oversized bundles before execution.
These are portable unit controls, not native Windows process, Job, lifetime
or desktop qualification. The release-helper verifier pins the retained
guardian's complete source shape without executing that actor during discovery.

## The turn-key runner

Worktrees' Windows update admission controls live in
`plugins/agent-worktrees/tests/test_update_entry_windows.py`. Contained pytest
proves early refusal through native and Git-sh fronts, read-only/foreground
availability and exact installed-runtime forwarding. The unmocked positive
check requires an explicitly detached/outside-Job Windows host, rather than
weakening the normal runner's restrictive Job. Prepare its interpreter with
`python tools/run-plugin-tests.py agent-worktrees --prepare-only`, then run:

```powershell
& "<prepared-test-python>" "plugins\agent-worktrees\tests\test_update_entry_windows.py" --native-breakaway-root "<private-caller-owned-scratch>" --powershell "<absolute-native-powershell>"
```

This dedicated check creates only private profiles and permissive fixture Jobs,
enters the real global/payload fronts with an offline bootstrap, and independently
pins any surviving probe child for exact cleanup. It refuses contained mode;
do not clear its marker or change an enclosing Job to manufacture a pass.
The corresponding pytest positive cases are skipped under containment, not
reported as native qualification. No real installation or request is submitted.

The Manager plugin and standalone test preparers install its application
non-editably through the normal retained-source policy. Both use the shared
cache, whose fingerprint binds that installation shape; embedded first-party
imports must survive private-input cleanup. Other plugin targets and the
separately prepared Bridge remain editable. Production installation and test
selection are unchanged.

The `TestSelectedManagerMaterialization` source-equivalence class is one ordered,
fresh-fixture sequence: fixture creation, full compose, selected compose,
full equivalence, then all 161 SDK byte comparisons and trailing rewrite checks.
Select the complete class, not an individual method. Partial/reordered collection
and missing predecessors fail; the class finalizer removes its owned workspace
and rejects incomplete evidence. Whole-tree equivalence alone has a 60-second
item limit; fixture creation, both compose phases, and SDK comparison retain
30 seconds each.
The native packet retains one 300-second clock from before fixture creation
through cleanup. This allocation does not turn the former compound item's
30-second timeout into a monolithic pass.

Closed-wheel deadline negatives use `negative_source_plugin`: a fresh real
three-package local source graph with non-Python resources, not the full
procutil/ZDD process-owner input. Invalid deadlines use an absent source path
to prove refusal before source lookup. The full `plugin` fixture remains
unchanged for native construction, child lifetime and positive consumers;
the negative graph does not qualify those integrations or replace cold proof.

Source-only tooling preparation selects the owning project's declared `dev`
extra (or flat `dev` dependency group) through
`declared_test_tooling_requirements`, plus the existing runner requirements.
The group is a fallback only when the extra is absent. Malformed metadata
tables or non-flat selected lists refuse before installation; group inclusion
objects are not implicitly resolved.
It does not promote runtime dependencies or `first-party-core` build inputs
into tooling. Source-only cache identity includes that selected requirement
set, so a pre-selection environment cannot silently retain missing dev tools.
A selected packet still uses each owner's source-bound native
environment and lease; package-cache warming alone is not preparation evidence.
The optional declared-tooling proof checks a real isolated tooling environment's
import after canonical retained installation, not application construction.

Worktrees' managed-session admission packet is explicitly selectable:
`python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_managed_session.py`.
It uses temporary profiles, real hook subprocesses, both available PowerShell
hosts, owner-stamped installation cells, and the actual extension with a
test-double SDK transport and real Python policy. Native Windows cases observe
two windowless cycles and pin the admission worker across timeout; optional
JUnit output retains individual cold/warm samples and visibility counts, not a
tail-latency or installed-runtime qualification. This does not enroll the
subprocess packet in required unit-only CI or change runner budgets.

The `prepared_cell_membership` selection in that packet stamps real selected
cells through the original resolver, publishes the original linked-worktree
association, then traps interpreter lookup in PowerShell 5.1 and 7. It covers
current reuse, changed install/payload bytes including restored-size/mtime
controls, missing namespace/content witnesses, conflicting payload selection,
wrong registry scope and incomplete older associations under the unchanged
30-second item and 10-second child bounds. Legacy prepared controls remain
separate compatibility evidence. These are isolated native membership checks,
not installed or complete cell-hook qualification.
The seven actor-free producer/adapter controls in
`tools/promotion_unit_tests/test_prepared_cell_membership.py` cover fresh
publication, one-time upgrade without input/Git replay and unchanged-view
no-op, reuse of the original validated bytes with exactly one resolution,
older-SDK, opaque-witness and oversized-source fallback using injected owner/filesystem
boundaries; they belong in unit CI,
not the native subprocess inventory.

The path-gated `prepared-membership-native.yml` workflow separately requires
both Windows shells for the selected-cell controls and exercises real stamped
producer isolation on Linux. The incomplete-association control reaches the
actual original admission CLI and its caller-watch helper, then verifies the
one-time prepared-view upgrade. It remains under the same item/child bounds;
missing native prerequisites are failures in the selected hosted lane.

The `plugin-activation` target runs the shared activation transaction suite.
Its `tests/test_preservation_processes.py` packet interrupts real transaction
holders before/after witness publication and during restoration. Worktrees'
`tests/test_update_install_preservation.py` separately covers native acquisition
integration; `python tools/run-plugin-tests.py agent-procutil --test-file tests/test_owned_streaming.py
--allow-explicit-tiers` covers POSIX FD progress, absolute deadlines, cancellation
and unverified cleanup. These explicit native packets do not expand required
unit-only CI, and skips are not native platform qualification.

### Shared-host command safety

The host may contain live work from other sessions or users. A temporary home,
`USERPROFILE`, `PSMUX_DATA_DIR`, redirected config, Windows Job or POSIX process
group does **not** prove that an external command targets a private service.
Jobs/groups contain child lifetime, not IPC to an already-running mux, Docker
engine, service manager or WSL distribution.

`run-plugin-tests.py` checks test/helper source before dependency preparation
or collection for literal destructive cleanup, including helpers later started
with Python `-I`/`-S`. The injected pytest portfolio guard and repository-tool
conftest also install a Python audit guard before tests execute. It rejects
server-wide mux shutdown, name/pattern/image-based process termination, global
signals, shared service/WSL shutdown, container/image pruning and known bulk
container cleanup. It also rejects real mux commands without a runner-owned
transport. Neither `--allow-host-state` nor `--allow-explicit-tiers` disables
command safety.

Supported local launchers retain scope validation through their effective
environment and working directory, including `env` assignment/unset/reset,
split-string and directory options. Privilege launchers cannot use inherited
`TMUX` as scope proof, and local scope does not authorize remote mux commands.
Shell inspection covers combined command flags such as `bash -lc`, literal
shell argv bodies and encoded PowerShell bodies in both runtime and source checks.
Source checks also recognize literal exec/spawn signatures and PowerShell
termination aliases. Executable lookups (`command -v`/`-V`) remain harmless
inspection, not commands to execute.
Recognized shell state changes invalidate inherited environment or relative-directory
scope evidence; an explicit absolute private transport can still be verified.
Shell substitutions are inspected as nested command positions. Unresolved
variable, home or glob expansion in a mux namespace is not private-scope proof;
literal argument vectors do not undergo shell expansion.
Quoted data is not a command group; unquoted parentheses and braces are
inspected as nested command positions. SSH options and destinations are
separated from the remote command, and explicit local/proxy command options
are inspected independently.
The supported `env -S` subset excludes GNU-specific expansions and escape
sequences that a shell tokenizer cannot safely interpret. Prefer an explicit
argument vector instead of relying on those additional parsing rules.

For real mux fixtures, use
`plugin_test_command_safety.private_mux_args(tmp_path)` and pass its exact
arguments to **every** client and owner-pool operation. On Windows it creates
a fresh `-L` namespace bound to this runner sandbox; on POSIX it selects a
private `-S` socket. Only POSIX tmux's documented, sandbox-contained `TMUX`
socket is accepted as an inherited transport. A different home/data directory
alone never qualifies Windows routing. An isolated Python fixture owner must
explicitly install the same command guard and validate the received namespace
before starting native work.

Cleanup must retire only the fixture's sessions or identity-pinned process
handles. Do not call `kill-server`, `pkill`, `killall`, `taskkill /IM`,
`Stop-Process -Name`, `wsl --shutdown`, `systemctl stop/restart`, or container
pruning from a test. Exact-PID commands are not ownership proof: establish
birth/handle identity first, refuse changed targets and preserve pre-existing
processes. Never broaden cleanup after a timeout or failed assertion. Preserve
evidence and report an unreaped fixture rather than terminating unrelated work.
Do not install/update the operator's runtimes or rewrite Terminal settings as
test setup.

These are accident-prevention guards, **not an OS security sandbox**. Source
checks recognize literal call shapes, not arbitrary generated code; audit
hooks do not automatically propagate through `-I`/`-S`, native shells or
third-party processes. A trusted fixture must carry its explicit scope through
those boundaries. If routing/ownership or child isolation cannot be proved,
mock the operation or run in a disposable VM/container without live host
sockets, credentials or work mounted. Do not add an annotation or opt-out to
execute a global cleanup on a shared host.

Validate refusal with pure command classification, source-only controls and
harmless executable stand-ins. Never execute the dangerous command as a
negative control, even when expecting rejection. Native qualification belongs
on a disposable worker: keep a fixture-owned sentinel in a separate namespace,
prove it and all pre-existing mux identities survive, and clean up only
identity-verified fixture resources. A passing pytest result without those
checks is not proof of host isolation.

The existing optional `installation-context-acceptance.yml` dispatch with
`interactive_completion=true` qualifies the real mux lifetime fixture on
disposable Linux and Windows workers, plus the private POSIX pane fixture.
Windows uses portable PSMux 3.4.0 in runner temp, pinned by archive SHA-256;
it does not install or register a host service. This lane remains manual and
outside required unit CI. Do not substitute a shared developer desktop for it.
The older 3.3.5 fixture can lose a cursor-response wakeup when control requests
overlap pane creation, blocking even direct Python before its first instruction.
The fixture pin does not change installed muxes or production compatibility.
Windows startup controls overlap private mux requests with four detached
launches, using direct Python and the PowerShell wrapper.
The original worker-startup deadline, real TTY, owner-death and sentinel
assertions remain unchanged. A cursor-position response is sent only after a
startup failure, to diagnose a blocked shell; it never converts that failure
into a passing qualification.

Manager's source-bound test environment installs its ordinary wheel, preserving
the declared metadata of its embedded first-party libraries. Other plugin
test environments remain editable; Manager's separate Bridge test dependency
also remains editable. The cache distinguishes this preparation mode from
an older editable Manager environment, without changing runtime installations.

The actor-free `tests/test_resident_configuration.py` selection executes the
native configuration consumer and qualified owner-report reader with a synthetic
peer transport. It compares direct and resident call/attach loading from a
neutral daemon directory, concurrent repository/source isolation, relative and
directory-marketplace declarations, missing/mismatched context refusal, and
pre-effect direct fallback against legacy or invalid capability replies.
It does not qualify an installed daemon, native desktop behavior or session
handover. Existing transport/lifecycle tests remain separately selectable.

Agent MCP's focused home-binding lane is:

```text
python tools/run-plugin-tests.py agent-mcp --test-file tests/test_home.py --test-file tests/test_home_binding.py --test-file tests/test_serve_cwd.py
```

Run it through the bounded supervisor. Fresh child processes use only temporary
profiles and real local daemon listeners, leases and routing publication.
They verify missing, empty, relative and absolute homes across the daemon's
cwd change, named configs/overlays, storage, digest keys and token caches,
with no loose artifacts in the launch directory. POSIX cases additionally
exercise symlink-plus-`..` home overrides and explicit socket paths through real
daemon publication and artifact writes. Existing `test_serve_cwd.py`
retains explicit relative-socket and passive-refusal controls. These native
cases stay outside required unit CI; the packet admits only the dependency-free
home selector's synthetic no-actor controls.

The read-only repository hosting-policy resolver has the explicit
`repository-policy` target, backed by `libs/repository-policy/tests`. Run
`tools/run_tests_in_devcontainer.py repository-policy` through the bounded
supervisor for canonical/legacy/marketplace parsing, negative-policy precedence,
fresh checkout selection and actionable refusal controls. This target neither
launches agents nor changes adoption state. Consumer entry-point and receiving
boundary tests remain with their owning plugins; passing the resolver target
alone is not proof that every managed launch is covered.

The dependency-free worktree identity codec has the explicit
`worktree-identity` target, backed by `libs/worktree-identity/tests`. Run it
through `tools/run_tests_in_devcontainer.py worktree-identity` with the same
bounded supervisor used for plugin suites. Worktrees' compatibility and
consumer tests remain in its own target; repository packaging contracts live
in `tools/test_worktree_identity_packaging.py`. These periodic/source checks
do not enroll new tests in the unit-only promotion packet.

The local runner measures each plugin from dependency preparation through final
sandbox removal. Its aggregate timeout cannot turn a slow cleanup into a pass.
After containment verifies that a group's processes are gone, one cleanup worker
removes that group's private pytest directory while the next group runs. Shared
environment roots and immutable fixture seeds outside those directories remain
available until the plugin finishes. Every queued removal is joined, including
failure and cancellation paths; cleanup failures are explicit. Unverified
process teardown retains the current sandbox, even if an earlier group's
cleanup also failed. The process, storage and per-item/group limits are unchanged.
These focused runner contracts do not qualify a complete suite's timing budget.

Bridge's generated Windows management and payload launchers have a focused
binary/prompt smoke in `tests/test_payload_invocation.py`. The full native
matrix is explicit T3, preserving the default fast portfolio:

```text
python tools/run-plugin-tests.py agent-bridge --allow-explicit-tiers --test-file tests/test_payload_invocation.py --test-file tests/test_carrier_stdio.py
```

It compares actual generated direct-PS1 and retained CMD bodies on PowerShell 7
and Windows PowerShell 5.1, with real selected-runtime resolution, arbitrary
binary frame boundaries, held-open stdin, clean EOF, separated streams and exit
codes. The staged runtime also executes the real carrier protocol. A strict
caller-owned Windows Job proves cancellation retires a pinned live descendant.
Fixtures never install into the operator's runtime or contact a live peer.
The Windows PowerShell 5.1 native prompt case uses its encoded command boundary:
that host rejects the standalone `-` argument before executing a `-File` script.
Only the fixture-owned command/arguments are encoded; the quoted multiline
prompt remains raw stdin. Framed carrier cases still exercise `-File` directly.

Bridge's `tests/test_worktree_owner_launch.py` separately checks the owner
trampoline's exact Windows argv, sanitized environment, inherited stream objects,
no-window flags and exit status. Its native pipe cases deploy the real resolver
into a temporary profile, construct a private versioned peer and run the actual
isolated trampoline with an empty PATH. They require Unicode/spaced stdin and
arguments, separate stdout/stderr, scrubbed caller routing and exact exits 0/7.
Select that file with the same contained runner; existing native deadlines and
platform skips remain unchanged, with no new required-unit CI enrollment.

The source and candidate Linux full-suite declarations use exactly two
work-stealing pytest workers for Worktrees only. Both workers remain inside the
same runner-owned containment boundary and unchanged 30-second item,
300-second group, 900-second plugin, process, memory and temporary-storage
limits. Guard, collection, baseline and unrelated steps do not inherit those
worker options. The local runner remains serial unless explicitly configured;
activation of preserved Main declarations is separately reviewed. These options
do not count as complete budget evidence until the exact full run succeeds.
CI's worker options also require an owning promotion run, so ordinary manual
validation of an older released Main stays serial and does not require a test
dependency that has not shipped there yet.
The initial paired disconnected Linux run executes all 17 groups with the same
11,941 passes and 336 skips: serial 560.951 seconds versus two workers 348.064
seconds, including preparation and cleanup. This is local execution evidence,
not a substitute for the exact hosted release gates or Windows margin proof.

Candidate Bridge validation also opts into exactly two work-stealing workers
inside its existing ordinary Linux job. The owning `promotion_run` is required;
older released Main validation without that input stays serial until the
test-only dependency has shipped. This adds no matrix entry or parallel
pipeline, retains every test and native subprocess boundary, and shares the
same item/group/plugin, process, memory and temporary-storage limits. It does
not increase the preserved Main job's eight-minute budget. Local complete
execution and actual hosted candidate qualification remain separate gates.
Its real relay-probe fixture reserves an ephemeral listener before starting
the probe, and completed-slot harness cleanup looks up its optional function
directly rather than searching unrelated commands or autoloading modules.

The existing artifact-readiness suite also executes the immutable release
materializer against current direct and nested retained-flat source references.
On Dev's legacy controller it reads the actual `origin/main` controller; an
exact generated candidate retains its preserved controller but replays the
unmaterialized manifests and libraries from its recorded exact Dev commit,
not its already-rewritten payload. A real generated Git checkout exercises
that boundary, with altered generated library bytes and refused missing,
invalid or unavailable source identities. Full Git history is
required, as in both existing artifact-contract CI lanes. The fixture loads
only pinned Git helper files in an isolated child, verifies materialized bytes
and rewritten paths, and retains the obsolete-helper refusal as a negative.
It does not build, install, publish or qualify an artifact.

Source-installer smoke contracts cover receipt changes, canonical snapshot
publication and generation selection with small assembled payloads. Logger and
Vault additionally share an explicit full-install lane in Logger's
`tests/test_source_runtime_install.py`: it stamps the complete assembly, makes
the publisher unavailable, provisions and reuses the immutable runtime, then
checks installed commands and failed-candidate preservation in separate bounded
tests sharing one healthy installation. Run each plugin parameter separately
through the bounded supervisor with
`COPILOT_EXTENSIONS_SOURCE_INSTALL_FULL=1` in the runner's environment:

```text
python tools/run-plugin-tests.py agent-logger --allow-explicit-tiers --test-file tests/test_source_runtime_install.py -k "(stamp_provision_reuse or failed_source_candidate) and agent-logger"
python tools/run-plugin-tests.py agent-logger --allow-explicit-tiers --test-file tests/test_source_runtime_install.py -k "(stamp_provision_reuse or failed_source_candidate) and agent-vault"
```

These expensive full-install cases are not unconditional required smoke tests.
They do not start a deployed service or request credentials.

Native tmux pane targeting and seeded New/Resume handoff have a focused lane:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_mux_active_pane.py --test-file tests/test_launch_seed_state.py
```

The native cases use a test-owned foreground tmux server and private socket,
never the user's default server. They exercise both active-pane lookup helpers
and the final cold-handoff guard with real staged-prompt state and a recording
backend, including rejection of a different pane without consuming the prompt.
The companion argv cases preserve psmux target syntax. This module belongs to
the existing fast contract-guard lane, so ordinary PR CI exercises its native
cases on Linux rather than only collecting them. POSIX GitHub Actions refuses
missing tmux instead of silently skipping that proof. Local native cases skip
when POSIX tmux is unavailable; this is not a live Copilot or Windows psmux test.

The Windows status-monitor console regression keeps the at-most-one hidden
terminal host and zero visible-window/focus assertions across eight psmux
probes. Its process census rejects a parent edge only when readable Windows
creation times prove the child predates that numeric parent; unavailable
identities remain conservatively counted. Focused controls cover reused root
and intermediate PIDs, unavailable identities and multiple real terminal
hosts. A native failure records parent/birth/baseline observations and actual
probe-console membership. These diagnostics do not by themselves establish the
cause of the tracked hosted seven-host occurrence (#6266).

Source-bound native lifecycle and restart-evidence contracts use the existing
bounded runner:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_lifecycle_source or test_tracking_session_registration_write or test_tracking_session_deregistration_write or test_native_hosting_evidence or test_restart_observer or test_restart_evidence or test_hook_ipc or test_register_session'
```

The fixtures cover original-source transport, real held-file ownership, source
PID/birth mismatch, delayed historical End versus a replacement self-claim,
source-bound Start through PID-only catalog refresh and matching End cleanup,
timestamp-less Start replay with released claims and duplicate historical End
without title mutation,
same-UUID replacement before or after title input is read,
source-aware legacy End cleanup without fabricated proof and a source-changing
already-linked handoff surviving a stale save,
warm End over real resident IPC with two same-UUID records and an explicit
binding outside cwd,
history-pruning ambiguity including complete source removal, bounded sticky
pruning fences across stale saves and saturation, pre-dispatch old-daemon refusal with a real legacy
peer whose mutation handler stays uncalled, installed-bin helper loading,
the 256-entry retained-history boundary, tombstone-visible closure and
shutdown/cancellation ordering across inferred exits. Closed or definitively
consumed unknown-End unmapped history does not dilute live polling, but a new
live mapping re-admits its subject. Journal compatibility, marker validation,
same-generation persistence and execution-generation reset are covered separately
from inferred-End corroboration.
They do not reboot a machine, kill a
production session, qualify Windows native source capture, or prove acknowledged
restart-seed delivery. Those remain separate native integration obligations.

Default-off recovery configuration and native notification transport use:

```text
python tools/run_tests_in_devcontainer.py worktree-manager -k 'test_manager_config or test_restart_capabilities or test_restart_notification_runtime or test_restart_notification_thread or test_restart_notifications'
```

The disposable transport tests exercise actual pipe-FD ownership, inhibitor-before-
subscription ordering, checkpoint/cancellation/rearm, owner and bus loss, spoofed
signals, immediate SQLite lock refusal and FD cleanup before coroutine pickup.
`test_restart_notifications_dbus.py` additionally uses a private `dbus-daemon`
and negotiated SCM_RIGHTS; it never connects to the host system bus. That native
fixture skips explicitly if the binary is unavailable (including the standard
isolated image), so such a run is not actual D-Bus qualification. A disposable
derivative of the same pinned, hardened image with Debian's `dbus-daemon`
installed can run this fixture without changing the wrapper's filesystem,
credential or network isolation. At production daemon cadence, the private-bus
fixture also delivers a real shutdown signal while ordinary work stays blocked
for 1.25 seconds: checkpoint and inhibitor release must occur within 0.75 seconds,
before the one-second native delay expires. It asserts the notification thread
and its event loop are closed before the actual daemon lease is released.
Thread fixtures prove exclusive adapter ownership, stale-admission refusal,
drain waiting through cleanup, safe undrain and visible startup/I/O/cleanup
failures without busy retry. Default-off/passive paths create no native owner.
Actual adapter pipe-descriptor failures cover both the active inhibitor and
unclaimed reply descriptors, during pump-time and shutdown cleanup. Failure
must survive later adapter state changes and keep runtime drain/close unclean,
even when adapter cleanup records the error without raising.
These contracts do not prove hard real-time OS/filesystem bounds, real host
shutdown/startup coverage, automatic enrollment or acknowledged Resume.

Explicit exact-original restart Resume and read-only outcome composition use:

```bash
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_restart_launch or test_embody or test_bridge_acquisition_launch or test_native_restart_check or test_restart_owner_check or test_restart_launch_inspection or test_restart_capabilities'
```

These contracts cover real concurrent owner processes, owner death after the
native invocation boundary, private/fsynced uncertainty before invocation,
failed outcome publication, corrupt/indirect receipts, changed owner policy,
unknown mux absence, typed staged seeds, exact UUID/full interactive STATUS
FIRST argv and read-only frozen-attempt/outcome binding. The process tests keep
the real locks and durable state; native epoch, process liveness and mux creation
are synthetic. The explicit acknowledgement fixtures exercise actual owner
policy/hosting reduction with synthetic process observations, retained original
activation history and fresh invocation ancestry. Wrong native/ancestry/boot,
head/incarnation changes, missing holds, intentional End, pruned sources,
opaque/other claims and pending prompts must not publish an ACK. A real child
exits after durable ACK publication; an exact repeat must retain the same
receipt without another native invocation. Manager fixtures accept only the
exact typed version-2 acknowledged binding, retaining false restored/automatic
flags and a qualification blocker. They do not launch Copilot, prove prompt
readiness or actual seed consumption, or qualify automatic startup/platform
recovery.
Canonical self-session claim regressions cover the actual native Start
transaction's persisted claim, unchanged owner bytes through exact-original
Resume, wrong machine/project/worktree/UUID, released/producer/opaque/duplicate
or mixed claims, inbound/dispatch/released-ledger fences, and replacement during
preflight. Persisted launch-admission cases clear the writer cache before the
owner reread; opaque dispatch metadata must survive actual YAML serialization.
Concurrent owner processes and post-invocation owner death exercise
both an empty ledger and the original self claim with real locks and durable
uncertainty. The provisional check makes no self-claim provider callback;
task-policy snapshots still cannot authorize a launch.

Exact-original survivor inspection uses:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_restart_survivor or test_restart_survivor_inspection or test_native_restart_launch or test_native_restart_ack_platform'
```

These fixtures distinguish fresh exact-original native hosting from historical
launch/ACK receipts. Stale or missing holds, unknown/dead hosting, wrong
head/path/incarnation/epoch, intentional End, pruned/ambiguous original history,
non-increasing activation/revision, pending seeds and task/inbound/profile
policy cannot confirm a survivor. Current owner/epoch changes during the read
invalidate the snapshot. Real child processes exercise persisted owner reads
and the Manager's engine JSON boundary without changing YAML or frozen SQLite
bytes; process/epoch/mux facts remain synthetic. Ordinary default-off inspection
makes no owner call. No case grants adoption or seed-ACK authority, launches a
native CLI, or qualifies automatic recovery.

Receipt-history fixtures retain the original ACK while launching and
acknowledging a separately frozen next-boot request. They refuse different
requests after either unacknowledged state, changed head/source/activation,
reused UUIDs, boot cycles, malformed history and exhausted counter capacity.
The exact 16-entry limit and aggregate 32-KiB boundary are checked independently,
including a pending receipt that fits but whose maximum valid future ACK would
not. Publication failures before/after the native boundary retain either the
prior acknowledged history or durable uncertainty without retry authority.
Read-only owner/Manager checks retrieve older frozen targets only within the
same platform/environment; current-owner inspection remains boot-strict.
Real owner processes cover concurrent reservation and crashes both before and
after synthetic mux invocation. No fixture qualifies a real native recovery.

Manager-owned durable receipt observation uses:

```text
python tools/run_tests_in_devcontainer.py worktree-manager -k 'test_restart_outcomes or test_restart_attempts or test_restart_launch_inspection or test_restart_owner_check or test_restart_notification_runtime'
```

The lane covers additive version-1 migration, unchanged frozen payload bytes,
typed exact-request outcomes, monotonic state and immutable ACK binding,
orphaned/malformed/oversized storage, write failure and capacity refusal.
At the actual 4096-attempt boundary, failed freezing of a new candidate must
still permit an existing uncertain receipt to advance, without eviction.
Unconfirmed startup evidence still prevents that historical owner call.
Real child processes crash before COMMIT or after publication, both during
migration and later updates; concurrent owner observations retain the newer
receipt without replacement or retry. Active opt-in polling is bounded and
fair, skips historical ACKs and preserves their original target boot.
Unavailable or stale owners cannot erase uncertainty. Default-off, passive and
draining generations make no owner call. A real existing-daemon loop fixture
observes under its lease/mutation fence, and a blocked owner read keeps drain
busy until it returns without persisting after admission closes. Ordinary
inspection reports retained outcomes without owner I/O or schema migration.
These are durable receipt and daemon-lifecycle contracts with synthetic owner
responses, not live native Copilot readiness, seed consumption or automatic
startup qualification.

Positive owner/Manager ACK fixtures use the mux producer's actual `$1:100`
incarnation shape, including the leading dollar sign, rather than a
synthetic token alphabet. Newline and delimiter-bearing tokens remain refused.
`test_native_restart_ack_platform.py` is not POSIX-skipped: unsupported platform,
container or unavailable-epoch capture must return a structured refusal before
configuration, lifecycle/admission locks or Unix-only receipt I/O is reached.

Frozen restart-attempt identities and historical owner bindings use:

```text
python tools/run_tests_in_devcontainer.py agent-worktrees worktree-manager -k 'test_native_hosting_evidence or test_restart_observer or test_restart_evidence or test_restart_attempts or test_restart_capabilities or test_restart_notification_runtime'
```

Provisional current-worktree checks additionally use
`-k 'test_native_restart_check or test_restart_owner_check'` with those same
two release units. The tests cover exact incarnation/head/lifecycle/activation
binding, pause/seed/task/claim/profile refusals, changed owner observations,
transcript/mux/liveness blockers, target-epoch skew, malformed input and typed
response mismatch. Failed/denied/timed-out mux probes stay unknown, confirmed
empty listings qualify absence, and existing dotted worktree names use their
canonical mux encoding. One actual child reads persisted owner YAML and a separate
Manager/engine process fixture verifies the no-update/no-reconcile environment
and unchanged real attempt storage. Process observations and native epochs are
synthetic; these fixtures do not launch Copilot, qualify action-time task policy,
reserve a recovery launch or prove restart-seed acknowledgement.

Current task-policy snapshots additionally use:

```text
python tools/run_tests_in_devcontainer.py agent-dispatch agent-worktrees -k 'test_native_restart_policy or test_native_restart_check or test_claim_providers or claim_status'
```

This lane covers the opt-in owner API's reduced SQLite snapshot, explicit
writer/native owner/generation/hold/steer/waiter checks, old-coordinator and
missing-field refusal, and unchanged default callback output.
Submitting two steering instructions while `awaiting_steer` and
`resume_requested` are false must block qualification; partial consumption
keeps the blocker, and consuming the final entry restores only a provisional
match. Missing or non-Boolean inbox evidence also refuses.
An actual CLI
child makes only the task-policy HTTP GET; another child reads persisted
worktree YAML and invokes its registered callback process with the exact writer,
opt-in flag and no-self-provision environment. These are disposable protocol
fixtures, not live task acquisition, automatic launch permission or native
restart qualification. Mixed/legacy/released/producer claims remain blocked;
generation snapshots are not reservations.
Persisted-record regressions cover unknown/null/structured claim states and
malformed claim collections through actual owner load/save. `test_claim_codec`
checks the retained `metadata_opaque` marker, conservative placeholders and
unchanged valid/legacy wire shapes. Normalized active state cannot become
restart admission proof; ordinary finality handling stays conservative.

The frozen-identity lane covers no-write default/passive/inspection paths, legacy
binding refusal, original-snapshot retention, cancelled-shutdown fences, private
storage, corruption/schema refusal and atomic capacity rollback. Two real
processes prove concurrent freeze deduplication; a disposable child exiting
after insert but before commit proves crash rollback without discarding prior
attempts. These are metadata/transaction contracts, not owner/task admission,
native launch, seed pickup or platform restart qualification.
Malformed SQLite column types, truncated databases and orphaned sidecars must
block without losing state or preventing ordinary mux startup. Initial and
promoted daemon activation share the drain-visible mutation fence; admitted
freezing prevents idle-drain acknowledgement, and draining blocks a new freeze.
Extreme numeric timestamps and deeply nested JSON exercise the same structured
blocker across both the source-evidence and attempt journals.

Canonical native machine-record repair has focused existing-linked-Git and
removed-record lanes:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_machine_record_repair.py --test-file tests/test_machine_record_history.py
```

The removed-record fixture also drives the real CLI from a fresh knowledge-only
registration without a projects entry or local project config. It verifies
read-only preview, exact scalar replacement and preserved alias relationships.
Existing linked-Git proof and native/guest refusal remain separate contracts.
Legacy handoff fixtures explicitly model their non-Git anchors as having no
in-repo settings, asserting those files remain absent. Receiving machine-local
YAML and machine-registry readers remain real, with a fresh memoization scope for
each offer/accept transaction. No snapshot crosses a topology edit or retry;
record/registry locks and native Git leases remain real. The ordinary item and
suite deadlines are unchanged.

Bridge's owned-reader cancellation tests cover both the sample join and the
native-command join. Non-cancelling task waits retain detached ownership and
existing deadlines while late failures are consumed without abandoned-shield
exception reports, including on Python 3.14.
The separate native HTTP/2 peer consumes the redirect request body before its
307 response; its evidence probe verifies body consumption and no forwarding.

Standalone fleet, Manager and Dispatch PR jobs are admitted before runner
allocation by `tools/ci_job_admission.py` in the existing discovery job.
`tools/test_ci_job_admission.py` exercises pinned real-Git inputs, dependency/
unknown-path widening, actual job expressions and required aggregate refusal;
`tools/test_fleet_ci_scope.py` retains all six Linux/Windows partition contracts.
The contract selection runs once per platform's planner partition, not twelve
times. Non-PR full/manual/promotion coverage remains enabled even when discovery
is skipped or fails; the existing aggregate still decides overall success.

Guest-environment coverage runs the actual Python, Bash and PowerShell CLIs
with only operating-system observations substituted (account home, guest kind
and machine-id). Each writer's private registration is read repeatedly by all
three runners with and without direct-entry variables. Exact profile snapshots,
registration mode, foreign/conflicting identity and malformed-version controls
retain real filesystem, parser, lock and atomic-write behavior. All three
writer parameters participate in `installation_context_smoke` on native POSIX;
native Windows tests separately cross both installed PowerShell hosts and
prove inherited WSL/OS labels do not confer guest identity. Targeted Worktrees
doctor tests cover explicit enrollment, repeated SSH-style no-write calls and
refusal before unrelated repair machinery.
Separate enrollment-admission fixtures inject another completed registration at
real lock entry, using the native atomic writer. Every runner must revalidate
the newly visible identity, refuse a conflicting name and retain canonical
case for an idempotent enrollment; lock ownership and cleanup are not mocked.

Remote ACP's focused contract/ledger tests and independent synthetic-process
consumer lane run together:

```text
python tools/run-plugin-tests.py agent-bridge --test-file tests/test_remote_acp_transport.py --test-file tests/test_remote_acp_sessions.py --test-file tests/test_remote_acp_peer.py
```

The peer uses a disposable locally trusted synthetic certificate and Hypercorn:
actual negotiated TLS HTTP/2 with connection/session SSE, and actual WSS
upgrade. It checks explicit structured permission/form roundtrips, cancellation,
auth refusal/refresh, cookie affinity, redirects-off, disconnect/load without
prompt replay, capability mismatch without a write and independent server
survival after client close. It does not contact a deployed remote agent or
prove deployment readiness. Existing ACP/client/API tests retain local/provider
regression coverage; the new lane is bounded rather than an exhaustive fleet
matrix.

Bridge's CI smoke job retains the full contained suite and fresh independent
peer processes. Its bounded job budget is 12 minutes (the same native-consumer
exception as dispatch/index): an observed complete run exceeded the former
eight-minute job limit during cleanup despite passing test steps. Other plugin
budgets and the required aggregate gate are unchanged.

Bridge's tolerant-wire-reader guard continues to cover every discovered peer
model. Its explicit local-configuration classifications are the immutable remote
target, contribution, binding and auth-approval settings: unknown configuration
fails closed instead of silently ignoring a typo. Subclasses do not inherit
that classification. Catalog/source tests cover enabled payload ownership,
non-runnable missing bindings and exact URL/auth policy. The native TLS lane
also crosses ordinary roster/detail/create and remote resume for configured,
URL and contributed targets with a real custom-scheme header.
Remote replay tests also include unfinished historical
tools, checking live tracking and telemetry after load, database restore and
event-log rebuild.

The shared `agent-procutil` daemon-broker unit suite covers authenticated frame
bounds, adopted finite native handoff, containment opt-out, partial-start
cleanup and adoption failures. Its Windows-only native regression launches from
a windowless parent in both breakaway-permitted and breakaway-forbidden
kill-on-close Jobs. Each mode runs two full launch/stop cycles and observes
actual daemon survival, exact argv/environment, split logs and zero attributable
visible windows or foreground changes. A separate broker-handle case checks
wait timeout, termination and actual exit status. The forbidden-breakaway cases
fail rather than skip when lifetime escape fails; contained-mode execution
instead proves that the daemon dies with its caller's Job.
The redirector-fallback case also runs two native cycles; standalone import
tests cover both an alias-loaded single file and a minimal package. Consumer
checks exercise the real Session Host zipapp and the actual standalone
Worktrees lifecycle loader. Fault-injected cleanup waits/closes verify
independent cleanup, normalized errors and refusal to retry with uncertain
process ownership.

Pull Requests' installer wiring contract covers both canonical development
references and self-contained materialized payloads. Its fixture invokes the
real installer-engine materializer without changing the source wrappers or
canonical assets. Both language variants must use the same layout; materialized
engines and their required support files must exist locally and match the
canonical bytes. Missing or modified assets and mixed source/local references
remain failures, not accepted release-layout variations.
Its separate synthetic legacy-source transaction fixture carries the complete,
byte-checked canonical `versioned_runtime*` family rather than unrelated cell
bootstrap inputs. Native construction/reuse and installed-closure retention
remain real; full cell payloads stay in their own acceptance lane. Each native
harness owns its child tree and reaps pipe-inheriting descendants on timeout
within the unchanged query/item deadlines.

Bridge's native PowerShell/Bash completed-slot admission and serving-reconcile refusal
matrices use a minimal plugin payload. Both setup and admission still compute
the real fingerprint and use real versioned-runtime evidence; dedicated
shared-library fingerprint cases retain the full transitive fixture. This
keeps unrelated MSYS hashing subprocesses from consuming the admission cases'
unchanged 30-second item budget. Install and update partitions retain every
case without extending the 300-second suite budget.
Two separate-process rerun cases prepare a completed slot once and invoke the
extracted update function twice, checking receipt bytes and mtime, slot and
receipt identities, and slot contents. They carry a 120-second item bound for
the three native invocations. Installer locks, drain, build, manifest and
serving remain faked, so these cases are not whole-update or native-lock proof.
They run in the periodic Linux plugin suite, not the actor-free PR unit packet;
the periodic Windows job remains unenrolled.
The current-daemon native fixture starts independent cold daemon and CLI
processes concurrently. A stdin barrier holds the actual CLI entrypoint until
the daemon is verified ready; neither process is pooled or replaced by a mock.
Their imports and consumer execution share the same 25-second budget, with
cleanup inside the unchanged 30-second item cap. IPv4 exercises reconcile and
IPv6 exercises the read-only installer probe. Fresh-root cases retain ordinary
`python -m agent_bridge` invocation.
Native fixture processes are contained before execution: required no-breakaway
Jobs on Windows, owned sessions/process groups on POSIX. Bounded teardown covers
broker descendants even before a serving-worker receipt exists; an injected
early-failure case verifies both actual processes exit. Real worker CLI cases
also prove missing/corrupt serving credentials stay unchanged, while ordinary
startup retains first-run credential creation.
The same native lifecycle fixture also exercises a supported non-default
loopback bind and graceful start/stop/reconcile. The stopped case compares
runtime files after confirmed native exit, excluding only its own log/output
files, and requires inactive success without activation or runtime writes.
Conditional cutover's native contention checks retain their one-second limit,
measured from actual holder acquisition. Earlier healthy publication/fixture I/O
is not part of that contention interval; the native lock and whole-item deadline
remain real and unchanged.
Real stalled-probe regressions establish descendant readiness and birth-bound
Job/group custody before the forced 0.8-second capture; that fixture input is
not a bootstrap allowance or the production 30-second authentication timeout.
Communication and retirement stay real. On Windows, known pending termination
may receive one same-pinned-handle exit confirmation, capped at 1,000ms inside
the existing five-second actor window with one second reserved for heartbeat
and diagnostics. Failed/unknown observations remain fatal; identity, custody,
absent stop, signaled exit and stable heartbeat must hold before watchdog
expiry and fixture Job close. No production timeout or containment is changed.
The finite Windows probe actors do not import the unrelated process runtime
before publishing readiness. A poisoned-runtime-module regression proves this
dependency boundary while retaining the ten-second readiness and five-second
active windows.

Windows census transport writes compact JSON directly to explicit UTF-8 stdout,
without host formatting. Its native synthetic-data regression round-trips a
command line longer than 400,000 characters with Unicode and control characters;
real durable-launch and cutover tests still exercise the actual CIM inventory.

Budget Guidance's installer fixture supports both development and materialized
payloads through the real materializer. It preserves an embedded dependency
byte-for-byte and fails visibly
for an incomplete embedded manifest/package rather than repairing it from the
development checkout. The cross-platform fixture guards cover both layouts,
source immutability, and malformed embedded input; installer behavior is unchanged.
The billing-response rejection matrix uses short explicit parameter IDs while
retaining its 65,537-byte oversized input, so Windows can set pytest's current-test
environment value without preventing the actual 65,536-byte boundary check.
External-reader fixture snippets use isolated, no-site Python because they need
only the standard library. The real reader, process cleanup and existing
half-second deadline still execute; unrelated editable-package startup is not
part of the fixture's parse/exit/byte-bound classification workload.
Native failed-install and retry cases execute the real PowerShell installer from
a standalone fixture completed by the real dependency materializer. The payload
has no file-pointer or installer-engine references to expand. Fixture copies
omit only their own top-level tests and runtime caches, retain embedded
dependency test/data inputs, and use at most 16 copy workers with propagated
errors. The fake UV's stdlib-only driver uses isolated no-site Python, and the
native child receives explicit executable/module paths and disabled PowerShell
telemetry. Real installer execution, failure injection, slot/selection assertions
and existing item/child deadlines remain unchanged.
The verified-session quota guard checks one absolute acquisition deadline before
and after RPC phases and passes only remaining budget to each RPC. Deterministic
clock cases prove completed late values are withheld; separate real async cases
retain cancellation checks at first status, quota and final status.

The registered-repository Git-probe guard follows the real `repos._git` to
`git_ops.git` call chain. It checks headless options at that subprocess owner,
the existing 60-second bound and UTF-8 capture policy, and removal of inherited
repository/config selectors while retaining unrelated environment state.
The companion real-Git identity test and native registered-only first-touch
CLI test remain process-boundary coverage rather than mocked-runner evidence.

The install-contract machine-gate probe exercises actual policy, gate, planner
and execution-barrier source with synthetic config/inventory/installer
boundaries. Registered and module plans cover forced and ordinary Windows/Linux
updates, explicit enabling repositories and anchor fallback, missing manifests,
universal runtimes and detection failures. Source-mutation tests independently
break each planner and the shared policy/barrier; the production guard-wiring
test prevents these probes from becoming an unwired helper. This source-level
contract does not substitute for native installer or installed-update proof.

Runtime purge contracts retain the original install-lock identity and active
staged sources while removing runtime/config contents. The existing tool guard
suite collects native POSIX flock, live PID-fallback, link-target and failure
cases. Linked roots with terminal slash/dot components must be refused, and a
symlink followed by `..` must purge only the physical directory holding the lock.
Root/home resolution preserves trailing newlines and ignores `CDPATH`.
An unprivileged mode-0300 case holds the real transaction lock and requires
unreadable roots to fail; its source-mutation control reproduces the old false
success without changing item or job deadlines.
Vault's transaction suite runs the real shared helper under both Windows
PowerShell 5.1 and PowerShell 7, with a held lock, open staged source and outside
junction target. Native PowerShell purge cases compare home directory identities
through linked ancestors while retaining legitimate aliased runtime paths; the
existing POSIX guard also exercises that helper under native PowerShell.
Native consumer cases exercise supported uninstall entrypoints
and read-only previews in fresh roots; service APIs are isolated, never live.
Pull Requests retains its existing 300-second production wait: its immediate
native lock probe and source-wiring test cover contention without stretching a
test deadline to wait out that default.
Its fresh-payload fixture invokes the real engine materializer, then runs the
actual marketplace-path self-stage/uninstall twice under native PowerShell and
POSIX Bash. The next invocation reaps the prior dead stage while an independently
live stage survives. Immediate self-stage cleanup is not the v4 protocol.

The Worktrees standalone instruction-preflight fixture copies runtime files
with at most 16 workers, waits for every copy and propagates copy errors.
Byte-preservation and cache-exclusion guards cover that fixture separately.
Native PowerShell and Bash preflight checks assert the framed launch-progress
events while retaining the existing test and subprocess deadlines.

The Worktrees bootstrap fixture also copies its complete plugin payload with
at most 16 workers before the real release materializer closes the dependency
graph. Copy failures propagate, and byte, file-mode and modification-time
guards cover the staged input. Installer source assertions follow the isolated
UTF-8 runtime arguments and direct installation-context helper invocation.
The shared PR repository seed pins `core.autocrlf=false` in its own local Git
config, so copies retain identical blobs even when system Git configuration
enables line-ending normalization. Its regression creates the matching squash
commit, pushes it and runs the real pull-forward operation; production Git
configuration and rebase conflict handling remain unchanged.

Landing explanations evaluate each base's complete evidence once and reuse
those checks only while forming that read-only report. Its creation-history
queries share a context-local snapshot that is discarded even when the report
raises. Native Git regressions add an empty commit or expire actual reflog
evidence after a report, requiring later reports and pruning checks to read
fresh state. Report fields, refusal messages and provider validation remain
unchanged. Historical cross-base cases copy complete prepared Git topologies
for both publication bases, retaining independent objects, refs and reflogs;
each case still performs its own later commit/revert, rewrite, expiry or
shadow mutation and original safety assertions.

Config-overlay regressions require self-hosted lookups to retain base and
machine-local sources without resolving an unused state root. Stateless and
external-state configurations still resolve the knowledge overlay on every
call, including changed or removed bindings. `config_sources` owns this read
path; `state_root` re-exports the same types and function for existing callers.
`tests/test_related_knowledge.py` additionally checks binding-driven related
synthesis, canonical registration, collisions without policy-trust promotion,
ambiguous sources and preservation of ordinary self-hosted configuration.
The opt-in T3 `tests/native/test_related_knowledge_native.py` uses real Git
bare/linked-worktree fixtures and the installed CLI to exercise list/resolve,
aligned-only planning, narrative-scaffold refusal and structured nonzero
canonical-class failure. It allocates no managed pair and preserves both
repository trees. The `related-knowledge` portfolio in
`installation-context-full.yml` selects these files on Linux and Windows.
A separate standard-tier packet checks their dependent PR-binding, session
context and repository-configuration consumers, including the actual CLI.
Neither packet extends required unit-only CI or qualifies actual write/cleanup,
late-binding retrofit or installed rollout.
The diverged-checkout Git fixtures
hide only the unrelated CodeSpace CLI from discovery, leaving real Git,
finalization, status polling and checkout-invalidation assertions intact.
The claim-warning suite separately exercises CodeSpace CLI invocation.
The native consoleless-parent observation in `test_pr_rebase.py` also executes
both state-root Git probes over two cycles, observing windows and foreground
transitions without pytest's subprocess-suppression hook.
Post-publication refspec finalization cases use the existing private-copy
publication fixture. They still perform their own real squash merge, fetch,
reconciliation and unchanged branch/content assertions; publication itself is
executed by the shared seed rather than repeated as setup in each case.

The optional profile-assignment-state failure guard follows both real command
layers returned by worktree creation: exact record-bound execution admission,
then the installed setup launcher. It checks the underlying model arguments,
fallback profile environment and absence of assignment metadata without
mocking either launcher or treating a setup host as the Copilot executable.

The create-plan project-scoping guard uses a real `WorktreeRecord` derived from
the shared fixture, with the selected creation identity and owning project.
Only external side effects are stubbed: the real creation path assembles its
launch plan, including the deferred admission command. The guard still requires
that plan to name the selected repository rather than the stale ambient project.

Agent Logger's PowerShell access-denied classifier guard executes the extracted
installer functions under both installed PowerShell hosts. Each child uses fresh
contained home/config/temp roots, noninteractive startup and disabled telemetry
and update checks; its existing 20-second process deadline and all downgrade/
rethrow assertions remain unchanged. An immediate stderr marker distinguishes
entry into the harness from a pre-script startup timeout. The original hosted
timeout's cause remains tracked under #6747; a passing native run alone is not
flake acceptance.

The POSIX lean-provision launcher test uses fake uv and excludes runtime `src`
and `libs` trees from its temporary payload. It still exercises real lifecycle
admission, self-staging, activation, resolver deployment and both generated
launchers under the existing 90-second item budget. Package construction and
whole-source fingerprint coverage belong to their own contracts; this fixture
must not grow with unrelated runtime source files.
The healthy and namespace/watchdog cases share this source-free payload builder.
Healthy provision has a 20-second installer-owned deadline inside the unchanged
90-second pytest ceiling; failures retain both stdout and stderr diagnostics.

### Shared PR identity against a disposable KeePass database

The opt-in combined contract is
`plugins/agent-worktrees/tests/test_identity_vault_keepass_live.py`.
It requires a real `keepassxc-cli`, both plugins' source/dependencies available
to the selected test interpreter, and
`AGENT_WORKTREES_IDENTITY_REAL_KEEPASS=1`. Run that exact file with pytest
and an explicit 180-second item ceiling for native launcher integration;
normal required suites skip it without the explicit opt-in.

The fixture creates its own database, master password, owner key, cache,
configuration and foreground daemon with a private local endpoint (dynamic
loopback TCP or a Unix socket). It verifies the daemon PID before private IPC
and retires only that child. It never targets an installed daemon or the
operator's database.

The individually bounded cases prove existing-key reuse, mutation-free
status, persisted source selection, a fresh normal consumer while the private
daemon is locked, ordinary missing-entry refusal, explicit first-owner creation,
retry without database rewrites, and stopped-service refusal without spawning
a resident inside the contained adapter. Setup and source configuration must not
expose fixture key material. This is a source integration proof, not installed
release or whole-machine clean-room evidence. Keep source/runtime selection
explicit and record the Docker lane separately when it is unavailable.

The vault's matching `test_identity_payload_gate.py` actual-launcher contracts
are also opt-in (`AGENT_VAULT_IDENTITY_PAYLOAD_GATE=1`, and
`AGENT_VAULT_IDENTITY_REAL_KEEPASS=1` for real databases). Select that exact file
with a 120-second item ceiling. Its pure pin-validation contract remains in
the ordinary suite. This keeps repeated native-shell startup and real databases
out of the required fast portfolio rather than expanding its default budget.

### Fleet replay-retention boundaries

Native index installer snapshot tests separate dependency-content identity
invalidation from publication ordering and snapshot-local consumer closure.
Every case retains its real PowerShell boundary and existing 25-second child
deadline; engine and library changes are independently covered, not omitted
from the unchanged publication/older-pointer refusal case.

Select Index's two-cell shutdown contracts with `tools/run-plugin-tests.py
agent-index --test-file tests/test_installation_cells.py -k
"two_cell_local_services or two_cell_shutdown"`. The real isolated services
retain their observed instance tokens, require explicit shutdown acknowledgements
and zero exit codes, and report control failures after waiting for both children.
Four boundary-fault controls inject a false acknowledgement, a response failure,
a nonzero wait result and a timeout result after real graceful shutdown; they
verify that neither actual child remains running. Native deadlines, item limits
and required unit-only CI selection are unchanged.

The passive index-service promotion contract supplies an explicit synthetic
search engine, just as the adjacent drain cases do. Its real HTTP admission
must reject passive queries without invoking that engine, then admit exactly
the tested query after owned promotion; it never contacts an embedding service.

The required native fleet lane runs the replay-expiry/idempotency contract with
a four-event window and real, fully synchronous SQLite transactions. The same
parameterized test also crosses the production 1,024-event window under
`observation_retention_full`; unfiltered local and manual workflow runs retain
that full-size proof. Required PR/push lanes exclude only that parameter and
the existing `client_distribution_full` artifact matrix, keeping the
120-second containment budget unchanged. This avoids making one boundary
test issue over 1,000 disk flushes on every hosted Windows run; neither the
production retention limit nor its durability policy changes. Explicit manual
runs select both windows and the full artifact matrix with a 600-second fleet
suite budget inside a 15-minute native runner job. PR/push budgets remain
120 seconds for fleet, five minutes for the Linux job and seven for Windows.

The native fleet matrix runs disjoint planner, durable storage, artifact,
client-distribution/diagnostic, Gateway TLS and Gateway process partitions on
both operating systems, each with its own unchanged containment and job deadline.
The exact `-k` selectors are
declared together in `ci.yml` and checked by `tools/test_fleet_ci_scope.py`.
The required PR aggregate depends on both static GitHub-hosted
`fleet-native-linux` and `fleet-native-windows` partition matrices;
a failed or cancelled partition fails that gate.
Every otherwise selected test belongs
to exactly one partition; neither partition is advisory or fail-fast.
Manual runs use the same complete partition and retain both full markers.
Native artifact/diagnostic subprocesses and durable planner/ledger tests do not
consume each other's wall-clock budgets. Gateway TLS/credential tests and
separate-process acceptance are independently bounded too; none shares the
unrelated test-runner job's budget.
The full Worktrees runner gives `test_python_runtime_dev_live.py` and
`test_python_runtime_install.py` separate file groups. Exact-head native CI
showed the former shared group consuming 196 seconds through both dev cycles
before monitor drain began; that left 104 seconds for a drain test independently
measured at 129 seconds. Splitting those files preserves every collected test
and all item, 300-second group and whole-plugin deadlines. Filtered invocations
retain their existing single-group behavior.

The focused `test_python_runtime_install.py` selector
`-k prepared_bootstrap_loads_actual_installer_without_source` prepares the real
retained Worktrees lifecycle, removes its frozen source and runs the copied
installer's `--help` with an isolated interpreter. The complete closure must
load, while deleting either wheel-snapshot helper must fail at its import.
These controls require no package build, UV invocation or host runtime change.

### Native PR-owner installation acceptance

Released checkouts retain independent vendored library copies in each payload.
When the runner combines same-checkout consumers, it resolves duplicate library
identities through the repository's canonical `libs/` sources, with separate
constraints preserving the consumers' version requirements, platform markers
and selected extras. Inactive optional requirements do not add constraints;
marker truth remains with UV's selected target rather than the runner's Python.
Those canonical sources participate in cache freshness. Missing or ambiguous
canonical sources, direct library URLs and replacement dependency metadata
refuse preparation rather than choosing an arbitrary copy. Development graphs
already sharing one source keep their existing behavior. This is test-environment
composition only: installers and independent payload closures remain unchanged.
Source mappings with `marker`, `extra` or `group` selectors also refuse composed
overrides across the original and selected canonical graphs; unlike dependency
requirement markers, their conditional local/registry choice cannot become an
unconditional canonical URL.

The manual `installation-context-acceptance.yml` workflow retains its derived
diagnostic matrix by default. Dispatch it at the candidate ref with
`watch_owner=true` to run the PR owner's normal full-payload refresh/live upgrade
and two-shipped-cell isolation, normal PR-owner peer roundtrip, plus
reachable/unreachable lease-only refusal,
on separate fresh Linux and Windows hosted
accounts. Each job explicitly uses `--allow-host-state --allow-explicit-tiers`;
the test fixture refuses non-hosted runners, existing installation/config state,
or a home/profile differing from the OS account database (POSIX) or native
profile-folder API (Windows). It never redirects HOME or USERPROFILE in this
lane. The hosted job discards the whole account afterward, while each test
gracefully stops only its own original, natively captured owners.

The Windows normal-upgrade scenario also runs the opt-in bootstrap admission
visibility/cleanup probe for Windows PowerShell and PowerShell 7. It launches
the actual wrapper from a real `pythonw.exe` parent, exercises an unflagged
local console descendant across two invocations, and uses the shared live
Win32 window observer plus stricter foreground tracking. A separate forced
timeout proves all four native participants belong to the caller's Job and
exit when that Job is terminated, with zero active processes confirmed.
The probe is restricted to the disposable hosted-account lane, not the
operator's desktop or fast required CI. It creates no daemon or remote route
and does not test or authorize deployment.

These are manual acceptance jobs, not additions to required fast CI. Each
composite case runs separately under a 1,200-second group and 1,800-second
aggregate bound; actual service cutover and native-exit deadlines remain
120 and 30 seconds. Ordinary contained Windows invocations still use disposable
fixture profiles and are not evidence for the native-account lane. The lease-only jobs first perform normal full-payload installation, then
prove refusal with its already-selected metadata and archived terminal custody
preserved; they never control or replace the unprovable original. Local Windows
private-profile coverage for that first-touch path is explicitly selected with
`--allow-explicit-tiers -k normal_payload_lease_only_owner_preserves_selected_metadata`.
The `normal_payload_peer_roundtrip` case uses the normally installed PR payload
as both caller and target peer, with a test-owned Worktrees echo backend and
canonical adoption/resolver primitives. It proves inherited streams and exit
status, credential/override scrubbing, explicit same-cell PR health, unchanged
selection and no bytecode mutation of the immutable bootstrap snapshot.

The same opt-in also runs separate contained owner-boundary jobs on Linux and
Windows, without host-state admission: actual completed-slot controller death
at 12 cutover boundaries and a real installer activation amid admitted
register/unregister requests, a poll result, a slow acknowledged callback and
queued legacy callbacks. These use disposable fixture profiles and test-owned
fetch/gate/spawn adapters, not native-account or full-shipped-payload evidence.
The passive-bind crash regression remains an ordinary smoke case; the other
crash items and the composite concurrent upgrade are explicit T3 cases. Run the
full process matrix with `--allow-explicit-tiers` and the selectors
`actual_controller_death_recovers or bound_passive_endpoint_recovery` and
`actual_installer_upgrade_drains_handlers_poll_ack` in separate runner calls.
Production callback, cutover and original-exit deadlines remain unchanged.

The separate native macOS job runs
`native_macos_governed_watch_refuses_unprovable_identity` in a real completed
interpreter. Until shared native birth/exit evidence supports macOS, governed
activation and instance creation refuse; an absent-owner stop stays bounded,
and cleanup/original-exit recovery refuse without health control, signaling,
spawning or selected-state mutation. This proves the fail-closed boundary, not
working macOS governed cutover. The ordinary unsupported-evidence unit case
injects unavailable primitives and is not native macOS evidence.

### Derived native installation diagnostics

The Windows source-provenance/read-only lane separates command-catalog
contracts, command-lifecycle contracts and native first-touch cases. Every
previously selected case still runs; each step retains its two-minute deadline
and the enclosing job retains five minutes. The former combined selection could
exhaust a step's budget through cumulative successful native calls, without any
single subprocess exceeding its own deadline. Do not re-combine those groups or
drop first-touch assertions to make the aggregate appear faster.

`libs/payload-invocation/tests/test_derived_diagnostic_cli.py` executes the
actual vendored shell entry points selected by the installation-context
inventory. Each copy has independent legacy/active and absent/retained-pointer
items; each item invokes `status` and `probe-legacy` twice and compares the
complete fixture profile after every call and after PowerShell-host shutdown.
The existing initialized-host fixture excludes only host initialization from
the baseline, never product execution or profile contents.

Required change-scoped CI runs the Agent Worktrees copy's four cases with
`-m "not derived_diagnostic_full"`; the existing derived-Python API matrix and
mutation tripwires remain required. The explicit
`installation-context-acceptance.yml` manual workflow executes every native
copy on Windows PowerShell 5.1 and POSIX, partitioned by mode and pointer state
into eight independently bounded four-minute jobs. Run it against the exact
candidate ref before claiming all-copy native acceptance. A smoke pass or
byte-identical vendoring check alone does not satisfy that broader gate.

### Assignment reconnect contracts

The assignment TCP reconnect guard first holds the initial ready frame until
startup reconciliation completes, then waits for the independent ready-triggered
read before disconnecting. It holds the replacement SSE subscription at
an explicit server-side barrier while creating unannounced work, and releases
that barrier only after checking the disconnected queue, rather than assuming
an HTTP request and a fixed sleep fit inside the real reconnect backoff.
The blocked-spawn presence guard also waits for actual reader readiness;
heartbeat progress alone does not prove the independent reader connected.

### Native category view contracts

The worker-instance first-touch CLI contract has independent catalog, save,
activation, retry, published-catalog and degraded-catalog items. Each makes one
real cold subprocess call under
the existing 20-second subprocess and 30-second item bounds; prerequisite
states use the same CLI parser/handler in-process against the fixture's real
files. This preserves every cold-entry boundary without composing multiple
permitted 20-second calls into one 30-second item.

`plugins/agent-dispatch/tests/test_task_categories.py` gives each native view
contract its own function-scoped coordinator/database and pytest item:
`test_native_cli_category_view_contract[default-work|all-categories|maintenance-board|evidence]`.
Each item makes one real cold CLI invocation with the existing 20-second
subprocess bound under the unchanged 30-second item budget. Producer creation
still crosses real loopback HTTP; evidence inspection retains result, progress,
statistics and session/worktree attachment assertions.

Do not combine these into one serial multi-CLI item: four individually permitted
20-second calls allow 80 seconds before setup/teardown, which cannot fit a
30-second item. This granularity fix addresses test-contract budget composition,
not a diagnosis of an observed process hang. The original unknown-cause timeout
tracking remains open; an isolated or instrumented pass is not flake acceptance.

```powershell
python tools\run-plugin-tests.py agent-dispatch -k native_cli_category_view_contract
python tools\run-plugin-tests.py agent-dispatch --guards -k one_cold_cli_boundary
```

### Restricted supervisor Docker acceptance

`plugins/agent-dispatch/tests/test_restricted_supervisor_docker.py` is opt-in.
It uses a disposable, test-owned host Bridge/supervisor, a real central service
container and a separate restricted worker on an internal network. No installed
service, live account, shared Docker socket mount or cloud token is used.
The offline ACP witness creates its actual native identity, then starts,
reads, reports progress and either completes or abandons its own task through
authenticated HTTP, in independent fresh-container cases.
It must reject cross-task, fleet-route and signature-tampering attempts, and
observe protected task-file rotation by the trusted supervisor.
Acceptance also waits for the witness's final proof after its terminal
revocation check, not merely for the central task row to become completed.
An older matching-label task with unmet capabilities must be deferred without
an agent, allowing the subsequent eligible task to run in the same lane.

Build the central image from the candidate source with
`services/dispatch/Dockerfile`, using the environment's approved package-index
configuration. Prepare the `agent-dispatch`, `agent-bridge` and
`agent-containers` test runtimes through `tools/run-plugin-tests.py --prepare-only`.
Set `AGENT_DISPATCH_RESTRICTED_E2E=1`,
`AGENT_DISPATCH_E2E_CENTRAL_IMAGE` to that image, and
`AGENT_DISPATCH_E2E_BRIDGE_PYTHON` / `AGENT_DISPATCH_E2E_PROVIDER_PYTHON` to the
prepared Bridge/provider interpreters. Then run:

```powershell
python tools\run-plugin-tests.py agent-dispatch --test-file tests\test_restricted_supervisor_docker.py
```

For bounded execution, select `-k complete` and `-k abandon` in separate runner
invocations, each with the original 300-second item / 360-second aggregate
limits. The [host-driven clean-room lane](tools/clean-room/restricted-dispatch/README.md)
does this automatically and retains a structured report plus both cases'
proofs outside the checkout.

The fixture owns its temporary Bridge state, provider runtime copy, containers
and networks, and tears them down. Provider discovery uses an isolated legacy
manifest to exercise its supported compatibility path without changing real
plugin activation. The test is programmatic transport/authority acceptance,
not a model evaluation or a claim of automatic pool placement.
For timeout diagnosis, `AGENT_DISPATCH_E2E_EVIDENCE_DIR` may name a new absolute
directory under an ignored local test-output root. The fixture refuses reuse
and retains timestamped Bridge diagnostics and operation-label-only stage
timing, without writing provider stdin or task credentials into the stage log.
All diagnostics remain inside the unchanged test and subprocess deadlines.

### Consented Windows elevation (Agent Conduct Guidance)

`tools/run-plugin-tests.py agent-conduct-guidance --test-file tests/test_windows_elevation.py`
covers exact terminal IDs, incomplete/stale/success-shaped failure refusal,
actual stdout/stderr, argument quoting, native exit codes, script drift and
owned-Job deadlines. Broker failure controls replace `Start-Process`, so this
selection never requests UAC or registers a task.

On a quiescent, active same-user Windows desktop, set
`AGENT_CONDUCT_TEST_INTERACTIVE_ELEVATION=1` and select
`tests/test_windows_elevation_scheduler.py` through that same runner. Its two
bounded cycles use private directories, real limited interactive tasks and the
harmless probe. They observe visible console windows and foreground transitions,
require matching `probe` receipts and verify exact task cleanup. They never
request UAC and do not qualify administrator execution.
Window qualification first requires the baseline and watcher thread to match
the worker's recorded session, window station and desktop. An unbound observer
explicitly reports both window and focus observation unavailable; a nonempty
`EnumWindows` result alone is not qualification for the selected desktop.
The watcher takes its first snapshot before signaling readiness and propagates
startup or later observation failures to the test. Its three synthetic observer
controls run without desktop opt-in; empty or truncated observations cannot pass.
Foreground qualification additionally requires a non-null observable foreground window.
A headless caller can enumerate visible windows yet receive a null foreground
even when the operator confirms an unlocked desktop. Such a run emits an
explicit unavailable-observation warning: it does not prove zero focus changes.
Use an observer with access to the interactive foreground or obtain a literal
operator witness of the two harmless cycles before claiming that dimension.
The native fixture allows thirty seconds for queued consent admission, gives
its real child PowerShell ten seconds and bounds the complete command at
twenty-five seconds. A five-second fixture child bound expired in the
combined native run; the separate two-second owned-Job deadline
control and production defaults remain unchanged. Failure diagnostics retain
the log tail rather than hiding the last completed stage behind a reader timeout.
Discovery controls exclude access-denied/unmapped foreign entries while still
requiring positive SID ownership, and preserve unexpected errors. Identity
preflight tests use token elevation type (including linked limited tokens),
not the filtered group list, and reject unsupported alternate-account elevation
before staging or consent.
The worker contract selection also verifies an actual write/replacement denial
between hashing and execution, separate admission expiry while execution time
remains, and retained process-handle proof that long-lived descendants die on
leftover detection and on forced worker exit. These controls are non-elevating
and do not request a UAC dialog.

Actual administrator qualification remains an explicitly approved, on-demand
invocation of the skill's harmless `assets/probe.ps1` with `-ConsentConfirmed`,
a fresh private log and the selected interactive desktop. Require its actual
elevated-token output and matching successful terminal marker; do not count a
limited `probe` result, scheduler result or canceled dialog as that proof.
No ordinary test/CI path prompts a human, changes UAC policy, stores a password,
registers a highest-privilege task or reboots.

### Native Agent Machines lifecycle boundaries

`tools/run-plugin-tests.py agent-machines --test-file tests/test_registry_native.py`
checks DWORD/QWORD convergence against real `reg.exe` hexadecimal output.
It reads the existing Windows `InstallDate`/`InstallTime` values independently
with `winreg`, then exercises dry-run and apply through a query-only runner.
No registry value is created, changed or deleted. Missing Windows prerequisites
skip explicitly; a skip is not native convergence proof. Pure injected-runner
controls remain in `tests/test_resources.py`; required unit CI is unchanged.

`plugins/agent-machines/tests/test_payload_installer_lifecycle_powershell.py`
likewise gives each cleanup outcome, staging/watchdog outcome and output
encoding its own function-scoped sandbox and pytest item. The default remains
an 18-second subprocess bound and the runner's 30-second item budget; combining
those native scenarios into serial loops exceeds the composed budget. The two
Windows PowerShell 5 staging items alone use a 45-second subprocess bound and
a 60-second item budget: measured cold nested hosts/probes consumed roughly
five CPU seconds apiece before reaching the smoke seam. Their fixture watchdog
uses 20 seconds and a 30-second simulated sleeper so it can observe the started
child before proving exit 124. PowerShell 7 staging uses a 30-second subprocess
bound and 45-second item budget, with an eight-second fixture watchdog: its
three-second watchdog demonstrably killed a cold staged child before its smoke
record existed. Other native cases, production deadlines and every
readiness/output/cleanup assertion are unchanged. Run the
native lifecycle file without `--guards`, which excludes these integration
items. An individual subprocess timeout still fails and requires diagnosis;
parameterization does not excuse it.

This lifecycle fixture preserves the upstream in-process governance stub and
its phase diagnostics; it is not an end-to-end governance invocation. Actual
owner admission and native installed first-touch repair are exercised separately
by `test_installation_repair.py`.

The native plan/apply/retry, multi-apply restart-confirmation and independent
restart-batch items in that file have a 60-second native Windows item budget,
retaining 30 seconds elsewhere.
Their several real PowerShell calls exceeded the former 30-second item cap;
an isolated unchanged-bound run took 24 seconds. Production readiness/installer
deadlines and all restart, dependency and confirmation assertions are unchanged.
The plan/apply/retry case also exceeded 30 seconds after adding mandatory
executable admission and re-admission of initially-ready outcomes; its real
process calls and repeated-apply assertions remain intact.
Native CMD path/argument preservation and POSIX conductor-termination tests live
in `test_installation_repair_boundaries.py`; the latter proves descendant cleanup
before lock release for SIGINT and SIGTERM during action execution, spawn
ownership handoff and normal-exit cleanup, plus preservation of caller handlers
and ignored child dispositions. The same file covers executable
admission failure without execution and binding drift during an initially-ready
probe and required-dependent blocker propagation after optional prerequisite
binding refusal; source-content admission is shared by planning and actual invocation.
The admitted executable is passed as an absolute argv path. A native POSIX
relative-PATH case separates the conductor and payload working directories and
places a different executable at the payload-relative path; real plan/apply
must still invoke the admitted interpreter.
POSIX payload commands bind supported `sh`/`bash` shebang interpreters, bypassing
`env` lookup after the working-directory change and refusing unsupported forms.
Both command and script relative-PATH cases exercise real plan/apply. Native
Windows Job teardown confirms zero active members, including an exited
installer's redirected descendant, before releasing the repair lock.
POSIX cleanup likewise waits for transient group members to exit (ignoring
resource-free Linux zombies); an exited-parent regression verifies a redirected
descendant's exclusive file lock is released before repair unlocks. Interpreter
admission preserves the declared absolute path's basename semantics while
binding its resolved executable content, including `sh` symlinked to Bash.
Declared action content is bound independently of payload cache exclusions.
Post-install probes also require fresh owner admission before readiness is
accepted, including the final selected module. Binding drift or cancellation
retains the completed installer result but rejects stale readiness and blocks
dependents. The eight cancellation-boundary and three post-install drift cases
use a 60-second native Windows item budget after a real PowerShell probe exceeded
30 seconds; POSIX item limits and production deadlines remain unchanged.

The first-use items in
`plugins/agent-machines/tests/test_payload_provisioning_powershell.py` separate
stamp refusal from the real failed-provision-then-retry-then-warm sequences.
PS5's retry flows use at most 60 seconds per invocation within a shared
120-second workflow deadline and a 150-second item budget. Measured native
failure/retry/warm chains took 50-80 seconds; a 45-second invocation bound still
expired before the first installer entered in the namespaced cold-host chain.
The single stamp refusal instead uses a 45-second workflow and 60-second item
budget. PowerShell 7's retry/warm cases use a 30-second invocation within a
shared 60-second workflow and a 90-second item budget: a measured cold
legacy invocation exceeded 20 seconds after stamp but before provision.
Its single stamp-refusal case retains the 20-second invocation and
30-second item limits, with a 25-second workflow bound leaving cleanup time.
These are aggregate deadlines, not independent retries that each reset the
workflow budget. Both hosts still verify live progress, complete split
stdout/stderr capture, exact exit codes, actual retry, warm dispatch and cleanup.
Transient processes use the canonical caller-owned Windows Job: a deadline must
close the whole tree before joining readers or closing their streams.

### Central versus local-only authority

`tests/test_client_authority.py` in `agent-dispatch` covers the opt-in
`AGENT_DISPATCH_TASK_AUTHORITY` selector, separate credentials, no automatic
cross-authority fallback, local installer lifecycle, producer/worker routing,
restricted task pins, explicit browse/create authority and presence-only role
admission. The existing CLI discovery fixture has its own distinct name so it
cannot shadow the suite's authentication-command isolation fixture.
Authority-qualified claim providers retain fixed callback target prefixes and
disable read-time provisioning. The existing native consumer-prefix tests also
run under the managed Windows runner: their CMD fixture uses an ordinary path
spelling of the same interpreter file, while preserving every registered
argument, child environment and process boundary.
Native fallback retirement retains the selected CIM creation identity and a
process handle: creation-time validation and termination use that same handle,
never a later PID lookup. Missing, replaced or unqueryable identities refuse
with a warning. The native regression covers replacement of both roots and
descendants, handle disposal and a real test-owned child's stale-identity
refusal followed by matching-identity termination. Runtime existence probes use
literal paths so extended Windows interpreter paths reach these real adapters.

The opt-in native multi-container proof is documented in the
[central/local authority clean-room lane](tools/clean-room/central-local-authority/README.md).
It uses the actual central service, an independent peer database and native
CLI processes inside fresh containers, including a central network partition
without ownership migration. It is not required PR CI or a model evaluation.
The lane's host driver durably preassigns the network and all three container
names (including the UID probe), binds the fixture to that registry and verifies
absence after contained-worker death within the original 360-second budget.
`tests/test_central_authority_clean_room.py` covers report/owner refusals and a
real native containment timeout against a file-backed daemon protocol stub.
That fatal-worker regression is not a real Docker fault-injection proof.

The Index local-serving observation controls run with
`python tools/run-plugin-tests.py agent-index --test-file tests/test_serving_observation.py --test-file tests/native/test_serving_observation_native.py --allow-explicit-tiers`.
The native case launches and settles an isolated host, then verifies its routed
PID through the actual owner probe. The unit controls keep missing, foreign,
changing and unready evidence `unknown`; they do not prove a service cutover
or inactive-host ownership.

The standalone runner accepts the legacy `agent-index-service` and
`agent-dispatch-service` component names as aliases for `services/index` and
`services/dispatch`, respectively. It normalizes aliases before environment
admission, dependency preparation, and smoke/full selection, so a released
promotion workflow can validate a development snapshot after the service move.
New invocations should use the canonical service paths. The native runner
contract executes both aliases in canonical contained fixture environments
from an isolated parent whose pytest dependencies reside outside its interpreter
scheme, exercising the layered dependency layout without installing packages.
Each alias has its own native fixture item and unchanged subprocess budgets.
That pure pytest fixture disables unrelated third-party plugin autoload without
changing the real contained runner or its ownership/environment assertions.
Preparation's disposable override files stay under the repository so retaining
their topology does not expand copied source paths back to a drive-root layout
and exceed Windows' relative-path limit.

`agent-chat-service` resolves to `services/chat` through that same canonical
preparation and environment-admission path. Its dependency-free conversation
core and optional client role have an **on-demand contained standalone
portfolio**, not required native Windows/Linux CI lanes. Current source CI
executes the admitted [unit-only packet](#unit-only-promotion-packet); its green
required aggregate does not qualify this standalone portfolio or either native
client platform. The existing periodic integration portfolio does not currently
enroll `services/chat`.

The separate `tools/test_chat_container_template.py` packet consumes Chat's
portable container contribution through the canonical Fleet design/role-binding
readers and source CLI. Its synthetic support graph checks exact private
actualization, provenance, resources and refusal without installing a runtime
or changing Fleet implementation. Run it with an existing pytest interpreter;
it is not enrolled in the native Chat construction portfolio and does not
qualify an image, private allocation, Gateway connector or Komodo effect.

Portable SQLite, real CLI/provider/loopback HTTP contracts,
process-crash reader-lock recovery and source-independent core/client wheel
installation run within the existing contained standalone harness. A fresh
default-core environment proves the helper is absent; the separate offline
client-role environment installs its declared canonical helper and uses the
actual console after all fixture source trees are removed. Temporary listeners
and credentials are synthetic fixtures, not production authentication, model
wake, plugin activation or managed service deployment.
Auxiliary parser/offline dispatch checks run in-process, without replacing the
real provider/HTTP operations. Actual CLI round trips, installed-console death,
source-independent artifacts and worker-shadow cases retain their real process
boundaries. Capacity setup seeds existing valid records; the actual final
admission, overflow refusal and uncertain-request recovery still use the normal
client and its durable writer at the unchanged 128-record boundary.

The shared role fixture builds four independent wheels and installs three
separate fresh environments with bounded parallel builders. Its source copies
exclude test trees unrelated to wheel construction; package inputs, offline
dependency resolution and source-removal assertions remain real. The healthy
installed console/catalog exchange uses a fixed-route stdio provider forwarding
to the fixture HTTP service. Installed credential/isolated-HTTP, caller death,
worker shadow, uncertain replies, deadlines and raw capture bounds retain their
own actual process cases. Exact CLI argument forwarding is checked cheaply
against the client API rather than repeating those same full-stack operations
for every parser variant. Preserve the real installed join before the
console-death fault; cold startup is not part of its five-second read barrier.

Finite artifact/CLI fixture commands use the published caller-owned process
API with their unchanged 20/30-second bounds and separate raw capture limits.
The artifact fixture passes the supported CWD/environment inputs directly,
removes `PYTHONPATH` and preserves stdin plus separate stdout/stderr without
another Python child adapter. It does not change the test process's global
CWD/environment or implement another cleanup authority. The canonical helper
owns the entire bounded tree; the explicit console-death
test still performs its fault and pinned observations before external cleanup.
The shared first-party library wheels and independent base Chat sdist are built
once in parallel. Metadata-only version variants each receive a fresh wheel
and core/client/service environment set; returning to a previously qualified
version reuses only that exact version's artifacts. The promoted-version
control checks the requested version explicitly. Independent library sdists
are built only when the full construction control requests them. No collected
case or real installed boundary is removed by this fixture reuse.

Chat qualification requires **both lanes on the same immutable candidate**:

```text
python tools/run-standalone-tests.py services/chat --prepare --chat-suite acceptance --timeout 180
python tools/run-standalone-tests.py services/chat --chat-suite full --timeout 600
```

Chat defaults to the acceptance lane and its 180-second allowance; `full`
defaults to 600 seconds. Other consumers keep their existing 600-second
defaults. The expanded artifact/version portfolio exceeded the previous
120-second acceptance allowance; the measured increase retains the entire
acceptance inventory rather than moving cases out of the lane. Per-operation
deadlines, capture bounds and the full allowance are unchanged.
Neither lane substitutes for the other. The acceptance packet runs
the real source-removed installed client, native caller/provider/descendant
death, protocol, authentication, durable data/progress, deadlines and raw
capture contracts, plus explicit local rendezvous refusal, TCP rebinding and
the native POSIX Unix-socket client. The complete lane additionally runs all host and
source-removed service lifecycle cases, with no test removed or marked skipped
by this partition. Existing platform-specific skips still require positive
qualification on their owning platform.

`services/chat/tests/qualification.py` lists every test family and its lane.
Every collected parameter node must map to exactly one family with its retained
parameter count; new, missing or duplicate families/nodes and lost parameter
cases fail qualification instead of silently disappearing.
Each lane reports the same complete membership digest, collected/selected
counts and a parameter-level JSON map, atomically persisted when collection
finishes rather than waiting for the terminal summary. Supply
`--chat-membership-report <new-file>` to retain that map, including the actual
runner and test-process exit codes, after sandbox cleanup. A killed test
process retains its collected map. A failure before completed collection
exports explicit unavailable membership rather than fabricated empty nodes;
reporting never replaces an original nonzero containment result. Missing
membership after a zero exit refuses qualification. Its parent directory must already
exist and an existing report is never overwritten. The
acceptance collection explicitly deselects full-only families, while `full`
executes their union. Direct pytest/partial diagnostics without the runner's
explicit qualification environment remain unqualified and unpartitioned.
Filters and collection/setup-only modes are rejected for either lane.
The runner validates membership even without external report retention:
an early zero exit such as pytest help/version is not qualification.
Fixture-listing modes are also refused. At session completion, actual
passed-call reports and the explicitly allowed Windows skips for POSIX
private-file permissions and the native Unix-socket host must
exactly cover the selected nodes, without duplicates, unexpected skips,
xfails or failing phases. The map records pending/complete/incomplete
execution separately from collection. A zero exit with collection alone
refuses qualification, even if a map exists.

The native caller-death case reuses the process utility's test-only birth-pinned
Windows handle/Linux pidfd observer. It observes the actual installed provider,
its sleeping descendant and the console ancestry alive, then requires them all
to exit before outer observer cleanup and before the existing held-read
watchdog. The test dependency does not enter the core/client runtime wheels.

Typed collaboration adds pure normalization and captured-view-control contracts
to `tools/promotion_unit_tests/test_chat_collaboration.py`, with the exact
five-file import/test closure and production coverage targets enrolled in the
existing unit manifest. These units create no store, provider, process or
listener. Database atomicity, non-consuming pending/thread pages, unchanged
reader progress, strict receipts, lost-response recovery and actual source-removed
installed commands stay in the existing Chat acceptance/full portfolio.
Its family and parameter inventory expands additively. Every original family
remains; the current whole-lane allowances are the 180/600-second bounds above.
The empty-inbox and sparse-thread regressions compare actual SQLite instruction
counts with100 versus5000 unrelated questions/messages, including the viewer's
own ambient traffic and correspondence in another channel. They allow at most1000 extra instructions
while retaining correct pending/page results and unchanged semantic progress.
Pending-backlog cases additionally require the same bound with100 versus5000
unanswered questions and mixed answer/question attention. Settled-history cases
retain the bound for answered, resolved and superseded conversations, proving
candidate examination is limited before retirement filtering. The client follows
empty scan-progress pages within the captured boundary without skipping pending
entries or consuming semantic progress, and rejects stalled/out-of-bound
continuations and mismatched returned counts.

The sharing-race control injects its transient error only after the first
successful real readiness-file read. A stale negative existence probe cannot
bypass injection. Both normal and fault cases retain actual child birth/prefix
checks, crash-before-outer-Job-cleanup proof and the original five-second bound.

The [per-test assessment](efforts/active/agent-chat-graduation/test-portfolio.csv)
and [measured comparison](efforts/active/agent-chat-graduation/README.md#journal)
separate the full222-case snapshot from explicit42/221-case candidates measured
under120s. Later230/236-case measurements and a65-pass/one-skip changed-path
packet are recorded separately in that journal; none is a current full120s
acceptance claim. Those historical results do not retroactively pass either
new lane. The split does not change required CI, per-operation bounds or
containment. Use pytest's built-in JUnit call-duration reporting plus
`--durations=0 --durations-min=0` to retain every case and fixture cost; quote
Windows output paths inside `PYTEST_ADDOPTS` so its argument parser preserves
backslashes. Shared artifact setup must not be charged solely to its first user.
Record the exercised platform and artifact separately. The retained client
candidate consumes the published caller-death API with separate raw capture
budgets. Source release alone does not update a resolved installation.
Qualify fresh installed consumers on both Windows and native Linux;
portable-core or unsupported-mode
control results are not positive native Linux client qualification.

The index-service smoke lane includes offline container-context and explicit
fleet-image profile contracts; neither needs Docker, a model or enrollment.
The real-wheel release test builds the sixth first-party `fleet-manager` wheel
and verifies the actual declared fleet extras, without publishing or installing
those fixture versions. Native schema-1 release behavior remains covered.

Dependency preparation uses the canonical seven-day configured-resolver policy.
Do not replace a failed admission with plain pip or an alternate index. After
changing declarations, run `python tools/check-dependency-age.py`; its explicit
`--ecosystem` lanes are diagnostics, not a replacement for the all-ecosystem CI
gate.

The dependency-age audit retains every source-contract read and reuses an AST
only when its decoded source text is identical within that audit. Changed
evidence is reparsed; containment, read, encoding and syntax failures are not
cached away. Each invocation owns fresh tree storage, with no persistent cache
or change to declaration inventory, metadata requests or age policy.
`tools/test_dependency_age.py` covers parse reuse, mid-audit evidence changes
and failures, and revocation observed by a later audit.

The automatically selected `tools/test_installer_dependency_policy.py` suite
also exercises the deny-local CLI mode without product discovery. Its native
bootstrap fixture runs the real materializer,
removes the original library tree and deliberately corrupts embedded product
identity, then bootstraps a pinned registry wheel into a fresh venv through
real UV. Stricter cutoff, unseasoned artifacts and wrong hashes still refuse.
The ordinary attributed-source path remains covered separately.

Focused policy tests are:

```bash
python -m pytest -q tools/test_dependency_age.py tools/test_installer_dependency_policy.py tools/test_dependency_uv_executable.py tools/test_seasoned_npm_install.py
```

The editable-retention regression runs the real setuptools backend; the test
environment must include `setuptools>=83.0.0` and `wheel`, installed through
`tools/seasoned_uv.py` like CI's guard tooling.
The native UV lane uses a controlled index to exercise a transitive dependency
and an isolated build backend, including newer/missing upload timestamps,
transitive URL refusal and UV's conservative exclusive boundary. The native npm
lane runs configured-registry queries and `npm ci` against a controlled registry;
newer or unknown releases must refuse before artifact download. These helpers
perform no production service mutations.

The standalone runner's retained-resource regressions build real Fleet and
Index wheels with their declared setuptools backend; use a prepared test
environment, not an older host setuptools installation. Installer fixtures
must run from their own attributable payload or isolated working directory so
UV does not discover unrelated checkout metadata. Retained-path assertions
validate source identity, private ownership and content at the physical UV
boundary before normalizing paths for ordering or retry comparisons.
Vault's offline UV fixture builds its native PE directly with the Windows
.NET Framework compiler, preferring Framework64 and falling back to Framework.
Its existing preflight contract forces fresh compilation and executes the real
provider, then proves byte-identical reuse without another compiler invocation.
Headless execution and the 20-second native child deadline remain unchanged.
This fixture does not alter snapshot copying or the separate NoService contract.

Vault's NoService snapshot fixture invokes the actual installer and isolated
runtime root through a PowerShell wrapper with throwing scheduled-task mutation
controls. The existing stamp case proves all five controls refuse mutation,
preserves arguments containing spaces and verifies exact nonzero exit forwarding.
Real snapshot provisioning and compatibility launch retain their original
assertions and deadlines; installer/runtime code and host services are unchanged.

Vault's two snapshot-provisioning integration cases explicitly allow 60 seconds
per item: fixture setup performs one complete four-package installation,
health gate and publication transaction. The remaining items keep the default
30-second limit; suite, child-process and CI budgets are unchanged.
A focused, change-scoped Windows CI job executes both native cases under the
same two-minute suite limit and participates in the required PR aggregate;
the existing POSIX plugin suite remains required independently.
The off-PATH Windows bootstrap fixture creates a real stdlib virtualenv in a
spaced directory and probes its exact executable and prefix with a restricted
PATH before invoking the hook. Copying a base `python.exe` and writing
`pyvenv.cfg` is not a runnable cold interpreter fixture: its DLL dependencies
may be absent, independently of argument forwarding or installation policy.
The full native Fleet check allows 300 seconds on Windows under its unchanged
seven-minute CI job limit; Linux retains 120 seconds. This is a full-suite
budget, not a reduced test selection or a relaxed dependency-admission gate.
Multiline PowerShell steps propagate each native admission or prerequisite-test
failure explicitly before starting later commands.
`tools/test_installer_finalizers.py` exercises production cleanup and exclusive
lock reacquisition in the same still-live Windows PowerShell host.

The pre-commit hook runs staged guards in batches of 32 paths to stay within
Windows child-command limits during large integrations. Every path is checked,
later batches still run after a failure, and any failed batch blocks the commit.
`python -m pytest -q tools/test_pre_commit_batches.py tools/test_hook_interpreter.py tools/test_check_large_files.py`
exercises the real hook, spaced/flag-like paths, batch failures and existing guards.

The review-request workflow's fast contract uses
`node --test tools/test_copilot_review_gate.cjs`. To exercise its exact script
against the bundled `actions/github-script` runtime as well, download
`dist/index.js` from the commit pinned in `workflow-lockdown-guard.yml` into a
task-specific scratch folder and pass its absolute path as
`COPILOT_REVIEW_ACTION_BUNDLE` to that same command. The opt-in test uses
synthetic read/write tokens and an in-memory HTTP adapter; it makes no live API
requests or review writes. This catches bundled dependency/import differences
that an injected unit-test client cannot reproduce.

CI first runs `tools/prepare_bridge_contract_evidence.py` against its explicitly
selected repository URL. If an exact HTTP-47 through HTTP-52 capture checkpoint
is absent, this bounded preparation retains it without moving a branch or changing capture
evidence. Full ordinary history does not include every squash-orphaned commit.
Missing or malformed checkpoints fail rather than waiving closure validation.
The targeted tests cover existing and missing objects, exact remote/revision
selection, failed preparation and a real unreferenced Git commit.

The CI agent-bridge contract guard (`tools/check-agent-bridge-contracts.py`)
requires Git 2.45+ for enforced `GIT_NO_LAZY_FETCH` support; older versions
fail before object reads, including in partial clones. This contributor
validation requirement is stricter than the general installation minimum.
It
uses only local Git evidence. It does not fetch history or access credential
helpers: unavailable historical commits remain opportunistic, while available
commit and content-addressed blob cross-checks remain enforced. Local Git
probes have a 10-second bound and a probe timeout fails the guard. Repeated
object reads are memoized within one validation invocation, with bounded
caches, so fixture fan-out does not multiply identical Git subprocesses. Fetch any
additional history explicitly, using the repository's scoped identity, before
running validation rather than asking the guard to recover it during a push.
The headless-launch guard shares a single file read and AST parse among its
Python rules within each invocation; its rule coverage, syntax-error refusal,
and declarative checks are unchanged. Daemon-only AST analysis runs only in
the adopting plugins' source trees where that rule applies; all other Python
rules still scan every production root. Adoption and file eligibility are
recomputed on every invocation. Each AST visitor resolves its bound dispatch
method once per node type and file, retaining the same traversal and scope
handling without repeated dynamic method lookup.

`tools/run-plugin-tests.py` builds/reuses a cached dev venv per plugin in
external scratch (uses `uv`, so vendored
`[tool.uv.sources]` path deps resolve) and runs `pytest`:

```bash
python tools/run-plugin-tests.py agent-bridge           # one plugin, full suite
python tools/run-plugin-tests.py --changed              # plugins changed vs origin/main
python tools/run-plugin-tests.py --all                  # every plugin with a suite
python tools/run-plugin-tests.py agent-bridge --guards  # just the fast @pytest.mark.guard checks
python tools/run-plugin-tests.py agent-bridge -k picker # pass-through pytest -k filter
```

Both shared runners resolve `AGENT_SCRATCH_ROOT` as an absolute directory,
or use a per-user `ce-<user-key>` directory under the
canonical OS temporary directory when it is unset. OS-provided temporary-parent
aliases are resolved first (including macOS `/var`); explicit linked roots
remain invalid. Relative, file, linked/reparse,
or checkout-internal scratch roots fail explicitly, without falling back or
touching existing checkout-local environments.
On POSIX, the default per-user directory must also be owned by the current
user and grant no group/other access; unsafe existing directories are refused,
never permission-repaired or removed.

A retained `ce-tests-<canonical-checkout-key>/venvs/<platform>/<target>` cache
keeps different checkouts and targets independent; canonical spellings of the
same checkout reuse it. Its `scratch-owner.json` records source ownership and
creation time, not cleanup permission. Disposable runs and dependency overrides
use exclusively allocated short `c-<random>` directories directly under scratch,
with ownership and timestamps in their breadcrumbs and the existing process-aware
cleanup. The shallow paths preserve Unix socket address budgets; choose a short
explicit scratch root when the OS temporary parent itself is too long.
Nested test tools receive a sandbox-local `AGENT_SCRATCH_ROOT`, never the host's
retained scratch root. Pytest's cache and interpreter bytecode caches also
stay in the disposable sandbox. Environment leases, source fingerprints and unreaped-tree
retention remain unchanged. `--list` does not allocate or validate scratch.
Old checkout-root `.test-venvs` and `.test-runs` directories are not migrated;
the runner no longer writes them. Reclaiming them is guided, never a sweep:

1. `python tools/reclaim_legacy_test_dirs.py [--checkout <worktree>]` inventories
   registered worktrees of this clone without changing anything. Each tree is
   `ready` (recognized virtual-environment layout), `needs-decision` (other
   files such as captured evidence, even inside a virtual environment, with a
   sample listed) or `refused` with its reason.
2. Review the set, then record it:
   `--select <dir> [--discard <dir>] --plan-out plan.json`. A `needs-decision`
   tree needs a matching `--discard`, the owner's decision to drop its
   unclassified files. The plan binds each tree's identity and
   metadata fingerprint.
3. Stop test/capture writers for the selected set, then run
   `--apply-plan plan.json --confirm-writers-stopped`. It re-verifies each target, renames it aside,
   re-verifies again and only then deletes it. Failed checks refuse and attempt
   restoration without replacing a newly existing original path. Partial or
   unverifiable removal is not reported as success; retained quarantine paths
   and restoration errors are reported per target.

Only Git-ignored, untracked, link-free trees older than `--min-age-hours`
(default 24) qualify. Socket, FIFO and device entries always refuse, even
with a discard decision. On Linux, a process of yours that cannot be inspected
(for example a non-dumpable `sshd`) refuses a target unless you name it with
`--accept-uninspectable NAME` while planning; the plan records that choice.
Stop test and capture writers for the selected set before applying a plan.
These checks detect known users when they run; they do not prove exclusive use
or protect against concurrent or adversarial mutation. Windows rename detects
sharing violations, not every possible open handle.
Permission failures are reported without changing permissions or blindly
retrying filesystem functions; incomplete removal is reported as partial.
`tools/typecheck.py` keeps its prepared environments under the same
`ce-tests-<canonical-checkout-key>/typecheck/<platform>/` retained cache.
Changing scratch storage starts a separate cache; keep the same setting between
`--prepare-only` and execution. This does not relocate unrelated tools' caches
or caller-selected reports.

For a focused lane, repeat `--test-file tests/test_name.py` with exactly one
named plugin to avoid importing unrelated test modules. The selector accepts
only existing, unlinked Python test files inside that plugin's `tests` tree;
it retains the usual containment and time budgets and fails if no selected
tests run. Combine it with `-k` to preserve a diagnostic lane's exact test
roster without collecting the full plugin suite.

The resident repository-map native lane uses
`agent-worktrees --allow-explicit-tiers --test-file tests/test_repository_map.py
--test-file tests/native/test_repository_map_native.py` with this runner.
Set `REPOSITORY_MAP_DISPATCH_PYTHON` to the separately prepared Dispatch
interpreter for the real cross-distribution catalog case. It uses a private
local server, fresh registry and actual CLI/JSON boundary, with one whole-map
call and no per-repository lookup. Missing that interpreter explicitly skips
only the cross-distribution case. This on-demand native lane does not add a
process matrix to required unit CI or qualify an installed rollout.

Explicit out-of-plugin targets use the same bounded runner and admission lease:
`worktree-manager` selects that product's tests, `payload-invocation` selects
`libs/payload-invocation/tests`, and `runtime-source` selects the source
preparation/identity/native-consumer contracts in `libs/versioned-runtime/tests`.
`installation-context` selects `libs/installation-context/tests`, including
the compiler-free PowerShell adapter. Use the same target with
`tools/run_tests_in_devcontainer.py` for ordinary POSIX fixtures on a WSL host;
synthetic POSIX profiles do not establish native WSL guest identity.
`agent-procutil` selects the shared process primitives in
`libs/agent-procutil/tests`, including explicit native daemon wait-status controls.
`--all` remains scoped to plugin suites.

Native command-lifecycle fixtures materialize the complete installer and its
declared driver dependency closure. The initialized PowerShell host frames its
control replies separately from captured raw stdout, including native JSON.
Refusal comparisons retain every product, command, staging and user-data byte;
only declared driver audit files, the known PowerShell startup cache and Python
bytecode for existing payload modules are accounted separately. Driver requests
must finish nonzero without unresolved execution. Same-size/same-mtime source,
runtime and command mutations remain detected.

The payload-lifecycle controls retain the complete process-helper package,
including its lazy Job-accounting module. Windows deadline and early-parent-exit
checks require an empty owned Job before admission releases. The source-only
test target prepares the update driver and binds its transitive source bodies
to the test-cache key. Real-installer fixtures materialize the complete
execution closure once while retaining each preflight refusal and product-byte
snapshot.

Retained-control origin validation uses the constructor's PEP 610 archive
contract. Empty `archive_info` retains the exact hash-pinned URL and version
binding; an explicit wrong hash, contradictory hash fields or unsupported
archive metadata remains a refusal.

The process SDK's native interpreter controls launch both file-backed and
retained-byte output relays twice from a selected Windows venv whose executable
path exceeds 260 characters, preserving exact output and owned-Job settlement.
The caller-owned launcher also preserves that selected venv's imports, arguments,
streams and exit status over two cycles; bare executable lookup remains supported.

Native development and repair fixtures use the SDK's caller-owned runner with
their existing absolute command limits and eight-MiB per-channel capture bounds.
Timeouts settle the complete owned Job or process group before returning; output
does not renew the command limit. Focused controls retain stream bytes and exit
status and refuse completion while a nested child survives.
The offline source-build controls construct private control and target candidates
through one installation cache, then change a dependency body to require a new
wheel without rewriting the earlier completed control.
Both PowerShell hosts also prove that lifecycle diagnostics arrive before child
completion, once only, with stdout/result framing and failing exit status intact.

The copied-pane boundary controls are an explicit, non-serving native lane:

```text
python tools\run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests\native\test_pane_boundary_native.py --test-file tests\native\test_pane_boundary_posix.py
```

Each host runs its native shell cases and skips the other platform. These use
small synthetic completed prefixes, the real runtime bootstrap and canonical
shell code to check A-start/B-end, removal of A, and quiet absent-handler exit.
Fixture preparation runs the real boundary help check before the timed effectful
calls; first-time full runtime construction is not hidden inside that call budget.
They do not install the full suite, use a real Copilot, qualify terminal
interactivity or prove literal uninstall/reinstall survival. The files stay
outside ordinary unit discovery; pure asset and registry controls remain in
their owning unit suites.

Manager's cold canonical-adopter acceptance has an explicit native lane:

The separate `tools/run-manager-runtime-acceptance.py --contract provider
--root <ignored-output>` packet builds
the actual Manager wheel with its canonical resource hooks, installs it into a
fresh dependency-free environment, removes its disposable build checkout, and
invokes the installed provider through an explicitly prepared native tooling
interpreter. Tooling is a separate disposable environment populated from
Manager's declared development requirements through the canonical seasoned
registry installer, not an editable Manager or Bridge application install.
Tooling preparation and cleanup share the same 300-second group.
The complete-profile/compiler, admission and offline application
lock pipeline runs without mocked producer calls. Synthetic associated
first-party inputs have no registry dependencies; this is not production-feed
transport or the full real Manager dependency closure. Wrong source identity
and corrupt wheel inputs refuse without changing completed output or activating
a runtime. The existing 300-second group, 180-second native child and 30-second
assertion limits remain unchanged. This packet does not qualify the separate
multi-install adapter's existing deadline failure, default routing or rollout.

The focused `tests/native/test_manager_runtime_install.py -k
preparation_completed_units` packet separately connects the real Manager copy
and inventory producers to the existing native installer child observer.
Completed units keep productive work alive beyond the unchanged quiet
allowance; silent work still expires, and failed diagnostic delivery cannot
publish a completed payload. Source bytes remain unchanged. These are isolated
producer/consumer controls, not a full bootstrap, live cutover or rollout.

```text
python tools/run-manager-runtime-acceptance.py --root .validation
```

It uses the existing process-tree containment supervisor and one 300-second
group budget for fresh/legacy-repair fixture construction plus the assertions.
All Manager acceptance contracts retain their sandbox and report its path when
process settlement raises `UnreapedProcessError`, preserving that original error.
Successful runs and settled failures still clean up normally. A retained sandbox
may remain owned by unresolved processes; it is not ordinary failed-test debris.
Run `python -m pytest -q tools/test_run_plugin_tests.py -k manager_acceptance_`
through the bounded test supervisor for the focused wrapper controls. They inject
the unsettled-process error without creating a genuinely unreaped process.
Fixtures are constructed before pytest's unchanged 30-second item checks;
no constructor, dependency policy, package health or failed-selection assertion
is mocked. The assertions run through `run-plugin-tests.py` with explicit
`tests/native/test_manager_runtime_install.py` selection. The ordinary Manager
suite does not recurse into that prepared native lane. Report both ordinary
orchestration/assembly units and this cold native acceptance before publishing
a Manager installation change; neither is a substitute for the other.
The runner's default file discovery honors the existing
`[tool.pytest.ini_options].norecursedirs` declaration before passing explicit file
groups to pytest. `--test-file` deliberately retains access to the prepared
native lane; those tests still require their native driver's fixture data.
The migration fixture requires Git history containing published pre-engine
Manager `0.6.35-dev1` at `9c7a61518540787498c77a284f640c36bbe7d8e4`.
It executes that release's controller against the new payload, witnesses the
old launcher fail with an interpreter-only selection, then enters the new
bootstrap and reuses the same registered command without `uv` on `PATH`.
This is an isolated handoff proof, not a live-host or live registry-age probe;
missing pinned history refuses the lane rather than substituting new code.
The lane also constructs two same-package, distinct generations, imports both
through their isolated installed interpreters, and exercises a real contained
fixture mux cutover to the source-qualified target. Injected failures before and
after the second selector replacement prove both selector states and runtime
receipts survive, with no failed-update cutover. Fixture mux processes suppress
production breakaway and are terminated/waited within the containing test.
The installed-generation update witness forbids installer IPC to the incumbent
and predecessor recovery/termination. It waits for the real old daemon to exit
cleanly and for its successor to acquire the work lease, while preserving all
incumbent runtime file hashes. This Manager-only proof does not qualify the
assembled suite's ordinary-update/repair paths or scheduled registrations.

Whole-suite replacement has a separate manual Windows VM portfolio:
`installation-context-full.yml`, `portfolio=manager-suite`. It runs normal
update and repair in independent disposable GitHub-hosted Windows jobs:

```text
python tools/run-manager-suite-acceptance.py --root .v --mode normal
python tools/run-manager-suite-acceptance.py --root .v --mode repair
```

These commands refuse local execution before creating fixtures. Each case
constructs V1 and V2 through canonical materialization, starts the applicable
V1 portfolio, and calls the real installed Manager for normal updates or its
native bootstrap repair entrypoint for repair, against the same default HOME
and durable data. The private repaired-engine handoff is not a substitute for
constructing the fresh Manager first. Controlled acquisition replaces only external artifact
delivery and credentials; the planner, constructors, launchers and scheduled
registrations remain real. Missing prerequisite or resident-attribution evidence
fails acceptance rather than reducing the portfolio.
The disposable project explicitly opts Vault into runtime participation with
`enabled: true`; plugin visibility alone is not activation. This does not alter
the default inactive behavior or any operator configuration.

If baseline preparation reports a Vault activation refusal, the fixture also
runs the retained payload's read-only activation reader under a bounded native
owner. Caught payload exceptions and a refused configuration owner's bounded
stderr become diagnostics without changing the reader, opt-in, original
failure or settlement requirements.
Missing payloads and settled probe timeouts/output-limit failures are secondary
diagnostics: the original assertion or `check=False` result remains intact.
Unconfirmed execution or cleanup still propagates with its exact process/Job
custody rather than being reduced to a diagnostic result.

The Index host role is written as YAML readable by both native and
dependency-free role resolvers. Preparation explicitly provisions its full
independent engine through the native installer; ordinary Manager updates
preserve that separately managed engine rather than provisioning it implicitly.
Its exact native process identity, running version, healthy readiness and sealed
slot are attested as retained; it is not misclassified as a replaced runtime.
After cold setup, the Codespaces owner starts through its installed on-demand
ensure API, retaining the controlled caller's delivery environment, with
installed liveness observed before recording V1. Its real native scheduled
registration is retained and its current-runtime resolver is separately verified.
Worktrees' V1 monitor is explicitly ensured through its installed on-demand API;
installation alone intentionally need not start an absent monitor. A
fixture-owned Codespaces tenant hold, periodic Worktrees list demand and an
authenticated Manager subscriber keep these on-demand residents needed through
the final routing and exclusive-work probes without changing their idle policy. The Manager subscriber
follows the current route and releases the prior and final generations.
This synthetic demand does not qualify a real remote SSH channel.
Settlement releases the fixture tenant,
uses Codespaces' generation-bound `owner --stop` and waits for normal channel
cleanup before removing the exact owned scheduled registration. Task termination
alone is not daemon-exit evidence. Idle policy, deadlines and final birth-verified
resident-exit assertions are unchanged.
Observers retain each successor's native handle through the final sample rather
than reopening a possibly exited process ID after the update. Failure artifacts
include bounded tails of the fixture's known Bridge and Codespaces daemon logs;
durable application data and unrelated files are not collected.
Bridge's first observed incumbent exit also snapshots those bounded logs before
successor startup can truncate the shared output files.
Strict V1 snapshots retain hashes, size, modification time and native file
identity for every file. Failure diagnostics name added, removed and changed
paths after update and after settlement, with exact counts and bounded metadata
samples rather than file contents. No cache-file exemption or resealing converts
a changed immutable slot into retained evidence.
The outer VM coordinator retains a bounded process trace beginning immediately
before baseline observation and sealing, through exercise and settlement.
Construction noise does not consume its at-most-512 process birth identities with component,
module and cache-flag observations. Raw command lines and argument values are
not retained. Readiness and the final stop/report are checked; scan failures
remain explicit failures. This trace supplies candidate launch evidence, not
proof that a particular process wrote a changed file.
The assembled Manager update retains a finite stderr allowance of 4 MiB per
declared component plus Manager, rather than applying one component's 4 MiB
capture to the entire suite. Other fixture calls and stdout keep their existing
limits; execution deadlines and per-unit source progress remain unchanged.

Cold preparation has an 1800-second owned-process deadline; exercise has 900 seconds.
Assertions retain a separate 300-second runner boundary and the usual pytest
item limit. Settlement runs independently after success, stage failure or
timeout. The disposable VM owns the outer scheduled-registration and explicit
product-daemon lifetime boundary; this flow does not run inside a restrictive
local test Job. The workflow retains synthetic diagnostics and cannot pass on
failed or missing settlement. Neither this test flow nor a passing component
test asserts that whole-suite acceptance has already passed.

The bootstrap RedirectionGuard controls create links as an unprivileged user:
Windows deliberately [trusts administrator-created links](https://www.microsoft.com/en-us/msrc/blog/2025/06/redirectionguard-mitigating-unsafe-junction-traversal-in-windows).
An elevated test host
uses a restricted same-user thread token only during fixture link creation,
then restores its original identity, including on failure. File-symlink cases
retain their developer-mode/privilege prerequisite; guard and physical-path
assertions are not relaxed.

Prepared-source Manager selection/recovery has its own bounded native contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract source
```

Its four existing constructions run as separately process-contained commands
sharing one 1,800-second aggregate deadline with the final assertions. Every
command receives only the remaining aggregate allowance, not a fresh timeout;
an individual stage may use more than 300 seconds. Assertions retain their
30-second item limits.
Production constructor/inactivity limits and all other native contracts remain
unchanged. No cold builds are added to the original runtime lane or required
PR CI. One borrowed test-environment lease and retention-aware sandbox span
every group; a failed step stops later work and unsettled processes retain the
sandbox. Fixture receipts carry the original bytes/file-identity witnesses
between steps. The source lane uses
an automatically cleaned OS-temporary sandbox, avoiding Windows path-length
failures from source-plus-repair identities below a deep checkout. It constructs
real non-editable same-package source generations. Source fixtures use the
canonical source materializer before identity stamping, including nested
first-party references; the legacy bootstrap copy is not a prepared-source
assembly. Manager's explicit Dev
self-update acquires a clean standalone fixture's merged `dev` tip using its
own trusted preparer, independently of a different service-source policy.
The first apply runs the actual standalone Python bootstrap adapter under an
isolated base interpreter without site packages. Its cold construction uses the
same four constructions rather than an additional fixture or deadline.
The lane refuses a subsequently dirtied acquisition checkout without changing
the installed selection through both self-update and bootstrap, while an already
qualified exact payload remains reusable without resolving that dirty checkout.
It repairs an intentionally
damaged installed module into a distinct slot, then reuses that exact healthy
repair. Dry-run reports the actual source selection; the damaged slot and its
completion/origin receipts remain unchanged.
Both the repaired interpreter and the restored previous interpreter must resolve
the same fixture-default data root without a root override, report their actual
installed prefix/module/generation, and read the original SQLite witness and
schema version. Config, database bytes/file identity and global command contents
are preserved. This is synthetic compatible-state continuity, not qualification
of production data migrations, the shell bootstrap's download/prerequisite
transport, service-source engine repair or host rollout.

Independent service-source engine repair has a separate native contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract engine-source
```

It uses the same 300-second group and 30-second assertion budgets in an
automatically cleaned OS-temporary sandbox.
The existing account-isolated repair fixture supplies the registered incumbent;
only native Copilot payload acquisition is substituted with its local published
fixture. Manager acquires merged Dev, prepares it through trusted tools, runs
the actual native runtime-only repair and queries the constructed engine's real
inventory. It first constructs the exact source engine, damages an installed
module, then invokes the same pinned native command twice: canonical distinct
repair must preserve the damaged slot, and the next call must reuse the healthy
repair without changing selection. This includes the canonical repair owner's
control/candidate constructions, not an in-place edit or a fake successful
native result. Upstream Dev advances between repair and inventory: the complete
subsequent suite packet must still name the original commit/tree and artifact.
The final suite process is intercepted to check that packet and exact selected
argv; this does not exercise marketplace refresh or the full update portfolio.
The installed interpreter and generated global command execute, the installed
module belongs to the selected non-editable slot, and the original default-root
SQLite witness/schema, config/database bytes and file identity are retained.
Command ownership remains the incumbent's. This is not production-data migration,
consumer cutover, namespaced repair or host rollout evidence.
The coupled mode/recovery units additionally check both enabled and disabled
adopted-Manager recovery through the source-health fallback, CLI parser,
control-process argv and settlement, alongside the existing adopted-Manager
recovery controls. The native runtime-only lane keeps that recovery disabled.

The archived normal-updater consumer has its own explicit Windows contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract updater
```

It prepares the actual Worktrees `1.24.91-dev1` release and broken Manager
`0.6.35-dev1`, then invokes the old payload's normal project `update` command
without force or Manager-bypass flags. A staged-index snapshot is materialized
through the release helpers into a private published-like Git artifact.
Copilot delivery and credential transport are controlled fixture processes;
Git checkout, canonical package construction, the fresh-interpreter handoff,
registered Manager launch and the private Windows Terminal profile writer are
real. The resulting profile must report `verified_current`.
Archived-fixture preparation and normal-update consumption each retain a
300-second containment boundary. A separate 300-second admission/concurrent-caller
phase reuses those constructed fixtures: it checks healthy reuse without source
parsing, unadopted no-op, failed source pin and denied credentials with prior
selection preserved, repair of unexecutable legacy and completed canonical
interpreters, and an in-flight old caller skipping installation followed by
recovery on the next normal invocation.
Pytest items retain their 30-second limit.
This proves the updater migration contract, not release qualification or a
live-host rollout.

Supported Manager removal has its own explicit native contract:

The admitted pure packet also covers batch-custody input refusal in
`tools/promotion_unit_tests/test_manager_uninstall.py`, before filesystem
ownership can be acquired. Real lease contention and removal remain in the
separate functional/native portfolios, not the unit-only CI packet.

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract uninstall
```

It prepares a real standalone payload and canonical installed Manager in a
redirected disposable home, then checks an actual installed daemon start,
health request and graceful shutdown before non-mutating preview, installed-self
refusal, actual external removal, repeated absence and actual reinstall.
The daemon and Installation & Updates list command execute their production
child argv without an inherited bytecode suppression setting; both must leave
the sealed runtime unchanged.
The removal exercise then creates a real post-construction CPython cache beside
an inventory-owned source and removes it without rewriting the sealed inventory.
Ordinary removal controls cover timestamp and checked/unchecked hash headers,
optimization tags, another interpreter tag, unknown artifacts and plan-time races.
Installed package/payload hashes and origin evidence must match; runtime slots,
selectors and owned frontdoors/registration must disappear while durable-state
bytes remain. No synthetic inventory, constructor mock or manual fixture
deletion substitutes for removal. Initial installed preparation and the
removal/reinstall exercise use separate existing 300-second native groups:
each requires a real cold construction, with a 240-second command bound inside
its group. Ordinary pytest items retain 30 seconds. Other native contracts,
production deadlines and required PR CI are unchanged.
The disposable daemon is caller-contained and fully settled before removal.
This operation does not launch a mux or Copilot, qualify interactive session
survival, or authorize a host uninstall/rollout.
The ordinary Manager suite excludes this prepared lane.
The manual `installation-context-full.yml` portfolio `manager-uninstall`
executes the same owner on a fresh Windows runner with Python 3.12, read-only
repository permissions, exact checked-out source verification and a 15-minute
setup/job envelope. Its native groups retain their 300-second boundaries.
It is not part of required PR CI or the default scheduled portfolio.

Retained mux shell callbacks have a separate, independently contained contract:

```text
python tools/run-manager-runtime-acceptance.py --root .validation --contract callbacks
```

This does not replace or split the original ten runtime checks. Each contract
keeps the existing 300-second aggregate budget and 30-second pytest item limit.
The callback contract cold-constructs a real package while recovering an
unchanged selector whose old slot is missing, then executes the actual retained
PowerShell/Bash registration and removal bodies with `uv` absent. It observes
real registry updates/tombstones against a private live rendezvous and an owned
fake engine, not command-string assertions. A distinct contract avoids adding
another cold construction plus consumer subprocesses to the original group,
whose measured native Windows execution is already close to its unchanged bound.
That contract also removes only the selected marker from an otherwise healthy
constructed installation: dry-run must plan repair, then apply must restore the
selection using real installed health and unchanged origin/completion receipts.
An unexpected constructor call fails the test rather than rebuilding silently.
The same prepared installation is reused for empty and whitespace-only selector
repair; it is not rebuilt for each state. `tests/test_manager_selector_states.py`
uses real canonical file publication/recovery for both selector fields, including
faults before/after the second replacement, exact blank-byte/absence rollback,
held runtime/publication custody, and unchanged invalid-name/target refusal.
`tests/test_companion_popup_runtime.py` separately executes the generated Ctrl+K
popup command through the Windows command shell against a synthetic installed
fixture CLI and real retention bootstrap (not a package-construction proof).
It checks paths with spaces, exact Companion argv, the held lease, target prefix,
root binding, stdin/stdout/stderr and exit status without opening a popup/TUI.
Source and legacy entries execute through an owned `uv` stand-in. Since the
PowerShell runtime resolver is shared, also run the real callback contract after
changing it; the original ten-case cold contract need not repeat for popup text
and equivalent resolver extraction alone.
The payload suite's PowerShell contracts require a real `pwsh`; when it is absent
from the isolation container, run that target through the host's contained runner
rather than counting skipped PowerShell contracts as validation.
On native Windows, the generator file's complete portfolio can run in two
complementary bounded selections: `-k "agent_machines"` and
`-k "not agent_machines"`. Account for both test/skip totals; neither alone
is complete coverage. These keep the repeated native governance contracts
within the existing per-run budget without extending any item deadline.
Disposable Worktrees copies retain the complete runtime/dependency graph
where consumed, but do not write bytecode that no later fixture reuses.

For an explicit clean/reprepare without running tests, use the existing runner:

```bash
python tools/run-plugin-tests.py <target> --reinstall --prepare-only
```

This cleans only that target's current-platform test environment in this
checkout, after acquiring its normal lease. A busy environment refuses or waits
under `--admission-wait`; linked cache roots refuse before cleanup. Other
targets, platforms, checkouts, installed runtimes and the global UV cache are
untouched. Legacy checkout-root `.test-venvs` trees are reclaimed separately
with `tools/reclaim_legacy_test_dirs.py`; purging UV's cache is never required.

On Windows, cache inspection/removal and disposable sandbox cleanup use one
native extended-path spelling so deep private UV wheel paths do not become
false missing-file/nonempty-directory failures. Drive, UNC and already-native
paths retain their targets; POSIX paths are unchanged. This changes neither
machine long-path settings nor ownership: rebuild keeps the leased root,
linked roots/ancestors refuse, child junctions are unlinked without traversal,
and failed cleanup cannot publish a ready dependency fingerprint. Disposable
group cleanup still follows confirmed process reaping; an unreaped scope
retains its sandbox, including overlong files.

> **Cached local dependencies are source-bound, not editable.** Path dependencies
> install as retained copies in the source-bound scratch venv. The runner
> fingerprints their complete canonical/transitive local source graph, explicit
> companion projects and retained standalone build resources, not just package
> versions or manifests. Changed, added or removed source files trigger a rebuild;
> generated build/bytecode/cache files and unrelated projects do not.
> Both automatic source drift and explicit `--reinstall` forward reinstall to UV,
> so rebuilding cannot silently reuse an old same-version local-path wheel.
> Rebuild cleanup preserves the leased environment directory and unlinks child
> junctions without traversing their targets, including under Python 3.11.
> Native GUI-parent probes capture child diagnostics even when `pythonw -I`
> cannot import the installed dependency API; an opaque exit code is not
> windowless-launch evidence. This also means a shared
> lib's **own** test suite (its top-level `libs/<lib>/tests/`, as opposed to
> any plugin's `tests/`) is never run by this runner at all — it belongs to
> no single plugin. Validate it directly against one specific vendored copy by
> shadowing the venv's stale install on `PYTHONPATH` (the `SSH_MANAGER_WINDOWS_PROXY_TEST`
> example just below does exactly this). See CONTRIBUTING.md's *Hot-patching a
> deployed venv for fast pre-merge iteration* gotcha for the fuller loop,
> including validating a fix against the real deployed CLI (not just this test
> venv) before a PR merges.

Every invocation is contained by default. The runner redirects user, Copilot,
plugin, XDG, and temporary state beneath a per-run sandbox; owns the pytest
process job/group and its ordinary descendants; and enforces budgets at three
time scales: 30 seconds per test, 300 seconds per sequential 25-file sub-suite,
and 900 seconds across the plugin. Each sub-suite also defaults to 128
processes, 4096 MiB of process-tree memory, and 2048 MiB of temporary storage.
The pytest group parent is created with mode `0700` on POSIX, so an inherited
shared-write umask cannot expose the fixture namespace.
The runner resolves its temporary parent before allocating that owned sandbox,
so Windows short TEMP aliases do not look like redirected daemon registries.
Daemon-registry redirection and ownership refusals remain unchanged.
PowerShell module discovery uses a sandbox-owned `PSModulePath` by default,
so inherited host modules cannot autoload into a test. Core PowerShell commands
and native application lookup remain available. Only the existing explicit
host-state tier preserves the caller's module path; ordinary contained runs
do not modify the parent environment.
Changed-plugin selection always includes both `copilot-extensions-harness` and
`customizing-copilot`, whose suites inspect other plugins' session-context
contracts. An admission-wrapper change must therefore pass the real whole-suite
hook-composition consumer before merge, not first encounter it in post-merge
CI. This scheduling adds no new policy exception or scanner allowlist.

Temporary-storage accounting uses a fresh `scandir` walk per check and retains
each entry's native metadata instead of issuing repeated path-stat calls.
On Windows, each check has at most two readers and at most 32 directories
per submitted batch; POSIX traversal remains serial. Each invocation measures
growth and removal afresh, without caching totals between checks. Reader
failures propagate, and cancellation or failure waits for active readers to
close before returning so the caller can safely clean up its sandbox.
Symlinks remain excluded; counting and process-tree termination at the storage
ceiling are covered by native containment tests.

Sandbox cleanup requires verified process-tree termination, not just the
command's exit. The POSIX controller reports status through a private pipe
and keeps its PID allocated until teardown; the owner verifies its group and
session identity before each signal and confirms no live members remain.
The controller stays live through TERM grace and is excluded only from the
grace-period member count. An identity-checked final SIGKILL is mandatory even
when an earlier census appears empty; post-kill membership must reach zero.
An independent owner pipe stays open only in the runner. EOF makes an abandoned
controller terminate and then kill its own still-pinned group, both during
command execution and after reporting status; abrupt runner death cannot leave
the controller waiting forever.
Failed membership observation also releases that pipe before waiting for the
controller, allowing its independent reaper to escalate while the original
unreaped error still requires sandbox retention.
Zombies still count toward process admission but cannot hold sandbox files.
Windows teardown explicitly terminates the Job Object and waits for zero
active members before closing its handle. Uncertain termination raises
`UnreapedProcessError`; plugin and standalone runners and the parallel coverage
collector retain their sandbox, detach automatic cleanup and report its path
rather than deleting potentially owned files. Normal exit, deadline and
cancellation retain the same reaping requirement and existing resource limits.
If the environment audit also fails, the unreaped error retains precedence
and reports both failures; an audit exception cannot authorize file deletion.

Full dispatch execution in the CI smoke matrix (push, manual and promotion)
distributes its complete file inventory across three independent jobs.
The PR owner uses two complete/disjoint jobs. Its default sequential process
group is ten files rather than twenty-five: the hosted second assignment
exceeded the unchanged 300-second group budget before completing its last
cases. Each assigned file still runs exactly once, with no marker/filter
change. The runner's explicit file-group limit still wins; other plugins keep
their existing grouping, and all item, group, plugin and job deadlines remain
unchanged.
`tools/ci_plugin_partitions.py` assigns
whole canonical 25-file cohorts round-robin and supplies explicit `--test-file`
arguments to the same contained runner, which may subdivide an assigned cohort
into tighter sequential process groups. Every file belongs to exactly one
partition, and every full-suite partition remains required by the applicable aggregate;
a failed or cancelled partition fails the gate. Local unfiltered runs remain
sequential. Item, sub-suite, plugin and 12-minute job deadlines are unchanged.
This addresses #6052's recurrence where all 5,933 tests passed but aggregate
reporting/cleanup reached the job deadline. Partition-contract tests exercise
the actual runner's file-selection boundary, native helper CLI, complete
inventory and workflow/gate wiring; empty or invalid partitions refuse rather
than silently falling back to a different suite.
These contracts execute in both required native test-runner lanes, independently
of the selected plugin smoke matrix. Their changed-path selectors cover the
partition helper/tests, contained runner/tests and CI workflow, so scheduling-only
changes cannot bypass the complete/disjoint coverage and runner-compatibility
checks.
Promotion qualification derives every required partition name from the helper's
literal `_SMOKE_PARTITIONS` mapping without executing candidate Python. Its
receipt binds the normalized workflow and helper bytes. Missing, unsuccessful,
duplicate or unsupported rows still refuse qualification; both native promotion
contract lanes also select changes to this scheduling helper.
The development regression
`test_isolated_matrix_and_files_cli_need_only_the_single_script` copies only the
partition script and runs ordinary `matrix`/`files` commands with `python -I -S`,
without repository helpers or site packages. It checks the matrix and exact
second-group file arguments using synthetic test paths. This native-process
witness does not expand required unit-only CI or qualify periodic-container
enrollment.
The PR-only coverage-selected dispatch lane below instead requests one matrix
row through `--single-job-plugin agent-dispatch`. Selection and its evidence
artifact run once, not once per complete-suite partition. The explicit matrix
override leaves every other plugin and the default full partition policy intact.
Qualification requires the complete canonical execution step, including its
partition environment and file selection. Appended collection/filter flags,
shell failure masking and changed partition selection refuse qualification.

Agent-index's fleet-ingress tests select the same-checkout `fleet-manager[auth]`
distribution explicitly during development preparation. Its runtime metadata
keeps an ordinary versioned optional dependency, not an escaping standalone
project path or a vendored Gateway copy. The runner fingerprints that project's
dependency metadata and uses an editable test install so source changes remain
visible. The canonical dependency-seasoning adapter retains the shared
`libs/versioned-runtime` build resources needed by that standalone distribution,
just as standalone test preparation does; it does not bypass policy or build
from a mutable source fallback.

The independent `libs/docker-resource-producer/tests` suite uses only a fake
Docker process and temporary JSON state, never a live daemon. Current required
PR CI admits only the source-bound unit packet and does not run this independent
suite. A green PR gate is not evidence that these fixtures ran. Run the suite
explicitly under the existing test containment, including Linux on the declared
minimum Python 3.10 for POSIX-only cases; record unavailable platform coverage.
Isolated dependency provisioning uses the canonical seasoned UV adapter.
The library also declares the native seven-day UV cutoff for direct consumers.
Use the selected interpreter explicitly without replacing the caller's Python
environment. This catches fractional-timestamp and
optional TLS client-identity compatibility; it is not live Docker lifecycle
acceptance.

On Windows, contained runs also set the installer test-mode contract
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`. Conforming installers virtualize
persistent User/Machine environment reads and writes to Process scope. The
runner separately snapshots both registry environment keys and fails the
sub-suite if it detects drift, so a missed adapter cannot silently alter host
PATH or other persistent environment state. It deliberately does not roll the
key back because another process may have made a legitimate concurrent edit.
Installer subprocesses launched by a direct pytest invocation receive the same
prevention through pytest's inherited `PYTEST_CURRENT_TEST` marker.

Codespaces PowerShell installer regressions additionally use a fixture-only
executable search path and explicit batch exit-code forwarding. Temporary
payloads carry test guards through self-staging and first-use provisioning:
they require the fake uv resolver, reject uv network bootstrap and service
mutations, and expose attempted forbidden effects as assertion failures.
User-root redirection alone does not isolate executable discovery or scheduled
tasks. The signed-venv health/exit-code matrix uses simulated signatures on both
platforms; native signature-policy behavior still requires Windows validation.
Isolated runtime-helper fixtures must copy `versioned_runtime.py` with its
adjacent `versioned_runtime_leases.py` and `versioned_runtime_construction.py`.
Standalone wheel/sdist tests bind every member to canonical bytes and wheel
RECORD; a missing companion must fail rather than yield a partial artifact.

`plugins/agent-index/tests/test_payload_hash_stability.py` copies a cold helper
closure without bytecode caches. Its portable CLI, native Bash role probe and
PowerShell 5/7 construction checks compare every payload file before and after
actual execution, and require real completion evidence. Windows uses Git Bash,
not WSL, for the Bash case. Run it directly with pytest; it needs neither an
Index service nor package downloads.

Dependency fixtures must also preserve source attribution. Stage matching
publisher/project metadata and the declared local library graph at the real
helper location; use the existing release materializers before stamping a
payload rather than retaining development paths that escape its root.
Direct policy API callers attest the fixture creator's fixed repository root,
not a root derived from the requested package argument. Keep admission active:
do not mock ownership discovery or widen trust to make a fixture pass.

The Codespaces module-launch matrix is an opt-in T3 contract:
`python tools/run-plugin-tests.py agent-codespaces --allow-explicit-tiers -k activation_requires_installed_module_launch`.
A focused completed-namespace pre-construction refusal remains in the ordinary
installer suite. For exact-candidate Linux provisioning and forced-update
refusal on a disposable fresh machine, run
`tools/clean-room/run.ps1 -Scenario agent-codespaces-launch-health -HarnessMount <candidate> -NameSuffix <unique> -Then down`.
This agent-free scenario requires no credentials or live CodeSpace and tests
the actual installed `python -I -m agent_codespaces --help` entrypoint.

Agent SSH's cold first-use snapshot provisioning restores real network
dependencies and is an explicit T3 check, not a default fast unit case.
Its pytest item budget remains 30 seconds; an unavailable budget is a failed
or unexercised native lane, never an installation pass. The agent-free
`tools/clean-room/scenarios/agent-ssh-snapshot` lane exercises the exact local
candidate with separate bounded stamp/provision phases, removes its original
source copy, and reads machine-transport from the actual fresh runtime:
`tools/clean-room/run.ps1 -Scenario agent-ssh-snapshot -HarnessMount <candidate>
-NameSuffix <unique> -Then down`. Supply the approved feed explicitly on a
governed host. This Linux-container proof does not claim Windows signature or
live SSH/WSL deployment coverage.

```bash
python tools/run-plugin-tests.py agent-dispatch \
  --test-timeout 20 --subsuite-timeout 180 --plugin-timeout 600 \
  --max-files-per-sub-suite 15 \
  --max-processes 32 --max-memory-mb 2048 --max-temp-mb 512
```

Filtered (`-k`) and guard-only selections normally run as one contained
sub-suite rather than repeatedly importing unrelated file groups.

On Windows, the normal runner automatically partitions
`agent-codespaces/tests/test_installer_engine_adoption.py` within its existing
file-group plan: healthy admission and retry/fatal admission/orchestration run
separately for each editable mode, with the non-boolean admission-spy negative
controls in the editable failure group; standalone
snapshot/development-generation lifecycle; and an exhaustive complement for
everything else, including newly collected tests. The selectors are disjoint;
other files retain their original groups and are not recollected for each
partition. This also applies to `--test-file` and intersects every partition
with a caller's `-k`; `--guards` remains an additional marker restriction.
Non-Windows and unrelated suites retain their existing grouping.
Exact-node selection manifests retain their existing collection plan and
fail-closed missing-node admission rather than adding a keyword restriction.
The Windows standalone snapshot fixture supplies a real pipless stdlib venv
interpreter on its isolated PATH: a batch-only `py` shim cannot satisfy the
installer's windowless native bootstrap invocation.

This is automated partitioning, not a manual-selector or timeout exception.
Each group retains the default 300-second ceiling, remaining plugin time
still caps every group within the default 900-second aggregate, and the
30-second runner request, existing pytest markers and 45-second native fixture
subprocess deadlines are unchanged. An unexpectedly empty unfiltered partition
fails. Filtered empty groups throughout an automatically partitioned plan may
be traversed, including other files preceding the installer partitions, but
cannot turn an entirely empty explicit-file selection into success; only the
existing non-explicit guard-only empty-suite convention remains a skip.

Within these automatic Codespaces partitions, pytest assertion exit `1` is sticky:
later partitions still execute, but later passes cannot turn the plugin result
green (#6917). Ordinary unpartitioned groups retain their fail-fast behavior.

The native snapshot-copy contract is additionally exercised by
`agent-codespaces/tests/test_snapshot_copy_contract.py` in canonical-source and
real-materialized layouts. It compares fresh-destination copies with native
`Copy-Item`, including complete binary/empty/hidden/read-only contents and
root-reparse behavior; deterministic copy failures and linked canonical-input
symbolic-link refusals must remain non-successful. Windows junction non-refusal
is characterized rather than silently changed into a new policy. An unavailable
actual symbolic-link capability blocks that required check; a junction, mock,
privilege change or skip is not a substitute. Isolated no-op, omitted-entry, truncated
content and wrong-destination mutations must fail the content oracle.
The existing installer module retains its original node inventory while its
real stamp fixture also compares complete library contents and a nested payload
probe. No cache/metadata inputs are discarded to meet the native deadline.
The shared PowerShell copy helper stays canonical on `dev`; normal release
materialization, not checked-in per-plugin engine copies, supplies shipped bytes.
Focused copy-contract results do not replace the complete automatic installer
run or increase its existing native/item/group/aggregate limits.
Ordinary absolute materialization paths use fresh no-follow metadata checks
within their resolved root rather than resolving every ancestor again for each
asset. Relative paths, parent traversal and reparse aliases retain the original
resolved walk. Snapshot manifests accumulate relative keys during traversal but
still read fresh metadata and hash complete file contents on every call; no
content or identity is cached across calls, no input is discarded, and fixture
time remains charged to the existing budgets.
Interrupt/internal/usage failures, unfiltered empty collection, containment
exceptions, resource failures and aggregate timeouts still abort immediately
with their original status. The runner's multi-plugin policy is unchanged.

`tools/test_run_plugin_tests.py` checks normal `run_plugin` command planning,
real pytest collection inventory and exact-once coverage, explicit/filter
selection behavior, failure propagation, and unchanged resource/aggregate
limits. Run the owned native contract through the same automated plan without
manual selectors:

```powershell
python tools\run-plugin-tests.py agent-codespaces --test-file tests\test_installer_engine_adoption.py
```
Use repeatable `--test-file <path-relative-to-tests>` with one plugin to avoid
collecting unrelated modules before applying `-k`. Unknown, escaping and
non-file selections fail before environment preparation. The Windows
reconciliation smoke names `test_reconcile.py` and
`test_installer_command_binding.py`, retaining all 18 selected checks and the
120-second budget without collecting the other 10,000-plus tests.

Managed-worktree removal fixtures accept the canonical project loader's
keyword-only overlay selection while retaining synthetic receiving-side
configuration; they never discover installed project or plugin registries.
Both explicit overlay values also run in the required PR guard lane.

The default headless-pool CLI case retains its original unparameterized node ID;
explicit central/local authority cases have separate parametrized IDs. This
keeps a current candidate compatible with an existing coverage selection while
retaining all three real command contracts. Missing requested nodes still fail
collection; they are not silently removed from the selection.

Execution-space registry tests retain the supplied receiving configuration
through the real YAML overlay reader and reject malformed registries. A fresh
Python subprocess also exercises the machine getter's missing-registry path
and verifies that configuration is loaded exactly once. Packaged-wrapper
previews use the actual wrapper manifest and canonical assets, without copying
unrelated runtime sources or reading the checkout's pending changefiles.
Native landing tests exercise explain and finalize separately against the same
repository scenario, retaining both real Git contracts within each item budget.
The landing fixture isolates the unrelated CodeSpace-claim warning; that
provider adapter has its own dedicated tests.

Agent-worktrees installer command resolution, pipefail execution and POSIX
background-prune tests are explicit T3 native contracts. Collection never probes
Bash or PowerShell. A lazy session fixture admits native shells only after
`--allow-explicit-tiers`; direct pytest also requires
`COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS=1`. Windows admission prefers Git
Bash and rejects WindowsApps/System32 WSL launchers before executing anything.
Fallback lookup removes those launcher directories before searching PATH, so
an earlier WSL shadow cannot hide a later portable Git Bash.
The exit-code, pipefail and Windows cygpath probes run once per admitted session,
headlessly with five-second bounds. Native semantic tests and path conversion
remain real; ordinary tests mock the admission policy and forbid process
creation during fresh imports. The pipefail text drift guard remains default.

```powershell
python tools\run-plugin-tests.py agent-worktrees -k native_shell_collection
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers -k 'installer_copilot_resolution or installer_pipefail or invoke_payload_runtime_posix'
```

### Shell-first update: real local Git acceptance

Run this explicit native lane under a process-tree supervisor (a Windows Job
supervisor on Windows), never raw pytest:

```text
python tools/run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests/native/test_update_entry_local_git.py --test-timeout 45 --subsuite-timeout 180 --plugin-timeout 240
```

The lane creates private temporary roots and a real bare Git repository holding
only harmless `worktree-manager/bootstrap.sh` and `.ps1` fixtures. Its ordinary
Manager `config.toml` selects that source with a `file://` URI and exact ref;
there is no source-selection bypass. Fixture Git objects are deterministic:
no `git commit`, identity lookup, publication, SSH or network source is used.
The generated project launchers and installed shared launchers are unmodified.
POSIX executes the actual shell entry; Windows executes both actual project
`.cmd` and `.ps1` entries with missing and failing old runtimes.
When an eligible Git Bash is installed, Windows also exercises the existing
global sh binstub with its real Windows `provider_root` registration. That
entry must select the native PowerShell early path before POSIX root parsing.
Unavailable/unsupported native shells are reported as skipped, not positive
platform acceptance.

Success means the freshly fetched fixture records exact argv and exits **73**,
with no old resolver/interpreter/Manager marker, no installer staging, and no
remaining per-attempt scratch. It is deliberately **not** whole-suite update
success. Native Windows execution and console/foreground observation remain
separate acceptance evidence. The `tests/native` directory is excluded from
default discovery and required unit promotion; collect it explicitly as above.

The keyed workspace-creation regressions have an opt-in native companion.
It runs the real create parser/handler, Git allocation, tracking writer, and
creation journal in a fresh synthetic owner, then replays from a second Python
process. No Copilot session or host registration is created. Run it with:

```powershell
$env:AGENT_WORKTREES_CREATION_NATIVE = '1'
python tools\run-plugin-tests.py agent-worktrees -k creation_requests --test-timeout 120
python tools\run-plugin-tests.py agent-bridge -k workspace_creation --test-timeout 120
```

The bridge companion uses the same fresh-owner fixture across real create,
exact-record/head reads and explicit `bind-session` subprocess calls. It also
exercises a real dispatch queue's lost allocation response, restart and changed
routing, proving one execution checkout despite a different queue lane. Bridge
SessionManager/SQLite and durable first-turn admission are real; Copilot ACP/
provider I/O is simulated. This is connected first-touch evidence, not a
deployed binstub, full host installation or live model transcript check.

Native conversation-continuity probes should use the normal worktree launcher
and Bridge CLI control when exercising the complete ownership path. A deliberately
manual mux/TTY probe must establish folder trust before waiting for model output:
use the owner's `permissions.add_trusted_folder()` against the fixture's actual
Copilot configuration, or explicitly handle the one-session trust prompt with a
known key sequence. An isolated `COPILOT_HOME` must not accidentally leave that
helper writing production configuration. Assert the fixture trust entry before
launch; an unanswered trust prompt is not evidence that native resume failed.

For a cold-resume claim, observe the predecessor process exit, the exact native
conversation ID, and a new response proving remembered context. Use UTF-8 for
captured Windows mux output. Keep backend-only evidence distinct from managed
owner/HTTP acceptance, and close registered fixtures through Bridge control
rather than inventing a second controller. A failed terminal-exit sequence must
preserve the fixture's exact PID/identity for authorized cleanup; it is not a
passing complete lifecycle test.

`plugins/agent-bridge/tests/test_native_worktree_continuity.py` is an opt-in
real-backend acceptance lane (`AGENT_BRIDGE_NATIVE_CONTINUITY=1`). It requires
explicit lifecycle approval (`AGENT_BRIDGE_NATIVE_LIFECYCLE_APPROVED=1`), a private
`AGENT_BRIDGE_NATIVE_ROOT`, the prepared candidate Worktrees interpreter in
`AGENT_BRIDGE_NATIVE_OWNER_PYTHON`, and a nonsecret preferences JSON file named
by `AGENT_BRIDGE_NATIVE_PREFERENCES_FILE`. The preferences contain the operator's
model, effort and context selections, never credentials. Authenticate the
fixture profile through the vendor's normal login before running; never copy a
credential store or export a token. The shared private owner home is reused
across mode cases while each importing Bridge starts with an empty database.
The fixture materializes unchanged source launch assets and explicitly forwards
its owner PATH and Bridge endpoint through the mux environment. Both ACP and
CLI cases use real owner commands, HTTP routes and Bridge turn control, then
assert remembered context, actual idle, exact native identity and no replacement
history. Run through the normal bounded test runner, not against a shared daemon.

Resident list-warm snapshot policy tests keep process calls mocked and verify
exact output shapes, filter/project separation, next-pass freshness, unavailable
classification, and unchanged standalone reads. Their bounded native companion
executes the real warm loop, classify client/server transport and Git classifier
against a fresh divergent checkout; transcript, mux and reclaim enrichment are
modeled separately. The fixture refuses a bypass of the classify transport.
Its cache clock is isolated from the shared standard-library clock, so the
native transport's request deadlines remain real.
Four same-selection shapes retain eight direct Git starts in each of two
unchanged passes, then observe a subsequent dirty edit. Run that lane explicitly:

```powershell
$env:AGENT_WORKTREES_LIST_WARM_NATIVE = '1'
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers -k test_native_resident_warm_two_cycle_git_budget
```

The Windows deep-build control in `tools/test_standalone_python_artifacts.py`
builds the real Worktrees wheel from a source root whose wheel staging paths
exceed the native path limit, then compares every packaged runtime resource
against its source bytes. The ordinary Worktrees and Manager wheel/sdist
round trips separately cover the shared build helper's existing consumers.
The metadata-only control simulates the public distutils missing-wheel
exception; a real no-wheel toolchain check remains a separate validation.

The agent-worktrees native lean-provision and namespace-package health-gate
regressions materialize the real release payload and install it through UV and
the canonical seasoning adapter. Preparation retains registry metadata, wheels,
and isolated-build dependencies in the runner venv's `.bootstrap-uv-cache`. Each fixture copies
that seed into its own temporary root and sets `UV_CACHE_DIR` and `UV_OFFLINE`,
so redirecting HOME/XDG state cannot trigger dependency downloads after the
container disconnects. Missing prerequisite state fails explicitly; prepare
with `tools/run-plugin-tests.py agent-worktrees --prepare-only`.
The lean-provision case uses the same isolated offline cache handoff and checks
the installed resolver and both real launcher entrypoints after construction.
The UV wrapper changes only the staged interpreter's health invocation:
it removes package entrypoints to exercise real namespace rejection, or stalls
that invocation to exercise the installer's process-tree watchdog. Both cases
must reach health before asserting rejection, and retain child stderr when
construction fails. The 60-second installer deadline stays inside the
90-second subprocess and 120-second test bounds.
The stalled descendant/captured-pipe case lives separately in
`test_first_install_bootstrap_watchdog.py` and runs only in the full suite,
not the required guard lane.

The opt-in Windows first-use test exercises both payload and global entries in
disposable homes. The payload entry stamps before queued provision; the global
entry provisions directly and uses a private test mutex. Both check the
completed runtime and command. Each uses a fresh cache and an explicitly
supplied anonymous HTTPS index, not the offline seed or the live installation.
Select an approved feed before running:

```powershell
$env:AGENT_WORKTREES_FIRST_USE_NATIVE = '1'
$env:UV_DEFAULT_INDEX = '<approved-https-index>'
<prepared-test-python> -B -m pytest -q plugins\agent-worktrees\tests\test_first_install_bootstrap.py -k test_windows_payload_first_use_stamps_and_provisions_under_driver
```

This is first-use evidence, not a two-generation update or service-cutover test.

Worktree Manager's native viewport regression uses 1,100 live synthetic
worktrees, with 40 visible by default, both without workers and with 1,100
loaded worker rows. It checks Show Hidden and the actual pulse refresh through
the real Textual compositor, bounds formatting by the viewport, retains
one-line option geometry and exercises off-screen selection/content updates,
late worker changes, focus and scrolling. Worker scopes are indexed once per
build, keyed by each pivot's effective scope: account-wide worker rows are
indexed once even in the cross-machine All view; machine-scoped rows stay
separate. Native title, detail and worker rows
remain deferred during incremental repaint too. Native chrome does not build
the data body; deterministic capture resolves its selected window with Rich
styles intact.
The ordinary regression enforces access counts rather than host-speed timing.
For an explicit local 200 ms rebuild/pulse-to-painted check, collect only the
benchmark file and run each shape in its own contained process, matching one
production application. Wait for the default view to paint before measuring a
mode change. Process CPU and GC costs are reported, never subtracted from the
wall-time gate; no GC policy is changed:

```powershell
$target = (Resolve-Path worktree-manager\tests\production_picker\test_native_viewport.py).Path
$ignore = Get-ChildItem worktree-manager\tests -Filter 'test*.py' -Recurse -File |
    Where-Object FullName -ne $target |
    ForEach-Object { '--ignore="' + $_.FullName + '"' }
$env:WORKTREE_MANAGER_RENDER_BUDGET = '1'
foreach ($shape in 'False', 'True') {
    $env:PYTEST_ADDOPTS = '-s -k ' + $shape + ' ' + ($ignore -join ' ')
    python tools\run-standalone-tests.py worktree-manager --prepare
    if ($LASTEXITCODE -ne 0) { throw 'Native viewport budget failed' }
}
```

FINAL stamp regressions use one immutable real-Git template and isolated
per-test copies. The changed-plugin guard lane runs four smoke cases:
serialized stamp/poll persistence, a tracked edit invalidating the stamp,
the production cached-display/removal split, and a retained at-rest claim.
The remaining checkout/obligation matrix runs in the existing full
promotion/scheduled/manual suite, not as an unconditional new PR matrix.
The report-only audit recipe's pure parameter/receipt/path checks and
service-free CLI render smoke also run in the changed-dispatch guard lane.
A non-dry-run CLI kick additionally traverses the real parser/create/client
path against an isolated loopback coordinator without spawning a worker.
Native symlink escape coverage runs where the test environment permits
creating symlinks; a platform refusal is reported as a skip, not a pass.

There is no host-wide test singleton. Each target takes a lease keyed by its
**concrete environment directory's filesystem identity** (`st_dev`, `st_ino`),
held across venv preparation,
dependency-fingerprint rebuilds, `--reinstall`, and all test use. This includes
`--guards`, `--collect-only`, and `--prepare-only`; `--list` neither establishes
nor locks an environment. Different plugins or checkouts run independently
under the unchanged per-job resource and time bounds. Filesystem aliases of
the same environment, including case-insensitive and bind-mount aliases,
contend for the same lease. Managed runners establish the directory before
locking it and preserve its identity through rebuild: only contents are
cleared under the lease, and `uv venv --allow-existing` preserves the root.
The installer environment excludes `UV_VENV_CLEAR`, and a post-creation
identity check detects an unexpected replacement. An absent or unidentifiable
custom root is rejected; there is no lexical fallback lock key.
A multi-target run releases
each target before acquiring the next, so it never holds multiple environment
locks. A conflicting run fails fast with exit code 3 and names the environment
and live holder; `--admission-wait` allows a non-negative, finite, bounded wait
**per target**:

```bash
python tools/run-plugin-tests.py agent-worktrees --admission-wait 900
```

Environment setup failures are recorded per target without aborting later
targets; genuine busy contention retains exit code 3. Junction detection is
guarded for Python versions without `Path.is_junction`.

The former Linux/Windows runner-contract and payload-CWD jobs no longer run
in Source PR/push CI. Their real-process, actual-Git, uv rebuild and native
assertions remain integration source, not eligible units. The standalone
`payload-cwd-contract.yml` retains weekly/manual routing metadata, with its
execution jobs explicitly disabled until a hardened root-tools/native container
route is enrolled. A skipped job is not validation evidence.

`windows-coverage-rotation.yml` reuses its existing schedule/manual entrypoints
for all nine Linux plugin suites through `run_tests_in_devcontainer.py`, with
immutable source selection, execution logs and named failure ownership.
Each Linux job provisions the exact CLI in `tools/devcontainer-cli/package-lock.json`
through the existing configured-registry age/integrity admission helper, with
dependency scripts disabled and a matching `devcontainer --version` health
check. The CLI is job-local, not a global install; Docker and CLI prerequisite
checks remain before the hardened wrapper. CLI readiness alone is not a
container-suite or platform qualification.
Its former hosted Windows execution is explicitly unavailable pending Windows
container capacity. Linux success does not qualify Windows, explicit-tier
families or other unenrolled residuals; `tools/periodic-integration-inventory.json`
records those boundaries under portfolio issue #1303.

Tests may declare `@pytest.mark.portfolio_tier("T0" ... "T4")` and repeatable
`@pytest.mark.effect(...)` markers. The injected policy rejects effects that do
not belong in the declared tier. T3 clean-room and T4 end-to-end families are
skipped unless the caller passes `--allow-explicit-tiers`; target-specific
environment gates still apply. Credential-dependent explicit-tier checks may
also pass `--allow-host-state`; this preserves host credentials and config
roots while keeping temporary/runtime roots sandboxed and removing live Copilot
session and worktree-owner bindings.

The shared `agent-procutil` spawn helper detects contained test runs and
suppresses deliberate Windows Job breakaway and POSIX session detachment.
Production daemon semantics remain unchanged outside the runner. Runtime
survival adapters, including the Agent Bridge Session Host's in-process
`setsid()` / Job setup, consult the same policy. An adversarial test launches a
descendant through the detachment API and proves the containment owner still
reaps it on timeout.

Agent-worktrees' captured-child guard tests run in-process. Its opt-in
`AGENT_WORKTREES_BOUNDED_NATIVE=1` integration requires `--allow-explicit-tiers`
and a quiet Windows desktop: a real consoleless Python parent exercises two
captured venv-interpreter cycles, stdout/stderr and exit status, then a stalled
child with a grandchild. The probe checks descendant teardown, visible-window
creation, cleanup despite explicit descendant breakaway requests, and foreground transitions without
pytest's headless spawn adapter.
The ambient desktop observations intentionally fail rather than excuse an
unattributable window or focus change; run this lane separately from normal
interactive desktop activity.

Agent-worktrees' tree-comparison policy tests also run in-process. The opt-in
`AGENT_WORKTREES_TREE_DIFF_NATIVE=1` lane in `test_git_tree_diff.py` requires
`--allow-explicit-tiers` and constructs isolated fresh Git repositories. It
measures two unchanged divergent-WIP cycles with one and twenty changed
paths, requiring eight direct classifier Git starts per cycle, excluding
fixture setup, with 500 unrelated upstream paths excluded from the actual
lookup output. It also checks dirty/ref changes, Unicode names, replacement
objects, exact ancestor/descendant lookups and differently shaped landed content.
These are direct Git-launch
budgets, not a host-wide spawn/window census or a deployed-daemon measurement.

Large test modules should be split by behavioral contract, not by arbitrary
line count. Within each contract family, prefer one parameterized or
scenario-style test that validates several related observable features over
many process-launching micro-tests that repeat the same setup and failure
boundary.

Tag the behavioral contract a test (or test class) belongs to with
`@pytest.mark.contract("<component>.<behavior>")` — e.g.
`@pytest.mark.contract("agent_worktrees.pr_ops.merge")`. This is attribution,
not policy: unlike `portfolio_tier`/`effect`, nothing rejects a missing or
unrecognized value. Its purpose is to make the contract a test module was
split along an explicit, filterable, greppable fact instead of something only
inferable from a file's name or a docstring — `pytest -m 'contract("agent_worktrees.pr_ops")'`
selects every test attributed to that contract regardless of which file it
now lives in, which is exactly what a future re-split needs: proof that
moving tests between files didn't silently drop or duplicate a contract's
coverage. When splitting an existing oversized test module, assign one
contract per resulting file as you go; retrofitting the marker onto
already-small, single-contract files is not required.

## Optional devcontainer-based isolation (Linux)

`tools/run_tests_in_devcontainer.py` is an opt-in wrapper around the turn-key
runner above that additionally runs it inside a hardened, ephemeral
devcontainer (`.devcontainer/test-isolation/devcontainer.json`, a named alternate config -- never the canonical `.devcontainer/devcontainer.json` root path, which would let a direct "Reopen in Container" pick up an empty, wrapper-only workspace by accident) -- a real OS-level
filesystem/privilege boundary on top of (not instead of) the turn-key
runner's own process-level containment. **Networking is now part of that
boundary for the real test run**: the wrapper first runs a dependency-
preparation pass (`--prepare-only`, which drives the same venv-install path
a real run would WITHOUT ever importing a single test module or
`conftest.py` -- deliberately not `--collect-only`, which still runs
pytest's own collection and would execute that module-level code with
network access) while the container still
has its default outbound reach, then disconnects the container from every
attached network before running the real, now network-disconnected test
pass -- a test can no longer open an ordinary socket to an external or
host/LAN service during actual execution. The filesystem/privilege boundary
exists for the case the turn-key runner cannot cover on its own: a buggy or
adversarial test that escapes process-level containment via an absolute
path write or a privilege a job object doesn't restrict. See
`efforts/active/devcontainer-test-isolation/README.md` for the full design
rationale and the findings (Phase 0) that shaped it.

Requires a seasoned [devcontainers CLI](https://github.com/devcontainers/cli)
on `PATH` and a working Docker daemon. The scheduled lane provisions the locked
CLI described above; it does not assume the hosted image supplies one. Linux only --
this repo's CI already runs a dedicated `windows-latest` job covering the
Windows-specific containment paths described above, so a devcontainer adds
nothing there.

The isolation container bootstraps pinned, SHA-256-verified `uv` and Node.js
interpreters before dependency preparation. Node 22.15.0 supports the native
extension contract tests without relying on a host installation; only its
interpreter is extracted, not npm. Both x86-64 and ARM64 archives are pinned.
The actual suites still run after every container network has been disconnected.

```bash
python tools/run_tests_in_devcontainer.py agent-worktrees
python tools/run_tests_in_devcontainer.py --changed
python tools/run_tests_in_devcontainer.py --all -- -k some_filter
python tools/run_tests_in_devcontainer.py --include-untracked agent-worktrees
```

Everything after the wrapper's own small flag set (`--keep`,
`--include-untracked`; or a literal `--` anywhere in the remaining
arguments) is passed through to `tools/run-plugin-tests.py` *inside* the
container -- with one normalization (a `--base` value that resolves on
the host is rewritten to its resolved commit SHA, or appended when
changed-selection is active and `--base` was omitted entirely, before
the in-container invocation is assembled; see below for why) and two
known exceptions to otherwise-transparent passthrough: `--allow-host-state`
is rejected outright (see below), and a `--max-memory-mb`/`--max-processes`/
`--max-temp-mb` value above the container's own fixed outer ceiling is
rejected outright (see below). The wrapper validates `--admission-wait` before
any container work (even for `--list`) and passes it through unchanged. It
takes no host-wide lease: each invocation has its own container workspace and
environments; the inner runner retains environment-scoped exclusion. The wrapper:

1. Writes a per-invocation copy of `.devcontainer/test-isolation/devcontainer.json` with
   its workspace volume name made unique to this run, creates that volume
   explicitly as a size-bounded (4 GiB), tmpfs-backed Docker volume (not
   the default unbounded local-disk volume), then brings the container up
   via `devcontainer up` -- **never** a bind mount of the host checkout.
2. Copies a point-in-time snapshot of the host checkout into that volume
   (a `tar` file streamed through `docker exec`'s stdin). The working-tree
   files come from `git ls-files --cached` by DEFAULT -- git-tracked files
   only, deliberately NOT every file physically present under the checkout
   and NOT untracked files either: this repository has no blanket
   `.gitignore` rule for `.env`-style config or arbitrary credential
   filenames, so an untracked-but-not-ignored secret file sitting in the
   working tree would otherwise still be copied into a container that has
   outbound network access during its dependency-preparation pass. Pass
   `--include-untracked` to additionally
   include untracked-but-not-gitignored files (e.g. to test a new,
   not-yet-committed file) -- a deliberate, explicit opt-in, never the
   default. `.git` is handled separately and deliberately minimally: rather
   than copying the real git database wholesale (which would carry every
   branch, stash, reflog, and unreachable object -- local-only content
   having nothing to do with the plugin suite being run, into a container
   that can still reach the network during its dependency-preparation
   pass), a `git bundle` containing only the
   object closure of `HEAD` and the `--changed` diff base (`--base`, or
   that runner's own
   `origin/main` default when `--base` isn't passed) is built and cloned
   into a fresh, minimal git directory instead -- this also transparently
   handles a linked worktree's `.git` (this repo's own required flow,
   where `.git` is a pointer FILE naming an absolute HOST path, meaningless
   inside the container) without needing to special-case it. The index is
   then rebuilt from `HEAD` (`git read-tree HEAD`) rather than copied from
   the host -- a copied index can reference a staged-but-uncommitted
   blob that is unreachable from both `HEAD` and the base ref, which the
   bundle would then be missing entirely. The tradeoff: staged state isn't
   preserved as "staged" inside the container, but every modification
   (staged or not) is still visible as an ordinary working-tree difference,
   since the modified file's actual current on-disk content is what gets
   copied in regardless. `config` is always replaced with a fresh,
   credential-free minimal one and `hooks` is always dropped (neither is
   needed for `git diff`/`status`/`rev-parse`, and either could carry
   credential-bearing or otherwise sensitive
   content). Every `git` subprocess call here scrubs ambient
   `GIT_DIR`/`GIT_WORK_TREE`/etc. from its environment first, so a
   contaminated calling environment can't silently redirect it to the wrong
   repository. The host checkout is only ever **read**, never mutated, by
   anything that happens afterward inside the container.
3. Resolves every targeted plugin's venv dependencies first (a
   `--prepare-only` pass -- never imports test code -- run with the
   container's default outbound network reach), then disconnects the
   container from every attached network, strips a passed-through
   `--reinstall` (the prep pass already rebuilt the venv; the real pass
   must reuse it, not rebuild it with no network left), then runs
   `tools/run-plugin-tests.py` for real inside the now
   network-disconnected container via `devcontainer exec` and
   propagates its exit code. A request for only `--list` (which never
   touches a venv) skips both the preparation pass and the network
   disconnect.
4. Tears the container AND its per-invocation volume down afterward (pass
   `--keep` to leave both running for debugging); a failed removal raises
   rather than silently reporting success, and a failed `devcontainer up`
   itself still triggers best-effort cleanup of anything it managed to
   create.

The container itself runs with every Linux capability dropped
(`--cap-drop=ALL`), `no-new-privileges`, a read-only root filesystem with
only `/tmp`, `/run`, `$HOME`, and the size-bounded workspace volume
writable, and hard resource ceilings (14 GiB memory with no extra swap, 4
CPUs, a 512-process PID limit) -- no Docker socket is ever mounted in.
Outbound networking is available only during the dependency-preparation
pass above; the real pytest run is fully network-disconnected (see step 3) --
everything above has been validated against a real container, not merely
asserted.

Because the workspace is a fresh copy rather than the live checkout, an
uncommitted MODIFICATION to a tracked file is included (the copy reads the
working tree's current on-disk content at invocation time, not the
committed blob) -- but a new, never-committed file is NOT included unless
`--include-untracked` is passed, per the tracked-files-by-default policy
above. **Known, accepted residual exposure**: the tracked-files boundary is
about which PATHS are copied, not which bytes -- a secret pasted directly
into an otherwise-tracked file (e.g. a config example) and never committed
is still copied in, since the content read is the live on-disk file, not
the last-committed blob. A clean CI checkout has no such dirty state; a
contributor's local checkout might. This is a deliberate tradeoff (the
wrapper's whole point is testing in-progress, uncommitted changes), not an
oversight -- "tracked" means "this path isn't the kind of thing that
normally carries secrets," never "every byte currently in it is safe." The
wrapper prints an explicit stderr warning naming every dirty tracked file
before building the snapshot, so this residual exposure is surfaced at the
moment it's actually relevant, not only here (and fails closed -- aborts
rather than proceeding -- if that check itself cannot run). The same
residual exposure applies to a tracked file carrying a Git
assume-unchanged or skip-worktree index flag: `git status` deliberately
will not report an on-disk modification for such a path, but the wrapper
still copies the file's real current content. The wrapper separately warns
about any such flagged path before building the snapshot (and likewise
fails closed if that check itself cannot run), so a locally "hidden"
modification doesn't go unnoticed just because `git status` stays quiet
about it. A fresh, per-invocation volume means no state (including prior
test artifacts) carries over between runs.

An unresolvable `--base` is a HARD failure, not a silent degradation, when
changed-selection is actually in play (an explicit `--changed`, or
`tools/run-plugin-tests.py`'s own default when neither `--all` nor an
explicit plugin name is given): that runner's own `changed_plugins()`
quietly reports an empty target set for a bad diff base rather than
erroring, so a typo'd or never-fetched `--base` could otherwise make a run
silently report "No plugin suites to run." instead of the real problem. An
explicit plugin name or `--all` run is unaffected, since neither ever
consults `--base` at all.

A `--base` value that DOES resolve on the host (bare flag, `=value` form,
or an unambiguous abbreviation like `--bas`) is rewritten to its resolved
commit SHA before the in-container invocation is assembled -- this matters
for a ref-relative expression (e.g. `origin/dev~1`): it resolves fine on
the host, but `git bundle create` does not preserve a remote-tracking ref
(`refs/remotes/origin/...`) as a named ref in its resulting clone (unlike
a plain local branch name, which it does preserve), so the unrewritten
expression would otherwise fail to resolve again inside the materialized
snapshot even though the underlying commit object is present. A bare SHA
has no such problem -- it resolves against any clone containing its
object, named ref or not.

One of `run-plugin-tests.py`'s own flags cannot retain its documented
semantics through this wrapper, for a structural reason (a fresh,
credential-free tmpfs `$HOME` per container): `--allow-host-state` is
rejected outright with a clear error (its whole contract is preserving
the caller's real HOME/config/credentials, which this isolation boundary
specifically does not expose) -- run `tools/run-plugin-tests.py` directly
for that case instead. Environment leases remain inside the container:
the wrapper's unique volume prevents a wrapped invocation from mutating
another wrapped or bare invocation's cached environment, so they do not
need to serialize on the host. Each retains its own process/resource
containment.

The standalone runner (`tools/run-standalone-tests.py`) uses the same
environment lease protocol for managed venvs and custom `--python`
interpreters. Standard venv roots are discovered from the canonical containing
`bin`/`Scripts` directory **with `pyvenv.cfg`** or a known managed target,
without executing candidate code. Directory names alone are not proof of a
venv: an opaque wrapper can also live in `bin` or `Scripts`. Known managed
roots remain identifiable while a competing rebuild removes their contents;
custom environments with temporarily missing configuration need an explicit
root. Availability is checked only after the lease is acquired, so
`--admission-wait` covers the missing-executable race.
Nonstandard custom launchers require `--environment-root <actual-env-root>`:
their target cannot be inferred safely without executing them. This explicit
address must name the environment they actually use, so different launchers
sharing it contend. The override requires `--python` and cannot be used with
`--prepare`; it is not an environment-exclusion bypass.
`--prepare` is restricted to that component's exact managed scratch interpreter;
preparation and test use share one held lease. POSIX venv `bin/python`
symlinks are invoked as venv launchers, not resolved to the base interpreter.
An old checkout-local interpreter may still be explicitly selected with
`--python` for execution, but is never provisioned or migrated implicitly.
Same-target fixtures use their active interpreter environment. For cross-target
fixtures, both shared runners explicitly export
`COPILOT_EXTENSIONS_TEST_VENV_ROOT` naming the prepared platform-level cache.
This is distinct from sandbox-local `AGENT_SCRATCH_ROOT`: tests never infer the
other target's prepared interpreter from their disposable child scratch.

Windows standalone cleanup retains extended-length paths and the unchanged
15-second transient sharing retry. A read-only regular file may have its write
bit restored only inside the runner-owned temporary sandbox before retrying
that same unlink. Shared Git objects require every hardlink to be accounted
for within that sandbox; the link inventory is captured lazily once.
Symlink/junction traversal, outside targets, writable-file denials and
non-unlink failures do not gain a permission-changing fallback. Permanent
cleanup failures remain runner failures, not silently successful test runs.

The complete Manager portfolio can be partitioned without raising its
per-run budget: run `tools/run-standalone-tests.py worktree-manager` once with
`PYTEST_ADDOPTS='-k "production_picker"'` and once with
`PYTEST_ADDOPTS='-k "not production_picker"'`. Account for both test/skip totals;
neither selector alone is a full-suite pass. Keep the dedicated viewport timing
lane isolated as described above.

A second exception, for a different reason: the container itself enforces
FIXED, lower outer resource ceilings (`--memory=14g`, `--pids-limit=512`,
and `/tmp`'s own `size=6144m` tmpfs) regardless of what the inner runner's
own `--max-memory-mb`/`--max-processes`/`--max-temp-mb` flags claim. A
value above the matching outer ceiling would otherwise pass through
unmodified, then be silently preempted by the container at the wrong
moment (an OOM-kill, a hit `ENOSPC` on `/tmp`, or a stalled fork) instead
of the clear, immediate rejection the other exception already gives
-- so the wrapper rejects an over-the-ceiling value outright, before any
container work begins, naming the exact flag/value/ceiling involved.

Every git subprocess the wrapper runs on the HOST (bundling, cloning,
reading the dirty/hidden-flag warnings, etc.) forces
`GIT_NO_LAZY_FETCH=1` and `GIT_NO_REPLACE_OBJECTS=1` in addition to the
repository-selection scrubbing and `GIT_OPTIONAL_LOCKS=0` described above
-- without them, resolving `HEAD`/the diff base in a partial clone could
lazily fetch missing objects INTO the host repository (a host mutation
this wrapper exists to prevent), and a locally configured replacement ref
could silently substitute different history into the snapshot than what
`HEAD`/`--base` actually name.

Inside the container, `uv` is bootstrapped via a pinned-version,
SHA-256-verified direct download of its release tarball (not a bare
`curl ... | sh` pipeline) -- the installed `uv` persists and runs again on
every later `devcontainer exec` with the checkout snapshot already
present and outbound networking reachable, so an unpinned/unverified
installer would have everything it needed to defeat the isolation
boundary. The devcontainer spec also grants the workspace path a `git`
`safe.directory` exemption (via `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/
`GIT_CONFIG_VALUE_0` `containerEnv` entries): the workspace volume's own
mountpoint is always root-owned (nothing inside the container can ever
`chown` it, since `--cap-drop=ALL` drops `CAP_CHOWN` too), and modern Git
refuses to operate inside a working tree it discovers is owned by a
different user -- without the exemption, every git invocation
`run-plugin-tests.py` makes (including its own changed-file diffing)
would fail, and since that script treats a failed `git diff` as an empty
target set rather than an error, it would silently report "no plugin
suites to run" instead of the real problem.


## Local Windows SSH proxy regression

After preparing the isolated `agent-bridge` test environment with the turn-key
runner, the opt-in native smoke test exercises two SSH/proxy cycles from a
consoleless parent with native Windows OpenSSH and, when installed, Git for
Windows. It checks displayed terminal windows and foreground changes using a
synthetic proxy, without remote hosts or credentials:

The windowless parent carries synthetic Win32-OpenSSH socket-descriptor metadata
while its children receive newly redirected handles. Both cycles must reach the
real SSH proxy, and the broker-owned proxy must not inherit that obsolete state.
Portable contracts separately cover ambient, explicit and empty environments,
case-insensitive key removal, retained unrelated entries, caller immutability,
and unchanged POSIX handling.

```powershell
$env:PYTHONPATH = "$(Join-Path $PWD 'libs\ssh-manager\src');$(Join-Path $PWD 'libs\agent-procutil\src')"
$env:SSH_MANAGER_WINDOWS_PROXY_TEST = '1'
python tools/run-plugin-tests.py ssh-manager --test-file tests/test_proxy_windows.py
```

The portable proxy contracts cover binary forwarding, routing identity, and
timeout/cancellation cleanup and run in the Agent Bridge CI lane.

## Coverage-selected plugin execution

Required Source CI currently runs the positively admitted unit packet through
`ci_pr_unit_reuse.py`; it does not run the historical full-plugin dispatch PR
pilot. Required promotion remains unit-only. The retained, explicitly invoked
`tools/run-coverage-selected-tests.py` supports `agent-dispatch` and reuses
`coverage_guided_selection.decide()` and the trusted main-history pointer /
Release-asset baseline. `agent-worktrees`' separate consumer remains shadow-only.
Changing this helper does not restore full-plugin integration to required CI
or enroll another profile. Independent manual/periodic lane admission remains
unchanged.

Coverage execution always unions exact diff-attributed test nodes,
the existing weighted-set-cover smoke floor (120 seconds of measured baseline
test costs in the historical pilot, T0-T2/untiered candidates), structural
`guard` tests, and every current eligible test without verified auditability
evidence. Changed or
new `tests/test_*.py` files run in full too. The smoke budget bounds only that
floor, never the implicated tests or complete invocation. Current debt limits
are 200 intervening commits and seven days. Curation uses compact line bitsets
and lazy marginal-gain ranking, with the same greedy score/cost/node-ID ties
and hard budget as the original algorithm.

Selected execution requires the baseline's actual measured commit. If a
selected test module changed between that commit and the candidate, the
producer adds the whole current module while retaining the originally requested
node IDs. New or previously unselected unauditable/unclassified tests are
retained from the current collection, even without baseline entries.
The collector emits repository-relative source keys with portable separators,
including when measured on Windows.

Diff attribution uses the merge-base's **old** changed line numbers, remapping
the earned baseline to that fork point rather than invalidating it against the
replacement being tested. Added lines, unrecognized/binary diff shapes, and
changes outside the attributed source scope remain explicit fallback triggers.
That fallback adds the measured smoke set without removing known implicated
tests, including when age or commit-volume debt exceeds its threshold:
remappable implicated tests remain additive to smoke. Legacy shadow consumers
retain their existing debt fallback. No usable baseline (including an unavailable
Release asset) visibly runs the full/default portfolio; it never means “guards
only” or zero tests.
An empty curated smoke floor is an error even when attributed tests remain.
The pilot CLI also refuses an absent plugin test suite, so full fallback cannot
become a successful no-suite skip.

`--selection-file PATH` on `run-plugin-tests.py` accepts a versioned JSON
manifest. Version 1 remains explicit manual exact selection, for example:

```json
{
  "version": 1,
  "plugin": "agent-dispatch",
  "nodes": ["tests/test_example.py::test_contract"],
  "files": ["tests/test_changed.py"],
  "include_guards": true
}
```

Coverage producers emit version 2, with `coverage_profile` and
`coverage_exclusions` in addition to those fields. The runner collects its
whole existing admitted file inventory, then excludes an unrequested node
only when its current auditability declaration and unchanged dependency
digests match earned evidence for the actual interpreter/platform profile.
Missing, legacy, malformed, partial or cross-profile evidence retains tests.
Coverage-selected subprocesses use the collector's fresh-cache, no-write
policy too; legacy version-1 exact selection keeps its existing behavior.
Existing tier/platform skips and manual opt-ins are not overridden.

Declare auditability independently of tier/effect/contract markers:

```python
@pytest.mark.coverage_auditability(
    "auditable", dependencies=["plugins/example/src/example/policy.py", "fixtures/input.json"],
)
def test_policy():
    ...
```

`dependencies` is a reviewed, complete repository-relative input closure,
including relevant Python helpers and configuration/data. The collector also
binds the test file and ancestor conftest/project configuration. Declarations
must be unique and well-formed; a tag alone is not execution proof.
`coverage_auditability("unauditable")` needs no closure; missing declarations
are unclassified and equally conservative.

The ordinary schema-v4 collector qualifies only the bounded T0/T1 in-process
families with no declared effects beyond filesystem. Every declared production
Python dependency needs runtime-context attribution, and an undeclared
runtime-covered source refuses qualification rather than trusting an incomplete
closure. Each collection chunk uses a fresh private bytecode-cache prefix and
disables cache writes, so timestamp/size-valid stale bytecode cannot substitute
for current source. The `python-inprocess/v2` profile rejects earlier evidence
that did not enforce this. Its bytes and all input digests must agree
before/after collection and at selection/execution. This is
not a claim of 100% line/branch coverage. A changed dependency file retains the
test even if line attribution is incomplete. Native/process/network/service
behavior has no qualification in this collector and remains always retained
inside its independently admitted lane; no native instrumentation is added.
The five pure selection-unit cases are the initial declared family. Required
unit-packet execution remains unchanged; that separate profile is not silently
enrolled by adding their tags.

Use one named plugin and no other selection mode. Paths must identify existing,
unlinked plugin-relative test files; node IDs must match collected items
exactly, including parameter IDs. Missing/renamed nodes, empty files, malformed
or empty evidence fail rather than silently deselecting everything. Selection
diagnostics report total counts and bounded samples; the manifest/decision
artifact retains the complete requested selection. The
portable validator rejects POSIX absolute paths, Windows drive/UNC paths and
drive-relative forms on every platform. Interactive `--test-file` still accepts
native relative Windows separators before normalization.
The contained invocation snapshots the validated manifest, neutralizes ambient and
project `addopts`, and retains the existing isolation and process-tree limits.
Ordinary unselected runs are unchanged. The hardened
`run_tests_in_devcontainer.py` wrapper accepts the same flag: it transfers only
validated manifest data into the disposable container, strips the selection
from dependency-only preparation, then executes it with networking removed.

For retained per-case results from the local contained runner, add
`--junit-dir <new-report-root>` to `run-plugin-tests.py`, including exact-selection
runs. Each plugin gets a fresh `<new-report-root>/<plugin>/` directory and one
`group-001.xml` (then `group-002.xml`, etc.) per attempted group. Existing plugin
report directories are refused before dependency preparation, so a retry must
name a distinct destination and cannot overwrite earlier evidence. The explicit
report option survives neutralized `PYTEST_ADDOPTS` and project `addopts` without
changing selected nodes, isolation, item deadlines or group/resource limits.
Reports survive owned sandbox cleanup and distinguish failures and declared
skips; an absent requested report cannot accompany a successful group result.
Partial reports do not establish group success or cleanup. Reporting is not
available for dependency-only preparation or listing and does not add a
devcontainer artifact-export mechanism.

The producer decision artifact records requested nodes, changed test files,
baseline generation, debt, fallback reasons, predicted smoke cost and attributed
line breadth. The coverage collection hook reports the complete current
retained/selected/excluded node sets, so inventory-only additions remain
auditable separately from producer requests and pytest pass/skip outcomes.
The CLI step summary contains bounded metadata. Complete invocation timing includes planning, dependency
preparation, execution and cleanup, not just pytest's test timer. Guard and
additional implicated/changed-file costs are not claimed to fit the predicted
smoke-floor cost.

Focused contracts live in `tools/test_coverage_selected_execution.py`,
`tools/test_coverage_fallback_equivalence.py` and
`tools/test_plugin_test_selection.py`. The first file's opt-in
`test_runtime_covered_regression_is_selected_and_fails` (`CGS_RUN_INTEGRATION_TEST=1`)
earns real runtime coverage, proves smoke alone cannot afford the detecting
test, and checks the selected clean/broken/repaired executions. The manual
proof requires `coverage` and `pytest-json-report` in the test environment.
Its private fixture reuses those declared dependencies while executing the real
collector driver and contained runner, rather than resolving packages inside
each test's deadline.

The existing manual `Installation context full` workflow's `coverage-selection`
portfolio qualifies these controls on Linux and Windows at the exact dispatch
SHA, including a strict audited subset, unknown/unauditable retention, a new
unmeasured test, unchanged T3 admission and a retained-test regression that makes
the real contained selected command fail. It has no PR/scheduled trigger and
does not expand required unit CI or qualify the deferred native prototype
tracked in #10209.

## Test portfolio invariants

Owned-PR rebase publication keeps representative real-Git success, reviewer-race
and pre-push rejection contracts in the default/guard lane. The existing
agent-worktrees changed-plugin gate scopes those checks to affected PRs. The full
head-scheme/publication-flow and refusal matrix is opt-in and owned by the
manually dispatched `PR rebase full` workflow on Linux and Windows:

```powershell
$env:AGENT_WORKTREES_PR_REBASE_EXHAUSTIVE = '1'
python tools\run-plugin-tests.py agent-worktrees -k "owned_pr_rebase or pr_recovery or pr_break_glass or pr_rewrite or pr_publication_state or pr_publish_stale_tip or pr_partial_publish_guard" --allow-explicit-tiers --subsuite-timeout 1800 --plugin-timeout 1800
```

Use the exhaustive mode when changing replay authorization or its publication
adapters; unrelated PRs and ordinary promotion runs do not pay the matrix cost.
The same manual lane covers exact operator-recovery previews, advanced live
heads, backed-sync bookkeeping settlement, immutable source/lease races and
ordinary stale-tip reconciliation against disposable Git repositories. It
requires no live PR, credentials or effectful operator recovery.

Required pull-request CI is a fast regression gate, not the complete test
inventory. Its design target is a wall time below five minutes under normal
runner variance. This is not the runner's timeout: containment budgets such as
the 15-minute per-plugin ceiling remain safety limits for unusually slow or
explicit runs. A suite that cannot fit the required pull-request CI target must
provide a contract-focused smoke lane and move its exhaustive cross-products to
a scheduled or manually dispatched workflow.

- Gate specialized suites by changed paths so unrelated pull requests do not
  pay their cost.
- The existing artifact/toolchain, Python 3.10 compatibility, Windows composed
  artifact and Windows candidate-readiness lanes also cover their source inputs:
  root/nested `.gitattributes`, plugin and shared-library trees (including
  metadata and copied files), declared standalone consumers, changefiles,
  marketplace versions, materializers and their helpers, and canonical copied
  documentation. Both inline artifact selectors in `ci.yml` must agree;
  `tools/test_public_toolchain_source.py` checks positive and unrelated-path
  cases. `tools/test_promotion_python_artifacts.py` exercises the real producer
  hash/materializer/verifier roundtrip with offline wheel-encoding fixtures,
  including LF/CRLF policy and altered source, dependency, bundle and wheel
  bytes. These contracts do not normalize downloaded bytes or replace raw
  archive/wheel digest checks, and do not require an exhaustive portfolio for
  unrelated documentation changes.
- `tools/test_promotion_source_alias.py` uses real Git candidates, squash
  landings and real source-bound archives to prove that an unchanged version
  alias survives both a prior squash merge and a failed Main merge. Signed
  prior-index proof never waives current plugin-source or raw-byte checks.
- Before a workflow-only control activation, run the prepared file explicitly:
  `python tools/test_release_publication_boundary.py --workflow <prepared-workflow> --base-branch-workflow <prepared-admission-workflow>`.
  It executes the literal merge, pause and control-generation admission code
  with bounded provider/Git fixtures. A legacy default controller is explicitly
  skipped, not credited as implementing the new boundary; fixture proof does
  not replace exact-candidate live CI and artifact qualification.
- Rehearse the existing merge-wait portfolio against that same controller with
  `python tools/test_promotion_merge_wait.py --workflow <prepared-workflow>`,
  inside the normal bounded test supervisor. The fixture supplies the original
  qualification owner for successful cases; missing ownership still refuses
  before a provider call. Default legacy-profile results alone do not validate
  a newly preserved Main controller.
- Release-tool compatibility tests retain complete legacy-controller branches
  and execute the prepared source-owned graph when present. Before activation,
  the full tool portfolio must also pass with the actual controlled workflow
  overlays, not only the default Dev YAML. Include workflow-contract test modules
  invoked by native CI cells, such as Fleet Manager's admission/partition guard,
  as well as the release-tool portfolio. Source-policy assertions read the
  recorded Dev recipe; separate actual-controller assertions retain preserved
  admission, required coverage and deadlines. Native source-owned tests use the
  existing second Windows job without increasing either five-minute budget;
  unfiltered file partition assertions guard against lost cases.
- Keep one canonical implementation broad; sample external adapters at genuine
  divergence seams such as parsing, interoperability, security boundaries,
  atomicity, concurrency, and wrapper wiring.
- Preserve an explicit exhaustive mode for adapter implementation changes.
- Reuse bounded processes or in-process APIs when process startup is not the
  behavior under test. Concurrency and process-lifecycle tests must bypass
  pooling so they still exercise real process boundaries.
- When adding a large or subprocess-heavy family, state which lane owns it and
  measure required pull-request CI wall time. Do not make the default lane grow
  merely because the tests are individually valid.

The installation-context foundation follows a reference-versus-adapter model
for its most expensive matrices. The default suite exercises the complete
snapshot-provenance and installation-governance case corpora against the
canonical Python implementation, preferring its importable API when a Python
CLI process would duplicate the same contract. A smaller contract-focused set
keeps Python CLI, POSIX shell, and PowerShell parity, interoperability,
security-boundary, and atomicity coverage. Every exemplar's delegation wiring
is checked; repeated security behavior still spans both exemplar plugins and
both shell styles, while low-risk staged-CWD behavior defaults to the installer
with legacy-discovery support. Source-identity fixtures likewise run exhaustively
in-process against the reference implementation and use representative vectors
for external adapter parity instead of respawning every adapter for every
fixture. Set
`INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1` to restore the full CLI and
exemplar cross-products when changing an adapter implementation:

`test_posix_path_capture.py` covers lossless output assignment, comparison
failures, fast/fallback resolution and native symlink targets. Its synthetic
provider controls run under Git Bash too; native filename cases explicitly
require POSIX. Directory resolution, receipt-root validation and helper
bootstrap regressions in `test_installation_context_posix.py` compare actual
CLI results with the Python reference for one or multiple trailing linefeeds.
Windows skips those filename cases rather than claiming native qualification;
the on-demand `context` portfolio below supplies the native adapter lane.
The `path-captures` portfolio supplies bounded Linux/Windows path/reference,
bootstrap, digest and exemplar diagnostics. The unchanged selectors are
partitioned into `paths`, `producers` and `slots` jobs per OS, each with two
pytest workers and the same 20-minute bound; no case is omitted or duplicated
between those three families. Its POSIX receipt-validation
control compares the contribution base with the candidate and retains both
timings, so worker parallelism cannot silently substitute for investigating a
candidate-specific slowdown. It does not replace the exhaustive collection.
The complete `context` collection and manual-only `context-windows` collection
each use two isolated pytest workers with the same exhaustive adapter setting
and 90-minute bound. No test selector or failure limit shrinks those collections.
Installed exemplar fixtures use the release materializer for canonical
dependencies; completion controls hold real construction ownership and inject
failure only at the actual marker writer, with a positive downstream control.
Windows long-path controls exercise the shared Python owner's stable file
reads, exclusive directory creation and no-replacement completion publication
through the real PowerShell adapter, rather than resurrecting retired C# types.
They retain the 259/260/280-unit file/publication and 247/248/260/280-unit
directory boundaries, destination identity/content checks and long snapshot
parity.
Windows path comparisons separately cover ordinary and extended DOS/UNC
representations returned during concurrent directory creation. Canonical I/O
paths retain their extended spelling, while different roots stay distinct and
device-prefix spellings are not rewritten.
Concurrent slot controls retain the five-second lock-admission bound: a
confirmed busy reply is checked explicitly and retried once after the competing
publisher finishes, rather than assuming every contender must wait indefinitely.
CAS-negative controls prepare completed versions through the reference CLI
and exercise both actual native cutovers; native completion/cutover sequences
remain covered by rollback and interoperability controls. The slots diagnostic
also exercises the actual PowerShell cell-lock watchdog on POSIX.
That lock-wait control first inspects its prepared slot through the actual native
entry, then invokes the unchanged installer watchdog and kernel lock loop extracted
from that entry. This separates source/receipt setup from the four-second
lock-wait measurement, without mocking the process owner or native lock.
The native watchdog result, waiting/entry witnesses and outer bound remain
unchanged; the separate cell-transaction controls still exercise the whole entry.
Regular-file capture controls retain the two-second blocking-open watchdog,
settle its timer on cancellation and close inherited capture channels. Repeated
native captures preserve trailing bytes without paying every watchdog deadline;
the separate FIFO control still requires a bounded refusal.
Its expiry control also refuses cancellation against a timer already reaped by
the watchdog, retaining child custody only until the original wait settles.
Bootstrap drift fixtures explicitly model the publication decision as well as
context selection, including a denied-publication control. No-write PowerShell
hook checks use the bounded initialized host and retain every fixture file in
their byte comparison; console output is captured separately from its protocol.

```bash
INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1 \
  python -m pytest -q libs/installation-context/tests
```

Required PR CI executes the positively admitted unit packet, not
`installation_context_smoke`, native command/diagnostic matrices or a full
foundation suite. Generated payload checks and source guards remain separate
from runtime qualification. The complete installation-context adapter portfolio
runs nightly and on demand through `Installation context full`.

`Installation context native acceptance` retains the explicitly dispatched
Linux/Windows derived-diagnostic mode/pointer matrix and separately selected
PR-owner and Dispatch-delegation portfolios. Native Windows diagnostic cases
use `INSTALLATION_CONTEXT_POWERSHELL=powershell`; an explicitly requested
unavailable shell fails rather than silently supplying evidence. The Windows
derived-diagnostic step propagates both dependency-construction and pytest
failures explicitly. None of these jobs gates ordinary PR or promotion snapshots.
The legacy/no-pointer cell also runs Logger's two real-process command-removal
regressions once per OS: a held publication lease blocks a competing publisher,
and removal refuses a same-byte replacement file. They retain each platform's
command-family scope and run in this native lane, never in the subprocess-free
promotion-unit packet. Existing matrix and job budgets are unchanged.

`tools/test_installation_context_ci_scope.py` checks the current unit-only
boundary, explicit manual matrix/selector bounds and real PowerShell failure
propagation. The path detector remains a callable source contract; its presence
does not mean required CI runs the retired native jobs. Source-preserved
diagnostic, command-lifecycle and Vault first-use cases remain available for
isolated integration. The existing periodic inventory records unavailable
Windows container capacity and unenrolled portfolios; source presence, a
disabled job or a passing schema audit is not native qualification. See the
[portfolio boundaries](#test-portfolio-invariants) and
`tools/periodic-integration-inventory.json`.

Source-qualified catalog no-write cases invoke the unchanged PowerShell script
twice in one real, initialized host. Host-only initialization stops CLR startup
profile recording before the full-profile baseline; no product script is warmed.
Each invocation and host shutdown must preserve every file byte, directory, and
modification time. This measures diagnostic behavior, not the separate startup
cache writes made by fresh Windows PowerShell processes.
The owned case then changes the receipt and requires the next emission to
refuse it without writes. Command-binding launcher controls execute the real
isolated bootstrap/helper and separately retain unavailable-interpreter
fallback, helper failure, empty-response refusal, and opt-in stderr-only
tracing. An admitted launcher passes the helper in its first invocation;
helper failures must not rediscover or launch a fallback interpreter.

Run the relevant isolated integration suite when changing a runtime boundary;
required PR CI's unit gate is not that evidence. Fast structural/contract checks
may carry `@pytest.mark.guard` (marketplace + picker integrity, shipped-manifest
contract, binding invariants), but that marker alone does not admit a test into
the unit-only release packet.

## Required-application observation budget

The explicit T3 case below runs the actual caller-owned Machines CLI against
twelve fixture-owned required applications and 128 capability-only plugins.
Native readiness scripts run; no installer does. It retains New's original
55-second execution allowance plus five seconds for settlement, with a separate
75-second test observer covering fixture work. It also checks the returned
required result, not only process exit or a work-count proxy. This synthetic
roster is not proof of installed application health or a latency distribution.

```text
python tools/run-plugin-tests.py agent-machines --allow-explicit-tiers --test-file tests/test_installation_repair.py -k readiness_only_roster_respects_the_original_caller_budget
```

The focused `-k readiness_only` controls cover no payload walk or installer
qualification, missing inventory, retained runtime claims, selected optional
prerequisites, changed actions/declarations, and scope-bound digest refusal.
The actual-caller case includes a generic v1 payload command and a core v2 owner.

## Lint + contract gates (before a push)

The install-contract guard exercises actual Worktrees receipt-judgment source
with a fake child boundary. Its focused source-mutation controls verify that
missing/stale receipts and busy/deadline outcomes cannot become success.
It also exercises the legacy monitor result adapter with a fake transition,
including failed, refused and busy outcomes and the previous-live flag.
The native installer wrappers and actual service cutover remain separate tests.
The admitted daemon-cwd units exercise the Codespaces owner launch with a fake
child, checking a durable working directory and runtime-root log outside
replaceable snapshot/version trees. They do not cover other launchers or a live daemon.

```bash
ruff check --select F,E9 <touched .py files>   # fast lint (pyflakes + syntax)
python tools/check-install-contract.py         # runtime-plugin install contract — zero violations
python -m pytest -q tools/promotion_unit_tests/test_daemon_cwd_contract.py  # Codespaces owner launch boundary
python tools/check-version-consistency.py      # plugin.json / pyproject / marketplace versions agree
python tools/check-marketplace-isolation.py    # report-only legacy installation inventory
python libs/payload-invocation/generate.py --all --source-check  # canonical producers and owned output inventory
python tools/sync-installation-context.py --check  # inert exemplar copies match the canonical primitive
python tools/sync-peer-launch.py --check  # shared native peer boundary matches packaged consumers
python -m pytest -q libs/peer-launch/tests  # canonical/vendor packaging contracts
python -m pytest -q libs/installer-readiness/tests  # schema/discovery/graph fixtures
```

For a materialized candidate, Invocation's `--all --check` and Installer
Engine's default `verify()` retain actual-copy equality. A successful Dev
source/layout check is not that evidence; the promotion copier verifies its
real writes before freezing the candidate.

## Per-plugin coverage (unit suites)

- **agent-bridge:** transport, sessions, config, CLI, and the **Session Host**
  (framing, reattach/ack/buffering, reap logic, protocol-aware turn boundaries,
  version-mux, host-index persistence). Preference-authority tests require a
  real release-label mutation, exercise the promotion fallback writer and
  materializer against a disposable payload, and reject old receipts after
  executable authority-code changes. Only standalone root-module release
  labels are normalized; the complete staged authority closure remains bound.
- **agent-dispatch:** queue/coordinator/supervisor behavior plus the opt-in
  worktree-focus `sessionStart` kernel (payload cwd authority, Git/config and
  agent-worktrees status-core gates, strict bounded input, exact config shape,
  symlink/reparse and contaminated-Git-environment rejection, exact output,
  process-cwd isolation, and live platform-aware Bash/PowerShell parity).
  The targeted `-k procutil` smoke lane also exercises same-cell sibling
  invocation with disposable real venvs, shipped peer governance/resolvers,
  native argv/stdio fidelity (including Windows PowerShell 5.1 resolution),
  execution-time receipt rejection, legacy parity, and a two-cycle windowless
  Windows parent. POSIX runs additionally require the normal symlinked venv
  interpreter to remain usable. These are bounded contract samples, not a
  duplicate of the exhaustive installation-context adapter matrix.
- **agent-index:** fail-closed effective repository/required-state-root
  activation, gated command/scope contribution, non-mutating CLI admission,
  base-only explicit first-use provisioning, attributed dispatch-managed host
  declaration and interpreter-only launch, unsupported/missing-supervisor
  inertness, and ordered direct-SSH routing parity. Agent-dispatch also consumes
  the shipped declaration/provider in its materialization and launch tests.
  Installer fixture contracts in `test_install_engine_adoption.py`,
  `test_installer_staging.py`, and the two
  `test_installer_uv_editable_lib_preinstall_{sh,ps1}.py` files build explicit
  canonical Dev and self-contained materialized layouts independently of the
  checkout running the tests. The real materializer supplies payload-local
  engine assets and regenerates native human-output declarations after wrapper
  changes. Cell coordinator cases retain the live payload-replacement/no-reentry
  proof in both layouts and shells. Procutil fallback cases remove only their
  copied local procutil directory, assert its absence, and retain canonical
  dependency-policy/private-copy checks; an existing local copy is not evidence
  that fallback ran. Run all four files in source and generated-consumer views.
- **agent-codespaces:** config, lifecycle, resolver, and the credential relay.
  The focused `-k worktrees_peer` lane covers every same-cell worktrees adapter,
  refusal propagation (including claim and auth fallbacks), and cross-context
  cache isolation. The dispatch `-k procutil` lane owns the shared real-process
  proof for both plugins, including the source-only CodeSpaces hook outside the
  repository with a dependency-free disposable bootstrap interpreter.
- **agent-containers:** config, lifecycle, the lease broker, and the resolver.
- **agent-mcp:** config loading, auth injectors, transports, bridge framing, the
  decorator pipeline; the code-mode Node tests skip automatically when `node` is
  absent. The native POSIX cutover integration waits for the exact spawned
  daemon's published control endpoint, not just `active.json` existence:
  conservative custody history may precede publication of the active route.
  The native PowerShell owned-construction cleanup case records fixture-only
  abort/reply/exit stages and Python stacks at eight-second intervals. An
  unchanged 20-second subprocess timeout prints bounded diagnostic readback
  before re-raising the original exception; no deployed helper is instrumented.
- **agent-ssh:** transport rendering, managed OpenSSH fragment source identity,
  tri-state reconciliation, stale/duplicate quarantine, bounded warnings,
  report-only doctor parity, and reachability-vs-hygiene separation.
- **agent-logger:** session segmentation and lookup, local/remote sync targets,
  verified provider-rescue ingestion, generic provenance transport, compaction,
  and background-chronicle source/sink orchestration.
- **agent-worktrees:** a large suite covering worktree lifecycle, the
  status/tracking model, PR flow, activation-preserving installed-inventory
  updates, and the Picker-facing engine contracts.
- **Worktree Manager:** its standalone suite includes the production Textual
  **Picker** UX, golden, cache, pivot, streaming, steering, profile, SSH-source,
  selection, capture, mock, and PNG-validation corpus under
  `worktree-manager/tests/production_picker/`.
  The real-child feed deadline regressions separate process/SSH startup from
  admitted snapshot/update transactions, retain stale rows across transport
  expiry, reconnect to a complete replacement generation, and verify child
  cleanup on cancellation, including partial JSON lines returned after child
  termination. Malformed output without watchdog expiry and explicit terminal
  peer refusals retain no-respawn coverage.
  The configuration-action contracts exercise native forms, Cancel-default
  confirmation, safe argv substitution and visible command failures. For the
  real Workers-to-override acceptance lane, prepare agent-dispatch's test venv
  with `python tools/run-plugin-tests.py agent-dispatch --prepare-only`, set
  `WORKERS_CONFIG_TEST_PYTHON` to that venv's interpreter, then run
  `python tools/run-standalone-tests.py worktree-manager` with
  `PYTEST_ADDOPTS='-k workers_picker_real_override_round_trip'` (add `--prepare`
  only when the Manager test environment needs provisioning). It uses the
  current source and shipped Workers form with an isolated registrar declaration
  and override store: Cancel writes nothing, Disable sets the logical override,
  Enable removes it, and declaration bytes stay unchanged. Ordinary Manager
  runs skip this cross-distribution lane when no interpreter is supplied.
  The original-request recovery lane uses the same opt-in pattern:
  set `COPILOT_TEST_FORM_REQUEST_OWNER_PYTHON` to the existing agent-dispatch
  test interpreter, then run the Manager standalone runner with
  `PYTEST_ADDOPTS='-k native_form_requests'`. Its real owner/CLI tests cover
  both category contributions and rendered Textual forms/global recovery at
  80x24 and 120x40: retain before a lost response, replace the card, reopen a
  client without the row, inspect the original receipt, cancel and separately
  confirm exact retry. Assertions preserve original fields/fingerprint and
  one answer/wake. The owner, storage and provider attribution are isolated
  fixtures; list/subscription refresh is disabled on every fixture runtime,
  including the fresh client, so replay cannot start an ambient board producer.
  No installed registry, live task or worker is used. Ordinary runs
  skip this lane without the explicitly selected dependency interpreter.
- **ai-attribution:** payload-only hook tests covering authoritative
  `sessionStart` payload cwd, malformed/missing payloads, git-repo gating, safe
  defaults, bounded operator/repository config discovery, symlink/reparse
  rejection, repository authority boundaries, normalized guide and
  host-qualified account/remote validation, same-owner cross-forge isolation,
  injection-shaped data, complete JSON control escaping, missing-script
  fallback, setup-skill structure, exact JSON and context size, and live
  Bash/PowerShell parity when `pwsh` is available.
- **agent-conduct-guidance:** scoped conduct and projection tests, explicit
  assignment discovery, and inert optional model resolver contracts. Actual
  projection consumers omit the retired ambient delegation strategy; model
  lookup remains independent of budget information.
- **budget-guidance:** current usage/quota attribution, field freshness and
  contradictions, monthly pace estimates, optional economy suggestions and
  real CLI/session-cue consumers. Budget information neither selects models
  or delegates nor blocks requested work.
- **customizing-copilot:** payload-only scanner and plugin-state helper tests
  covering installed inventory vs. user/repository activation, dry-run-safe
  user deactivation, loaded-plugin
  discovery, project/`.claude`/`.ai`/suite agent ownership, Task-disabled
  exemptions, MCP readiness and equivalent fallback checks, origin/version-aware
  external advisories, collision remediation, and counts-only context inventory.
- **context-handoff:** payload-only hook tests covering the owner/version-marked
  continuity kernel, its 2 KB budget, the bounded 3 KB adjacent agent-worktrees
  compatibility catalog, plugin-root compatibility aliases, incomplete-payload
  failure-open behavior, oversized-catalog fallback, and cross-platform kernel
  and catalog semantics; Node tests cover thresholds, configuration, successor
  seeds, storage, prompt-before-session candidate association, retry-safe
  acknowledgement/takeover, payload-local extension-free CLI parity, exact
  plugin-root resolution, and guidance. The identity-free
  `context-handoff-eval` clean-room fixture has a deterministic self-test and an
  opt-in Tier-E run that emits seed/token, turn/tool, timing, fidelity, and
  lifecycle metrics.
- **efforts:** payload-only policy-producer tests covering exact repository
  adoption, authoritative payload cwd, Git/config containment, malformed input,
  symlink/reparse rejection, contaminated Git environments, manifest-derived
  ownership, the read-only cross-repository adoption probe, the 1 KB context
  budget, setup-fallback structure, and live Bash/PowerShell parity when `pwsh`
  is available. Hook registration remains deferred while #1234 prevents
  deterministic multi-plugin context aggregation.

---

## Opt-in end-to-end smoke tests (real infrastructure)

Some flows can only be validated against **live** infrastructure. They are
therefore **opt-in** and take **no defaults**: a target's identity — CodeSpace
names, repos, checkout paths, launch commands — is account- and
environment-specific and must never be hardcoded into a test. Each such module
**skips itself** (naming exactly which variables are missing) unless the caller
supplies the target via the environment, so it **never runs in the default
suite**.

The calling agent/operator is responsible for providing the target and the
account/auth preconditions listed per module below.

### agent-bridge — CodeSpace Session-Host path

**Module:** `plugins/agent-bridge/tests/test_codespace_e2e_smoke.py`

Exercises the real remote stack the unit suite (fakes/monkeypatch) cannot: `gh`
CodeSpace SSH → far-side Session Host bootstrap → the `-L` forward → the
credential relay → ACP over the forwarded loopback — including the reattach
guarantee (adopt the surviving child, not respawn) this session-host work
hardened.

**Required environment (ALL must be set, or the module skips):**

| Variable | Must contain |
|----------|--------------|
| `AGENT_BRIDGE_E2E_CODESPACE` | raw or friendly CodeSpace name (Available/resumable for the active `gh` account) |
| `AGENT_BRIDGE_E2E_REPO` | `owner/repo` the CodeSpace hosts (e.g. `example-org/example-web-codespaces`) |
| `AGENT_BRIDGE_E2E_WORKSPACE` | absolute workspace checkout path **on** the CodeSpace, used as the ACP cwd (e.g. `/workspaces/example-web`) |
| `AGENT_BRIDGE_E2E_ACP_COMMAND` | the far-side shell command that launches copilot in ACP mode, passed **verbatim** (e.g. `cd /workspaces/example-web && copilot --acp --stdio`) |

**Optional (timeouts only — operational, not target identity):**
`AGENT_BRIDGE_E2E_BOOT_TIMEOUT` (default `420`s), `AGENT_BRIDGE_E2E_TURN_TIMEOUT`
(default `240`s).

**Preconditions the caller owns (not asserted by the tests):**
- `gh auth`'s active account **owns** the CodeSpace — the resolver is
  active-account sensitive (the account-flip gotcha), so a wrong active account
  surfaces as "Codespace not found".
- If a turn needs ADO/git, the daemon's credential relay is reachable. The smoke
  turns are intentionally trivial and need neither.

**Run** (with the four `AGENT_BRIDGE_E2E_*` variables exported):

```bash
python tools/run-plugin-tests.py agent-bridge -k e2e \
  --allow-explicit-tiers --allow-host-state
```

The explicit host-state escape hatch is required because these opt-in checks
intentionally use the caller's authenticated provider configuration.

**Flows:**
1. `test_e2e_dispatch_and_single_turn` — cold dispatch reaches `IDLE` with a live
   child pid and runs one ACP turn to a clean terminal stop reason.
2. `test_e2e_reattach_adopts_same_child` — a stop + resume adopts the **same**
   far-side child (pid) and ACP session id — reattach, not respawn — then runs
   another turn to prove the reattached session is live.

> `stop` here is a graceful detach (the analogue of a transport drop); a true
> mid-turn socket sever (tunnel flap) and a "silent long turn survives a drop"
> flow are natural follow-ups once these are validated against a live CodeSpace.

### Adding a new opt-in e2e module

Follow the same contract so it stays safe-by-default and reproducible:
- Gate the **whole module** on its required env with
  `pytest.skip(<reason listing what is missing>, allow_module_level=True)`.
- Take **no defaults** for target identity — read every target value from the
  environment; only *timeouts* may carry internal constants.
- Assert observable end-state, not exact model output (turns vary).
- Document the module and its variables in this file.

## Optional native registration inspection

The additive native installer runtime-only seam has focused controls in
`tests/test_runtime_install_driver.py` and `tests/test_installer_config.py`.
Select both through `tools/run-plugin-tests.py agent-worktrees` with
`--allow-explicit-tiers -k runtime_only`. Driver controls preserve the exact
admitted flag through participant re-entry and reject contradictory arguments
before bootstrap. Real shell controls retain ordinary existing-config inference,
skip it on repeated runtime-only selections, and exercise the actual front-door
parser's pre-admission refusal. These checks do not construct a runtime or
qualify a fresh native installation; keep that separate disposable-host evidence
and higher-level caller migration explicit.
The manual `worktrees-runtime-only` portfolio runs this selection plus the
existing native driver re-entry and default scaffold controls on Linux and
Windows. Supply the branch and exact `source_sha`; mandatory native cases
cannot be missing or skipped. The copied cold-driver payload excludes unrelated
library test trees and retains the original item and child deadlines. A bounded
diagnostic snapshot reports its real request state and log without changing
custody, readiness or settlement.

The `registration-inspection` portfolio in `installation-context-full.yml`
runs only the focused native registration reader/CLI and incoming bootstrap controls on Linux and
Windows. Supply the published branch and its exact `source_sha`; both jobs
verify that commit before testing. The existing bounded runner's process,
storage and 30/300/900-second item/group/plugin limits are unchanged, and the
portfolio adds no scheduled or required-unit work.

The same local selection is:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_registration_inspection.py
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_incoming_registration_inspection.py --test-file tests/native/test_incoming_registration_inspection_native.py --allow-explicit-tiers
```

It exercises unique canonical/legacy aliases, preserved exposure/editing mode,
fresh default and conflict/unknown-source refusals, native installation-cell
selection and the actual CLI from a neutral directory. Existing repository
inventory remains a separate old-caller control. Configuration bytes stay
unchanged; no actual registration, installer, service or authentication provider
is started. Hosted jobs require both native consumer cases and refuse skipped
or failed evidence. A separate incoming step requires real cold construction
and the existing reader with an unusable selected predecessor. Both exposure
choices, a fresh default, ambiguity and an actually unusable incoming UV tool
run twice, preserving registry, selector, malformed owner and binding bytes.
This uses the ordinary
constructor and owned-child cleanup, not installation or service startup.
The native test retains its 180-second child and 300-second item limits;
the original reader packet remains separate. These checks do not qualify
caller migration or installed-host recovery.
Two focused controls also supply a deliberately lossy normalizer while retaining
real distinct directories and native file-identity checks. They reject foreign
selection and inconsistent roots without provisioning a case-sensitive Windows
directory or claiming that separate filesystem-mode acceptance.

## Optional paired-worktree finalization

For paired-worktree finalization, select
`tools/run-plugin-tests.py agent-worktrees --test-file tests/test_paired_claims.py
--test-file tests/test_paired_finalize_integration.py`.
The first file covers claim mutation/refusal and concurrent lifecycle ownership.
The second uses isolated fresh Git repositories and actual CLI refusal paths,
then proves bottom-up sibling settlement, BOTH-finalized removal retention,
and preservation of untracked, modified and unpublished committed knowledge.
These are on-demand filesystem/process controls, not additions to required
unit-only CI or a claim of installed-runtime rollout.


## Guarded Windows observation recovery

Run `test_observation_review_regressions.py`,
`test_windows_observation_custody.py` and
`test_status_monitor_guarded_recovery.py` through the normal agent-worktrees
plugin test runner. They protect main-thread cancellation propagation,
background custody transfer, secondary cancellation during final updater
settlement, real singleton-lease contention, deferred cache invalidation and
strict serving/retirement receipts. The canonical procutil
`test_windows_observation_spawn.py` injects all CRT/Win32-only dependencies;
its pure creation/cleanup cases are not platform-skipped.

For explicit native proof on Windows, set
`AGENT_WORKTREES_NATIVE_RECOVERY=1` and select
`tests\test_status_monitor_guarded_recovery_native.py`,
`tests\test_status_monitor_guarded_cutover_native.py` and
`tests\test_git_observation_native.py` with repeated `--test-file` arguments
to `tools\run-plugin-tests.py agent-worktrees`. Keep the existing isolated
runner and finite timeouts; do not use host-state opt-outs. The native helpers
are disposable strict Job-owned processes, not production monitors or agents.
They exercise real loopback framing, route/identity barriers, a successful
published-successor handoff, failed/unready promotion, accepted-but-unsettled
predecessor shutdown and exact native exit. These process-level witnesses stay
outside the required no-actor promotion unit packet and do not qualify an
installed rollout or a performance threshold.

The focused best-effort monitor-retirement packet combines
`tests\test_status_monitor_guarded_recovery.py` and
`tests\test_status_monitor_guarded_cutover_native.py` with
`--allow-explicit-tiers --timeout 900 -k "not test_native or keeps_incoming_after_failed_predecessor_recovery or drain_receipt_failure_rolls_forward or passive_health_cannot_retire_old or successful_cutover_activates_then_waits"`.
It retains eight native cases plus the pure contract controls. Disposable
provider admission allows 60 seconds for real imports/readiness; each native
case has a 150-second bound including two starts and Job cleanup. The aggregate
allowance includes these native setup costs, not a change to production startup
or drain deadlines. Controls require actual outgoing-process exit and old-port
rebinding while the incoming owner serves, incoming failure rollback and durable
failed-retirement evidence for unconfirmed cleanup. Run other native files as
separate bounded packets rather than combining every actor test under one
default suite allowance.

The ordinary asynchronous installer controls additionally select
`tests\test_status_monitor_cutover_helper.py`,
`tests\test_python_runtime_install.py` and the native cutover file with
`-k "async or installation_promotes_successor or serving_incoming_monitor"`.
The four native async cases pair an incompatible or timed-out predecessor with
a serving or capability-incompatible target. They require real old-process
exit, old-port rebinding and new serving health only for successful targets;
failed preparation must preserve the predecessor. Unit controls separately
retain the original runtime slot through missing old metadata/control failure
and require cleanup failure to preserve successful irreversible publication.
All cases using this disposable provider share its 60-second import and
150-second item bounds, including two starts and Job cleanup. Its separate
180-second maximum lifetime prevents early fixture expiry while another
provider imports; strict Job cleanup still runs at the item boundary.
These allowances address fixture import contention, not a production startup
or performance claim.
The same packet also selects
`unnegotiated_predecessor_is_not_drained_when_incoming_fails`: a real legacy
peer stays serving and receives no drain/shutdown mutation when the incoming
peer refuses promotion or remains unready. Pure controls pair refused/timed-out
drain with failed publication/promotion and require zero native retirement.

## Explicit paired Sync and first launch

Worktrees' optional `--test-file tests/test_worktree_sync.py
--test-file tests/test_local_cache_lifecycle.py --allow-explicit-tiers` packet
checks explicit paired Sync using real isolated Git repositories, preservation
on dirty/ahead/active/fetch/late-merge failures, launch-admission contention,
legacy single-repository effects and both Resume environment protocols.
Knowledge-side Sync also reloads fetched harness policy through the active
command cache and revalidates the pair before any plugin write.
Changed synchronization targets require a retry, and incomplete saved bindings
cannot downgrade to single-repository success.
Narrow remote refspecs still refresh the selected default branch; a deleted
target blocks preparation without reusing its stale tracking ref.
Manager's `--test-file tests/test_launch_admission_ordering.py --test-file
tests/production_picker/test_worktree_sync_actions.py --allow-explicit-tiers`
packet exercises actual native entry without installation or plugin
recomposition, the affirmative Sync dialog, partial-result failure and an
older receiver's pre-effect parser refusal without replay.
The Manager New control joins its real resolution and relocated native entry,
requiring instruction preparation before handoff and exactly one creation.
It also checks the final provisioning environment. Both receiving resolver
surfaces retain provisioning for the typed `--first-launch` handoff with an
already-created local ID; ordinary Resume stays fenced. A private ConPTY case
drives the actual Windows shell-owned, mux-enabled handoff and checks that this
intent survives in literal arguments without requiring extra environment fields.
Completed Sync results support Up/Down, PgUp/PgDn and Home/End navigation;
the render/key regression reaches a fourth failed pair and its partial progress.
The existing standalone-instructions packet retains native explicit composition.
Platform skips are not passes; these source controls neither deploy the change
nor qualify installed stability or the complete fresh Manager installer.
The manual `installation-context-acceptance.yml` input `native_launch`
executes these packets and the existing fresh Manager runtime acceptance on
hosted Linux and Windows at the dispatch's immutable source. It changes no
required PR/promotion packet and performs no operator-host rollout.
Manager's existing `tests/test_manager_engine_adapter.py` packet checks that
source-qualified preparation copies the original pinned payload, not the
dependency resolver's rewritten build manifests. Fresh runtime acceptance
continues to verify the installed payload's original source identity.

The existing `installation-context-full.yml` `vault-activation` portfolio runs
the focused Vault launcher and cold configuration-capture regressions on both
Linux and Windows, retaining source-pinned JUnit artifacts. The stock runner
owns fixture admission, private runtime slots and cleanup. These integration
results are separate from the required unit-only PR packet; platform skips
are not native coverage or installed-service acceptance.
