# Optional command telemetry

Bridge can project existing session lifecycle records to an independently
installed receiver command. This is source support for
`consumer-telemetry-command` version **1**, transport `command-jsonl-v1`,
source `agent-bridge`, source schema `bridge.lifecycle.v1`.
It does not enroll Dispatch, diagnostics, inbox delivery or performance events.
It does not install a receiver, daemon, SDK or dependency.

## Trust and selection

The existing Bridge service configuration admits exact local harness roots by
the existing authenticated operation's `caller_id`:

```yaml
consumer_telemetry_harnesses:
  example-caller: /absolute/path/to/harness
```

Use the platform's absolute path syntax. These paths are trusted **server-side
configuration**, not incoming request fields. The normal Bridge authentication
and request owners still admit callers and targets. This explicit map authorizes
the command in that root for the named caller's activity. Selection is captured
on the session and retained independently of reducer/history replacement.
It is not inferred from the target agent or target directory,
the request's cwd, an environment variable or the most recent caller.
Use distinct caller identities for distinct initiating harnesses. The same
target agent can serve different admitted callers without changing their routes.
Remote hosts never receive a sender's executable path through this feature.
An elevated sub-daemon inherits no command admission from its primary.
An inherited bound map becomes empty, not legacy mode. Ordinary receiver
admission does not authorize elevated receiver execution.

An omitted/null map preserves explicitly unbound legacy factory/spool behavior.
An object selects bound mode: an unlisted caller exports nothing. A missing,
disabled, invalid or refused declaration never falls back to the legacy sink.
The map and each root's declaration are snapshotted at manager initialization.
Reconfiguration requires ordinary owner shutdown and a new initialization.
No configuration is read on a lifecycle callback.
Logs that do not yet have admitted session context, including constructor-time
recovery observations, are explicitly unavailable in bound mode. They do not
inherit the legacy sink. Binding is separate from replaceable reducer/history
state. Newly encountered unadmitted logs remain suppressed, not suite-wide
capture inferred from missing context.
Later remote attachments and merge-recovery sessions use the accepted manager
session table and the same snapshotted caller map. Once selected, session
admission does not change on reuse. Database adoption retains that owner;
candidate exit and partial construction failure settle its optional writer.

The admitted root contains `.agent-telemetry/config.json`:

```json
{
  "schema_version": 1,
  "mode": "command",
  "allow_local_override": false,
  "command": {
    "argv": ["python", "-I", "tools/receive_telemetry.py"]
  }
}
```

Dependencies must already exist. Arguments are literal; scripts use an explicit
native interpreter. The receiver runs with the admitted root as cwd, regardless
of later `chdir`. Only the contract's OS environment allowlist reaches it.
Keep credentials and dynamic record/session data out of argv.

An optional Git-ignored `.agent-telemetry/config.local.json` replaces the whole
selection only when shared command policy permits it. A valid local disable
always suppresses export. Malformed selected configuration refuses export.
The shared parser enforces the reviewed precedence, UTF-8/duplicate-field rules,
16 KiB file bound, 32 arguments and 8 KiB total argument bound.

## Receiver and capability

The receiver reads compact UTF-8 JSON objects, one LF-terminated record per
line, from its dedicated stdin. Its stdout/stderr go to null handles, not
product output. It must reject unsupported contract/version/source schemas and
invalid frames. A receiver must not detach or leave background descendants.

Each envelope contains only `contract`, `version`, `source`, `source_schema`,
`source_instance`, `route_generation`, `sequence`, `observed_at` and `event`.
Source-instance and route-generation values are opaque UUIDs. Sequence gaps
can indicate loss. `event` is the closed structural projection; native session,
worktree, tool-call identities, payload text and arbitrary statuses are excluded.
Log-rebuild epochs are omitted; null optional fields remain absent.

The executing package's `metadata` directory contains
`consumer-telemetry-contract.json`. Resolve it from the **selected runtime**,
not a newer marketplace payload. Bridge's existing `/health` response adds the
same optional object as `consumer_telemetry_contract`. A newer installed
descriptor does not prove that an older running daemon supports the contract.
Missing or malformed metadata means unknown support. Existing legacy APIs and
configuration are not removed.

One existing `BufferedSink` owns a maximum of 128 queued records and one
32-record drain batch across at most four admitted generations. Its worker
serializes the projection and lazily starts one receiver per route. It does
not start a process per event. Windows uses atomic caller-owned Job custody;
Linux reuses the private-session guardian and parent-lifetime pipe.
Unsupported custody refuses the optional route before any record write.
There is no second transport queue or blocking pipe-writer thread.
Concurrent producers use a nonblocking offer guard. Contention drops an offer
and leaves a sequence gap; accepted records retain increasing source sequences.

Record writes have a one-second aggregate deadline. An explicit off-path close
stops offers, drains for at most 250 ms, and settles receiver custody within a
two-second aggregate budget. Unconfirmed cleanup retains exact custody and
prevents replacement; it is not reported as success. Product outcomes remain
independent. Unconfigured operation starts no telemetry worker or receiver.

`ConsumerTelemetry.status()` exposes content-free selection states and separate
offer/queue/drop/serialization/write/abandonment counts through the existing
manager owner. `BufferedSink.delivered` still means callback return. A complete
pipe write is **not** receiver acceptance, persistence or backend delivery.
Qualify those independently through receiver-owned readback.

## Focused source validation

Use the repository's contained runner:

```text
python tools/run-plugin-tests.py agent-bridge --test-file tests/test_consumer_telemetry_command.py --test-file tests/test_consumer_telemetry_lifetimes.py --test-file tests/test_telemetry.py --test-file tests/test_process_telemetry.py --allow-explicit-tiers
```

The native witness loads the ordinary service configuration and calls the
authenticated HTTP stop endpoint on fixture-owned persisted sessions. Its actual
manager/reducer emits session
and conversation transitions. An independent standard-library receiver writes
and syncs its own file; the test reads that file and checks the records, one
reused child, cwd, literal arguments and environment isolation. It does not call
an emitter or command helper as its integration proof. Invalid/disabled
declarations preserve the ordinary HTTP 204 outcome and create no receiver.
An unlisted caller using
the same target agent does not inherit either the admitted route or legacy sink.

Source validation is not installed/release support. Native skew, whole-suite
coverage reconciliation and release/installed qualification retain their
separate U2–U4 owners.
