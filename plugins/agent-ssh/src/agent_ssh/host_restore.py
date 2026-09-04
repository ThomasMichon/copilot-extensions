"""Transport-owned host restoration for declarative machine convergence."""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs


def _payload_manifest(path: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads((path / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(manifest, dict) and manifest.get("name") == "agent-ssh":
        return manifest
    return None


def _payload_root() -> Path:
    configured = os.environ.get("COPILOT_PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    home = Path.home()
    marker = home / ".agent-ssh" / "payload-dir"
    marker_payload = None
    marker_manifest = None
    try:
        marker_payload = Path(
            marker.read_text(encoding="utf-8").strip()
        ).resolve()
        marker_manifest = _payload_manifest(marker_payload)
    except OSError:
        pass

    deploy_manifest = home / ".agent-ssh" / "deploy-manifest.json"
    try:
        deployed = json.loads(deploy_manifest.read_text(encoding="utf-8"))
        source = deployed.get("source", {})
        kind = source.get("kind")
        marketplace = source.get("repo")
        plugin = source.get("plugin")
        version = source.get("version")
        if kind != "marketplace" and marker_manifest is not None:
            return marker_payload
        if (
            kind == "marketplace"
            and marker_manifest is not None
            and marker_manifest.get("version") == version
        ):
            return marker_payload
        if (
            kind == "marketplace"
            and isinstance(marketplace, str)
            and marketplace
            and plugin == "agent-ssh"
            and isinstance(version, str)
        ):
            payload = (
                home
                / ".copilot"
                / "installed-plugins"
                / marketplace
                / plugin
            ).resolve()
            manifest = _payload_manifest(payload)
            if manifest is not None and manifest.get("version") == version:
                return payload
    except (AttributeError, OSError, ValueError):
        pass

    if marker_manifest is not None:
        return marker_payload
    source = Path(__file__).resolve().parents[2]
    if (source / "plugin.json").is_file():
        return source
    raise RuntimeError("cannot resolve the active agent-ssh payload")


def _dtssh_command(alias: str, port: int, *, apply: bool) -> list[str]:
    payload = _payload_root()
    script = payload / "transports" / "dtssh" / "scripts" / "install-host.ps1"
    if not script.is_file():
        raise RuntimeError(f"dtssh host installer is unavailable: {script}")
    host = shutil.which("pwsh") or shutil.which("powershell")
    if host is None:
        raise RuntimeError("PowerShell is required to restore a dtssh host")
    return [
        host,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "update" if apply else "status",
        "-Alias",
        alias,
        "-Port",
        str(port),
        *(["-SkipLogin"] if apply else []),
    ]


def _login_status() -> tuple[bool, str]:
    devtunnel = shutil.which("devtunnel")
    if not devtunnel and os.environ.get("LOCALAPPDATA"):
        sibling = (
            Path(os.environ["LOCALAPPDATA"])
            / "dtssh"
            / "bin"
            / "devtunnel.exe"
        )
        if sibling.is_file():
            devtunnel = str(sibling)
    if not devtunnel:
        return False, "devtunnel is unavailable"
    try:
        proc = subprocess.run(
            [devtunnel, "user", "show", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot inspect Dev Tunnel login: {exc}"
    text = f"{proc.stdout}\n{proc.stderr}"
    compact = "".join(text.casefold().split())
    logged_in = proc.returncode == 0 and (
        '"status":"loggedin"' in compact
        or "logged in as " in text.casefold()
    )
    return (
        (True, "")
        if logged_in
        else (False, "Dev Tunnel login is required; run `dtssh login` interactively")
    )


def _healthy_status(output: str) -> bool:
    unhealthy = (
        "host not running",
        "NOT serving",
        "watchdog not running",
        "startup shortcut missing",
        "durable host identity: pending",
    )
    if any(marker.casefold() in output.casefold() for marker in unhealthy):
        return False
    return re.search(
        r"tunnel .*:\s*0 host connection\(s\)",
        output,
        flags=re.IGNORECASE,
    ) is None


def _ssh_session() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _spawn_dtssh_update_via_wmi(command: list[str]) -> tuple[bool, str]:
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if powershell is None:
        return False, "Windows PowerShell is required for remote-safe dtssh convergence"

    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "agent-ssh-dtssh"
    log_path = log_dir / "restore-host-detached.log"
    argv = " ".join(f"'{_ps_quote(arg)}'" for arg in command[1:])
    child = (
        "Start-Sleep -Seconds 3; "
        f"New-Item -ItemType Directory -Force -Path '{_ps_quote(str(log_dir))}' "
        "| Out-Null; "
        f"& '{_ps_quote(command[0])}' {argv} "
        f"*>> '{_ps_quote(str(log_path))}'; "
        "exit $LASTEXITCODE"
    )
    child_encoded = base64.b64encode(child.encode("utf-16-le")).decode("ascii")
    child_command = (
        f'"{powershell}" -NoProfile -NonInteractive '
        f"-EncodedCommand {child_encoded}"
    )
    broker = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{ CommandLine = '{_ps_quote(child_command)}'; "
        f"CurrentDirectory = '{_ps_quote(str(Path.home()))}' }}; "
        "exit [int]$r.ReturnValue"
    )
    broker_encoded = base64.b64encode(broker.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                broker_encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot launch remote-safe dtssh convergence: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return False, (
            "cannot launch remote-safe dtssh convergence"
            + (f": {detail}" if detail else "")
        )
    return True, str(log_path)


def restore_host(
    transport: str,
    alias: str,
    port: int,
    *,
    apply: bool,
) -> dict[str, Any]:
    if transport != "dtssh":
        return {
            "ok": False,
            "transport": transport,
            "alias": alias,
            "port": port,
            "applied": False,
            "error": f"unsupported host transport: {transport}",
        }
    if platform.system() != "Windows":
        return {
            "ok": False,
            "transport": transport,
            "alias": alias,
            "port": port,
            "applied": False,
            "error": "dtssh host restoration is supported only on Windows",
        }
    logged_in, login_error = _login_status()
    if not logged_in:
        return {
            "ok": False,
            "transport": transport,
            "alias": alias,
            "port": port,
            "applied": False,
            "blocked": "authentication",
            "error": login_error,
        }
    try:
        command = _dtssh_command(alias, port, apply=apply)
        if apply and _ssh_session():
            launched, detail = _spawn_dtssh_update_via_wmi(command)
            return {
                "ok": launched,
                "transport": transport,
                "alias": alias,
                "port": port,
                "healthy": False,
                "would_change": True,
                "applied": False,
                "detached": launched,
                "verification_required": launched,
                "command": command,
                "returncode": 0 if launched else 1,
                "stdout": "",
                "stderr": "",
                **(
                    {"detached_log": detail}
                    if launched
                    else {"error": detail}
                ),
            }
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return {
            "ok": False,
            "transport": transport,
            "alias": alias,
            "port": port,
            "applied": False,
            "error": str(exc),
        }
    output = f"{proc.stdout}\n{proc.stderr}"
    healthy = (
        proc.returncode == 0 and _healthy_status(output)
        if not apply
        else False
    )
    status_command = None
    status_proc = None
    if apply and proc.returncode == 0:
        status_command = _dtssh_command(alias, port, apply=False)
        for attempt in range(5):
            try:
                status_proc = subprocess.run(
                    status_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    check=False,
                    **no_window_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {
                    "ok": False,
                    "transport": transport,
                    "alias": alias,
                    "port": port,
                    "mode": "apply" if apply else "dry-run",
                    "applied": False,
                    "error": f"cannot verify dtssh host after install: {exc}",
                }
            output = f"{status_proc.stdout}\n{status_proc.stderr}"
            healthy = status_proc.returncode == 0 and _healthy_status(output)
            if healthy or attempt == 4:
                break
            time.sleep(2)
    ok = proc.returncode == 0 and (healthy or not apply)
    return {
        "ok": ok,
        "transport": transport,
        "alias": alias,
        "port": port,
        "healthy": healthy,
        "would_change": not healthy,
        "applied": apply and ok,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "verification_command": status_command,
        "verification_returncode": (
            status_proc.returncode if status_proc is not None else None
        ),
        "verification_stdout": (
            status_proc.stdout if status_proc is not None else ""
        ),
        "verification_stderr": (
            status_proc.stderr if status_proc is not None else ""
        ),
        **(
            {"error": "dtssh host remains unhealthy after installation"}
            if apply and not ok and proc.returncode == 0
            else {}
        ),
    }


def emit_result(result: dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        output = f"{result.get('stdout', '')}{result.get('stderr', '')}".rstrip()
        if output:
            print(output)
        if result.get("verification_required"):
            print(
                "[OK] Detached convergence launched; reconnect and run "
                "`agent-ssh restore-host "
                f"--transport {result['transport']} "
                f"--alias {result['alias']} "
                f"--port {result['port']} --dry-run` to verify it."
            )
        if result.get("error"):
            print(f"[FAIL] {result['error']}")
    return 0 if result.get("ok") else 1
