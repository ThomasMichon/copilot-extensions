"""Experimental reference implementation; see the parent README."""

import argparse
import contextlib
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


SERVICE = "gateway-ssh-mesh"
VERSION = "1.12.0"
ASSETS = {
    "x86_64": ("chisel_1.12.0_linux_amd64.gz",
               "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"),
    "aarch64": ("chisel_1.12.0_linux_arm64.gz",
                "2ec6152cd2c74fe0146d4d79e4e7aa174521368c56e433d55e023a92ea404ec3"),
}
PEERS = dict(zip(
    ("operator-node-a", "operator-node-b", "operator-node-c",
     "operator-node-d", "operator-node-e"), range(23001, 23006)))
DEFAULT = {
    "schema": 1, "service": SERVICE, "server_host": "0.0.0.0",
    "server_port": 18443, "candidate_host": "127.0.0.1",
    "candidate_port": 18445, "keepalive": "15s", "peers": PEERS,
}
PREFIX = "private.gateway-ssh-mesh."
FINGERPRINT = re.compile(
    r"(?:^|\n)[^\n]*server: Fingerprint ([A-Za-z0-9+/]{43}=)\r?(?:\n|$)")


class GatewayError(Exception):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GatewayError("Cannot read valid JSON-subset YAML/JSON: " + path.name) from exc


def write_file(path, data, mode=0o600):
    if path.is_symlink():
        raise GatewayError("Refusing symlink: " + path.name)
    pending = path.with_name(path.name + ".pending")
    if pending.exists() or pending.is_symlink():
        raise GatewayError("Unfinished write requires inspection: " + pending.name)
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(pending, mode)
        os.replace(pending, path)
    finally:
        if pending.exists():
            pending.unlink()


def validate_config(value):
    if value != DEFAULT:
        raise GatewayError("Configuration must match the reviewed fixed peer/port policy")
    return value


def route_rules(machine):
    if machine not in PEERS:
        raise GatewayError("Unknown canonical machine")
    return [
        rf"^R:127\.0\.0\.1:{PEERS[machine]}$",
        r"^127\.0\.0\.1:(23001|23002|23003|23004|23005)$",
    ]


def unpack_verified(archive, expected):
    if digest(archive) != expected:
        raise GatewayError("Chisel archive SHA256 mismatch; refused before decompression")
    try:
        return gzip.decompress(archive)
    except (OSError, EOFError) as exc:
        raise GatewayError("Verified archive is not valid gzip") from exc


def run(argv, cwd=None, timeout=90, include_stderr=False):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GatewayError("Command unavailable or timed out: " + Path(argv[0]).name) from exc
    if result.returncode:

        raise GatewayError("Command failed: " + Path(argv[0]).name +
                           " (exit " + str(result.returncode) + ")")
    return result.stdout + result.stderr if include_stderr else result.stdout


class Docker:
    def call(self, *args):
        return run(["docker", *map(str, args)], include_stderr=args[0] == "logs")

    def inspect(self, name):
        names = self.call("container", "ls", "-a", "--format", "{{.Names}}").splitlines()
        if name not in names:
            return None
        try:
            return json.loads(self.call("inspect", name))[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise GatewayError("Invalid Docker inspect response") from exc


def download(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read(32 * 1024 * 1024 + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise GatewayError("Chisel release download failed") from exc


def endpoint(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


class Gateway:
    def __init__(self, root, docker=None, fetch=download, probe=endpoint):
        self.root = Path(root).expanduser().absolute()
        self.docker = docker or Docker()
        self.fetch = fetch
        self.probe = probe
        self.config = self.root / "config"
        self.state_dir = self.root / "state"
        self.versions = self.root / "versions"

    def owned_root(self, create=False):
        if self.root.is_symlink():
            raise GatewayError("Refusing symlink root")
        marker = self.root / "owner.json"
        expected = {"service": SERVICE, "root": str(self.root)}
        if not self.root.exists():
            if not create:
                raise GatewayError("Gateway is not installed")
            self.root.mkdir(mode=0o700, parents=True)
        if not marker.exists():
            if not create or any(self.root.iterdir()):
                raise GatewayError("Refusing foreign or unmarked root")
            os.chmod(self.root, 0o700)
            write_file(marker, encoded(expected))
        if read_json(marker) != expected:
            raise GatewayError("Root ownership marker mismatch")
        allowed = {"owner.json", "config", "state", "versions", "current-version",
                   SERVICE, "deploy-manifest.json", "lifecycle.lock"}
        if {p.name for p in self.root.iterdir()} - allowed:
            raise GatewayError("Unexpected user state in owned root; refused")
        for path in [self.root, *self.root.rglob("*")]:
            if path.is_symlink():
                raise GatewayError("Refusing symlink in owned root")
            if os.name != "nt":
                info = path.stat()
                if info.st_uid != os.getuid():
                    raise GatewayError("Runtime is not owned by the current user")
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise GatewayError("Runtime permissions must exclude group/other")
        if create:
            for path in (self.config, self.state_dir, self.versions):
                path.mkdir(mode=0o700, exist_ok=True)

    def state(self):
        path = self.state_dir / "active.json"
        return read_json(path) if path.exists() else None

    def material_digest(self):
        paths = sorted(self.config.iterdir())
        expected = {"gateway.yaml", "users.json", "server-key",
                    *(machine + ".secret" for machine in PEERS)}
        if {p.name for p in paths} != expected or any(not p.is_file() for p in paths):
            raise GatewayError("Unexpected or incomplete protected configuration")
        validate_config(read_json(self.config / "gateway.yaml"))
        users = {}
        for machine in PEERS:
            secret = (self.config / (machine + ".secret")).read_text(encoding="utf-8")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
                raise GatewayError("Malformed machine credential")
            users[machine + ":" + secret] = route_rules(machine)
        if read_json(self.config / "users.json") != users:
            raise GatewayError("Authfile route or credential drift")
        return digest(encoded({p.name: digest(p.read_bytes()) for p in paths}))

    def check_material(self):
        manifest = read_json(self.state_dir / "materials.json")
        observed = self.material_digest()
        if manifest != {"configdigest": observed}:
            raise GatewayError("Configuration/key/credential drift; refused")
        return observed

    def prepare_material(self, slot, desired):
        marker = self.state_dir / "materials.json"
        if marker.exists():
            result = self.check_material()
            if read_json(self.config / "gateway.yaml") != desired:
                raise GatewayError("Desired configuration differs; explicit review required")
            return result
        if any(self.config.iterdir()):
            raise GatewayError("Incomplete initialization; preserve and inspect config")
        write_file(self.config / "gateway.yaml", encoded(desired))
        users = {}
        for machine in PEERS:
            secret = secrets.token_urlsafe(32)
            write_file(self.config / (machine + ".secret"), secret.encode())
            users[machine + ":" + secret] = route_rules(machine)
        write_file(self.config / "users.json", encoded(users))
        run([str(slot / "chisel"), "server", "--keygen", str(self.config / "server-key")],
            cwd=self.root)
        os.chmod(self.config / "server-key", 0o600)
        result = self.material_digest()
        write_file(marker, encoded({"configdigest": result}))
        return result

    def labels(self, state):
        return {
            PREFIX + "owner": SERVICE, PREFIX + "root": str(self.root),
            PREFIX + "version": state["slot"], PREFIX + "configdigest": state["configdigest"],
        }

    def require_container(self, name, state):
        info = self.docker.inspect(name)
        if info is None:
            return None
        actual = info.get("Config", {}).get("Labels") or {}
        if any(actual.get(key) != value for key, value in self.labels(state).items()):
            raise GatewayError("Foreign container or ownership/version/configdigest drift: " + name)
        if info.get("Config", {}).get("Image") != state["image_id"]:
            raise GatewayError("Container image drift: " + name)
        if info.get("Image") != state["image_id"]:
            raise GatewayError("Container image identity drift: " + name)
        host = info.get("HostConfig", {})
        candidate = name == SERVICE + "-candidate"
        expected_cmd = self.server_args(candidate)
        if (info["Config"].get("Cmd") != expected_cmd or
                info["Config"].get("User") != f"{os.getuid()}:{os.getgid()}" or
                host.get("NetworkMode") != "host" or not host.get("ReadonlyRootfs") or
                host.get("CapDrop") != ["ALL"] or
                host.get("SecurityOpt") != ["no-new-privileges"] or
                host.get("RestartPolicy", {}).get("Name") !=
                ("no" if candidate else "unless-stopped") or
                host.get("LogConfig") != {"Type": "json-file",
                                         "Config": {"max-size": "5m", "max-file": "3"}}):
            raise GatewayError("Container security/command configuration drift: " + name)
        mounts = info.get("Mounts", [])
        if (len(mounts) != 1 or mounts[0].get("Type") != "bind" or
                mounts[0].get("Source") != str(self.config) or
                mounts[0].get("Destination") != "/config" or mounts[0].get("RW") is not False):
            raise GatewayError("Container config mount drift: " + name)
        return info

    def remove(self, name, state):
        if self.require_container(name, state):
            cache = self.startup_cache()
            self.docker.call("rm", "-f", name)
            if cache["records"].pop(name, None) is not None:
                write_file(self.state_dir / "startup-verification.json", encoded(cache))

    def create_container(self, name, state, candidate=False):
        if self.docker.inspect(name):
            raise GatewayError("Container name already occupied: " + name)
        args = [
            "run", "-d", "--name", name, "--network", "host",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--restart", "no" if candidate else "unless-stopped",
            "--log-driver", "json-file", "--log-opt", "max-size=5m",
            "--log-opt", "max-file=3",
            "--mount", f"type=bind,source={self.config},target=/config,readonly",
        ]
        for key, value in self.labels(state).items():
            args += ["--label", key + "=" + value]
        args += [state["image_id"], *self.server_args(candidate)]
        self.docker.call(*args)

    @staticmethod
    def server_args(candidate):
        return [
            "server", "--host", "127.0.0.1" if candidate else "0.0.0.0",
            "--port", "18445" if candidate else "18443", "--reverse",
            "--keyfile", "/config/server-key", "--authfile", "/config/users.json",
            "--keepalive", "15s",
        ]

    def observed(self, name, state, port):
        info = self.require_container(name, state)
        if not info or not info.get("State", {}).get("Running"):
            return {"running": False, "ready": False, "version": None, "fingerprint": None}
        provenance = self.run_provenance(name, info, state, port)
        version = self.docker.call("exec", name, "/chisel", "--version").strip()
        cache = self.startup_cache()
        record = cache["records"].get(name)
        cached = bool(record and record["provenance"] == provenance)
        fingerprint = None
        listener_log = False
        if cached:
            fingerprint = record["fingerprint"]
            listener_log = True
        else:


            logs = self.docker.call("logs", "--since", provenance["started_at"], name)
            fingerprints = FINGERPRINT.findall(logs)
            fingerprint = fingerprints[-1] if fingerprints else None
            listener_log = re.search(
                rf"(?:^|\n)[^\n]*server: Listening on http://"
                rf"{re.escape(provenance['host'])}:{port}\r?(?:\n|$)", logs) is not None
        if fingerprint and state.get("fingerprint") and fingerprint != state["fingerprint"]:
            raise GatewayError("Server fingerprint drift")
        listening = self.probe(port)
        current = self.require_container(name, state)
        if (not current or not current.get("State", {}).get("Running") or
                self.run_provenance(name, current, state, port) != provenance):
            return {"running": bool(current and current.get("State", {}).get("Running")),
                    "ready": False, "version": None, "fingerprint": None,
                    "startup_identity": "unverified"}
        ready = version == VERSION and bool(fingerprint) and listening and listener_log
        if ready and not cached:
            cache["records"][name] = {
                "provenance": provenance, "fingerprint": fingerprint,
                "version": VERSION, "listener_verified": True,
                "source": "docker-startup-logs",
                "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            write_file(self.state_dir / "startup-verification.json", encoded(cache))
        return {
            "running": True, "ready": ready,
            "version": version, "fingerprint": fingerprint, "endpoint_reachable": listening,
            "listener_reported": listener_log,
            "startup_identity": "verified-cache" if cached else
            ("verified-startup-logs" if ready else "unverified"),
            "container_id": provenance["container_id"], "started_at": provenance["started_at"],
        }

    def startup_cache(self):
        path = self.state_dir / "startup-verification.json"
        empty = {"schema": 1, "service": SERVICE, "root": str(self.root), "records": {}}
        if not path.exists():
            return empty
        cache = read_json(path)
        if (not isinstance(cache, dict) or set(cache) != set(empty) or
                any(cache[key] != empty[key] for key in ("schema", "service", "root")) or
                not isinstance(cache["records"], dict) or
                set(cache["records"]) - {SERVICE, SERVICE + "-candidate"}):
            raise GatewayError("Startup verification cache provenance is invalid")
        provenance_keys = {"root", "name", "container_id", "started_at", "image_id", "slot",
                           "slot_manifest_sha256", "configdigest", "host", "port"}
        for name, record in cache["records"].items():
            if (not isinstance(record, dict) or
                    set(record) != {"provenance", "fingerprint", "version", "listener_verified",
                                    "source", "verified_at"} or
                    not isinstance(record["provenance"], dict) or
                    set(record["provenance"]) != provenance_keys or
                    record["provenance"]["root"] != str(self.root) or
                    record["provenance"]["name"] != name or
                    not isinstance(record["fingerprint"], str) or
                    not re.fullmatch(r"[A-Za-z0-9+/]{43}=", record["fingerprint"]) or
                    record["version"] != VERSION or record["listener_verified"] is not True or
                    record["source"] != "docker-startup-logs" or
                    not isinstance(record["verified_at"], str)):
                raise GatewayError("Startup verification cache record is invalid")
        return cache

    def run_provenance(self, name, info, state, port):
        container_id = info.get("Id")
        started_at = info.get("State", {}).get("StartedAt")
        if (not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id) or
                not isinstance(started_at, str) or started_at.startswith("0001-") or
                not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", started_at)):
            raise GatewayError("Docker container run identity is invalid")
        return {
            "root": str(self.root), "name": name, "container_id": container_id,
            "started_at": started_at, "image_id": info["Image"],
            "slot": state["slot"], "slot_manifest_sha256": state["slot_manifest_sha256"],
            "configdigest": state["configdigest"],
            "host": "127.0.0.1" if name == SERVICE + "-candidate" else "0.0.0.0",
            "port": port,
        }

    def wait_ready(self, name, state, port):
        for _ in range(30):
            result = self.observed(name, state, port)
            if result["ready"]:
                return result
            time.sleep(0.5)
        raise GatewayError("Readiness failed: running/version/fingerprint/listener/TCP gate")

    def check_slot(self, state):
        if not re.fullmatch(r"1\.12\.0-[a-f0-9]{16}-[a-f0-9]{16}", state["slot"]):
            raise GatewayError("Invalid immutable slot identifier")
        slot = self.versions / state["slot"]
        if {p.name for p in slot.iterdir()} != {
                "chisel", "gateway.py", "gateway.yaml", "Dockerfile", "slot.json"}:
            raise GatewayError("Unexpected immutable slot contents")
        manifest = read_json(slot / "slot.json")
        if state.get("slot_manifest_sha256") and (
                digest((slot / "slot.json").read_bytes()) != state["slot_manifest_sha256"]):
            raise GatewayError("Installed slot metadata drift")
        if manifest["slot"] != state["slot"] or manifest["image"] != state["image"]:
            raise GatewayError("Installed slot manifest drift")
        if state.get("image_id") and manifest["image_id"] != state["image_id"]:
            raise GatewayError("Installed image identity drift")
        if self.image_identity(state["image"]) != manifest["image_id"]:
            raise GatewayError("Docker immutable image tag drift")
        if set(manifest["files"]) != {"chisel", "gateway.py", "gateway.yaml", "Dockerfile"}:
            raise GatewayError("Invalid immutable slot file inventory")
        for name, expected in manifest["files"].items():
            if digest((slot / name).read_bytes()) != expected:
                raise GatewayError("Installed immutable slot drift: " + name)
        return slot

    def image_identity(self, image):
        try:
            value = json.loads(self.docker.call("image", "inspect", image))[0]["Id"]
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise GatewayError("Invalid Docker image inspect response") from exc
        if not isinstance(value, str):
            raise GatewayError("Invalid Docker image identity")
        return value

    def prepare_slot(self, source, config_source):
        architecture = platform.machine().lower()
        if architecture not in ASSETS:
            raise GatewayError("Unsupported Linux architecture")
        asset, archive_hash = ASSETS[architecture]
        script = Path(source).read_bytes()
        desired = validate_config(read_json(Path(config_source)))
        slot_id = VERSION + "-" + archive_hash[:16] + "-" + digest(script)[:16]
        slot = self.versions / slot_id
        image = SERVICE + ":" + slot_id
        if slot.exists():
            manifest = read_json(slot / "slot.json")
            self.check_slot({"slot": slot_id, "image": image})
            if manifest["archive_sha256"] != archive_hash:
                raise GatewayError("Archive provenance drift")
            return slot, desired, image
        archive = self.fetch(
            "https://github.com/jpillora/chisel/releases/download/v" + VERSION + "/" + asset)
        binary = unpack_verified(archive, archive_hash)
        slot.mkdir(mode=0o700)
        write_file(slot / "chisel", binary, 0o700)
        write_file(slot / "gateway.py", script)
        write_file(slot / "gateway.yaml", encoded(desired))

        dockerfile = b"FROM scratch\nCOPY --chmod=0555 chisel /chisel\nENTRYPOINT [\"/chisel\"]\n"
        write_file(slot / "Dockerfile", dockerfile)
        if run([str(slot / "chisel"), "--version"], cwd=self.root).strip() != VERSION:
            raise GatewayError("Downloaded Chisel reported unexpected version")
        self.docker.call("build", "--network", "none", "--pull=false", "-t", image, str(slot))
        image_id = self.image_identity(image)
        write_file(slot / "slot.json", encoded({
            "slot": slot_id, "image": image, "image_id": image_id, "archive_sha256": archive_hash,
            "files": {name: digest((slot / name).read_bytes()) for name in
                      ("chisel", "gateway.py", "gateway.yaml", "Dockerfile")},
        }))
        return slot, desired, image

    def launcher(self, create=False):
        root = shlex.quote(str(self.root))
        body = (
            "#!/bin/sh\nset -eu\nROOT=" + root + "\n"
            'SLOT=$(cat "$ROOT/current-version")\n'
            'case "$SLOT" in *[!0-9a-f.-]*) echo "Invalid runtime selection" >&2; exit 1;; esac\n'
            'case "$SLOT" in 1.12.0-????????????????-????????????????) ;; '
            '*) echo "Invalid runtime selection" >&2; exit 1;; esac\n'
            'cd "$ROOT"\n'
            'exec python3 "$ROOT/versions/$SLOT/gateway.py" --root "$ROOT" "$@"\n'
        ).encode()
        path = self.root / SERVICE
        if path.exists() and path.read_bytes() != body:
            raise GatewayError("Stable launcher drift")
        if not path.exists():
            if not create:
                raise GatewayError("Stable launcher is missing")
            write_file(path, body, 0o700)

    def activate(self, new, old):
        candidate = SERVICE + "-candidate"
        if self.docker.inspect(candidate):
            raise GatewayError("Stale/foreign candidate requires inspection; refused")
        if self.probe(18445):
            raise GatewayError("Candidate port already occupied")
        try:
            self.create_container(candidate, new, candidate=True)
            ready = self.wait_ready(candidate, new, 18445)
            new["fingerprint"] = ready["fingerprint"]
        finally:
            self.remove(candidate, new)
        if old:
            self.remove(SERVICE, old)
        elif self.docker.inspect(SERVICE):
            raise GatewayError("Refusing foreign active container")
        try:
            self.create_container(SERVICE, new)
            self.wait_ready(SERVICE, new, 18443)
        except GatewayError as activation_error:
            self.remove(SERVICE, new)
            if old:
                try:
                    self.create_container(SERVICE, old)
                    self.wait_ready(SERVICE, old, 18443)
                except GatewayError as rollback_error:
                    raise GatewayError("Activation failed; rollback readiness also failed") from rollback_error
            raise GatewayError("Activation failed; prior deployment restored" if old else
                               "First activation failed; configuration retained") from activation_error

    def install(self, source, config_source):
        self.owned_root(create=True)
        old = self.state()
        if old:
            self.checked_active()
        elif self.docker.inspect(SERVICE):
            raise GatewayError("Foreign active container; refused")
        self.launcher(create=True)
        slot, desired, image = self.prepare_slot(source, config_source)
        configdigest = self.prepare_material(slot, desired)
        new = {"slot": slot.name, "image": image,
               "image_id": read_json(slot / "slot.json")["image_id"],
               "slot_manifest_sha256": digest((slot / "slot.json").read_bytes()),
               "configdigest": configdigest}
        if old and old["slot"] == new["slot"]:
            info = self.require_container(SERVICE, old)
            if info and info["State"]["Running"]:
                self.wait_ready(SERVICE, old, 18443)
            else:
                self.start()
            return self.status()
        if old:
            new["fingerprint"] = old["fingerprint"]
        self.activate(new, old)
        self.commit_selection(new, old, source, slot)
        return self.status()

    def commit_selection(self, new, old, source, slot):

        receipt = encoded({
            "schema_version": 1, "service": SERVICE, "version": VERSION,
            "slot": slot.name, "installer_sha256": digest(Path(source).read_bytes()),
            "archive_sha256": read_json(slot / "slot.json")["archive_sha256"],
            "configdigest": new["configdigest"], "fingerprint": new["fingerprint"],
            "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "lifecycle": "container-managed", "root": str(self.root),
        })
        paths = [self.state_dir / "previous.json", self.state_dir / "active.json",
                 self.root / "current-version", self.root / "deploy-manifest.json"]
        prior = {path: path.read_bytes() if path.exists() else None for path in paths}
        try:
            if old:
                write_file(paths[0], encoded(old))
            write_file(paths[1], encoded(new))
            write_file(paths[2], slot.name.encode())
            write_file(paths[3], receipt)
        except (GatewayError, OSError) as commit_error:
            try:
                self.remove(SERVICE, new)
                if old:
                    self.create_container(SERVICE, old)
                    self.wait_ready(SERVICE, old, 18443)
                for path, content in prior.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        write_file(path, content)
            except (GatewayError, OSError) as rollback_error:
                raise GatewayError("Selection commit failed; rollback incomplete, inspect runtime") from rollback_error
            raise GatewayError("Selection commit failed; prior deployment restored" if old else
                               "Selection commit failed; configuration retained") from commit_error

    def checked_active(self):
        self.owned_root()
        state = self.state()
        if not state:
            raise GatewayError("No active installation")
        if self.check_material() != state["configdigest"]:
            raise GatewayError("Active configuration digest mismatch")
        self.check_slot(state)
        if (self.root / "current-version").read_text() != state["slot"]:
            raise GatewayError("Active version marker drift")
        self.launcher()
        self.require_container(SERVICE, state)
        return state

    def start(self):
        state = self.checked_active()
        info = self.require_container(SERVICE, state)
        if info:
            if not info["State"]["Running"]:
                self.docker.call("start", SERVICE)
        else:
            self.create_container(SERVICE, state)
        self.wait_ready(SERVICE, state, 18443)
        return self.status()

    def stop(self):
        state = self.checked_active()
        info = self.require_container(SERVICE, state)
        if info and info["State"]["Running"]:
            self.docker.call("stop", "--time", "15", SERVICE)
        return self.status()

    def status(self):
        if not self.root.exists():
            return {"installed": False, "running": False, "ready": False,
                    "host_enrollment": "unknown", "client_routes": "unknown"}
        self.owned_root()
        if not self.state():
            if (self.state_dir / "materials.json").exists():
                self.check_material()
            return {"installed": False, "running": False, "ready": False,
                    "configuration_retained": bool(list(self.config.iterdir())),
                    "host_enrollment": "unknown", "client_routes": "unknown"}
        state = self.checked_active()
        return {"installed": True, "root": str(self.root), "slot": state["slot"],
                **self.observed(SERVICE, state, 18443),
                "host_enrollment": "unknown", "client_routes": "unknown",
                "readiness_scope": "owned container + exact version + run-bound verified startup identity + TCP only"}

    def export_client(self, machine, secret_stdout):
        if not secret_stdout:
            raise GatewayError("Enrollment requires explicit --secret-stdout")
        if machine not in PEERS:
            raise GatewayError("Unknown canonical machine")
        state = self.checked_active()
        self.wait_ready(SERVICE, state, 18443)
        secret = (self.config / (machine + ".secret")).read_text(encoding="utf-8")
        return {
            "machine": machine, "auth": machine + ":" + secret,
            "fingerprint": state["fingerprint"], "relay_port": PEERS[machine],
            "server_port": 18443,
            "peers": [{"machine": name, "relay_port": port} for name, port in PEERS.items()],
        }

    def uninstall(self, remove_config=False):
        self.owned_root()
        if self.docker.inspect(SERVICE + "-candidate"):
            raise GatewayError("Candidate exists; inspect before uninstall")
        for slot in self.versions.iterdir():
            manifest = read_json(slot / "slot.json")
            self.check_slot({"slot": slot.name, "image": manifest["image"]})
        state = self.state()
        if state:
            self.checked_active()
            self.remove(SERVICE, state)
        elif self.docker.inspect(SERVICE):
            raise GatewayError("Unattributed active container; refused")

        for name in ("current-version", SERVICE, "deploy-manifest.json"):
            (self.root / name).unlink(missing_ok=True)
        for name in ("active.json", "previous.json"):
            (self.state_dir / name).unlink(missing_ok=True)
        (self.state_dir / "startup-verification.json").unlink(missing_ok=True)
        if self.versions.exists():
            shutil.rmtree(self.versions)
            self.versions.mkdir(mode=0o700)
        if remove_config:
            if (self.state_dir / "materials.json").exists():
                self.check_material()
            shutil.rmtree(self.config)
            self.config.mkdir(mode=0o700)
            (self.state_dir / "materials.json").unlink(missing_ok=True)
        return {"installed": False, "running": False, "configuration_retained": not remove_config}


@contextlib.contextmanager
def locked(gateway, create=False):
    import fcntl
    gateway.owned_root(create=create)
    path = gateway.root / "lifecycle.lock"
    with path.open("a", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GatewayError("Another lifecycle action owns this root") from exc
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path.home() / SERVICE))
    actions = parser.add_subparsers(dest="action", required=True)
    for verb in ("install", "update"):
        sub = actions.add_parser(verb)
        sub.add_argument("--config", type=Path, default=Path(__file__).with_name("gateway.yaml"))
    for verb in ("start", "stop", "status"):
        actions.add_parser(verb)
    sub = actions.add_parser("uninstall")
    sub.add_argument("--remove-config", action="store_true")
    sub = actions.add_parser("export-client")
    sub.add_argument("--machine", required=True, choices=list(PEERS))
    sub.add_argument("--secret-stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux" or os.getuid() == 0:
            raise GatewayError("Run on Linux as the Docker-enabled non-root user; no sudo")
        gateway = Gateway(args.root)
        if args.action == "status" and not gateway.root.exists():
            result = gateway.status()
        else:
            with locked(gateway, create=args.action in ("install", "update")):
                if args.action in ("install", "update"):
                    result = gateway.install(Path(__file__), args.config)
                elif args.action == "uninstall":
                    result = gateway.uninstall(args.remove_config)
                elif args.action == "export-client":
                    result = gateway.export_client(args.machine, args.secret_stdout)
                else:
                    result = getattr(gateway, args.action)()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (GatewayError, OSError, KeyError, TypeError) as exc:

        message = str(exc) if isinstance(exc, GatewayError) else "Local runtime state is invalid/unreadable"
        print(json.dumps({"error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
