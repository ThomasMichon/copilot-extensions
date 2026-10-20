"""Bounded ordinary Docker composition, receipts first, exact-ID teardown only."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from architecture import NativeArchitecture
from source import ProofError, save

ID = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
PROBE_MODES = frozenset({
    "ready", "absent", "routes", "denials", "stream", "updated",
    "replacement", "reconnected", "revoked",
})
IDENTITY_CODE = (
    "import json,os;from pathlib import Path;"
    "s=Path('/proc/1/stat').read_text().rsplit(')',1)[1].split();"
    "print(json.dumps({'pid':1,'start_ticks':s[19],"
    "'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),"
    "'argv':Path('/proc/1/cmdline').read_bytes().replace(bytes([0]),b' ').decode(),"
    "'uid':os.getuid()}))"
)


class Engine:
    def __init__(self, root: Path, receipt: dict, architecture: str = "amd64") -> None:
        self.architecture = NativeArchitecture(architecture)
        self.root, self.receipt = root, receipt
        self.deadline = time.monotonic() + 300
        self.sequence = 0
        self.network: str | None = None
        self.containers: list[str] = []
        self.pending: list[Path] = []
        self.ambiguous_network = False
        self.logs = root / "cr-logs"
        self.logs.mkdir(mode=0o700)
        (root / "docker-config").mkdir(mode=0o700)
        (root / "docker-home").mkdir(mode=0o700)

    def journal(self) -> None:
        self.receipt["container_ids"] = list(self.containers)
        self.receipt["network_id"] = self.network
        save(self.root / "receipt.json", self.receipt)

    @staticmethod
    def write_log(path: Path, data: bytes) -> None:
        try:
            path.write_bytes(data)
        except OSError:
            raise ProofError("docker-log-write-failed") from None

    def call(self, *args: str, timeout: float = 20, build: bool = False,
             cleanup: bool = False,
             on_success: Callable[[bytes], None] | None = None) -> str:
        remaining = self.deadline - time.monotonic()
        if not build and not cleanup:
            if remaining <= 0:
                raise ProofError("witness-deadline", "runtime-install")
            timeout = min(timeout, remaining)
        self.sequence += 1
        log = self.logs / f"{self.sequence:03d}-{args[0]}.log"
        env = {"PATH": os.environ.get("PATH", os.defpath),
               "HOME": str(self.root / "docker-home"),
               "DOCKER_CONFIG": str(self.root / "docker-config")}
        try:
            result = subprocess.run(
                ["docker", *args], cwd=self.root, env=env, capture_output=True,
                timeout=timeout, check=False,
            )
        except FileNotFoundError:
            self.write_log(log, b"docker executable unavailable\n")
            raise ProofError("docker-unavailable") from None
        except OSError:
            self.write_log(log, b"Docker command could not be launched\n")
            raise ProofError("docker-command-unavailable") from None
        except subprocess.TimeoutExpired:
            self.write_log(log, b"bounded Docker command timed out\n")
            raise ProofError("docker-command-timeout") from None
        if result.returncode == 0 and on_success is not None:
            on_success(result.stdout)
        # Fixture processes print only classified outcomes; never credential contents.
        self.write_log(log, result.stdout + result.stderr)
        if result.returncode:
            raise ProofError("docker-command-failed")
        return result.stdout.decode("utf-8").strip()

    def preflight(self) -> None:
        facts = json.loads(self.call("info", "--format",
                                    '{"os":"{{.OSType}}","arch":"{{.Architecture}}",'
                                    '"version":"{{.ServerVersion}}"}'))
        self.architecture.require_engine(facts)
        self.receipt["engine"] = facts

    def build(self, sha: str, timeout: int) -> str:
        iid = self.root / "image-id"
        self.call("build", "--pull", "--no-cache", "--platform", self.architecture.platform,
                  "--iidfile", str(iid), "--build-arg", "SOURCE_COMMIT=" + sha,
                  "-f", str(self.root / "context/fleet-manager/containers/gateway/Dockerfile"),
                  str(self.root / "context"), timeout=timeout, build=True)
        image = iid.read_text(encoding="ascii").strip()
        if not IMAGE_ID.fullmatch(image):
            raise ProofError("invalid-image-receipt")
        self.deadline = time.monotonic() + 300
        metadata = json.loads(self.call("image", "inspect", image))[0]
        if (metadata["Architecture"] != self.architecture.name or metadata["Os"] != "linux"
                or metadata["Config"]["Labels"]["org.opencontainers.image.revision"] != sha):
            raise ProofError("image-source-or-architecture-mismatch")
        self.receipt.update(image_id=image, architecture={
            "selected": self.architecture.name, "image": metadata["Architecture"],
            "execution": "UNRUN", "process": "UNRUN",
        })
        self.journal()
        return image

    def create_network(self) -> None:
        self.ambiguous_network = True

        def retain(data: bytes) -> None:
            try:
                network = data.decode("ascii").strip()
            except UnicodeError:
                raise ProofError("invalid-network-receipt") from None
            if not ID.fullmatch(network):
                raise ProofError("invalid-network-receipt")
            self.network = network
            self.ambiguous_network = False
            self.journal()

        self.call("network", "create", "--internal",
                  "--label", "gateway-role-witness=" + self.root.name,
                  self.root.name, on_success=retain)

    def create(self, image: str, *, name: str, command: list[str] | None = None,
               mounts: list[tuple[Path, str, bool]] | None = None,
               isolated: bool = False, gateway: bool = False) -> str:
        if not IMAGE_ID.fullmatch(image) or not re.fullmatch(r"[a-z0-9-]+", name):
            raise ProofError("invalid-container-selection")
        cidfile = self.root / (name + ".cid")
        for source, target, writable in mounts or []:
            if self.root not in source.parents or (
                writable and (source != self.root / "fixtures" or target != "/output")
            ):
                raise ProofError("foreign-or-writable-mount-refused")
        proof_mount = (self.root / "proof", "/proof", False)
        if gateway and not command and not isolated:
            expected = [(self.root / "fixtures/gateway", "/config", False),
                        (self.root / "fixtures/tls", "/tls", False)]
        elif command == ["/proof/fixture_auth.py", "/output"] and isolated and not gateway:
            expected = [proof_mount, (self.root / "fixtures", "/output", True)]
        elif command == ["/proof/probe.py", "idle"] and not isolated and not gateway:
            expected = [proof_mount, (self.root / "fixtures/probe", "/credentials", False)]
        elif (command and len(command) == 3 and command[0] == "/proof/fixture_service.py"
              and command[1] in ("a", "b") and command[2] == name
              and not isolated and not gateway):
            expected = [proof_mount,
                        (self.root / "fixtures" / command[1], "/credentials", False)]
        else:
            raise ProofError("unsafe-container-command")
        if mounts != expected:
            raise ProofError("unexpected-fixture-mounts")
        args = container_args(
            image, cidfile, self.root.name + "-" + name,
            "none" if isolated else self.network, mounts or [], gateway, command)
        self.pending.append(cidfile)
        self.call(*args)
        container = self.read_cid(cidfile)
        if container not in self.containers:
            self.containers.append(container)
        self.journal()
        return container

    @staticmethod
    def read_cid(path: Path) -> str:
        value = path.read_text(encoding="ascii").strip()
        if not ID.fullmatch(value):
            raise ProofError("invalid-container-receipt")
        return value

    def start(self, container: str, *, attached: bool = False) -> str:
        self.owned(container)
        return self.call("start", *(["--attach"] if attached else []), container,
                         timeout=35)

    def owned(self, container: str) -> None:
        if container not in self.containers or not ID.fullmatch(container):
            raise ProofError("foreign-container-refused")

    def execute(self, container: str, *argv: str) -> str:
        self.owned(container)
        if not (argv == ("python", "-c", IDENTITY_CODE) or (
            len(argv) == 3 and argv[:2] == ("python", "/proof/probe.py")
            and argv[2] in PROBE_MODES
        )):
            raise ProofError("unsafe-exec-command")
        return self.call("exec", container, *argv, timeout=25)

    def identity(self, container: str) -> dict:
        result = json.loads(self.execute(container, "python", "-c", IDENTITY_CODE))
        state = json.loads(self.call("inspect", "--format", "{{json .State}}", container))
        if not state["Running"] or result["uid"] == 0 or "fleet-manager" not in result["argv"]:
            raise ProofError("gateway-process-not-observed")
        result.update(container_id=container, engine_pid=state["Pid"],
                      engine_started_at=state["StartedAt"])
        return result

    def cleanup(self) -> list[str]:
        errors = []
        for path in self.pending:
            if path.exists():
                try:
                    cid = self.read_cid(path)
                    if cid not in self.containers:
                        self.containers.append(cid)
                except (OSError, ProofError):
                    errors.append("unreadable-creation-receipt")
            else:
                errors.append("container-create-outcome-unknown-no-id")
        for cid in reversed(self.containers):
            try:
                self.call("logs", "--tail", "160", cid, cleanup=True)
            except ProofError:
                errors.append("container-log-capture-failed")
            failed = False
            for operation in (("stop", "--time", "5", cid), ("rm", cid)):
                try:
                    self.call(*operation, timeout=10, cleanup=True)
                except ProofError:
                    failed = True
            if failed:
                errors.append("owned-container-cleanup-failed")
        if self.network:
            try:
                self.call("network", "rm", self.network, timeout=10, cleanup=True)
            except ProofError:
                errors.append("owned-network-cleanup-failed")
        if self.ambiguous_network:
            errors.append("network-create-outcome-unknown-no-id")
        self.receipt["cleanup_errors"] = errors
        self.journal()
        return errors


def container_args(image: str, cidfile: Path, name: str, network: str | None,
                   mounts: list[tuple[Path, str, bool]], gateway: bool,
                   command: list[str] | None) -> list[str]:
    """Only this fixed hardened shape may create proof containers."""
    if network != "none" and (network is None or not ID.fullmatch(network)):
        raise ProofError("owned-network-required")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise ProofError("nonroot-host-required", "repo-config")
    args = ["create", "--cidfile", str(cidfile), "--name", name,
            "--network", network, "--user", f"{uid}:{gid}", "--read-only",
            "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
            "--pids-limit", "64", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--ulimit", "nofile=256:256",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size=32m,uid={uid},gid={gid}",
            "--tmpfs", f"/state:rw,noexec,nosuid,size=16m,uid={uid},gid={gid}",
            "--log-opt", "max-size=1m", "--log-opt", "max-file=1"]
    if gateway:
        args.extend(["--network-alias", "gateway"])
    for source, target, writable in mounts:
        if (not source.is_absolute() or "," in str(source) or source.is_symlink()
                or not target.startswith("/") or "," in target):
            raise ProofError("invalid-proof-mount")
        args.extend(["--mount", f"type=bind,src={source},dst={target}"
                     + ("" if writable else ",readonly")])
    if command:
        args.extend(["--entrypoint", "python"])
    return [*args, image, *(command or [])]
