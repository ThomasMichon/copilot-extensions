# Explicit standalone report hosting

Back to the [canonical effort](README.md), [native report owner](native-report-owner.md)
and [size-qualified response contract](bounded-report-response.md).

**Agent-recommended source contract reviewed in #11705 under #11692 and #6007.
Finite source implementation and qualification merged in #11712.
This is not enrollment or rollout authority.**
This closes part of
[one service contract](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract)
and follows
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
Existing provider, credential, configuration, Gateway and lifecycle owners remain.

## Measured gap and finite choice

The optional report reader, selected GitHub.com adapter and v2 transport compose
through source embeddings. The normal foreground owner is
`services/pr/src/agent_pull_requests_service/hosting.py`. Its strict `HostConfig`
cannot select reports, and `WatchService` neither accepts report readers nor
passes a selected response ceiling to its actual connector handler.
An isolated Gateway fixture is therefore not normal-host adoption.

Extend those existing host seams with one explicit optional `reports` object.
Construct the existing `NativeGitHubReport` and `ReportReader` in the selected
service process; pass them through `WatchService` to the existing Gateway.
Do not add another executable runner, credential issuer, watcher, listener,
database, enrollment controller or report dispatch path.

## Strict trusted configuration

The proposed optional object has exactly `response_bytes` and `bindings`.
`response_bytes` is an explicit integer from the existing 32 KiB baseline
through the existing 8 MiB transport ceiling. Booleans and out-of-range values
refuse. It selects the actual handler ceiling and each configured reader's
v2 ceiling, not a caller-granted allowance. At 32 KiB only v1 is available;
above it v1 retains its old bound and v2 uses the selected finite ceiling.

Each entry in the existing bounded collection selects exactly:

| Field | Owner and meaning |
|---|---|
| `principal`, `target` | One existing admitted caller/target binding; no duplicates |
| `source_config` | Absolute reference to the existing Worktrees configuration owner |
| `scope` | Exact report `provider`, `endpoint`, `account` and `repo` |
| `context_id` | Explicit report-owner context label, not a credential or grant |

Use the existing host JSON, path, collection and report-scope validators.
Unknown/duplicate fields, missing entries, scope mismatch or an unsupported
provider/endpoint refuse before credentials or provider work. Account/repository
must agree with the admitted binding. The initial adapter remains GitHub.com
only; no Gitea, ADO or Enterprise fallback is selected.

This is trusted launch configuration, never request JSON, a header, environment
override or subscriber input. It selects no arbitrary executable, token command,
new credential reference or configuration-selected import path.
`source_config` identifies the existing native policy owner; it does not make
the host's routing labels a substitute for that owner's current target/account
resolution and authenticated selected-account checks.
The observation credential worker retains its separate watch-only duties;
its credential cannot be borrowed or relabelled as the report credential.

Configuration loading and provisioning must not acquire report credentials,
authenticate an account or read a provider. Native resolution remains on demand,
within the actual admitted request. Preserve the existing strict native
selection, original owner exit and complete report without retaining secrets,
identity responses or report bodies.

## Executing support, lifetime and storage

The selected process must actually contain the optional Worktrees native owner
and compatible report/SDK APIs. Missing selected support refuses the optional
host configuration before connector admission, without probing credentials.
Do not infer support from package versions or require unrelated installations
to upgrade together. Default hosts neither import a required Worktrees owner
nor silently install one; explicit native-owner packaging remains a launch
owner responsibility through the existing distribution mechanism.

The response selection must reach the `HandlerBinding` used by the real
`ServiceConnector`. V2 advertisement and reads still require the actual
executing SDK request's admitted ceiling. A matching config value alone is not
support, and an old/local handler must refuse before report acquisition.
No larger limit applies to watch/workspace responses or ordinary v1 reports.

Reuse `LaunchScope`, the host operation budget, `WatchService` writer,
Gateway task set and connector drain. Configuration/credential/identity/report
work stays off the event loop under the original request checkpoint and one
finite budget. Stop, expiry, disconnect or cancellation prevents later reads
and publication; native cleanup and response delivery retain their original
owners until actual settlement. No startup authentication, background retry,
second acquisition or per-stage deadline reset is introduced.

Reports are transient read exchanges, not workspace generations or replay
events. No report-specific disk layout, cache, migration or downgrade is needed.
Existing store identity, explicit provision-versus-serve separation, workspace
fresh-provisioning rules and uncertain-drain custody remain unchanged.
The finite qualification and any later separately authorized first enrollment
are fresh-only; this proposal selects no retained deployed configuration or
volume and authorizes no migration, recovery, replacement or resource release.

## Compatibility, validation and finite exit

Absence of `reports` preserves ordinary watch/workspace hosting, 32 KiB handler
defaults, native commands and dependency selection. Existing configuration
version 1 gains only this strict optional property. Older hosts reject it
explicitly rather than ignoring required selection; removing the optional
selection restores the existing baseline without rewriting state.
Expand the host first, select it only where supported, and retire no old route,
native command or configuration form. A v2 client encountering old or rolled-back
support refuses without native, v1 or alternate-forge fallback.

The finite source implementation has focused isolated controls, not live
credentials or retained hosts. Evidence and limits are recorded in the
[implementation journal](journal/2026/11.09%20Standalone%20report%20host%20implementation.md):

- [x] Actual host parser/defaults: valid explicit bindings and boundary ceilings;
  unknown/duplicate fields, invalid paths/types/scopes, unsupported providers,
  missing executing APIs and old-host refusal before acquisition.
- [x] Actual foreground host, `WatchService`, connector/SDK/Gateway and source
  client: selected v1/v2 report delivery, complete larger reports and native
  error/exit evidence, plus ordinary watch/workspace controls.
- [x] Actual native policy and owned-process seams with synthetic credentials:
  no config/provision/startup account probes, request-only selected identity,
  changed policy/account refusal and no borrowed observation credentials.
- [x] Expiry, withdrawal, disconnect and delayed native/delivery cleanup through
  the real host lifetime: no successful partial report, later read, abandoned
  task, early writer release or persisted report/credential bytes.
- [x] Default/prior/new/rollback peers and fresh fixture provision/restart:
  unchanged store/replay identities and workspace rules, no automatic migration,
  implicit dependency install or second dispatch.
- [x] Required affected tests, admitted strict service typing, packaging controls,
  ordinary review/CI/merge and parent reconciliation. Record Linux source
  evidence separately from native Windows, installed hosting, live providers,
  retained-host/container qualification and complete CLI/HTTP/Dispatch parity.

Installed activation, rollout, actual operator configuration/CLI-store access
and production effects remain separately authorized acceptance. A source
implementation or this reviewed proposal cannot close either parent effort.
