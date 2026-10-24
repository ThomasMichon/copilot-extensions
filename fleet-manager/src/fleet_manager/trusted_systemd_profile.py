"""Explicit Linux host policy input; never loaded by importing this module."""

from itertools import product

PROFILE_NAME = "fleet-manager-trusted-systemd-v1"

_POLICY = """abi <abi/3.0>,
#include <tunables/global>

@{FLEET_SYSTEMD_ROOTS} = / /run/systemd/unit-root/ /run/systemd/unit-root/**/

profile @NAME@ flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>

  file,
  network,
  deny network alg,
  deny network vsock,

  capability audit_write,
  capability chown,
  capability dac_override,
  capability fowner,
  capability fsetid,
  capability kill,
  capability mknod,
  capability net_bind_service,
  capability net_raw,
  capability setfcap,
  capability setgid,
  capability setpcap,
  capability setuid,
  capability sys_admin,
  capability sys_chroot,

  signal (receive) peer=unconfined,
  signal (receive) peer=runc,
  signal (receive) peer=dockerd,
  signal (send,receive) peer=@NAME@,
  ptrace (trace,tracedby,read,readby) peer=@NAME@,

  # Keep kernel controls denied through temporary systemd root aliases too.
  deny @{FLEET_SYSTEMD_ROOTS}proc/* w,
  deny @{FLEET_SYSTEMD_ROOTS}proc/[^0-9]*/** w,
  deny @{FLEET_SYSTEMD_ROOTS}proc/{kcore,kmsg,keys,sysrq-trigger} rwklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/[^f]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/f[^s]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/[^c]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/c[^g]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/cg[^r]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/cgr[^o]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/cgro[^u]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/fs/cgrou[^p]*/** wklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/firmware/** rwklx,
  deny @{FLEET_SYSTEMD_ROOTS}sys/kernel/security/** rwklx,

  # Entrypoint pins /run/systemd as a mountpoint so aliases cannot be renamed.
  deny umount /,
  deny umount @{FLEET_SYSTEMD_ROOTS}run/,
  deny umount @{FLEET_SYSTEMD_ROOTS}run/systemd/,
  mount options=(rw,bind) /run/systemd/ -> /run/systemd/,
  remount /sys/fs/cgroup/,
  mount options=(rw,rslave) -> /,
  mount options=(rw,rshared) -> /,
  mount options=(rw,rslave) -> /dev/,
  mount options=(rw,slave) -> /run/systemd/incoming/,
  mount options=(rw,rbind) / -> /run/systemd/unit-root/,
  mount options=(rw,rbind) /run/systemd/unit-root/etc/ -> /run/systemd/unit-root/etc/,
  mount options=(rw,rbind) /run/systemd/unit-root/run/ -> /run/systemd/unit-root/run/,
  mount options=(rw,rbind) /run/systemd/unit-root/usr/lib/modules/ -> /run/systemd/unit-root/usr/lib/modules/,
  mount options=(rw,rbind) /run/systemd/unit-root/proc/sys/kernel/hostname -> /run/systemd/unit-root/proc/sys/kernel/hostname,
  mount options=(rw,rbind) /run/systemd/unit-root/proc/sys/kernel/domainname -> /run/systemd/unit-root/proc/sys/kernel/domainname,
  mount options=(rw,rbind) /run/systemd/inaccessible/dir/ -> /run/systemd/unit-root/{home,root,run/user,run/credentials}/,
  mount options=(rw,rbind) /run/systemd/inaccessible/{reg,chr,sock} -> /run/systemd/unit-root/{dev/kmsg,proc/kmsg,proc/modules},
  mount options=(rw,rbind) /tmp/systemd-private-*/tmp/ -> /run/systemd/unit-root/tmp/,
  mount options=(rw,rbind) /var/tmp/systemd-private-*/tmp/ -> /run/systemd/unit-root/var/tmp/,
  mount options=(rw,bind) /run/systemd/propagate/*/ -> /run/systemd/unit-root/run/systemd/incoming/,
  mount options=(rw,rbind) /run/systemd/inhibit/ -> /run/systemd/unit-root/run/systemd/inhibit/,
  mount options=(rw,rbind) /run/systemd/seats/ -> /run/systemd/unit-root/run/systemd/seats/,
  mount options=(rw,rbind) /run/systemd/sessions/ -> /run/systemd/unit-root/run/systemd/sessions/,
  mount options=(rw,rbind) /run/systemd/shutdown/ -> /run/systemd/unit-root/run/systemd/shutdown/,
  mount options=(rw,rbind) /run/systemd/users/ -> /run/systemd/unit-root/run/systemd/users/,
  mount options=(rw,rbind) /var/lib/systemd/linger/ -> /run/systemd/unit-root/var/lib/systemd/linger/,
  mount fstype=proc options=(rw,nosuid,nodev,noexec) -> /run/systemd/unit-root/proc/,
@UNIT_ROOT_REMOUNTS@
  mount options=(rw,move) /run/systemd/unit-root/ -> /,

  # systemd 252 prepares unit credential stores in a private /dev/shm mount.
  mount fstype=(ramfs,tmpfs) options=(rw,nosuid,nodev,noexec) -> /dev/shm/,
  mount options=(rw,rbind) /run/credentials/*/ -> /dev/shm/,
@CREDENTIAL_REMOUNTS@
  mount options=(rw,move) /dev/shm/ -> /run/credentials/*/,
  umount,
}
"""


def _remount_rules() -> tuple[str, str]:
    unit_root, credentials = [], []
    atime_modes = ((), ("relatime",), ("noatime",), ("strictatime",))
    # AppArmor "options in" makes every listed flag optional, including ro.
    for selected in product((False, True), repeat=4):
        optional = tuple(flag for flag, enabled in zip(
            ("nosuid", "nodev", "noexec", "nodiratime"), selected,
        ) if enabled)
        for atime in atime_modes:
            flags = ",".join(("ro", "bind", *optional, *atime))
            for target in ("/run/systemd/unit-root/", "/run/systemd/unit-root/**"):
                unit_root.append(f"  remount options=({flags}) {target},")
    for access in ("ro", "rw"):
        for nodiratime in ((), ("nodiratime",)):
            for atime in atime_modes:
                flags = ",".join(
                    (access, "bind", "nosuid", "nodev", "noexec", *nodiratime, *atime)
                )
                credentials.append(f"  remount options=({flags}) /dev/shm/,")
    return "\n".join(unit_root), "\n".join(credentials)


def profile_bytes() -> bytes:
    unit_root, credentials = _remount_rules()
    return (_POLICY.replace("@NAME@", PROFILE_NAME)
            .replace("@UNIT_ROOT_REMOUNTS@", unit_root)
            .replace("@CREDENTIAL_REMOUNTS@", credentials).encode("utf-8"))
