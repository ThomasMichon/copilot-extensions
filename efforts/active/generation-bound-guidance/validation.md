# Source validation and remaining native acceptance

Parent contract: [Generation-Bound Guidance](README.md), #7943.

## Scope and source identity

The implementation owns static instruction preparation/reuse, its bounded
SessionStart backup and post-tool completion notice, and Bridge's local
prelaunch preparation seam. It does not change production bootstrap,
native launch scripts, optional maintenance, Manager loading or other owners'
APIs. No shared installation, service restart or deployment was performed.

- Pre-E source: `943e2ca139fd15e25003b6b4e863227afcabeaf1`.
- Preserved implementation: `0f43df6a4`.
- These are source-validation identities, not release/installed receipts.

## Completed checks

The unchanged initial focused baseline was **107 passed, 4 skipped**.
Seven newly added generation/partial-delivery regressions failed before the
implementation. Focused implementation validation subsequently completed:

| Worktrees group | Passed | Skipped |
|---|---:|---:|
| `test_local_cache_lifecycle.py` | 32 | 0 |
| `test_local_cache_refresh.py` | 104 | 4 |
| `test_local_cache_worker.py` | 17 | 0 |
| `test_nudge_status.py` | 10 | 0 |
| `test_static_instructions_standalone.py` | 6 | 6 |
| **Total** | **169** | **10** |

Bridge's `test_local_static_preparation.py` and `test_local_cache_refresh.py`
completed **65 passed, 3 skipped**. Skips are explicit platform/opt-in tiers,
not successful native qualification. Install-contract (13 plugins),
headless-launch, module-size and touched-Python lint checks passed.

The Worktrees runner used one selected file per existing bounded group; no
deadline was raised. An earlier six-file group reached the existing 300-second
group ceiling and was replaced by those smaller groups, not treated as passing.
The real native process fixtures cover first-touch publication without the
authoring renderer, reuse in a second process, and an old delayed publisher
losing to the current source selection. A real post-tool script receives the
late completion notice. Bridge's consumer-order test holds preparation before
allowing spawn; these are source-consumer proofs, not a live Copilot admission
or installed end-to-end launch.

## Matched publication-phase measurements

One synthetic provider, identical declared bytes and isolated empty homes.
Each of three paired trials performs one first touch and two unchanged calls.
The old publisher is loaded unchanged from the pre-E source capsule; the new
publisher uses the preserved candidate. Both use the same interpreter and
unchanged library environment. The measured boundary is `publish`, **not**
process entry, registration, first prompt, frame display or end-to-end launch.

| Publisher / boundary | n | Median ms | Range ms | Desired-set builds/call | Git calls/call |
|---|---:|---:|---:|---:|---:|
| Pre-E first touch | 3 | 1781.0 | 1141–2703 | 2 | 4 |
| Candidate first touch | 3 | 1360.0 | 1219–1516 | 1 | 4 |
| Pre-E unchanged | 6 | 1179.5 | 953–1390 | 2 | 4 |
| Candidate unchanged | 6 | 85.5 | 78–156 | 0 | 0 |

The earlier 1.50-second first touch and 141/78-millisecond reuse probe was
unmatched; the table above supersedes it for comparison. Small local samples
and shared-host variation do not establish a broader launch-speed claim.

## Cold-bootstrap comparison: independent, still failing

Exact case:
`tests/test_hook_ipc.py::test_windows_first_install_direct_hook_stamps_without_runtime`.
The raw-source fixture first failed because the payload lacked its vendored
`agent-procutil`. The fixture now uses the existing
`test_first_install_bootstrap._materialize_installed_payload` helper, which
calls the canonical release materializer. It does not install a shared runtime.

Only this case was then compared against pre-E and candidate source:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_hook_ipc.py -k windows_first_install_direct_hook
```

The comparison temporarily substituted the pre-E fixture source, retaining the
same canonical materialization, `{"sessionId":"cold-hook"}` input, absent
killswitch, isolated home and deadlines. The same case was run serially against
each source twice; both sources failed both times. The second pair emitted
pre-invocation identity receipts before the test's timeout.

The failure phase is `_bootstrap_first_install` waiting in
`subprocess.communicate` for the direct Windows deferred-runtime stamp:

```text
<python> -I -B -X utf8 <payload>/scripts/command_binding.py
  --payload-root <payload> --plugin-id agent-worktrees
  --legacy-root <isolated-home>/.agent-worktrees
  --source-payload-root <payload> --deferred-runtime --run-lifecycle
  --lifecycle-timeout 10.0 --
  <powershell> -NoLogo -NoProfile -ExecutionPolicy Bypass
  -File <payload>/scripts/install.ps1 stamp -BootstrapPython <python>
```

The lifecycle's **10-second** deadline is unchanged. The direct wrapper's
subprocess timeout is `None`; the existing **30-second pytest item limit**
terminated the matched cases, followed by runner-owned containment cleanup.
No outer or inner limit was raised. An earlier candidate-only staged attempt
returned the lifecycle's own exit 124; it is not the matched comparison result.

The staged `plugin.json`, materialized `pyproject.toml`, `hook_client.py`,
`install.ps1`, `command_binding.py` and `command_lifecycle.py` hashes matched
between sources. Representative unchanged bootstrap hashes:

- `hook_client.py`: `65bf9f373d7c172b077c4fa1f9addbdb7a46e12b6b7b05930451a17285e9b237`
- `command_lifecycle.py`: `949c796a2683f8915d939a5475465870429e1a6e46cf5e0e365b5bc7ea17182a`
- Pre-E source capsule: `fce371ad3838568207b6335b6a023bf58ea36fb4a6da17283c103fcf10cd8873`

This establishes an independent native-bootstrap failure, not a passing native
tier or a complete root-cause diagnosis. Follow-up ownership is the unchanged
`scripts/hook_client.py` → `command_binding.py` → `command_lifecycle.py` →
`install.ps1 stamp` boundary. Lane E does not modify those production paths or
take over their owners.

## Completion boundary

Source partial delivery may proceed only with normal review and green required
CI. The cold-bootstrap/native entry and installed acceptance obligations remain
open under #7943 and the coordinating campaign. No failing required check may
be bypassed. Source merge, release promotion, installed verification and actual
model admission remain separate states.

## Review corrections

Automated review identified five defects. Six focused controls failed before
the corrections (the symlink finding has settings and declaration variants).
The resulting focused validation completed **19 passed, 127 deselected**:

- A one-second reuse-probe timeout retains backup admission when sufficient
  hook budget remains and refuses it when the overall budget is exhausted.
- Initial/complete publication revisions detect intervening foreground and
  partial publishers even when the backup itself copies zero files.
- Unchanged regular-file symlink inputs publish and reuse successfully.
- Creating an ignore negation invalidates prior readiness.
- Index changes between preflight and mutation/save refuse publication or
  receipt creation; a new unchecked index is never certified.
- Existing exclude-reread behavior, independent-process reuse/contention and
  completion-failure semantics remain covered in the same focused run.

The publication measurements above identify the original preserved candidate,
not these later correctness refinements. They remain phase-only historical
evidence until a matched post-review measurement is recorded.

The subsequent review refinement propagates deadlines through real input,
output and Git-index hashing; timestamps watches before the probe; persists
abnormal-worker failure data; and aligns Bridge's outer process-group custody
with nested Worktrees Git processes. Focused controls completed **11 passed,
2 skipped** in Worktrees and **13 passed** in Bridge. The two skips are the
new native Linux cancellation/owner-failure scenarios on this Windows worker;
their actual native execution remains part of #7943's open qualification, not
a claimed pass. No test or lifecycle deadline was increased.

The macOS review correction stays within Bridge: it uses the already-required
psutil native birth-time API rather than adding a shared-library API or copying
a platform ABI. Matching, mismatched, missing and inaccessible birth evidence
plus pre-spawn custody refusal have focused controls (5 passed). The broader
identity selection had 12 other/overlapping passes and 5 explicit platform
skips before correcting its Windows-only fake-signal setup; no production
change was needed for that test harness correction. Two native Darwin
descendant controls are source-preserved but unexecuted here and remain in
#7943's open native acceptance.

Post-copy failure evidence is covered separately: the actual output is written,
verification then fails, and the post-tool notice still names repair plus
restart/new-context requirements. A mutation revision is retained before output
effects and failure counts are explicitly unavailable; a later successful
reprepare restores reusable readiness. Focused results: **13 passed** in
Worktrees and **4 passed** in Bridge. Abnormal exit after a mutation attempt
uses the same conservative notice, without claiming that a failed refresh
delivered a complete set.

Dangling source links have explicit partial-delivery controls. Metadata-only
evidence no longer follows a missing link target merely to revalidate a failed
provider; healthy providers still publish, and healthy linked inputs retain
content-hash validation. Both dangling controls failed before the correction;
the focused broken/healthy source-link selection then passed **7 cases**.

The final notification ordering controls cover a foreground publication after
the backup has already returned its unchanged result, and a real queueing
process that exits before its admitted worker fails. The standalone post-tool
consumer still reports failure without relying on the vanished reaper. Native
read-only exit observation retains live or access-denied owners; it does not
terminate processes or rewrite worker state. Those two integration controls
plus four liveness controls completed **6 passed**.

The clarification review identified cyclic (not merely dangling) source links
on Python 3.10–3.12. Fresh and previously cached self-linked providers now fail
independently; invalid cached evidence is retried while `TimeoutError` still
propagates. Both new controls failed before the correction. The cyclic,
missing-source and actual receipt-deadline selection completed **10 passed**.

An inherited-output-pipe control reproduced unbounded foreground preparation
remaining blocked after its owner exited. The fixture explicitly preserves
stdout/stderr while keeping Windows children headless; its original bounded
control still times out as expected. Owner-exit observation now runs independently
of pipe EOF, allowing identity/Job-bound descendant cleanup before communication
finishes. Five focused cleanup/cancellation controls passed. Two asyncio
transport-finalizer warnings were reported by the existing cancellation fixture;
the process-retirement assertions passed, but these warnings are not represented
as a warning-free native qualification.

The already-reaped POSIX owner case has a separate deterministic regression:
missing birth evidence previously left communication pending indefinitely.
Preparation now fails before entering that wait, closes its local capture
transport, and reports descendant cleanup as unconfirmed rather than weakening
identity checks or claiming retirement. The focused missing-custody, normal
owner-retirement and held-pipe selection passed **5 cases**, with **3 explicit
platform skips**.

Cache-only metadata is explicitly not a live worker. A focused regression
reproduced false liveness after foreground preparation without any queued
request. The correction preserves actual queued ownership and an unconfirmed
admission lock; those three controls passed. This is a liveness/reclamation
correction, not evidence of faster full-roster CLI startup.

Partial source ownership has explicit collision controls. A failed provider's
existing slot cannot be replaced by another valid provider, including after
an initially partial publication. Unknown legacy slots are preserved until a
complete source set is available, while proven healthy slots remain updateable.
Three negative controls failed before the correction; the partial-ownership,
cyclic-source, normal reuse, late-notice and full-reconciliation selection
completed **13 passed**. Private preparation metadata is version 2 and
distinguishes partial ownership history from reusable ready state.
