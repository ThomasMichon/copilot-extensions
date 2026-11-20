# Optional consumer telemetry command

This is opt-in **source support**, not proof of release or installation.
Dispatch uses the shared
[consumer telemetry command v1](../../../docs/patterns/consumer-telemetry-command.md)
parser, closed projection, `SinkSlot`/`BufferedSink` writer and native command
custody. It does not install a receiver, observability SDK or service.

## Trusted coordinator admission

The coordinator owner may place `consumer-telemetry.json` in Dispatch's
existing installation/configuration directory, beside `telemetry.json`.
This is a local service admission policy, not a receiver command declaration:

```json
{
  "schema_version": 1,
  "repositories": {
    "https://github.com/example/project": "/absolute/approved/harness"
  },
  "service_root": "/absolute/approved/service-harness"
}
```

Use native absolute paths on Windows. The example is not an activation command.
Only `schema_version`, `repositories` and `service_root` are admitted.
The version is integer 1, not Boolean. Repository keys are at most 512
characters; roots are nonempty absolute paths of at most 4096 characters.
At most four roots are admitted in total, including the optional service root.
Repository keys use the queue's existing remote canonicalization; duplicate
canonical lanes refuse the entire policy. Duplicate JSON members, invalid
UTF-8, unknown fields and an opened file larger than 16,384 bytes also refuse it.
The loader preserves the parsed keys; owner construction normalizes them once,
matching queue admission rather than repeatedly removing repository suffixes.

The normal `serve` -> `server.build_app` owner reads this exact file once.
Absence preserves the existing convention-located factory/spool/environment
selection. A present empty, malformed or unreadable policy **suppresses** command
export and legacy fallback for this coordinator. An empty `repositories`
mapping is valid and admits no initiating roots.
A dangling policy-file symlink is unreadable, not absent.
An embedding can supply the same trusted object as `create_app`'s
`consumer_telemetry` argument. Omitting that argument preserves legacy behavior.
No HTTP field, MCP argument, current working directory, task payload or sender
executable selects a root or a command.

Command admission requires a configured data bearer token. Existing
authentication and task-scoped mutation authorization remain authoritative.
The configured data/control principals can act on the approved repository
lanes; this policy is **not** per-principal isolation between clients sharing
one bearer. Do not approve a lane for an untrusted principal. Remote callers
can select only the receiving coordinator's approved local lanes, never send
their own root or executable.

Each approved root supplies the exact shared `.agent-telemetry/config.json`
and optional gated `config.local.json`. Their parser, precedence and local
disable are unchanged. Valid local disable wins even without override
permission. Invalid, disabled, unavailable or unconfigured roots do not borrow
another route or export to a legacy sink.

## Actual producers and lifetime

The existing HTTP and MCP lifecycle/fence producers call `telemetry.emit_for`
with their coordinator's existing `EventBus` owner. This does not subscribe a
new bus or synthesize missing instrumentation. Relevance, periodic relevance
and verification keep that same owner for their existing telemetry offers.

Task observations select the route for the task's stored, immutable canonical
repository. Interleaved tasks therefore retain their approved origin through
later transitions, without retaining a caller's request context. An unknown
repository has no route, even when a service root exists.
Producer-fence observations use **only** the explicitly admitted service root:
rejected or taskless requests cannot borrow a caller's claimed repository
identity. Without that service binding those observations are unavailable.

Selections are snapshotted at lifespan start, before ordinary producers run.
There is one source instance, sequence and existing bounded writer per
coordinator lifetime, with at most four route generations and one reused child
per active route. The existing aggregate two-second shutdown budget drains and
retires children after the coordinator's background producers settle.
Startup failure and cancellation enter this same cleanup boundary.
Receiver failure is terminal for that route; normal task results and output
remain independent. A new coordinator lifetime creates new source/route IDs;
it does not replay prior telemetry. Live reconfiguration is not supported.

The child uses literal argv, the admitted canonical root as cwd and the strict
shared OS-prerequisite environment. `HARNESS_*`, bearer tokens, task/session
identities and arbitrary environment variables are not inherited. No
elevation, shell evaluation, detached child or per-event process is requested.

## Schema and support discovery

The envelope is `consumer-telemetry-command` version 1 over `command-jsonl-v1`,
with source `agent-dispatch` and the closed `dispatch.lifecycle.v1` projection
defined in the shared contract. It is not OTLP. It excludes titles, content,
repo/source strings, native IDs, workers, paths and trace context. Actual
attempt counts, fence generations and replay flags retain their source meaning;
sequence identifies a source observation, not a deduplicated operation attempt.
Missing context stays absent.

The selected executing package carries
`agent_dispatch/metadata/consumer-telemetry-contract.json`.
The existing authenticated `/health.consumer_telemetry_contract` advertises
the running generation. Both advertise **only** `dispatch.lifecycle.v1`,
not Bridge's schema. `/health.consumer_telemetry` returns content-free route
states/counters, without configured roots, argv, repository keys or credentials.

Adopt provider-first: verify the selected or running provider and admit the
exact Dispatch schema in the receiver before configuring this route.
Missing/old metadata means unknown support. Bridge-only receivers must not be
assumed to accept Dispatch. Legacy configurations continue on new providers;
older providers do not read this new admission file and therefore do not
enforce its policy. For rollback, withdraw command admission and arrange any
required disable through that older provider's existing configuration before
selecting it. Never claim a mandatory disable is enforced by an old reader.

## Reproducible producer recipe

In a disposable authenticated coordinator, approve a repository root and place
the standard command declaration in that root. Supply an independent receiver
which reads JSONL from stdin, flushes/fsyncs its own output, and finishes on EOF.
Use ordinary `POST /tasks`, then claim/approve/start through the existing API.
A producer-fence rejection exercises the explicit service route separately.
Read the receiver's file independently after owned teardown; a pipe write is
not persistence or backend acceptance.

`tests/test_consumer_telemetry_command.py` executes this normal policy loader,
real HTTP mutations and real receiver subprocesses. It verifies two interleaved
origins plus a service route, literal Unicode argv, cwd after `chdir`,
environment/privacy, strict opt-out, failed/nonreading receivers and new-source
restart. The existing contained Windows/Linux native workflow runs this packet
alongside the unchanged Bridge custody controls. These are finite disposable
fixtures, not an installed-service rollout or complete U3 qualification.
