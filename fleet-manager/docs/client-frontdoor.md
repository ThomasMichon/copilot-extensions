# Owned client command registration

`fleet-manager client-state register-commands` registers version-pinned client
commands in an **explicit, existing, private command directory**. It never
chooses a command directory, edits PATH or shell profiles, creates a service,
obtains credentials, changes runtime selectors or admits another release.
This is one managed-client prerequisite, not a complete install/uninstall or
permission to roll out to a live machine.

```text
fleet-manager client-state register-commands --root <private-installation> --identity identity.json --selection selection.json --release release.json --proof proof.json --policy policy.json --sources sources.json --staging-parent <separate-private-stage> --target target.json --command-directory <separate-private-commands>
```

The same selected client must already be explicitly [admitted](client-admission.md),
[constructed](client-construction.md) and [selected](client-runtime-activation.md).
The owning client requires the same release-verification and construction extras
used for fresh authentication and local health. The Python entry point is
`register_client_frontdoor(..., command_directory=..., current_policy=...,
current_target=...)`; the suppliers provide current trusted policy and target
configuration, not cached verification reports. The CLI rereads those files at
checkpoints.

## Commands and ownership

The directory must be absolute, ordinary, privately owned and separate from the
installation and staging roots. Registration does not repair permissions,
overwrite foreign commands or adopt same-byte replacements. POSIX publishes
`fleet-manager`; Windows publishes the PowerShell primary `fleet-manager.ps1`
and `fleet-manager.cmd` compatibility entry point. Invoke these explicit paths;
PATH integration is not part of this command.
The PowerShell script carries a UTF-8 BOM for Windows PowerShell 5.1.
The batch script is ASCII-only and resolves its sibling through `%~dp0`, so
neither wrapper depends on a code page being able to encode installation or
command-directory names.
The compatibility command and registration's PowerShell probe request
`-ExecutionPolicy Bypass` for that child process, matching the shared installer
wrapper convention. They do not change persistent execution policy or override
Group Policy. A caller whose current PowerShell host disallows local scripts
can use the compatibility command or start the primary with that explicit
process option.

Each command selects the exact completed candidate's interpreter, using
`-I -S -B` and the canonical slot-local retention bootstrap before importing site
packages. Construction packages that bootstrap and the shared runtime family
from the owning wheel's SHA-256 RECORD resources **before** fingerprinting and
completing the candidate. Existing immutable candidates without it refuse
registration; they are not patched in place. The bootstrap holds the existing
shared runtime retention lease through the client's process lifetime.

Registration holds installation ownership, a SQLite transaction, candidate
construction/retention and selector publication ownership while checking the
exact durable admission, current policy, compatibility, actual completed bytes
and physically attributed active selectors. It runs the installed client console
and local diagnostics before publishing, then runs diagnostics through every
registered command before acknowledging success. These checks do not contact a
controller or activate a hosting role.

The canonical `CopyPublication` primitive owns command staging, physical
created-descriptor proofs, target-writer leases and caught-failure copy recovery.
The client retains its native context, receipts and backups in
`client-runtime/client-frontdoors/<id>/`. `client-frontdoor.json` binds that
attempt to the installation's active receipt, command directory and compatible
target digest. Byte equality alone does not prove ownership.

An exact rerun freshly revalidates policy, compatibility, runtime bytes, selectors,
command ownership and actual command health without rewriting the commands or
registration receipt. A newly active admitted candidate may replace only the
previous registration's physically proved command family in the same directory.
Both candidates and all old registration evidence remain intact. Runtime-selector
activation alone does not update an existing command registration; explicitly
register the newly selected candidate too. Neither transition runs garbage
collection or authorizes deleting a candidate referenced by a registration.

## Failures and remaining gates

Before any command publication, a durable `client-frontdoor-pending.json` names
its exact intended binding. Command writes are individually atomic, not
simultaneously visible as a family. Failure before registration acknowledgement
uses canonical caught-failure rollback when its output custody is still known.
Foreign replacements are preserved; failed recovery is not success. An
acknowledgement failure can leave the new commands in place.

Any interrupted publication/acknowledgement reports an **unknown outcome** and
retains pending state and evidence. Further registration refuses until an
owned withdrawal reconciles it as described below. Registration does not infer
success from matching text or silently restore an older release.
Corrupt or foreign proof refuses. A pending attempt's durable created-object
prefix can authorize only the explicit withdrawal below, never registration
reuse or forward completion.
The accepted admission floor, user configuration and runtime selectors are never
cleared by registration. A private root does not isolate state from a hostile
process running as the same OS principal.

The `fleet-manager.client-frontdoor-registration`, version `1`, report includes
the identity, admitted selection, candidate, exact command paths, local health,
`active`, `frontdoor_registered`, `reused` and `mutations_performed`.
`path_modified` and `live_readiness_verified` remain `false`. A success report is
not a retained lease, remote grant or fleet-readiness claim.

## Owned withdrawal and retry

Use an independently installed owner command, not the command being removed:

```text
fleet-manager client-state unregister-commands --root <private-installation> --identity identity.json --command-directory <separate-private-commands>
```

The Python entry point is
`fleet_manager.client_command_removal.unregister_client_frontdoor(...)`.
Withdrawal needs the existing initialized installation identity and the same
explicit private command directory. It does not authenticate or execute a
candidate, and therefore does not require a selected-release record, artifact
source, current signing policy or target configuration. Expired/revoked release
authority cannot grant new execution through withdrawal; it also need not
prevent withdrawing already-owned commands.

The owner validates the durable admission database and runtime-namespace owner,
then the registered and pending intents and their canonical copy contexts.
Registered publications still require complete created-descriptor receipts.
For a pending attempt only, withdrawal can read an unfinished receipt's complete
newline-framed prefix, including caught-failure recovery receipts. Each original
producer appends and syncs its physical created-descriptor record before the
corresponding command rename. A missing finish record or incomplete final record
therefore does not erase the authority in earlier complete records, but supplies
no proof for the interrupted record itself. Every complete record, producer file
identity, root, target family, sequence and declared content digest must still
validate. Original receipt bytes and unfinished tails remain unchanged.

A pending update whose failed health restored physically proved old commands
can also be withdrawn. Every present command must match one of
those physical proofs; matching text, a saved pathname, an unrelated command
or a same-byte peer replacement supplies no deletion authority. The whole
command family is checked before the first removal.

Under installation, SQLite, selector-publication and target-writer ownership,
`client-frontdoor-removing.json` durably snapshots both registration references
before any command is removed. The canonical native recovery primitive uses a
separate **empty-backup removal context**, never the original copy backups:
withdrawal deletes the proved commands rather than restoring an older executable.
All intents, copy sources, backups, physical proofs and removal outcomes remain
under `client-runtime/client-frontdoors/` and `client-command-removals/`.
Acknowledgement staging is attempt-local, so a failed registration acknowledgement
does not leave a shared temporary filename blocking the next explicit registration.

Registration refuses while withdrawal is unsettled. If the owner dies or
settlement fails, repeat the same `unregister-commands` operation. A fresh owner
rereads retained intent and proofs, tolerates already-absent commands, and
preserves any new foreign replacement. Only after the complete command family
is absent and directory metadata is synced are the registered/pending references
and the removal marker cleared. An exact repeated withdrawal checks absence
again and performs no new removal. A pending intent with neither copy context
nor forward receipt contributes no new ownership proof, as before its first
native copy effect. Once either file exists, missing required peers, an empty
or incomplete producer header, invalid complete records and unproved present
commands refuse before deletion. There is no inferred or manufactured ownership
proof. A header alone authorizes no deletion: a present command still
needs an earlier complete registered proof or another valid created record.
Without such a proof, only already-absent targets can settle.
This command recovers by **withdrawal**, not
forward completion of an interrupted registration; it never marks an unfinished
publication successful.

The `fleet-manager.client-command-unregistration`, version `1`, report includes
the installation identity, unchanged admitted receipt, exact command paths,
`commands_absent`, `frontdoor_registered`, `reused` and `mutations_performed`.
`pending_registration_withdrawn` explicitly identifies settlement of a pending
attempt rather than successful completion of that registration.
Runtime selectors, completed/failed candidates, configuration, user data,
backups and the accepted sequence floor remain untouched. This does not stop
an already-running client or release its runtime retention lease. No PATH or
profile is changed, no service is contacted and the installation identity is
not retired.

After command withdrawal, [owned selector withdrawal](client-runtime-activation.md#owned-selector-withdrawal)
is a separate explicit transition that still preserves all runtime bytes.
Exact [runtime deletion](client-runtime-activation.md#completed-candidate-removal)
requires settled command and selector withdrawal and preserves other candidates.
Retained-good rollback, recovery without an attributable producer/context prefix,
identity retirement and reinstall fencing, complete lifecycle orchestration and
controller/node assembly remain separate obligations. Do not erase admission
or retained evidence while candidates or commands can still be replayed.
Release promotion and an authorized rollout are also separate from merging source.
