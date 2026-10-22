"""Experimental reference implementation; see the parent README."""

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


BEGIN = b"\n# BEGIN gateway-ssh-mesh client-liveness v1\n"
END = b"# END gateway-ssh-mesh client-liveness v1\n"
CONFIG = Path("/etc/ssh/sshd_config")


class PolicyError(Exception):
    pass


def section(user):
    if (not isinstance(user, str) or user == "root" or
            not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user)):
        raise PolicyError("An explicit non-root Gateway account is required")
    return (BEGIN + f"Match User {user}\n"
            "    ClientAliveInterval 15\n"
            "    ClientAliveCountMax 3\n"
            "Match all\n".encode("ascii") + END)


def without_section(data, user):
    expected = section(user)
    if b"# BEGIN gateway-ssh-mesh client-liveness" not in data and END not in data:
        return data, False
    if data.count(BEGIN) != 1 or data.count(END) != 1 or not data.endswith(expected):
        raise PolicyError("Foreign, edited, misplaced or differently scoped policy section")
    return data[:-len(expected)], True


def run(argv):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PolicyError(f"Provider command failed: {type(error).__name__}") from error
    if result.returncode:
        raise PolicyError(f"Provider command refused with exit {result.returncode}")
    return result.stdout


class Policy:
    def __init__(self, path=CONFIG, sshd="/usr/sbin/sshd",
                 reload_command=None, runner=run):
        self.path = Path(path)
        self.sshd = sshd
        self.reload_command = reload_command or ["/usr/bin/systemctl", "reload", "ssh"]
        self.run = runner

    def snapshot(self):
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                (os.name == "posix" and info.st_mode & 0o022)):
            raise PolicyError("Provider configuration must be an ordinary, non-writable-by-others file")
        return self.path.read_bytes(), info

    def effective(self, path, user):
        text = self.run([self.sshd, "-T", "-f", str(path), "-C",
                         f"user={user},host=localhost,addr=127.0.0.1,laddr=127.0.0.1,lport=22"])
        values = {}
        for line in text.splitlines():
            key, separator, value = line.partition(" ")
            if (not separator or not key or
                    (key in values and key in {"clientaliveinterval", "clientalivecountmax"})):
                raise PolicyError("Invalid effective sshd configuration")
            values[key] = values[key] + "\n" + value if key in values else value
        if not {"clientaliveinterval", "clientalivecountmax"} <= values.keys():
            raise PolicyError("Effective sshd liveness settings are unavailable")
        return values

    @contextlib.contextmanager
    def candidate(self, data, info):
        descriptor, name = tempfile.mkstemp(prefix=".gateway-liveness-", dir=self.path.parent)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if hasattr(os, "chown"):
                os.chown(path, info.st_uid, info.st_gid)
            os.chmod(path, stat.S_IMODE(info.st_mode))
            yield path
        finally:
            path.unlink(missing_ok=True)

    def replace(self, old, new, info):
        current, current_info = self.snapshot()
        if (current != old or (current_info.st_dev, current_info.st_ino) !=
                (info.st_dev, info.st_ino)):
            raise PolicyError("Provider configuration changed concurrently; refusing overwrite")
        with self.candidate(new, info) as path:
            os.replace(path, self.path)

    def apply(self, action, user):
        old, info = self.snapshot()
        base, present = without_section(old, user)
        self.run([self.sshd, "-t", "-f", str(self.path)])
        if action == "status":
            return {"configured": present, "running_policy": "not-probed",
                    "effective": self.effective(self.path, user)}
        new = base if action == "uninstall" else base + section(user)
        other = "nobody" if user != "nobody" else "root"
        before = self.effective(self.path, user)
        other_before = self.effective(self.path, other)
        with self.candidate(new, info) as path:
            self.run([self.sshd, "-t", "-f", str(path)])
            after = self.effective(path, user)
            other_after = self.effective(path, other)
        allowed = {"clientaliveinterval", "clientalivecountmax"}
        if ({key: value for key, value in before.items() if key not in allowed} !=
                {key: value for key, value in after.items() if key not in allowed} or
                other_before != other_after):
            raise PolicyError("Proposed policy changes unrelated effective sshd settings")
        if action != "uninstall" and (
                after["clientaliveinterval"] != "15" or after["clientalivecountmax"] != "3"):
            raise PolicyError("Provider configuration overrides the required liveness policy")
        changed = old != new
        if changed:
            self.replace(old, new, info)
        try:
            self.run([self.sshd, "-t", "-f", str(self.path)])
            self.run(self.reload_command)
        except PolicyError as error:
            if changed:
                current, current_info = self.snapshot()
                if current != new:
                    raise PolicyError("Reload failed and concurrent edits prevent safe rollback") from error
                self.replace(new, old, current_info)
                try:
                    self.run([self.sshd, "-t", "-f", str(self.path)])
                    self.run(self.reload_command)
                except PolicyError as rollback:
                    raise PolicyError("Reload failed; predecessor bytes restored but reload unverified") from rollback
                raise PolicyError("Reload failed; predecessor restored and reloaded") from error
            raise
        return {"configured": action != "uninstall", "changed": changed,
                "reloaded": True, "existing_sessions": "unchanged",
                "running_policy": "new-connections-only", "effective": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "update", "status", "uninstall"])
    parser.add_argument("--user", required=True, help="Explicit existing non-root Gateway account")
    args = parser.parse_args()
    try:
        section(args.user)
        if os.name != "posix" or os.geteuid() != 0:
            raise PolicyError("Run only this explicit provider-policy helper with root authorization")
        import fcntl
        import pwd
        if pwd.getpwnam(args.user).pw_uid == 0:
            raise PolicyError("The Gateway account must not be root")
        if CONFIG.parent.is_symlink() or CONFIG.lstat().st_uid != 0:
            raise PolicyError("Provider configuration ownership is invalid")
        descriptor = os.open("/run/lock/gateway-ssh-liveness.lock",
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            info = os.fstat(lock.fileno())
            if info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
                raise PolicyError("Provider policy lock ownership is invalid")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = Policy().apply(args.action, args.user)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PolicyError, OSError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
