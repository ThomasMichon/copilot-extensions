# Installed Manager lifecycle before promotion

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Tracked under #6237, extending the existing [installed Manager qualification](../installed-manager-tty-qualification/README.md)
(#11377), [release control](../dev-branch-release-pipeline/README.md) (#3336)
and [configured-feed construction](../feed-construction-release-gate/README.md)
(#7529). Standing intent:
[installed lifecycle before publication](../../../visions/release-pipeline/README.md#installed-manager-lifecycle-before-publication).

## Requested outcome

The full operator wording is retained in the parent's
[Request](README.md#request). Installation must yield a usable Worktree
Manager, not just a successful installer. The requested user flows are:

1. Set up a representative harness, install the suite, launch Manager and use
   its controls to create a worktree and start an actual Copilot session.
2. Exit Copilot normally, open Manager again and Resume that worktree with its
   original conversation continuity.
3. While a session remains active, open another Manager and Open the same
   session without replacing or duplicating it.

The operator permits mocking Copilot's endpoint to remove token dependence.
That is not permission to replace the CLI, Manager, worktree owner or mux with
successful callbacks.

## Existing evidence and missing boundary

- #11377's installed-TTY witness performs real candidate Manager/engine
  installation, registers a generic harness, invokes generated CMD and
  PowerShell project binstubs and observes ConPTY rendering, input and exit.
  Its negative control rejects install-success/launch-failure. It does not
  create a worktree or operate a session from the Manager UI.
- `tests/native/test_resume_pane_startup.py` in Manager qualifies concurrent
  production Resume launch plans and real private PSMux attachment. Its
  private engine resolution and finite Copilot setup fixtures do not establish
  installed suite operation or an actual CLI conversation.
- Worktrees' `tests/native/test_headed_worktrees_copilot.py` already runs the
  actual Copilot executable against a loopback deterministic provider and
  observes fresh/resumed history through real hosting boundaries. Reuse its
  endpoint and continuity techniques rather than adding a second model rig.
  Its launch-owner cases are not Manager-driven Create/Resume/Open acceptance.

These are complementary witnesses, not three proofs of the requested end-to-end
flow. Preserve their existing assertions and do not count a source test as
installed or frozen-candidate release evidence.

## Ownership and scope

- Campaign driver owns this CI/promotion requirement, plan, review and the new
  `worktree-manager/tests/native/test_manager_installed_lifecycle.py` witness.
  It consumes existing qualified helpers without taking their edit ownership.
- The accepted required-readiness contributor owns only the new
  `worktree-manager/tests/native/manager_installed_readiness_profile.py` and
  `worktree-manager/tests/test_installed_readiness_profile.py` input/control
  modules. Actual runtime discovery, installation and execution remain in the
  shared lifecycle witness, not a second fixture or promotion job.
- The existing #11377 qualification coordinator retains native installation,
  TTY fixture source and its open Main-activation PR. Agree actual overlapping
  helper/runner/result edits before touching those files; independent new-file
  work does not wait for that entire PR to merge. This does not supersede it.
- #3336 retains the promotion transaction and independently governed Main
  controller; #7529 retains configured-feed construction and its wider matrix.
- Existing launcher, path resolver, launch-admission, Copilot driver and
  PSMux/session owners retain fixes discovered by the witness. Report a precise
  failing boundary instead of waiving its policy inside the fixture.

The request authorizes source qualification work through normal review. It does
not authorize an operator installation, shared mux/service repair, new runner
capacity, credential transfer, promotion retry, control-pin change or Main
activation. A private fixture backend is not the operator's PSMux namespace.
No new daemon, scheduler, promotion writer or competing qualification effort
is introduced. Plan approval does not imply acceptance of a source handoff.

## Plan

The following implementation decomposition is **agent-recommended**.

### Phase 1 - Review and accept existing-owner seams

- [x] Review the release-vision extension and this linked parent amendment
  before implementation. #11739 merged after current-head Copilot approval,
  correction of the exception-list finding and required green CI.
- [ ] Agree actual overlapping native-helper and release-result integration
  seams with #11377 and #3336; preserve their active PRs and source custody.
  Proceed independently on the assigned new witness/profile files rather than
  make Main-PR completion or a blanket acknowledgement a prerequisite.
- [ ] Select the representative generic harness and its complete declared suite
  installation recipe from canonical owners. Enumerate what is installed and
  enabled; a manually assembled Manager/engine pair cannot be called a suite
  installation without proving the selected suite closure.
  Frozen payload delivery is verified by #11972 and exact-source run
  42933306312 for all 24 declared payloads. Run 43014684583 additionally
  verifies complete fresh runtime construction and actual Machines readiness.
  Full fresh lifecycle acceptance remains unverified; this item stays open.

### Phase 2 - Extend the authentic installed consumer

- [ ] Reuse the canonical candidate installation, provenance and native
  terminal owners in fresh private HOME, config, registry and mux state.
  Keep configured feed/security policy; do not borrow selected host runtimes.
- [x] Invoke generated project entry points, observe the installed Manager,
  and use normal UI controls for Create. Prove an actual Git worktree,
  installed launch resolution, real Copilot readiness and a deterministic
  request/response through the local inference endpoint.
  Installed-core proof: #11864, exact-source run 42892225611. Complete selected
  suite installation remains a separate open item above.
- [x] Exit Copilot through its normal user flow, close and reopen Manager,
  select Resume, and prove the same worktree and durable conversation with a
  further endpoint-backed turn. Do not repair tracking or rewrite session IDs.
  The same installed-core run retains the original durable conversation.
- [x] Keep a CLI session active while starting a second Manager, select Open
  and prove attachment to that exact native process incarnation/session/pane.
  Closing Manager or detaching its view preserves the original owner and its
  ability to complete another turn; no duplicate Copilot is admitted.
  The same installed-core run verifies native birth/lock ownership and detach.
- [ ] Exercise the relevant cold and retained/warm backend states without
  allowing accidental reuse of a host server to supply success.

### Phase 3 - Bind the complete result before publication

- [ ] Extend the existing qualification result and refusal rules with explicit
  installed, Create/session, exit/Resume and active-Open outcomes. Bind the
  frozen candidate, installation closure, CLI/mux versions and hashes, harness
  configuration, fixture/endpoint inputs and producing run/attempt.
- [ ] Keep source CI's fast unit/guard contract separate from native execution.
  Use the existing explicit native lane for exact-source development proof;
  require the complete bounded candidate witness before artifact publication
  or promotion admission. No optional/manual result may masquerade as the
  mandatory candidate result.
- [ ] Missing, skipped, failed, timed-out, incomplete, duplicate, stale,
  wrong-candidate or wrong-input evidence refuses admission. Reuse requires
  independent verification of complete equivalent inputs and original success;
  a prior installed-TTY-only receipt cannot satisfy the extended contract.
- [ ] Preserve independent unit, artifact, integrity, signing, review and
  publication gates. Record timings within the existing release objective;
  do not manufacture a pass by increasing deadlines or dropping witnesses.

### Phase 4 - Prove enforcement and record acceptance

- [ ] Earn real isolated native positive and falsifying results through the
  supported execution lane, with diagnostics and verified owned cleanup.
- [ ] Through the existing release owner, prove an actual frozen candidate
  cannot publish with a failing lifecycle result and can proceed with complete
  successful evidence. Source wiring alone is not enforcement.
- [ ] Land any required Main workflow adoption through its separate reviewed
  control path. Do not infer activation from a Dev merge or steal the
  existing #11377 activation PR.
- [ ] Update TESTING/pipeline documentation and the parent journal with exact
  source/native/candidate/activation evidence, remaining platform limits and
  named repair ownership. Resolve the matrix before marking this slice done.

## Validation Plan

- [ ] Fresh representative harness and authentic selected-suite installation:
  no source imports, preexisting completion state, hand-injected launch plan,
  direct low-level Create/Resume replacement or production-policy bypass.
- [ ] Native Windows entry points traverse installed path resolution, actual
  admission, generated launch assets, real terminal and private PSMux. A Linux
  scenario alone cannot qualify the reported Windows failure classes.
- [x] Real Manager Create yields the expected Git worktree and real Copilot
  conversation. The endpoint records an actual request and deterministic
  response; UI rendering, ready files or process existence are insufficient.
- [x] Normal Copilot exit and a fresh Manager invocation reach Resume through
  real discovery. The original conversation survives and another turn completes
  without duplicate history, stale-path repair or manual session-state edits.
- [x] A second Manager's Open attaches to the exact active owner rather than
  creating a replacement. Original process birth, session and pane identity
  remain consistent; detach/Manager exit leaves that session usable.
  These three action/continuity items are verified by #11864's installed-core
  run 42892225611; they do not close selected-suite or promotion acceptance.
- [ ] Install-success/entry-launch failure, launch/path-resolution refusal and
  fixture-scoped mux connection failure each fail the appropriate lifecycle
  phase. Expected-negative classification requires its specific observed
  refusal; generic fixture, infrastructure or cleanup failure is not credit.
- [ ] All credentials and inference traffic remain fixture-local. Real CLI
  startup/hooks are retained; a network or authentication dependency must be
  diagnosed, not silently satisfied from the operator's login or settings.
- [ ] Deadlines, bounded output and retained diagnostics cover normal exit,
  timeout, cancellation and failure. Settle only owned native descendants and
  private mux resources. Unconfirmed cleanup retains exact custody and fails.
- [ ] Gate controls reject partial phase success, old TTY-only evidence and
  every absent/non-success/mismatched identity without publication authority.
- [ ] Actual candidate and activation evidence proves the gate is required;
  green source/unit checks, callback receipts and an informational native job
  are not promotion protection. Record each unexecuted tier explicitly.

## Journal

### 2026-11-09 - Requested lifecycle mapped onto existing qualification

The Campaign driver captured the full request in the parent and inspected
the existing installed-TTY, native Resume, real-CLI loopback-provider and
frozen-candidate gate seams. Coordination was posted on #6237 and #11377.
No native or workflow implementation is claimed; exact extension ownership
and reviewed intent precede those edits. Existing TTY activation remains
with its current coordinator, and all broader campaign gates remain open.

### 2026-11-09 - Reviewed lifecycle contract accepted

#11739 merged at `6c5be356d7a66bd10c9895d1270b607e312cff09`.
The [dated acceptance record](journal/2026/11.09-installed-manager-lifecycle.md)
retains the review, qualification limits and next existing-owner seam.
Only the reviewed-intent item closes; native lifecycle implementation,
suite closure and actual candidate enforcement remain unproved.

### 2026-11-09 - Independent file split instead of whole-PR blocking

Following the operator's coordination direction, the Campaign driver traced
the owned Main PR's publishing controller across machines and posted the exact
source split on its PR thread. Direct peer delivery was not acknowledged;
the existing session and control transaction were preserved.
The required-readiness contributor accepted the two new companion/control
paths above. The Campaign driver reserved the distinct lifecycle witness.
Shared helper/runner/result edits still require concrete overlap coordination;
neither this split nor the open Main PR establishes executed lifecycle proof.

### 2026-11-10 - Isolated the deferred Windows argument boundary

The second disposable native run (42766907684, candidate
`5dbe28a88ea64d482c7513f748a551a65887a605`) passed the repaired quoted-process
census and created the actual private PSMux session, then the pinned CLI
rejected its command before inference. Direct tests against that exact pinned
CLI accepted the configured flags and both directory spellings; those inputs
were not removed or weakened.

A separate private argv witness reproduced PowerShell's `-File` conversion
of `-C=<drive>:\...` into two arguments. The ordinary startup manifest and
full pane-manifest route preserved it. The deferred seed owner can reach a
later canonical setup spawn after mux serialization, leaving that final
boundary unprotected. The fix reuses the existing copied shell and one-shot
manifest at that spawn, retaining the original admission and seed ownership.
No provider mock, policy, budget or native-session identity was changed.

Focused source validation passed 254 shell/seed controls with 16 platform
skips, followed by both native final-spawn success/failure variants. Lint,
install-contract and module-size checks passed. These controls are not the
installed Manager lifecycle result: draft #11864 remains unqualified until
the exact updated source completes the disposable native witness. Authentic
selected-suite installation, ordinary upgrade and frozen-candidate mandatory
enforcement remain open under this parent.

### 2026-11-10 - Real CLI startup reached, conversation assertion unresolved

Disposable run 42772527770 tested the exact repaired source
`f179ee7789f0209bec48840c884d2ace01de54cb`. The native CLI started and rendered
the seeded prompt instead of rejecting its argv. Qualification still failed
at the first completed-conversation assertion. Recent terminal evidence includes
missing enabled payload warnings and redraw-fragmented reply text; it does not
identify which history/rendering predicate failed. No lifecycle phase is
credited from that output.

A separate private ConPTY probe of the same pinned CLI completed one actual
loopback request, persisted the user/assistant turn and normal exit. That is a
diagnostic without installed Manager, mux or plugin closure, not acceptance.
The lifecycle witness now retains bounded history counts and prompt/reply/turn
predicates separately from its terminal-reply predicate on failure. Its
original completion, identity, terminal and cleanup assertions remain intact.
Resolve the measured predicate and missing payload closure before treating
another native attempt as qualified; do not infer a fix by removing the
terminal check or extending its timeout.

All five focused diagnostic controls passed, including absent, unfinished
and completed-but-unmatched history. The same native case will retain these
observations on its next exact-source attempt; its pass criteria are unchanged.

### 2026-11-10 - Measured a terminal-stream false negative

Run 42775388223 at `26a4adf7615409d8936af5cadfee1b700262c5d7`
retained one actual history with 11 events: the exact prompt and reply were
present and one assistant turn had ended. The raw-stream terminal predicate
alone was false. A PSMux redraw stream is not a rendered screen: stripping
escape sequences does not apply cursor edits or recover text reused by a
later frame.

The witness now falls back to the real private mux's bounded `capture-pane`
view, only after completed history matches. It resolves and verifies that
history's actual worktree/head/session binding before targeting the pane.
Rendered reply, native process identity, normal exit and same-conversation
Resume/Open assertions remain required. This is not a callback, terminal
assertion removal or a timeout extension. Missing enabled payload warnings
remain a separate unresolved fixture-closure observation, not an accepted
degraded installation.

### 2026-11-10 - A later run stopped before completed history

Run 42776996317 at `9756e5cc5ae89fa0280046ac5913fff142be69bc`
retained one history with six events and the expected prompt, but no reply or
assistant turn end. The rendered-pane fallback was therefore not reached.
This is a distinct unfinished-turn failure, not another raw-stream false
negative. The owned witness now retains bounded event-type counts and the
private provider's request count without request content. All 14 source
controls passed; native qualification and the missing enabled-payload closure
remain open. No assertion, deadline, plugin policy or installed receipt changed.

### 2026-11-10 - Inference completed; pane read lacked explicit private scope

Run 42780390102 at `cb6a005bf0aadec6050b9c887de046cdc167b952`
recorded one actual provider request and one history with eleven events,
including the exact reply and assistant turn end. The pane fallback reached
the command-safety boundary and correctly refused an adapter command without
an explicit private socket/namespace. A subsequent diagnostic-file sharing
error during cleanup did not turn that refusal into qualification.

The owned witness now passes the selected real PSMux binary and its verified
private namespace directly. The guard remains enabled. Separately, lifecycle
preparation stages the exact candidate's two observed native launch companions
through the canonical marketplace materializer, outside the two already
selected runtime payloads. Catalogue/manifests and existing runtime bytes and
receipts are checked; activation and runtime installation scope are unchanged.
These are fixture repairs, not evidence that missing payloads caused inference
to stall. Native positive qualification, actual suite installation and required
frozen-candidate enforcement remain open.

Two local complete-catalogue materialization probes exceeded their fixed
180-second control deadline, first during pointer scanning and then during
unrelated runtime materialization. Passing the extractor's pointer candidates
removed the redundant pointer scan, but did not make whole-suite preparation
appropriate for this core witness. The implementation therefore stages only
the two observed payload-only launch companions and reports that limited scope.
It does not raise deadlines or credit either timed-out probe as qualification.

The narrowed source selection passed all 21 controls in 114.95 seconds,
including an actual Git candidate archive and canonical companion
materialization under the unchanged control deadline. The controls verify
byte-preserved runtime payloads/receipts, catalogue and manifest mismatches,
unsafe paths, absent companions and unexpected runtime companions, plus
command-safety admission of the explicit private-pane read. This is lightweight
source-consumer evidence, not the installed native lifecycle result.

### 2026-11-10 - Private pane proof passed; native owner count is ambiguous

Run 42784466120 at `3f5ec453b093ff1960175e153285e8a62c38d7a7`
recorded one actual request, one completed history with the exact reply and a
successful real private-pane capture. The retained companion inventory
confirms unchanged selected runtime payloads/receipts and its deliberately
limited scope. Other missing-catalogue-payload warnings are still visible;
the installation closure is not accepted.

The next assertion found two live Job-owned processes using the exact pinned
Copilot executable. Their executable identity alone does not establish whether
these are two independent CLI sessions or a runtime helper/owner relationship.
The witness continues to refuse the ambiguous count. Failure-only diagnostics
now retain each pinned candidate's parent PID and allowlisted CLI flag names,
plus the completed history's lock PIDs, without raw argument values. All three
focused diagnostic controls passed, including unavailable metadata preserving
the original duplicate-owner refusal. No native-owner assertion changed.

### 2026-11-10 - Reproduced the extension host, not a second conversation

Run 42786590873 at `bb360d3e3abb51df12678042e8c020d09f415e2d`
retained one lock-bound interactive process and a later-born direct child using
the same executable, without recognized CLI flags. A private same-version
Git-folder probe reproduced the child only after plugin discovery was enabled.
Its sole argument is `extension_bootstrap.mjs`, an ordinary file within the
private fixture. The parent alone holds the actual conversation lock.

The witness now distinguishes that positively identified extension host from a
second conversation owner. It still refuses unknown children, foreign ancestry,
older children, a helper claiming a conversation lock, additional owners and an
orphaned helper. Native handles and Job custody are unchanged. All eleven focused
controls passed. The actual pinned CLI probe selected the original parent and
proved normal exit left no native processes before fixture cleanup. This is
lightweight consumer evidence, not installed Manager acceptance; the next exact
native run must prove the same role binding through Manager Create/Open/Resume.

### 2026-11-10 - A fresh run stopped at dialog prompt observation

Run 42790346307 at `179a2541da52792a4193e0091185a1f14321b7e2`
timed out before Create, while observing the New dialog's entered prompt.
The retained raw fragment lacked its leading character. There is no native
conversation or hosted extension-role result from this attempt.

The owned witness now renders the actual fixed-size ConPTY stream incrementally
and checks the current dialog, rather than a concatenated redraw history.
Failure evidence includes that rendered screen. A private real-dialog
preflight also failed the rendered prompt check once; later focus/model
observations accepted the exact prompt with both key-burst and paste input.
The cause of the earlier missing prefix remains unproved. The witness uses
the supported, explicitly enabled bracketed-paste operation for atomic seed
entry and still requires the exact current prompt and subsequent durable CLI
history. It does not claim to qualify rapid key-burst input or repair that
production behavior. No input retry, acknowledgement injection or timeout
extension is introduced.

Open/Resume also waits for the actual CLI prompt frame before another turn,
instead of sending keys as soon as the menu action is dispatched. The existing
UI and overall deadlines remain unchanged. The new renderer is a test-only
dependency; its exact pin passed the canonical seven-day seasoning check.
All 36 source controls passed, followed by all seven focused current-screen,
paste-mode and attachment-readiness controls after the latter addition.
The real native dialog accepted the exact seed through the same fixture helper
with no options selected. Lint, size and all 13 installation contracts passed.
These are source/consumer checks, not installed lifecycle acceptance.

### 2026-11-10 - Create and hosted owner proof passed; passive viewer refused a query

Run 42796260636 at `1f30361b6f80f3bed8894820431e43c4fc1c1746`
created the actual Git worktree, completed one provider-backed conversation,
captured the exact reply from its real pane and identified the lock-bound
parent plus its owned extension host. It then launched a second Manager and
selected Open. During attachment observation, the passive VT decoder raised
on a private device-status query before a further turn could be verified.
Open/detach/normal exit/Resume acceptance remains unproved.

The owned observer now accepts normal and private status queries without
sending input or inventing a terminal acknowledgement. It still renders and
requires the actual prompt frame. All six focused query/current-frame/
attachment controls passed, including split queries and proof that no input
was sent. No UI, provider, process-owner, history, cleanup or deadline
assertion changed.

### 2026-11-10 - Active Open, detach and normal exit passed; discovery was advisory

Run 42799090979 at `1627fabf579eb3c3bb00ba797196501821da1e3d`
completed three actual provider-backed turns in one conversation. Active
Open preserved the original native owner, detaching the first view preserved
work, and normal Copilot/Manager exit left no native CLI owner. The fresh
Manager then offered Open rather than Resume; its advisory `HOST(?)` state
and lack of Stop identify the conservative unknown-observation branch, not
proof that a mux terminal remained alive.

The existing UI contract reserves Resume for proven stopped worktrees and
provides per-row Refresh for an on-demand live observation/cache update.
The witness now drives that ordinary UI operation once before Resume.
It checks unchanged worktree/head/session identity immediately after exit,
requires the actual Refresh highlight before Enter, and still requires the
stopped row and real Resume action afterward. No cache, liveness, PID or
session-ID data is injected or rewritten by the fixture.

All seven focused safe-action/current-frame/query controls passed, including
wrong-highlight and still-unknown refusals. A real native production action
menu selected Refresh through the same controller. That component check is
not installed liveness or Resume acceptance. All original deadlines and
broader suite/promotion holds remain unchanged.

The exit proof now also checks zero from the pinned native Copilot handle,
not only the pane/Manager wrapper, whose success can conceal a nonzero child
exit. The earlier run observed the normal exit command, owner termination
and clean wrapper returns; it did not retain the native exit code directly.
All nine focused native-exit/live-discovery/current-frame controls passed,
including rejection of a nonzero native code behind a successful wrapper.

### 2026-11-10 - Live Refresh still reports a process after native exit

Run 42807975718 at `9d1a05b24a7169b267f882db9a541169a278de29`
again completed the first three turns, active Open and detach preservation.
This time the native Copilot handle also proved exit code zero, with no
native CLI owner remaining and unchanged tracking identity before Refresh.
The real UI Refresh executed and changed advisory `HOST(?)` into `PROC`,
but the row remained Active. The fixture correctly refused to continue
to Resume rather than asserting absence or changing the menu's policy.

The retained evidence does not yet identify which bound-process or execution
field keeps the row live. Timeout diagnostics now read the private selected
installed engine's current classified rows before teardown and retain a
bounded projection of liveness flags, PID identities and execution-binding
state. They do not request Refresh or print credential-bearing binding
values. Foreign engine/worktree reads refuse; unconfirmed diagnostic-process
cleanup remains a hard failure. All seven focused projection/privacy/custody/
discovery controls passed. This is diagnostic work, not a liveness fix or
Resume acceptance.

### 2026-11-10 - Retaining bound-live hint and dropped Refresh request identified

Run 42811263198 at `749673de7fa7a81ba853d765b1a7d4d4aebd52a5`
again proved normal native exit zero and unchanged identity, then failed
stopped-state discovery after the real UI Refresh. The pre-teardown installed
engine capture identifies `session_bound_live=true`; mux presence and attachment
are false, clients are zero, and no `session_lock_live` field is emitted.
This identifies the retaining hint, not a remaining native owner or a
particular process identity.

The source trace finds that Manager requests a current-roster point read with
`refresh=True`. The engine routes it to `worktrees_client.emit_current` before
the legacy refresh helper. That reader validates `--refresh` but omits it from
the resident request, losing the requested bound/mux reconciliation. A repair
must preserve resident authority and prove the requested observation/publication
boundary; caller-side legacy scans, forced flags, TTL waits and substituting
Open for Resume are not acceptance.

The exact finding and repair-ownership request are recorded on #11377
(comment 6766917141). The fleet coordinator identified a potentially overlapping
current-roster publication owner, who received a bounded seam query. Active
overlapping hunks and the separately held idle-project policy must be reconciled
before editing that surface. No engine, shared native helper, runner/result or
Main controller change is included here. Draft #11864 remains unqualified;
full suite/Machines, authentic update and mandatory candidate enforcement
remain open.

The identified current-roster owner subsequently confirmed no active overlapping
hunks or pending repair. The campaign driver is implementing the isolated
resident-side explicit-refresh request and publication barrier under the normal
Dev review gates, without taking the idle-project policy or Main controller.

The correction negotiates `worktrees-refresh-v1` with the actual authenticated
resident, queues a bounded point request and accepts only its same-generation
reconciliation/publication receipt. The producer uses the existing refresh
helper with strict observation failures and registered-session scoping; neither
caller scans nor cached mux receipts can replace its observed absence.
Ambiguous selectors reject only their request, not the project's publications.
Ordinary/passive/feed reads and the separate `--fresh` behavior are preserved.

The six-file source regression selection passed 265 controls after correcting
a test-thread event race, with three nominal CLI timing cases deselected only
after retaining their actual failures. The final ambiguity/point-refresh
selection passed all nine cases. Lint, module size and all 13 install contracts
passed. The authenticated transport/producer controls are a simple integration
check, not installed or frozen-candidate acceptance.

The three unchanged two-second, 1,100-row CLI feed cases failed in the broader
packet and again in a diagnostic-only import-profile run. The latter completed
their first snapshots at 2.187, 3.063 and 3.688 seconds; none qualifies the
nominal bound. The profile identifies startup before first health along the
existing config/control import path. The optional Refresh import is lazy and
not on that ordinary path. These failures remain explicitly linked to the open
nominal-performance gaps #7935/#7936/#7946, not waived or reported as passes.
No scaled timing run, host repair or production deadline change was performed.
Clean-room and live external validation of this correction still require the
next exact-source isolated installed-lifecycle run.

### 2026-11-10 - Stopped discovery and Resume reached; owner check ran before binding

Run 42819736352 at `dd25d9c1beb2556514abdebf4b5c4b8da19ca18e`
passed normal native exit, unchanged identity, stopped discovery and the real
Resume selection. The resumed CLI reached its prompt with one interactive
parent and two owned extension-bootstrap children, but its history had no
active lock at the instant the witness tried to identify the bound owner.
The strict duplicate-owner check refused; no fourth turn or complete lifecycle
acceptance is claimed.

The witness now sends the resumed prompt after its existing actual-prompt
readiness check, then identifies the replacement through the same completed
history/rendered-reply/lock-bound single-owner postcondition used by the
fresh conversation. The replacement must differ from the original native
incarnation, and unchanged history plus all four provider-backed prompts
remain mandatory. No helper exclusion or duplicate-owner refusal is weakened.
All 21 focused resumed-turn, native-role, duplicate-owner, exit, UI-readiness
and Refresh controls passed. Native and cleanup budgets are unchanged;
another exact-source installed run is required.

### 2026-11-10 - Historical core pass retained; review corrections reopen qualification

Run 42821962997, attempt 1, passed the original installed-core witness at
`6cdb0e16d1b0c81455989c67dbcf24848f6efbb9`
(tree `7f68391b5f52c514372a06ed50ad075a5f1a604b`).
Its JUnit contains exactly one selected test, zero failures/errors/skips and
459.878 seconds; its receipt measures 443.407 seconds within the unchanged
case budget. Installed Manager/engine origins and bytes (302/691 modules),
actual Create/Open/detach/exit/Resume in a different native incarnation,
four provider-backed turns, unusable-entry refusal and settled cleanup passed.
This is historical installed-core evidence, not full-suite/update or promotion
acceptance, and not qualification of the subsequently corrected candidate.

Current-head Copilot review 5842652647 identified four blocking corrections:
inconclusive process queries could certify absence; the witness did not require
persisted assistant replies; failed Refresh could stall ordinary publication;
and an unchanged managed observation could reactivate the next publication.
The source correction reuses strict tri-state native liveness, refuses uncertain
Bridge-lock evidence, isolates observation failures to terminal request errors
while retaining hard cleanup failures, and suppresses superseded managed cache
entries across later publications while accepting newer observations.
The witness now requires each exact persisted reply and all four assistant
messages in the final resumed history.

All 452 focused Worktrees/legacy regressions passed, with the three previously
recorded nominal timing cases still separately excluded and not credited.
All 55 lifecycle source controls passed, including rendered-but-unpersisted
reply refusals. The corrected candidate must earn another exact-source
installed result and current-head review before merge. The parent campaign,
complete suite/Machines, authentic update and mandatory candidate/Main gates
remain open.

### 2026-11-10 - Stricter native pass retained; legacy discovery/write corrections

Run 42831416101, attempt 1, passed at
`d130f9c038e4bbf3f7b3c545d5da96e4547fedfe`
(tree `1ac29533d46254d40d94da59b6b01a4c423870f3`).
JUnit records exactly one test with zero failures/errors/skips and 428.719
seconds; the lifecycle receipt measures 407.328 seconds. This run also enforces
all four exact persisted assistant replies. Candidate origins/bytes, the real
Create/Open/detach/exit/Resume flow, unusable-entry refusal and owned cleanup
passed. This remains installed-core evidence for that candidate only.

Review 5843274168 confirms the prior four corrections, but its body identifies
two further Medium issues despite its "No open issues" banner: best-effort
backfill can hide an unreadable workspace, and best-effort stamps can skip
persistence while an in-memory row is acknowledged. Both are treated as
blocking, not excused by the banner or absence of inline threads.

The sanctioned backfill implementation was extracted in a separate
behavior-preserving commit, retaining its facade, compatibility helpers and
single-sweep guard, and tightening only its own module ceiling. Strict discovery
now refuses incomplete or unattributable candidate metadata. Strict point
reconciliation probes outside the record lock, rechecks a fresh record fence
under the canonical required-sidecar lock, saves registry/liveness together and
rereads the actual mapping before returning the confirmed record for publication.
Legacy best-effort callers remain unchanged.

The combined regression selection passed 469 controls, with explicit backfill
scope in the larger sessions file and the three already recorded nominal timing
cases excluded; this is not a full-plugin pass. Real tracking-file controls
cover unreadable workspace data, actual thread-held lock contention, a no-op
writer, reload between publications and successful new requests after fault
removal. The changed candidate still requires exact-source native
requalification and fresh current-head review. All broader campaign gates remain
open.

### 2026-11-10 - Confirmed-persistence pass retained; bind Refresh to its selection

Run 42841168402, attempt 1, passed at
`cf1a02310ab16e7d9510c5475926fe7db9817d33`
(tree `86ede778df4313afd2c511359224a9356072beca`).
JUnit records one selected test, zero failures/errors/skips and 443.431 seconds;
the complete lifecycle receipt measures 426.578 seconds. Installed origins and
candidate bytes are verified across 302 Manager and 693 engine modules.
Create, same-owner Open, detach, normal exit, same-conversation Resume in a
different native incarnation, all four persisted replies, unusable-entry
refusal and settled cleanup pass. This qualifies that installed-core candidate,
not the subsequent correction or the broader campaign.

Review 5844439919 identifies two further High findings in its body despite
another "No open issues" banner. Filtering before response selection could
return a suffix match different from the producer's unfiltered exact match.
Also, best-effort tracking enumeration could omit an unreadable record and
acknowledge a refresh that processed nothing. Both remain merge blockers until
the changed candidate earns its own review and native qualification.

The correction retains each request's full immutable selection and uses the
canonical SelectionIndex over the producer's current record/filter facts.
Publication and refresh share those facts. No readable selection refuses
success. Each applied request identifies the actually reconciled worktree ID,
creation nonce and path; completion, subsequent server reads and client
validation require exactly one matching returned record. Reusing an ID with
different filters refuses rather than reusing another selection's receipt.
This repairs the new, unreleased optional capability within this PR; it does
not add another endpoint, caller scan or liveness authority.

All 200 tests in the affected producer, runtime, client and projection files
pass. These include real best-effort tracking enumeration with a denied file
read and malformed YAML, status/platform/checkout exact-ID versus suffix
collisions, filter-changing request reuse, and changed/empty record selection
before and after completion. Lint, module ceilings and all 13 install contracts
pass. The three separately recorded nominal feed-timing failures are outside
this selection, not passed or waived. Exact-source installed requalification,
fresh current-head review, required CI and all parent acceptance gates remain
open.

### 2026-11-10 - Selection-bound native pass; require single-process conversation binding

Run 42851885922, attempt 1, passed at
`62ed94ba9c7692686c985dca0a547a410b29093a`
(tree `379215893bb7e4edc999971ea380af11d108e9fd`).
JUnit records one selected test, zero failures/errors/skips and 422.196 seconds;
the complete lifecycle receipt measures 407.219 seconds. Installed origins and
candidate bytes are verified across 302 Manager and 693 engine modules.
Create/Open/detach/normal exit/same-conversation Resume, all four persisted
replies, unusable-entry refusal and settled cleanup pass. This remains
installed-core evidence for that candidate only.

Review 5845313738 emits a negative summary referencing omitted Refresh and
conversation-owner findings, but provides no detailed findings or new inline
comments. Its "No open issues" banner is not treated as an accepted review.
The prior Refresh scenario is covered by the canonical full-selection and
exact-identity correction. Direct examination confirms another witness gap:
the single-native-process branch did not require that process's PID in the
verified conversation's lock set. It now applies that requirement even without
an extension host; bound, unbound and missing-history cases have focused controls.

The owned branch was rebased onto current `dev`. The only content conflict was
between independently added archive-selection and Refresh tests; both blocks
were retained. All 205 affected Worktrees tests pass, including the upstream
archive-selection cases.

The first lifecycle source packet hit its original 180-second real-archive
control limit during a redundant complete-source extraction. The companion
staging pass now uses the canonical Git/archive reader for the complete
catalogue and all plugin manifests plus the two selected companion trees.
Canonical extraction/materialization, catalogue validation and retained runtime
hash/receipt checks remain unchanged. The real control proves that unselected
runtime source trees are not extracted. All 58 lifecycle source controls now
pass in 86.66 seconds; no deadline was increased or failed attempt credited.

The rebased, stricter candidate still requires exact-source installed
qualification, a fresh actual review and required CI. No complete-suite,
authentic-update, mandatory frozen-candidate or Main-adoption gate is closed.

### 2026-11-10 - Strict owner-binding native pass; reject malformed raw registries

Run 42857279887, attempt 1, passed at
`86d8f360d0b874f1e2e6ddbe2b895b864870072e`
(tree `7ebb4e982a7f37bd1a0aaebf29d3ad0004b0c30b`).
JUnit records one selected test, zero failures/errors/skips and 348.862 seconds;
the complete lifecycle receipt measures 339.735 seconds. Installed origins and
exact candidate bytes are verified across 303 Manager and 697 engine modules.
The complete real flow, stricter conversation-owner binding, all four persisted
replies, refusal and settled cleanup pass. Required CI passed; a metadata-parser
npm configuration timeout also passed its single job-only retry under unchanged
limits. This is installed-core evidence for that candidate, not broader suite,
update or publication enforcement.

After the full post-push wait expired, an actual current-head Copilot review
5845864973 arrived during the explicit re-request. Its body recommends changes:
malformed session metadata can become an empty registry before strict Refresh
checks the native owner. There are no new inline comments; the negative body
remains blocking despite its "No open issues" banner.

Five real tracking-file controls reproduced successful acknowledgements for a
non-list registry, a discarded entry, coerced null/numeric IDs and a partially
discarded registry. Strict reconciliation now validates raw registry structure
and every session identity before the existing parser, on each initial,
locked-fence and persistence-confirmation read. Malformed metadata refuses
before absence stamps or receipts; legacy best-effort parsing is unchanged.
The valid indexed-empty, valid registered-session and unindexed discovery paths
remain covered, as does malformed metadata introduced during observation.

All 213 tests in the affected producer/runtime/client/projection files pass in
58.06 seconds, including successful unindexed discovery. The changed candidate
still requires exact-source installed
requalification, fresh actual review and required CI. No prior native pass
qualifies this correction, and no parent acceptance gate is closed.

The same class-level check also found that strict unindexed discovery could
silently skip a record with no checkout path or compare an unqualified relative
path. It now refuses that unattributable input before discovery; legacy
best-effort callers keep their previous behavior. The final combined packet
passes 222 controls in 53.36 seconds, selecting only backfill cases from the
larger sessions file (152 unrelated cases deselected), alongside the complete
four affected Worktrees files. Both missing and relative tracked paths have
focused refusal controls. No installed run was dispatched for the intermediate
raw-registry-only commit; the combined candidate must qualify as one unit.

### 2026-11-10 - Preserve backfilled registries against stale full-record writers

Reviews 5845986009 and 5846264600 surface High comment 4527926377: registry
initialization did not advance `lifecycle_revision`, so the canonical stale-write
merge could later accept an older `sessions=None` snapshot and erase a discovered
Resume target. The finding still applied to `4ebc8fade813d7e410462a3ace80b74a8add32c0`.
Its installed run 42864326611 was cancelled after that blocking feedback was
identified; qualification was cancelled and the evidence-retention step failed.
No native qualification is credited to that run.

Three regressions reproduced the failures: both strict and best-effort backfill
lost the discovered target after a real stale save with an unrelated title
change, and strict reconciliation acknowledged a concurrent lifecycle revision
change. Both refresh paths now use the canonical revision helper when initializing
the registry, including relation-revision updates for discovered sessions. Strict
reconciliation also includes the lifecycle revision in its observation fence.
Liveness-only refreshes do not create another lifecycle revision.

All 237 selected controls pass in 63.98 seconds: complete producer/runtime/client/
projection files, backfill-scoped sessions tests and strict native-observation
reclaim tests (224 unrelated cases deselected). These include real YAML and
stale-writer merge behavior, retained Resume identity, unchanged unrelated edits,
idempotent later liveness refresh and concurrent-revision refusal. Fresh
exact-source installed qualification, actual review and required CI remain
mandatory; all parent acceptance gates remain open.

### 2026-11-10 - Atomic legacy initialization and observable dialog focus

Exact-source run 42869501687 at
`5aae3a4938d042e62d12739a73883e13148eab16` failed before Create:
the New dialog never rendered the entered prompt. JUnit records one test, one
failure, zero errors/skips and 291.325 seconds. No lifecycle pass is credited.
The terminal shows an empty prompt field; it does not establish the precise
event ordering that lost input. The controller's byte-count focus waits could
accept unrelated redraws, so they were not evidence that the field was ready.

The witness now observes the rendered Create highlight before Tab, the empty
prompt field's drawn caret before paste, and the Create highlight after accepting
the prompt. Existing exact prompt/history assertions remain. The same waits keep
their original 30-second limits and the case keeps its original 720-second
budget. Bounded input-style diagnostics distinguish unknown focus, stale frames
and unrelated redraws. All 65 lifecycle source controls pass, including the new
rendered-focus positive and refusal cases; installed proof remains required.

Current-head review 5846807016 confirms the previous lifecycle-revision finding
resolved, but raises High comment 4528558342 on the best-effort path: an
interleaved initializer can reach the same next revision as a stale backfill,
allowing that stale save to overwrite the winner. Three real-file regressions
reproduce concurrent registration, competing refresh initialization and a replaced
incarnation receiving liveness stamps.

Legacy initialization now reloads uncached under the canonical required-sidecar
record lock, checks the incarnation, and initializes only a still-unindexed
current record. It otherwise retains the winning registry and revision without
writing. Only lifecycle fields are adopted into the caller, preserving unrelated
snapshot/CAS pairing. A replaced incarnation stops before liveness stamps.
The final backend packet passes 240 selected controls in 54.97 seconds, with the
same 224 unrelated sessions/reclaim cases deselected. Fresh combined-head native
qualification, actual review and required CI remain mandatory.

### 2026-11-10 - Palette-independent focus and request-local parsing failures

Exact-source run 42879412714 at
`0d8a9b0789f3f01c53325d36c6f5775e97f92310` failed before Create:
one test, one failure, zero errors/skips and 289.282 seconds. Its bounded
input diagnostics show Create already highlighted with foreground `1a1a1a`
and background `ffaf00`, while Cancel retained its rest style. The observer
incorrectly required the foreground to be literal black. It now recognizes the
selected orange1 background independently of ANSI foreground remapping, requires
a uniform Create foreground distinct from Cancel, and rejects Cancel, dual-button
and partial highlights. All 30 focused palette/focus controls pass.

Actual current-head review 5847618117 resolves the atomic-initialization finding
but names Windows path mismatches and malformed-YAML publication failures in
its body. Both remain blocking until corrected-candidate acceptance.
Legacy workspace matching now uses lexical Windows/POSIX path semantics rather
than case-sensitive string prefixes, preserving child-path boundaries and POSIX
case sensitivity. Strict tracking reads translate malformed YAML and typed
record-parsing errors into request-local unavailability; hard cleanup errors
are not caught or weakened.

The contained targeted packet passes 63 controls in 45.79 seconds, including
16 strict/default Windows/POSIX path cases and eight actual YAML-file races
after selection or during observation. The latter cover malformed YAML,
invalid typed fields, missing keys and non-finite numeric conversion; they
require terminal rejection, no receipt, unchanged corrupted bytes, continued
ordinary publication and no implicit retry. All 227 tests in the complete
producer/runtime/client/projection packet pass in 86.75 seconds. Scoped lint,
module limits and all 13 installation contracts pass.

Two lifecycle source attempts using the OS-default temporary venue timed out
in candidate-archive hashing/materialization under the existing 180-second test
deadline. Neither is a pass. Restoring this task's established scratch venue,
used by the previous successful packet, passes all 88 lifecycle source controls
in 106.60 seconds under unchanged limits, including the archive boundary.
New exact-head installed qualification, fresh actual review and required CI
remain mandatory. No parent suite/update/promotion gate closes.

### 2026-11-10 - Preserve legacy identity through receipt publication

Exact-source run 42888702165 at
`1453fad0ef029d5012ff4c7904426174e0245ae0` failed earlier at initial Manager
readiness: one test, one failure, zero errors/skips and 228.228 seconds.
The screen remained in loading/knowledge-check setup with the host's default
identity, not the registered project binding. Direct installed preflight reads
had confirmed the intended project/machine and ready knowledge binding.
The launch trace reaches `picker_live_sources_prepared`, but not
`picker_live_loader_started`, before the original 30-second readiness boundary.
This identifies the stalled interval, not its root cause. No readiness predicate
or deadline is weakened, and no lifecycle pass is credited.

Actual review 5848403116 flags a legacy-record receipt identity mismatch in its
body despite the "No open issues" banner. Two real-file regressions reproduce
terminal unavailability after an otherwise successful refresh of indexed and
unindexed records lacking `creation_nonce`: receipt construction used the
record's empty string, while canonical publication omits that field and the
reader interprets it as null.

The producer now derives receipt identity through the same canonical selection
row and identity helper used by publication/completion, retaining absence rather
than inventing a nonce. The new tests require a successful matching receipt,
unchanged absent nonce on disk and canonical omission in the row. All 229 tests
in the complete producer/runtime/client/projection packet pass in 54.16 seconds.
Scoped lint, module limits and diff checks pass. The unchanged observer's 88
source controls remain passing; this changed engine candidate still requires
exact-source installed qualification, fresh actual review and required CI.
The earlier startup stall remains a failed result, not an accepted flake.

### 2026-11-10 - Installed core accepted; complete-suite execution started

#11864 merged into Dev as `fa93ea37c295da6b4096e87cafb42f58c98af718`
after actual current-head Copilot approval 5848798419, all three live-required
checks passing and all review threads resolved. The configured Maintainer
completion path verified the merge; no CI gate or unresolved finding was waived.

Exact-source run 42892225611, attempt 1, qualifies
`4feb77db2944b54cddcdd949faab166013a417bb`, tree
`5161d5d7fcb8495527ec805dec3b8b6df1c434a5`. Retained JUnit records one test,
zero failures/errors/skips and 381.634 seconds; the complete receipt measures
370.75 seconds. Installed origins and exact candidate bytes are verified across
303 Manager and 697 engine modules. Actual Create, same-owner active Open,
detach preservation, normal native exit, same-conversation Resume in a different
native incarnation, all four persisted assistant replies, unusable-entry refusal
and settled owned cleanup pass. The evidence-aware re-review explicitly
approved that unchanged source. This closes the action/continuity items marked
above, not the complete acceptance matrix.

The next active slice is complete selected-suite construction and actual Machines
readiness through the canonical normal updater, using the accepted readiness
profile. The existing suite fixture's synthetic V1/V2 versions and substituted
delivery are not an authentic admitted-baseline update. Reuse its lifecycle and
disposable Windows venue boundaries, not that synthetic baseline as evidence.
Inspect the current profile, suite constructor and source/feed owners before
implementing the independently owned companion; agree concrete shared-helper or
runner/result edits with their existing owners. Preserve the 1800-second
preparation and 900-second exercise boundaries.

Ordinary upgrade from an authentic admitted baseline, frozen-candidate mandatory
enforcement, separately governed Main adoption and the wider parent campaign
remain open. The worktree remains active for this next slice; merged installed
core is not effort completion or permission to roll out on an operator machine.

### 2026-11-10 - Complete payload acquisition separated from runtime acceptance

Live ownership reconciliation found #11377 closed and its completion #11814
and Main adoption #11478 merged. Those historical PRs are not live content
overlaps or prerequisites for this parent extension. Coordination comments
6779028766 on #11377 and 6779134232 on #6237 reserve the independently owned
payload companion and bounded preflight in the existing manual Dev lane.
No whole-suite runner/result, Main controller or promotion transaction changed.

The companion consumes the canonical whole-tree materializer's commit-bound
artifact and the accepted complete required-readiness profile. It invokes the
actual CLI for every declared payload, discovers selected paths through the
installed engine, and compares private installed payload bytes with the frozen
artifact. It rejects incomplete discovery, foreign or changed payloads, changed
CLI/artifact inputs and reduced activation. Inventory hashing is shared per
parent rather than repeated per payload; nested receipt files remain covered.
Its result explicitly reports runtime installation as unobserved.

The existing optional `manager-lifecycle` Windows lane now runs that preflight
before the unchanged installed-core witness, using the existing pinned CLI and
native engine constructor. It requires one successful, non-skipped test and one
complete, exact-commit/run/attempt payload receipt, retained separately from the
core receipt. The same 720-second case, 900-second runner and 30-minute shared
job envelope remain; no deadline, required CI or release gate is expanded.

The contained payload/profile controls pass all 28 cases. Workflow refusal
controls, Python lint and alias-aware Actions lint are checked separately.
Actual CLI acquisition still needs the exact published source's disposable-VM
run; source controls are not native evidence. Complete runtime construction,
actual Machines/required-readiness discovery and New after an authentic
admitted-baseline ordinary update remain open, as do mandatory frozen-candidate
enforcement, this extension's separate Main adoption and the parent campaign.

Run 42903264094 at `848fc8f7c710c95cad41e029c684464157ae96c2`
failed its real payload acquisition after the native engine constructor and
canonical whole-tree materialization. The actual CLI's first `plugin install`
refused because marketplace `copilot-extensions` was not registered.
The test ran for 289.98 seconds; no deadline expired and no runtime/Machines or
payload-closure success is claimed. The subsequent core step did not run.
This is not a Bridge communication failure: its payload name was only the first
sorted acquisition target.

The companion now invokes the documented `plugin marketplace add` for that
exact local frozen artifact through the same actual CLI and bounded private
HOME executor before installation. Source controls require this command and
stop immediately on registration failure; no ledger or acquisition result is
fabricated. A new exact-head native run remains required.

Run 42905375223 at `66e9d22518e29068ca53e30a86c7397c7cfe706b`
successfully registered the actual local marketplace (6.656 seconds) and ran
all 24 real CLI plugin installations successfully (roughly 0.8-1 second each).
It then refused the first installed engine-selected payload location; the case
failed after 302.54 seconds. This is progress past acquisition, not a complete
payload-closure or runtime/readiness pass. The original source result did not
retain the selected paths or CLI inventory, so the exact location discrepancy
is not yet established.

The witness now retains bounded actual CLI inventory, selected-path and command
custody diagnostics. It preserves the private-installation and exact-byte
assertions rather than copying payloads into a guessed layout or substituting
discovery. Another exact-source native observation is required before deciding
whether the correction belongs in fixture configuration or product discovery.

Run 42907778944 at `3ce7bdd6bdc3478f2ed44f65cdff9dcea295a45c`
retains the actual discrepancy. The CLI reports all 24 selected plugins as
`source: live` from the exact materialized artifact, explicitly stating that
directory installation copied nothing. The installed engine's cache-only
discovery therefore returns no paths for all 24. The 281.33-second failure is
not a completed copied installation or a timeout, and the refusal is retained.

The fixture now uses a real private Git transport for the same verified
materialized artifact. Its bare repository is outside the artifact; actual Git
stages all verified bytes, preserves the source commit's date, and records the
distinct transport commit/tree. The actual CLI registers the `file:` Git URL
and performs normal Git-backed installation; transport identity is rechecked
before and after acquisition, while copied private payloads must still match
the original frozen bytes. The required-readiness profile declares that same
Git source. Nothing is copied into a guessed installation cache by the fixture.

This is fixture data transport only, not a release composition, publication,
admitted baseline or promotion writer. It adds no remote repository, service,
daemon or manual promotion. Exact native validation is still required; no
runtime/readiness, ordinary-upgrade or parent completion is inferred.

Run 42912342047 at `285a009b27fecac6085bc8bb836370d364c4f409`
failed before CLI registration: `git add --force --all` exceeded its 120-second
command bound while indexing the complete artifact. The case lasted 371.09
seconds; no deadline is increased and no transport/CLI-copy success is claimed.
The transport now writes a packed `git fast-import` stream from the verified
artifact inventory instead of indexing and creating loose objects. Generated
ignored files and the root materialization receipt remain included, source
timestamps remain attributable, and stream generation checks the original case
deadline. The real-clone preservation check still applies.

Required CI independently found a missing Worktree Manager changefile: that
guard covers these test changes too. The canonical changefile tool added the
required patch entry; no version was hand-edited and no CI exception is used.
The corrected head must independently clear both native qualification and
required CI before becoming reviewable.

Run 42915011065 at `215428d79df3a78749f55b0f3fadf051fbfc6e03`
completed real packed import in 10.750 seconds, well within its unchanged
120-second limit. It then failed actual CLI registration because the pinned
CLI interprets a `file:` URL as a local path rather than a Git transport.
The 276.57-second case is not copied-install or lifecycle success.

The same real private Git repository is now exported with `git update-server-info`
and served through a context-owned loopback HTTP server for normal Git clone.
This is actual Git object/ref delivery, not a successful acquisition callback.
Only the bare repository is exposed, the owned HEAD response is checked before
use, connection reads are bounded and the server thread/listener must retire
before success. The small real-Git preservation check now exercises that HTTP
route. The profile/receipt require that exact loopback Git source; foreign
origins and invalid identities refuse. No host service, persistent daemon,
remote repository, release admission or upgrade baseline is introduced.

Run 42918039967 at `25d234b25a8287ac8660519fcdbcbd825a37e160`
reached the real CLI's HTTP acquisition, but its exact command uses
`git clone --depth 1 --progress`. The static/dumb Git HTTP transport cannot
provide shallow capabilities, so it failed after 325.65 seconds. Required
source CI has a successful pass at this head; native qualification is still
failed, and neither is waived.

The loopback transport now delegates read-only advertisement/upload-pack
requests to the actual `git http-backend` through the same bounded native
process owner. Binary request/response files avoid rewriting pack data;
backend failures and unconfirmed custody remain fatal even if a client retries.
The small real-Git check now uses the observed shallow-clone options instead
of a full-clone proxy. Existing command/case/runner/job limits remain unchanged.

The payload receipt reader also now matches the existing core reader's refusal
of duplicate JSON fields, including identical duplicate values. Its focused
malformed-receipt control passes; a complete result must be unambiguous before
any later release-contract adoption.

Run 42922216324 at `dc8daf766ecc27a0d6bbfa465b956238271cf930`
completed actual shallow Git HTTP acquisition and all 24 CLI installations.
The CLI reported copied, enabled installations, and the actual installed engine
discovered all 24 canonical private-home payload paths. The 460.147-second
case then refused the first payload, `agent-bridge`, because its full content
manifest differed from the frozen candidate. No runtime, readiness or lifecycle
pass follows from this partial acquisition evidence; the core step was skipped.

The comparison previously retained no differing paths, so the cause remains
unknown. It now retains expected/actual entry counts and up to 20 differing
relative paths with bounded metadata and hashes, both in the owned diagnostics
and in the failure. File contents are not reported. Focused controls retain
strict rejection of changed, missing and extra files, including nested receipts;
no exclusions, content normalization or acceptance rule changed.

Run 42926168518 at `82a26c0386c3dc968a2a4147a2ebc460d7be79af`
failed after 480.755 seconds and retained the exact discrepancy: all 3,630
`agent-bridge` entries were present, with no extras; only the bytes of
`bin/agent-bridge.cmd`, `bin/agent-bridge.ps1` and
`scripts/emit-command-catalog.ps1` differed. For each file, the expected hash
equals the committed LF blob and the actual hash exactly equals that blob's
CRLF checkout projection. The source `.gitattributes` explicitly declares
CRLF for `.cmd` and `.ps1`. This identifies ordinary Git checkout conversion,
not missing payloads or a Bridge communication failure.

The fixture now supplies a private Git template whose `info/attributes` disables
text conversion when the actual CLI clones its data transport. The complete
frozen artifact and strict comparison remain unchanged; no bytes are normalized
after acquisition and no path is excluded. The small actual shallow-clone
control includes LF script blobs with CRLF source attributes, and acquisition
controls refuse a foreign or changed template. This is data-transport custody
only, not ordinary platform-checkout, runtime or admitted-upgrade acceptance.
The corrected head still requires native qualification.

Run 42928717907 at `089a02c92b31e5fecc3c45df144312c2991d3977`
passed both exact-source witnesses: actual CLI acquisition, installed-engine
discovery and strict byte comparison of all 24 payloads took 488.537 seconds;
the unchanged installed-core lifecycle took 348.119 seconds. The distinct
payload receipt still reports runtime installation unobserved, so complete
runtime/Machines readiness, authentic update and release enforcement remain
open. The PR cleared current required CI and entered review, not merge.

Copilot review 5851705493 identified an error-precedence defect: an earlier
ordinary HTTP failure could mask a later unreaped-backend failure, allowing
sandbox deletion despite unresolved custody. Transport exit now prioritizes
the actual containment owner's `UnreapedProcessError` across backend, caller
and transport-cleanup failures. Focused controls cover an ordinary request
failure followed by backend or caller custody failure and verify that the
real sandbox retains its files. The reviewed correction needs new exact-head
native qualification and a passing subsequent review.

### 2026-11-11 - Complete payload closure accepted; runtime construction started

PR #11972 merged to Dev as `0286b3f71537d1a3d8128e2833add3a980b9b51f`.
Exact-source native run 42933306312 at
`c51a191e0c1cf651fa5e9dadfc652f69104a3f44` passed actual CLI acquisition,
installed-engine discovery and strict full-manifest comparison for all 24
private payloads in 308.756 seconds. The unchanged installed-core lifecycle
passed in 260.986 seconds on that same source. Both producing-run receipts
passed their workflow validators and retained the original finite limits.

Copilot re-review 5851823621 confirmed no open findings after the custody fix;
its sole remaining pending-native concern was resolved by that exact run.
All review threads were resolved. The first managed merge correctly refused
a newly expected metadata-triggered required PR gate; after CI run
42935102060 passed, the managed retry merged normally under the configured
Maintainer flow. Only the separate CODEOWNER vote was omitted; no CI failure,
review finding, draft, hold or conflict was bypassed. Parent #6237 remains open.

This closes payload acquisition only. Its receipt explicitly reports runtime
installation unobserved; no full-runtime/Machines, authentic update, mandatory
candidate enforcement or Main activation acceptance is inferred. The next
active slice is a separate companion using the existing canonical installation
and normal Manager update owners to construct the declared runtimes and reach
actual Machines/required-readiness discovery in the disposable Windows venue.
Use genuinely merged source admission; the fixture's Git transport commit is
data delivery, never an admitted baseline. Preserve the existing full-suite
preparation/exercise limits and coordinate concrete shared-helper/result edits.

### 2026-11-11 - Fresh hosted runtime adapter implemented; native proof pending

The separate runtime companion now calls the installed Manager's normal update,
requires the complete eligible private runtime portfolio, checks actual installed
imports and exact versions, and invokes the selected Machines owner's required
readiness contract. Its refusal controls do not establish native construction.

Fresh mode is integrated alongside normal/repair in the existing hosted
`manager-suite` portfolio. It retains the original preparation/exercise/assertion/
settlement limits and default private HOME roots. An uncounted discovery engine
precedes final construction from actual CLI-selected payloads; the canonical
Manager/engine constructors and the already-qualified lifecycle action sequence
are reused. The original core defaults and synthetic replacement witnesses are
preserved. The Git fixture can reopen its same private endpoint across stages,
without keeping an unowned server alive between them.

The narrowed contained source packet passed all 45 runtime, profile, transport
and lifecycle controls; all 58 runner/workflow controls passed. A standalone
isolated-interpreter invocation also refused this shared host before creating
its requested fixture. Changed-source lint, workflow syntax and the install
contract passed. An earlier broad source packet exceeded its aggregate budget;
it is not accepted evidence. Native fresh construction and actual Machines
readiness have not yet run. The authentic admitted-baseline update, mandatory
frozen-candidate enforcement and separate Main adoption remain open, with no
operator rollout or shared-host repair authorized.

Run 42946413939 exercised draft #12013 at exact source
`e24b119f1f483843538243b0c28f0baab4a9d516`. All three cases failed preparation,
and each retained successful finally-owned settlement. Fresh stopped at the
canonical materializer's unresolved-source refusal, before payload acquisition
or runtime construction. The original error omitted its actual refusal reasons.
A local exact-source diagnostic hit the unchanged 120-second materialization
limit and supplied no replacement acceptance evidence.

Normal and repair reached installed-origin observation but failed to spawn a
selected interpreter. Their common resolver assumed only venv-as-slot layout;
it now also recognizes the canonical nested `.venv` layout, without changing
selected generations or installing a substitute. The canonical materialization
error now retains up to eight bounded refusal reasons and still emits no
receipt on failure. Two controls for each correction passed, with changed-source
lint and staged diff checks. The unresolved fresh source must be diagnosed
from the next retained reason; no runtime, readiness or lifecycle result is
credited from this failed run, and no deadline or provenance rule is waived.

The corrected diagnostics in run 42948793163 at
`cd0848ac317b6fbbf541b6179d0700a0808bcfbe` identified eight identical native-asset
refusals: the allocated extraction root retained the Windows `RUNNER~1` short
spelling while its owner check used the resolved long spelling. The builder now
canonicalizes only its newly allocated private extraction root before extracting
or invoking committed materializer code. Derived-asset link, traversal,
single-link and containment guards remain unchanged.

A real committed-source construction control reproduces an equivalent lexical
alias and requires canonical root delivery before extraction. It also exposed
an incomplete test fixture, which now copies the committed materializer's
Invocation helper rather than relying on an ambient fallback. The full provenance
packet passes 42 tests with two platform-inapplicable skips; the seven focused
root/committed-code/diagnostic cases and changed-source lint pass. Native
construction on this correction remains unverified, and the fresh case on
`cd0848ac` is still failed, with successful settlement, not superseded acceptance.

Run 42949961145 at `9edfc0b14b0da25b588e940e1bf376f50c840810` passed
materialization but failed fresh bootstrap: the low-level Worktrees constructor
correctly required live native update-driver custody. Failure rendering itself
then failed because captured strings were passed to a file-based diagnostic
reader. The adapter now passes retained output paths and enters the canonical
native driver for both private engine roots. Its 38 contained runtime controls
pass; that is source evidence, not native runtime acceptance.

Run 42952406260 at `ab0ad1ec417e694767a5677739a97ff8ca9ac6d7` advanced through
driver-owned bootstrap construction and then failed frozen marketplace
registration. Retained bootstrap logs show that the full native `install`
action had already registered the public marketplace and acquired a plugin.
That acquisition is not frozen-suite evidence. The adapter now requests the
canonical lean `provision` action, which constructs the selected source runtime
without launcher/plugin acquisition. The actual CLI still owns subsequent
complete frozen payload acquisition, and no existing registration is erased or
silently treated as the requested source.

The normal/repair failures on `cd0848ac` and `9edfc0b` are distinct. Their
Codespaces on-demand owner started successfully, but their required scheduled
task was absent. The native update log explicitly reported the default
preserve policy and no task creation; preparation and settlement reported no
cleanup errors. The disposable replacement fixture now explicitly declares
installation-local `windows_scheduled_task: enabled` before installation,
preserving its strict native task ownership assertion and production defaults.
Two focused configuration controls pass through the actual Codespaces policy
and native/dependency-free Index role readers. Native registration, runtime
closure, actual Machines readiness and all later acceptance remain open.

Run 42954102322 at `f6f0ed98a4e2484dce4b0a2327ace1ca5a646c76` passed lean
bootstrap and actual CLI acquisition, then correctly rejected byte differences
in the first frozen payload. Sample digests exactly match CRLF artifact bytes
versus LF installed bytes. A read-only real-Git reproduction shows that archive
honors ambient `core.autocrlf`: the same committed file produces those two
digests under true versus false. The builder now supplies per-command
`core.autocrlf=false` and `core.eol=lf`, without changing repository/global Git
settings or normalizing an installed payload after acquisition. Explicit source
attributes remain in effect. The full provenance packet passes 44 tests with
two platform-inapplicable skips, including two new real-Git controls covering
ambient autocrlf and text/eol projections.

Required CI independently refused the provenance helper's stale admitted-unit
source digest. Its single manifest source binding is updated to the corrected
helper; test nodes, coverage targets, packet budget and controller selection are
unchanged. Native payload comparison, complete runtime construction and actual
readiness remain unverified. No failed hosted case is credited as acceptance.

Run 42956773294 at `ca0e62ec8840c34cce1ab7e6d7abd6e07e25b72e`
completed normal and repair successfully. Fresh still failed strict payload
comparison, with 72 changed files and no missing or extra paths; settlement
succeeded. Its actual CLI inventory reported released versions rather than
the candidate's source versions. The archive correction alone did not establish
frozen acquisition, and no fresh runtime or readiness acceptance is claimed.

A tiny actual-CLI reproduction isolates the remaining source-selection error.
Without folder trust, nested project settings select the loopback fixture.
With the fresh adapter's real `trustedFolders` configuration and an enclosing
Git repository, installation instead selects that enclosing repository's
marketplace. The contradictory source in the reproduction is an unreachable
loopback URL, so this failure cannot silently acquire a public replacement.
Initializing the private project as its own Git root before acquisition makes
the same trusted CLI install the fixture version and preserve all three
distinguishable files, including conflicting CRLF/text attributes.

The adapter now initializes that root before any constructor or acquisition.
Its existing later initialization remains idempotent. All 39 contained runtime
controls pass, including a real-Git regression that checks the private settings
root before construction. The extended real-Git transport control also preserves
CRLF bytes under conflicting attributes. These are unit and lightweight
first-touch CLI/transport checks, not complete native suite acceptance. A fresh
hosted run must still prove all frozen payloads, complete runtime construction
and actual Machines readiness; the authentic upgrade, frozen enforcement and
separate Main adoption remain open.

Run 42961736606 at `9202df599847242d1600c1f645ae765551ad247d`
passed normal and repair again. Fresh passed complete frozen payload acquisition
and reached the ordinary Manager update, but did not complete runtime
construction. Vault activation configuration refused the adopted harness's
missing stable remote identity; Manager self-update also tried to clone the
fixture's raw commit as a branch. Settlement independently failed with an owned
Index resident still live, despite the stop command returning zero: its retained
stderr reported missing repository context. These failures remain failures.

The fresh fixture now initializes `main` explicitly and persists its actual
project root before construction, so cooperative shutdown enters that root
rather than the replacement fixture's otherwise-unused `project` directory.
The shared command owner accepts only an existing direct fixture child; normal,
repair and pre-project cleanup keep their previous working directory. Fresh
adoption alone creates a real private bare origin before registration and
pushes the actual project commit, supplying a resolvable canonical identity and
an ordinary cloneable anchor. The synthetic replacement profiles are unchanged.
Manager source selection uses the frozen transport's sole `main` branch, with
the same exact commit/tree verification before and after the update.

The focused contained packet passes 70 controls with 158 unrelated tests
deselected. It covers real Git registration input and cloning, explicit `main`
selection, retained payload bytes under conflicting attributes, shutdown
working-directory ownership and unchanged strict runtime/readiness refusals.
Changed-source lint and whitespace checks pass. This is unit and lightweight
integration evidence; the real private clones are first-touch inputs, not an
authentic admitted upgrade baseline. The next disposable hosted run owns full
native clean-room construction, Machines readiness and cooperative settlement.
No shared-host installation, cleanup waiver, source admission or Main activation
is authorized or credited by these controls.

Documentation-impact review: these corrections change only native test inputs
and command ownership, not a released installer, runtime API or operator
procedure. This journal records the evidence and limitations; no public usage
documentation changes are needed. The install-contract guard passes for all
13 plugins.

Run 42966913273 at `d8e7f834e0b74d9cb526cc335bf635112b8c52d1`
passed normal and repair; fresh reached successful ordinary Manager update
(exit zero after 518.2 seconds) and then failed the witness's portfolio
comparison. Its actual installed planner returned all 24 selected plugins:
13 eligible runtime entries and 11 explicitly ineligible payload-only entries
with null runtime roots. Comparing that whole map with the runtime-only
expectation incorrectly treated those legitimate payload entries as extra
runtimes. Fresh settlement now succeeds with no errors or remaining tasks.
Required CI independently passes all three required jobs at this exact head.

The observation validator now classifies those extra entries only when the
frozen inventory declares them payload-only, the planner explicitly reports
that status with no runtime eligibility/root, and the payload path matches
the acquired selection. Unknown extras, a missing runtime, a rooted/eligible
payload-only entry and failed/foreign runtime selections still fail. The
construction control now models the actual mixed planner response instead of
an unrealistically runtime-only response. All 48 focused runtime controls pass,
including the classification's success and refusal cases; lint and whitespace
checks pass. This remains a fixture-only correction with no operator-document
or install-contract change. Actual selected imports, Machines readiness and
Manager lifecycle actions still need the next exact-head disposable run; a
successful update command alone does not establish those later contracts.

Run 42970992493 at `04df70053e6920a9eaee2852d3f1fc9b53085f34`
passed normal and repair again. Fresh passed the mixed planner observation and
all 13 selected runtime import/version checks, then correctly refused actual
Machines required-readiness: Index and Vault reported failed. Fresh settlement
again succeeds without errors or remaining tasks; required CI also passes.
No fresh result or lifecycle acceptance is credited.

Vault's native installation log explicitly reports missing `keepassxc-cli`.
Its readiness contract correctly requires that real backend even when no
database is configured. The fresh disposable job now provides the official
portable KeePassXC 2.7.11 archive, released 2025-11-23, through the existing
bounded downloader, with its published SHA-256 verified before extraction.
The same archive was independently downloaded, hash-checked and its actual
CLI returned version 2.7.11 without host installation, service startup or vault
state creation. Only the fresh job receives this path; the shared prerequisite
anchor, installed-core and normal/repair cases are unchanged.

Index's raw failed-readiness detail was not retained by Machines' normalized
observation. Its installer reported a healthy service cutover and an activation
role warning; neither establishes why the readiness adapter failed. The witness
now retains bounded failure-only details through the selected Machines runtime,
its admitted owner binding and canonical read-only invocation. It does not
apply repair or change acceptance. Ordinary diagnostic errors retain the
original readiness refusal, and unresolved cleanup custody still takes
precedence. The next hosted run must establish the exact Index reason rather
than treating a warning, unknown corpus state or successful service start as
satisfied readiness.

All 51 runtime controls and 58 workflow/coordinator controls pass. Changed-source
lint, whitespace, YAML loading and native PowerShell syntax checks pass.
Documentation-impact review: these are disposable qualification inputs and
failure diagnostics, not a released runtime or operator procedure. The full
native clean-room/readiness result remains pending the next exact-head run;
authentic admitted upgrade, frozen enforcement and separate Main adoption are
still open.

Run 42977725641 at `162112b586d4505a9ee410ceacdf4b193bb42ed1`
passed normal and repair; fresh still refused actual Machines readiness for
Index and Vault, with successful settlement and required CI. The Vault
installer now confirms that the real `keepassxc-cli` prerequisite is found.
That removes the known missing-backend input, not the remaining readiness
failure.

The added failure diagnostic returned `invocation-refused`, not either owner's
raw reason. It had obtained the host owner bindings but omitted the canonical
execution-binding step that `repair` performs before `_invoke`. The diagnostic
now uses `_binding_inputs` on only the requested failed modules before
read-only invocation. A contained check through the actual Machines API and
native fixture action proves the difference: the previous program returns
`invocation-refused`, the corrected program exits with the owner's raw
`not-ready` reason, and no installer state is created. Four focused regression,
original-refusal and custody-precedence controls pass; lint and whitespace
checks pass. No production repair API or acceptance policy changes. Index and
Vault causes remain unproven until the next exact-head native diagnostic;
neither the backend input nor this observation fix is fresh acceptance.

Run 42982347366 at `8999810d27bc792acf1acbe67cb46093ab83f0db`
passed normal and repair, with successful fresh settlement and required CI.
Its now execution-bound diagnostics report the real refusals: Index cannot
read effective repository configuration, and Vault cannot resolve activation
configuration. The selected Machines conductor invokes those repository-aware
readiness gates from each installed payload directory, losing the explicitly
requested adopted project context.

The existing actual-caller readiness regression now requires the selected
project directory inside both native payload-script and payload-command
actions, while starting the caller outside that project. Both Windows cases
fail before the correction and pass after it. Machines now binds the admitted
project root into each owner and the opaque plan digest, revalidates that
directory before each read-only action, and runs the probe there. Manifest
action paths, executable/content binding, no-self-provision behavior, installer
payload working directories and apply authority are unchanged. Missing or
indirect project contexts refuse execution instead of falling back elsewhere.

This narrow production correction is coordinated on the existing conductor
issue #6464; it does not supersede #10757's separate inactive-participation
change. The complete repair test file passes 122 controls, including actual
native callers, context replacement refusals, unchanged installer context,
cancellation, changed-input rejection and the original budget cases
(466.53 seconds; wrapper including preparation/cleanup 491.91 seconds).
Changed-source lint, whitespace and the 13-plugin install-contract guard pass.

Intent and contract review: this restores the existing selected-project
readiness contract under the installer vision's authoritative replacement
goal, not a new conductor or relaxed owner judgment. The internal owner binding
and opaque digest now include the action's repository context; an earlier
digest must be replanned rather than reused. No public invocation syntax or
readiness descriptor changes. Documentation-impact review updates Machines'
README and the suite's testing guidance; a generated Machines patch changefile
covers the production behavior. Native tests above exercise Windows; the POSIX
fixture counterpart is retained but is not claimed as executed on this host.
The next existing disposable fresh run must still prove real Index/Vault
configuration and complete Machines readiness before lifecycle acceptance.

Run 42988759861 at `a9dd8eaa96675e9cd4240b8a666e70eaaa336d33`
passed normal and repair, with successful fresh settlement and required CI.
Vault now satisfies actual required readiness as `configuration-empty`; Index
alone still refuses effective repository configuration. No complete fresh
readiness or lifecycle acceptance is credited.

A configuration-only reproduction isolates another fixture input: fresh
`_roles` wrote JSON into Index's `config.yaml`. The dependency-free native reader
does not support that JSON form. The exact JSON input returns
`repository-config-invalid` with `expected a mapping entry`, without any runtime,
service or configuration owner present. An earlier YAML-only cold check did not
reproduce the fixture input and did not establish the cause. The fresh adapter
now emits native YAML, retaining the same indexer aliases and role selection.
The production parser and readiness judgments are unchanged.

A focused regression calls the real native YAML reader against the actual
`_roles` output under isolated `-I -S` Python and proves PyYAML is unavailable.
It passes (one selected test, 51 unrelated deselected); changed-source lint and
whitespace pass. Temporary tracing/preflight code used during diagnosis was
removed rather than shipped after this smaller input correction was identified.
This is fixture-only, with no additional runtime protocol or operator-document
change. The next exact-head disposable run must still prove the installed-owner
path and full readiness; local parser success alone is not that acceptance.

Run 42995152279 at `0a091a8cda54990e359c6050cd3ae5b926782a81`
passes all 13 actual runtime import/version checks and actual selected Machines
required-readiness. All nine required module observations are satisfied,
including Index and Vault `configuration-empty`. Fresh then correctly fails
the post-update exact payload comparison for Index; settlement succeeds.
This is readiness evidence, not a complete runtime-closure or lifecycle result.
Repair passes. Normal independently fails its initial Manager constructor in
`driver.drain` with `ParticipantError: transaction returned with unclosed
participants`; its case also settles successfully.

A contained real-reader comparison demonstrates an immutable-payload defect:
Index's configuration helper uses `-E`, so the cache environment flag is
ignored. It creates ten cache paths in a copied payload; explicit `-B` leaves
the entire manifest unchanged. The native gate's configuration, role and
session-stamp helpers now use explicit no-bytecode flags on both platforms.
Generator-owned catalog gates receive the same correction; only Index declares
that gate, and only its two catalog outputs change on regeneration. Runtime
owned-cache behavior, source selection and readiness judgments are unchanged.
A generated Index patch changefile covers this production correction.

Fixture payload failures now retain bounded exact path/hash differences, with
no bytecode exclusion or resealing. A failed diagnostic write preserves the
original immutable-payload refusal. The normal builder's original log was not
advertised before the cleanup exception. PR #11121 owns overlapping foreground
bootstrap hunks, so the failure is reported cooperatively there rather than
pre-patched. Only fixture diagnostics change: without an advertised request,
they may retain bounded strictly named logs from this case's private driver
directory, never witness data, unrelated files or other homes. The normal
failure's underlying cause remains unproven and unwaived.

Source validation includes 65 runtime/diagnostic controls and three subsequent
diagnostic-write/refusal edge controls, plus two passing generated-catalog
controls with one POSIX execution skip. The broader Index packet exposed stale
synthetic dispatcher fixtures: a contained old/new-flags comparison reproduces
the same configuration refusal in both. Those cases now explicitly isolate
the independent preference owner when using synthetic receipts; native reader
and readiness cases retain the real owner path by default. Missing source/SDK
inputs in private fixture payloads and setup runtimes are supplied. After these
corrections, 51 Index gate/catalog cases pass in the broad packet; its remaining
two duplicate-copy setup errors are corrected and both targeted cases pass.
The 37 platform skips are not native POSIX proof. These fixture changes confer
no namespaced-owner or live acceptance.

Changed-source lint, generated-file consistency, the 13-plugin install contract,
documentation consistency and whitespace pass. The generator's existing
eligible-unit source digest is refreshed from the exact staged LF bytes; nodes,
coverage targets, effect restrictions and budgets are unchanged. Metadata/source
binding validation is not a substitute for the next exact-head required CI.
Documentation-impact review updates the suite's evidence/diagnostic guidance;
there is no new operator syntax or mutable runtime policy. The existing
disposable Windows lane must still prove unchanged payloads, complete fresh
actions and settlement, and resolve or expose the normal bootstrap failure.
Authentic upgrade, mandatory frozen enforcement and separate Main adoption
remain open.

Required CI at `c0887c9d3a6ced56286ed93aa95ca6686630f21d` exposes a second
binding that the native-helper literal change must maintain. The Linux eligible
unit job passes 4,711 cases, then refuses the closed catalog typing witness:
`Catalog module/validator/renderer binding changed`. This failure is not waived
by the source-digest refresh or native controls.

The semantic AST comparison confirms only the module and `render` hashes
changed; `_load_command`, `load_manifest` and `expected_files` retain their
reviewed bindings. The closed `python-catalog-inputs.json` declaration now
records those two reviewed shapes, and its existing eligible-unit source
digest is refreshed from staged LF bytes. Source membership, validator/enum/
conversion checks, declared functions, unit nodes, coverage and budgets remain
unchanged. All 18 catalog correspondence/refusal controls pass. This is binding
maintenance, not a weaker witness or operator-facing protocol change; the next
exact-head required CI still owns the full Linux unit result. The already-running
`c0887c9d` native matrix is left running and will retain its own exact-head
outcome rather than being cancelled or credited to a later metadata commit.

Run 43006825243 at `c0887c9d3a6ced56286ed93aa95ca6686630f21d`
passes normal and repair. Fresh completes the runtime-closure function,
including all 13 imports/versions, actual Machines required-readiness, exact
post-update payload and transport checks, and frozen artifact verification.
It then fails the next Manager observer because the admitted-core helper
requires `runtime-source.json`. Fresh intentionally has no such source-admission
receipt. Settlement succeeds; no fresh lifecycle result is accepted. The prior
normal bootstrap failure did not recur here, but its old underlying cause is
still unproven and the cooperative report remains open.

The fresh companion now has a separate non-admission Manager observer, using
the existing installed-origin/version contract plus every frozen Manager Python
module byte, selected private generation and constructor-record hashes.
Candidate identity is bound to the verified frozen artifact; evidence explicitly
records `source_admission: not-granted`. Foreign roots, escaped generations,
changed modules and missing/empty constructor records fail. The existing
admitted-core helper and its retained source-receipt checks are untouched.
No exact-source receipt is invented in the artifact or installed snapshot.
Runtime-closure data is retained as diagnostic evidence before the later Manager
observation, without changing the final ready/result gate.

The eight origin controls and thirteen final evidence controls pass in targeted
packets, including wrong-candidate, false-admission and incomplete-record-hash
refusal. This is source-level validation; the next exact-head
hosted run still owns actual fresh Manager observation and all UI actions.
The previously committed typing-binding correction is included in that next
candidate, and required CI must independently pass it. Documentation-impact
review updates the fresh-versus-admitted observation distinction in TESTING.md.
Complete fresh acceptance, authentic upgrade, frozen enforcement and separate
Main adoption remain open.

### November 11 - Fresh launch phase and bounded lifecycle observation

Required CI passes at `bedaf3e3eb574d96e3e41811361321762f65693e`.
Exact-source run 43014684583 passes normal and repair. Fresh preparation now
passes complete construction, all 13 runtime imports/versions, actual Machines
readiness, unchanged frozen payload/transport/artifact checks and the separate
non-admission Manager observation. Exercise still fails: paired worktree and
instruction preparation finish, but no user history or provider request exists
when the witness's 60-second completed-turn clock expires. Settlement succeeds.
No fresh Create/Resume/Open acceptance or source admission is inferred.

Source tracing identifies two existing, independently bounded 60-second New
required-readiness passes, including one after instruction preparation. Fresh
now separately waits up to 120 seconds for the actual initial/resumed user event,
then applies the unchanged 60-second completed-turn proof. Both waits are clipped
to the unchanged exercise deadline; core timing, production readiness, UI
actions, duplicate-owner/history checks and rendered reply requirements remain
unchanged. Three virtual-clock controls prove unchanged core timeout, fresh
phase completion and refusal at the outer deadline.

The same run's observer consumed its 512 process identities during construction
and reported one scan error. Its cause is still unproven. Fresh now arms this
trace only after preparation records readiness. The cap and scan-error failure
remain; the first eight failures retain only exception type and HRESULT, never
messages or arguments. A real controlled PowerShell scan failure still exits
nonzero with sanitized diagnostics.

The focused phase/pane/trace packet passes all 19 controls. A broader local
packet passes 231 cases before exposing one overstrict synthetic cache
expectation; both installed stop-selection cases pass after correction.
Encountered stale test fixtures now use the witness's injected runner, advance
a virtual progress clock through the real throttled emitter, verify the current
unsealed Index cache namespace, and retain exact construction exit-code/stderr
proof without requiring one obsolete leading message. No production runtime
hunks change in this slice. Four hosted-native acceptance cases were explicitly
not credited locally: they require their actual executable or prepared disposable
VM inputs. Ruff, the 13-plugin install contract, documentation consistency and
whitespace pass.

Documentation-impact review updates TESTING.md's fresh launch and trace phases;
no public invocation, readiness policy or deadline changes. The next exact-head
hosted matrix must still prove complete fresh UI actions and settlement.
Authentic upgrade, mandatory frozen enforcement and separate Main adoption
remain open, as does the older cooperatively reported normal bootstrap cause.

### November 11 - Resume attachment shares the fresh launch allowance

Required CI passes at `30da5278ca50c5ed46169f00aff137fff1607e9e`.
Exact-source run 43024963735 passes normal. Fresh again passes preparation
and now completes actual Create, active Open, detach and three provider-backed
turns. It fails before the final resumed turn: the separate prompt-attachment
wait still uses the core 30-second default. The retained final rendered frame
contains both required prompt markers. Fresh settlement succeeds; the four
actions are not accepted as a complete result.

Fresh Resume attachment and its subsequent actual-user-event wait now share
one 120-second launch allowance from action activation. The user-event wait
uses only the remaining time, never a new 120 seconds. Core attachment remains
30 seconds; completed-turn and outer exercise deadlines remain unchanged.
Virtual-clock controls prove core refusal, fresh completion and exhaustion
of the shared allowance before a late user event.

The process observer still reports four CIM exceptions and a truncated
512-identity trace after arming at the correct phase. Exception type and
HRESULT alone do not identify their cause. Diagnostics now additionally retain
numeric CIM native/status codes, with nulls for non-CIM failures. All scan errors
still fail; query timeout, cadence, cap and argument/message omission remain
unchanged. Both real CIM-exception and ordinary-exception controls prove
sanitized reports and nonzero exit.

Repair fails initial Manager bootstrap before its repair mutation. Newly
retained private-driver output ends with the original error,
`Proof object is not owned by this principal`, after successful wheel
installation and payload inventory. The outer unclosed-participant error masks
that physical publication-proof refusal. Exact offending path/principal and
root cause are still unproven. Settlement succeeds with no errors or remaining
tasks. This distinct evidence was reported cooperatively on #11121 at its
still-open head `c3770520b3cbde2fc86e23251b393b8832b51c55`; no competing
constructor/publication change is made here.

All 21 phase/selection/pane/trace controls and changed-source lint pass.
Documentation-impact review records the single shared Resume launch allowance
and numeric diagnostics in TESTING.md. The next unchanged hosted matrix remains
required for complete fresh actions, strict scan observation and settlement.
No authentic upgrade, frozen enforcement, Main activation or rollout is claimed.

### November 11 - Completed resumed turn, final sequence still refuses

Required CI and exact-source run 43030233768's normal and repair cases pass at
`4447ddb60cd5f1df26a9f05e7e0cfa4d00e89f89`. Fresh preparation and settlement
pass. The shared Resume launch allowance now reaches the original conversation,
replacement native owner and fourth provider-backed reply; its launch phase
takes 32.7 seconds. The final exact sequence assertion still refuses acceptance.
The history has seven user and five assistant messages, not four each, with
five provider requests. Earlier rendered evidence contains a Context Handoff
intervention and an extra first reply, but that does not establish the identity
of every extra event. No event is ignored and no complete result is credited.

Failure diagnostics now project at most 32 user/assistant events as fixture
labels, content byte counts/hashes, fixed envelope flags and a closed source
classification. They retain no message text and do not alter any sequence,
owner or continuity assertion. Diagnostic-read errors preserve an already
failing original exception. Focused controls retain duplicate reply labels,
classify an unknown/known-prefix extra, omit its private text and bound output.

The trace again fails, now with three CIM exceptions whose native and status
codes are both generic failure (1). Its cause remains unproven. The census
requests only the four process properties it consumes, reducing unnecessary
provider serialization without filtering out any previously eligible process.
Samples add elapsed milliseconds and record count at failure. The one-second
query limit, 100-millisecond cadence, 512-record cap and fatal scan-error policy
stay unchanged. Controls verify the exact requested projection, process-birth
identity, bounded output and sanitized nonzero ordinary/CIM failure reports.

All 16 focused diagnostic/deadline/trace controls and changed-source lint pass.
Documentation-impact review updates TESTING.md's content-free sequence and scan
diagnostics. The prior repair publication-proof refusal did not recur at this
head; that alone does not establish its cause or repair, and its cooperative
#11121 report remains open. The next exact-head hosted matrix must identify
the extra history events and prove strict final sequence and scan success.
Authentic upgrade, mandatory frozen enforcement and separate Main adoption
remain open.

### November 11 - Attribute runtime system context and retained image teardown

Required CI and run 43036181824's normal/repair cases pass at
`a50ae2b375035634d28cdd1e835a99ac30dd3de1`. Fresh preparation and settlement
pass. The reduced four-property census completes with zero scan errors and no
truncation in this shorter run; that is not complete-window acceptance.
The failure occurs after the third completed provider-backed turn: native
image lookup returns access denied. Source inspection finds this observer
calls the raw query rather than the installed-TTY helper already designed
for image teardown preceding the retained handle's exit signal.

Native attribution now reuses that existing helper, with no shared-helper edit.
It may ignore a query failure only when the same pinned handle confirms exit
within the helper's existing 250-millisecond bound (clipped by its deadline).
Live/unverified query failures still propagate. Focused controls prove both
outcomes and exact handle closure, without reopening a PID or relaxing
native-owner/birth/history binding.

The message projection identifies two extra startup user events explicitly
marked by the actual CLI as `source: system`, one before the first reply and
one between that reply and its duplicate. Operator prompts retain their
unmarked source. These are runtime context events, not duplicate operator
input. Fresh alone now validates this distinction: exactly four ordered
operator prompts and matching replies, with an additional matching reply
permitted only after new explicitly marked system context. Unmarked or
non-text context, duplicate operator prompts even when marked system,
unpaired/foreign replies and missing operator replies fail. Core retains its
original exact four-user/four-assistant comparison.

Fresh turn completion additionally requires the current prompt's exact reply
followed by a turn-end event; the extra hook turn cannot inflate a cumulative
count into false completion. Neither the system-context classification nor
image helper supplies an inference result, history, owner or success callback.
The selected-suite adapter alone enables this observed all-plugin contract.
No frozen-candidate source admission or promotion enforcement is inferred.

The initial 34 focused context/deadline/pane/trace and retained-image controls
pass, including the existing helper's own two attribution controls.
The broader affected-file packet passes all 250 locally supported cases after
adding malformed-context refusal; four hosted acceptance cases remain explicitly
uncredited locally. Changed-source lint passes.
Documentation-impact review records the distinct core/fresh history contract
and confirmed-exit-only image behavior in TESTING.md. Complete fresh lifecycle,
ordinary admitted-baseline update, mandatory frozen enforcement and separate
Main adoption remain open.

### November 11 - Complete actions reach strict daemon cleanup

Required CI and run 43043246633's normal/repair cases pass at
`fa0dd539837130678948d90916358b95ff2ad684`. Fresh preparation and all four
actions, typed-system-context history, native ownership, final normal exit and
provider-continuity checks complete. Exercise then correctly refuses its
unsettled daemon-instance results: two MCP launch reservations remain held
with unknown launch outcome and no PID. Independent settlement succeeds, but
does not convert those held outcomes into accepted lifecycle evidence.

The verified MCP interpreter is physically under its selected generation's
`.venv`. The canonical resolver supports that layout, but the bootstrap's
healthy-generation shortcut accepts only flat `Scripts/python.exe` or
`bin/python`. Four new healthy nested controls reproduce unnecessary
provision-supervisor submission before the fix, in PowerShell 7 and Windows
PowerShell for ordinary and source-repair generations. The shortcut now
recognizes the canonical nested paths as well, preserving exact selected
generation/source, completion and resolver link checks. Its synchronized
POSIX block is updated consistently in Bridge, Dispatch and MCP; no shared
service is repaired or installed. Current paginated #11121 file inspection
shows no overlapping bootstrap files; its constructor/publication hunks
remain untouched.

The complete MCP bootstrap packet passes 56 cases after the correction,
including flat/nested healthy, incomplete, wrong-generation and foreign-source
controls. Fourteen native POSIX cases remain untested on Windows; block
synchronization is separately verified. Patch changefiles cover all three
affected plugins. MCP's canonical footprint refresher updates only the script
producer digest: revision 36, contract semantics and the `src` digest remain
unchanged. The full install-contract guard passes after that binding refresh.

The same native run reports three generic CIM scan failures taking 5,754,
1,940 and 1,284 milliseconds, despite the existing one-second operation limit.
Their exact underlying cause remains unproven. The tracer now lazily creates
one owned local DCOM session after arming and reuses it, rather than relying
on implicit per-poll sessions. A bounded three-scan read-only host probe verifies
the client path is supported; it is not full-suite acceptance. Creation and
query limits, cadence, cap and fatal-error policy stay unchanged. `finally`
closes that exact session; close failure is also fatal. No service, remote
endpoint or credential deployment is introduced.

Thirteen Manager controls pass, including actual two-poll mock-session reuse,
creation/query/close failure, pre-window no-acquisition, strict held cleanup
beyond the 32-record diagnostic sample and preservation of original custody
failure on diagnostic-write error. Fresh teardown now retains that bounded
cleanup projection before refusing. No reservation is fabricated, deleted or
reclassified as settled. Changed-source lint, the 13-plugin install contract,
documentation consistency and whitespace pass. Documentation-impact review
updates MCP's healthy-layout contract and TESTING.md's session/cleanup evidence.
The next unchanged exact-head hosted matrix must still prove complete cleanup,
strict scan success and the final post-lifecycle checks. Authentic update,
mandatory frozen enforcement and separate Main adoption remain open.

### November 11 - Reconcile the source-binding conflict before CI

The published `bf22bb3f5e6cbfadd9ff4f83cbf32c80130b3b69` has successful
trusted lockdown and source-attribution checks, but no PR CI run. Live CI
triggers match the candidate and still include draft PR updates. GitHub reports
a merge conflict, not a successful or skipped CI result. Run 43053171740 remains
owned at its original frozen source; it is neither cancelled nor credited to a
later reconciled candidate.

A local backup preserves that published tip. Rebase onto
`7508f32f3ba0abef31d63c192052866079ecf39b` replays 21 owned commits cleanly
and exposes only the final MCP footprint producer-digest conflict. Both branches
retain revision 36 and identical semantic contract fields. The resolution
preserves newer upstream code and source binding, then uses the canonical
refresher to recompute the merged producer digests. It does not choose an old
manifest wholesale, change footprint policy, reset files or use shared stash.

The reconciled code at `72611a2eff79a9668c0a62aafa2d9eb484adeaba` passes
56 MCP bootstrap cases and all 257 locally supported Manager cases, plus
install-contract, documentation and whitespace checks. Four hosted acceptance
cases and fourteen native POSIX cases remain uncredited locally. The managed
rewrite preview confirms the exact old published-head lease; it is not itself
publication. Actual managed rewrite, current-head CI and a new exact-source
hosted matrix remain required before review/merge acceptance. No old green
check or native result is reused across the rebase.

### November 11 - Cleanup and census pass; post-lifecycle immutability refuses

The retained frozen-source run 43053171740 at
`bf22bb3f5e6cbfadd9ff4f83cbf32c80130b3b69` passes normal and repair.
Fresh preparation, all actual actions/continuity checks and all six daemon
cleanup outcomes complete successfully. Its census has zero errors and closes
the owned CIM session; the 512-record truncation remains explicitly recorded.
Fresh still fails: the next post-lifecycle frozen-payload comparison finds an
Index change. Independent settlement passes. No complete result or immutable
payload acceptance is inferred from the successful earlier phases.

Current required CI 43056366851 at
`e530d48508ed38053b81641537c31e37ab8680cb` passes eligible units, then its
runtime-resolution source guard flags the new nested paths in the healthy-slot
comparisons as venv-link launches. The code already consumes the canonical
marker resolver and compares its result with physical paths beneath the exact
selected generation. The guard now recognizes only those two established
Bash/PowerShell comparison shapes. It still flags direct nested-venv launch
assignment, root-link comparison and a forbidden path on a mixed line.
No per-line allow marker or general launch exemption is introduced.

Nineteen guard controls and the actual repository-wide strict scan pass.
The post-lifecycle payload verifier now reuses the bounded path/hash difference
projection so the next refusal identifies the added/removed/changed paths.
Bytecode remains included; no path is exempted and no manifest is resealed.
Two focused cases retain the exact added-cache refusal and preserve it even
when the diagnostic write fails. The combined 14 Manager payload/cleanup/trace
controls, changed-source lint and whitespace pass. Documentation-impact review
updates TESTING.md; no runtime, profile, source-admission or budget policy changes.
The still-running exact `e530` native matrix retains its own outcome, not the
next source head's result. Current-head CI, complete fresh immutability and
settlement, authentic upgrade, frozen enforcement and Main adoption remain open.

### November 11 - Identify and stop scope-guidance payload bytecode

Required CI 43060269249 passes at
`2caee135fd988dc890a70fc274e2d70773e05c36`, including the corrected strict
runtime-resolution guard. Exact-source run 43060369382 passes normal/repair.
Fresh again passes preparation, actual actions/continuity, all six daemon
cleanup outcomes and zero-error census/session close, then refuses post-lifecycle
Index payload immutability. Settlement succeeds.

The new bounded difference report identifies twelve added paths and no removed
or changed original file: eight `.pyc` files and four cache directories beneath
Index's configuration-reader scripts, vendored configuration helpers and source
package. The active session scope writer invokes `emit_scope_binding.py` with
`-E` but without `-B`; that script imports `resolve_effective_config` and the
identified module chain. Its retained PowerShell/Bash wrappers have the same
omission. Environment cache/no-bytecode flags are ignored by `-E`.

Three actual cold-copy controls reproduce those eight cache files before the
fix through the real session-writer function, PowerShell 7 and Windows
PowerShell. The callers now explicitly add `-B`, preserving `-E`, UTF-8, cwd,
guidance output, budgets and normal runtime-owned caching. No payload file is
excluded, deleted or resealed. Current paginated #11121/#10757 file checks show
no overlapping caller or payload-stability-test hunks. The existing Index patch
changefile is clarified to include the scope-reader case, not duplicated.

The broader packet also exposes three older role/construction fixtures whose
explicit repository override is only a directory. The actual reader correctly
reports `repository-override-unavailable`. Fixtures now initialize real Git
roots and supply the complete copied configuration-reader inputs; production
refusal of invalid overrides is unchanged. The actual role and construction
assertions remain, rather than being replaced with success callbacks.

All thirty configuration-scope/guidance/catalog/payload-stability cases pass,
with nine platform skips uncredited. Each new helper case repeats against the
same cold payload and proves every file unchanged. Changed-source lint,
strict runtime-resolution, the 13-plugin install contract and whitespace pass.
Documentation-impact review updates Index's read-only scope contract and
TESTING.md. The next unchanged exact-head hosted matrix must still prove the
final immutable comparison and complete result; authentic update, mandatory
frozen enforcement and separate Main adoption remain open.

### November 11 - Keep the burst pass separate from the actual launch shape

The managed reconciled-head rewrite initially exceeds Git's existing
30-second command limit. Git and the provider independently still report the
old `7fe797` ref, so no successful publication or dispatch is inferred.
One managed retry succeeds using the recorded exact lease, without a manual
force, hook bypass or timeout change. The actual hook is the managed PR shim
with no local companion; its generic error suggestion does not prove a
runtime-update stall.

Current source `c9e546c87d7268ad8c7dce3f234de3640344d1ac` is published,
mergeable and draft. Its exact CI run 43120307991 passes. The source-bound
burst request `4f7d4ceb484a48c6a92f71085c877123`, run 43120805625 attempt 1,
also qualifies on the existing protected controller
`85ffb5f56366635474bb78fb6864463683e50d5c`. The actual JUnit summary records
128 short-lived children, 123 observed incarnations, 38 successful scans,
query-pipeline total/max 1553/102 ms and session creation 255 ms. No scan or
close error, missing stop, truncation or unowned child cleanup is admitted.
This six-second pass does not reproduce or waive the original full-run
failure.

Further source/trace correlation corrects the workload model: the fresh census
window arms only after runtime installation completes. The retained original
records near failure show repeated direct parent/child Python launch pairs,
whereas the short profiles used base Python. The next residual changes only
that missing launch dimension: the same bounded burst uses an actual private
standard-library no-pip Windows venv. No suite or dependency installation,
shared runtime, provider substitution, global machine change or full-suite
retry is introduced. Venv launcher/base-child behavior remains a hypothesis
until that source-bound hosted case runs.

The expanded profile's helper parses, F/E9 lint and documentation consistency
pass, and ordinary contained selection skips all four T3 cases in 0.60 seconds.
No native provider operation or venv construction runs in that default lane.
The preceding exact-source CI and hosted burst success are retained at `c9e546`,
not relabeled as evidence for this new residual.

### November 11 - Close the short-profile investigation without waiving census

The venv-only request `f45dea5b1cae41f995cd2ec1a5f8c33d`, run 43146338506
attempt 1, qualifies exact source
`5eacaa4c961dd0bfffaffc3f22a8294a9518accb` on protected controller
`3c0e071e2113d7934b32f288d5400b02b9016b2b`. Its retained JUnit summary
confirms actual venv execution, 128 short-lived children, 253 observed
incarnations and 37 successful scans. Query-pipeline total/max is 1542/126 ms,
with session creation 220 ms. Scan errors are zero, the session closes,
the window and stop are observed, and no truncation occurs.

All four short diagnostic dimensions now have real hosted evidence, but none
reproduces the original long-workload failure. Stop growing this synthetic
load matrix: buffering, short concurrent load and venv launch pairs have no
positive causal evidence. No provider replacement, census exemption or
larger timeout follows from these passes.

Publication of `5eacaa4` was initially interrupted by local PowerShell
startup stack-overflow/out-of-memory failures. Independent Git/provider reads
prove that attempt had not published; the native profile was not submitted.
After read-only host measurements show memory recovered, the normal managed
publication and one new request succeed. No shared process termination,
configuration change, runtime repair or bypass is performed.

Dev moves during that interruption. A backup preserves `5eacaa4` before
reconciling onto `8426b0512190e1c2944d2721714e9229af20f2e9`. The two
conflicts are additive: Machines retains this branch's selected-project
binding and the upstream script-only installer-runtime declaration/digest;
MCP's canonical producer refresh preserves the merged scripts with revision
36 unchanged. Range-diff retains all 35 owned commits, with only those
two integration seams changed. Frozen profiles remain evidence for their
original commits, not the reconciled tip.

The remaining diagnostic criterion requires the original workload: capture
the new passive failure phase, safe numeric embedded HRESULT/category and
query-pipeline timing when the complete installed consumer executes. One
instrumented exact-candidate run through the existing disposable native
owner is justified after focused integration validation and publication.
This is not an unchanged retry hoping for zero errors. Keep the existing
60-minute job, 1800/900/300/90-second stage limits, one-second CIM operations,
100-ms cadence, record/failure caps, fatal errors, strict payload comparison
and exact owned cleanup. A pass cannot erase the earlier failed run or prove
its root cause; a failure must be diagnosed from the new retained fields.
No new workflow, runner capacity, operator installation or Main activation
is authorized. Full acceptance, admitted-baseline upgrade, frozen enforcement
and the parent's other open validation obligations remain separate.

Reconciled validation passes 30 focused Machines installer-runtime/project-context
cases and 33 MCP bootstrap/readiness/provider cases. The initial broader
Machines file selection reaches its unchanged 180-second subsuite limit before
completion; that full file is not credited as passing. The narrower selection
exercises the actual merged seam without enlarging deadlines. All contributed
Python passes F/E9 lint; strict runtime resolution, 13 install contracts,
documentation consistency and whitespace pass. Documentation impact remains
the testing reference and this journal; there is no new runtime command,
configuration or policy. The pending exact-source CI is independent of these
source controls and frozen earlier results.

### November 11 - Verify the strict full consumer and address review

Exact-source run 43148010946 attempt 1 at
`e2d824d74d7881bdecf96bfce51550eff3712fd6` passes normal, repair and fresh.
The actual fresh artifact binds the same candidate/source and tree, complete
24-plugin runtime closure, all five lifecycle outcomes and original
conversation continuity. Every stage returns zero. The result is written only
after strict daemon cleanup and the second whole-payload comparison; no payload
difference files exist. All six daemon instances settle, with no omitted or
unsettled entry and no remaining tasks.

The instrumented census performs 1,636 scans with zero errors; query-pipeline
maximum is 464 ms and session creation 47 ms. It observes window/stop and closes
its session. The declared 512-record list truncates, but polling and fatal-error
enforcement continue afterward. This is a strict fresh-candidate observation,
not proof that earlier intermittent CIM failures are repaired. Those historical
failures, unavailable older artifacts and the original preparation-failure
cause remain explicit limitations. CI 43147922441 also passes at exact `e2d824d`.

The PR enters review with accurate current evidence and remaining boundaries.
Copilot review 5868827517 names one blocking finding: selected-project readiness
can expose Index's bare-name Git lookup to checkout-local commands on Windows
or relative PATH entries. Preserve the selected context rather than undoing it.
The reader now enumerates only absolute PATH directories and directory-qualified
command candidates for implicit Git, Worktrees and PowerShell resolution.
Windows suffixes explicitly follow PATHEXT, including Python 3.11's qualified
lookup behavior. Explicit Worktrees command overrides and same-cell routing
retain their existing precedence.

Three focused cases place a marker-writing fake Git in the current directory,
dot PATH and a relative tools directory. The actual probe still resolves the
real repository without executing it. All 63 configuration-reader cases pass
after correcting the first implementation's missing Windows suffix expansion
and subsequent missing import; intermediate failures are not credited.
F/E9 lint passes. README documents implicit versus explicit command selection.
The existing Index patch changefile covers this review fix. It adds no command
syntax, service, network lookup, new runtime or per-call child process.
New-head CI, review and installed-consumer validation remain required; the
successful `e2d824d` run is not silently relabeled.

### November 11 - Identify and stop snapshot-guidance payload bytecode

Required CI 43069145815 is green at
`594d9a3ff5d9a185e4100cc99f9f9faed4390239`. Exact-source native run
43069195469 passes normal/repair. Fresh completes the actual four actions,
same-conversation history and six settled daemon instances, then refuses
post-lifecycle SSH payload immutability. Preparation and settlement return
zero; settlement has no errors or remaining tasks.

The actual difference report is captured first: seven added entries, no removed
or changed original entry, without truncation. Four bytecode files belong to
the payload's process primitive, snapshot dispatcher, runtime selector and
installation-context primitive. Three cache directories account for the rest.
This is not a Bridge communication failure.

The SSH writer and retained mesh wrappers invoke isolated snapshot helpers
without explicit `-B`; nested runtime and optional Worktrees request workers
also load payload helpers. Environment cache controls do not survive `-I`.
Four actual cold-copy Windows controls reproduce payload mutation while
returning a valid bound external-state snapshot. The source correction adds
explicit `-B` throughout those read-only caller chains, retaining isolation,
UTF-8, invocation governance, current selections, output and production budgets.
Ordinary runtime caching, payload integrity and admission are unchanged.
No cache entry is excluded, deleted after admission or resealed.

The focused after packet passes 54 tests, with four platform skips and six
unselected longer native/latency controls uncredited. The four new Windows
controls each repeat the actual caller and compare every file and directory
in both SSH and Worktrees payloads. The cold fixture skips payload precompilation
without changing default fixture behavior or installed-runtime preparation.
Documentation-impact review updates SSH's read-only snapshot contract and
TESTING.md. A fresh exact-head hosted matrix remains required before readiness;
authentic baseline update, frozen enforcement and separate Main adoption are
still open.

### November 11 - Preserve cold standalone Worktrees hook payloads

Required CI 43075895027 passes at
`b46c83443eaeebb8d8a141e92f679e5895c890bb`. Exact-source native run
43075917269 passes normal/repair. Fresh completes the actual four actions,
same-conversation history and all six daemon settlement outcomes, then refuses
Worktrees payload immutability. Preparation and settlement return zero with no
errors or remaining tasks.

The actual difference is captured before further changes: twelve added
entries, ten bytecode files and two cache directories, with no removed/changed
original entry or truncation. These belong to the standalone hook runtime,
lifecycle/runtime context, managed-session and native-source helper imports.
This is not a Bridge communication failure.

The actual raw session-start declaration reproduces cache writes on PowerShell
7 and Windows PowerShell, even for an ordinary unadmitted directory with no
runtime available. Pre/post no-op controls preserve their payloads. The narrow
correction disables bytecode writing only when `hook_client.py` executes as
the standalone program, before its first sibling load. Importing the client
does not change the resident runtime's cache policy; selected runtime-child
caches and lifecycle decisions/budgets remain unchanged. Current paginated
#11121/#10757 checks have no overlapping hook-client or new-test file.

The focused after packet passes 54 tests, with four platform skips uncredited:
actual raw-hook cold/repeat entry comparisons, an actual import-mode cache-policy
control, existing fail-open hooks and native-source controls. The new no-op
regression is not admitted lifecycle acceptance. Documentation-impact review
updates Worktrees' standalone hook contract and TESTING.md. A new exact-head
hosted matrix is still required; no cache is excluded/deleted/resealed and no
parent baseline-update, frozen-enforcement or Main-adoption gate is closed.

### November 11 - Retain fresh bootstrap logs before the outer driver error

Required CI 43082224444 passes at
`552a8e15e1cdba610fe59dffab0ae509d85d0479`. Exact-source native run
43082234109 passes normal/repair, but fresh fails during preparation, before
exercise or a payload comparison. The actual bootstrap engine command returns
`ParticipantError: transaction returned with unclosed participants` through
`runtime_install_driver._foreground -> driver.drain -> Transaction._close ->
Witness.close`. No original builder/request error is present in its stderr.
Preparation returns one; settlement returns zero with no errors or remaining
tasks. Process tracing never arms and contains no lifecycle observations.

This is not another observed hook-bytecode failure or a Bridge communication
failure. The earlier #11121 cooperative reports retain the same masking
boundary, but this run does not expose enough evidence to equate its underlying
cause with the earlier proof-ownership error. The original contributor's PR
remains open at `c3770520b3cbde2fc86e23251b393b8832b51c55`; its constructor,
driver-drain and publication work is not pre-patched or superseded.

The fresh fixture's command owner was missing the existing case-owned request-log
retention call used by normal/repair. A real failing child reproduces the lost
unannounced log. One call now delegates to the same bounded collector before
raising the unchanged command failure. It scans no broader root, retains only
strict request names under the private case, and changes no admission, custody,
retry, installation or cleanup behavior. No constructor result is converted to
success.

All 71 fresh-runtime/diagnostic controls pass, followed by four explicitly
selected existing bounded-log controls. Their initial empty selection is not
credited: the native-directory collection exclusion was removed for only those
four diagnostic controls, with all full-suite actions deselected. The actual
fresh preparation failure remains unresolved pending its original request
evidence. Documentation-impact review updates TESTING.md. A changed diagnostic
candidate may gather that evidence in the existing hosted lane, but no unchanged
retry, accepted flake, cache waiver or parent completion is claimed.

### November 11 - Preserve attribution payload and complete failure diagnostics

Required CI 43087596699 passes at
`fc4115e08af6410964b25decdd89f5274555f2c3`. Exact-source native run
43087608190 passes normal/repair. Fresh passes preparation and completes the
four actual lifecycle actions and same-conversation history, then refuses
AI-attribution payload immutability. Settlement returns zero with no errors or
remaining tasks; all six daemon instances settle. No preparation request fails,
so no original error is recovered for the prior #11121 masking boundary. That
cause remains unproven, not fixed or waived by this later preparation pass.

The actual difference is captured first: one profile-helper bytecode file and
its cache directory are added, with no original entry changed or removed and
no truncation. The standalone guidance writer imports `attribution_profile`
before any bytecode control. Real Python/PowerShell 7/Windows PowerShell
entrypoints reproduce the addition with valid current policy output. The first
test fixture had only a directory and correctly received unavailable policy;
it now initializes a real Git repository rather than changing the producer's
admission. The source correction disables bytecode only for standalone writer
execution, before the import. Policy/cache/profile behavior and imported callers'
cache policy remain unchanged.

The after packet passes 91 writer/profile controls, with eleven platform skips
uncredited. Each new positive repeats the real caller and compares the complete
payload. The final observer also now preserves bounded differences for every
changed payload before raising the unchanged first refusal. A two-owner real
manifest regression fails before the change because the second report is lost;
all 72 fresh-runtime controls pass afterward. No cache is excluded, deleted or
resealed and no changed owner is certified.

This run also has one actual CIM scan failure: native error/status code one,
elapsed 1349ms, at 32 records. The session is created and closed, but this is
not clean native-census evidence. Existing one-second queries, cadence, caps and
fatal-error rules remain unchanged; the next exact-head proof must satisfy them
too. This is not a Bridge communication failure.

Documentation-impact review updates AI-attribution's standalone-read contract
and TESTING.md's complete failure-report and cold-writer flows. Full fresh
acceptance, authentic baseline update, frozen enforcement and Main adoption
remain open.

### November 11 - Reconcile the attribution candidate with current Dev

GitHub marks published `4bdf4a06e9138599c46ac4a02917eeaa63869a04`
conflicting, so no current-head PR CI is delivered. The already-dispatched
43093690653 native run remains owned at that frozen source, not evidence for a
later reconciled candidate.

The owned branch is backed up and rebased onto
`a6b3ec102dae86ebe080144922602ad4617e1977`. Only TESTING.md, MCP's README and its
generated uninstall profile overlap. The sole conflict is producer hashes;
both sides retain revision 36 and identical footprint semantics. The canonical
refresh tool preserves the newer upstream source binding while regenerating
the merged script binding. Range-diff confirms all 29 owned commits remain,
with no other patch changed. No constructor/publication logic, upstream
readiness/provider change or source contract is discarded.

Reconciled code `3205bdae272d7d3255afd48460cbcab5b087e0f2` passes 66 focused
MCP bootstrap/readiness/provider controls, with fourteen platform skips
uncredited. All 34 owned Python files pass F/E9 lint; strict runtime-resolution,
13 install contracts, documentation consistency and whitespace also pass.
Prior attribution/Manager source controls retain identical patches and their
recorded revisions; the frozen native run is not silently relabeled.

Publication uses the managed history-rewrite flow and exact recorded old-head
lease, never manual force, a stash or a gate bypass. New current-head CI and
exact-source native acceptance remain required before draft readiness; the
earlier CIM scan and preparation masking gaps remain unwaived.

### November 11 - Separate Actions failures from the budget cue mutation

The frozen pre-rebase run 43093690653 at
`4bdf4a06e9138599c46ac4a02917eeaa63869a04` finishes with all three jobs red.
Normal and repair complete their qualification commands successfully, then
artifact creation fails after five retries with an HTML response where the
Actions client requires JSON. Fresh separately fails its post-actions retained
payload comparison: `budget-guidance: the frozen installed payload changed`.
The original source callsite is after `_actions` and native cleanup, not before
the lifecycle. The outer observer also exits one. Preparation/settlement return
zero and exercise one, with errors empty.

All three artifact-upload steps fail and the artifact API lists no retained
artifacts. The original complete path-difference, trace, cleanup and result
files therefore remain unavailable; no exact hosted bytecode list, clean census
or full native acceptance is inferred. This run cannot qualify the rebased
candidate regardless. Current-head 43095516456 at `99e9ef18b9` separately reports
an internal Actions startup error with no jobs. Its one accepted platform-start
retry again produces no jobs; no further automatic start request is planned.
The exact external blocker is recorded in #12013 comment 6806733466.

Independent local reproduction uses the budget plugin's existing actual writer
and wrappers with copied cold source, without a fake policy or provider. Python,
PowerShell 7 and Windows PowerShell each add seven source bytecode files and
one cache directory while returning the correct unavailable-config cue and
empty output envelope. This proves a local mutation, not the contents of the
lost hosted report. One standalone-only setting now disables bytecode before
those imports. Imported callers, pure posture, reader/refresh restrictions,
five-second writer deadline and no-provisioning behavior remain unchanged.

All 47 guidance controls pass afterward, with four platform skips uncredited.
The expanded existing control compares every file and directory after both
startup and resume. Documentation-impact review updates the budget plugin's
read-only cue contract and TESTING.md. No census rule, payload integrity,
artifact-publication requirement or parent completion gate is relaxed.

### November 11 - Close the attribution test's admitted type regression

Current-head CI 43101429123 at
`a31ad9dade070ee165f8aa37c1bb6d115b61af64` completes eligible units successfully,
but its admitted Python contract reports seven errors in the new attribution
cold-writer test. This is an owned test regression, not an accepted flake or
infrastructure waiver. Missing function/return annotations, direct use of the
private `sys._base_executable` attribute and nullable shell arguments are now
corrected with explicit types, the existing attribute fallback idiom and real
non-null assertions. Production behavior and the regression assertions remain
unchanged.

The canonical `typecheck.py ai-attribution --strict` contract passes all eight
source targets for both Linux and Windows. Its missing environment is prepared
only after the checker reports it missing. The corrected cold/repeat writer
packet passes three native cases, with one POSIX skip uncredited; F/E9 lint and
whitespace pass. No type admission, target or guard is removed or bypassed.
Documentation-impact review requires only this validation journal; no operator
syntax, configuration or policy has changed.

The same CI run publishes a complete unit-evidence artifact
(13892407316, 1,804,496 bytes). Actual jobs starting and artifact creation
succeeding are concrete changed Actions boundaries relative to the prior
startup/upload failures, not proof that every provider path is repaired.
After publishing the type correction, one new-candidate native run may use the
existing unchanged owner and budgets. This is not a third retry of the
unexecuted `99e9` run; another startup failure still does not license a retry
loop. Required current CI, native artifact/census/cleanup evidence and every
parent gate remain open.

### November 11 - Isolate the remaining real-provider census failure

Exact-source run 43104575507 at
`0935a801f18b548e5c664295ccf0365a7d2b0c5a` completes normal and repair
successfully. Fresh preparation, exercise, result assertions and settlement
also return zero. Its retained result records all five authentic lifecycle
actions, the complete 24-plugin closure, unchanged frozen payloads and all six
daemon instances settled. This remains a controlled candidate construction,
not an admitted historical-baseline upgrade.

The outer fresh observer still fails: two real CIM queries report generic
`CimException` status/native code 1, HRESULT -2146233088, after 2348 and
1930 milliseconds, with 39 and 298 accumulated records. Session creation and
closure, baseline-window arming and cooperative stop are recorded. The bounded
512-record list is truncated. These are fatal census failures, not full fresh
acceptance; the existing query deadline is not evidence that either exception
was a timeout.

A separate short read-only provider probe does not support a buffering fix.
All three inline and three buffered queries succeed despite deliberate
300-millisecond consumption per owned row. Only the six probe-owned children
are signalled and reaped. This is diagnostic evidence, not a local full-suite
run or an acceptance retry.

The existing observer now adds bounded aggregate scan/query-pipeline timing,
failure phase, fixed error category and numeric-only embedded HRESULT
classification. It never writes raw error messages or command arguments.
The same one-second DCOM operations, 100-millisecond cadence, record/failure
caps and fatal-error policy remain. Query-pipeline timing includes consumption;
it is not claimed to measure only the provider.

Thirteen focused real-PowerShell controls pass through the contained runner.
Its first attempt exhausts the 240-second aggregate during preparation, before
pytest reports a result. The completed cache fingerprint matches the candidate,
and the same selection then passes with unchanged limits in 75.05 seconds.
The two new six-second real-provider profiles are T3, skipped by default
(two skips verified) and selected only explicitly through the existing
owner-only Windows Tools qualification lane. Their hosted result is pending;
neither a profile pass nor these controls waive the original census failure.
F/E9 lint and whitespace pass. Documentation-impact review updates only the
testing command/reference and this journal; no runtime interface, policy or
installer behavior changes.

The published Git ref identifies `0935`, but both PR provider surfaces still
report the preceding `a31ad9` head. No repush, dummy commit, forced metadata
edit, close/reopen or draft-readiness change is used to manufacture convergence.
Publication and current-head CI remain separate gates. The next step is the
narrow hosted quiet/churn profile, not an unchanged whole-suite retry.
Admitted-baseline upgrade, complete fresh acceptance, frozen enforcement and
separate Main adoption remain open under the parent.

### November 11 - Measure the narrow hosted profiles and reconcile Dev

Owner-only Windows Tools request `a11dc0049fd84a4ba3e865d7cffd4d81`, run
43117520605 attempt 1, qualifies the exact `7fe797a01f2a6d0112a45cca61799ac7a32dc392`
source on the existing protected controller
`24c2a03a8e7d405e0b8890e61e69bedc03f0cfac`. The manifest independently binds
that source and both successful cases; the controller commit is not a runtime
Main pin or adoption result.

The retained JUnit summaries show 47 successful scans per six-second case.
Quiet observes one owned process, with query-pipeline total/max 964/90 ms
and session creation 104 ms. Churn observes 34 owned process incarnations,
with query-pipeline total/max 1010/72 ms and session creation 101 ms.
Both record zero scan errors, cooperative stop, an armed window, no
truncation and closed CIM sessions. The contained helper independently reaps
its children. This disproves neither a rare provider failure nor a
construction-pressure effect; it does not repair or waive the original
full-suite census failure.

The legitimate diagnostic push also restores current PR-head metadata, but
Dev has moved and the PR is conflicting. A backup preserves `7fe797` before
rebasing onto `f864f03fc0219a90e7756b371f8f5c950d50f2d5`. All 33 owned
commits remain. The sole conflict is MCP's generated uninstall-profile
producer hashes: the canonical refresh preserves upstream's newer source
binding and the contributed script binding, with revision 36 and contract
semantics unchanged. Range-diff confirms no other owned patch changes.
The frozen hosted result remains evidence for `7fe797`, not the rebased tip.

All 33 focused MCP bootstrap, readiness and provider-selection controls pass
after reconciliation (160.16 seconds including environment preparation and
cleanup). All contributed Python files pass F/E9 lint; strict runtime
resolution, all 13 install contracts, documentation consistency and whitespace
also pass. The original 13 census controls have an unchanged range-diff patch.
The profile helper parses, but only real hosted execution can qualify the new
burst case. Existing Manager patch changefiles cover this PR's fixture work;
no version, runtime CLI or service contract changes.

The next diagnostic selects only a new six-second eight-child CPU/churn burst
through that same opt-in profile and existing owner. It changes the measured
load dimension without an unchanged full-suite retry, host runtime effects,
new fixture family, query/record-cap change or new workflow. Required
current-head CI, strict complete fresh acceptance, admitted-baseline upgrade,
frozen enforcement and separate Main adoption remain open.
