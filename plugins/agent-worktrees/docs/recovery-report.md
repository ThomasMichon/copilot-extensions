# Exact-input recovery report

`agent-worktrees doctor --recovery-report <request.json> --json` is the G3
report-only consumer of the [G1 envelope and placement contract](recovery-facts.md).
It returns before normal doctor project/session discovery and cannot be
combined with repair, GC, daemon-health or other targeted doctor options.
The legacy doctor/default adoption guards are unchanged.
`front_door_cli.resolve_invocation_project` recognizes this exact mode before
explicit project lookup: `--project`, `--project=...` and `-p` do not discover a
registry or change CWD for this report. Relative request filenames therefore
remain relative to the invoking CWD. Unencodable/surrogate paths produce
`invalid_root`, not a filesystem traceback.
The routing check includes argparse's currently accepted unique option
abbreviations (`--rec` through `--recovery-report`), with separated or `=` values.

The caller supplies an independently resolved, exact selection:

```json
{
  "schema": "agent-worktrees.recovery-report-request",
  "version": 1,
  "repository_id": "00000000-0000-0000-0000-000000000001",
  "resource_id": "00000000-0000-0000-0000-000000000002",
  "worktree_root": "<absolute fixture worktree>",
  "git_common_dir": "<absolute fixture Git common directory>",
  "marker_ignored": true,
  "central_record_path": "<optional absolute fixture record>",
  "fact_files": [],
  "fact_budget": 8
}
```

Replace the path placeholders with actual absolute paths; they are not discovery
instructions. Omit `central_record_path` when it is not selected. `marker_ignored`
is an input observation, not consent to edit Git exclusions.
No provisioning, Git commands, configured-root search, session lookup, lease
read, ownership election or file write occurs in this branch. Qualification is
limited to isolated synthetic resources; no installed/live qualification is
claimed.

## Bounded selection

Ordinary reports read the request and the two exact identity markers. They never
enumerate a directory or replay historical facts. A selected central record is
statted only and reported `central_record_unexamined`; neither its contents nor
its absence determines ownership or settlement.

For explicit discovery, supply `fact_files` as canonical operation-UUID `.json`
basenames beneath the selected common journal directory. This is labeled
`explicit-bounded-file-selection`, not automatic discovery. The request is at
most 64 KiB with at most 256 unique filenames; `fact_budget` is 0–32, default 8.
Only the first budgeted filenames are opened, at most 64 KiB each. The result
reports attempted and omitted counts. Names are checked before any resource
reads; traversal, duplicates and unsupported required request capabilities are
refused. Marker and fact parsing retain G1's strict size, schema, numeric and
unknown-field-preservation boundaries.

## Outcomes are evidence, not authority

Per-input statuses are `candidate`, `missing`, `conflict`, `unsupported` or
`unknown`, with stable reason codes. The overall outcome is `conflict`,
`unsupported`, `partial` or `unknown`. A foreign/mismatching marker is conflicting
evidence, not proof of a foreign owner. Malformed/truncated/unavailable inputs
remain explicit gaps; readable optional data is not rewritten or echoed.

**G3 never emits complete migration.** Every report retains:

- `migration_completeness: unknown`, reason `g2_manifest_unavailable`;
- `obligations: unknown`, never an inferred empty set;
- `writer_authorized: false` and `effects_admitted: false`.

No G2 manifest is yet accepted, even if a request includes an optional
`migration_complete` or `g2_manifest` assertion. Markers do not certify semantic
journal completeness. G3 registers no semantic reader capabilities, so a fact
requiring one produces `required_capability_unsupported` rather than claiming
the operation is understood. #1460's actual durable rollback capability floor
remains unestablished; the consolidated #1915 inventory is not qualification.

Exit 0 means a report was produced, including partial/conflicting observations;
it does not mean readiness, settled obligations or permission to mutate. Exit 1
means the request/option/read surface itself was refused, with a structured
`error`. The text renderer carries the same non-admission headline.

G2 writers, G4 loss/rebuild, G5 Bridge/installer changes and G6 publication/force
remain unadmitted. No changes to `record_read`, `session_projection`, ground-state
transactions or live adoption are part of this diagnostic.
