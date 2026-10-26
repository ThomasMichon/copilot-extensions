# Reproducer lab: pure boundary controls

This directory implements only the first pure controls from the
[reviewed persistent reproducer plan](../../efforts/active/linux-role-fleet/persistent-isolated-reproducer.md).
It does not create, inspect, start, stop or delete Docker resources. There is no
provisioning CLI, supported storage backend or qualified persistent lab yet.

`boundary.py` provides:

- `parse_declaration(value)`: strict version-1 synthetic configuration, full
  image/Git object identities, separate experiment resource names and positive
  integer budgets. Malformed, unknown and out-of-scope inputs raise
  `DeclarationError` without echoing their payloads.
- `inspect_static_boundary(experiment, observed)`: pure checks on a Docker
  inspect-shaped observation. It identifies known unsafe/missing image,
  identity, namespace, privilege, exposure, CPU/memory/PID, persistent-mount
  and guest-environment fields without invoking Docker or reading host state.

The initial proposed image contract uses non-root `1000:1000`, the fixed HOME
`/home/lab`, PATH `/usr/local/bin:/usr/bin:/bin` and `PYTHONUNBUFFERED=1`.
It has four separately named volume destinations for home, state, workspace and
evidence. Config/runtime/registrar roots will live under the owned state path;
their actual service selection is not implemented or qualified here.
Supported declaration ceilings are 8 GiB memory (equal swap limit), four CPUs,
256 PIDs, 4 GiB persistent storage, 512 MiB `/tmp`, 64 MiB `/run` and 64 MiB
native output. These are tool-side input ceilings, **not enforced storage**.

Every static result retains explicit unresolved requirements, even with zero
violations: real bounded persistent/native-output capacity, actual internal
network membership/egress, exclusive resource/driver custody, guest source and
volatile/runtime configuration, and provisioning/strict pre-effect support.
There is deliberately no `ready` or admission result. A dictionary claiming
verified quota cannot supply a backend implementation or clear these gaps.
Synthetic inputs, expected names and image identities are not physical custody,
running-source or persistence proof.

The next source slice must implement the bounded host reader and qualify an
existing quota-capable backend before any creation path can be enabled.
Restricted-provider policy and ephemeral tests remain unchanged. Real resource
creation, guest lifecycle/fault actions and deletion need separate authorization
through the normal owner. No production credentials, state or network are used.

## Tests

`tools/test_reproducer_lab_boundary.py` is an in-process, synthetic control set:
declaration/budget rejection, static unsafe or incomplete Docker observations,
mount/environment isolation and retained unavailable prerequisites.
Run it with the repository's selected Python/pytest environment through the
caller's bounded test supervisor:

```text
<bounded-test-supervisor> -- <python> -m pytest -q tools/test_reproducer_lab_boundary.py
```

It does not run Docker, open a socket, install dependencies, exercise native
service lifetimes or join the unconditional promotion matrix. See
[`TESTING.md`](../../TESTING.md) for the separate real integration portfolio.
