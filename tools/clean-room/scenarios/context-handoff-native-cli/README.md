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

Before the pressure run, send a literal-mode setup-measurement prompt asking for
`get_context_remaining`, actual offered native tool schemas and the released
context-handoff skill, then a synthetic `setup-measurement.json`. It must not
clear context or repair missing support. Require a measured prompt limit
materially below the reference coordinator limit, not merely the picker label.

`controller.py pressure` generates deterministic synthetic audit records and
submits the bounded literal prompt (also saved in `eval/acceptance-prompt.txt`).
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
Setup runs these deterministic tests before any paid witness turn.

The selected acceptance contract for new runs is **semantic**, not exact tool
choreography. After each clear the actual bound checkpoint, AGENTS content and
owning targeted skill must be refreshed before substantive recorded task work.
Any valid read tool, including view or bash, and combined sequential prerequisite
reads are permitted. Task/contract reads may precede the atomic task action.
Native measurement must evidence each recovered window, without an artificial
position/count. Checkpoints retain `recovery_prerequisites` separately from their
one successor TASK action; a rollover is never that task action.
`validation.py recovery-events <events.jsonl> <workspace>` validates successful
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

Evidence-integrity checks are conservative. A witness-write claim must identify
the **first** substantive action's actual destination, not a quoted filename or
a later write. Native create/edit paths, literal shell redirections and
option-free two-operand `cp` commands are supported; dynamic destinations, conditional/pipeline writes
and unsupported writer forms are explicitly uncredited rather than guessed.
Potential writes through `tee`, other mutators, unknown commands/interpreters or
unparseable shell input remain substantive invalid work even when their
destinations cannot be proven. They cannot disappear from refresh-before-work
checks or be replaced by a later supported action.
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
success or an analyzer crash.
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
Warning extraction includes `session.warning`; native preparation notifications
must not be dropped or confused with agent-facing idle nudges or force behavior.
Preserve a prior failed run's report, judge verdict and evidence unchanged; a
rerun always uses a new isolated container/results directory.

Exit the CLI with `/exit` and remove only this run's exact container after
capturing the terminal events. Do not register a host bridge provider, mutate
operator queues/claims/settings, create a forge repository, or publish from this
scenario. Do not credit mock SDK tests or an ACP run as real CLI continuity.
