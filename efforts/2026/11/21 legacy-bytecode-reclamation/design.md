# Legacy bytecode reclamation proposal

Parent and acceptance: [effort README](README.md).

## Boundaries

This proposal extends the existing cache/runtime lifecycle, not general
historical cleanup. Cache data is disposable only after classification and
current-use/retention proof. Names, modification age, a successful rename and
the absence of a PID in an old record are not sufficient independently.

Preserve [authoritative runtime replacement](../../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and complete selection define update success. Backlog
cleanup cannot veto that success or require an incumbent runtime's permission.
Delayed work must still preserve the then-current selection and its users.

## One planner, independent proof layers

The runtime owner resolves exactly one installation and cache root. Reuse its
construction/selection/publication and retained-runtime policy; do not infer
retirement from a missing version directory alone. The shared cache owner
provides classification, inventory and native no-follow mutation.

The planner produces distinct outcomes:

| Outcome | Meaning |
|---|---|
| Eligible | Complete content, runtime and live-reference proof passed for this exact namespace |
| Held | A named current/retained user, unknown proof, changed identity or unsafe content prevents collection |
| Unmeasured | A deadline, unreadable subtree or inventory bound prevents a complete byte count |
| Interrupted | Admitted work ended before completion; progress and uncertain effects remain explicit |

Only recognized direct `unsealed-<id>` and `installed-<digest>` children enter
whole-prefix qualification. Check the addressed root/ancestors and descendants
without following links. Preserve unexpected data, reparse points, symlinks,
hard-linked leaves and changed objects. Reuse the canonical anchored native
remover and exact identities; do not substitute recursive deletion.

Do not treat every installed digest as retired. Resolve its runtime/source
associations against current installation evidence and protected versions.
An unresolved association is a hold. Selected, rollback, retained,
construction-owned and live runtime associations protect the namespace.

## Windows reference and writer proof

Replace the unconditional unavailable result with a native, bounded observation
of relevant processes owned by the installation principal and recorded runtime
consumers. Include inherited `PYTHONPYCACHEPREFIX` and explicit interpreter
cache-prefix arguments, not executable-name matching alone. Verify principal
and process identity before excluding unrelated processes. An unknown principal
or unreadable relevant process is not absence.

Retain birth-pinned process observations through the decision boundary. Bound
enumeration and memory reads, handle supported process bitness explicitly and
report access failures, unsupported layouts and changing censuses. Return only
cache/runtime references and diagnostic counts; never persist or print complete
process environments or command lines.

Legacy writers do not honor a new lease. Their namespace cannot become eligible
merely because a new collector acquired one. Qualify how an existing writer,
inherited-prefix child or old selected/retained launcher is excluded; combine
that proof with the existing runtime construction/publication fence. Revalidate
references and exact namespace/content identity before effects. If that boundary
cannot be qualified for a candidate, keep it held and name the supported
quiescence/inspection step. No automatic age-only fallback.

## Catch-up, not a larger foreground sweep

Keep allocation, report reads and incoming selection independent of disk
cleanup. Normal maintenance admits one installation-scoped catch-up operation
through its existing native process owner. No per-session shell, process-name
killer, global reaper or permanent new service is introduced.

The operation has a finite aggregate deadline and exclusive installation-scoped
custody. It resumes identity-bound progress across bounded passes rather than
resetting its scan to the first protected prefix. Existing new-cache collection
and legacy prefix collection retain separate work accounting.

Initial qualification targets are a 30-second soft pass deadline, at most
512 namespaces, 262,144 entry operations and 4 GiB of successful removals per
catch-up pass. An explicit drain invocation has a finite aggregate limit,
initially 30 minutes, and reports what remains. Native calls are not claimed
preemptible. Tune these limits from measured Windows work, not assumed storage
throughput; a target is not a qualified performance promise.

Automatic continuation must have an accepted native maintenance owner before
the foreground caller exits. If that owner is unavailable, preserve progress
and print the exact explicit drain command instead of claiming background work
started. Power loss, cancellation or a lost owner cannot erase pending work.
Do not add an independent scheduler around an unowned subprocess.

For backlog fixtures, measure useful bytes/files per pass and end-to-end drain
time, including inventory, proof and deletion. The acceptance record must state
the workload, platform, rate, deadline/budget stops and remaining held bytes.
Do not describe a 32 MiB pass or an inactive maintenance owner as a bounded
wall-clock drain of a multi-gigabyte backlog.

## Honest reporting and command compatibility

Extend the existing dry-run-default CLI through its owning parser. Preserve
strict arguments, prior JSON fields and old explicit prune behavior; discover
new support from the selected executable rather than a package version.
Reports provide the exact supported apply/drain command with the addressed root,
scope and limits. A report is not approval to execute it.

Expose logical byte counts for measured total, eligible and held candidates,
counts/bytes by reason, unmeasured work, progress, observation time and estimated
remaining drain time when a useful measured rate exists. Physical free-space
recovery is a separate observation, not equal to logical `.pyc` bytes.

Update prints a prominent pending/held summary even if it reclaimed zero bytes.
Doctor provides detailed proof failures and next commands. Ordinary status uses
the collector's bounded retained observation and marks stale/incomplete data;
it never recursively scans millions of files or calls unknown data zero.

## Qualification and rollout

Use the [effort validation plan](README.md#validation-plan), including actual
Windows live-prefix consumers, PID/ownership/access negatives, retention races,
all bytecode layouts and interrupted multi-pass deletion. New native modules
must follow the canonical shared-library distribution/generation contract.

First prove the supported report against an installed dry run, then disposable
native apply. A source-read-only real-host assessment can qualify current proof
without installing a candidate or deleting data. Host cleanup, candidate install
and fleet rollout remain separately authorized effects. A merged `dev` PR still
needs release promotion and authorized adoption before the installed command
can be advertised as available.
