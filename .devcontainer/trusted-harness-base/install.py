"""Image construction only: fixed public artifacts, no runtime enrollment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import stat
import subprocess
import sys
import urllib.request
import uuid

from build import validate_age

ROOT = Path("/opt/trusted-harness")


def checked_download(url: str, expected: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        destination.unlink()
        raise ValueError("Artifact checksum mismatch")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stdin=subprocess.DEVNULL)


def ownership(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_uid, info.st_gid


def permissions(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def set_owner(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)


def set_permissions(path: Path, mode: int) -> None:
    path.chmod(mode)


def regular(path: Path) -> bool:
    return not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode)


def directory(path: Path) -> bool:
    return (not path.is_symlink()
            and not (hasattr(path, "is_junction") and path.is_junction())
            and path.is_dir())


def validate_mount(path: Path) -> bool:
    if not directory(path):
        raise ValueError("Persistent mount must be a real directory")
    owner = ownership(path)
    if owner == (1000, 1000):
        return False
    if owner == (0, 0) and not any(path.iterdir()):
        return True
    raise ValueError("Refusing foreign or nonempty root-owned persistent mount")


def user_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if not directory(path) or ownership(path) != (1000, 1000):
            raise ValueError("Refusing foreign or linked home directory")
        return
    path.mkdir(mode=0o700)
    set_owner(path, 1000, 1000)
    set_permissions(path, 0o700)


def validate_identity(identity: Path) -> None:
    expected = {"machine-id": 0o444, "ssh_host_ed25519_key": 0o600,
                "ssh_host_ed25519_key.pub": 0o644}
    if (not directory(identity) or ownership(identity) != (0, 0)
            or permissions(identity) != 0o700 or set(p.name for p in identity.iterdir()) != set(expected)):
        raise ValueError("Refusing malformed or partial persistent identity")
    for name, mode in expected.items():
        path = identity / name
        if not regular(path) or ownership(path) != (0, 0) or permissions(path) != mode:
            raise ValueError("Refusing linked, foreign or malformed identity file")
    machine = (identity / "machine-id").read_text()
    if not re.fullmatch(r"[0-9a-f]{32}\n?", machine) or machine.strip() == "0" * 32:
        raise ValueError("Persistent machine identity is invalid")
    private = identity / "ssh_host_ed25519_key"
    derived = run("ssh-keygen", "-y", "-P", "", "-f", str(private)).split()
    public = (identity / "ssh_host_ed25519_key.pub").read_text().split()
    if len(derived) < 2 or len(public) < 2 or derived[:2] != public[:2] or derived[0] != "ssh-ed25519":
        raise ValueError("Persistent SSH key pair does not match")


def prepare_runtime(home: Path, workspace: Path, etc: Path) -> None:
    mounts = [(path, validate_mount(path)) for path in (home, workspace)]
    for path, fresh in mounts:
        if fresh:
            set_owner(path, 1000, 1000)
    parent = home
    for name in (".local", "state", "trusted-harness"):
        parent = parent / name
        user_directory(parent)
    identity = parent / "identity"
    if not identity.exists() and not identity.is_symlink():
        identity.mkdir(mode=0o700)
        set_owner(identity, 0, 0)
        set_permissions(identity, 0o700)
        machine = identity / "machine-id"
        machine.write_text(uuid.uuid4().hex + "\n")
        set_owner(machine, 0, 0)
        set_permissions(machine, 0o444)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "",
            "-f", str(identity / "ssh_host_ed25519_key"))
        for name, mode in (("ssh_host_ed25519_key", 0o600), ("ssh_host_ed25519_key.pub", 0o644)):
            path = identity / name
            set_owner(path, 0, 0)
            set_permissions(path, mode)
    validate_identity(identity)
    # DBus and user systemd must not need to traverse a possibly 0700 home.
    projected = etc / "machine-id"
    if not regular(projected) or ownership(projected) != (0, 0):
        raise ValueError("Expected an image-owned regular /etc/machine-id")
    reference = etc.parent / "var/lib/dbus/machine-id"
    if not reference.is_symlink() or reference.resolve() != projected.resolve():
        raise ValueError("Invalid DBus machine-id reference")
    projected.write_bytes((identity / "machine-id").read_bytes())
    set_permissions(projected, 0o444)


def main() -> None:
    lock = json.loads((ROOT / "recipe/dependencies.lock.json").read_text())
    validate_age(lock)
    if run("dpkg", "--print-architecture").strip() != "amd64":
        raise ValueError("Only Linux amd64 is supported")
    if run("id", "-u", "vscode").strip() != "1000":
        raise ValueError("The fixed vscode UID must be 1000")
    if run("id", "-g", "vscode").strip() != "1000":
        raise ValueError("The fixed vscode GID must be 1000")
    build = ROOT / "build"
    build.mkdir(parents=True)
    apt = lock["apt"]
    checked_download(apt["evidence"], apt["release_sha256"], build / "Release")
    checked_download(
        apt["snapshot"] + "dists/bookworm/main/binary-amd64/Packages.xz",
        apt["packages_xz_sha256"], build / "Packages.xz",
    )
    sources = Path("/etc/apt/sources.list.d")
    for path in sources.iterdir():
        if path.suffix in (".list", ".sources"):
            path.unlink()
    Path("/etc/apt/sources.list").write_text(
        "deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] "
        + apt["snapshot"] + " bookworm main\n"
    )
    policy = Path("/usr/sbin/policy-rc.d")
    own_policy = not policy.exists()
    if own_policy:
        policy.write_text("#!/bin/sh\nexit 101\n")
        policy.chmod(0o755)
    try:
        run("apt-get", "update")
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends",
             *[f"{name}={version}" for name, version in apt["packages"].items()]],
            check=True, env=env, stdin=subprocess.DEVNULL,
        )
    finally:
        if own_policy:
            policy.unlink()
    for name, artifact in lock["archives"].items():
        archive = build / (name + ".archive")
        checked_download(artifact["url"], artifact["sha256"], archive)
        destination = ROOT / name
        destination.mkdir()
        run("tar", "-xf", str(archive), "-C", str(destination),
            "--strip-components=" + str(artifact["strip_components"]))
        archive.unlink()
    for command, version in (
        (["node", "--version"], lock["archives"]["node"]["version"]),
        (["npm", "--version"], lock["archives"]["node"]["npm_version"]),
        (["copilot", "--version"], lock["archives"]["copilot"]["version"]),
        (["uv", "--version"], lock["archives"]["uv"]["version"]),
        (["python3", "--version"], lock["base"]["python_version"]),
    ):
        if version not in run(*command):
            raise ValueError("Unexpected tool version: " + command[0])
    for tool in ("git", "gh", "tmux", "ssh", "sshd", "systemctl", "mount", "mountpoint", "findmnt"):
        if not shutil.which(tool):
            raise ValueError("Missing required tool: " + tool)
    (build / "os-packages.txt").write_text(run("dpkg-query", "-W"))
    (build / "Packages.xz").unlink()
    Path("/workspaces").mkdir(exist_ok=True)
    os.chown("/workspaces", 1000, 1000)
    linger = Path("/var/lib/systemd/linger")
    linger.mkdir(parents=True, exist_ok=True)
    (linger / "vscode").touch()
    for key in Path("/etc/ssh").glob("ssh_host_*"):
        key.unlink()
    machine = Path("/etc/machine-id")
    machine.unlink(missing_ok=True)
    machine.write_text("")
    machine.chmod(0o444)
    reference = Path("/var/lib/dbus/machine-id")
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.unlink(missing_ok=True)
    reference.symlink_to(machine)
    # The inherited devcontainer PATH shim is a no-op until systemd is running.
    run("/usr/bin/systemctl", "--root=/", "enable", "ssh.service")
    run("/usr/bin/systemctl", "--root=/", "mask", "apt-daily.service", "apt-daily.timer",
        "apt-daily-upgrade.service", "apt-daily-upgrade.timer")
    run("apt-get", "clean")


if __name__ == "__main__":
    if sys.argv[1:] == ["--runtime"]:
        prepare_runtime(Path("/home/vscode"), Path("/workspaces"), Path("/etc"))
    elif sys.argv[1:]:
        raise SystemExit("Unsupported image installer arguments")
    else:
        main()
