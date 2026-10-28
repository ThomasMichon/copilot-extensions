# Qualification and Adoption Boundary

Evidence and reproducible controls for the [canonical effort](README.md).
The product is opt-in guidance plus a read-only inspector, not an execution
engine, cursor writer or new journal provider.

## What is delivered

- The agenda-driving skill defines the continuous next-action, exit, bounded
  detour, parking and parent-return procedure.
- The worktree agent owns its roster, cursor and work stack across sessions,
  beside its journal through an explicit owner/adopter locator. Canonical
  efforts/tasks retain their own gates; no repository-global current pointer
  or duplicate completion checklist is introduced.
- The optional Python `next`/`check` helper reads the active/next snapshot and
  gives diagnostics. It does not create state, choose new assignments, execute
  graph data, advance a cursor, acquire authority or prove source completion.
- Manual use and ordinary non-opted-in efforts remain available without
  Python helpers, agent-worktrees or agent-dispatch.

## Controls

| Requirement | Control and observation | Boundary |
|------------|-------------------------|----------|
| Parent/task completion | The [paired spike](spike-results.md) used identical independent task checks; both arms passed 8/8 | One exploratory pair; no general efficacy claim |
| Worktree-local roster | Treatment journal retained the parent, parser detour, assigned counts root and parked CLI idea | No global plugin installation or shared cursor |
| Nested continuation | A separate synthetic A -> B -> C -> B -> A run saved returns and checked real proof bytes/source gates before popping | A manual single-writer witness, not transactional execution |
| Blocked/steering work | Three-frame steering retained both parent frames and a concrete next action | No automatic external wake or permission grant |
| Bounds | Manual fourth-child refusal; unit checks for visit/depth/detour excess and invalid outcomes | Successful entries count too; no automatic metric/plateau evaluator |
| Stale/corrupt state | Revision and definition mismatch, wrong lane, bad returns, malformed/duplicate/nonfinite/oversized JSON refuse inspection | Read guard only, no CAS for later edits |
| Read-only behavior | Unit before/after bytes and all ten synthetic helper receipts show no cursor mutation | Caller redirection/edits remain outside helper control |
| Source authority | Proof files and child contracts were independently checked; helper prints criteria/declared moves and a source-gate warning | Exit code 0 is not task completion |
| Existing binding guidance | The implementation remained bound to an earlier declared slice while its journal cursor selected qualification work under the same root | Manual reconciliation observed; no provider nudge/projection rewrite is shipped |
| Cross-platform use | Native Windows packet and real Linux Python CLI reads, including active, three-frame and steering-with-queue snapshots and stale-reader refusals | Not a Linux agent lifecycle, installation or host-health claim |

The three-frame qualification's parent proof files were byte-checked independently
after the agent run. Captured snapshots contain stack depths 3, 2, 1 and 0,
and a current-helper rerun on the three-frame snapshot preserved its bytes.
The helper's source-contract warning remained visible throughout.

The complete relevant Windows efforts packet passed with platform-specific
producer controls skipped as reported in the Journal. Focused helper cases
are in
[`plugins/efforts/tests/test_agenda.py`](../../../../plugins/efforts/tests/test_agenda.py).
The bounded source runner can reproduce them:

```text
python tools/run-plugin-tests.py efforts --test-file tests/test_agenda.py --allow-explicit-tiers
```

The source-owned runner supplies isolated state, time bounds and cleanup.
Use the full `efforts --allow-explicit-tiers` packet for the existing producer
and documentation contracts too. Do not interpret a platform skip as a pass.
Linux CLI checks used the actual standard-library interpreter on Linux; host
startup diagnostics were kept separate and no system configuration was changed.

## Manual and helper scope

There are no transition-writing operations in v1. Missing exit evidence keeps a
manual exit open; inspection reports the criteria but never certifies them.
Consequently the helper cannot silently apply an undeclared branch/reset,
double-pop a child or replace a newer cursor: it never writes at all.
The manual agent must still reread/reconcile interrupted or stale edits.

An optional definition revision is checked against the saved frame when present;
without it the helper explicitly leaves freshness to manual verification.
Legacy manual cursors without a version are read as v1 with a note, not
silently rewritten. Unknown history/metadata survives because no serialization
is written back. JSON output is a report, not replacement cursor state.

No experiment proved concurrent writers, atomic multi-file writes, crash
transactions, power-loss recovery, exactly-once external effects, arbitrary
off-agenda-action prevention or authenticated custody. These are not promised
by the product. Adding a writer would be a separately designed and tested
capability, not an implication of the read-only CLI.

## Adoption

Enable the released efforts payload through the normal marketplace/update
owner when rollout is authorized. Explicitly opt the lane into
`driving-effort-agendas`, choose the journal/state home, adapt the example
boards/cursor to assigned sources and persist that locator in the assignment
and handoff. New assignments extend this worktree roster; they do not erase
prior parent frames or grant scope beyond the assignment.

Invoke the resolved skill script through an existing Python interpreter with
`next --state-home <journal-home>` or `check --state-home <journal-home>`.
Manual operation remains supported without it. No path into a provider's
private tracking layout is inferred or modified, and no new service is started.

Source merge and marketplace release do not establish installed rollout.
Record those stages separately; never claim an already-running session has
received the new guidance merely because files exist.

## Source closure and publication owner

The initial guidance and helper were merged in #9421 and #9429 respectively,
with current-head accepted reviews and green CI. The final relevant local packet
passed **77 tests, 9 reported platform skips**, including 22 focused helper
cases; post-merge Dev CI also passed.

The observed pinned Main snapshot
[`108c515`](https://github.com/ThomasMichon/copilot-extensions/tree/108c51551e0fd378940a5c8ca4635bf25b4490fa)
has efforts `0.1.6-dev1` and does not contain the new helper. This is a
merged/pending-release observation, not an installed-state claim. Normal
qualified Main publication remains with the existing
[#3336 promotion pipeline](https://github.com/ThomasMichon/copilot-extensions/issues/3336).
This effort does not create another publisher, force a promotion or authorize
an adopter's live update.

## See Also

- [Canonical effort and remaining gates](README.md)
- [Initial spike findings](spike-results.md)
- [Driving effort agendas](../../../../plugins/efforts/skills/driving-effort-agendas/SKILL.md)
