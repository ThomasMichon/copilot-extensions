"""Experimental reference implementation; see the parent README."""

import argparse
import base64
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import stat
import struct
import sys


SERVICE = "gateway-ssh-mesh-openssh"
BACKEND = "openssh-reverse"
MACHINES = ("operator-node-a", "operator-node-b", "operator-node-c",
            "operator-node-d", "operator-node-e")
BEGIN = ("# BEGIN " + SERVICE + " ").encode()
END = ("# END " + SERVICE + " ").encode()


class AdapterError(Exception):
    pass


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise AdapterError("Malformed JSON-subset YAML/JSON") from exc


def canonical(machine):
    if not isinstance(machine, str):
        raise AdapterError("Machine must be a string")
    name = machine if machine.startswith("operator-") else "operator-" + machine
    if name not in MACHINES:
        raise AdapterError("Unknown pilot machine")
    return name


def public_key(value):
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise AdapterError("Expected single-line ed25519 public key")
    fields = value.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise AdapterError("Only ed25519 public keys are accepted")
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise AdapterError("Invalid public key encoding") from exc
    expected_prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if (len(raw) != 51 or raw[:19] != expected_prefix or
            base64.b64encode(raw).decode() != fields[1]):
        raise AdapterError("Invalid ed25519 public key wire format")
    return "ssh-ed25519 " + fields[1]


def config_policy(value, username):
    required = {"schema", "gateway_user", "host_public_key", "server_port", "peers"}
    if (not isinstance(value, dict) or set(value) != required | (
            {"gateway_http"} if "gateway_http" in value else set()) or value["schema"] != 1):
        raise AdapterError("Explicit configuration schema is invalid")
    if "gateway_http" in value and type(value["gateway_http"]) is not bool:
        raise AdapterError("gateway_http must be an explicit boolean")
    if value["gateway_user"] != username:
        raise AdapterError("Configured Gateway user differs from the current non-root user")
    if value["server_port"] != 22:
        raise AdapterError("This adapter only uses the provider-owned sshd on port 22")
    host_path = value["host_public_key"]
    if (not isinstance(host_path, str) or not Path(host_path).is_absolute() or
            Path(host_path).name != "ssh_host_ed25519_key.pub"):
        raise AdapterError("Configure the exact platform ed25519 PUBLIC host-key file")
    expected = dict(zip(MACHINES, range(23001, 23006)))
    if value["peers"] != expected or any(type(port) is not int for port in value["peers"].values()):
        raise AdapterError("Explicit topology must match the reviewed five-machine loopback ports")
    return value


def peer_policy(payload):
    if not isinstance(payload, dict) or set(payload) != {"peers"} or not isinstance(payload["peers"], list):
        raise AdapterError("Enrollment requires {peers:[{machine,user,public_key,host_key}]}")
    result = []
    seen = set()
    for row in payload["peers"]:
        if not isinstance(row, dict) or set(row) != {"machine", "user", "public_key", "host_key"}:
            raise AdapterError("Invalid peer public record")
        machine = canonical(row["machine"])
        if machine in seen:
            raise AdapterError("Duplicate peer machine")
        seen.add(machine)
        if not isinstance(row["user"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", row["user"]):
            raise AdapterError("Invalid peer login user")
        result.append({"machine": machine, "user": row["user"],
                       "public_key": public_key(row["public_key"]),
                       "host_key": public_key(row["host_key"])})
    keys = [row["public_key"] for row in result]
    if len(set(keys)) != len(keys):
        raise AdapterError("Each enrolled machine needs its own public authentication key")
    return sorted(result, key=lambda row: row["machine"])


def no_links(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise AdapterError("Symlink paths are not allowed")
    if path.exists():
        info = path.stat()
        if path.is_file() and info.st_nlink != 1:
            raise AdapterError("Hard-linked files are not allowed")


def checked_bytes(path, uid, private=True, authorization=False):
    no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise AdapterError("Expected an ordinary file")
    if os.name != "nt":
        if private and info.st_uid != uid:
            raise AdapterError("File ownership differs from the current user")
        if not private and info.st_uid not in (0, uid):
            raise AdapterError("Public host-key ownership is not platform/current-user owned")
        if (authorization and (stat.S_IMODE(info.st_mode) & 0o133 or
                              stat.S_IMODE(info.st_mode) & 0o600 != 0o600) or
                private and not authorization and stat.S_IMODE(info.st_mode) not in (0o600, 0o700) or
                not private and stat.S_IMODE(info.st_mode) & 0o022):
            raise AdapterError("Unsafe file permissions")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (info.st_dev, info.st_ino, 1):
            raise AdapterError("File identity changed during read")
        return stream.read()


def atomic(path, data, uid, expected=None, compare=False, mode=0o600, authorization=False):
    no_links(path)
    pending = path.with_name(path.name + "." + SERVICE + ".pending")
    no_links(pending)
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(pending, mode)
        if compare:
            observed = checked_bytes(path, uid, authorization=authorization) if path.exists() else None
            if observed != expected:
                raise AdapterError("Concurrent artifact/authorized_keys change; refused")
        no_links(path)
        os.replace(pending, path)
    finally:
        if pending.exists():
            pending.unlink()


def ssh_banner(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.settimeout(1)
            data = connection.recv(512)
    except OSError:
        return None
    line = data.split(b"\n", 1)[0].rstrip(b"\r")
    if not re.fullmatch(rb"SSH-2\.0-[!-~ ]{1,200}", line):
        return None
    return line.decode("ascii")


class Adapter:
    def __init__(self, root, home, username, uid, probe=ssh_banner):
        if uid == 0:
            raise AdapterError("Run as the expected non-root Gateway user, never sudo")
        self.root = Path(root).absolute()
        self.home = Path(home).absolute()
        self.username = username
        self.uid = uid
        self.probe = probe
        self.keys = self.home / ".ssh" / "authorized_keys"
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"

    def read(self, path):
        return checked_bytes(path, self.uid, authorization=path == self.keys)

    def write(self, path, data, **kwargs):
        if path == self.keys:
            no_links(path)
            kwargs["authorization"] = True
            if path.exists():
                kwargs["mode"] = stat.S_IMODE(path.stat().st_mode)
        atomic(path, data, self.uid, **kwargs)

    def ownership(self, create=False):
        no_links(self.root)
        if not self.root.exists():
            if not create:
                raise AdapterError("Adapter is not installed")
            self.root.mkdir(mode=0o700, parents=True)
        root_info = self.root.stat()
        if os.name != "nt" and (root_info.st_uid != self.uid or stat.S_IMODE(root_info.st_mode) != 0o700):
            raise AdapterError("Unsafe adapter root permissions/ownership")
        marker = self.root / "owner.json"
        if not marker.exists():
            if not create or any(self.root.iterdir()):
                raise AdapterError("Refusing foreign/unmarked adapter root")
            self.write(marker, encode({
                "schema": 1, "service": SERVICE, "root": str(self.root),
                "authorized_keys": str(self.keys), "user": self.username, "uid": self.uid,
                "id": secrets.token_hex(16),
            }))
        owner = parse(self.read(marker))
        expected = {"schema": 1, "service": SERVICE, "root": str(self.root),
                    "authorized_keys": str(self.keys), "user": self.username, "uid": self.uid}
        if (not isinstance(owner, dict) or set(owner) != {*expected, "id"} or
                any(owner[key] != val for key, val in expected.items()) or
                not isinstance(owner["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", owner["id"])):
            raise AdapterError("Adapter root ownership marker drift")
        allowed = {"owner.json", "config", "state", "bin", "versions", "current-version",
                   "deploy-manifest.json", "lifecycle.lock"}
        if {item.name for item in self.root.iterdir()} - allowed:
            raise AdapterError("Unexpected user state in owned adapter root")
        for path in (self.root, *self.root.rglob("*")):
            no_links(path)
            info = path.stat()
            if os.name != "nt" and (info.st_uid != self.uid or
                    stat.S_IMODE(info.st_mode) not in ((0o700,) if path.is_dir() else (0o600, 0o700))):
                raise AdapterError("Runtime permissions/ownership drift")
        if create:
            for name in ("config", "state", "bin", "versions"):
                (self.root / name).mkdir(mode=0o700, exist_ok=True)
        for name, allowed_files in (
                ("config", {"gateway.json", "peers.json"}),
                ("state", {"active.json"}), ("bin", {SERVICE})):
            directory = self.root / name
            if directory.exists() and {item.name for item in directory.iterdir()} - allowed_files:
                raise AdapterError("Unexpected files in owned adapter " + name)
        return owner

    def host_key(self, config):
        path = Path(config["host_public_key"])
        try:
            raw = checked_bytes(path, self.uid, private=False)
        except OSError as exc:
            raise AdapterError("Platform PUBLIC host key unavailable; no TOFU fallback") from exc
        try:
            value = raw.decode("ascii").rstrip("\r\n")
        except UnicodeError as exc:
            raise AdapterError("Platform PUBLIC host key is malformed") from exc
        return public_key(value)

    def key_snapshot(self):
        no_links(self.keys)
        for path in (self.home, self.keys.parent):
            no_links(path)
            if path.exists():
                info = path.stat()
                if not stat.S_ISDIR(info.st_mode):
                    raise AdapterError("Expected an owned home/.ssh directory")
                if os.name != "nt" and (info.st_uid != self.uid or
                        stat.S_IMODE(info.st_mode) & 0o022):
                    raise AdapterError("Unsafe home/.ssh permissions or ownership")
        return self.read(self.keys) if self.keys.exists() else None

    def block(self, owner, config, peers, state):
        if not state["enabled"] or not peers:
            return b""
        begin = BEGIN + owner["id"].encode() + b"\n"
        end = END + owner["id"].encode() + b"\n"
        lines = []
        destinations = [f'permitopen="127.0.0.1:{config["peers"][machine]}"' for machine in MACHINES]
        if config.get("gateway_http"):
            destinations.append('permitopen="127.0.0.1:8787"')
        for row in peers:
            options = ['restrict', 'port-forwarding', 'command="/bin/false"',
                       f'permitlisten="127.0.0.1:{config["peers"][row["machine"]]}"',
                       *destinations]
            lines.append((",".join(options) + " " + row["public_key"] + " " +
                          SERVICE + ":" + row["machine"] + "\n").encode())
        return (b"\n" if state["separator_added"] else b"") + begin + b"".join(lines) + end

    def unowned(self, data, owner, expected):
        data = data or b""
        lines = data.splitlines(keepends=True)
        markers = [(index, line) for index, line in enumerate(lines)
                   if line.startswith(BEGIN) or line.startswith(END)]
        if not markers:
            if expected:
                raise AdapterError("Owned authorized_keys block is missing/drifted")
            return data
        if (len(markers) != 2 or markers[0][1] != BEGIN + owner["id"].encode() + b"\n" or
                markers[1][1] != END + owner["id"].encode() + b"\n" or
                markers[0][0] >= markers[1][0]):
            raise AdapterError("Foreign/duplicate/malformed adapter block; refused")
        start = sum(map(len, lines[:markers[0][0]]))
        stop = sum(map(len, lines[:markers[1][0] + 1]))
        if expected.startswith(b"\n"):
            start -= 1
        if start < 0 or data[start:stop] != expected:
            raise AdapterError("Owned authorized_keys block content/source drift")
        return data[:start] + data[stop:]

    def state(self):
        path = self.state_dir / "active.json"
        return parse(self.read(path)) if path.exists() else None

    def checked(self):
        owner = self.ownership()
        state = self.state()
        if not state:
            raise AdapterError("No adapter configuration")
        fields = {"schema", "enabled", "installed", "separator_added", "config_sha256",
                  "peers_sha256", "host_key_sha256", "slot", "block_sha256"}
        if (not isinstance(state, dict) or set(state) != fields or state["schema"] != 1 or
                any(type(state[key]) is not bool for key in ("enabled", "installed", "separator_added")) or
                any(not isinstance(state[key], str) or not re.fullmatch(r"[a-f0-9]{64}", state[key])
                    for key in ("config_sha256", "peers_sha256", "host_key_sha256", "block_sha256"))):
            raise AdapterError("Adapter state schema/source drift")
        config_bytes = self.read(self.config_dir / "gateway.json")
        peers_bytes = self.read(self.config_dir / "peers.json")
        if sha(config_bytes) != state["config_sha256"] or sha(peers_bytes) != state["peers_sha256"]:
            raise AdapterError("Owned configuration/enrollment source drift")
        config = config_policy(parse(config_bytes), self.username)
        peers = peer_policy(parse(peers_bytes))
        host_key = self.host_key(config)
        if sha(host_key.encode()) != state["host_key_sha256"]:
            raise AdapterError("Platform host public identity changed; explicit review required")
        expected = self.block(owner, config, peers, state)
        if sha(expected) != state["block_sha256"]:
            raise AdapterError("Owned authorization block state drift")
        self.unowned(self.key_snapshot(), owner, expected)
        if state["installed"]:
            self.check_slot(state)
            if self.read(self.root / "current-version").decode() != state["slot"]:
                raise AdapterError("Installed version selector drift")
            if self.read(self.root / "bin" / SERVICE) != self.launcher_bytes():
                raise AdapterError("Stable launcher drift")
        return owner, config, peers, state

    def launcher_bytes(self):
        root = shlex.quote(str(self.root))
        return (
            "#!/bin/sh\nset -eu\nROOT=" + root + "\n"
            'SLOT=$(cat "$ROOT/current-version")\n'
            'case "$SLOT" in *[!a-f0-9]*) echo "Invalid adapter slot" >&2; exit 1;; esac\n'
            '[ "${#SLOT}" -eq 64 ] || exit 1\ncd "$ROOT"\n'
            'exec python3 "$ROOT/versions/$SLOT/gateway_openssh.py" --root "$ROOT" "$@"\n'
        ).encode()

    def check_slot(self, state):
        if not isinstance(state["slot"], str) or not re.fullmatch(r"[a-f0-9]{64}", state["slot"]):
            raise AdapterError("Invalid adapter runtime slot")
        slot = self.root / "versions" / state["slot"]
        if {item.name for item in slot.iterdir()} != {"gateway_openssh.py", "slot.json"}:
            raise AdapterError("Unexpected immutable slot contents")
        manifest = parse(self.read(slot / "slot.json"))
        if manifest != {"schema": 1, "service": SERVICE, "script_sha256": state["slot"]}:
            raise AdapterError("Immutable slot provenance drift")
        if sha(self.read(slot / "gateway_openssh.py")) != state["slot"]:
            raise AdapterError("Installed immutable script drift")

    def transaction(self, updates, expected=None):
        pass
        previous = {path: self.read(path) if path.exists() else None for path in updates}
        if any(previous[path] != value for path, value in (expected or {}).items() if path in previous):
            raise AdapterError("Concurrent artifact/authorized_keys change; refused")
        changed = []
        try:
            for path, content in updates.items():
                if previous[path] == content:
                    continue
                if path == self.keys:
                    self.key_snapshot()
                    self.keys.parent.mkdir(mode=0o700, exist_ok=True)
                self.write(path, content, expected=previous[path], compare=True)
                changed.append(path)
        except (AdapterError, OSError) as error:
            try:
                for path in reversed(changed):
                    if previous[path] is None:
                        if self.read(path) != updates[path]:
                            raise AdapterError("Concurrent change prevents rollback")
                        path.unlink()
                    else:
                        self.write(path, previous[path], expected=updates[path], compare=True)
            except (AdapterError, OSError) as rollback_error:
                raise AdapterError("Adapter write failed; rollback incomplete, inspect owned state") from rollback_error
            raise AdapterError("Adapter write failed; previous authorization/state restored") from error

    def converge(self, owner, config, peers, old, enabled, slot=None,
                 installed=True, extra_updates=None):
        before = self.key_snapshot()
        old_block = b""
        if old:
            old_config = self.read(self.config_dir / "gateway.json")
            old_peers = self.read(self.config_dir / "peers.json")
            if sha(old_config) != old["config_sha256"] or sha(old_peers) != old["peers_sha256"]:
                raise AdapterError("Concurrent configuration/enrollment source drift")
            old_block = self.block(owner, config_policy(parse(old_config), self.username),
                                   peer_policy(parse(old_peers)), old)
        base = self.unowned(before, owner, old_block)
        state = dict(old or {})
        peers_bytes = encode({"peers": peers})
        state.update({"schema": 1, "enabled": enabled, "installed": installed,
                      "separator_added": bool(enabled and peers and base and not base.endswith(b"\n")),
                      "config_sha256": sha(encode(config)), "peers_sha256": sha(peers_bytes),
                      "host_key_sha256": sha(self.host_key(config).encode())})
        if slot:
            state["slot"] = slot
        elif not installed:
            state["slot"] = None
        block = self.block(owner, config, peers, state)
        state["block_sha256"] = sha(block)
        updates = {self.config_dir / "gateway.json": encode(config),
                   self.config_dir / "peers.json": peers_bytes}
        desired = base + block
        if before is not None or desired:
            updates[self.keys] = desired
        updates[self.state_dir / "active.json"] = encode(state)
        updates.update(extra_updates or {})
        self.transaction(updates, expected={self.keys: before})
        return state

    def install(self, source, config_path, accept_config=False):
        desired = config_policy(parse(Path(config_path).read_bytes()), self.username)
        self.host_key(desired)
        if not self.probe(desired["server_port"]):
            raise AdapterError("Provider-owned platform SSH banner unavailable; do not start/reconfigure sshd")
        owner = self.ownership(create=True)
        old = self.state()
        peers = []
        if old:
            _, config, peers, old = self.checked()
            if desired != config:
                if not accept_config:
                    raise AdapterError("Desired config differs; review then use --accept-config")
                if ({k: v for k, v in desired.items() if k != "gateway_http"}
                        != {k: v for k, v in config.items() if k != "gateway_http"}):
                    raise AdapterError("Only the fixed Gateway HTTP opt-in may change")
        else:
            self.unowned(self.key_snapshot(), owner, b"")
            if any(self.config_dir.iterdir()):
                raise AdapterError("Unattributed configuration requires inspection")
        script = Path(source).read_bytes()
        slot = sha(script)
        slot_dir = self.root / "versions" / slot
        if slot_dir.exists():
            self.check_slot({"slot": slot})
        else:
            slot_dir.mkdir(mode=0o700)
            self.write(slot_dir / "gateway_openssh.py", script)
            self.write(slot_dir / "slot.json", encode({"schema": 1, "service": SERVICE, "script_sha256": slot}))
        launcher = self.root / "bin" / SERVICE
        if launcher.exists() and self.read(launcher) != self.launcher_bytes():
            raise AdapterError("Stable launcher drift")
        if not launcher.exists():
            self.write(launcher, self.launcher_bytes(), mode=0o700)

        self.converge(owner, desired, peers, old, True, slot=slot, extra_updates={
            self.root / "current-version": slot.encode(),
            self.root / "deploy-manifest.json": encode({
                "schema": 1, "service": SERVICE, "backend": BACKEND,
                "script_sha256": slot, "config_sha256": sha(encode(desired)),
                "provider": "existing-platform-sshd", "provider_owned": True,
                "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }),
        })
        return self.status()

    def import_peers(self, payload):
        owner, config, peers, state = self.checked()
        if not state["installed"]:
            raise AdapterError("Install the adapter before importing peers")
        merged = {row["machine"]: row for row in peers}
        for row in peer_policy(payload):
            if row["machine"] in merged and merged[row["machine"]] != row:
                raise AdapterError("Existing public enrollment differs; no implicit key rotation")
            merged[row["machine"]] = row
        records = peer_policy({"peers": list(merged.values())})
        self.converge(owner, config, records, state, state["enabled"])
        return self.status()

    def control(self, enabled):
        owner, config, peers, state = self.checked()
        if not state["installed"]:
            raise AdapterError("Adapter is not installed")
        self.converge(owner, config, peers, state, enabled)
        return self.status()

    def status(self):
        if not self.root.exists():
            return {"installed": False, "enrollment_enabled": False, "authenticated": "unknown"}
        owner = self.ownership()
        if self.state() is None:
            if any(self.config_dir.iterdir()):
                raise AdapterError("Incomplete adapter configuration requires inspection")
            self.unowned(self.key_snapshot(), owner, b"")
            return {"installed": False, "enrollment_enabled": False,
                    "configuration_retained": False, "authenticated": "unknown",
                    "revocation_scope": "new-authentications-only", "existing_sessions": "unknown"}
        _, config, peers, state = self.checked()
        banner = self.probe(config["server_port"])
        enrolled = bool(state["enabled"] and peers)
        return {
            "installed": state["installed"], "enabled": state["enabled"],
            "enrollment_enabled": enrolled, "enrolled_machines": [row["machine"] for row in peers],
            "platform_ssh": {"provider_owned": True, "server_port": 22,
                             "banner": banner, "observed": bool(banner)},
            "route_health": [{"machine": name, "relay_port": config["peers"][name],
                             "ssh_banner": self.probe(config["peers"][name]), "authenticated": "unknown"}
                            for name in MACHINES],
            "authenticated": "unknown", "listener_scope": "provider-dependent, unverified",
            "revocation_scope": "new-authentications-only", "existing_sessions": "unknown",
            "readiness_scope": "owned public-key enrollment and platform SSH banner only",
            "gateway_http_configured": config.get("gateway_http", False),
            "gateway_http_authorized": bool(enrolled and config.get("gateway_http")),
        }

    def export_client(self, machine):
        _, config, _, state = self.checked()
        if not state["installed"]:
            raise AdapterError("Adapter is not installed")
        name = canonical(machine)
        return {
            "schema": 1, "backend": BACKEND, "machine": name, "gateway_user": self.username,
            "host_key": self.host_key(config), "server_port": 22, "relay_port": config["peers"][name],
            "peers": [{"machine": name, "relay_port": config["peers"][name]} for name in MACHINES],
        }

    def export_directory(self):
        _, _, peers, _ = self.checked()
        return {"peers": peers}

    def uninstall(self, remove_config=False):
        owner, config, peers, state = self.checked()
        for slot in (self.root / "versions").iterdir():
            self.check_slot({"slot": slot.name})
        self.converge(owner, config, peers, state, False, installed=False)
        for path in (self.root / "current-version", self.root / "deploy-manifest.json",
                     self.root / "bin" / SERVICE):
            path.unlink(missing_ok=True)
        for slot in (self.root / "versions").iterdir():
            (slot / "gateway_openssh.py").unlink()
            (slot / "slot.json").unlink()
            slot.rmdir()
        if remove_config:
            for name in ("gateway.json", "peers.json"):
                (self.config_dir / name).unlink()
            (self.state_dir / "active.json").unlink()
        return {"installed": False, "enrollment_enabled": False,
                "configuration_retained": not remove_config,
                "revocation_scope": "new-authentications-only", "existing_sessions": "unknown"}


@contextlib.contextmanager
def locked(adapter, create=False):
    import fcntl
    adapter.ownership(create=create)
    path = adapter.root / "lifecycle.lock"
    no_links(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdapterError("Another adapter action owns this root") from exc
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    for verb in ("install", "update"):
        command_parser = actions.add_parser(verb)
        command_parser.add_argument("--config", type=Path, required=True)
        command_parser.add_argument("--accept-config", action="store_true")
    for verb in ("status", "start", "stop", "import-peers", "export-directory"):
        actions.add_parser(verb)
    actions.add_parser("export-client").add_argument("--machine", required=True)
    actions.add_parser("uninstall").add_argument("--remove-config", action="store_true")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise AdapterError("This adapter runs on the Linux Gateway")
        import pwd
        account = pwd.getpwuid(os.getuid())
        home = Path(account.pw_dir)
        adapter = Adapter(args.root or home / SERVICE, home, account.pw_name, os.getuid())
        if args.action == "status" and not adapter.root.exists():
            result = adapter.status()
        else:
            with locked(adapter, create=args.action in ("install", "update")):
                if args.action in ("install", "update"):
                    result = adapter.install(Path(__file__), args.config, args.accept_config)
                elif args.action == "import-peers":
                    result = adapter.import_peers(parse(sys.stdin.buffer.read(1024 * 1024 + 1)))
                elif args.action == "export-client":
                    result = adapter.export_client(args.machine)
                elif args.action in ("start", "stop"):
                    result = adapter.control(args.action == "start")
                elif args.action == "uninstall":
                    result = adapter.uninstall(args.remove_config)
                else:
                    result = getattr(adapter, args.action.replace("-", "_"))()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AdapterError, OSError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, AdapterError) else "Local adapter state is invalid/unreadable"
        print(json.dumps({"error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
