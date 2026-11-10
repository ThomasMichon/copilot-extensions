# Installable-service hourly contributions

Parent: [Machine Health Heartbeats](README.md).

**Agent-recommended design amendment: review before implementation.** This
realizes the machine-heartbeat vision's domain-owned observations, diagnosed
hourly errors and bounded independent publication. It does not activate a
publisher or extend any service's operational authority.

## Required outcome

An installable service can describe, by convention, its supported hourly
reporting interface. The machine report owner discovers that support through
the existing installation/configuration owner and composes bounded results.
Adding a service must not require teaching the central collector its private
logs, database layout, credentials or application-specific HTTP response.

The convention works without Copilot, a particular marketplace, a new resident
collector or a running UI. A service may implement the protocol in any language.
The existing dependency-free telemetry library may supply reusable Python
validation/summary helpers; it does not become a reporting daemon.

## Responsibilities

| Owner | Responsibility |
|---|---|
| Service/native adapter | Select its existing telemetry and cached health evidence, reduce private details, and export one supported hourly contribution |
| Installation/configuration owner | Resolve the exact admitted installation, selected executable and its reporting declaration; distinguish installed, configured, running and absent |
| Agent Machines | Bound the one-shot collection, validate contributions, preserve missing coverage, compose the machine report and later publish its own hourly slot |
| Existing telemetry library | Reusable schema/statistical primitives, not another sink, writer, scheduler or inventory authority |
| Carrier | Eventual delivery of admitted summaries only; no service discovery or execution instructions |

Services keep their normal lifecycle, buffers, retention and diagnostic stores.
The collector never starts, ensures, repairs, updates or authenticates them.
Receiving a shared report cannot trigger collection, remediation or configuration.

## Selected-runtime discovery

An optional versioned **reporting declaration** belongs to the service's
installed, selected runtime artifact. Reuse an existing appropriate artifact
descriptor where possible; otherwise use a small adjacent data-only declaration.
Its exact delivery must follow the existing build/installer owner.

The declaration names supported report schema versions and a logical native
export operation. The installation owner resolves that operation to the exact
selected executable. The collector does not search `PATH`, scan home directories,
read the newest marketplace payload as installed capability, evaluate shell
templates, or execute a command obtained from synchronized data.

Discovery reuses the admitted installation/module inventory, rather than a new
mutable machine-wide service registry. Explicit standalone service adapters can
provide the same normalized owner record through their existing configuration
owner; they need not pretend to be Copilot plugins.

Declare support separately from package versions. Old runtimes without reporting
support remain explicitly unsupported; their existing status/log commands still
work. Expand providers and installed metadata before conditionally migrating the
collector. Do not send new flags to an unqualified old executable and replay an
older effectful operation after a parser-looking error.

No producer, reporter, installer or scheduled task is started during discovery.
An installed-but-stopped service can still export retained historical telemetry
offline. Its current running health remains unavailable unless its native owner
actually supplied timestamped evidence.

## Read-only hourly export

The logical operation accepts exactly one absolute UTC hour and emits one
complete, bounded JSON result on stdout, with diagnostics on stderr. It uses
explicit existing noninteractive owner access. It never prompts for or refreshes
credentials, enumerates unrelated resources, or provisions missing dependencies.
Capability selection must prohibit a self-provisioning payload path.

An on-demand export can reuse the service's existing retained-file inventory
and known native observation adapters. A service that already maintains an
hourly snapshot may return it instead. Neither choice needs a second event
buffer, log writer, per-event subprocess or per-service hourly schedule.
Collection must remain independent of software update/restore execution.

Every invocation has an absolute deadline, bounded complete stdout/stderr,
source scan work and result cardinality. A service that is slow, missing,
malformed, unsupported or denied contributes that condition; it does not block
valid independent contributors or become a zero-valued healthy service.
Process exit and inherited stream EOF remain separate observations, with exact
owned-child cleanup. No cleanup targets a process by name, alias or port.

## Common payload

Use an independent schema identifier and integer version, not a package version
or the machine-health record parser. The first protocol must carry:

- The service identity matched to the selected local owner, requested absolute
  hour, source observation times and any native continuity/revision evidence.
  Generation, collection and source observation times remain distinguishable.
- Coverage that distinguishes observed, partial, unavailable and unsupported
  data, including known drop/scan limits and explicitly unknown population.
- Optional typed health observations with their evidence kind and source time.
  A listener/banner never becomes authenticated access, and OS process counts
  never become logical active agents.
- Compatible performance/count groups, preserving units, stage/outcome meaning,
  observed counts and supported statistics from the existing telemetry seam.
- Bounded structured diagnostic summaries: component, reason code, first/last
  source observation, occurrence count, and separately qualified symptom,
  confirmed cause, hypothesis or recovery outcome.

Keep the first schema closed and bounded: strict UTF-8/JSON, duplicate/unknown
field rejection, finite typed numbers, explicit units and timestamps, capped
nesting, strings, metrics, diagnostics and total bytes. No raw log messages,
tracebacks, command arguments, paths, prompts, account/token/key material or
arbitrary label maps. The service adapter owns privacy reduction; a JSON
validator is not a generic secret redactor.

Service-specific metrics use a declared bounded vocabulary with type, unit,
aggregation and scope. Unsupported extensions stay unsupported, not silently
coerced into generic health. Do not add a generic arbitrary-payload escape hatch.
The machine envelope may retain a namespaced contribution, but services cannot
write another service's identity or directly control machine-wide conclusions.

## Aggregation and history

Collect each selected service installation once per requested hour. Keep
installation identity local and distinguish same-named independent instances;
export only the audience-admitted presentation identity. Reject ambiguous
ownership and conflicting duplicate contributions instead of silently choosing
the newest mtime. De-duplicate repeated inputs before counting.

Only source evidence inside its declared hour contributes to that hour's
observations. An export generated now may summarize retained history; it cannot
relabel current health as past uptime. Preserve gaps, delayed observations,
counter resets and unknown loss. A service restart is not a reset of machine
publisher continuity or permission to replace a newer accepted machine slot.

Reuse compatible existing statistical primitives. Never average percentiles,
sum overlapping durations, merge incompatible units or infer availability from
event silence. Keep service groups namespaced and explain observed population.
The machine's existing slot/revision owner handles any later corrected hourly
publication; a service contribution does not create another synchronized writer.

Raw events and detailed diagnostics stay local. The report/chart consumer
works from the admitted contribution data without rescanning private logs.
Service collection is local-first and must still work when the carrier or
Gateway is unavailable.

## Dependency-ordered implementation and acceptance

1. **Review this amendment.** Preserve the operator follow-up and existing
   activation limits in the canonical effort.
2. **Implement the bounded protocol and provider first.** Reuse the telemetry
   library and one existing service's retained inventory/native owner. Cover
   schema/time/coverage/privacy reduction and explicit unsupported capability.
   Do not pre-patch another contributor's still-open producer work.
3. **Prove one complete consumer flow.** Exercise actual service-produced
   retained events, the service's normal hourly export, selected-runtime
   reporting metadata and Agent Machines' normal report/aggregation CLI.
   Fixture-resolved installations remain identified as such; they do not prove
   a deployed host's installed inventory or activation.
4. **Broaden by native ownership.** Add other services and SSH/Gateway/auth/
   session observation adapters, preserving their independent contracts.
   Unsupported/absent/stopped contributors and partial reports remain useful.
5. **Continue the parent.** One-shot native/platform, scheduling, selected
   carrier, installed adoption, actual hourly publication and peer receipt
   remain separate parent gates, not implied by this source convention.

Focused controls must include version skew and absent metadata, two
same-named installations, forged/mismatched owner identity, malformed/oversize
or partial output, timeout and retained-stream cleanup, unavailable providers,
unsupported metrics, duplicate delivery, clock skew and delayed history.
Use pure unit cases for required CI and selected native/subprocess controls for
the actual consumer. First-use fixtures must not touch the operator's installed
services, cloud namespace or credentials.

No source-test result, reviewed design or new installed declaration grants
publisher enrollment, service reload, credential acquisition, real OneDrive
write or an expansion of the adopter's activation cohort.
