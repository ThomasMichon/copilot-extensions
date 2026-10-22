# Admitted client construction

`fleet-manager client-state construct` constructs and locally checks a supplied,
durably admitted client release. It never selects a release, advances admission,
publishes an active selector, installs a service or obtains credentials.
It is a construction prerequisite, not a complete managed install/update/uninstall
flow or evidence of Gateway connectivity.

```text
fleet-manager client-state construct --root <private-installation> --identity identity.json --selection selection.json --release release.json --proof proof.json --policy policy.json --sources sources.json --staging-parent <separate-private-stage> --target target.json
```

First explicitly initialize and admit the selected release through
[client admission](client-admission.md). Construction refuses an unadmitted,
different or lower selection; it does not silently perform those transitions.
The owning client needs the explicit `release-verification` and `construction`
extras, its RECORD-admitted lifecycle resources, and standard-library `venv`.
The construction extra supplies the maintained pip tool in the owning runtime,
not in the candidate. Setup of that extra belongs to the normal qualified
package/setup authority; construction never installs it implicitly.
The existing POSIX process guardian also needs the operating system's `ps`.
No package index, build backend, Docker, controller or Copilot process is used.

## Explicit compatibility and supported artifacts

The trusted target input describes the API and every data schema used by
current configuration and retained state:

```json
{
  "schema": "fleet-manager.client-target",
  "version": 1,
  "authority": "example-authority",
  "installation": "example-client",
  "revision": 1,
  "components": {
    "cli": {"api": 1, "data_schemas": [1]}
  }
}
```

Component names must exactly cover the selected client closure. API values
must fall within each authenticated declaration's range, and all supplied
config/retained-data schemas must be supported. Missing, unknown, duplicate or
incompatible inputs refuse before candidate execution. This is compatibility
against explicit trusted inputs, not discovery of a server or a data migration.

This first constructor supports the existing single, pure-Python
`fleet_manager-...-py3-none-any.whl` client distribution. Other closures,
source/native artifacts and distributions refuse explicitly. Installation is
offline with `--no-index --no-deps --no-compile` through that exact owner's
`pip --python <candidate>`; no pip bootstrap or hosting extra is installed in
the dependency-free candidate. The installed
dependency check must pass. A future multi-wheel resolver or hosting lifecycle
is not implied by authenticated staging's broader component support.

The Python API is `construct_client_release(..., current_policy=...,
current_target=...)`. Both suppliers belong to the trusted caller and must
return current inputs, not historical verification reports. The CLI rereads
the supplied policy and target files at checkpoints. Construction rereads the
exact installation/admitted selection under exclusive ownership and rechecks
authentication and compatibility before executing candidate commands and before
completion. A valid signature or a newer release does not replace selection.

## Candidate ownership, health and retry

The installation owns `client-runtime/client-owner.json`, bound to its exact
initialization identity. Foreign or linked namespaces refuse. Candidates use
the canonical `versions/client-<sequence>-<manifest-sha256>` layout.
Construction, retention, admission, failed-candidate discard, source fingerprint
and completion all use the installed canonical `versioned-runtime` resource,
not a second installer protocol.

Each candidate's private input receipt lives under `client-runtime/client-inputs`,
outside the removable slot. It binds installation identity, selected decision
and decision digest. This placement matters: a failed native removal can
delete part of an unfinished tree without erasing its only ownership evidence.
Unknown candidate contents without that receipt refuse rather than becoming
an adoptable failed build.

Authenticated immutable snapshots are copied into the candidate, never executed
from the ephemeral stage. A copied, separate venv receives only the selected
wheel. Fixed probes run its actual console `--version` and `doctor --role client`
outside the checkout, plus the offline installed dependency check. Their
environment excludes inherited credentials and uses a candidate-local home.
The trusted base interpreter and its standard library remain prerequisites;
this is not a hermetic interpreter or hostile-code sandbox.

The canonical installer child supervisor bounds execution and settles its
descendants. Its fixed helpers use one retained owning-SDK RECORD snapshot,
with helper bytes rechecked before every child; this is separate from fresh
release-policy authentication. POSIX retains the construction descriptor in its existing guardian.
On Windows a finite guardian independently retains the candidate until its
native Job settles, including after caller death. Losing a caller's
construction lock alone therefore does not admit destructive cleanup while
that guardian still owns retention. This is finite caller-owned supervision,
not a new background service.

After health, `client-constructed.json` binds the input receipt to the actual
candidate file fingerprint. The canonical completion marker binds that receipt
digest. The two completion records themselves are excluded from the tree
fingerprint. Reuse checks the stored binding and actual bytes, then freshly
repeats policy, compatibility and health checks without rewriting completion.
Altered completed content requires distinct-generation repair, never an
in-place rebuild.

Failed construction or health leaves an unpublished owned candidate for an
explicit, bounded retry; it does not mask the original failure with eager
recursive cleanup. Retry freshly validates intent and uses canonical
failed-candidate discard before rebuilding. Foreign state, active/retained
content and cleanup failures refuse. Partial discard preserves the external
input receipt. The durable accepted floor and previous active selectors,
retained-good references and user configuration are never reduced or rewritten.
An uncertain completion outcome requires a fresh owned retry/reread.

## Reports and remaining transitions

A successful `fleet-manager.client-construction`, version `1`, reports
`constructed: true`, `healthy: true`, the candidate/runtime fingerprint,
current target digest, local health result and whether it reused a completed
candidate. `active`, `activation_authorized` and `live_readiness_verified`
remain `false`. A failure exits nonzero; partial owned files can remain.
The report does not convey a live retention lease or execution/activation grant.

[Owned runtime activation](client-runtime-activation.md) separately reacquires
ownership, revalidates current policy, selection, compatibility and health, and
publishes physically attributed local selectors. Frontdoor registration,
removal, retained-good recovery, identity retirement, controller/node assembly,
artifact publication and authorized rollout remain separate gates.
