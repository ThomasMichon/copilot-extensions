# Local diagnostic logs

`agent-dispatch logs` is a read-only JSON inventory for the local installation.
It uses `install_paths.install_dir()`, including `AGENT_DISPATCH_INSTALL_DIR`.
It does not contact or start a coordinator, read credentials, create directories,
prune files, or return diagnostic contents. Missing files are explicit.
Discovery alone cannot prove another process can write: `write_health` remains
`unknown-from-discovery`.

## Application owner

The implementation is now the optional owner-injected
`process_telemetry.diagnostic_logs` policy shared with Bridge. Dispatch keeps its
existing compatibility adapter, paths, discovery schema, synchronous emission and
maintenance ownership. The shared library discovers no roots or locks itself.

The existing `configure_file_logging` entrypoints for the coordinator,
supervisor daemon and actual worker-lane `supervise` process attach a handler
to `agent-dispatch.*`. Python logging handlers are process-local; configuring
the daemon alone does not configure its separately launched lanes. Current files live at
`<resolved-install-root>/logs/diagnostics/{coordinator,supervisor}.jsonl`.
Archives in the same directory have the form
`<component>.<UTC-YYYYMMDDTHHMMSSffffffZ>-<unique-segment>.jsonl`.
The inventory lists both. Segment IDs distinguish files, not process identity.

Each UTF-8 JSON line carries UTC `ts`, service, component, logger, level, PID,
the existing OS process-start token (null when unavailable), imported
`runtime_version`, actual interpreter `runtime_prefix`, and message. Exceptions
are escaped inside the same JSON record. Only explicitly supplied `operation_id`,
`correlation_id` and `generation` extras are copied; arbitrary LogRecord fields,
environment, tokens and ownership receipts are not serialized. The handler does
not infer a serving generation from a version marker or turn diagnostics into
activation evidence. Call-site redaction/content restrictions still apply.
The Dispatch adapter also filters known credentials and scoped caller inputs
from legacy application messages and exception fields before handing a cloned
LogRecord to the shared writer. It does not mutate the original record or
exception. Bounded explicit-cause and visible implicit-context summaries precede
stack context so a long stack cannot displace the underlying cause. Suppressed
contexts stay suppressed; cyclic or oversized chains have an explicit omission
marker. This final filter is not a general private-data
classifier or permission to log arbitrary task content.

### Spawn causal records

The `agent-dispatch.spawn` logger emits JSON objects in the existing JSONL
record's `message`. Decode that string to inspect `event: spawn_stage`, `stage`
and `outcome`. The outer record retains the standard process/runtime attribution.
Cycle/call IDs group observations only; they never authorize launch or retry.
Task ID/generation and reservation/creation keys are included as they become
available. Existing reservation keys join ordinary Bridge caller records to
their supervisor cycle even when the caller has its own diagnostic group.

Each observed call emits `entered` **before** invocation, then `returned` with
monotonic `elapsed_seconds`, or `error` with the causal exception type and
bounded message. A returned call is not necessarily a successful operation:
inspect `ok`, `available`, `reserved` or `returncode` where supplied. An unmatched
entry means the call's completion was not observed, not that it definitely hung
or that no effect occurred.

Coverage includes ordered reconciliation/recovery, task selection, retry-budget
and active-reservation reads, capacity/governance checks, reservation,
workspace preparation, backend invocation and spawn bookkeeping. Scheduling
records distinguish concurrency/no-capacity/governance/contention deferrals;
the existing bounded dead-letter warning retains task examples and the stage
result adds an exhausted count. Ordinary Bridge records cover reservation
read, create/resume/status/receipt/send subprocess boundaries and durable
invocation store/readback. Subprocess errors keep their original exception and
primary result; timeout records do not invent an exit code. Nonzero exits retain
selected structured errors and bounded stderr, never arbitrary stdout envelopes.

Known credential values, credential assignments and supplied prompts are removed
using the existing diagnostic-text owner. Raw task/environment/invocation
envelopes are not copied into these records. These restrictions are not a reason
to erase benign operation identities or causal errors. The existing optional
`logs/bridge-cli` trace remains separate and default-off.
Exact supplied prompts/invocations are removed before bounded normalization, so
a long input cannot leak a prefix merely because it was truncated before
matching. Plain `[Errno N]`/`[WinError N]` causes remain diagnostic text, not JSON
arrays. The serving loop logs a type-only failure summary after the filtered
causal stage rather than writing the same exception's unfiltered traceback;
its optional cycle callback has its own causal stage.

These records use synchronous local application emission, including its existing
bounded lock/degradation policy; they introduce no child process, network export
or new collector. A failed sink still warns and preserves the primary operation,
so this is not a strict retained-pre-effect guarantee. Native child output,
Bridge-internal preparation/first-prompt stages, standalone one-shot caller sink
configuration and full timeline export remain separate acceptance work.

Retention compares aware timestamps against **now UTC minus exactly 30 days**.
Records strictly before that cutoff expire; equality, younger records and future
timestamps remain. File mtime, timezone-local dates, count and size do not decide
age. Malformed/naive timestamps remain with a degraded warning rather than being
guessed away. A backward clock does not expire future evidence. This cannot undo
expiry already performed before a clock was corrected.

The handler rolls active output at UTC date changes or 5 MB, with no backup-count
limit replacing age retention. An unterminated tail from an interrupted append
also rolls before the next write, preserving the damaged evidence separately
instead of concatenating a fresh record into it.
Emission only appends/rolls the current file:
it never scans or prunes history on a coordinator coroutine's event-loop thread.
Existing daemon maintenance prunes active **and** archived files, debounced to at
most once per 60 seconds. The coordinator invokes it through `asyncio.to_thread`;
the supervisor already has a synchronous maintenance iteration.
Unchanged segments reuse in-memory file-identity/size/time-validated earliest
expiry information; unexpired history is not repeatedly parsed or copied.
Only files with expired records are rewritten, and custody is yielded between
segments. Restart scans establish fresh information rather than trusting a
persisted index. Strictly recognized abandoned prune copies are reclaimed under
the component lock, which excludes an active participating pruner.
Startup forces maintenance. The existing coordinator liveness-GC loop,
supervisor daemon and standalone worker-lane iteration boundaries also invoke
maintenance, even when no new messages
arrive and before governance backoff. Quiet-service cleanup therefore follows the
owner's configured maintenance cadence; a stopped service cleans up on restart.
The existing lifespan-owned coordinator loop stays armed on a 60-second
diagnostics-only cadence when task GC is disabled; that branch never reconciles
queue state and uses the same lifespan cancellation owner. There is no separate
scheduler, process, service, cron job or collector.

All participating application writers close their data handles after each append.
The existing crash-releasing `OperationLock` serializes append, rotation and
pruning across threads/processes; the stable hidden per-component lock file is
not itself diagnostic history. Maintenance rewrites through a same-directory
file and replaces only after its reader/writer handles close. Contention has a
two-second bound. Logging/retention failures give a bounded stderr warning,
remain fail-open, and never change the primary operation's result or write to
protocol stdout. A third-party reader that denies Windows delete sharing can
prevent replacement; that is degraded retention, not a successful prune.

## Compatibility and uncovered owners

The old `<root>/logs/{coordinator,supervisor}.log[.N]` text files remain discoverable
and untouched. New JSONL files deliberately use a separate namespace: mixed-version
cutover may leave an old generation's `RotatingFileHandler` open. Renaming those
files on POSIX would strand its writer; Windows may refuse the rename entirely.
Consumers tailing the old files must use the inventory to select the new active
file and archives. No transparent text/JSON format compatibility is claimed.

The same inventory exposes known native `serve-service`/`supervise-service`
version/PID fallbacks, supervisor launcher, generation/self-deploy/successor
captures, boot-trace (including its explicit environment override), reconcile and
ZDD lifecycle files (including the actual `config.routing_dir()` override).
They are labeled **not managed by the application handler**
and are never renamed, truncated or deleted by it.

These remaining boundaries stay under #8377, not silently covered here:

- Native capture needs its lifetime owner to drain both streams, frame UTC and
  real process/runtime attribution, rotate without moving/truncating inherited
  handles, and maintain age retention while quiet. A launch header or size limit
  is not that contract. Qualify real Windows and POSIX lifetime/handle behavior.
- #8370 owns independent installer attempt logs, bootstrap discovery and their
  protected active/incomplete attempts; #6812 owns progress formatting. Neither
  implementation is replaced here.
- Configured telemetry spools retain their existing producer format, path,
  privacy and atomic append contract (#8334). They are not rotated here.
- Container output, controller/connectors, Bridge/session diagnostics and
  deployment mappings require their own owner-resolved sink/discovery/retention
  adoption. Authoritative session/task state and ownership receipts never enter
  this expiry policy.

## Explicit retained-spool API

`telemetry.make_retained_spool_sink(directory)` is a separate Python API, not a
change to existing factories/configured paths. The existing owner must provision
a new exclusively participating `directory/retained/` namespace, exclude legacy
writers, schedule quiet maintenance and expose `sink.inventory()`. Missing parents
are never created. Producer fields and existing epoch-millisecond timestamps are
preserved, with one append write under the injected custody and no suffix retry.
This synchronous API is not an implicit async adapter or deployed adoption.
See [process-telemetry](../../../libs/process-telemetry/README.md) for the exact
contract and explicit timestamp-decoder seam.

Source merge does not activate a running daemon. After release, only the existing
authorized installation/update and graceful daemon cutover path may select the
new handler. Verify the new process's imported version and actual log inventory;
an old running generation does not gain retention from a source or payload update.
## Configured retained telemetry

Use an explicit alternative in the convention-located `telemetry.json`:

```json
{"retained_spool": {"directory": "<absolute owner-provisioned base directory>"}}
```

Provision a new exclusive `directory/retained` before selecting it. The active
name is `agent-dispatch-telemetry.jsonl` there, separate from Bridge's component.
The loader creates no parents and never adopts legacy files. The object accepts
exactly one absolute, nonempty string `directory`; top-level `sink`/`spool` may
not coexist with it. Legacy zero-argument factories and spool semantics are
unchanged without this opt-in.

The existing owned buffer constructs and maintains the sink on its worker,
including startup and quiet/busy iteration. Producers only offer bounded
primitive snapshots. Selection is pending, not health/persistence evidence;
initialization failure is optional/degraded with a content-free warning.
Coordinator lifespan shutdown clears this configured buffer without joining a
blocked writer. Clear/replacement never starts a replacement while its previous
worker remains alive; retry after exit. Embedded callers own their matching
`clear_telemetry_sink()` boundary.

`agent-dispatch logs` reports configured retained active/archive locations and
policy in `telemetry`, using configuration and metadata only. Live write health
remains explicitly unknown. Custom legacy factories are not imported or exposed.
See Bridge's diagnostic documentation for the identical validation, timestamp,
retention and asynchronous failure semantics.
