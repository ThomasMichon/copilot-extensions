"""Bundled Podman implementation of the version-1 runtime-driver protocol."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_procutil.owned_process import run_caller_owned

from .config import FLEET_LABEL, SECURITY_PROFILE_LABEL, TRUSTED_PROFILE
from .runtime_drivers import DRIVER_LABEL, INSTANCE_LABEL

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ID = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VERBS = ("capabilities", "provision", "list", "inspect", "start", "stop",
          "remove", "exec", "copy-in", "exec-stream")


class PodmanError(RuntimeError):
    """A portable protocol failure without captured native diagnostics."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str) or not value or value.startswith("-")
        or any(ord(char) < 32 for char in value)
    ):
        raise PodmanError("invalid_request", f"{label} must be nonempty literal text")
    return value


def _name(value: Any, label: str) -> str:
    value = _text(value, label)
    if not _NAME.fullmatch(value):
        raise PodmanError("invalid_request", f"invalid {label}")
    return value


def _mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and _ENV.fullmatch(key)
        and isinstance(val, str) and "\0" not in val
        for key, val in value.items()
    ):
        raise PodmanError("invalid_request", f"{label} must map environment names to strings")
    return value


class PodmanDriver:
    """Use one explicitly selected Podman client and its native configuration."""

    def __init__(self, request: dict[str, Any]):
        if type(request.get("protocolVersion")) is not int or request["protocolVersion"] != 1:
            raise PodmanError("unsupported_protocol", "expected protocolVersion 1")
        if request.get("driver") != "podman":
            raise PodmanError("invalid_request", "expected driver podman")
        self.fleet = _name(request.get("fleet"), "fleet")
        options = request.get("driverOptions")
        allowed = {"image", "executable", "connection", "pull_policy"}
        if not isinstance(options, dict) or options.keys() - allowed:
            raise PodmanError("invalid_request", "unsupported driverOptions")
        self.image = _text(options.get("image"), "driverOptions.image")
        self.policy = options.get("pull_policy", "missing")
        if self.policy not in ("always", "missing", "never"):
            raise PodmanError("invalid_request", "invalid driverOptions.pull_policy")
        executable = _text(options.get("executable", "podman"), "driverOptions.executable")
        connection = options.get("connection")
        if connection is not None:
            connection = _text(connection, "driverOptions.connection")
        selected = shutil.which(executable)
        if selected is None:
            raise PodmanError("runtime_unavailable", "selected Podman executable is unavailable")
        self.command = [selected]
        if connection is not None:
            self.command += ["--connection", connection]

    def _run(self, args: list[str], *, timeout: float = 60, stdin: str | None = None):
        try:
            return run_caller_owned(
                [*self.command, *args], timeout=timeout, input_text=stdin,
                max_stdout_bytes=64 * 1024 * 1024, max_stderr_bytes=1024 * 1024,
            )
        except subprocess.TimeoutExpired:
            raise PodmanError(
                "timeout", "Podman deadline expired; inspect retained state before retrying",
            ) from None
        except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError):
            raise PodmanError(
                "runtime_unavailable",
                "Podman execution or cleanup failed; operation outcome may be unknown",
            ) from None

    def _checked(self, args: list[str], *, timeout: float = 60) -> str:
        done = self._run(args, timeout=timeout)
        if done.returncode:
            raise PodmanError(
                "runtime_unavailable",
                f"Podman {args[0]} failed (exit {done.returncode}); check the selected "
                "connection, configured image sources and authentication; driver did not replay",
            )
        return done.stdout

    def _json(self, args: list[str]) -> list[dict]:
        try:
            rows = json.loads(self._checked(args))
        except ValueError:
            raise PodmanError("runtime_unavailable", "Podman returned invalid JSON") from None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise PodmanError("runtime_unavailable", "Podman returned invalid inspection rows")
        return rows

    def _member(self, row: dict) -> dict:
        config, state = row.get("Config"), row.get("State")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise PodmanError("runtime_unavailable", "incomplete Podman inspection")
        labels = config.get("Labels") or {}
        if not isinstance(labels, dict) or (
            labels.get(FLEET_LABEL) != self.fleet or labels.get(DRIVER_LABEL) != "podman"
        ):
            raise PodmanError("conflict", "member belongs to another fleet or driver")
        member_id = row.get("Id")
        if not isinstance(member_id, str) or not _ID.fullmatch(member_id):
            raise PodmanError("runtime_unavailable", "Podman did not report a full member ID")
        status = state.get("Status")
        if not isinstance(status, str) or not status:
            raise PodmanError("runtime_unavailable", "Podman did not report member state")
        return {
            "name": _name(str(row.get("Name", "")).lstrip("/"), "member.name"),
            "id": member_id, "fleet": self.fleet,
            "instance": _name(labels.get(INSTANCE_LABEL), "member.instance"),
            "state": status, "status": status, "image": str(row.get("Image") or self.image),
        }

    def _inspect(self, name: str, instance_id: str | None = None) -> dict:
        target = instance_id or name
        done = self._run(["container", "inspect", target])
        if done.returncode:
            exists = self._run(["container", "exists", target])
            if exists.returncode == 1:
                raise PodmanError(
                    "conflict" if instance_id else "not_found", "requested member is absent",
                )
            raise PodmanError("runtime_unavailable", "selected Podman member cannot be inspected")
        try:
            rows = json.loads(done.stdout)
        except ValueError:
            rows = None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise PodmanError("runtime_unavailable", "invalid Podman member inspection")
        member = self._member(rows[0])
        if member["name"] != name or (instance_id and member["id"] != instance_id):
            raise PodmanError("conflict", "member incarnation changed")
        return member

    def _exec_args(self, request: dict, member: dict, *, stream: bool = False) -> list[str]:
        argv = request.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(arg, str) and "\0" not in arg for arg in argv
        ) or not argv[0]:
            raise PodmanError("invalid_request", "argv must be a nonempty string vector")
        args = ["exec", "--interactive"]
        if request.get("user") is not None:
            args += ["--user", _text(request["user"], "user")]
        if request.get("workdir") is not None:
            args += ["--workdir", _text(request["workdir"], "workdir")]
        for key, val in _mapping(request.get("env", {}), "env").items():
            args += ["--env", f"{key}={val}"]
        inherited = request.get("inheritEnv", [])
        if not isinstance(inherited, list) or not all(
            isinstance(key, str) and _ENV.fullmatch(key) for key in inherited
        ) or (inherited and not stream):
            raise PodmanError("invalid_request", "invalid inheritEnv")
        for key in inherited:
            if key not in os.environ:
                raise PodmanError("invalid_request", "an inherited environment value is missing")
            args += ["--env", key]
        return [*args, member["id"], *argv]

    def handle(self, verb: str, request: dict) -> dict:
        """Return response fields, or raise without replaying an uncertain effect."""
        if verb == "exec-stream" and os.name == "nt":
            raise PodmanError("unsupported", "Podman streaming transport requires a POSIX host")
        if verb == "capabilities":
            return {"capabilities": {"owns_relay": False, "ssh_transport": True}}
        if verb == "provision":
            spec = request.get("member")
            if not isinstance(spec, dict) or spec.get("fleet") != self.fleet:
                raise PodmanError("invalid_request", "invalid member fleet")
            name = _name(spec.get("name"), "member.name")
            instance = _name(spec.get("instance"), "member.instance")
            workspace = _text(request.get("workspaceFolder"), "workspaceFolder")
            if not workspace.startswith("/"):
                raise PodmanError("invalid_request", "workspaceFolder must be an absolute guest path")
            user = _text(request.get("execUser"), "execUser")
            environment = _mapping(request.get("environment", {}), "environment")
            exists = self._run(["container", "exists", name])
            if exists.returncode == 0:
                raise PodmanError("conflict", "member name already exists; replacement is forbidden")
            if exists.returncode != 1:
                raise PodmanError("runtime_unavailable", "cannot establish that member name is free")
            pulled = self._checked(
                ["pull", f"--policy={self.policy}", "--quiet", self.image], timeout=3000,
            ).strip()
            if not _IMAGE_ID.fullmatch(pulled):
                raise PodmanError("runtime_unavailable", "Podman pull did not resolve one immutable ID")
            # Inspect the returned ID, never the mutable name after pull.
            images = self._json(["image", "inspect", pulled])
            image_id = images[0].get("Id") if len(images) == 1 else None
            if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
                raise PodmanError("runtime_unavailable", "Podman returned an invalid image ID")
            if image_id.removeprefix("sha256:") != pulled.removeprefix("sha256:"):
                raise PodmanError("conflict", "resolved image identity changed")
            args = ["run", "--detach", "--pull=never", "--name", name,
                    "--user", user, "--workdir", workspace]
            for key, val in {
                FLEET_LABEL: self.fleet, DRIVER_LABEL: "podman",
                INSTANCE_LABEL: instance, SECURITY_PROFILE_LABEL: TRUSTED_PROFILE,
            }.items():
                args += ["--label", f"{key}={val}"]
            for key, val in environment.items():
                args += ["--env", f"{key}={val}"]
            created = self._checked(
                [*args, "--entrypoint", "/bin/sleep", image_id, "infinity"], timeout=300,
            ).strip()
            if not _ID.fullmatch(created):
                raise PodmanError("runtime_unavailable", "creation outcome unknown; inspect retained state")
            member = self._inspect(name, created)
            if member["state"] != "running":
                raise PodmanError("conflict", "created member is not running; retained without removal")
            return {"member": member}
        if verb == "list":
            ids = self._checked([
                "ps", "--all", "--no-trunc", "--filter", f"label={FLEET_LABEL}={self.fleet}",
                "--filter", f"label={DRIVER_LABEL}=podman", "--format", "{{.ID}}",
            ]).split()
            if not all(_ID.fullmatch(member_id) for member_id in ids):
                raise PodmanError("runtime_unavailable", "invalid Podman inventory IDs")
            members = [
                self._member(row) for row in self._json(["container", "inspect", *ids])
            ] if ids else []
            if len(members) != len(ids) or {row["id"] for row in members} != set(ids):
                raise PodmanError("conflict", "inventory changed during Podman inspection")
            return {"members": members}
        if verb not in _VERBS:
            raise PodmanError("unsupported", "unsupported Podman driver verb")
        name = _name(request.get("name"), "name")
        instance_id = request.get("id")
        if instance_id is not None and (
            not isinstance(instance_id, str) or not _ID.fullmatch(instance_id)
        ):
            raise PodmanError("invalid_request", "invalid member id")
        member = self._inspect(name, instance_id)
        target = member["id"]  # Every effect targets the inspected immutable incarnation.
        if verb == "inspect":
            return {"member": member}
        if verb in ("start", "stop", "remove"):
            force = request.get("force", False)
            if not isinstance(force, bool):
                raise PodmanError("invalid_request", "force must be boolean")
            command = "unpause" if verb == "start" and member["state"] == "paused" else verb
            args = ["rm" if verb == "remove" else command]
            if verb == "remove" and force:
                args.append("--force")
            self._checked([*args, target], timeout=300)
            return {}
        if verb == "copy-in":
            source = Path(_text(request.get("source"), "source"))
            destination = _text(request.get("target"), "target")
            if not source.is_absolute() or not source.exists() or not destination.startswith("/"):
                raise PodmanError("invalid_request", "copy-in requires existing absolute paths")
            self._checked(["cp", str(source), f"{target}:{destination}"], timeout=500)
            return {}
        args = self._exec_args(request, member, stream=verb == "exec-stream")
        if verb == "exec-stream":
            os.execvp(self.command[0], [*self.command, *args])
        timeout = request.get("timeoutSeconds", 30)
        if (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
        ):
            raise PodmanError("invalid_request", "timeoutSeconds must be finite and positive")
        stdin = request.get("stdin")
        if stdin is not None and not isinstance(stdin, str):
            raise PodmanError("invalid_request", "stdin must be text or null")
        done = self._run(args, timeout=timeout, stdin=stdin)
        return {"exitCode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


def main(argv: list[str] | None = None) -> int:
    """Serve exactly one version-1 request; streaming stdout remains untouched."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=_VERBS)
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    streaming = args.verb == "exec-stream"
    try:
        if streaming != (args.request is not None):
            raise PodmanError("invalid_request", "--request is required only for exec-stream")
        raw = args.request if streaming else sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PodmanError("invalid_request", "request exceeds limit")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise PodmanError("invalid_request", "request must be an object")
        result = PodmanDriver(request).handle(args.verb, request)
        if streaming:
            return result["exitCode"]
        response, code = {"ok": True, **result}, 0
    except (PodmanError, ValueError, OSError) as exc:
        if streaming:
            print(str(exc) if isinstance(exc, PodmanError) else "invalid Podman request",
                  file=sys.stderr)
            return 125
        response, code = {
            "ok": False, "error": {
                "code": exc.code if isinstance(exc, PodmanError) else "invalid_request",
                "message": str(exc) if isinstance(exc, PodmanError) else "invalid Podman request",
            },
        }, 1
    print(json.dumps({"protocolVersion": 1, **response}))
    return code


if __name__ == "__main__":
    sys.exit(main())
