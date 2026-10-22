# Installation-context lock recovery

Companion to [recovering-operational-locks](../SKILL.md).

Use the **published** `scripts/installation-context/installation_context.py`
inside the owning plugin's attributable payload. Resolve that payload from its
command catalog/source binding; do not guess an installed copy or patch it.
Use an existing trusted physical Python interpreter. The entry is stdlib-only
and does not invoke the broken front door or provision a runtime.

Read the owner receipt locally, without disclosing its token. The lock's
`kind`, `marketplaceId` and `pluginId` must match the owning operation, not values
invented to make validation pass. A legacy command-binding lock uses kind
`command-binding-publication`, marketplace `legacy`, and its owning plugin ID.

```text
<python> <published-installation_context.py> lock-inspect --lock-path <absolute-lock> --kind <kind> --marketplace-id <marketplace> --plugin-id <plugin>
<python> <published-installation_context.py> lock-repair --lock-path <absolute-lock> --kind <kind> --marketplace-id <marketplace> --plugin-id <plugin> --expected-evidence <inspection.expectedEvidence> --inactive-owner-evidence "<verified owner AND descendant settlement evidence>"
```

Inspect is read-only. Repair acquires kernel exclusion, rechecks the exact
directory/file/content evidence and native owner state, then quarantines the
directory with a prepared/completed receipt. It never deletes the original owner
bytes. New normal writers use the same short mutation exclusion, but do not
automatically reap dead admitting parents: an installer child can still be
writing. Independently assess the original operation's descendants before
**every** repair, including a valid stale owner. Legacy live PIDs remain protected; new receipts
also bind native process birth so PID reuse is not mistaken for ownership.
The kernel releases mutation exclusion after a process dies or a machine reboots.
The persistent `.mutation-guard` file is **not** a stale lock to delete.

## Incomplete receipt: approved manual bypass

An empty or malformed owner record is not automatically safe. After establishing
the specific inactivity evidence in the parent skill, inspect in manual mode:

```text
<python> <published-installation_context.py> lock-inspect --lock-path <absolute-lock> --kind <kind> --marketplace-id <marketplace> --plugin-id <plugin> --allow-incomplete
<python> <published-installation_context.py> lock-repair --lock-path <absolute-lock> --kind <kind> --marketplace-id <marketplace> --plugin-id <plugin> --expected-evidence <inspection.expectedEvidence> --inactive-owner-evidence "<specific verified boot/drain/process evidence>"
```

The evidence argument is an explicit assertion by the authorized repairer, not
an automatically verified boot/stop certificate. It is preserved in every
recovery receipt. It additionally enables the incomplete-owner path; it never overrides a valid
live/foreign/unknown owner, unsafe path, changed evidence or unexpected contents.
Do not use a generic "probably stale" explanation, rewrite a malformed record
into invented valid metadata, or use this mode merely because automatic repair
refused.

The printed `backupPath` contains the original lock; its parent contains
`recovery.json`. A `prepared` receipt after a crash requires inspecting both the
original and recorded backup path. If the original remains, inspect again before
retrying. If only the backup exists, preserve it and retry the legitimate
operation; do not recreate the old stale lock. If both exist, inspect the new
original as a potentially live successor. Neither a receipt nor quarantine proves
that the interrupted update's payload/runtime cutover is coherent.
