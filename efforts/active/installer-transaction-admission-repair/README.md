# Installer Transaction Admission Repair

- **Slug:** `installer-transaction-admission-repair`
- **Repo:** copilot-extensions
- **Branch(es):** one corrective PR against `dev`
- **Created:** 2026-10-13
- **Status:** Done
- **Vision:** below-altitude correction to the existing exclusive install-transaction contract
- **Umbrella issue:** #6463
- **Parent effort:** [CI Reliability & Flakiness Telemetry](../ci-flakiness-telemetry-and-reliability/README.md), Phase 3a

## Guiding Intent

Repair the POSIX PID-fallback admission race in the four matching agent-mcp,
agent-containers, agent-vault and agent-ssh installers. Their transactions must
remain exclusive during fresh admission and stale-owner recovery; empty owner
reads never authorize deletion of another holder's lock.

This effort owns only that repair, its regression evidence, related contract
documentation, patch changefiles and review/landing/released-source proof.
Telemetry campaigns, native continuity acceptance, other lock families and
host deployment are separate objectives.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Correctness worker | Local repair, targeted evidence and documentation | Dedicated corrective worktree; no publication authority |
| Integration coordinator | Independent review, publication, required CI and release proof | Normal repository PR and promotion flow |

## Coordination

- **Topology:** one bounded corrective worktree/PR; #6463 is the public claim.
- **Host (owns PRs):** Integration coordinator.
- **Delegates:** Correctness worker owns local changes; no child agents.
- **Handoff:** validated local changes return to the coordinator before commit
  or publication. The legacy-target raw-byte follow-up is locally validated
  and awaits independent review; integration retains publication authority.
- **Binding replacement:** explicitly replace the worker's broad Phase 3a
  binding with this canonical effort, participant `Correctness worker`, slice
  `Repair and validation`. The parent remains Active and retains all telemetry
  work; replacing the binding neither completes nor abandons the parent.
- **Completion gate:** all Plan and Validation Plan items must be resolved,
  including clean current-head review, actual required CI, merge and proof that
  the released source contains all four repairs. A completed local phase or an
  unrelated green run is not completion.
- **Rollout boundary:** no host installation, runtime update or deployment is
  authorized by this slice. Any adopter rollout remains with its standing,
  explicitly authorized maintenance owner/workflow; it is not silently
  undertaken or claimed complete here.

## Context

#6483 introduced the matching install-transaction blocks. Required CI exposed
the stale-recovery overlap; a controlled actual-script regression reproduced
`start start end end` after successful subprocess exits. A reaper-only mutex
does not prevent an acquirer from replacing an absent link between an empty
comparison and unlink.

The existing `.reap` primitive now guards both admission and stale cleanup.
Flock, positive timeout configuration, owner-checked EXIT cleanup and explicit
repair of a dead admission mutex are preserved. The normative behavior is in
[`docs/install-contract.md`](../../../docs/install-contract.md).

## Request

Public-safe continuing objective under the repository's required-CI repair
policy: fix the reproduced admission race across all four matching installers,
then independently review, publish, merge and prove the released source.
Keep this bounded repair separate from the broader telemetry objective and
native continuity acceptance; neither parent is completed by this repair.

The objective-binding correction changed documentation only while the
independent reviewer inspected unchanged code. Integration now owns commit,
review and publication. Host rollout remains outside this authorized slice.

The named-residual follow-up requires exclusive creation at the exact pathname,
without following destination directories, for both owner and admission paths.
Directory-shaped nodes must remain untouched. Check-then-create is insufficient
when the shared namespace can change; the primitive must preserve the documented
portable contract rather than silently requiring GNU-only flags.

The corrective worker's implementation scope is the same four protocols,
their tests, directly related docs and patch change intent. Preserve the earlier
overlap/stress evidence, dead-mutex fail-closed behavior, timeouts, traps and
flock. Real consumers must refuse directory-shaped nodes at both shared paths,
including a controlled late directory insertion, without altering malformed
nodes or outside sentinels. No worker Git publication, child agents, paid trials
or host changes are authorized; integration retains final review and publication.

## Plan

### Repair and validation

- [x] Reproduce the exact empty-owner overlap before changing the installer.
- [x] Repair all four matching POSIX admission blocks without widening installation policy.
- [x] Preserve flock, ordinary/stale admission, timeout, EXIT ownership and dead-mutex behavior.
- [x] Add deterministic and adverse regressions; run isolated real-script stress and fresh transactions.
- [x] Update the install contract and generate patch change intent for all four plugins.
- [x] Correct directory-shaped owner/admission nodes with atomic exact-path creation
  and validate the same four consumers without widening the task.
- [x] Reproduce the actual review's NUL corruption across all four consumers,
  then validate raw receipt bytes before any lossy Bash text parsing.
- [x] Add mirrored NUL, newline/byte-bound, typed-owner compatibility and EXIT
  regressions; repeat the complete targeted suites and real transaction stress.
- [x] Reproduce legacy trailing-newline target normalization, validate private
  raw `readlink` output before command substitution, and repeat all four
  consumers' malformed-target, compatibility, EXIT and byte-bound regressions.

### Integration and released-source proof

- [x] Independently verify the empty-read correction and exact-path/type-safety residual.
- [x] Obtain independent clean current-head review and resolve any blocking findings.
- [x] Publish through the repository flow; confirm actual required CI passes for the repair head.
- [x] Merge the repair into `dev` and record the merged result.
- [x] Confirm promotion/release validation and compare the released four installer bodies
  with the reviewed repair, recording the release identity and source fingerprints.
- [x] Resolve every Plan/Validation Plan item and mark this bounded effort Done;
  keep the broad parent Active for its remaining telemetry objectives.

## Validation Plan

- [x] Before-fix actual-script regression fails with overlapping starts and successful children.
- [x] After-fix suites cover deterministic overlap, fresh/stale contention, malformed owners,
  dead admission mutexes, timeouts, release failures and EXIT ownership.
- [x] Four current targeted suites: 316 passed; 12 unchanged PowerShell behavior tests skipped
  because the isolated Linux image has no PowerShell.
- [x] Real-script stress: 24 six-child fresh/stale schedules, 144 serialized transactions.
- [x] Ruff F/E9, four shell syntax checks, install contract (13 plugins), module guard
  and diff whitespace checks pass; no module baseline widened.
- [x] Named residual: directories, directory symlinks and numeric directory symlinks
  at both paths are refused, preserved and never populated with child locks.
- [x] Controlled creation-race and typed-receipt snapshot/FIFO tests pass; prior
  overlap, ordinary/stale/flock, timeout, exit-ownership and stress evidence is revalidated.
- [x] NUL at six prefix/body/newline-sensitive positions is refused at both
  shared paths, preserving bytes and inode identity without admission or stale
  deletion; NUL-corrupted current-owner receipts also survive EXIT cleanup.
- [x] Positive regular live/dead owners, legacy dangling PID symlinks, exact
  64-byte receipts and strict newline framing pass in all four matching consumers.
- [x] Legacy targets with leading/internal/trailing newline or CR, multiple
  trailing newlines, overlong targets, malformed utility output and utility
  failure are refused with original target bytes/inodes preserved. Malformed
  current-owner legacy links survive EXIT cleanup; digit-only legacy links
  remain compatible within the bound.
- [x] Independent coordinator validation and required CI confirm the final reviewed repair head.
- [x] Released-source proof confirms every repaired installer, not merely an unrelated green run.

## Proposal

This records an already-authorized bounded correctness slice, not a new
architecture or policy campaign. Review and publication remain coordinator-owned.
The legacy-target follow-up changes only the four matching readers' legacy
branches, their mirrored tests, the install contract and this effort. The
published typed-receipt NUL validation is unchanged. Existing four-plugin patch
change intent and version metadata remain unchanged.

## Journal

### 2026-10-21 - Actual review, merge and four-installer release proof reconciled

- Completion-only reconciliation under #7556 credits the actual final #6551,
  rather than repeating the historical local/unpublished states below.
  Head `7b619967dc42c0c25825b3d50827b89dc2240e44` received an actual Copilot
  **Approved** review at `2026-10-18T11:03:47Z`, with no open findings.
  Actual required PR gate, guards, four repaired plugin jobs and native
  Windows contracts passed in run `39121148737`. Skipped full-matrix jobs
  are not represented as executed acceptance.
- #6551 merged into `dev` as
  `938fc9aa1abe1883374748e6b274aa7cfcf01f3f`. The earlier independent
  actual-consumer, raw-byte and stress evidence remains attributed to its
  recorded revisions; no tests or installer were rerun for this reconciliation.
- Actual Main release #7780 is
  `b8fec3dc71f786c6dc92e21630e25c9a43be41e2`, from qualified Dev source
  `c8c0b3060454d812e7ac42ce7b7c04ea6375f6a1`. Static Git-blob comparison
  shows all four complete transaction protocols, from the install-transaction
  start marker through its end marker, equal the reviewed #6551 repair
  byte-for-byte. Each is 7,586 bytes.
- MCP, Containers and Vault protocol SHA-256 is
  `22851fbf7e7de22ff5b544304add11f30f04e85d0462a1438c94995ba194081a`;
  SSH is
  `172cef62e099787081a0e97d2f0ff6e95ab72230e56b9891e0e05bb132b6cff1`.
  Released complete installer blobs, in the same order, are
  `641d6ddcc6f85807d78b6ba81c95a36ec2b844e2`,
  `c0c8d478d96bdf65db978e760455a7449d6baafd`,
  `afbed82563528625ef9d106b0d65eca93bad100e` and
  `22748b8bcc9357bf4506bcd5e1b53262ef5b2b94`.
  Later changes outside the compared protocol mean the complete files are
  not claimed identical to the older repair.
- Every Plan and Validation item is now resolved for this bounded repair.
  Status is **Done**, pending ordinary archive maintenance. #6463 also
  credits its original runtime-scope/native-lock repair #6483 and exact
  machine-gate guard #7011; it does not absorb machine-wide locking,
  uninstall/purge or every governed entrypoint.
- The broad telemetry parent remains **Active**. Native continuity,
  unavailable platform evidence and adopter rollout remain distinct; no
  host installer, runtime, service, credential or system effect was authorized
  or performed. This completion records source release, not deployment.

### 2026-10-14 - Legacy target raw-byte residual corrected locally

- The actual Balanced provider review `5514428890` on #6551 at
  `1c78ce53d1a4122c0b22e858f98e67760d9530f3` resolved the four prior NUL
  findings but named four additional Medium findings in its body: command
  substitution trimmed trailing newlines from legacy dangling PID targets.
  The body's findings remain a blocking observation despite the overview's
  zero-open-finding counter. This worker made no provider mutation.
- The permitted bounded API probe returned `linux`; the already-present base
  image retained the recorded identity. No Docker startup, diagnosis, restart,
  WSL operation, image update or host remediation was attempted.
- Before changing source, **16 of 16 actual Bash negatives** reproduced across
  the four scripts: digit targets with one or two trailing newlines were
  normalized to a dead PID, stale-deleted and followed by successful admission;
  malformed current-PID targets were also deleted by EXIT cleanup.
  An initial pytest-importing reproduction exhausted its 120-second bound and
  is not counted as evidence; the direct standard-library reproduction then
  completed within its separate 90-second bound.
- Only the legacy branches now capture standard `readlink` output in a private
  regular file. A bounded NUL-delimited byte read requires EOF, exactly one
  utility newline terminator and an otherwise nonempty digit-only target.
  Failed utility calls never publish owner evidence. Original links are
  untouched; the private capture is removed on acceptance and refusal.
  No GNU-only flag, new dependency, interpreter or lock protocol was added.
- All four **complete** transaction-lock modules passed: **316 passed,
  12 unchanged PowerShell skips**. The 108 new cases cover both shared paths
  with leading/internal/trailing newline and CR, multiple newline and CRLF
  targets, original target/inode preservation and no admission; current-owner
  EXIT refusal; 63-digit acceptance and 64/1024-digit refusal; missing/extra
  utility terminators, NUL output and failure despite a numeric partial output.
  Existing digit-only legacy live/dead compatibility remains exercised.
- Successful full selections were agent-mcp plus agent-containers (**158 passed,
  6 skipped, 212.79 seconds**), agent-vault (**79 passed, 3 skipped, 91.76
  seconds**) and agent-ssh (**79 passed, 3 skipped, 89.21 seconds**). Each used
  the unchanged pytest arguments `-q --tb=short -c /dev/null --noconftest
  --import-mode=importlib -p no:cacheprovider -o
  'markers=guard: static installer guard'` through `pytest.main`, selecting
  each named plugin's `tests/test_install_transaction_lock.py`.
- Direct dependency import from the read-only host bind exhausted a 240-second
  run; copying that dependency tree through the bind also exhausted 240 seconds.
  Neither is counted as a pass. The existing 515 dependency files were instead
  transported as one read-only archive and unpacked solely into each disposable
  container's tmpfs before importing pytest. No package installation, version
  change, host runtime write or image provisioning occurred. The temporary
  transport archive is worker-owned cleanup, not persistent repository state.
- Every Linux invocation used the existing image with `--pull=never`, `--rm`,
  no network, read-only source/root, all capabilities dropped, 128 PIDs,
  512 MiB memory, a 128 MiB temporary filesystem and an explicit timeout.
  Successful suites retained the 240-second bound; no credential or Docker
  socket was mounted.
- Four actual `bash -n` checks passed. The four complete owner readers are
  byte-identical, with SHA-256
  `136b9d5b3a4bd063e0a51bd6b661b5afcf033abf07fc09f791768d8d6d5b1acb`.
  A direct comparison with the published head confirmed that all four regular
  typed-receipt validation paths remain byte-for-byte unchanged. All prior
  NUL, exact-path, FIFO, directory/race, flock, timeout and dead-mutex tests pass.
- Fresh/stale actual-script stress passed again: **24 schedules and 144
  serialized transactions**, with each transaction root completely empty
  afterward. Existing Ruff F/E9, the 13-plugin install-contract check, module
  guard and diff whitespace checks passed without baseline changes.
- **Documentation impact:** the authoritative install contract now states the
  legacy raw-output bound and exact terminator/digit checks. This effort records
  the material residual and current local proof. This is below-altitude
  enforcement of the established malformed-owner contract, not a new protocol
  or a vision change. The existing four-plugin patch changefile remains intact.
- **Validation tiers:** static/unit and actual Linux consumer integration passed,
  including fresh lock roots. Native BSD/macOS and PowerShell execution remain
  unproved. Full runtime provisioning, host installation, paid trials and other
  feature implementation were unauthorized and not attempted.
- Source is locally modified only, with no worker commit, rebase, push or PR
  write. Independent review of this material legacy correction, publication,
  current-head required CI, merge and released-source proof remain parent-owned
  and open. This bounded effort remains **Active**; previous NUL acceptance and
  published-head checks do not substitute for those gates.

### 2026-10-14 - Independent NUL correction verification closed the named findings

- Independent review reran the unchanged four suites in two bounded Linux
  selections: 208 passed and 12 unchanged PowerShell skips. Supplemental
  verification passed 300 byte-boundary reader checks, 48 actual admission
  checks and 16 EXIT checks, including every NUL insertion position, high
  bytes, newline framing, length limits and preserved malformed bytes/inodes.
- Typed live/dead receipts, legacy dangling links and existing directory/FIFO
  guards remain valid. The four readers are byte-identical; no significant
  residual was found within the named corruption scope.
- Initial launcher errors, a combined supplemental timeout and a one-second
  positive-acquisition timeout are not counted as passing evidence. Corrected
  bounded selections and the five-second positive rerun passed. All owned
  containers were removed; no source or host settings changed during review.
- Current-head provider review, required CI, merge and released-source proof
  remain outstanding. Native BSD/macOS and PowerShell behavioral validation
  remain unproved, and no adopter rollout is authorized by this repair.

### 2026-10-14 - Linux lane restored; NUL correction locally validated

- At the next authorized read-only startup diagnosis, Desktop reported
  `running`. The actual Docker API returned `linux`, and the existing
  `copilot-cleanroom:base` image was verified as
  `sha256:428d6339a0a18126af5fe35805583212e6b18cb3d7ee4023323fe672aef11656`.
  Startup logs now reported the Linux daemon ready. This worker issued no
  restart, PID termination, WSL termination, privileged service operation,
  data reset, software update or image provisioning. Earlier failed startup
  deadlines remain recorded below; a later healthy API does not erase them.
- Before changing the parser, actual Bash consumers reproduced **24 of 24**
  negatives: NUL at prefix start/body, PID start/body, immediately before the
  terminating newline and immediately after it was accepted as a dead PID,
  stale-unlinked and followed by successful transaction admission.
- The four byte-identical readers now use one `LC_ALL=C read -r -d '' -n 65`
  against the already type-checked private snapshot. A successful read means
  a NUL or the byte limit was reached and is rejected. Only EOF within the
  bound proceeds to exact newline/prefix/digit validation. There is no second
  read that can discard NUL or observe different bytes between validation and
  parsing. The primitive follows existing bounded NUL-read prior art in
  `plugins/ai-attribution/scripts/emit-policy.sh`; no GNU-only flag, interpreter
  or dependency was added.
- All four complete transaction-lock modules passed: **208 passed, 12 unchanged
  PowerShell skips**. The additional 80 cases cover six NUL positions at both
  shared paths, preserved bytes/inodes and no transaction entry, current-owner
  EXIT refusal, regular live/dead compatibility, the exact byte bound, and
  missing/extra newlines. Existing flock, legacy dangling links, timeout,
  empty-read overlap, directory/FIFO races and dead-mutex contracts still pass.
- The original four-module invocation exhausted its 240-second container
  budget; it is not counted as passing evidence. Without changing tests or
  increasing that budget, the same selection was divided into two complete
  two-module runs: agent-mcp plus agent-containers reported 104 passed and
  6 skipped in 125.04 seconds; agent-vault plus agent-ssh reported 104 passed
  and 6 skipped in 158.56 seconds. Both runs returned zero.
- The pytest command shape was `python3 -u -m pytest -q --tb=short -c /dev/null
  --noconftest --import-mode=importlib -p no:cacheprovider -o
  'markers=guard: static installer guard'`, selecting the named plugins'
  `tests/test_install_transaction_lock.py` files. Every Linux invocation used
  the existing image with `--pull=never`, `--rm`, no network, read-only source
  and root, dropped capabilities, 128 PIDs, 512 MiB memory, a 128 MiB temporary
  filesystem, and an explicit timeout; no credentials or Docker socket were
  mounted.
- Repeated actual-script stress passed **24 fresh/stale schedules and 144
  serialized transactions**, with each transaction root completely empty
  afterward. Four `bash -n` checks and byte-identical reader comparison passed.
- Existing Ruff `check --select F,E9` passed for the four changed tests.
  `python -B tools/check-install-contract.py` reported all 13 plugins conformant;
  `check-effort-vision-structure.py` accepted this canonical effort;
  `check-module-size.py` passed without baseline changes; `git diff --check`
  passed. An initial combined Linux static-check invocation exhausted its
  120-second budget; focused native static checks and the separate actual
  Linux shell/reader checks above subsequently completed successfully.
- **Documentation impact:** the authoritative install contract now states raw
  NUL refusal, exact newline framing and the byte bound. This effort records
  current evidence and gates. This is below-altitude enforcement of the existing
  malformed-record contract, not a vision or lifecycle redesign. The existing
  four-plugin patch changefile was verified and left intact.
- **Validation tiers:** unit/static and actual Linux consumer integration passed;
  fresh temporary roots exercised first-touch lock admission. Full runtime
  provisioning and host installation were not run because they are unchanged
  and unauthorized. No remote external venue was authorized. Native macOS/BSD
  and PowerShell execution are not claimed.
- Local source remains uncommitted and unpublished at the previously published
  repair head. Independent review of this material NUL correction, publication,
  current-head required CI, merge and released-source fingerprints remain
  integration-owned and open. The earlier green published-head CI does not
  validate these uncommitted changes. This bounded effort and its broader
  parent objectives remain **Active**; native continuity evidence and adopter
  rollout remain outside this slice.

### 2026-10-13 - Authorized Desktop startup did not restore the Linux lane

- The operator explicitly authorized starting the existing Docker Desktop
  application, without installation, updates, provisioning or other system
  changes. The existing GUI executable was launched using a window-suppressed
  process start; no service configuration or administrator operation was used.
- The actual Linux Docker API did not become ready within the 180-second
  startup deadline. A subsequent bounded 20-second API probe also timed out.
  `docker desktop status` reported `starting`; its backend state likewise
  reported Linux mode with Docker and Docker API still `starting`.
- Docker's startup log repeatedly reported the missing guest-services
  `socketforwarder-receive-fds.sock`, alongside an IPC request deadline.
  Desktop/backend processes were present, but process presence did not prove
  an available container engine. No additional restart, repair, service start,
  image change or privileged system mutation was attempted.
- The authorized startup therefore did not yet restore the validation lane.
  No NUL reproduction or consumer test ran, and installer/test sources remain
  unchanged. The effort remains **Active**, with the same NUL correction,
  independent review, required CI, merge and released-source gates open.
  The historical pre-startup blocker below is retained rather than erased.

### 2026-10-13 - NUL-record follow-up blocked before reproduction

- The current published repair is #6551 at
  `e35a4078110cb8bd6e90f9ff9e5bf6f9654cadc3`. Its previously reported full CI
  run `38437426233` and required PR gate passed, but those checks do not
  establish acceptance of a subsequent NUL-validation fix.
- The actual Copilot review comments `4261885824`, `4261885901`,
  `4261885985` and `4261886059` were read directly. All four identify one
  blocking defect: Bash text reads discard embedded NUL bytes, potentially
  accepting malformed typed PID receipts as stale-owner evidence.
- The authorized all-four-consumer, six-position pre-fix reproduction could
  not start: the local Docker Linux endpoint was unavailable. A read-only
  check of the alternate local Docker endpoint also failed; the Docker service
  was stopped and no Docker backend process was present. No consumer executed,
  so this continuation claims neither a reproduced negative nor a passing fix.
- Host runtime changes are outside this worker's authorization. No engine was
  started, no installer or test source was changed, and no Git publication,
  PR mutation, native continuity trial or host deployment occurred.
- The effort remains **Active**. Integration must restore the authorized
  disposable Linux validation lane before the ordered NUL reproduction,
  bounded parser correction and mirrored regression work can proceed.
  Independent review of that material correction, current-head required CI,
  merge and released-source proof remain open; earlier 128-pass and stress
  evidence applies only to the earlier repair. The parent objectives remain
  unchanged.

### 2026-10-13 - Independent local verification closed the named residual

- The coordinator reran all four final transaction-lock suites against unchanged
  source in a hardened, network-disabled Linux container: 128 passed and 12
  unchanged PowerShell skips. Install-contract and effort-structure gates pass.
- Independent actual consumers closed the directory-shaped creation residual
  with nine targeted checks: exact-path directory/symlink refusal, preserved
  nodes, late-directory and FIFO races, typed regular receipt lifecycle, legacy
  dangling-link compatibility, foreign-owner cleanup and dead-mutex refusal.
  The reviewer removed its owned container and found no residual in this scope.
- Provider review, required CI, merge and released-source proof remain open.
  This local verification neither completes the parent telemetry effort nor
  authorizes native continuity trials or host rollout.

### 2026-10-13 - Named directory-shaped admission residual corrected locally

- Before correction, all eight focused agent-mcp shape/race cases failed.
  Directory-shaped owner cases logged `start start end end` after both real
  Bash consumers returned zero; directory-shaped admission paths also acquired
  incorrectly or received unowned child links. A late directory inserted at
  the actual creation boundary reproduced the same class.
- All four matching blocks now publish typed regular PID receipts with the
  standard exact-path `link(1)` utility and privately staged source files.
  There is no shared-path `ln -s`, GNU-only `-T`, new runtime library or
  interpreter. Regular source receipts avoid macOS/BSD/Linux differences in
  linking a symbolic-link source. Legacy dangling PID symlinks remain readable;
  directory symlinks and malformed records are refused without deletion.
- Owner-file reads take private hard-link snapshots and inspect the captured
  node before opening it. The raced-FIFO regression returns promptly and leaves
  the malformed source untouched.
- The four complete targeted modules now report **128 passed, 12 unchanged
  PowerShell skips**. This includes two real consumers for each of the three
  directory shapes at both shared paths, exact-boundary insertion tests, and
  the prior empty-read overlap, ordinary/stale/flock, timeout, trap ownership,
  dead-mutex and release-failure contracts. Node identity, directory contents
  and outside sentinels remain unchanged in every malformed-path case.
- Repeated real-script stress on the corrected bodies passed again:
  **24 fresh/stale schedules and 144 serialized transactions**, with the
  transaction roots empty afterward, including temporary snapshot cleanup.
- Ruff F/E9, shell syntax, the 13-plugin install-contract check, module guard
  and diff whitespace checks pass. The existing four-plugin patch change
  intent and normative install-contract documentation describe the correction.
  Tests ran in bounded, network-disabled, read-only-source Linux containers;
  no host, image or deployment change occurred. Native macOS/BSD execution
  was unavailable and is not claimed; its documented POSIX primitive and
  regular-receipt requirements are recorded in the contract.
- The bounded effort remains **Active**. Independent named-residual verification,
  publication, actual required CI, merge and released-source proof are still
  integration-owned and unresolved. The broad telemetry parent was not changed
  or declared complete.

### 2026-10-13 - Local repair validated; bounded completion contract established

- The deterministic `test_posix_pid_fallback_empty_owner_interval_never_overlaps`
  failed before the source repair with `start start end end`, after its
  subprocess zero-exit checks. It passes for all four repaired scripts.
- Targeted pytest used real Bash in disposable, network-disabled Linux
  containers with read-only source/root and bounded process/memory/time
  resources. The four complete transaction-lock modules produced 72 passes and
  12 PowerShell skips. Independent stress reused `_sh_contender` on three fresh
  and three stale roots per plugin: 24 schedules and 144 successful transactions.
- **Unit/static:** targeted regressions, structural contracts and source gates.
  **Simple integration:** actual Bash children, controlled scheduling, held-lock
  stamp refusal and exit cleanup. **Clean-room:** fresh temporary transaction
  roots prove first-touch admission, not a complete runtime installation.
  Full provisioning was skipped because dependency/service lifecycle behavior
  is unchanged and outside this correction. **Live external venue:** no remote
  host or CodeSpace was authorized; the real Linux kernel lane was exercised
  locally. No host activation or image change occurred.
- Detailed evidence was moved from the parent's Phase 3a journal to this
  canonical bounded objective; the parent retains a truthful summary and link.
  The replacement is explicit and does not declare the parent Done.
- Local changes remain uncommitted/unpublished. Review, actual required CI,
  landing and released-source proof are outstanding. Host rollout remains
  unauthorized here and outside this effort's completion claim.
