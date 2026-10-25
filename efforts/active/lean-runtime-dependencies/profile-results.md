# Profile Results and Remaining Acceptance

[Effort and completion gate](README.md).

## Delivered source changes

| Slice | Merged contribution | Result |
|-------|---------------------|--------|
| Worktrees UI closure | #6293, `cd7f0f939` | Remove unused Textual; declare required `packaging`; retain the actual Worktree Manager process/UI |
| Index primary/host roles | #6317, `a0869fbae` | Lightweight primary CLI; explicit owning host runtime for controller, workers and foreground commands; no duplicate primary server fallback |
| Legacy stdio MCP | #6658, `0976e89aa` | Explicit `[stdio-mcp]`; normal read CLI and hosted runtime do not acquire its SDK |
| Index statistics | #6686, `cbbef0837` | Project source/language fields and aggregate bounded batches; retain result shape and propagate count/partial-scan errors |
| Restricted SSH profile | #6735, `b54361f6f` | Persistent explicit base/provider-exec selection, legacy capability retention and self-contained immutable generations |
| Windows seed reduction | #6766, `5d08c74d7` | Signed stdlib copies omit pip/setuptools only when uv installs; ordinary-pip fallback remains seeded |

Textual removal and the primary/host boundary are confirmed in released `main`
snapshot `f327cf447` from #6642. The actual released Windows/POSIX installers
select the base primary, provision the required `[store,server]` sibling and
refuse failed host provisioning; the released CLI carries explicit host
dispatch. This is released-tree inspection, not an ancestry check against
regenerated `main`. At the latest release observation (2026-10-14), that remains
the selected `main` snapshot; later source merges do not establish their own
release or running-state attribution. The existing #3336 / #4876 / #6697 release owners
retain promotion/publication follow-through. This campaign does not trigger a
duplicate release, retry or updater.

## Measured profiles, not a fleet reclaim estimate

All MiB use 2^20 bytes. Counts and bytes describe their recorded source snapshot,
interpreter and seeding, not every installation of the package.

| Observation | Distribution / footprint evidence | Interpretation |
|-------------|------------------------------------|----------------|
| Historical worktrees UI stack | At least 8.6 MiB logical installed UI files per observed environment | Removal avoids a real unused closure; retained historical slots are not deleted |
| Fresh index client | 12 distributions / 2.46 MiB logical external files; no store/server/model closure | Lightweight client role; MCP opt-in was separately proved without NumPy or FastAPI |
| Native index host sibling | 59 distributions / 508.85 MiB logical external files in the measured seeded profile | Hosted work still has a real large dependency stack; this is not a second client install or a physical-allocation estimate |
| Signed seeded Containers base / provider-exec | 11 / 18 distributions; 25,165,401 / 38,810,561 logical RECORD bytes | Matched profile delta is seven distributions / 13,645,160 logical bytes (13.01 MiB) |
| Signed unseeded Containers base / provider-exec | 9 / 16 distributions; 2,265,172 / 15,903,843 logical RECORD bytes | Both actual CLIs work with no pip/setuptools when uv owns installation |
| Same signed unseeded runtime allocation | 3,248,664 / 20,141,176 file-stream bytes | Fresh runtime trees only; every copied `python.exe` and `pythonw.exe` signature is valid |
| Actual no-uv base fallback | 11 distributions; 27,596,323 logical RECORD / 31,768,600 allocated runtime bytes | Real CLI and `python -m pip` work; fallback is not silently made unseeded |
| Five host syntax grammars | 5,390,318 logical / 5,639,720 allocated bytes; 1.53-second install phase | Retain host defaults at about one percent of the measured host closure; no parser-quality claim |
| Immutable helper artifacts | Two-runtime union 19,721,512 allocated bytes; 95 shared file identities / 1,357,032 bytes | [Bounded experiment](helper-closure-evidence.md), not production artifact adoption or an end-to-end speedup |

The corrected matched seeded runtime trees allocate 29,044,088 / 45,901,048
bytes. The unseeded base is roughly 3.10 MiB rather than 27.70 MiB in that
fresh seeded observation, without substituting an unsigned interpreter.
The no-uv fallback is a different construction path and is not used to claim a
matched uv timing or footprint delta.

Compressed wheel-download observations remain in the [audit](audit.md); they
are not added to unpacked package bytes. Hardlink-aware allocation excludes
directory/MFT metadata, shared base interpreters, production retained slots,
model caches and corpus state. The original 924.7 MiB / 32-environment census
includes historical versions and shared identities; its 600.1 MiB pip/setuptools
component is **not** a promise of reclaimable physical storage.
Earlier editable-source counts and the artifact lane that dropped `metrics`
are superseded, not additional measurements to average into these rows.

## Validation coverage and limits

| Slice | Changed-code and real-boundary evidence | Platform / live limit |
|-------|----------------------------------------|-----------------------|
| UI closure | 144 focused tests; actual CLI/Manager handoff with UI imports refused; fresh environment excludes Textual/Rich | Windows local and required Linux CI; no production updater or native macOS lane claimed |
| Primary/host boundary | 789 contained Windows tests plus 43 focused checks; real fresh minimal client and actual PowerShell sibling construction without activation | Required Linux CI passed after correcting POSIX fixtures; service activation and Darwin remain #5768 gates |
| Optional MCP | 857 contained Windows tests; actual SDK initialize/list-tools exchange, all five tools, explicit SDK-absent refusal | Final Linux/Windows CI passed; no production endpoint or model/corpus touched |
| Bounded statistics | 858 contained Windows tests; 16 focused checks; real 2,502-row LanceDB fixture beyond the default query limit, no pandas materialization | Actual Linux index CI passed; no schema change, model load or live service test needed for this read-only query delta |
| SSH profiles and Windows seeding | Final 781 contained Windows tests; actual first-touch base/provider and no-uv fallback, retained provider selection, base rollback and valid copied signatures | Required current-head CI passed before each merge; no macOS execution or deployed service claimed |
| Helper candidate | Actual two-consumer separate CLI processes, full closure/extra parity and surviving second consumer after first uninstall/cache retirement; retained allocation and optimized-Python refusal controls | Synthetic routing fixtures are not auth isolation, live cutover, concurrency or two-marketplace proof |

The isolated native fresh-root installs establish the selected constructor
contracts; they are not described as full fresh-machine VM tests. A Docker
daemon/full disposable Linux rig and native macOS host were unavailable locally.
Linux evidence comes from the actual required PR lanes, not Git Bash.
No new standalone macOS adapter is admitted by this effort: #5768 explicitly
retains the legacy native path until Darwin supervision/staging parity passes.
New engine and shared-artifact mechanisms remain unenabled pending their owners'
platform/concurrency acceptance. The separate #6598 construction change has its
own native macOS lane and must not be treated as evidence for this campaign
before its exact relevant checks pass.

## Installation latency audit

Dependency footprint is not the installation-time acceptance criterion.
A 2026-10-25 read-only audit of twenty retained update histories from one
Windows adopter found successful runs taking roughly 28-53 minutes.
Runtime reconciliation alone took 13-34 minutes in several runs; individual
Codespaces package-install spans took 96-198 seconds. Two later Worktrees
attempts reached their roughly ten-minute engine-build deadline. Payload
refresh also consumed minutes, and a newer failure occurred before any
runtime installer ran, after a marketplace-cache sharing violation and timeout.
These are distinct bottlenecks; increasing an installer timeout does not fix
them.

A disposable, host-isolated comparison used the same signed CPython base,
stdlib `EnvBuilder`, copied interpreters and canonical child ownership:

| Construction | Observed wall time | Inner-phase evidence |
|--------------|--------------------|----------------------|
| Unseeded copied venv | 2.760 seconds | Interpreter copy: 0.126 s; script setup: 0.731 s; stdlib post-setup: 0 s |
| Seeded copied venv | Timed out at 45.059 seconds | Interpreter copy finished in 0.043 s; the process remained in `ensurepip` |

The seeded control was terminated with all owned descendants settled.
This isolates unnecessary seeding as a real cost; it is not a total-update
speedup measurement or evidence about service-specific post-copy hooks.
The post-package Bridge spans included 4.4-8.7 seconds of warm-up and
38.3-60.9 seconds of cutover/drain. Dispatch's coordinator handoff took
20.6-33.5 seconds, followed by separate supervisor retirement/registration.
Those costs are not simply file copying; this slice removes no health,
drain or re-adoption checks.
Several UV-owned installers still paid that cost, while Codespaces separately
ran nine retained compiler/install transactions for its nine local roots.
The targeted source changes opt those known UV callers out of seeding and
batch the Codespaces closure without changing dependency, signature, health
or service authority. Whole-update cold/warm timing and installed-state
qualification remain required after authorized rollout.

### Logger transaction consolidation

Logger's existing construction used ten governed UV invocations: separate
setuptools and PyYAML bootstrap installs, seven helper installs, and the plugin
install. The follow-on source change uses two: the same prerequisites together,
then all eight explicit local roots in one retained non-editable transaction.
Every root retains its named reinstall selection; no dependency is removed.
Reuse, source attribution, signature admission, build isolation, seasoning,
retry and activation gates remain in their existing owners.

The disposable Windows native fixture verifies both canonical editable
references and materialized payloads, two calls per construction, all ten
installed distributions including prerequisites, and a same-version helper edit
on the warm run. Its private loopback PEP 691 index supplies explicit old upload
times and hashes; ambient UV/PIP settings are removed, not allowed to redirect
the test to a different feed. A separate offline full-provision case exercises
the actual Logger package, helper closure, runtime health and publication flow.
These are correctness/work-count observations, not a whole-update timing result.
Cold and unchanged full-update qualification remains open with authorized rollout.

## Unresolved custody, not completion by renaming

| Remaining requirement | Existing objective and boundary |
|-----------------------|---------------------------------|
| Exact CPU/accelerator engine closures and removal of torch-only install-then-swap | #6737 applies the authority-approved contract; #4876 owns exact profile/lock selection and allowed feed identity; #6598 owns seasoning and held construction |
| Native/standalone activation, durable queue/schema rollback, canary and warm engine, Darwin supervision | #5768's existing Plan/Validation; #5873 retains its installer-adopter rollout, currently paused by its operator |
| Immutable helper consumer fast path, independent install/update/uninstall/rollback and two-marketplace concurrency | #4876's existing admission/consumer/retention phase; #6697 retains publication readiness; no parallel broker/runtime owner |
| Conditional daemon lifecycle adoption | #6143 retains bounded owner-led cutover; the helper experiment changes no route, daemon or retirement authority |
| Later source promotion/publication and actual authorized installed-state proof | Existing release owners and the designated deployment owner; no production rollout is authorized in this campaign |

Coordination requests name these concrete gates. They do not manufacture an
acceptance receipt, mark the parent effort Done, or clear its open worktree
binding. The remaining Plan/Validation items stay open until their source work
is complete or an explicit reviewed transfer to the named objective is accepted.
