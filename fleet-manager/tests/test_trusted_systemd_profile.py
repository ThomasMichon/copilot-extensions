import importlib.util
from pathlib import Path
import re
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src/fleet_manager/trusted_systemd_profile.py"
SPEC = importlib.util.spec_from_file_location("trusted_systemd_policy", SOURCE)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class TrustedSystemdProfileTests(unittest.TestCase):
    def test_fixed_enforcing_profile_has_canonical_bytes(self):
        data = POLICY.profile_bytes()
        self.assertEqual(data, POLICY.profile_bytes())
        self.assertNotIn(b"\r", data)
        self.assertNotIn(b"@NAME@", data)
        self.assertNotIn(b"@UNIT_ROOT_REMOUNTS@", data)
        self.assertNotIn(b"@CREDENTIAL_REMOUNTS@", data)
        self.assertLessEqual(len(data), 65536)
        self.assertIn(
            f"profile {POLICY.PROFILE_NAME} flags=(attach_disconnected,mediate_deleted) {{",
            data.decode("utf-8"),
        )
        self.assertNotIn("complain", data.decode("utf-8"))

    def test_mount_operations_are_scoped_to_pinned_systemd_runtime_paths(self):
        operations = [
            line.strip() for line in POLICY.profile_bytes().decode().splitlines()
            if re.match(r"\s*(?:mount|remount)\b", line)
        ]
        self.assertIn("remount /sys/fs/cgroup/,", operations)
        self.assertIn("mount options=(rw,rslave) -> /,", operations)
        self.assertIn("mount options=(rw,rshared) -> /,", operations)
        self.assertNotIn("mount,", operations)
        for line in operations:
            self.assertTrue(
                any(path in line for path in (
                    "/sys/fs/cgroup/", "/run/systemd/unit-root/",
                    "/dev/shm/", "/run/systemd/incoming/", "-> /dev/,", "-> /,",
                    "mount options=(rw,bind) /run/systemd/ -> /run/systemd/,",
                )),
                line,
            )
        fresh = [line for line in operations if line.startswith("mount fstype=")]
        self.assertEqual(
            fresh,
            [
                "mount fstype=proc options=(rw,nosuid,nodev,noexec) -> /run/systemd/unit-root/proc/,",
                "mount fstype=(ramfs,tmpfs) options=(rw,nosuid,nodev,noexec) -> /dev/shm/,",
            ],
        )

    def test_temporary_root_bindings_do_not_allow_arbitrary_source_repathing(self):
        text = POLICY.profile_bytes().decode()
        self.assertIn(
            "@{FLEET_SYSTEMD_ROOTS} = / /run/systemd/unit-root/ /run/systemd/unit-root/**/",
            text,
        )
        rules = [
            line.strip() for line in text.splitlines()
            if re.match(r"\s*mount options=.*bind", line)
        ]
        pairs = {
            tuple(re.fullmatch(r"mount options=\([^)]+\) (.+) -> (.+),", rule).groups())
            for rule in rules
        }
        root = "/run/systemd/unit-root"
        self.assertEqual(pairs, {
            ("/run/systemd/", "/run/systemd/"),
            ("/", root + "/"),
            (root + "/etc/", root + "/etc/"),
            (root + "/run/", root + "/run/"),
            (root + "/usr/lib/modules/", root + "/usr/lib/modules/"),
            (root + "/proc/sys/kernel/hostname", root + "/proc/sys/kernel/hostname"),
            (root + "/proc/sys/kernel/domainname", root + "/proc/sys/kernel/domainname"),
            ("/run/systemd/inaccessible/dir/", root + "/{home,root,run/user,run/credentials}/"),
            ("/run/systemd/inaccessible/{reg,chr,sock}", root + "/{dev/kmsg,proc/kmsg,proc/modules}"),
            ("/tmp/systemd-private-*/tmp/", root + "/tmp/"),
            ("/var/tmp/systemd-private-*/tmp/", root + "/var/tmp/"),
            ("/run/systemd/propagate/*/", root + "/run/systemd/incoming/"),
            ("/run/systemd/inhibit/", root + "/run/systemd/inhibit/"),
            ("/run/systemd/seats/", root + "/run/systemd/seats/"),
            ("/run/systemd/sessions/", root + "/run/systemd/sessions/"),
            ("/run/systemd/shutdown/", root + "/run/systemd/shutdown/"),
            ("/run/systemd/users/", root + "/run/systemd/users/"),
            ("/var/lib/systemd/linger/", root + "/var/lib/systemd/linger/"),
            ("/run/credentials/*/", "/dev/shm/"),
        })
        self.assertEqual(len(rules), len(pairs))

    def test_alias_ancestors_are_pinned_and_cannot_be_unmounted(self):
        text = POLICY.profile_bytes().decode()
        for rule in (
            "mount options=(rw,bind) /run/systemd/ -> /run/systemd/,",
            "deny umount /,",
            "deny umount @{FLEET_SYSTEMD_ROOTS}run/,",
            "deny umount @{FLEET_SYSTEMD_ROOTS}run/systemd/,",
        ):
            self.assertIn(rule, text)

    def test_remount_flag_families_keep_safety_flags_mandatory(self):
        text = POLICY.profile_bytes().decode()
        self.assertNotIn("options in", text)
        unit_root = re.findall(
            r"^\s*remount options=\(([^)]+)\) /run/systemd/unit-root/(?:\*\*)?,",
            text, re.MULTILINE,
        )
        self.assertEqual(len(unit_root), 128)
        for flags in unit_root:
            self.assertTrue({"ro", "bind"} <= set(flags.split(",")))
            self.assertNotIn("rw", flags.split(","))
        credentials = re.findall(
            r"^\s*remount options=\(([^)]+)\) /dev/shm/,", text, re.MULTILINE,
        )
        self.assertEqual(len(credentials), 16)
        for flags in credentials:
            self.assertTrue({"bind", "nosuid", "nodev", "noexec"} <= set(flags.split(",")))
            self.assertEqual(len({"ro", "rw"} & set(flags.split(","))), 1)

    def test_capabilities_do_not_exceed_the_declared_trusted_profile(self):
        capabilities = set(re.findall(
            r"^\s*capability ([a-z_]+),$", POLICY.profile_bytes().decode(), re.MULTILINE
        ))
        self.assertEqual(
            capabilities,
            {
                "audit_write", "chown", "dac_override", "fowner", "fsetid", "kill",
                "mknod", "net_bind_service", "net_raw", "setfcap", "setgid", "setpcap",
                "setuid", "sys_admin", "sys_chroot",
            },
        )
        self.assertNotIn("capability,", POLICY.profile_bytes().decode())

    def test_global_kernel_controls_remain_denied(self):
        text = POLICY.profile_bytes().decode()
        for rule in (
            "deny @{FLEET_SYSTEMD_ROOTS}proc/* w,",
            "deny @{FLEET_SYSTEMD_ROOTS}proc/[^0-9]*/** w,",
            "deny @{FLEET_SYSTEMD_ROOTS}proc/{kcore,kmsg,keys,sysrq-trigger} rwklx,",
            "deny @{FLEET_SYSTEMD_ROOTS}sys/firmware/** rwklx,",
            "deny @{FLEET_SYSTEMD_ROOTS}sys/kernel/security/** rwklx,",
            "deny network alg,", "deny network vsock,",
        ):
            self.assertIn(rule, text)


if __name__ == "__main__":
    unittest.main()
