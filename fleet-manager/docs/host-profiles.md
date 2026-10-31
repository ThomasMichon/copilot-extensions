# Explicit host-profile bootstrap

`python -m fleet_manager.host_profile install|status|uninstall` is an explicit,
stdlib-only Linux-root (UID/GID 0) bootstrap module for one code-owned AppArmor profile.
Importing it or running the ordinary planner/CLI performs no host action.
It is **not** an independent Fleet/Komodo production installer, controller,
deployment adapter, policy editor, daemon or automatic startup hook.

The [role compiler](role-deployment-plans.md) still treats `bootstrap_ref` as an
independently resolved owner requirement. This module can supply one narrowly
scoped host-policy prerequisite; it does not resolve that reference, enroll a
host, create a manager, activate a container or grant deployment authority.

## Fixed ownership boundary

The code-owned `trusted_systemd_profile` module supplies only
`PROFILE_NAME = fleet-manager-trusted-systemd-v1` and `profile_bytes()`.
This module accepts no alternate profile name, policy bytes, path or command.
It does not change Docker, default profiles, container privilege or cgroup binds.
Policy adequacy and native security/runtime qualification remain with that owner.

The policy targets the pinned image's systemd 252 namespace operations: private
container-root cloning and move, propagation changes, exact runtime bind pairs,
read-only unit-root remounts, and memory-backed unit-credential staging.
The required procfs instance is limited to the temporary unit root's `/proc`
with `nosuid,nodev,noexec`; it sees the already-admitted private PID namespace,
and the same global-control denials apply through that alias.
It does not allow arbitrary filesystems or arbitrary source/target binds, even
inside the temporary root. Kernel-control denials also cover temporary root
aliases so namespace setup cannot simply re-path protected files.
The image entrypoint requires `/run` to be a mountpoint and self-bind pins
`/run/systemd` before creating any unit-root aliases. Their mountpoint ancestors
cannot be renamed. Policy denies unmount of `/`, `/run` and `/run/systemd`,
including temporary-root aliases, so a confined process cannot unpin and
relocate those aliases outside the protected paths.

Use it only with an independently admitted private PID/mount/cgroup/UTS venue,
bounded resources and no host control-filesystem or Docker-socket binds.
Systemd may create shared peer groups **inside** that isolated container after
its initial root mount is verified private; the profile does not create those
isolation boundaries. Unit credential staging is systemd's local runtime
mechanism, not an operator login, auth pump or relay grant.

Files are fixed:

- `/etc/apparmor.d/fleet-manager-trusted-systemd-v1.<sha256>`: immutable,
  hash-qualified profile, root:root, mode `0444`, regular and single-link.
- `/var/lib/fleet-manager/host-profiles/`: root-private mode `0700`.
- Its `fleet-manager-trusted-systemd-v1/` child: root-private mode `0700`;
  retained `receipt.json` and `lock` are root:root mode `0600`.

Every ordinary-file read binds the opened descriptor to the named physical
identity, checks ownership/mode/link count and bounded size, and rechecks after
reading. The receipt attributes profile, state directories and lock by device/
inode identities; its own physical identity is recorded before atomic
publication. Symlinks, hardlinks, replaced files, writable/foreign ancestors,
permission drift and mismatched profile bytes are refused. No existing profile
or file is adopted just because its name or contents match.

## Install and observation

```text
python -m fleet_manager.host_profile install
python -m fleet_manager.host_profile status
```

The initial invocation creates exclusive owned state and an immutable profile
file; it never replaces a profile. Receipt publication flushes before external
effects. Each newly created state directory is synced into its parent, and the
lock inode and its directory entry are flushed before policy publication.
Parser validation uses the fixed executable and arguments:

```text
/usr/sbin/apparmor_parser --jobs=1 --skip-cache --skip-kernel-load <owned-profile-file>
```

Only an absent canonical kernel name can proceed to `--add`, with the same
`--jobs=1 --skip-cache` flags. `load-pending` is durable before that call.
Ready means the operation succeeded and the canonical name is observed in
enforce mode with its saved owned file intact. It does **not** prove image
startup, container systemd, cgroup access, credential readiness or fleet health.

A ready repeat install and `status` are observational: no parser call or receipt
rewrite. Status creates no missing directory or lock. Template changes return
`template-drift` without updating installed bytes; uninstall must precede a new
explicit install. A terminal receipt and lock remain usable for those repeats.

## Fenced uninstall

```text
python -m fleet_manager.host_profile uninstall --activation-quiesced
```

The owner must already hold the **actual launch/admission fence** preventing
new profile users throughout removal. The flag merely records that assertion;
it neither verifies nor grants the fence, privilege or deployment authority.
Process inspection is not an atomic kernel admission barrier.

Every thread label under `/proc/<pid>/task/<tid>/attr/current` must be
inspectable and none may use this profile, including stacked/child labels.
Process and aggregate thread enumeration are each bounded at 131,072 entries.
Missing task directories/labels for existing processes/threads, unreadable,
empty or oversized evidence refuses removal. A process or thread confirmed
to have exited cannot retain the profile and does not block retirement.
No process is killed. The saved receipt and physically
attributed old profile bytes govern uninstall, independently of today's
template hash, allowing bounded policy iteration without in-place replacement.

After a durable `unload-pending` journal, removal uses
`apparmor_parser --jobs=1 --skip-cache --remove <owned-profile-file>`.
Kernel absence must be confirmed before guarded deletion of the exact owned
file. Terminal `retired` state retains receipt and lock; state directories are
never recursively removed. Uninstall of a validated-but-unloaded candidate
performs only the corresponding owned-file cleanup after the same fence and
inspection gates.

Run from the admitted host with a complete host `/proc`/securityfs view, not a
container PID namespace. The assertion does not prove that visibility. Existing
distribution AppArmor services may consume `/etc/apparmor.d` at boot; this
module neither enables/changes those services nor treats a retained receipt as
proof of the current kernel state after reboot.

## Failure and recovery

States include `preparing`, `prepared`, `validation-failed`, `load-pending`,
`ready`, `unload-pending`, `unloaded-cleanup-pending` and `retired`.
Timeout, caller loss or uncertain kernel outcome never implies rollback.
Pending states refuse automatic replay/adoption; `receipt.next`, an unattributed
lock-only directory, or a crash before physical ownership publication also
requires explicit owner reconciliation. Do not delete evidence to retry.

The fixed parser must be a root-owned, single-link regular executable under
non-writable root-owned ancestors. It has no shell, stdin or inherited credential environment.
Its captured streams share a 64-KiB bound and a 30-second deadline, followed
by bounded termination of its caller-owned process group. Diagnostics expose
only fixed reason codes, not raw output, policy/environment dumps or arbitrary
host paths. Parser process cleanup is not permission to kill profile users.
No module timer or background task performs recovery.

CLI output is bounded JSON. Exit `0` means observed `ready`, `absent` or
`retired`; `2` means blocked/drift/unavailable evidence. Argument errors use
standard argparse handling. Linux-root admission precedes filesystem work;
the module remains importable on Windows for offline fixtures.

## Validation

```text
python -B -m unittest discover -s fleet-manager/tests -p test_host_profile.py -v
python -B -m ruff check --select F,E9 fleet-manager/src/fleet_manager/host_profile.py fleet-manager/tests/test_host_profile.py
```

Offline fixtures explicitly model root UID/GID/modes, locking, parser outcomes,
kernel observations and process labels. They exercise physical file/link
operations, attribution, crash boundaries, old-template retirement and guarded
cleanup without loading policy or invoking the actual parser/kernel/Docker.
The owner separately qualifies the installed module on an admitted native host,
including parser compatibility, effect ordering, user detection, fence custody
and preservation of unrelated policies. Offline success is not that proof.
