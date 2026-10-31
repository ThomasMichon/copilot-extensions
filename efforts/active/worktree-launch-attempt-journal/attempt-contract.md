# Launch-attempt contract

Implementation contract for [the owning effort](README.md), serving Picker's
`durable-launch-attempts` intent. This is a design and evidence inventory, not
an assertion that the journal or SSH repair is installed.

## Entry and evidence owners

| Boundary | Current owner | What it can attest |
|----------|---------------|--------------------|
| New confirmation | Manager `ScopeDlgScreen.on_focus_group_activated`, shared `launch_confirmation`, then `_decide` | Exact field text, selected target and options before dialog dismissal |
| Cold Resume confirmation | Manager `PromptDlgScreen`, then `resume_prompt_actions.open_resume_prompt` | Exact text, selected worktree and New/Resume provenance before dismissal |
| Ordinary Resume/Open, Bare Resume | Manager `_decide` and `_resume_decision` | Explicit selection and options before the Picker exits; Open is prompt-free |
| New-window Open/Resume | Manager `headed_actions.open_worktree_cli_headed` | Selection while the Picker remains open; it bypasses `_decide` |
| Compact Picker launch | Manager `picker_app._request_launch` / `pending_launch` | Selection before that app exits; no production composer |
| Restore | Manager `_restore_production_picker_session` | Requested remux preparation and its result, not a fresh prompt acceptance |
| Task Open into CLI | Manager `_embody_task_cli` / `_resume_embodied_worktree_cli` | Explicit task-to-session request before owner embodiment; later worktree receipts remain owner evidence |
| Provider venue Open | Manager `_open_venue` and runner `open-venue` branch | Selected provider/venue before handing the TTY to its owning provider; no inferred session acceptance |
| Engine resolution | Manager `_resolve_for` / `engine_client.resolve_launch_plan` | Returned worktree, request and seed identities; a plan is not a launched process |
| Remote admission | Engine `remote_seed_launch.prepare` and target `launch_request` | Target-owned admitted request/allocation/seed state; an SSH reply is not prompt consumption |
| Native handoff | Engine `launch_seed_exec` / `launch_seed_state` and existing native receipt owners | Only the actual owner can establish consumption/start; stage, take and finish have distinct meanings |
| Restart recovery | Manager `RestartAttempts` / restart owner inspection | Frozen restart candidates and validated owner receipts, not universal user launch attempts |

`SeedHistory` already commits nonempty raw field text before a supported local
prompt dialog dismisses. It stores only project/text/time, deduplicates adjacent
equal text and has no retention bound. It cannot identify empty-prompt actions,
targets, options or outcomes. Its current consumers subsequently strip text
for dispatch. Capture must preserve the original field bytes independently of
any deliberately normalized execution text.

Production `_decide` exits without a universal durable intent record. New-window
launches bypass it. `_run_launch` is their common execution seam, but persisting
only there is too late to survive a crash between confirmation and that seam.
No one seam alone covers the request.

## Data and custody

- The Manager owns an installation-local protected SQLite journal under its
  existing private-state root. Reuse `private_state`'s owner/SYSTEM Windows ACL,
  POSIX permissions and link refusal, and `RestartAttempts`' atomic initialized
  database publication pattern. No new service or sibling import is required.
- A UUID attempt identity is allocated at explicit confirmation and committed
  with immutable action, project, origin surface, target machine/environment,
  locality and selected worktree. Exact prompt/options/title are a separate
  expirable private input document, not repeated in metadata or event details.
- An attempt ID is correlation, not execution/retry authority. Bind returned
  target request/allocation/seed identities explicitly. Retain existing target
  admission and native receipt ownership; no journal writer may replay an
  ambiguous New or manufacture a replacement prompt.
- A rejected confirmed option combination is a recorded refusal, retaining its
  private input. Cancelled drafts are not confirmed launch intents. An empty
  prompt is still an attempt. Persistence failure leaves the dialog and draft
  open and prevents allocation/launch.
- Manager observations describe only Manager boundaries. Process return, a
  created mux, `copilot_invoked` or a zero SSH exit never becomes Copilot
  acceptance. Missing final evidence is unknown/interrupted, not success or
  replay eligibility. Existing target receipts remain independently inspectable.

## Storage and readers

Use versioned application/schema identifiers and exact schema validation.
Initialize privately and publish the completed database atomically; never adopt
an unrelated or partly initialized file. Writers serialize with SQLite
transactions and `synchronous=FULL`. The original input and first event commit
together before effects. Duplicate persistence of the same ID is idempotent
only for identical input; changed intent needs a new explicit confirmation.

Default bounded list/detail reads select metadata only, not private input
followed by redaction. Explicit prompt retrieval is separate and refuses expired
input. Structured filters include time, action, project, venue, outcome and ID.
Readers do not create a missing store or repair unknown/corrupt content.
Selected-machine sweeps use the existing configured transport and return
per-source unavailable/error evidence; they never provision, replay or write
target state. Private prompt text never goes into a general activity log,
exception, receipt, public evidence or a sweep's default response.

## Retention and legacy copies

Private input expires 90 days after confirmation, without extending on reads or
new events. Readers hide it at the exact expiry boundary even before maintenance
runs. Explicit journal maintenance physically erases expired input using SQLite
secure deletion; use DELETE journaling rather than retaining historical WAL
copies. Protect and inspect all sidecars; an orphan sidecar is an error, not
an empty journal. Maintenance runs on normal Manager use, not a new scheduler;
an offline machine performs physical expiry on its next maintenance opportunity.

Stop creating indefinite independent prompt-history copies. Migrate legacy
history through an explicit idempotent, protected transaction boundary, retaining
original timestamps and historical incompleteness. Expire legacy copies too;
verify SQLite main/freelist/journal/WAL handling and do not delete pending daemon
seed state. Historical prompt-only entries must never be relabeled as proven
worktree allocations or launches. Until that integration is complete, a
standalone journal store is not a delivered 90-day retention feature.

## Performance and qualification

The required commit is recovery state, not optional lossy telemetry. Keep the
fast path in-process with one transaction, no child process, network, daemon
bootstrap or whole-history materialization. Use indexed lookup/expiry and
bounded output. Cold initialization and permission checks count toward cost.
Lock contention reports failure rather than waiting indefinitely or dropping
the record.

Measure first-touch and warm commits on native Windows and POSIX with individual
samples, cardinality, p50/p95/p99/max and explicit failures. Use the existing
100ms key-acknowledgement target as the initial UI budget, not a claim already
met. If durable commit cannot meet it, keep the dialog responsive while the
existing confirmation owner awaits the commit; never dismiss before durability.
Required CI proves transaction/work-count/schema/privacy contracts; explicit
native lanes own crash, ACL, process-boundary and latency qualification.
