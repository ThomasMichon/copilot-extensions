from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.name != "nt":
    import pwd

ROOT = Path(
    os.environ.get("CR_PARTNER_PATH") or os.environ["CR_HARNESS_MOUNT"]
).resolve()
RESULTS = Path(os.environ["CR_REPORT"]).resolve().parent
WORK = RESULTS / "agent-index-installation-cells-state"
STATE = WORK / "state.json"
CONTEXT_TOOL = ROOT / "libs" / "installation-context" / "installation_context.py"
CONTEXT_TOOL_PS = ROOT / "libs" / "installation-context" / "installation-context.ps1"
PLUGIN_SOURCE = ROOT / "plugins" / "agent-index"
PLUGIN_ID = "agent-index"
CRASH_EXIT_CODES = {
    "passive": 86,
    "flipped": 87,
    "draining": 88,
    "committed": 89,
}


def profile_home() -> Path:
    if os.name == "nt":
        return Path(os.environ["USERPROFILE"]).resolve()
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


PROFILE = profile_home()
DURABLE = PROFILE / ".copilot-extensions"
LEGACY = PROFILE / ".agent-index"
POLICY = DURABLE / "installation-mode.json"
if os.environ.get("CR_UV_INDEX"):
    os.environ["UV_DEFAULT_INDEX"] = os.environ["CR_UV_INDEX"]
    os.environ["UV_INDEX_URL"] = os.environ["CR_UV_INDEX"]
    os.environ.setdefault("UV_EXTRA_INDEX_URL", os.environ["CR_UV_INDEX"])


def run(
    arguments: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {arguments!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def context(*arguments: str) -> dict[str, object]:
    if os.name == "nt":
        converted = [arguments[0]]
        for value in arguments[1:]:
            if value.startswith("--"):
                converted.append(
                    "-" + "".join(part.capitalize() for part in value[2:].split("-"))
                )
            else:
                converted.append(value)
        result = run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(CONTEXT_TOOL_PS),
                *converted,
            ]
        )
    else:
        result = run([sys.executable, str(CONTEXT_TOOL), *arguments])
    return json.loads(result.stdout)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def set_namespaced_policy(enabled: bool) -> None:
    write_json(
        POLICY,
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": enabled},
        },
    )


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object expected at {path}")
    return value


def path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    text = result.stdout.strip()
    position = text.find("{")
    while position >= 0:
        try:
            value = json.loads(text[position:])
        except ValueError:
            position = text.find("{", position + 1)
            continue
        if isinstance(value, dict):
            return value
        break
    raise RuntimeError(
        "command did not emit a JSON object\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def read_state() -> dict[str, object]:
    return read_json(STATE)


def save_state(value: dict[str, object]) -> None:
    write_json(STATE, value)


def plugin_version(payload: Path) -> str:
    return str(json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"])


def next_dev_version(version: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+-dev)(\d+)", version)
    if not match:
        raise RuntimeError(f"scenario requires a development version, got {version}")
    return f"{match.group(1)}{int(match.group(2)) + 1}"


def copy_payload(name: str) -> Path:
    target = WORK / name
    shutil.copytree(
        PLUGIN_SOURCE,
        target,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            ".venv",
            "build",
            "dist",
        ),
    )
    return target


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(PLUGIN_SOURCE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PLUGIN_SOURCE).as_posix()
        if relative.endswith((".pyc", ".pyo")):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_descriptor(name: str) -> dict[str, str]:
    return {
        "source": "github",
        "repo": f"example-org/{name}-marketplace",
    }


def stamp(
    payload: Path,
    key: str,
    descriptor: dict[str, str],
    *,
    expected_namespace_generation: int,
    expected_install_generation: int,
) -> dict[str, object]:
    return context(
        "stamp",
        "--source-json",
        json.dumps(descriptor, separators=(",", ":")),
        "--marketplace-key",
        key,
        "--plugin-id",
        PLUGIN_ID,
        "--payload-root",
        str(payload),
        "--payload-version",
        plugin_version(payload),
        "--payload-origin",
        "explicit",
        "--expected-namespace-generation",
        str(expected_namespace_generation),
        "--expected-install-generation",
        str(expected_install_generation),
        "--durable-home",
        str(DURABLE),
    )


def activate(
    install: Path,
    marketplace_id: str,
    namespace_generation: int,
    install_generation: int,
    activation_generation: int,
) -> dict[str, object]:
    return context(
        "activation-cas",
        "--context",
        str(install),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--expected-namespace-generation",
        str(namespace_generation),
        "--expected-install-generation",
        str(install_generation),
        "--expected-activation-generation",
        str(activation_generation),
        "--activation-mode",
        "namespaced",
        "--activation-state",
        "active",
        "--legacy-disposition",
        "absent",
        "--legacy-probe-json",
        '{"declared":true,"result":"absent","checkedAt":"2026-01-01T00:00:00Z"}',
        "--legacy-root",
        str(LEGACY),
        "--durable-home",
        str(DURABLE),
    )


def validate_install(
    install: Path,
    marketplace_id: str,
    payload: Path | None = None,
) -> dict[str, object]:
    arguments = [
        "validate",
        "--context",
        str(install),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--durable-home",
        str(DURABLE),
    ]
    if payload is not None:
        arguments.extend(["--expected-payload-root", str(payload)])
    return context(*arguments)


def installation_status(
    payload: Path,
    install: Path,
    marketplace_id: str,
) -> dict[str, object]:
    return context(
        "status",
        "--context",
        str(install),
        "--payload-root",
        str(payload),
        "--plugin-id",
        PLUGIN_ID,
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        PLUGIN_ID,
        "--expected-payload-root",
        str(payload),
        "--durable-home",
        str(DURABLE),
        "--legacy-root",
        str(LEGACY),
    )


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(PROFILE),
            "USERPROFILE": str(PROFILE),
            "PYTHONUTF8": "1",
        }
    )
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_PLUGIN_ROOT",
        "AGENT_INDEX_HOME",
        "AGENT_INDEX_STATE_DIR",
        "AGENT_INDEX_DATA_DIR",
        "AGENT_INDEX_RUN_DIR",
        "AGENT_INDEX_LOG_DIR",
        "AGENT_INDEX_CACHE_DIR",
        "AGENT_INDEX_CONFIG_ROOT",
        "AGENT_INDEX_CONFIG",
        "AGENT_INDEX_ROUTING_DIR",
        "AGENT_INDEX_ENDPOINT",
        "AGENT_INDEX_ENGINE_HOME",
        "AGENT_INDEX_ENGINE_PORT",
        "AGENT_INDEX_BACKUP_DIR",
        "AGENT_INDEX_BACKUP_MOUNT_ROOT",
        "AGENT_INDEX_ROLE",
        "AGENT_INDEX_INSTALLATION_ID",
        "AGENT_INDEX_INSTANCE_TOKEN",
        "AGENT_INDEX_CELL_LOCK_TOKEN",
        "AGENT_INDEX_CELL_LOCK_ROOT",
        "AGENT_INDEX_CELL_START_TOKEN",
        "AGENT_INDEX_CELL_TRANSACTION",
        "AGENT_INDEX_CELL_TRANSACTION_TOKEN",
        "AGENT_INDEX_CELL_TRANSACTION_ID",
        "AGENT_INDEX_PAYLOAD_ROOT",
        "AGENT_INDEX_CELL_BUILD_SMOKE",
        "AGENT_INDEX_CELL_NO_START",
        "AGENT_INDEX_REBUILD_CURRENT",
        "AGENT_INDEX_TEST_CUTOVER_CRASH_PHASE",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        environment.pop(name, None)
    return environment


def payload_invocation(
    payload: Path,
    install: Path | None,
    *arguments: str,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    environment = clean_environment()
    environment["COPILOT_PLUGIN_ROOT"] = str(payload)
    if install is not None:
        environment["COPILOT_EXTENSIONS_CONTEXT"] = str(install)
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "bin" / "agent-index.ps1"),
            *arguments,
        ]
    else:
        command = [
            "bash",
            str(payload / "bin" / "agent-index"),
            *arguments,
        ]
    return run(command, env=environment, check=check, timeout=timeout)


def light_dependencies_available() -> bool:
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("fastapi", "uvicorn", "httpx", "pydantic", "yaml")
    )


def build_mode() -> str:
    requested = os.environ.get("CR_AGENT_INDEX_BUILD_MODE", "full").strip().lower()
    if requested not in {"auto", "smoke", "full"}:
        raise RuntimeError("CR_AGENT_INDEX_BUILD_MODE must be auto, smoke, or full")
    if requested == "auto":
        return "full"
    if requested == "smoke" and not light_dependencies_available():
        raise RuntimeError(
            "smoke build requested, but the clean-room base interpreter does not "
            "provide the lightweight Agent Index service dependencies"
        )
    return requested


def cell_provision(
    payload: Path,
    install: Path,
    marketplace_id: str,
    cache_root: Path,
    *,
    mode: str,
    no_start: bool,
) -> subprocess.CompletedProcess[str]:
    environment = clean_environment()
    environment.update(
        {
            "AGENT_INDEX_ROLE": "host",
            "AGENT_INDEX_NO_ENGINE_DEPS": "1",
            "XDG_CACHE_HOME": str(cache_root),
        }
    )
    if no_start:
        environment["AGENT_INDEX_CELL_NO_START"] = "1"
    if mode == "smoke":
        environment["AGENT_INDEX_CELL_BUILD_SMOKE"] = "1"
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "-Action",
            "cell-provision",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            marketplace_id,
            "-DurableHome",
            str(DURABLE),
            "-OriginPayloadRoot",
            str(payload),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "install.sh"),
            "cell-provision",
            "--context",
            str(install),
            "--expected-marketplace-id",
            marketplace_id,
            "--durable-home",
            str(DURABLE),
            "--origin-payload-root",
            str(payload),
        ]
    return run(command, env=environment, check=False, timeout=600)


def slot_cutover(
    management_payload: Path,
    target_payload: Path,
    install: Path,
    marketplace_id: str,
    namespace_generation: int,
    install_generation: int,
    expected_current_version: str,
    target_version: str,
    *,
    crash_phase: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = clean_environment()
    environment["AGENT_INDEX_ROLE"] = "host"
    target_payload_version = payload_version_from_runtime(target_version)
    if crash_phase is not None:
        environment["AGENT_INDEX_TEST_CUTOVER_CRASH_PHASE"] = crash_phase
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(management_payload / "scripts" / "install.ps1"),
            "-Action",
            "slot-cutover",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            marketplace_id,
            "-ExpectedNamespaceGeneration",
            str(namespace_generation),
            "-ExpectedInstallGeneration",
            str(install_generation),
            "-ExpectedCurrentVersion",
            expected_current_version,
            "-TargetPayloadRoot",
            str(target_payload),
            "-TargetPayloadVersion",
            target_payload_version,
            "-TargetSnapshotId",
            target_payload_version,
            "-TargetRuntimeVersion",
            target_version,
            "-DurableHome",
            str(DURABLE),
        ]
    else:
        command = [
            "bash",
            str(management_payload / "scripts" / "install.sh"),
            "slot-cutover",
            "--context",
            str(install),
            "--expected-marketplace-id",
            marketplace_id,
            "--expected-namespace-generation",
            str(namespace_generation),
            "--expected-install-generation",
            str(install_generation),
            "--expected-current-version",
            expected_current_version,
            "--target-payload-root",
            str(target_payload),
            "--target-payload-version",
            target_payload_version,
            "--target-snapshot-id",
            target_payload_version,
            "--target-runtime-version",
            target_version,
            "--durable-home",
            str(DURABLE),
        ]
    return run(command, env=environment, check=False, timeout=300)


def cell_recover(
    management_payload: Path,
    install: Path,
    marketplace_id: str,
) -> subprocess.CompletedProcess[str]:
    environment = clean_environment()
    environment["AGENT_INDEX_ROLE"] = "host"
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(management_payload / "scripts" / "install.ps1"),
            "-Action",
            "cell-recover",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            marketplace_id,
            "-DurableHome",
            str(DURABLE),
        ]
    else:
        command = [
            "bash",
            str(management_payload / "scripts" / "install.sh"),
            "cell-recover",
            "--context",
            str(install),
            "--expected-marketplace-id",
            marketplace_id,
            "--durable-home",
            str(DURABLE),
        ]
    return run(command, env=environment, check=False, timeout=300)


def cell_runtime_action(
    payload: Path,
    action: str,
    install: Path,
    marketplace_id: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            str(payload / "scripts" / "cell-runtime.py"),
            action,
            "--context",
            str(install),
            "--expected-marketplace-id",
            marketplace_id,
            "--durable-home",
            str(DURABLE),
        ],
        env=clean_environment(),
        check=check,
        timeout=120,
    )


def command_launcher(cell: dict[str, object]) -> Path:
    root = Path(str(cell["plugin_root"])) / "launchers"
    return root / ("agent-index.ps1" if os.name == "nt" else "agent-index")


def service_launcher(cell: dict[str, object]) -> Path:
    root = Path(str(cell["plugin_root"])) / "launchers"
    return root / (
        "agent-index-service.ps1" if os.name == "nt" else "agent-index-service"
    )


def invoke_cell_command(
    cell: dict[str, object],
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    launcher = command_launcher(cell)
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            *arguments,
        ]
    else:
        command = [str(launcher), *arguments]
    return run(command, env=clean_environment(), check=check, timeout=180)


def http_json(
    address: str,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 3.0,
    installation_id: str | None = None,
    instance_token: str | None = None,
) -> dict[str, object]:
    data = b"{}" if method != "GET" else None
    request = urllib.request.Request(
        f"http://{address}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **(
                {"X-Agent-Index-Installation-Id": installation_id}
                if installation_id is not None
                else {}
            ),
            **(
                {"X-Agent-Index-Instance-Token": instance_token}
                if instance_token is not None
                else {}
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"HTTP object expected from {address}{path}")
    return value


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        tail = raw.rsplit(")", 1)[1].split()
        if tail and tail[0] == "Z":
            return False
    except (IndexError, OSError, UnicodeError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_birth_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", ctypes.c_uint32),
                ("high", ctypes.c_uint32),
            ]

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        try:
            if not ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return f"windows-filetime:{(int(creation.high) << 32) | int(creation.low)}"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        tail = raw.rsplit(")", 1)[1].split()
        if tail and tail[0] == "Z":
            return None
        return f"proc-start:{tail[19]}"
    except (IndexError, OSError, UnicodeError):
        result = run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            timeout=5,
        )
        started = result.stdout.strip()
        return f"ps-start:{started}" if result.returncode == 0 and started else None


def current_version(cell: dict[str, object]) -> str:
    return (
        Path(str(cell["plugin_root"])) / "current-version"
    ).read_text(encoding="utf-8").strip()


def profile_runtime_version(payload_version: str, role: str = "host") -> str:
    return f"{payload_version}+{role}"


def payload_version_from_runtime(runtime_version: str) -> str:
    for suffix in ("+host", "+client", "+unconfigured"):
        if runtime_version.endswith(suffix):
            return runtime_version[: -len(suffix)]
    return runtime_version


def endpoint_snapshot(
    cell: dict[str, object],
    expected_version: str,
    *,
    validate_cli: bool = True,
) -> dict[str, object]:
    run_root = Path(str(cell["run_root"]))
    endpoint_record = read_json(run_root / "endpoint.json")
    address = str(endpoint_record["endpoint"])
    pid = int(endpoint_record["pid"])
    process_birth = process_birth_identity(pid)
    if process_birth is None:
        raise RuntimeError(f"service process birth identity is unavailable for pid {pid}")
    health = http_json(address, "/health")
    status = http_json(address, "/status")
    installation_id = f"{cell['marketplace_id']}/{PLUGIN_ID}"
    active_document = read_json(run_root / "zdd" / "active.json")
    active = active_document.get("active")
    if not isinstance(active, dict):
        raise RuntimeError("zdd active.json has no active endpoint")
    active_address = f"{active.get('bind')}:{active.get('port')}"
    if health.get("status") != "ok":
        raise RuntimeError(f"service is not healthy at {address}: {health}")
    if (
        health.get("installationId") != installation_id
        or status.get("installationId") != installation_id
        or health.get("instanceToken") != status.get("instanceToken")
    ):
        raise RuntimeError("service did not attest the expected installation identity")
    if status.get("version") != expected_version:
        raise RuntimeError(
            f"endpoint {address} serves {status.get('version')}, expected {expected_version}"
        )
    if active_address != address or int(active.get("pid", 0)) != pid:
        raise RuntimeError("rendezvous and routing records disagree")
    running = read_json(Path(str(cell["plugin_root"])) / "running-version.json")
    if running.get("version") != expected_version or int(running.get("pid", 0)) != pid:
        raise RuntimeError("running-version evidence disagrees with the live endpoint")
    if validate_cli:
        cli = parse_json_output(invoke_cell_command(cell, "status"))
        if (
            cli.get("state") != "ready"
            or cli.get("running") is not True
            or cli.get("version") != expected_version
        ):
            raise RuntimeError(
                f"cell-local status did not report the live service: {cli}"
            )
    return {
        "address": address,
        "pid": pid,
        "version": expected_version,
        "installation_id": installation_id,
        "instance_token": str(status.get("instanceToken")),
        "process_birth": process_birth,
    }


def wait_for_service(
    cell: dict[str, object],
    expected_version: str,
    *,
    previous: dict[str, object] | None = None,
    validate_cli: bool = True,
) -> dict[str, object]:
    deadline = time.monotonic() + 45
    last_error = ""
    while time.monotonic() < deadline:
        try:
            snapshot = endpoint_snapshot(
                cell,
                expected_version,
                validate_cli=validate_cli,
            )
            if previous is not None and (
                snapshot["address"] == previous["address"]
                or snapshot["pid"] == previous["pid"]
            ):
                last_error = "service has not published a new endpoint and PID"
            else:
                return snapshot
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(
        f"service did not become healthy at version {expected_version}: {last_error}"
    )


def record_service_evidence(
    state: dict[str, object],
    cell_name: str,
    snapshot: dict[str, object],
) -> None:
    evidence = state.setdefault("service_evidence", {})
    if not isinstance(evidence, dict):
        raise RuntimeError("service evidence state is malformed")
    records = evidence.setdefault(cell_name, [])
    if not isinstance(records, list):
        raise RuntimeError("service evidence list is malformed")
    record = {
        key: snapshot[key]
        for key in (
            "address",
            "pid",
            "version",
            "installation_id",
            "instance_token",
            "process_birth",
        )
    }
    if record not in records:
        records.append(record)
    save_state(state)


def record_ensure_worker(
    state: dict[str, object],
    cell_name: str,
    result: dict[str, object],
) -> dict[str, object] | None:
    if result.get("status") not in {"started", "coalesced"}:
        return None
    pid = result.get("pid")
    process_birth = result.get("processBirth")
    worker_token = result.get("workerToken")
    completion = result.get("completionReceipt")
    if (
        type(pid) is not int
        or not isinstance(process_birth, str)
        or not process_birth
        or not isinstance(worker_token, str)
        or re.fullmatch(r"[0-9a-f]{64}", worker_token) is None
        or not isinstance(completion, str)
        or not completion
    ):
        raise RuntimeError(f"service ensure worker result is incomplete: {result}")
    cells = state.get("cells")
    cell = cells.get(cell_name) if isinstance(cells, dict) else None
    if not isinstance(cell, dict):
        raise RuntimeError(f"service ensure worker cell is missing: {cell_name}")
    token_id = hashlib.sha256(worker_token.encode("ascii")).hexdigest()[:24]
    expected_completion = (
        Path(str(cell["run_root"]))
        / "service-ensure-completions"
        / f"{token_id}.json"
    ).resolve()
    if Path(completion).resolve() != expected_completion:
        raise RuntimeError(
            "service ensure worker completion path is not token-bound: "
            f"{completion}"
        )
    record = {
        "pid": pid,
        "process_birth": process_birth,
        "worker_token": worker_token,
        "receipt": result.get("receipt"),
        "completion_receipt": completion,
    }
    workers = state.setdefault("ensure_workers", {})
    if not isinstance(workers, dict):
        raise RuntimeError("service ensure worker state is malformed")
    records = workers.setdefault(cell_name, [])
    if not isinstance(records, list):
        raise RuntimeError("service ensure worker inventory is malformed")
    if record not in records:
        records.append(record)
    save_state(state)
    return record


def wait_for_ensure_worker(
    cell: dict[str, object],
    record: dict[str, object] | None,
) -> None:
    if record is None:
        return
    pid = int(record["pid"])
    process_birth = str(record["process_birth"])
    worker_token = str(record["worker_token"])
    receipt_path = Path(str(cell["run_root"])) / "service-ensure-worker.json"
    completion_path = Path(str(record["completion_receipt"]))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        same_process = process_birth_identity(pid) == process_birth
        receipt_owned = False
        try:
            receipt = read_json(receipt_path)
            receipt_owned = (
                receipt.get("pid") == pid
                and receipt.get("processBirth") == process_birth
                and receipt.get("workerToken") == worker_token
            )
        except Exception:
            receipt_owned = False
        if not same_process and not receipt_owned:
            try:
                completion = read_json(completion_path)
            except Exception as exc:
                raise RuntimeError(
                    "service ensure worker exited without a token-bound "
                    f"completion receipt: {completion_path}"
                ) from exc
            result = completion.get("result")
            if (
                completion.get("schema")
                != "copilot-extensions.agent-index.service-ensure-completion"
                or completion.get("version") != 1
                or completion.get("pid") != pid
                or completion.get("processBirth") != process_birth
                or completion.get("workerToken") != worker_token
                or completion.get("outcome") != "succeeded"
                or not isinstance(result, dict)
                or result.get("status") != "ready"
            ):
                raise RuntimeError(
                    "service ensure worker did not publish a successful "
                    f"token-bound completion: {completion}"
                )
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"service ensure worker did not finish: pid={pid} birth={process_birth}"
    )


def exercise_minimal_store(cell: dict[str, object]) -> None:
    manifest = read_json(
        Path(str(cell["plugin_root"])) / "deploy-manifest.json"
    )
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise RuntimeError("store acceptance cannot resolve the selected runtime")
    interpreter = Path(str(runtime.get("interpreter", "")))
    slot = Path(str(runtime.get("path", "")))
    environment = clean_environment()
    environment.update(
        {
            "AGENT_INDEX_HOME": str(cell["plugin_root"]),
            "AGENT_INDEX_STATE_DIR": str(cell["state_root"]),
            "AGENT_INDEX_DATA_DIR": str(cell["state_root"]),
            "AGENT_INDEX_RUN_DIR": str(cell["run_root"]),
            "AGENT_INDEX_INSTALLATION_ID": (
                f"{cell['marketplace_id']}/{PLUGIN_ID}"
            ),
        }
    )
    code = (
        "import json, os; from pathlib import Path; import agent_index; "
        "from agent_index.chunking.base import Chunk; "
        "from agent_index.store.content_store import ContentStore; "
        "root=Path(os.environ['AGENT_INDEX_STATE_DIR'])/'acceptance-store'; "
        "store=ContentStore(root); "
        "chunk=Chunk(content='acceptance',file_path='acceptance.txt',"
        "chunk_type='text',language='text',line_start=1,line_end=1,"
        "source='acceptance'); "
        "written=store.upsert([chunk]); counts=store.source_counts(); "
        "store.delete_by_source_exact('acceptance'); "
        "remaining=store.source_counts(); "
        "print(json.dumps({'module':str(Path(agent_index.__file__).resolve()),"
        "'written':written,'counts':counts,'remaining':remaining,"
        "'root':str(root.resolve())}))"
    )
    result = run(
        [str(interpreter), "-I", "-X", "utf8", "-c", code],
        env=environment,
        cwd=str(slot),
        timeout=60,
    )
    value = parse_json_output(result)
    module = Path(str(value.get("module", ""))).resolve()
    try:
        module.relative_to(slot.resolve())
    except ValueError as exc:
        raise RuntimeError(
            "store acceptance imported agent_index outside the selected slot"
        ) from exc
    if (
        value.get("written") != 1
        or value.get("counts") != {"acceptance": 1}
        or value.get("remaining") != {}
    ):
        raise RuntimeError(f"minimal store write/read/delete failed: {value}")
    if not Path(str(value.get("root", ""))).is_dir():
        raise RuntimeError("minimal LanceDB store was not initialized")


def owned_live_instances(cell: dict[str, object]) -> list[dict[str, object]]:
    root = Path(str(cell["run_root"])) / "instances"
    if not root.is_dir():
        return []
    expected_installation = f"{cell['marketplace_id']}/{PLUGIN_ID}"
    records: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        record = read_json(path)
        pid = int(record.get("pid", 0))
        port = int(record.get("port", 0))
        if not pid_alive(pid):
            continue
        status = http_json(f"127.0.0.1:{port}", "/health", timeout=1)
        if (
            record.get("installationId") != expected_installation
            or status.get("installationId") != expected_installation
            or int(status.get("pid", 0)) != pid
            or status.get("instanceToken") != record.get("instanceToken")
        ):
            raise RuntimeError(f"unattributable live instance receipt: {path}")
        records.append(record)
    return records


def assert_one_owned_instance(
    cell: dict[str, object],
    expected_version: str,
) -> dict[str, object]:
    active = endpoint_snapshot(cell, expected_version)
    records = owned_live_instances(cell)
    if len(records) != 1:
        raise RuntimeError(
            f"expected one owned Agent Index PID, found {len(records)}: {records}"
        )
    record = records[0]
    if (
        int(record.get("pid", 0)) != int(active["pid"])
        or record.get("runtimeVersion") != expected_version
        or record.get("state") != "active"
    ):
        raise RuntimeError(
            f"owned instance does not match the active route: {record} vs {active}"
        )
    return active


def exact_process_alive(record: dict[str, object]) -> bool:
    pid = int(record.get("pid", 0))
    process_birth = str(record.get("process_birth", ""))
    return bool(process_birth) and process_birth_identity(pid) == process_birth


def exact_service_status(
    record: dict[str, object],
    *,
    timeout: float = 0.5,
) -> dict[str, object] | None:
    address = str(record.get("address", ""))
    if not address:
        return None
    try:
        status = http_json(address, "/health", timeout=timeout)
    except Exception:
        return None
    expected_installation = str(record.get("installation_id", ""))
    expected_token = str(record.get("instance_token", ""))
    expected_version = str(record.get("version", ""))
    if (
        status.get("installationId") != expected_installation
        or int(status.get("pid", 0)) != int(record.get("pid", 0))
        or (
            expected_token
            and status.get("instanceToken") != expected_token
        )
        or (
            expected_version
            and status.get("version") != expected_version
        )
    ):
        return None
    return status


def wait_for_stop(snapshot: dict[str, object]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        live = exact_process_alive(snapshot)
        healthy = exact_service_status(snapshot) is not None
        if not live and not healthy:
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"service remained live after stop: pid={snapshot['pid']} "
        f"endpoint={snapshot['address']}"
    )


def assert_no_legacy_artifacts() -> None:
    if LEGACY.exists() or LEGACY.is_symlink():
        raise RuntimeError(f"legacy runtime root was created: {LEGACY}")
    local_bin = PROFILE / ".local" / "bin"
    for name in ("agent-index", "agent-index.cmd", "agent-index.ps1"):
        if (local_bin / name).exists():
            raise RuntimeError(f"legacy global command was created: {local_bin / name}")
    unit_root = PROFILE / ".config" / "systemd" / "user"
    for name in ("agent-index.service", "agent-index-engine.service"):
        if (unit_root / name).exists():
            raise RuntimeError(f"generic user service unit was created: {unit_root / name}")
    if os.name == "nt":
        for task_name in ("agent-index", "agent-index-engine"):
            result = run(
                ["schtasks.exe", "/Query", "/TN", task_name],
                check=False,
                timeout=20,
            )
            if result.returncode == 0:
                raise RuntimeError(f"generic scheduled task was created: {task_name}")


def assert_cell_artifacts(
    cell: dict[str, object],
    expected_version: str,
) -> None:
    plugin_root = Path(str(cell["plugin_root"]))
    payload_version = payload_version_from_runtime(expected_version)
    version_root = Path(str(cell["versions_root"])) / expected_version
    expected = (
        version_root / ".runtime-slot-completion.json",
        version_root / ".install-complete.json",
        version_root / ".agent-index-runtime-profile.json",
        Path(str(cell["snapshots_root"])) / payload_version,
        Path(str(cell["state_root"])),
        Path(str(cell["run_root"])) / "endpoint.json",
        Path(str(cell["run_root"])) / "zdd" / "active.json",
        Path(str(cell["run_root"])) / "instances",
        Path(str(cell["run_root"])) / "service-identity.json",
        Path(str(cell["logs_root"])) / "service.log",
        Path(str(cell["cache_root"])),
        plugin_root / "config" / "config.yaml",
        command_launcher(cell),
        service_launcher(cell),
        plugin_root / "deploy-manifest.json",
    )
    for path in expected:
        if not path.exists():
            raise RuntimeError(f"cell artifact is absent: {path}")
        if is_link_or_reparse(path):
            raise RuntimeError(f"cell artifact is linked or reparsed: {path}")
    if current_version(cell) != expected_version:
        raise RuntimeError(f"cell selected {current_version(cell)}, expected {expected_version}")
    completion = read_json(version_root / ".install-complete.json")
    if set(completion) != {"version", "completed_at", "pid", "payload_hash"}:
        raise RuntimeError("build completion marker lost its canonical exact shape")
    profile = read_json(version_root / ".agent-index-runtime-profile.json")
    if (
        profile.get("marketplaceId") != cell["marketplace_id"]
        or profile.get("pluginId") != PLUGIN_ID
        or profile.get("runtime", {}).get("version") != expected_version
        or profile.get("profile") != {"role": "host", "extras": ["store"]}
    ):
        raise RuntimeError("runtime dependency profile receipt is invalid")
    identity = read_json(Path(str(cell["run_root"])) / "service-identity.json")
    if (
        identity.get("marketplaceId") != cell["marketplace_id"]
        or identity.get("pluginId") != PLUGIN_ID
        or identity.get("runtimeVersion") != expected_version
        or path_key(str(identity.get("launcher"))) != path_key(service_launcher(cell))
    ):
        raise RuntimeError("service identity does not match its installation cell")


def write_witness(cell: dict[str, object], label: str) -> dict[str, object]:
    witness = {
        "schema": "agent-index.clean-room-state-witness",
        "version": 1,
        "cell": label,
        "marketplaceId": cell["marketplace_id"],
        "value": f"durable-{label}",
    }
    path = Path(str(cell["state_root"])) / "clean-room-witness.json"
    write_json(path, witness)
    if read_json(path) != witness:
        raise RuntimeError(f"durable witness could not be read back for cell {label}")
    return witness


def witness(cell: dict[str, object]) -> dict[str, object]:
    return read_json(Path(str(cell["state_root"])) / "clean-room-witness.json")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instance_inventory(cell: dict[str, object]) -> list[dict[str, object]]:
    root = Path(str(cell["run_root"])) / "instances"
    if not root.is_dir():
        return []
    inventory: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        record = read_json(path)
        inventory.append(
            {
                "name": path.name,
                "digest": file_digest(path),
                "record": record,
                "birth": process_birth_identity(int(record.get("pid", 0))),
            }
        )
    return inventory


def peer_fingerprint(
    cell: dict[str, object],
    expected_version: str,
) -> dict[str, object]:
    live = wait_for_service(cell, expected_version)
    plugin_root = Path(str(cell["plugin_root"]))
    return {
        "current_version": current_version(cell),
        "endpoint": live["address"],
        "pid": live["pid"],
        "manifest": file_digest(plugin_root / "deploy-manifest.json"),
        "identity": file_digest(Path(str(cell["run_root"])) / "service-identity.json"),
        "instances": instance_inventory(cell),
        "witness": witness(cell),
    }


def assert_peer_unchanged(
    before: dict[str, object],
    after: dict[str, object],
    label: str,
) -> None:
    if before != after:
        raise RuntimeError(
            f"{label} changed during another cell's lifecycle operation\n"
            f"before={before}\nafter={after}"
        )


def cell_record(
    name: str,
    payload: Path,
    descriptor: dict[str, str],
    key: str,
    stamped: dict[str, object],
    activated: dict[str, object],
    validated: dict[str, object],
) -> dict[str, object]:
    payload_version = plugin_version(payload)
    return {
        "name": name,
        "payload_v1": str(payload),
        "descriptor": descriptor,
        "key": key,
        "install": str(stamped["installReceipt"]),
        "marketplace_id": str(stamped["marketplaceId"]),
        "plugin_root": str(validated["pluginRoot"]),
        "versions_root": str(validated["versionsRoot"]),
        "snapshots_root": str(validated["snapshotsRoot"]),
        "state_root": str(validated["stateRoot"]),
        "run_root": str(validated["runRoot"]),
        "logs_root": str(validated["logsRoot"]),
        "cache_root": str(validated["cacheRoot"]),
        "namespace_generation": int(stamped["namespaceGeneration"]),
        "install_generation": int(stamped["generation"]),
        "activation_generation": int(activated["activationGeneration"]),
        "payload_version_v1": payload_version,
        "version_v1": profile_runtime_version(payload_version),
    }


def assert_cells_distinct(
    cell_a: dict[str, object],
    cell_b: dict[str, object],
) -> None:
    if cell_a["marketplace_id"] == cell_b["marketplace_id"]:
        raise RuntimeError("independent sources resolved to one marketplace ID")
    path_fields = (
        "plugin_root",
        "versions_root",
        "snapshots_root",
        "state_root",
        "run_root",
        "logs_root",
        "cache_root",
    )
    for field in path_fields:
        if path_key(str(cell_a[field])) == path_key(str(cell_b[field])):
            raise RuntimeError(f"independent cells share {field}")
    for derived in (
        lambda cell: Path(str(cell["run_root"])) / "zdd",
        lambda cell: Path(str(cell["plugin_root"])) / "config",
        lambda cell: Path(str(cell["plugin_root"])) / "launchers",
        lambda cell: Path(str(cell["run_root"])) / "service-identity.json",
        lambda cell: Path(str(cell["versions_root"])) / str(cell["version_v1"]),
    ):
        if path_key(derived(cell_a)) == path_key(derived(cell_b)):
            raise RuntimeError("independent cells share a derived lifecycle artifact")


def bump_payload(payload: Path, old_version: str, new_version: str) -> None:
    plugin_json = read_json(payload / "plugin.json")
    plugin_json["version"] = new_version
    write_json(payload / "plugin.json", plugin_json)
    pyproject = (payload / "pyproject.toml").read_text(encoding="utf-8")
    updated = re.sub(
        r'(?m)^version = "[^"]+"$',
        f'version = "{new_version}"',
        pyproject,
        count=1,
    )
    if updated == pyproject:
        raise RuntimeError("could not update the copied payload pyproject version")
    (payload / "pyproject.toml").write_text(updated, encoding="utf-8")
    init_path = payload / "src" / "agent_index" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8")
    if old_version not in init_text:
        raise RuntimeError("copied payload fallback version is absent")
    init_path.write_text(init_text.replace(old_version, new_version), encoding="utf-8")


def assert_manifest(
    cell: dict[str, object],
    *,
    source_payload: Path,
    source_version: str,
    runtime_payload: Path,
    runtime_version: str,
) -> dict[str, object]:
    manifest = read_json(Path(str(cell["plugin_root"])) / "deploy-manifest.json")
    source = manifest.get("source")
    runtime = manifest.get("runtime")
    installation = manifest.get("installation")
    selected = runtime.get("selectedBy") if isinstance(runtime, dict) else None
    if manifest.get("schema_version") != 4:
        raise RuntimeError("Agent Index deploy manifest is not schema 4")
    if (
        not isinstance(source, dict)
        or source.get("version") != source_version
        or path_key(str(source.get("path"))) != path_key(source_payload)
    ):
        raise RuntimeError("deploy manifest lost reconciled source provenance")
    if (
        not isinstance(runtime, dict)
        or runtime.get("version") != runtime_version
        or path_key(str(runtime.get("path")))
        != path_key(Path(str(cell["versions_root"])) / runtime_version)
    ):
        raise RuntimeError("deploy manifest selected the wrong runtime slot")
    if (
        not isinstance(selected, dict)
        or selected.get("version")
        != payload_version_from_runtime(runtime_version)
        or selected.get("snapshotId")
        != payload_version_from_runtime(runtime_version)
        or path_key(str(selected.get("path"))) != path_key(runtime_payload)
    ):
        raise RuntimeError("deploy manifest lost the selecting payload")
    if (
        not isinstance(installation, dict)
        or installation.get("marketplaceId") != cell["marketplace_id"]
        or installation.get("pluginId") != PLUGIN_ID
        or installation.get("installationId")
        != f"{cell['marketplace_id']}/{PLUGIN_ID}"
        or path_key(str(installation.get("context")))
        != path_key(str(cell["install"]))
    ):
        raise RuntimeError("deploy manifest lost installation identity")
    return manifest


def reset_fixture() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    if DURABLE.exists():
        shutil.rmtree(DURABLE)
    if LEGACY.exists() or LEGACY.is_symlink():
        raise RuntimeError("clean-room profile already has a legacy Agent Index root")


def stage_1() -> None:
    reset_fixture()
    payload = copy_payload("payload-policy")
    for label, policy in (
        ("default", None),
        (
            "false",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": False},
            },
        ),
    ):
        if DURABLE.exists():
            shutil.rmtree(DURABLE)
        if policy is not None:
            write_json(POLICY, policy)
        stamped = stamp(
            payload,
            label,
            source_descriptor(label),
            expected_namespace_generation=0,
            expected_install_generation=0,
        )
        install = Path(str(stamped["installReceipt"]))
        marketplace_id = str(stamped["marketplaceId"])
        status = installation_status(payload, install, marketplace_id)
        if status.get("actualMode") != "legacy":
            raise RuntimeError(f"{label} policy did not retain legacy actual mode: {status}")
        validated = validate_install(install, marketplace_id, payload)
        plugin_root = Path(str(validated["pluginRoot"]))
        if (plugin_root / "installation-activation.json").exists():
            raise RuntimeError(f"{label} policy created an activation receipt")
        if (plugin_root / "versions").exists() or (plugin_root / "deploy-manifest.json").exists():
            raise RuntimeError(f"{label} policy provisioned a namespaced runtime")
        requested = payload_invocation(payload, install, "status", check=False)
        if requested.returncode != 126:
            raise RuntimeError(
                f"{label} policy allowed requested context invocation: "
                f"{requested.returncode}\n{requested.stdout}\n{requested.stderr}"
            )
        legacy = payload_invocation(payload, None, "version", check=False)
        if legacy.returncode != 0 or plugin_version(payload) not in legacy.stdout:
            raise RuntimeError(f"{label} policy did not preserve legacy invocation")
        assert_no_legacy_artifacts()
    print("default-false-policy=pass")


def stage_2() -> None:
    reset_fixture()
    payload_a = copy_payload("payload-a-v1")
    payload_b = copy_payload("payload-b-v1")
    descriptor_a = source_descriptor("alpha")
    descriptor_b = source_descriptor("beta")
    stamped_a = stamp(
        payload_a,
        "alpha",
        descriptor_a,
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    stamped_b = stamp(
        payload_b,
        "beta",
        descriptor_b,
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    set_namespaced_policy(True)
    cells: dict[str, dict[str, object]] = {}
    for name, payload, descriptor, key, stamped in (
        ("a", payload_a, descriptor_a, "alpha", stamped_a),
        ("b", payload_b, descriptor_b, "beta", stamped_b),
    ):
        install = Path(str(stamped["installReceipt"]))
        marketplace_id = str(stamped["marketplaceId"])
        activated = activate(
            install,
            marketplace_id,
            int(stamped["namespaceGeneration"]),
            int(stamped["generation"]),
            0,
        )
        validated = validate_install(install, marketplace_id, payload)
        config = Path(str(validated["pluginRoot"])) / "config" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("role: host\n", encoding="utf-8")
        cells[name] = cell_record(
            name,
            payload,
            descriptor,
            key,
            stamped,
            activated,
            validated,
        )
    cell_a = cells["a"]
    cell_b = cells["b"]
    assert_cells_distinct(cell_a, cell_b)
    mode = build_mode()
    state = {
        "build_mode": mode,
        "source_fingerprint": source_fingerprint(),
        "cells": cells,
        "service_evidence": {},
    }
    save_state(state)
    with ThreadPoolExecutor(max_workers=2) as executor:
        provisions = list(
            executor.map(
                lambda cell: cell_provision(
                    Path(str(cell["payload_v1"])),
                    Path(str(cell["install"])),
                    str(cell["marketplace_id"]),
                    Path(str(cell["cache_root"])),
                    mode=mode,
                    no_start=True,
                ),
                (cell_a, cell_b),
            )
        )
    for cell, result in zip((cell_a, cell_b), provisions):
        if result.returncode != 0:
            raise RuntimeError(
                f"cell {cell['name']} provision failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        ready = parse_json_output(result)
        if ready.get("status") != "ready" or ready.get("engineProvisioned") is not False:
            raise RuntimeError(f"cell {cell['name']} provision result is invalid: {ready}")
    with ThreadPoolExecutor(max_workers=2) as executor:
        starts = list(
            executor.map(
                lambda cell: payload_invocation(
                    Path(str(cell["payload_v1"])),
                    Path(str(cell["install"])),
                    "__cell-service-ensure",
                    check=False,
                ),
                (cell_a, cell_b),
            )
        )
    if any(result.returncode != 0 for result in starts):
        raise RuntimeError(
            "concurrent cell-local service ensure failed\n"
            + "\n".join(
                f"rc={result.returncode}\n{result.stdout}\n{result.stderr}"
                for result in starts
            )
        )
    worker_records: list[dict[str, object] | None] = []
    for name, cell, result in zip(("a", "b"), (cell_a, cell_b), starts):
        worker_records.append(
            record_ensure_worker(state, name, parse_json_output(result))
        )
    for cell, worker in zip((cell_a, cell_b), worker_records):
        wait_for_ensure_worker(cell, worker)
    wait_for_service(cell_a, str(cell_a["version_v1"]))
    wait_for_service(cell_b, str(cell_b["version_v1"]))
    live_a = assert_one_owned_instance(cell_a, str(cell_a["version_v1"]))
    record_service_evidence(state, "a", live_a)
    live_b = assert_one_owned_instance(cell_b, str(cell_b["version_v1"]))
    record_service_evidence(state, "b", live_b)
    if live_a["address"] == live_b["address"] or live_a["pid"] == live_b["pid"]:
        raise RuntimeError("independent cells share an endpoint or PID")
    assert_cell_artifacts(cell_a, str(cell_a["version_v1"]))
    assert_cell_artifacts(cell_b, str(cell_b["version_v1"]))
    if mode == "full":
        exercise_minimal_store(cell_a)
        exercise_minimal_store(cell_b)
    witness_a = write_witness(cell_a, "a")
    witness_b = write_witness(cell_b, "b")
    if witness_a == witness_b:
        raise RuntimeError("cell state witnesses are not independent")
    if (
        Path(str(cell_a["state_root"])) / "clean-room-witness.json"
        == Path(str(cell_b["state_root"])) / "clean-room-witness.json"
    ):
        raise RuntimeError("cell state witnesses share a path")
    assert_manifest(
        cell_a,
        source_payload=payload_a,
        source_version=str(cell_a["payload_version_v1"]),
        runtime_payload=payload_a,
        runtime_version=str(cell_a["version_v1"]),
    )
    assert_manifest(
        cell_b,
        source_payload=payload_b,
        source_version=str(cell_b["payload_version_v1"]),
        runtime_payload=payload_b,
        runtime_version=str(cell_b["version_v1"]),
    )
    assert_no_legacy_artifacts()
    save_state(state)
    print(
        f"dual-cell-service=pass mode={mode} "
        f"a={live_a['address']} b={live_b['address']}"
    )


def stage_3() -> None:
    state = read_state()
    cells = state["cells"]
    cell_a = cells["a"]
    cell_b = cells["b"]
    before_b = peer_fingerprint(cell_b, str(cell_b["version_v1"]))
    before_a = wait_for_service(cell_a, str(cell_a["version_v1"]))
    record_service_evidence(state, "a", before_a)
    payload_v2 = copy_payload("payload-a-v2")
    old_payload_version = str(cell_a["payload_version_v1"])
    new_payload_version = next_dev_version(old_payload_version)
    new_version = profile_runtime_version(new_payload_version)
    bump_payload(payload_v2, old_payload_version, new_payload_version)
    install = Path(str(cell_a["install"]))
    stamped = stamp(
        payload_v2,
        str(cell_a["key"]),
        dict(cell_a["descriptor"]),
        expected_namespace_generation=int(cell_a["namespace_generation"]),
        expected_install_generation=int(cell_a["install_generation"]),
    )
    activated = activate(
        install,
        str(cell_a["marketplace_id"]),
        int(stamped["namespaceGeneration"]),
        int(stamped["generation"]),
        int(cell_a["activation_generation"]),
    )
    result = cell_provision(
        payload_v2,
        install,
        str(cell_a["marketplace_id"]),
        Path(str(cell_a["cache_root"])),
        mode=str(state["build_mode"]),
        no_start=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"updated cell provision failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    ready = parse_json_output(result)
    if ready.get("runtimeVersion") != new_version:
        raise RuntimeError(f"updated provision selected the wrong runtime: {ready}")
    live_a = wait_for_service(cell_a, new_version, previous=before_a)
    record_service_evidence(state, "a", live_a)
    wait_for_stop(before_a)
    live_a = assert_one_owned_instance(cell_a, new_version)
    cell_a.update(
        {
            "payload_v2": str(payload_v2),
            "version_v2": new_version,
            "payload_version_v2": new_payload_version,
            "namespace_generation": int(stamped["namespaceGeneration"]),
            "install_generation": int(stamped["generation"]),
            "activation_generation": int(activated["activationGeneration"]),
        }
    )
    assert_cell_artifacts(cell_a, new_version)
    old_slot = Path(str(cell_a["versions_root"])) / str(cell_a["version_v1"])
    if not (old_slot / ".runtime-slot-completion.json").is_file():
        raise RuntimeError("cell A update removed its original completed slot")
    assert_manifest(
        cell_a,
        source_payload=payload_v2,
        source_version=new_payload_version,
        runtime_payload=payload_v2,
        runtime_version=new_version,
    )
    if witness(cell_a).get("value") != "durable-a":
        raise RuntimeError("cell A durable witness changed during update")
    after_b = peer_fingerprint(cell_b, str(cell_b["version_v1"]))
    assert_peer_unchanged(before_b, after_b, "cell B")
    assert_no_legacy_artifacts()
    save_state(state)
    print(f"isolated-update-cutover=pass endpoint={live_a['address']}")


def stage_4() -> None:
    state = read_state()
    cells = state["cells"]
    cell_a = cells["a"]
    cell_b = cells["b"]
    before_b = peer_fingerprint(cell_b, str(cell_b["version_v1"]))
    before_a = wait_for_service(cell_a, str(cell_a["version_v2"]))
    record_service_evidence(state, "a", before_a)
    payload_v1 = Path(str(cell_a["payload_v1"]))
    historical_runtime = payload_v1 / "scripts" / "cell-runtime.py"
    historical_context = (
        payload_v1
        / "scripts"
        / "installation-context"
        / "installation_context.py"
    )
    historical_runtime.write_text(
        "raise SystemExit('historical management runner must not execute')\n",
        encoding="utf-8",
    )
    historical_context.write_text(
        "raise SystemExit('historical context runner must not execute')\n",
        encoding="utf-8",
    )
    management_payload = Path(str(cell_a["payload_v2"]))
    result = slot_cutover(
        management_payload,
        payload_v1,
        Path(str(cell_a["install"])),
        str(cell_a["marketplace_id"]),
        int(cell_a["namespace_generation"]),
        int(cell_a["install_generation"]),
        str(cell_a["version_v2"]),
        str(cell_a["version_v1"]),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"historical slot cutover failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    value = parse_json_output(result)
    if value.get("status") != "ready":
        raise RuntimeError(f"historical slot cutover was not ready: {value}")
    live_a = wait_for_service(cell_a, str(cell_a["version_v1"]), previous=before_a)
    record_service_evidence(state, "a", live_a)
    wait_for_stop(before_a)
    live_a = assert_one_owned_instance(cell_a, str(cell_a["version_v1"]))
    assert_cell_artifacts(cell_a, str(cell_a["version_v1"]))
    assert_manifest(
        cell_a,
        source_payload=Path(str(cell_a["payload_v2"])),
        source_version=str(cell_a["payload_version_v2"]),
        runtime_payload=payload_v1,
        runtime_version=str(cell_a["version_v1"]),
    )
    for launcher in (command_launcher(cell_a), service_launcher(cell_a)):
        launcher_text = launcher.read_text(encoding="utf-8")
        if str(management_payload) not in launcher_text:
            raise RuntimeError("rollback launcher does not route through current management")
        if str(payload_v1 / "scripts" / "runtime-gate") in launcher_text:
            raise RuntimeError("rollback launcher references historical management")
    bootstrap = cell_runtime_action(
        management_payload,
        "bootstrap",
        Path(str(cell_a["install"])),
        str(cell_a["marketplace_id"]),
    )
    bootstrap_value = parse_json_output(bootstrap)
    if bootstrap_value.get("provisioned") is not False:
        raise RuntimeError("post-rollback bootstrap tried to reverse explicit selection")
    if current_version(cell_a) != str(cell_a["version_v1"]):
        raise RuntimeError("post-rollback bootstrap reversed the historical selection")
    current = str(cell_a["version_v1"])
    current_live = live_a
    for crash_phase, target in (
        ("passive", str(cell_a["version_v2"])),
        ("flipped", str(cell_a["version_v1"])),
        ("draining", str(cell_a["version_v2"])),
        ("committed", str(cell_a["version_v1"])),
    ):
        target_payload = (
            Path(str(cell_a["payload_v2"]))
            if target == str(cell_a["version_v2"])
            else payload_v1
        )
        old_live = current_live
        prior_artifacts = {
            str(path): path.read_bytes()
            for path in (command_launcher(cell_a), service_launcher(cell_a))
        }
        service_identity_path = (
            Path(str(cell_a["run_root"])) / "service-identity.json"
        )
        prior_artifacts[str(service_identity_path)] = (
            service_identity_path.read_bytes()
        )
        crash_evidence_path = (
            Path(str(cell_a["run_root"])) / "cutover-crash-evidence.json"
        )
        crash_evidence_path.unlink(missing_ok=True)
        crashed = slot_cutover(
            management_payload,
            target_payload,
            Path(str(cell_a["install"])),
            str(cell_a["marketplace_id"]),
            int(cell_a["namespace_generation"]),
            int(cell_a["install_generation"]),
            current,
            target,
            crash_phase=crash_phase,
        )
        expected_exit = CRASH_EXIT_CODES[crash_phase]
        if crashed.returncode != expected_exit:
            raise RuntimeError(
                f"cutover crash injection {crash_phase} exited "
                f"{crashed.returncode}, expected {expected_exit}\n"
                f"stdout:\n{crashed.stdout}\nstderr:\n{crashed.stderr}"
            )
        crash_evidence = read_json(crash_evidence_path)
        if (
            crash_evidence.get("phase") != crash_phase
            or crash_evidence.get("exitCode") != expected_exit
            or crash_evidence.get("installationId")
            != f"{cell_a['marketplace_id']}/{PLUGIN_ID}"
            or not crash_evidence.get("transactionId")
        ):
            raise RuntimeError(
                f"cutover crash evidence is not phase-specific: {crash_evidence}"
            )
        if crash_phase == "committed":
            target_live = wait_for_service(
                cell_a,
                target,
                previous=old_live,
                validate_cli=False,
            )
            record_service_evidence(state, "a", target_live)
            set_namespaced_policy(False)
            blocked = cell_recover(
                management_payload,
                Path(str(cell_a["install"])),
                str(cell_a["marketplace_id"]),
            )
            if blocked.returncode != 0:
                raise RuntimeError(
                    "governance-blocked recovery did not complete cleanly "
                    f"({blocked.returncode})\nstdout:\n{blocked.stdout}\n"
                    f"stderr:\n{blocked.stderr}"
                )
            blocked_value = parse_json_output(blocked)
            if (
                blocked_value.get("status") != "governance-blocked"
                or blocked_value.get("runtimeVersion") != current
            ):
                raise RuntimeError(
                    "committed-crash recovery did not restore the prior "
                    f"selection under deactivation: {blocked_value}"
                )
            wait_for_stop(target_live)
            current_live = wait_for_service(cell_a, current)
            if current_live["pid"] != old_live["pid"]:
                raise RuntimeError(
                    "governance rollback did not preserve the exact prior service"
                )
            for raw_path, expected in prior_artifacts.items():
                if Path(raw_path).read_bytes() != expected:
                    raise RuntimeError(
                        "governance rollback did not restore prior launcher "
                        f"or service identity: {raw_path}"
                    )
            blocked_receipt = read_json(
                Path(str(cell_a["plugin_root"])) / "selection-receipt.json"
            )
            if (
                blocked_receipt.get("outcome")
                != "governance-blocked"
                or blocked_receipt.get("priorRuntimeVersion") != current
                or blocked_receipt.get("targetRuntimeVersion") != target
            ):
                raise RuntimeError(
                    "governance rollback receipt is invalid: "
                    f"{blocked_receipt}"
                )
            if (
                Path(str(cell_a["plugin_root"]))
                / "selection-transaction.json"
            ).exists():
                raise RuntimeError(
                    "governance rollback left a pending transaction journal"
                )
            set_namespaced_policy(True)
            retried = slot_cutover(
                management_payload,
                target_payload,
                Path(str(cell_a["install"])),
                str(cell_a["marketplace_id"]),
                int(cell_a["namespace_generation"]),
                int(cell_a["install_generation"]),
                current,
                target,
            )
            if retried.returncode != 0:
                raise RuntimeError(
                    "post-deactivation cutover retry failed "
                    f"({retried.returncode})\nstdout:\n{retried.stdout}\n"
                    f"stderr:\n{retried.stderr}"
                )
        recovered = cell_recover(
            management_payload,
            Path(str(cell_a["install"])),
            str(cell_a["marketplace_id"]),
        )
        if recovered.returncode != 0:
            raise RuntimeError(
                f"cell recovery failed after {crash_phase} crash "
                f"({recovered.returncode})\nstdout:\n{recovered.stdout}\n"
                f"stderr:\n{recovered.stderr}"
            )
        recovered_value = parse_json_output(recovered)
        if recovered_value.get("status") != "ready":
            raise RuntimeError(
                f"cell recovery after {crash_phase} was not ready: "
                f"{recovered_value}"
            )
        current_live = wait_for_service(
            cell_a,
            target,
            previous=old_live,
        )
        wait_for_stop(old_live)
        current_live = assert_one_owned_instance(cell_a, target)
        assert_one_owned_instance(cell_b, str(cell_b["version_v1"]))
        if current_version(cell_a) != target:
            raise RuntimeError(
                f"recovery after {crash_phase} did not persist target marker"
            )
        if (Path(str(cell_a["plugin_root"])) / "selection-transaction.json").exists():
            raise RuntimeError(
                f"recovery after {crash_phase} left a pending transaction"
            )
        selection_receipt = read_json(
            Path(str(cell_a["plugin_root"])) / "selection-receipt.json"
        )
        if (
            selection_receipt.get("outcome") != "committed"
            or selection_receipt.get("targetRuntimeVersion") != target
        ):
            raise RuntimeError(
                f"recovery after {crash_phase} did not commit its transaction: "
                f"{selection_receipt}"
            )
        assert_manifest(
            cell_a,
            source_payload=management_payload,
            source_version=str(cell_a["payload_version_v2"]),
            runtime_payload=target_payload,
            runtime_version=target,
        )
        record_service_evidence(state, "a", current_live)
        current = target
    if current != str(cell_a["version_v1"]):
        raise RuntimeError("crash-recovery matrix did not return to the rollback target")
    if witness(cell_a).get("value") != "durable-a":
        raise RuntimeError("cell A durable witness changed during rollback")
    after_b = peer_fingerprint(cell_b, str(cell_b["version_v1"]))
    assert_peer_unchanged(before_b, after_b, "cell B")
    assert_no_legacy_artifacts()
    print(
        "isolated-schema4-rollback-and-crash-recovery=pass "
        f"endpoint={current_live['address']}"
    )


def expect_blocked(
    label: str,
    result: subprocess.CompletedProcess[str],
    *,
    expected: int | None = None,
) -> None:
    if expected is None:
        blocked = result.returncode != 0
    else:
        blocked = result.returncode == expected
    if not blocked:
        raise RuntimeError(
            f"{label} did not fail closed: rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def stage_5() -> None:
    state = read_state()
    cells = state["cells"]
    cell_a = cells["a"]
    cell_b = cells["b"]
    live_a = wait_for_service(cell_a, str(cell_a["version_v1"]))
    record_service_evidence(state, "a", live_a)
    before_a = peer_fingerprint(cell_a, str(cell_a["version_v1"]))
    before_b = peer_fingerprint(cell_b, str(cell_b["version_v1"]))
    payload_a = Path(str(cell_a["payload_v2"]))
    payload_b = Path(str(cell_b["payload_v1"]))
    install_a = Path(str(cell_a["install"]))
    install_b = Path(str(cell_b["install"]))

    expect_blocked(
        "ordinary namespaced deploy recovery",
        invoke_cell_command(
            cell_a,
            "deploy",
            "--recover",
            "--json",
            check=False,
        ),
    )
    copied_context = WORK / "copied-context" / "install.json"
    copied_context.parent.mkdir(parents=True)
    shutil.copy2(install_a, copied_context)
    expect_blocked(
        "copied context service ensure",
        cell_runtime_action(
            payload_a,
            "service-ensure",
            copied_context,
            str(cell_a["marketplace_id"]),
            check=False,
        ),
    )
    expect_blocked(
        "mismatched marketplace service ensure",
        cell_runtime_action(
            payload_a,
            "service-ensure",
            install_a,
            str(cell_b["marketplace_id"]),
            check=False,
        ),
    )
    for command in (
        ("__cell-service-ensure",),
        ("stop",),
        ("deploy", "--recover", "--json"),
        ("status",),
    ):
        expect_blocked(
            f"foreign payload/context {' '.join(command)}",
            payload_invocation(
                payload_b,
                install_a,
                *command,
                check=False,
            ),
            expected=126,
        )
    expect_blocked(
        "opposite-cell payload/context status",
        payload_invocation(payload_a, install_b, "status", check=False),
        expected=126,
    )
    assert_peer_unchanged(
        before_a,
        peer_fingerprint(cell_a, str(cell_a["version_v1"])),
        "cell A after negative controls",
    )
    assert_peer_unchanged(
        before_b,
        peer_fingerprint(cell_b, str(cell_b["version_v1"])),
        "cell B after negative controls",
    )

    active_a_path = Path(str(cell_a["run_root"])) / "zdd" / "active.json"
    endpoint_a_path = Path(str(cell_a["run_root"])) / "endpoint.json"
    active_a = active_a_path.read_bytes()
    endpoint_a = endpoint_a_path.read_bytes()
    shutil.copy2(
        Path(str(cell_b["run_root"])) / "zdd" / "active.json",
        active_a_path,
    )
    shutil.copy2(
        Path(str(cell_b["run_root"])) / "endpoint.json",
        endpoint_a_path,
    )
    try:
        refused = parse_json_output(invoke_cell_command(cell_a, "stop"))
        if (
            refused.get("stopped") is not False
            or refused.get("reason") != "ownership-mismatch"
        ):
            raise RuntimeError(
                f"cell A accepted cell B's stale routing evidence: {refused}"
            )
        assert_peer_unchanged(
            before_b,
            peer_fingerprint(cell_b, str(cell_b["version_v1"])),
            "cell B after cross-cell stop refusal",
        )
    finally:
        active_a_path.write_bytes(active_a)
        endpoint_a_path.write_bytes(endpoint_a)

    stopped_a = parse_json_output(invoke_cell_command(cell_a, "stop"))
    if stopped_a.get("stopped") is not True:
        raise RuntimeError(f"cell A local stop did not report success: {stopped_a}")
    wait_for_stop(live_a)
    assert_peer_unchanged(
        before_b,
        peer_fingerprint(cell_b, str(cell_b["version_v1"])),
        "cell B after cell A stop",
    )

    live_b = wait_for_service(cell_b, str(cell_b["version_v1"]))
    record_service_evidence(state, "b", live_b)
    stopped_b = parse_json_output(invoke_cell_command(cell_b, "stop"))
    if stopped_b.get("stopped") is not True:
        raise RuntimeError(f"cell B local stop did not report success: {stopped_b}")
    wait_for_stop(live_b)
    assert_no_legacy_artifacts()
    if source_fingerprint() != state["source_fingerprint"]:
        raise RuntimeError("mounted Agent Index source changed during the scenario")
    print("foreign-control-and-isolated-shutdown=pass")


def cleanup() -> None:
    if not STATE.is_file():
        return
    try:
        state = read_state()
    except Exception as exc:
        raise RuntimeError(f"cleanup could not read scenario state: {exc}") from exc
    cells = state.get("cells")
    if not isinstance(cells, dict):
        raise RuntimeError("cleanup scenario state has no cell inventory")
    evidence = state.get("service_evidence", {})
    if not isinstance(evidence, dict):
        raise RuntimeError("cleanup scenario service evidence is malformed")
    worker_evidence = state.get("ensure_workers", {})
    if not isinstance(worker_evidence, dict):
        raise RuntimeError("cleanup scenario worker evidence is malformed")
    failures: list[str] = []
    all_records: list[dict[str, object]] = []
    all_workers: list[dict[str, object]] = []
    for name, value in cells.items():
        if not isinstance(value, dict):
            continue
        recorded_workers = worker_evidence.get(name, [])
        if not isinstance(recorded_workers, list):
            failures.append(f"cell {name} worker evidence is malformed")
            recorded_workers = []
        cell_workers = [
            dict(worker)
            for worker in recorded_workers
            if isinstance(worker, dict)
        ]
        worker_path = Path(str(value.get("run_root", ""))) / "service-ensure-worker.json"
        try:
            worker_receipt = read_json(worker_path)
            discovered_worker = {
                "pid": int(worker_receipt["pid"]),
                "process_birth": str(worker_receipt["processBirth"]),
                "worker_token": str(worker_receipt["workerToken"]),
                "receipt": str(worker_path),
            }
            if discovered_worker not in cell_workers:
                cell_workers.append(discovered_worker)
        except Exception:
            pass
        for worker in recorded_workers:
            if not isinstance(worker, dict):
                failures.append(f"cell {name} has malformed worker evidence")
        for worker in cell_workers:
            all_workers.append(worker)
            try:
                wait_for_ensure_worker(value, worker)
            except Exception as exc:
                failures.append(f"cell {name} ensure worker did not finish: {exc}")
        expected_installation = f"{value.get('marketplace_id')}/{PLUGIN_ID}"
        records = evidence.get(name, [])
        if not isinstance(records, list):
            failures.append(f"cell {name} service evidence is malformed")
            records = []
        cell_records = [
            dict(record)
            for record in records
            if isinstance(record, dict)
        ]
        endpoint_path = Path(str(value.get("run_root", ""))) / "endpoint.json"
        try:
            endpoint = read_json(endpoint_path)
            address = str(endpoint["endpoint"])
            pid = int(endpoint["pid"])
            status = http_json(address, "/health", timeout=1)
            current = {
                "address": address,
                "pid": pid,
                "version": str(status.get("version", "")),
                "installation_id": expected_installation,
                "instance_token": str(status.get("instanceToken", "")),
                "process_birth": process_birth_identity(pid),
            }
            if current not in cell_records:
                cell_records.append(current)
        except Exception:
            pass
        instances_root = Path(str(value.get("run_root", ""))) / "instances"
        if instances_root.is_dir():
            for instance_path in sorted(instances_root.glob("*.json")):
                try:
                    instance = read_json(instance_path)
                    current = {
                        "address": (
                            f"{instance.get('host', '127.0.0.1')}:"
                            f"{int(instance['port'])}"
                        ),
                        "pid": int(instance["pid"]),
                        "version": str(instance.get("runtimeVersion", "")),
                        "installation_id": expected_installation,
                        "instance_token": str(instance.get("instanceToken", "")),
                        "process_birth": process_birth_identity(
                            int(instance["pid"])
                        ),
                    }
                    if current not in cell_records:
                        cell_records.append(current)
                except Exception:
                    failures.append(
                        f"cell {name} has malformed instance receipt {instance_path}"
                    )
        all_records.extend(cell_records)
        seen_services: set[tuple[str, int, str, str]] = set()
        for record in cell_records:
            address = str(record.get("address", ""))
            identity = (
                address,
                int(record.get("pid", 0)),
                str(record.get("instance_token", "")),
                str(record.get("process_birth", "")),
            )
            if not address or identity in seen_services:
                continue
            seen_services.add(identity)
            status = exact_service_status(record, timeout=2)
            if status is None:
                if exact_process_alive(record):
                    failures.append(
                        f"cell {name} could not ownership-attest live pid "
                        f"{record.get('pid')} at {address}"
                    )
                continue
            instance_token = str(record.get("instance_token", ""))
            if not instance_token:
                failures.append(
                    f"cell {name} cleanup evidence has no exact instance token "
                    f"for {address}"
                )
                continue
            try:
                http_json(
                    address,
                    "/shutdown",
                    method="POST",
                    timeout=2,
                    installation_id=expected_installation,
                    instance_token=instance_token,
                )
            except Exception as exc:
                failures.append(
                    f"cell {name} graceful shutdown failed at {address}: {exc}"
                )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        pending = False
        for record in all_records:
            if exact_process_alive(record) or exact_service_status(
                record,
                timeout=0.25,
            ) is not None:
                pending = True
                break
        if not pending:
            break
        time.sleep(0.25)

    for record in all_records:
        pid = int(record.get("pid", 0))
        address = str(record.get("address", ""))
        if exact_process_alive(record):
            failures.append(
                f"recorded Agent Index PID remains alive: pid={pid} "
                f"birth={record.get('process_birth')}"
            )
        status = exact_service_status(record, timeout=0.5)
        if status is not None:
            failures.append(
                f"recorded Agent Index endpoint remains reachable: "
                f"{address} installation={status.get('installationId')}"
            )
    for worker in all_workers:
        pid = int(worker.get("pid", 0))
        birth = str(worker.get("process_birth", ""))
        if birth and process_birth_identity(pid) == birth:
            failures.append(
                f"recorded service ensure worker remains alive: pid={pid} birth={birth}"
            )
    for name, value in cells.items():
        if not isinstance(value, dict):
            continue
        expected_installation = f"{value.get('marketplace_id')}/{PLUGIN_ID}"
        instances_root = Path(str(value.get("run_root", ""))) / "instances"
        if instances_root.is_dir():
            for instance_path in sorted(instances_root.glob("*.json")):
                instance = read_json(instance_path)
                if instance.get("installationId") != expected_installation:
                    continue
                pid = int(instance.get("pid", 0))
                address = (
                    f"{instance.get('host', '127.0.0.1')}:"
                    f"{int(instance.get('port', 0))}"
                )
                recorded = next(
                    (
                        record
                        for record in all_records
                        if int(record.get("pid", 0)) == pid
                        and record.get("instance_token")
                        == instance.get("instanceToken")
                    ),
                    {
                        "address": address,
                        "pid": pid,
                        "version": str(instance.get("runtimeVersion", "")),
                        "installation_id": expected_installation,
                        "instance_token": str(instance.get("instanceToken", "")),
                        "process_birth": "",
                    },
                )
                exact_live = exact_process_alive(recorded)
                exact_live = (
                    exact_live
                    or exact_service_status(recorded, timeout=0.5) is not None
                )
                if exact_live:
                    failures.append(
                        f"cell {name} still has an owned service instance: "
                        f"{instance_path}"
                    )
        worker_path = (
            Path(str(value.get("run_root", "")))
            / "service-ensure-worker.json"
        )
        if worker_path.is_file():
            worker = read_json(worker_path)
            pid = int(worker.get("pid", 0))
            birth = str(worker.get("processBirth", ""))
            if birth and process_birth_identity(pid) == birth:
                failures.append(
                    f"cell {name} still has an owned ensure worker receipt: {worker_path}"
                )
    if failures:
        raise RuntimeError("; ".join(failures))


STAGES = {
    1: stage_1,
    2: stage_2,
    3: stage_3,
    4: stage_4,
    5: stage_5,
}


def main() -> int:
    action = sys.argv[1]
    if action == "build-mode":
        print(build_mode())
        return 0
    if action == "cleanup":
        cleanup()
        return 0
    STAGES[int(action)]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
