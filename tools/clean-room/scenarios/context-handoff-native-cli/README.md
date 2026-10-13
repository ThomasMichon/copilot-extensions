# Native continuity: real CLI witness

This opt-in Tier-E scenario uses a real interactive CLI under the rig's documented
tmux lane, **not ACP**. `scenario.sh` establishes only a deterministic fresh venue.
Its green setup report is not an eval verdict. The coordinator supplies the
rig-compatible judge packet to the judge; the controller never judges.

Run `run.ps1 -Mode run -Scenario <this-directory> -NameSuffix <unique-suffix>`
from the rig. Results use the rig-owned out-of-repository directory. Setup fetches
released `main` and fails closed unless context-handoff is `0.5.12-dev1` and
`native-context.mjs` has Git blob
`9e733a6cf4809c9985238bc552722d7a936da3d7`. This pinned witness must not silently
substitute a newer or unpublished payload.

Inside that disposable container, run
`python3 /home/operator/scenario/controller.py launch`. This starts an idle CLI
with a new UUID, no model turn, default pressure thresholds and native context
enabled. `controller.py send /model` opens the real offered-model picker;
`capture offered-models` saves it. Select an actually offered shorter-context
model using `/model <id>`, then capture `/env`. Read the setting with
`copilot config contextManagementTools`; `/settings <key>` opens an interactive
editor and is not a safe read-only probe. No operator-host setting is changed.

Before any driven session, setup creates the complete corpus and captures task,
AGENTS, task contract, ledger and pressure config into
`/opt/copilot-cleanroom/native-cli-baseline`. The setup-only capture command runs
as root; capsule directories/files are read-only and are outside both the driven
repository and its writable home/results directories. Ordinary worker edits
cannot redefine expected values. The scenario mount supplies the read-only
validator; no workspace-local validator is authoritative.

After all provisioning and seed capture, root setup seals **only this disposable
venue**: it verifies the known base-image operator grant, removes that
passwordless grant, installs a deny-all operator sudo rule, removes sudo's
setuid/setgid elevation bits and validates the policy before writing the
root-owned readiness marker. Thus an already-running unrestricted same-UID
tmux server cannot regain root via the inherited passwordless-sudo route.
The shared base image and the host's sudo policy are not modified.

All scenario tmux operations use the dedicated
`/home/operator/native-cli-witness.sock`, never the default server. Cold server
startup itself runs through `setpriv --no-new-privs`, not merely the CLI pane;
launch rejects an existing socket and checks the actual server PID's
`/proc/<pid>/status` for `NoNewPrivs: 1` before proceeding. New panes/windows
therefore inherit NNP even when requested by an unrestricted client. The CLI
pane additionally retains NNP. Missing seal/server evidence stops launch.

The boundary covers ordinary worker and same-UID server-mediated sudo escalation
inside the fresh venue. Trusted root Docker-exec/control-plane authorization,
privileged pre-existing services, kernel compromise and privileged alteration of
the capsule/host/source are outside this fixture boundary. Root control-plane
Docker exec remains available for inspection and cleanup.

For the control plane's separately authorized **unpaid OS proof**, create an
unrestricted same-UID server *before* sealing in an exclusively owned fresh
venue, then verify both mediated routes after setup:

```text
docker exec <venue> tmux -L native-preseal-probe new-session -d -s probe "sleep 600"
# Run this scenario's setup/seed/seal; do not submit a Copilot prompt.
docker exec <venue> python3 -c "import sys; sys.path.insert(0, '/home/operator/scenario'); import controller; controller.tmux('new-session', '-d', '-s', controller.TMUX, 'sleep 600'); controller.verify_tmux_server()"
docker exec <venue> tmux -S /home/operator/native-cli-witness.sock new-window -t native-cli-witness "cat /proc/self/status; sudo -n id -u"
docker exec <venue> tmux -L native-preseal-probe new-window -t probe "sudo -n id -u"
```

The unrestricted client-created pane on the dedicated server must show
`NoNewPrivs: 1`; the pre-seal unrestricted server must still fail sudo after
the venue seal. In both lanes attempt direct and sudo-mediated write/chmod/
rename of the capsule and trusted validator, compare capsule hashes before/
after, and confirm ordinary workspace edits remain possible. Capture exit
codes/output under the rig results directory and retire only the probe servers
and owned venue. These commands are proof instructions, not a claim that
source mocks establish the actual OS boundary or that a paid acceptance passed.

`launch` and `pressure` verify capsule integrity and unchanged seed inputs.
Pressure never regenerates the corpus or recaptures expectations. Live
checkpoint/report commands use the trusted scenario script and explicit capsule:

```
python3 /home/operator/scenario/validation.py checkpoint <workspace> /opt/copilot-cleanroom/native-cli-baseline
python3 /home/operator/scenario/validation.py witness <workspace> /opt/copilot-cleanroom/native-cli-baseline
```

Before the pressure run, send a literal-mode setup-measurement prompt asking for
`get_context_remaining`, actual offered native tool schemas and the released
context-handoff skill, then a synthetic `setup-measurement.json`. It must not
clear context or repair missing support. Require a measured prompt limit
materially below the reference coordinator limit, not merely the picker label.

`controller.py pressure` submits the bounded literal prompt over the already
captured corpus (also saved in `eval/acceptance-prompt.txt`).
It requests exactly two native transitions at 60-70% measured prompt utilization,
with unchanged policy caps and default plugin thresholds. The workload exercises
the soft boundary, not necessarily hard or force behavior. This is intentional:
the remaining boundaries must be reported unexercised, not inferred from absence
of a custom handoff.

The fixture's `audit-contract.md` defines structured synthetic task actions and
required checkpoint/report facts, not native tool sequencing. The unchanged
released skill still owns that flow. `validation.py` is a read-only validator:
the witness passes the actual native content read-back on stdin before each
clear. It never writes artifacts or repairs invalid checkpoints. First-obstacle
STOP applies to validation errors as well. `test_validation.py` covers warning
extraction, line offsets, missing witness fields and stale/compound actions.
`test_cli_diagnostics.py` covers invalid controller arguments, unavailable Linux
account lookup and stable malformed-corpus diagnostics. Setup discovers both
modules before any paid witness turn; run the same agent-free checks with
`python -m unittest discover -p 'test_*.py'` from this scenario directory.

The selected acceptance contract for new runs is **semantic**, not exact tool
choreography. After each clear the actual bound checkpoint, AGENTS content and
owning targeted skill must be refreshed before substantive recorded task work.
Any valid read tool, including view or bash, and combined sequential prerequisite
reads are permitted. Task/contract reads may precede the atomic task action.
Native measurement must evidence each recovered window, without an artificial
position/count. Checkpoints retain `recovery_prerequisites` separately from their
one successor TASK action; a rollover is never that task action.
`validation.py recovery-events <events.jsonl> <workspace> <trusted-seed-baseline>` validates successful
returned content attributable to actual read operands and the expected file bytes,
owning skill delivery, recovered-window measurement and work-before-refresh.
It rejects failed/skipped/bogus refresh or incorrect task content and never
mutates the session. `semantic_recovery_validation` is event evidence, not a
Tier-E verdict or permission to self-heal.

Recorded task reads require a nonempty string path and a genuine-integer,
positive, ordered range within the source file. Explicit view ranges must match
the recorded range; returned task content must equal the bounded slice, not
merely contain it inside an entire-file read. Guidance refresh still permits
combined sequential reads and does not impose a fixed tool count or ordering
beyond completed refresh before task work. Transition counts must be genuine
integers 1 or 2; boolean and floating-point aliases are invalid.
Shell read credit is limited to literal `&&` chains of `cd`, single-file `cat`
and simple bounded-print `sed -n` commands. Every output-producing command must
be a validated reader; a filename operand elsewhere in the command cannot
attribute bytes produced by `printf`, another producer, a pipeline, redirection
or substitution. Combined sequential guidance/task reads remain supported.
Cursor record, severity and next-line values must also be genuine integers.
Checkpoint documents use only `transition_number`; witness documents use only
`transitions_completed`. Conflicting or wrong-kind counter fields are invalid.
Every retained trigger measurement must satisfy the same captured-field
validation as the real native result, including tokens-left and current window
identity; abbreviated limit/fraction/number summaries are not full measurements.

Evidence-integrity checks are conservative. A witness-write claim must identify
the **first** substantive action's actual destination, not a quoted filename or
a later write. Native create/edit paths, literal shell redirections and
option-free two-operand `cp` commands are supported; dynamic destinations, conditional/pipeline writes
and unsupported writer forms are explicitly uncredited rather than guessed.
The actual synthetic task and prompt declare
`unsupported_write_mechanisms: ["apply_patch"]`: the driven CLI's patch
destination schema has not been established from captured evidence, so no
patch grammar or filename mention is guessed as attribution. For the recorded
witness action, select a supported native create/edit path or literal shell
writer. A patch attempt remains substantive invalid work, not an ignored action.
Potential writes through `tee`, other mutators, unknown commands/interpreters or
unparseable shell input remain substantive invalid work even when their
destinations cannot be proven. They cannot disappear from refresh-before-work
checks or be replaced by a later supported action.
File redirection on stderr is also substantive; descriptor duplication is not
proof of a witness destination.
Only the simple `sed -n '<positive-line-range>p' <source>` form is exempted as
read-only; other scripts, shell substitutions and unsupported commands remain
potential mutations.
Checkpoint read-back requires successful root-owned write/read calls, the same
checkpoint path, matching captured owner/memory-scope/revision/source-window
metadata, and the read's history position bound to the write event. Missing,
failed, stale or mismatched evidence cannot earn a positive read-back.

Analyzer failures remain evidence: unsuccessful or malformed native tool
results are retained as structured error observations, excluded from affirmative
measurement/artifact lists, and still produce an observed-evidence/judge packet.
Optional execution/telemetry objects may be absent; explicit null/nonobject
metadata or invalid exit-code values are structured negative evidence, never
success or an analyzer crash. Present exit codes must be genuine integers;
floating-point and boolean aliases are not normalized into success.
Export likewise records missing, unreadable, empty or malformed event streams
in `export-errors.json`, keeps valid rows only as explicitly diagnostic/partial
evidence, and writes a judge packet with `INVALID_EXPORT` status. Its CLI exits
nonzero after preserving diagnostics; it never invents an empty valid run.
Event row validation is shared by export, analysis and the direct
`recovery-events` CLI. Invalid raw streams return structured observations and
nonzero status, without crediting partial rows. Checkpoint/witness stdin must
be a JSON object; malformed JSON or other JSON shapes produce structured
failure results rather than tracebacks. Seal setup refuses a missing or
unreadable known sudo grant with an actionable error before any policy/binary
mutation. Validator APIs have no task override argument: only the trusted seed
defines expected task values.
Event rows must contain nonempty id/type/time strings and an explicit data
object; tool identifiers/argument objects and consumed message/delivery fields
are validated before downstream access. Incomplete rows become line-specific
diagnostics, not invented defaults. Analysis propagates export diagnostics and
quarantines an unreliable exported event snapshot, including a pre-existing
destination left after a failed copy. Quarantined rows remain inspectable but
cannot supply root clears, tools, measurements or acceptance evidence; valid
captured snapshots retain their normal path.
The same quarantine applies to **any** event-parser error in direct analysis,
even without an export receipt. Partial valid rows remain inspection-only;
the unchanged raw stream and row diagnostics preserve invalid rows, and no
clear/measurement/artifact or semantic-recovery credit is computed from it.
Process substitutions (`<(...)` and `>(...)`) remain potential substantive
mutations, including nested writers hidden behind an otherwise read-only command.
One shared root-ownership predicate applies to clears and supporting calls;
child-agent activity is quarantined under `non_root_activity` and cannot supply
root windows, measurements, artifacts or refresh. Source regressions cover each
of these cases without invoking Copilot or regrading recorded paid runs.

All four recorded independent packets have final FAIL verdicts and remain
immutable. The semantic scope applied only to its fresh run, not as retroactive
regrading of the exact-five-tools/isolated-view attempt. Recorded native clears,
stable ownership and first semantic recovery are real proofs, but later
worker-authored checkpoint/report deficits prevent end-to-end acceptance;
no native plugin/storage defect was established. See the canonical effort's
final evidence table for the precise boundaries. Passing source tests or a green
setup report must not be presented as a passing paid witness.

When the driven turn ends, run `controller.py export`, then `analyze`.
`eval/judge-packet.json`, `observed-evidence.json`, actual session events, the
transcript, literal fixture, source fingerprints, workspace outputs and setup
report are the judge's inputs. Intermediate checkpoint revisions survive in
tool events even when the witness subsequently updates the latest artifact.
Read `judge_attention` literally; it identifies evidence gaps, not a verdict.
Export includes a hash-verified archive of the protected capsule and provenance.
Analysis uses the capsule's original task/guidance/corpus/contract and identity,
not the agent-mutable post-run workspace or driver report. Workspace changes
are negative observations. A missing capsule, including an older record made
without seed capture, is explicitly uncredited; there is no mutable fallback
or reconstruction/regrading of historical raw packets.
Warning extraction includes `session.warning`; native preparation notifications
must not be dropped or confused with agent-facing idle nudges or force behavior.
Preserve a prior failed run's report, judge verdict and evidence unchanged; a
rerun always uses a new isolated container/results directory.

Exit the CLI with `/exit` and remove only this run's exact container after
capturing the terminal events. Do not register a host bridge provider, mutate
operator queues/claims/settings, create a forge repository, or publish from this
scenario. Do not credit mock SDK tests or an ACP run as real CLI continuity.

## Unpaid programmatic OS permission smoke

`os_permission_smoke.py` is a separate one-shot root orchestration for an
exclusively owned, fresh Linux container with this scenario mounted read-only
at `/home/operator/scenario`. It requires `tmux`, `setpriv`, `sudo`, `visudo`
and Python; it stops if any are missing and does not install them. The parent
may provision tmux **only in that ephemeral venue** before execution. No Copilot,
authentication, plugin installation or paid/eval session is launched.

```text
docker exec --user root <owned-venue> python3 /home/operator/scenario/os_permission_smoke.py --results /home/operator/out/os-permission-smoke
```

The script creates a real unrestricted default tmux server before calling the
same `capture_baseline` and `seal_disposable_venue` functions used by setup.
It then probes direct NNP execution, an NNP client's new window on that old
unrestricted server, and an unrestricted client's new window on the actual
dedicated NNP server created through `controller.tmux`. Probe process flags
must respectively be 1, 0 and 1. All lanes attempt ordinary and sudo-mediated
write/chmod/rename of the capsule and write to the trusted validator; all must
be denied. Capsule and validator hashes must remain unchanged, while ordinary
workspace task changes must succeed and be reported against the seed.

Each probe has bounded command/file waits. Results include per-lane JSON and
`os-permission-report.json` under the specified rig artifact directory. Cleanup
retires only identity-matching servers created by the smoke. Capture binds PID,
all real/effective/saved/filesystem UIDs, kernel start ticks and boot ID, exact
socket/session, and socket owner/device/inode. Cleanup opens a pidfd, immediately
revalidates that full identity, and signals only the pinned process generation;
there is no generic/default/name-based kill or fallback when identity/pidfd
evidence is missing. A bounded wait confirms exit and cleanup refusals are
reported. These cleanup changes require the parent's real OS smoke rerun;
mocked source tests do not prove Linux pidfd behavior. It deliberately
does not restore sudo policy, remove the immutable capsule or reuse the venue;
the parent owns container retirement and any root Docker-exec inspection.
Source test success is not a claim that this OS smoke or native acceptance ran.
