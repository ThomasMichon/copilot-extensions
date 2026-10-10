"""Opt-in real Komodo bootstrap and role proof against a disposable nested daemon."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent
ROLE_IMAGE = "busybox@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class ProofError(RuntimeError):
    pass


def command(argv: list[str], *, cwd: Path, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        argv, cwd=cwd, input=data, capture_output=True, timeout=240, **NO_WINDOW,
    )
    if result.returncode:
        raise ProofError(f"{argv[0]} operation failed (exit {result.returncode})")
    return result.stdout


def cleanup_paths(root: Path) -> None:
    for name in ("database.secret", "admin.secret", "jwt.secret", "webhook.secret", "enrolled.toml"):
        path = root / name
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()


def run(root: Path) -> dict:
    project = "komodo-role-proof-" + secrets.token_hex(6)
    compose = ["docker", "compose", "-p", project, "-f", str(root / "compose.yaml")]
    receipt = {
        "version": 1, "project": project, "result": "fail", "cleanup": "pending",
        "host_socket_granted": False, "privileged_nested_target": True,
        "browser_used": False, "stages": [], "started_at": int(time.time()),
    }

    def docker(*args: str, data: bytes | None = None) -> str:
        return command([*compose, *args], cwd=root, data=data).decode("utf-8").strip()

    def client(mode: str) -> dict:
        result = json.loads(docker("run", "--rm", "-T", "client", "node", "/proof/client.mjs", mode))
        if not isinstance(result, dict) or result.get("result") != "pass":
            raise ProofError(f"Isolated client {mode} did not pass")
        return result

    def running() -> None:
        state = docker("exec", "-T", "nested", "docker", "inspect", "proof-role",
                       "--format", "{{.State.Running}}")
        if state != "true":
            raise ProofError("Role is not independently observed running")

    started = False
    try:
        for name in ("compose.yaml", "client.mjs"):
            shutil.copyfile(FIXTURE / name, root / name)
        for name in ("database", "admin", "jwt", "webhook"):
            (root / f"{name}.secret").write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if command(["docker", "info", "--format", "{{.OSType}}"], cwd=root).strip() != b"linux":
            raise ProofError("A running Linux Docker engine is required")
        docker("pull", "mongo", "core", "periphery", "client", "nested", "enrolled")
        command(["docker", "pull", ROLE_IMAGE], cwd=root)
        receipt["role_digest"] = ROLE_IMAGE
        started = True
        docker("up", "-d", "mongo", "core", "periphery")
        receipt["stages"].append("fresh-core-start")
        prepared = client("prepare")
        receipt["core_version"] = prepared["core_version"]
        receipt["stages"].extend(prepared["stages"])
        docker("--profile", "enroll", "up", "-d", "nested", "enrolled")
        if client("enrolled")["server_count"] != 2:
            raise ProofError("Second Periphery did not enroll")
        receipt["stages"].append("second-periphery-enrolled")
        for attempt in range(30):
            try:
                docker("exec", "-T", "nested", "docker", "info")
                break
            except ProofError:
                if attempt == 29:
                    raise
                time.sleep(2)
        image = command(["docker", "save", ROLE_IMAGE], cwd=root)
        docker("exec", "-T", "nested", "docker", "load", data=image)
        image_id = command(
            ["docker", "image", "inspect", ROLE_IMAGE, "--format", "{{.Id}}"], cwd=root,
        ).decode("utf-8").strip()
        docker("exec", "-T", "nested", "docker", "tag", image_id, "busybox:1.37")
        receipt["stages"].extend(client("deploy")["stages"])
        running()
        receipt["stages"].append("independent-role-running")
        docker("stop", "core", "enrolled")
        running()
        receipt["stages"].append("role-survives-manager-agent-outage")
        docker("start", "core", "enrolled")
        if client("repeat")["server_count"] != 2:
            raise ProofError("Repeat duplicated or lost an enrolled server")
        docker("--profile", "enroll", "up", "-d", "mongo", "core", "periphery", "nested", "enrolled")
        if client("repeat")["server_count"] != 2:
            raise ProofError("Idempotent Compose rerun changed server count")
        receipt["stages"].append("restart-repeat-no-duplicate-server")
        receipt["result"] = "pass"
    except (ProofError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
        receipt["error_type"] = type(exc).__name__
        receipt["error"] = str(exc) if isinstance(exc, ProofError) else "Proof operation failed"
    finally:
        try:
            if started:
                docker("--profile", "*", "down", "-v", "--remove-orphans")
            receipt["cleanup"] = "complete"
        except (ProofError, subprocess.TimeoutExpired, OSError):
            receipt["cleanup"] = "failed"
            receipt["result"] = "fail"
        try:
            cleanup_paths(root)
        except OSError:
            receipt["secret_cleanup"] = "failed"
            receipt["result"] = "fail"
        receipt["finished_at"] = int(time.time())
        (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-privileged-dind", action="store_true")
    parser.add_argument("--results-root", type=Path, required=True,
                        help="Existing private scratch root; a unique run directory is created beneath it")
    args = parser.parse_args()
    if not args.allow_privileged_dind:
        parser.error("Requires explicit --allow-privileged-dind approval; this is not a production installer")
    supplied = args.results_root.absolute()
    if any(path.is_symlink() for path in (supplied, *supplied.parents)):
        parser.error("--results-root must not contain symlink indirection")
    root = supplied.resolve()
    if not root.is_dir():
        parser.error("--results-root must be an existing private directory")
    run_dir = Path(tempfile.mkdtemp(prefix="komodo-role-proof-", dir=root))
    receipt = run(run_dir)
    print(json.dumps({"results": str(run_dir), **receipt}))
    return 0 if receipt["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
