# Phase 5 Wave 0 - Effective Configuration Consumer Contract

- **Status:** Reviewed through #6713; shared interaction landed through #6719.
  Native Repositories integration and installed acceptance remain open.
  Not yet an implemented interface.
- **Parent:** [Configuration workbench plan](phase-5-configuration-workbench.md)
  in the [Manager control-plane effort](README.md).
- **Coordination:** #6711 under #357; shared backstage and Workers remain #6379;
  remote worker targeting remains #6283.
- **Intent:** Closes the existing [Configuration vision](../../../visions/installer/configurator/README.md)
  effective-state, machine-local-adjustments, related-repositories-and-venues,
  read-only, consent, and refresh requirements. No vision expansion.

The design and implementation ordering below are **agent-recommended**
realizations of the reviewed Phase 5 direction. This document does not authorize
rollout, source maintenance, repository creation, or agent activation.

## Inventory: existing behavior, not missing work

These are source findings, not claims about a published or installed version.
The backstage implementation merged in #6427 already supplies the shared shell.
The earlier wave checklist predates it and must not trigger a second shell.

| Existing seam | Source and consumer | Consequence |
|---|---|---|
| Version-1 contributions | [Contract](../../../worktree-manager/docs/plugin-contribution-contract.md); [`parse_manifest` / `_parse_actions`](../../../worktree-manager/src/worktree_manager/plugin_contracts.py); production [`pivot_manifest`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/pivot_manifest.py) | Reuse `placement: config`, explicit account/global scope, columns, cards, row conditions and forms. Keep both parsers aligned. |
| Responsive backstage | [`engine_configuration`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_configuration.py) and [`_open_cfgmenu`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_pivot_actions.py) | Navigation/loading precede reads; legacy one-shot commands remain inert until explicitly invoked. No new navigation surface. |
| Row-to-form execution | [`_open_pivot_form`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_pivot_actions.py), [`ConfigurationFormScreen`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/configuration_form.py), [`format_form_template`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/pivot_actions.py) | `fields_from` and `body_from` supply fields/effects; substitution is single-pass. Cancel has no task draft or save. Current confirmation shows collected values plus previously loaded prose, not a fresh provider-computed preview. |
| Bounded execution and receipts | [`_submit_configuration_form` / `_run_configuration_command`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_configuration.py); [`PivotRuntime.run_resolved`](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/tasks.py) | Off-render subprocesses, timeouts, complete stdout/stderr and originating-scope refresh already exist. Exit status determines Completed/Failed; the Manager does not interpret domain activation success. |
| Real owner-written local configuration | [Workers manifest](../../../plugins/agent-dispatch/pivots/workers-config.json), [`workers_catalog`](../../../plugins/agent-dispatch/src/agent_dispatch/workers_catalog.py), [`save_instance`](../../../plugins/agent-dispatch/src/agent_dispatch/worker_instances.py) | Existing rows distinguish sources, drafts, publication and observation; save checks revisions and collision/retry rules, with separate activation outcomes. Do not replace this with a generic settings store or take over #6379. |
| Repository registration and account preference | [`repos_cli`](../../../plugins/agent-worktrees/src/agent_worktrees/repos_cli.py), [`repos`](../../../plugins/agent-worktrees/src/agent_worktrees/repos.py) | `repos list --json` and `repos copilot-account-for` read native registration/preference. `repos copilot-account set/unset` owns the existing local override; it is not a revision-checked preview/apply protocol today. |
| Route and workspace truth | [`related_cli`](../../../plugins/agent-worktrees/src/agent_worktrees/related_cli.py), [`related`](../../../plugins/agent-worktrees/src/agent_worktrees/related.py), [`state_root`](../../../plugins/agent-worktrees/src/agent_worktrees/state_root.py) | `related resolve`, `state-root --json`, `state-root --pair --json`, `config-root --json`, and `coordination-readiness` remain owners. A resolved state anchor is not permission to edit it. Existing `related` mutation commands can write versioned policy and are not local UI writers. |

The existing [backstage tests](../../../worktree-manager/tests/production_picker/test_configuration_backstage.py)
and [configuration-action tests](../../../worktree-manager/tests/production_picker/test_config_actions.py)
are prior art, not evidence that the proposed additions have passed. Inspection
found no generic post-input provider-preview step. Source-provenance columns
and revision-checked Workers saves do not by themselves establish that step.

## Minimal read contract: provider rows, no new registry

Use ordinary version-1 list rows, columns and detail cards. A provider may add a
`configuration` object to a row for CLI consumers and build `card.body` from that
same owner-derived data; the Manager need not parse every domain's schema.
Required semantics for providers adopting this design:

| Data | Meaning |
|---|---|
| Target and owner | Attributable provider/installation, exact repository or resource, invoking local machine, scope, and an opaque configuration revision. A label is not identity. |
| Effective state | Setting key, effective display value, configured validity, and observed running state separately. Unobserved is unknown, not healthy/running. |
| Contributions | Ordered source identity, kind, revision, display value, precedence and whether it wins or is masked. Provider computes precedence; Manager never merges files. |
| Local authority | Exact supported set/reset operations, editable keys, local destination description, safety constraints and unsupported-operation reasons. Every repository, knowledge, plugin and preset source is read-only. |
| Findings | Stable code, severity, affected setting/source and remediation. Unreadable composition is an error/unknown state, not an empty valid configuration. |

Distinguish **masked by an override** from **redacted for confidentiality**.
Non-secret masked declarations remain inspectable. A provider redacts secrets
before returning rows, cards, fields, previews, errors or receipts; neither
secret values nor credentials travel in action argv. This first contract supports no
secret editor. Do not populate a secret as a field default and rely on the
terminal to hide it.

Browse, refresh and preview do not mkdir, persist drafts, write configuration,
ensure daemons, reserve tasks, or launch agents. Keep reads usable without a
maintenance pool or helper service. A required helper's absence produces an
explicit section finding, not a second service started by the Manager.

## Missing interaction: optional provider-computed form preview

Retain `kind: form`, existing field definitions and `confirm: true`. Propose one
optional action field, `preview`, containing a command argv, on
Configuration-placed row forms only. It uses the same attributed command
resolution and single-pass collected-field substitution as `run`. No new action
kind, manifest registry, general YAML writer, or resident process is needed.

Existing static confirmation is adequate for existing forms; do not retrofit
them silently. The new step is necessary when validation, inherited values,
reset effects or target availability depend on the values just collected.
For an action declaring `preview`, `confirm: true` is mandatory:

1. Collect fields, then invoke the bounded read-only preview off the render
   flow. Show a pending state immediately; do not submit `run` yet.
2. Parse a versioned JSON result: `version: 1`, nonempty string `token`, `target`,
   `scope: machine-local`, `operation: set|reset`, `summary`, `effects` (array of
   strings), and `findings` (code/severity/message string records, severity
   `error|warning|info`). `summary` is a string; `target` is an object with
   nonempty string `installation`, `machine` and `resource` identities. Adopting
   rows supply that same `configuration.target` object; a mismatching returned
   target never retargets the action. Unknown version, malformed fields,
   blocking findings, timeout or nonzero exit leaves Apply unavailable,
   with a retryable error. Warnings remain visible in confirmation.
3. Show that provider result and collected non-secret values, with Cancel
   selected. Changing values, section, project, target or action invalidates
   the preview; late results cannot authorize a newer editor context.
4. Only explicit confirmation runs the original action. The host adds a
   reserved `{configuration_preview}` substitution containing the opaque token,
   distinct from row context and editable fields. The provider must require it
   for this new mutation path; a row or field cannot supply/override it.
5. Report the actual command result through the existing full-content receipt,
   then refresh the originating scope. Nonzero/partial failure remains Failed
   with any persistence outcome explained, never successful convergence.

The token is a cheap stale-proposal check, not an authorization credential or an
attestation system. The provider binds it to exact target/installation,
operation, normalized values and all relevant local/source baselines. Under
its existing mutation serialization, apply re-reads those inputs and validates
authority and safety constraints before writing. A changed baseline returns a
conflict and requires a new preview/consent, never an automatic rebase or retry
with broadened effects. No persistent preview-ticket store is required.

Reset is an explicit owner action: remove the supported local override and
recompute inheritance. Do not write the current inherited value into the
override store. If that owner has no reset or local writer, expose only
inspection and the reason; never substitute a versioned-source mutation.
Enable/disable/killswitch are domain operations, not synonymous with repository
participation, plugin enablement, registration or process health.

### Compatibility

The manifest addition is optional within version 1, subject to alignment of the
contract parser, production parser, materialization and execution path. It does
not change existing fields' types or action-kind vocabulary.

| Combination | Behavior |
|---|---|
| New Manager, legacy provider | Existing cards/forms/static confirmations work unchanged; do not label them provider-computed previews. |
| New Manager, adopting provider | Require validated preview and reserved token before the new write path. Unsupported preview on another action/placement is a contract finding. |
| Old Manager, adopting provider | Existing formatters substitute unknown tokens with empty text. The provider must reject a missing/empty/invalid token before any write; an old host may display the action but cannot perform it. Do not rely on an old parser noticing the optional field. |
| Missing/incompatible provider | Disable only the affected surface with an update/repair finding; peers and Profiles still work. |

Provider text/JSON receipts stay provider-owned; no generic activation-receipt
parser is proposed here. A CLI must return nonzero for a failed requested
mutation, even if a partial result was persisted, and explain that partial
result. Do not change existing Workers lifecycle semantics in this slice.

## First real consumer and owner boundaries

The first production vertical is an **agent-worktrees Repositories section**:
native registration, related-route, state-root and pair inspection, with one
bounded machine-local preference: the already existing per-repository
`copilot_account` override. Its set/reset restores the actual native
`copilot_account_for` inheritance chain. This changes a preference for future
launches; it does not log in, switch global `gh`/Copilot state, start a session,
mint credentials, or move existing workers. Show the identity-switch policy
separately; preference does not imply that policy is enabled or auth is ready.
Bind every query and mutation to the selected project/resource explicitly,
not an inherited working directory or the operational pivot's hidden machine.

Before enabling writes, the engine owns a thin catalog/preview/apply adapter
around these native semantics. Extend the existing registry writer with shared
serialization and revision checking for the affected mutation paths, rather
than implement a Manager-side writer. Ordinary concurrent registry commands
must not bypass the conflict fence. Failed default/composition reads cannot be
translated into an apparently valid empty inherited identity.

| Owner | Remains responsible |
|---|---|
| Manager | Discover/render contributions, strict preview parsing, bounded async invocation, context invalidation, consent, literal token transport, receipts and refresh. |
| agent-worktrees | Native registry, preference inheritance/writes, related-route resolution, config/state-root and tracked-pair validity; local operation revisions and conflict handling. |
| Binding capability | Select/validate/bind the knowledge repository using its existing canonical registration, privacy/account and workspace rules. No generic upstream binding writer is implied by a `state-root` read API. |
| Adopting policy plugin | Supply its read-only policy/provenance and supported local binding controls. An absent contributor leaves binding inspection available and editing explicitly unsupported. |
| Existing domain owners | #6379 keeps backstage/Workers lifecycle and installed acceptance; #6283 keeps remote targeting. SSH/container panels, presets and maintenance retain later-wave ownership. |

The Repositories section must distinguish participation, enablement,
registration, route preference, bound knowledge identity and the actual paired
writable checkout. Route/pair editing remains unavailable until the respective
owner exposes a supported revisioned local writer; no edits through
`related add/update`, repository files or an inferred venue. Existing leases and
workers are untouched. This first consumer is not completion of all Wave 2.

Reconcile #543's earlier direct `enabledPlugins` source-write request with this
boundary: source inspection is supported; local enablement controls require the
activation owner's actual override API, and versioned changes require the
separately authorized maintenance flow. Do not close #543 as implemented.

## Ordered follow-on slices and validation

Claim each implementation through its owning issue before editing. The design
merge closes only #6711, not #357 or the parent Configuration acceptance gate.

1. **Shared interaction (Done — #6719).** Implement the optional preview on the existing
   Configuration form path, with a synthetic owner CLI and isolated local
   override store. Prove real UI-to-subprocess browse/preview/cancel/set/reset,
   invalid/stale proposals, concurrent edits, output failures, late results,
   reserved-token isolation and old-host refusal. No production domain is
   migrated merely to exercise the host.
2. **Repositories consumer (In progress - #6721).** Add the native owner adapter and contributed
   section. Prove actual `copilot_account_for` changes after set and restores
   inheritance after reset in redirected roots, including a concurrent ordinary
   registry CLI write and invalid composition. Exercise registration, route and
   pair inspection for present, absent, stale and unsupported states without
   creating a repository/worktree, switching an account or launching a session.
3. **Owner-specific continuations.** Claim local venue/binding controls only
   after their writer contract exists. Keep unsupported operations visible, and
   record the exact remaining gaps rather than inventing new settings keys.

For both implementation slices, hash and compare the complete synthetic
repository, knowledge, plugin and preset source trees before and after browse,
refresh, preview, cancel, successful local set/reset and rejected writes.
Assert the exact allowlisted local destination changes and no others. Reset
must restore inheritance, including after the inherited source changed.
Use sentinel secrets to prove that rows, fields, previews, stdout/stderr,
receipts and argv contain no confidential value; do not only mock past the
provider's redaction.

Extend existing contract/backstage/configuration-action and native preference
tests; keep the required CI smoke lane scoped. Windows and Linux real
subprocess checks cover invocation and compatibility. Fresh isolated first
touch covers absent directories/provider/daemon and rerun behavior; if
installation/provisioning changes, add the applicable clean-room lane.
After normal promotion, authorized live local UI acceptance must identify the
actual Manager/provider builds, show responsiveness and refreshed results, and
compare source bytes. Record explicit reasons for unavailable/inapplicable
tiers; a fixture screenshot or design review is not installed proof.

The design review through #6713 ran documentation/effort/link guards. The shared
interaction landed through #6719 now exercises the production Textual editor,
preview, consent, receipt and refresh against a real isolated owner CLI on
Windows. Focused contract/backstage/action/runtime compatibility checks pass,
including source-tree comparisons, reset after inherited-source change,
serialized competing applies, stale-baseline refusal, reserved-token isolation
and sentinel-redacted provider output/argv. The two cross-distribution Workers
round-trips remain opt-in and are not evidence for this new interaction.

The synthetic first-touch lane creates no override while browsing or previewing;
only explicit Apply changes the allowlisted local file. No installer/bootstrap
path changes, so another installer clean-room run is inapplicable. Required CI
on Ubuntu 24.04 passed the Manager suite (2,229 passed, 25 skipped) in run
`38592321480` before merge. Production Repositories integration, promoted/installed
build identification, authorized live UI acceptance and documentation journeys
remain open; fixture UI does not close them.
