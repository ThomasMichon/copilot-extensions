"""Cross-runner tests for immutable snapshot provenance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from powershell_test_host import PowerShellTestHost

LIB = Path(__file__).resolve().parents[1]
PYTHON_SCRIPT = LIB / "installation_context.py"
POSIX_SCRIPT = LIB / "installation-context.sh"
POWERSHELL_TEST_HOST = Path(__file__).with_name("powershell-test-host.ps1")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
FIXTURES = LIB / "fixtures" / "source-identities.json"
BUILD_PID_ERROR = (
    "build completion evidence pid must be an integer from 0 through "
    "9223372036854775807"
)
IMMUTABLE_PID_ERROR = (
    "runtime slot completion build.pid must be an integer from 0 through "
    "9223372036854775807"
)
Runner = tuple[str, tuple[str, ...], str]
_POWERSHELL_HOST: PowerShellTestHost | None = None

def _supported_bash() -> str | None:
    if os.name == "nt":
        return None
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    try:
        result = subprocess.run(
            [
                candidate,
                "--noprofile",
                "--norc",
                "-c",
                "((BASH_VERSINFO[0] > 4 || "
                "(BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4)))",
            ],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return candidate if result.returncode == 0 else None


BASH = _supported_bash()


ALL_RUNNERS: tuple[Runner, ...] = (
    ("python", (sys.executable, str(PYTHON_SCRIPT)), "long"),
    *((("posix", (str(BASH), str(POSIX_SCRIPT)), "long"),) if BASH else ()),
    *(
        (
            (
                "powershell",
                (
                    str(POWERSHELL),
                    "-NoProfile",
                    "-File",
                    str(LIB / "installation-context.ps1"),
                ),
                "powershell",
            ),
        )
        if POWERSHELL is not None
        else ()
    ),
)
PARITY_RUNNERS = ALL_RUNNERS
EXHAUSTIVE_ADAPTERS = (
    os.environ.get("INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS") == "1"
)
REFERENCE_RUNNERS = (
    ALL_RUNNERS
    if EXHAUSTIVE_ADAPTERS
    else ALL_RUNNERS[:1]
)
RUNNERS = REFERENCE_RUNNERS
FAST_PARITY_RUNNERS = (
    ALL_RUNNERS
    if EXHAUSTIVE_ADAPTERS
    else tuple(runner for runner in ALL_RUNNERS if runner[0] != "posix")
)
ADAPTER_RUNNERS = (
    ALL_RUNNERS
    if EXHAUSTIVE_ADAPTERS
    else ALL_RUNNERS[1:]
)
EXHAUSTIVE_RUNNERS = (
    ALL_RUNNERS
    if EXHAUSTIVE_ADAPTERS
    else (
        pytest.param(
            ("exhaustive-adapters-disabled", (), "long"),
            marks=pytest.mark.skip(
                reason="set INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1"
            ),
        ),
    )
)


def _runner_case_matrix(
    cases: tuple[tuple[object, ...], ...],
    adapter_case_ids: set[str],
) -> tuple[object, ...]:
    return tuple(
        pytest.param(
            runner,
            *case[1:],
            id=f"{runner[0]}-{case[0]}",
        )
        for runner in PARITY_RUNNERS
        for case in cases
        if (
            EXHAUSTIVE_ADAPTERS
            or runner[0] == "python"
            or str(case[0]) in adapter_case_ids
        )
    )


def _interoperability_pairs() -> tuple[object, ...]:
    if EXHAUSTIVE_ADAPTERS:
        pairs = [
            (producer, consumer)
            for producer in ALL_RUNNERS
            for consumer in ALL_RUNNERS
        ]
    else:
        reference = ALL_RUNNERS[0]
        pairs = [(reference, reference)]
        for adapter in ALL_RUNNERS[1:]:
            pairs.extend(((reference, adapter), (adapter, reference)))
    return tuple(
        pytest.param(
            producer,
            consumer,
            id=f"from-{producer[0]}-to-{consumer[0]}",
        )
        for producer, consumer in pairs
    )


INTEROPERABILITY_PAIRS = _interoperability_pairs()


@pytest.fixture(scope="module", autouse=True)
def _bounded_powershell_host() -> None:
    yield
    global _POWERSHELL_HOST
    if _POWERSHELL_HOST is not None:
        _POWERSHELL_HOST.close()
        _POWERSHELL_HOST = None


def _load_python_module() -> Any:
    spec = importlib.util.spec_from_file_location("snapshot_installation_context", PYTHON_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _vectors() -> list[dict[str, object]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["vectors"]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_private_json_write_syncs_file_and_posix_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_python_module()
    syncs: list[int] = []
    monkeypatch.setattr(module.os, "fsync", syncs.append)

    path = tmp_path / "private.json"
    module._write_private_json(path, {"value": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}
    assert len(syncs) == (1 if os.name == "nt" else 2)


def _receipt_layout(
    tmp_path: Path,
    *,
    vector_index: int = 0,
    plugin_id: str = "agent-example",
    payload_version: str = "1.0.0",
    snapshot_id: str = "1.0.0",
    payload_root: Path | None = None,
) -> dict[str, Path | str]:
    vector = _vectors()[vector_index]
    normalized = vector["normalized"]
    assert isinstance(normalized, dict)
    marketplace_id = str(vector["marketplaceId"])
    durable = tmp_path / "durable"
    cell = durable / "marketplaces" / marketplace_id
    plugin_root = cell / "plugins" / plugin_id
    payload = payload_root or tmp_path / f"payload-{vector_index}-{plugin_id}"
    if payload_root is None:
        payload.mkdir(parents=True)
        (payload / "content.txt").write_text("original\n", encoding="utf-8")
    plugin_root.mkdir(parents=True)
    namespace = cell / "namespace.json"
    install = plugin_root / "install.json"
    if not namespace.exists():
        _write_json(
            namespace,
            {
                "schema": "copilot-extensions.marketplace-namespace",
                "version": 1,
                "marketplaceId": marketplace_id,
                "source": {
                    "kind": normalized["kind"],
                    "canonical": normalized["canonical"],
                    "ref": normalized["ref"],
                    "fingerprint": f"sha256:{vector['sha256']}",
                },
                "locators": [],
                "generation": 1,
                "state": "active",
            },
        )
    _write_json(
        install,
        {
            "schema": "copilot-extensions.plugin-installation",
            "version": 1,
            "marketplaceId": marketplace_id,
            "pluginId": plugin_id,
            "pluginRoot": str(plugin_root.resolve()),
            "namespaceReceipt": str(namespace.resolve()),
            "payload": {
                "root": str(payload.resolve()),
                "version": payload_version,
                "origin": "explicit",
            },
            "roots": {
                "versions": "versions",
                "snapshots": "snapshots",
                "state": "state",
                "run": "run",
                "logs": "logs",
                "cache": "cache",
                "launchers": "launchers",
            },
            "generation": 2,
            "state": "active",
        },
    )
    snapshot_root = plugin_root / "snapshots" / snapshot_id
    snapshot_root.mkdir(parents=True)
    (snapshot_root / "payload-content.txt").write_text(
        "materialized snapshot\n",
        encoding="utf-8",
    )
    return {
        "marketplace_id": marketplace_id,
        "plugin_id": plugin_id,
        "durable": durable,
        "cell": cell,
        "plugin_root": plugin_root,
        "snapshots": plugin_root / "snapshots",
        "snapshot_root": snapshot_root,
        "payload": payload,
        "namespace": namespace,
        "install": install,
    }


def _flag(style: str, name: str) -> str:
    if style == "long":
        return f"--{name}"
    return "-" + "".join(part.capitalize() for part in name.split("-"))


def _command(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
    runtime_version: str = "3.4.5",
    expected_namespace_generation: int | str = 1,
    expected_install_generation: int | str = 2,
    expected_payload_root: Path | None = None,
    expected_payload_version: str | None = None,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
) -> list[str]:
    _, prefix, style = runner
    command = [
        *prefix,
        action,
        _flag(style, "context"),
        str(layout["install"]),
        _flag(style, "durable-home"),
        str(layout["durable"]),
        _flag(style, "expected-marketplace-id"),
        str(layout["marketplace_id"]),
        _flag(style, "expected-plugin-id"),
        str(layout["plugin_id"]),
        _flag(style, "snapshot-id"),
        snapshot_id,
    ]
    if action in {"snapshot-stamp", "slot-cutover"}:
        command.extend(
            [
                _flag(style, "expected-namespace-generation"),
                str(expected_namespace_generation),
                _flag(style, "expected-install-generation"),
                str(expected_install_generation),
            ]
        )
    if action in {
        "slot-provision",
        "slot-validate",
        "slot-complete",
        "slot-completion-validate",
        "slot-cutover",
    }:
        command.extend(
            [
                _flag(style, "runtime-version"),
                runtime_version,
            ]
        )
        if expected_payload_root is not None:
            command.extend(
                [
                    _flag(style, "expected-payload-root"),
                    str(expected_payload_root),
                ]
            )
        if expected_payload_version is not None:
            command.extend(
                [
                    _flag(style, "expected-payload-version"),
                    expected_payload_version,
                ]
            )
    if action == "slot-cutover":
        if expected_current_version is not None:
            command.extend(
                [
                    _flag(style, "expected-current-version"),
                    expected_current_version,
                ]
            )
        elif expect_current_absent or expected_current_version is None:
            command.append(_flag(style, "expect-current-absent"))
    return command


def _run(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
    runtime_version: str = "3.4.5",
    expected_namespace_generation: int | str = 1,
    expected_install_generation: int | str = 2,
    expected_payload_root: Path | None = None,
    expected_payload_version: str | None = None,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
    environment_overrides: dict[str, str] | None = None,
    check: bool = True,
    direct: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = _command(
        runner,
        action,
        layout,
        snapshot_id=snapshot_id,
        runtime_version=runtime_version,
        expected_namespace_generation=expected_namespace_generation,
        expected_install_generation=expected_install_generation,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        expected_current_version=expected_current_version,
        expect_current_absent=expect_current_absent,
    )
    if runner[0] == "powershell" and not direct:
        global _POWERSHELL_HOST
        if _POWERSHELL_HOST is None:
            assert POWERSHELL is not None
            _POWERSHELL_HOST = PowerShellTestHost(
                str(POWERSHELL),
                POWERSHELL_TEST_HOST,
                LIB / "installation-context.ps1",
                timeout_seconds=60,
            )
        _, prefix, _ = runner
        result = _POWERSHELL_HOST.run(
            tuple(command[len(prefix) :]),
            environment_overrides,
        )
    else:
        environment = os.environ.copy()
        environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        environment.pop("COPILOT_PLUGIN_ROOT", None)
        if environment_overrides:
            environment.update(environment_overrides)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
    if check and result.returncode:
        raise AssertionError(
            f"{runner[0]} failed ({result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _run_slot(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    expected_payload_root: Path | None = None,
    expected_payload_version: str | None = None,
    environment_overrides: dict[str, str] | None = None,
    check: bool = True,
    direct: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        runner,
        action,
        layout,
        runtime_version=runtime_version,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        environment_overrides=environment_overrides,
        check=check,
        direct=direct,
    )


def _run_context_validate(
    runner: Runner,
    layout: dict[str, Path | str],
) -> subprocess.CompletedProcess[str]:
    _, prefix, style = runner
    arguments = (
        "validate",
        _flag(style, "context"),
        str(layout["install"]),
        _flag(style, "durable-home"),
        str(layout["durable"]),
        _flag(style, "expected-marketplace-id"),
        str(layout["marketplace_id"]),
        _flag(style, "expected-plugin-id"),
        str(layout["plugin_id"]),
    )
    if runner[0] == "powershell":
        global _POWERSHELL_HOST
        if _POWERSHELL_HOST is None:
            assert POWERSHELL is not None
            _POWERSHELL_HOST = PowerShellTestHost(
                str(POWERSHELL),
                POWERSHELL_TEST_HOST,
                LIB / "installation-context.ps1",
                timeout_seconds=60,
            )
        return _POWERSHELL_HOST.run(arguments, None)
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    return subprocess.run(
        [*prefix, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _stamp_with_python(
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
) -> dict[str, object]:
    result = _run(
        REFERENCE_RUNNERS[0],
        "snapshot-stamp",
        layout,
        snapshot_id=snapshot_id,
    )
    return json.loads(result.stdout)


def _provenance_path(
    layout: dict[str, Path | str],
    snapshot_id: str = "1.0.0",
) -> Path:
    return Path(layout["snapshots"]) / snapshot_id / "snapshot-provenance.json"


def _provision_slot_with_python(
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    expected_payload_root: Path | None = None,
    expected_payload_version: str | None = None,
    module: Any | None = None,
) -> dict[str, object]:
    module = module or _load_python_module()
    return module.provision_runtime_slot(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        runtime_version=runtime_version,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=layout["durable"],
        environment={},
    )


def _payload_version(layout: dict[str, Path | str]) -> str:
    install = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    return str(install["payload"]["version"])


def _snapshot_content_digest(snapshot_root: Path) -> str:
    records: list[tuple[bytes, str]] = []
    for directory, directory_names, file_names in os.walk(
        snapshot_root,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in directory_names:
            assert not (directory_path / name).is_symlink()
        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(snapshot_root).as_posix()
            if relative == "snapshot-provenance.json":
                continue
            records.append(
                (
                    relative.encode("utf-8"),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    digest = hashlib.sha256()
    for relative, file_digest in sorted(records):
        digest.update(b"F\0")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _build_receipt(
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    completed_at: str = "2026-01-02T03:04:05Z",
    pid: object = 123,
    payload_hash: object | None = None,
) -> Path:
    install = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    versions_root = (
        Path(layout["plugin_root"]) / str(install["roots"]["versions"])
    )
    path = (
        versions_root
        / runtime_version
        / ".install-complete.json"
    )
    _write_json(
        path,
        {
            "version": runtime_version,
            "completed_at": completed_at,
            "pid": pid,
            "payload_hash": (
                payload_hash
                if payload_hash is not None
                else _snapshot_content_digest(Path(layout["snapshot_root"]))
            ),
        },
    )
    return path


def _run_completion(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    expected_payload_root: Path | None = None,
    expected_payload_version: str | None = None,
    environment_overrides: dict[str, str] | None = None,
    check: bool = True,
    direct: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run_slot(
        runner,
        action,
        layout,
        runtime_version=runtime_version,
        expected_payload_root=expected_payload_root or Path(layout["payload"]),
        expected_payload_version=expected_payload_version or _payload_version(layout),
        environment_overrides=environment_overrides,
        check=check,
        direct=direct,
    )


def _run_cutover(
    runner: Runner,
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    expected_namespace_generation: int | str = 1,
    expected_install_generation: int | str = 2,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
    check: bool = True,
    direct: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        runner,
        "slot-cutover",
        layout,
        runtime_version=runtime_version,
        expected_namespace_generation=expected_namespace_generation,
        expected_install_generation=expected_install_generation,
        expected_payload_root=Path(layout["payload"]),
        expected_payload_version=_payload_version(layout),
        expected_current_version=expected_current_version,
        expect_current_absent=expect_current_absent,
        check=check,
        direct=direct,
    )


def _prepare_completion_slot(
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
) -> Path:
    _stamp_with_python(layout)
    _provision_slot_with_python(layout, runtime_version=runtime_version)
    return _build_receipt(layout, runtime_version=runtime_version)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        else:
            snapshot[relative] = ("other", b"")
    return snapshot


def _legacy_footprint_snapshot(
    plugin_root: Path,
    home: Path,
) -> dict[str, tuple[str, object]]:
    manifest = json.loads(
        (plugin_root / "payload-invocation.json").read_text(encoding="utf-8")
    )
    snapshot: dict[str, tuple[str, object]] = {}
    for relative in manifest["installation"]["legacyFootprint"]["paths"]:
        path = home / relative
        if path.is_dir():
            snapshot[relative] = ("directory", _tree_snapshot(path))
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        elif path.exists() or path.is_symlink():
            snapshot[relative] = ("other", b"")
        else:
            snapshot[relative] = ("missing", b"")
    return snapshot


EXEMPLAR_INSTALLERS = (
    *(
        (
            (
                "agent-machines",
                (
                    str(BASH),
                    str(
                        LIB.parents[1]
                        / "plugins"
                        / "agent-machines"
                        / "scripts"
                        / "init.sh"
                    ),
                ),
                "long",
            ),
            (
                "agent-index",
                (
                    str(BASH),
                    str(
                        LIB.parents[1]
                        / "plugins"
                        / "agent-index"
                        / "scripts"
                        / "install.sh"
                    ),
                ),
                "long",
            ),
        )
        if BASH is not None
        else ()
    ),
    *(
        (
            (
                "agent-machines",
                (
                    str(POWERSHELL),
                    "-NoProfile",
                    "-File",
                    str(LIB.parents[1] / "plugins" / "agent-machines" / "scripts" / "init.ps1"),
                ),
                "powershell",
            ),
            (
                "agent-index",
                (
                    str(POWERSHELL),
                    "-NoProfile",
                    "-File",
                    str(LIB.parents[1] / "plugins" / "agent-index" / "scripts" / "install.ps1"),
                ),
                "powershell",
            ),
        )
        if POWERSHELL is not None
        else ()
    ),
)
BEHAVIOR_EXEMPLAR_INSTALLERS = (
    EXEMPLAR_INSTALLERS
    if EXHAUSTIVE_ADAPTERS
    else tuple(
        exemplar
        for exemplar in EXEMPLAR_INSTALLERS
        if exemplar[0] == "agent-index"
    )
)
SECURITY_EXEMPLAR_INSTALLERS = (
    EXEMPLAR_INSTALLERS
    if EXHAUSTIVE_ADAPTERS
    else tuple(
        exemplar
        for exemplar in EXEMPLAR_INSTALLERS
        if exemplar[0] == "agent-index" or exemplar[2] == "long"
    )
)


def _run_exemplar_slot_action(
    exemplar: tuple[str, tuple[str, ...], str],
    action: str,
    layout: dict[str, Path | str],
    tmp_path: Path,
    *,
    include_context: bool = True,
    environment_overrides: dict[str, str] | None = None,
    expected_namespace_generation: int = 1,
    expected_install_generation: int = 2,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
) -> subprocess.CompletedProcess[str]:
    _, _prefix, style = exemplar
    command_prefix, installed_plugin, home = _installed_exemplar(exemplar, tmp_path)
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    if environment_overrides:
        environment.update(environment_overrides)
    command = [*command_prefix]
    if style == "powershell":
        command.extend(["-Action", action])
    else:
        command.append(action)
    if include_context:
        command.extend(
            [
                _flag(style, "context"),
                str(layout["install"]),
            ]
        )
    command.extend(
        [
            _flag(style, "expected-marketplace-id"),
            str(layout["marketplace_id"]),
            _flag(style, "durable-home"),
            str(layout["durable"]),
        ]
    )
    if action == "slot-cutover":
        command.extend(
            [
                _flag(style, "expected-namespace-generation"),
                str(expected_namespace_generation),
                _flag(style, "expected-install-generation"),
                str(expected_install_generation),
            ]
        )
        if expected_current_version is not None:
            command.extend(
                [
                    _flag(style, "expected-current-version"),
                    expected_current_version,
                ]
            )
        elif expect_current_absent:
            command.append(_flag(style, "expect-current-absent"))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=installed_plugin,
        check=False,
    )


def _installed_exemplar(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> tuple[tuple[str, ...], Path, Path]:
    _, prefix, _ = exemplar
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    source_script = Path(prefix[-1])
    source_plugin = source_script.parents[1]
    installed_plugin = (
        home / ".copilot" / "installed-plugins" / "example--0123456789abcdef" / source_plugin.name
    )
    if not installed_plugin.exists():
        shutil.copytree(
            source_plugin,
            installed_plugin,
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
    installed_script = installed_plugin / source_script.relative_to(source_plugin)
    return (*prefix[:-1], str(installed_script)), installed_plugin, home


@pytest.mark.parametrize(
    "exemplar",
    tuple(item for item in EXEMPLAR_INSTALLERS if item[0] == "agent-machines"),
    ids=lambda exemplar: exemplar[2],
)
def test_agent_machines_cell_provision_serializes_complete_transaction(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    version = next(
        line.split('"')[1]
        for line in (installed_plugin / "pyproject.toml").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("version = ")
    )
    layout = _receipt_layout(
        tmp_path,
        plugin_id="agent-machines",
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    witness = tmp_path / "cell-provision-lock-events.txt"
    environment = {
        "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE": str(witness),
        "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_SLEEP": "1",
        "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_MILLISECONDS": "1000",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: _run_exemplar_slot_action(
                    exemplar,
                    "cell-provision",
                    layout,
                    tmp_path,
                    environment_overrides=environment,
                ),
                range(2),
            )
        )

    assert all(result.returncode == 0 for result in results), [
        result.stderr for result in results
    ]
    events = witness.read_text(encoding="utf-8").splitlines()
    assert len(events) == 4
    first_start = events[0].split()
    first_end = events[1].split()
    second_start = events[2].split()
    second_end = events[3].split()
    assert first_start[0] == "start"
    assert first_end == ["end", first_start[1]]
    assert second_start[0] == "start"
    assert second_end == ["end", second_start[1]]


def _function_source(
    script: Path,
    start: str,
    following: str,
) -> str:
    source = script.read_text(encoding="utf-8")
    begin = source.index(start)
    end = source.index(following, begin)
    return source[begin:end].rstrip() + "\n"


def _run_agent_machines_snapshot_harness(
    exemplar: tuple[str, tuple[str, ...], str],
    installed_plugin: Path,
    layout: dict[str, Path | str],
    tmp_path: Path,
    *,
    fail_before_stamp: bool = False,
) -> subprocess.CompletedProcess[str]:
    _, _, style = exemplar
    version = next(
        line.split('"')[1]
        for line in (installed_plugin / "pyproject.toml").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("version = ")
    )
    source_script = installed_plugin / "scripts" / (
        "init.ps1" if style == "powershell" else "init.sh"
    )
    harness_root = tmp_path / f"snapshot-harness-{style}"
    harness_root.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "SNAPSHOT_ROOT_TEST": str(layout["snapshot_root"]),
    })
    if fail_before_stamp:
        environment["AGENT_MACHINES_CELL_SNAPSHOT_FAIL_BEFORE_STAMP"] = "1"
    else:
        environment.pop(
            "AGENT_MACHINES_CELL_SNAPSHOT_FAIL_BEFORE_STAMP",
            None,
        )
    if style == "powershell":
        fake_runner = harness_root / "fake-context.ps1"
        fake_runner.write_text(
            """param(
    [Parameter(Position=0)][string]$Action,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedNamespaceGeneration,
    [string]$ExpectedInstallGeneration,
    [string]$SnapshotId,
    [string]$DurableHome
)
$provenance = Join-Path $env:SNAPSHOT_ROOT_TEST 'snapshot-provenance.json'
if ($Action -ceq 'snapshot-stamp') {
    [IO.File]::WriteAllText($provenance, "{}`n")
    exit 0
}
if ($Action -ceq 'snapshot-validate' -and (Test-Path -LiteralPath $provenance -PathType Leaf)) {
    exit 0
}
exit 1
""",
            encoding="utf-8",
        )
        def ps(value: object) -> str:
            return str(value).replace("'", "''")

        harness = harness_root / "run.ps1"
        harness.write_text(
            "\n".join(
                (
                    "Set-StrictMode -Version 2.0",
                    "$ErrorActionPreference = 'Stop'",
                    f"$PluginDir = '{ps(installed_plugin)}'",
                    f"$snapshotsRoot = '{ps(layout['snapshots'])}'",
                    f"$SrcVersion = '{ps(version)}'",
                    f"$ExpectedMarketplaceId = '{ps(layout['marketplace_id'])}'",
                    f"$Context = '{ps(layout['install'])}'",
                    "$cellNamespaceGeneration = '1'",
                    "$cellInstallGeneration = '2'",
                    f"$DurableHome = '{ps(layout['durable'])}'",
                    f"$slotRunner = '{ps(fake_runner)}'",
                    "$probeHost = (Get-Process -Id $PID).Path",
                    _function_source(
                        source_script,
                        "function Get-CellSnapshotOwnerText {",
                        "\n# -- Paths",
                    ).rstrip(),
                    "try {",
                    f"    Ensure-CellSnapshot -SnapshotRoot '{ps(layout['snapshot_root'])}'",
                    "    exit 0",
                    "} catch {",
                    "    Write-Error $_",
                    "    exit 1",
                    "}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(harness),
        ]
    else:
        fake_runner = harness_root / "fake-context.sh"
        fake_runner.write_text(
            """#!/usr/bin/env bash
case "$1" in
  snapshot-stamp)
    printf '{}\n' >"$SNAPSHOT_ROOT_TEST/snapshot-provenance.json"
    ;;
  snapshot-validate)
    test -f "$SNAPSHOT_ROOT_TEST/snapshot-provenance.json"
    ;;
  *)
    exit 1
    ;;
esac
""",
            encoding="utf-8",
        )
        fake_runner.chmod(0o755)
        harness = harness_root / "run.sh"
        harness.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    "set -u",
                    f"PLUGIN_DIR={shlex.quote(str(installed_plugin))}",
                    f"SNAPSHOTS_ROOT={shlex.quote(str(layout['snapshots']))}",
                    f"SRC_VERSION={shlex.quote(version)}",
                    f"EXPECTED_MARKETPLACE_ID={shlex.quote(str(layout['marketplace_id']))}",
                    f"CONTEXT={shlex.quote(str(layout['install']))}",
                    "CELL_NAMESPACE_GENERATION=1",
                    "CELL_INSTALL_GENERATION=2",
                    f"DURABLE_HOME={shlex.quote(str(layout['durable']))}",
                    f"SLOT_RUNNER={shlex.quote(str(fake_runner))}",
                    "_fail() { printf '%s\\n' \"$1\" >&2; }",
                    _function_source(
                        source_script,
                        "_cell_snapshot_owner_text() {",
                        "\nFORCE=0",
                    ).rstrip(),
                    f"_ensure_cell_snapshot {shlex.quote(str(layout['snapshot_root']))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        command = [str(BASH), str(harness)]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


@pytest.mark.parametrize(
    "exemplar",
    tuple(item for item in EXEMPLAR_INSTALLERS if item[0] == "agent-machines"),
    ids=lambda exemplar: exemplar[2],
)
def test_agent_machines_snapshot_publication_failure_is_retryable(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    version = next(
        line.split('"')[1]
        for line in (installed_plugin / "pyproject.toml").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("version = ")
    )
    layout = _receipt_layout(
        tmp_path,
        plugin_id="agent-machines",
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    snapshot_root = Path(layout["snapshot_root"])
    shutil.rmtree(snapshot_root)

    failed = _run_agent_machines_snapshot_harness(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        fail_before_stamp=True,
    )

    assert failed.returncode != 0
    assert not snapshot_root.exists()
    assert not list(Path(layout["snapshots"]).glob(".agent-machines-snapshot-*"))

    snapshot_root.mkdir(parents=True)
    (snapshot_root / "interrupted.txt").write_text("partial\n", encoding="utf-8")
    (snapshot_root / ".agent-machines-snapshot-publish-owner").write_text(
        "\n".join(
            (
                "copilot-extensions.agent-machines.snapshot-publish:v1",
                f"marketplaceId={layout['marketplace_id']}",
                "pluginId=agent-machines",
                f"snapshotId={version}",
                "",
            )
        ),
        encoding="utf-8",
    )

    retried = _run_agent_machines_snapshot_harness(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
    )

    assert retried.returncode == 0, retried.stderr
    assert (snapshot_root / "snapshot-provenance.json").is_file()
    assert (snapshot_root / "plugin.json").is_file()
    assert not (snapshot_root / "interrupted.txt").exists()
    assert not (
        snapshot_root / ".agent-machines-snapshot-publish-owner"
    ).exists()
    assert not list(Path(layout["snapshots"]).glob(".agent-machines-snapshot-*"))


@pytest.mark.parametrize(
    "exemplar",
    tuple(item for item in EXEMPLAR_INSTALLERS if item[0] == "agent-machines"),
    ids=lambda exemplar: exemplar[2],
)
def test_agent_machines_snapshot_publication_preserves_unowned_final_state(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    version = next(
        line.split('"')[1]
        for line in (installed_plugin / "pyproject.toml").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("version = ")
    )
    layout = _receipt_layout(
        tmp_path,
        plugin_id="agent-machines",
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    snapshot_root = Path(layout["snapshot_root"])
    sentinel = snapshot_root / "payload-content.txt"
    before = sentinel.read_bytes()

    result = _run_agent_machines_snapshot_harness(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
    )

    assert result.returncode != 0
    assert sentinel.read_bytes() == before
    assert not (
        snapshot_root / ".agent-machines-snapshot-publish-owner"
    ).exists()


def _activation_tail_source(script: Path, plugin_id: str, style: str) -> str:
    source = script.read_text(encoding="utf-8")
    if style == "powershell":
        call = source.rindex("Invoke-VersionedMarkComplete\n")
        following = (
            "\n    Write-Ok "
            if plugin_id == "agent-machines"
            else "\n    Write-Manifest"
        )
    else:
        call = source.rindex("_versioned_mark_complete\n")
        following = (
            "\n    _ok "
            if plugin_id == "agent-machines"
            else "\n    _write_manifest"
        )
    start = source.rfind("\n", 0, call) + 1
    return source[start : source.index(following, start)].rstrip() + "\n"


def _run_exemplar_mark_complete(
    exemplar: tuple[str, tuple[str, ...], str],
    installed_plugin: Path,
    layout: dict[str, Path | str],
    tmp_path: Path,
    *,
    runtime_version: str,
    python_executable: str | None = None,
    activation_sentinel: Path | None = None,
    environment_overrides: dict[str, str] | None = None,
    powershell_preamble: str = "",
) -> subprocess.CompletedProcess[str]:
    _, _, style = exemplar
    producer = tmp_path / f"producer-{installed_plugin.name}-{style}"
    producer.mkdir()
    source_script = installed_plugin / "scripts" / (
        ("init.ps1" if style == "powershell" else "init.sh")
        if installed_plugin.name == "agent-machines"
        else ("install.ps1" if style == "powershell" else "install.sh")
    )
    powershell_sentinel = (
        str(activation_sentinel).replace("'", "''")
        if activation_sentinel is not None
        else ""
    )
    if style == "powershell":
        harness = producer / "producer.ps1"
        shutil.copyfile(
            installed_plugin / "scripts" / "versioned_runtime.py",
            producer / "versioned_runtime.py",
        )
        harness.write_text(
            "\n".join(
                (
                    "param(",
                    "    [Parameter(Mandatory = $true)][string]$PayloadRoot,",
                    "    [Parameter(Mandatory = $true)][string]$RuntimeRoot,",
                    "    [Parameter(Mandatory = $true)][string]$RuntimeVersion,",
                    "    [Parameter(Mandatory = $true)][string]$Python",
                    ")",
                    "Set-StrictMode -Version 2.0",
                    "$ErrorActionPreference = 'Stop'",
                    "$PluginDir = $PayloadRoot",
                    "$InstallDir = $RuntimeRoot",
                    "$LinkDir = Join-Path $InstallDir '.venv'",
                    "$SrcVersion = $RuntimeVersion",
                    "$VersionedRuntime = $true",
                    "$VenvPython = $Python",
                    "$VrScript = Join-Path $PSScriptRoot 'versioned_runtime.py'",
                    "function Get-BootstrapPython { return $Python }",
                    "function Write-Fail([string]$Message) { throw $Message }",
                    "function Write-Ok([string]$Message) {}",
                    (
                        "function Invoke-VersionedActivate { "
                        f"[IO.File]::WriteAllText('{powershell_sentinel}', 'activated'); "
                        "return $true }"
                        if activation_sentinel is not None
                        and installed_plugin.name == "agent-index"
                        else ""
                    ),
                    _function_source(
                        source_script,
                        "function Get-PayloadHash(",
                        "\nfunction Invoke-VersionedSlotClean",
                    ).rstrip(),
                    _function_source(
                        source_script,
                        "function Invoke-VersionedMarkComplete {",
                        "\n# === end install-contract:v4 marker/toss helpers",
                    ).rstrip(),
                    powershell_preamble,
                    (
                        _activation_tail_source(
                            source_script,
                            installed_plugin.name,
                            style,
                        ).rstrip()
                        if activation_sentinel is not None
                        else "Invoke-VersionedMarkComplete"
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(harness),
            "-PayloadRoot",
            str(installed_plugin),
            "-RuntimeRoot",
            str(layout["plugin_root"]),
            "-RuntimeVersion",
            runtime_version,
            "-Python",
            python_executable or sys.executable,
        ]
    else:
        harness = producer / "producer.sh"
        harness.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f"PLUGIN_DIR={shlex.quote(str(installed_plugin))}",
                    f"SCRIPT_DIR={shlex.quote(str(installed_plugin / 'scripts'))}",
                    f"INSTALL_DIR={shlex.quote(str(layout['plugin_root']))}",
                    'LINK_DIR="$INSTALL_DIR/.venv"',
                    f"SRC_VERSION={shlex.quote(runtime_version)}",
                    "VERSIONED_RUNTIME=1",
                    f"PYTHON={shlex.quote(python_executable or sys.executable)}",
                    'VENV_PYTHON="$PYTHON"',
                    'VR_SCRIPT="$SCRIPT_DIR/versioned_runtime.py"',
                    "_fail() { printf '%s\\n' \"$1\" >&2; }",
                    "_ok() { :; }",
                    "_bootstrap_python() { printf '%s\\n' \"$PYTHON\"; }",
                    (
                        "_versioned_activate() { "
                        f"printf activated >{shlex.quote(str(activation_sentinel))}; }}"
                        if activation_sentinel is not None
                        and installed_plugin.name == "agent-index"
                        else ""
                    ),
                    _function_source(
                        source_script,
                        "_payload_hash() {",
                        "\n_versioned_slot_clean()",
                    ).rstrip(),
                    _function_source(
                        source_script,
                        "_versioned_mark_complete() {",
                        "\necho ''",
                    ).rstrip()
                    if installed_plugin.name == "agent-machines"
                    else _function_source(
                        source_script,
                        "_versioned_mark_complete() {",
                        "\n# === install-contract:v4 source-kind",
                    ).rstrip(),
                    (
                        _activation_tail_source(
                            source_script,
                            installed_plugin.name,
                            style,
                        ).rstrip()
                        if activation_sentinel is not None
                        else "_versioned_mark_complete"
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        command = ("bash", str(harness))
    environment = os.environ.copy()
    if environment_overrides:
        environment.update(environment_overrides)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _run_exemplar_payload_hash(
    exemplar: tuple[str, tuple[str, ...], str],
    payload_root: Path,
    work_root: Path,
    *,
    max_entries: int,
    max_path_bytes: int,
    max_content_bytes: int,
) -> subprocess.CompletedProcess[str]:
    _, prefix, style = exemplar
    source_script = Path(prefix[-1])
    harness_root = work_root / f"payload-hash-{source_script.parent.parent.name}-{style}"
    harness_root.mkdir(parents=True)
    if style == "powershell":
        harness = harness_root / "hash.ps1"
        harness.write_text(
            "\n".join(
                (
                    "param([string]$PayloadRoot)",
                    "$ErrorActionPreference = 'Stop'",
                    "$PluginDir = $PayloadRoot",
                    _function_source(
                        source_script,
                        "function Get-PayloadHash(",
                        "\nfunction Invoke-VersionedSlotClean",
                    ).rstrip(),
                    (
                        "Get-PayloadHash "
                        f"-MaxEntries {max_entries} "
                        f"-MaxPathBytes {max_path_bytes} "
                        f"-MaxContentBytes {max_content_bytes}"
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        command = (
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(harness),
            "-PayloadRoot",
            str(payload_root),
        )
    else:
        harness = harness_root / "hash.sh"
        harness.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f"PLUGIN_DIR={shlex.quote(str(payload_root))}",
                    "_fail() { printf '%s\\n' \"$1\" >&2; }",
                    _function_source(
                        source_script,
                        "_payload_hash() {",
                        "\n_versioned_slot_clean()",
                    ).rstrip(),
                    (
                        "_payload_hash "
                        f"{max_entries} {max_path_bytes} {max_content_bytes}"
                    ),
                    "",
                )
            ),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        command = ("bash", str(harness))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _payload_file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_marker_writer_failure_prevents_activation_tail(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _ = exemplar
    plugin_root = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (plugin_root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    slot = Path(layout["plugin_root"]) / "versions" / version
    slot.mkdir(parents=True)
    activation_sentinel = tmp_path / "activation-reached"
    dispatcher_source = tmp_path / "completion-writer-dispatch.py"
    dispatcher_source.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import sys",
                f"sentinel = Path({str(activation_sentinel)!r})",
                "if 'activate' in sys.argv:",
                "    sentinel.write_text('activated', encoding='utf-8')",
                "    raise SystemExit(0)",
                "if 'mark-complete' in sys.argv:",
                "    raise SystemExit(23)",
                "raise SystemExit(0)",
                "",
            )
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        failing_writer = tmp_path / "completion-writer.cmd"
        failing_writer.write_text(
            f'@"{sys.executable}" "{dispatcher_source}" %*\r\n',
            encoding="utf-8",
        )
    else:
        failing_writer = tmp_path / "completion-writer"
        failing_writer.write_text(
            f"#!{sys.executable}\n"
            + dispatcher_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        failing_writer.chmod(0o755)

    result = _run_exemplar_mark_complete(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        runtime_version=version,
        python_executable=str(failing_writer),
        activation_sentinel=activation_sentinel,
    )

    assert result.returncode != 0
    assert not activation_sentinel.exists()
    assert not (slot / ".install-complete.json").exists()
    assert not (Path(layout["plugin_root"]) / "current-version").exists()
    assert not (Path(layout["plugin_root"]) / "last-known-good").exists()
    assert not (Path(layout["plugin_root"]) / "installation-activation.json").exists()


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_producer_evidence_is_accepted_without_activation_mutation(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _style = exemplar
    plugin_root = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (plugin_root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    (installed_plugin / ".payload-hash-dotfile").write_bytes(b"hidden payload\n")
    nested = installed_plugin / "payload-hash-fixtures"
    nested.mkdir()
    (nested / "snapshot-provenance.json").write_bytes(b"nested payload\n")
    (nested / "\u00e9.txt").write_bytes(b"unicode path\n")
    (nested / "line\nbreak.txt").write_bytes(b"newline path\n")
    (nested / "empty").mkdir()
    snapshot_root = Path(layout["snapshot_root"])
    (snapshot_root / "payload-content.txt").unlink()
    shutil.copytree(installed_plugin, snapshot_root, dirs_exist_ok=True)
    payload_bytes = _payload_file_bytes(installed_plugin)
    assert _payload_file_bytes(snapshot_root) == payload_bytes
    _stamp_with_python(layout, snapshot_id=version)
    legacy_before = _legacy_footprint_snapshot(
        installed_plugin,
        tmp_path / "home",
    )

    provisioned = _run_exemplar_slot_action(
        exemplar,
        "slot-provision",
        layout,
        tmp_path,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    result = json.loads(provisioned.stdout)
    plugin_root = Path(layout["plugin_root"])
    assert result["action"] == "slot-provision"
    assert Path(result["slotRoot"]) == plugin_root / "versions" / version
    assert result["activated"] is False
    assert result["operative"] is False
    assert not (tmp_path / "home" / f".{plugin_id}").exists()
    assert not (plugin_root / "current-version").exists()
    assert not (plugin_root / "last-known-good").exists()
    assert not (plugin_root / "installation-activation.json").exists()
    validated = _run_exemplar_slot_action(
        exemplar,
        "slot-validate",
        layout,
        tmp_path,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["reason"] == "runtime-slot-ownership-valid"
    plugin_state_before = {
        relative: _tree_snapshot(Path(layout["plugin_root"]) / relative)
        for relative in ("state", "run", "logs", "cache", "launchers")
    }
    produced = _run_exemplar_mark_complete(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        runtime_version=version,
    )
    assert produced.returncode == 0, produced.stderr
    build_path = (
        Path(layout["plugin_root"])
        / "versions"
        / version
        / ".install-complete.json"
    )
    build = json.loads(build_path.read_text(encoding="utf-8"))
    content_digest = _snapshot_content_digest(snapshot_root)
    assert build["payload_hash"] == content_digest
    assert set(build) == {"version", "completed_at", "pid", "payload_hash"}
    completed = _run_exemplar_slot_action(
        exemplar,
        "slot-complete",
        layout,
        tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    completion_result = json.loads(completed.stdout)
    assert completion_result["action"] == "slot-complete"
    assert completion_result["created"] is True
    assert completion_result["activated"] is False
    assert completion_result["operative"] is False
    completion_validated = _run_exemplar_slot_action(
        exemplar,
        "slot-completion-validate",
        layout,
        tmp_path,
    )
    assert completion_validated.returncode == 0, completion_validated.stderr
    assert (
        json.loads(completion_validated.stdout)["reason"]
        == "runtime-slot-completion-valid"
    )
    assert (
        _legacy_footprint_snapshot(installed_plugin, tmp_path / "home")
        == legacy_before
    )
    assert {
        relative: _tree_snapshot(Path(layout["plugin_root"]) / relative)
        for relative in ("state", "run", "logs", "cache", "launchers")
    } == plugin_state_before
    assert not (plugin_root / "current-version").exists()
    assert not (plugin_root / "last-known-good").exists()
    assert not (plugin_root / "installation-activation.json").exists()


@pytest.mark.parametrize(
    "exemplar",
    tuple(item for item in EXEMPLAR_INSTALLERS if item[0] == "agent-machines"),
    ids=lambda exemplar: exemplar[2],
)
def test_agent_machines_fixed_identity_cutover_adapter(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    plugin_id, _, style = exemplar
    source_plugin = LIB.parents[1] / "plugins" / plugin_id
    version = next(
        line.split('"')[1]
        for line in (source_plugin / "pyproject.toml").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    for egg_info in installed_plugin.rglob("*.egg-info"):
        shutil.rmtree(egg_info)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    snapshot_root = Path(layout["snapshot_root"])
    (snapshot_root / "payload-content.txt").unlink()
    shutil.copytree(installed_plugin, snapshot_root, dirs_exist_ok=True)
    stamp_runner = next(runner for runner in ALL_RUNNERS if runner[2] == style)
    _run(stamp_runner, "snapshot-stamp", layout, snapshot_id=version)
    provisioned = _run_exemplar_slot_action(
        exemplar,
        "slot-provision",
        layout,
        tmp_path,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    produced = _run_exemplar_mark_complete(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        runtime_version=version,
    )
    assert produced.returncode == 0, produced.stderr
    completed = _run_exemplar_slot_action(
        exemplar,
        "slot-complete",
        layout,
        tmp_path,
    )
    assert completed.returncode == 0, completed.stderr

    cutover = _run_exemplar_slot_action(
        exemplar,
        "slot-cutover",
        layout,
        tmp_path,
        expect_current_absent=True,
    )

    assert cutover.returncode == 0, cutover.stderr
    result = json.loads(cutover.stdout)
    assert result["action"] == "slot-cutover"
    assert result["activated"] is False
    plugin_root = Path(layout["plugin_root"])
    assert (
        (plugin_root / "current-version").read_text(encoding="utf-8").strip()
        == version
    )
    assert (
        (plugin_root / "last-known-good").read_text(encoding="utf-8").strip()
        == version
    )
    assert not (plugin_root / "installation-activation.json").exists()
    manifest_path = plugin_root / "deploy-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 4
    assert manifest["source"]["version"] == version
    assert Path(manifest["source"]["path"]).resolve() == installed_plugin.resolve()
    assert manifest["runtime"]["version"] == version
    assert Path(manifest["runtime"]["path"]).resolve() == (
        plugin_root / "versions" / version
    ).resolve()
    assert Path(manifest["runtime"]["selectedBy"]["path"]).resolve() == (
        installed_plugin.resolve()
    )

    for marker_name in ("current-version", "last-known-good"):
        (plugin_root / marker_name).write_text("2.0.0\n", encoding="utf-8")
    stale_manifest = json.loads(json.dumps(manifest))
    payload_v2 = tmp_path / "payload-v2"
    stale_manifest["source"] = {
        **manifest["source"],
        "version": "2.0.0",
        "path": str(payload_v2),
    }
    stale_manifest["runtime"] = {
        **manifest["runtime"],
        "version": "2.0.0",
        "path": str(plugin_root / "versions" / "2.0.0"),
        "interpreter": str(
            plugin_root
            / "versions"
            / "2.0.0"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        ),
        "selectedBy": {
            **manifest["runtime"]["selectedBy"],
            "version": "2.0.0",
            "path": str(payload_v2),
        },
    }
    _write_json(manifest_path, stale_manifest)

    rollback = _run_exemplar_slot_action(
        exemplar,
        "slot-cutover",
        layout,
        tmp_path,
        expected_current_version="2.0.0",
    )

    assert rollback.returncode == 0, rollback.stderr
    rollback_result = json.loads(rollback.stdout)
    assert rollback_result["currentVersion"] == version
    rolled_back_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert rolled_back_manifest["source"]["version"] == "2.0.0"
    assert (
        Path(rolled_back_manifest["source"]["path"]).resolve()
        == payload_v2.resolve()
    )
    assert rolled_back_manifest["runtime"]["version"] == version
    assert Path(rolled_back_manifest["runtime"]["path"]).resolve() == (
        plugin_root / "versions" / version
    ).resolve()
    assert Path(
        rolled_back_manifest["runtime"]["selectedBy"]["path"]
    ).resolve() == installed_plugin.resolve()

    manifest_before_failed_cas = manifest_path.read_bytes()
    failed_cas = _run_exemplar_slot_action(
        exemplar,
        "slot-cutover",
        layout,
        tmp_path,
        expected_current_version="9.9.9",
    )

    assert failed_cas.returncode == 0, failed_cas.stderr
    assert json.loads(failed_cas.stdout)["status"] == "revalidation-required"
    assert manifest_path.read_bytes() == manifest_before_failed_cas


@pytest.mark.parametrize(
    "exemplar",
    BEHAVIOR_EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
@pytest.mark.parametrize(
    ("entry_kind", "message"),
    (("link", "symbolic links"), ("non-regular", "ordinary files or directories")),
)
def test_exemplar_producer_hash_failure_does_not_omit_payload_hash(
    exemplar: tuple[str, tuple[str, ...], str],
    entry_kind: str,
    message: str,
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _ = exemplar
    plugin_root = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (plugin_root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    (
        Path(layout["plugin_root"])
        / "versions"
        / version
    ).mkdir(parents=True)
    if entry_kind == "link":
        target = tmp_path / "outside-payload.txt"
        target.write_bytes(b"outside\n")
        try:
            (installed_plugin / "!linked-payload").symlink_to(target)
        except OSError as error:
            pytest.skip(f"file symlinks are unavailable: {error}")
    else:
        if os.name == "nt":
            pytest.skip("non-regular filesystem payload entries require POSIX")
        os.mkfifo(installed_plugin / "!payload-pipe")

    produced = _run_exemplar_mark_complete(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        runtime_version=version,
    )

    assert produced.returncode != 0
    assert message in produced.stderr.lower()
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / version
        / ".install-complete.json"
    ).exists()


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
@pytest.mark.parametrize("mutation", ("add", "remove", "rename"))
def test_exemplar_payload_hash_rejects_tree_membership_change(
    exemplar: tuple[str, tuple[str, ...], str],
    mutation: str,
    tmp_path: Path,
) -> None:
    plugin_id, prefix, style = exemplar
    plugin_root = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (plugin_root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    slot = Path(layout["plugin_root"]) / "versions" / version
    slot.mkdir(parents=True)
    target = installed_plugin / "plugin.json"
    environment_overrides: dict[str, str] | None = None
    powershell_preamble = ""
    if style == "long":
        real_find = shutil.which("find")
        assert real_find is not None
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        count_path = tmp_path / "find-count"
        if mutation == "add":
            mutation_command = (
                f"printf 'added\\n' >{shlex.quote(str(installed_plugin / 'added.txt'))}"
            )
        elif mutation == "remove":
            mutation_command = f"rm -f -- {shlex.quote(str(target))}"
        else:
            mutation_command = (
                f"mv -- {shlex.quote(str(target))} "
                f"{shlex.quote(str(installed_plugin / 'renamed-plugin.json'))}"
            )
        fake_find = fake_bin / "find"
        fake_find.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f"count_file={shlex.quote(str(count_path))}",
                    "count=0",
                    '[[ ! -f "$count_file" ]] || count="$(cat "$count_file")"',
                    "count=$((count + 1))",
                    'printf "%s\\n" "$count" >"$count_file"',
                    f"if [[ \"$count\" -eq 2 ]]; then {mutation_command}; fi",
                    f"exec {shlex.quote(real_find)} \"$@\"",
                    "",
                )
            ),
            encoding="utf-8",
        )
        fake_find.chmod(0o755)
        environment_overrides = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        }
    else:
        payload_entries = list(installed_plugin.rglob("*"))
        directory_count = 1 + sum(1 for path in payload_entries if path.is_dir())
        hashed_file_count = sum(
            1
            for path in payload_entries
            if path.is_file()
            and path.relative_to(installed_plugin).as_posix()
            != "snapshot-provenance.json"
        )
        get_item_count_before_second_scan = (
            1
            + directory_count
            + len(payload_entries)
            + (2 * hashed_file_count)
        )
        target_ps = str(target).replace("'", "''")
        added_ps = str(installed_plugin / "added.txt").replace("'", "''")
        renamed_ps = str(
            installed_plugin / "renamed-plugin.json"
        ).replace("'", "''")
        if mutation == "add":
            mutation_statement = (
                f"[IO.File]::WriteAllText('{added_ps}', 'added')"
            )
        elif mutation == "remove":
            mutation_statement = f"[IO.File]::Delete('{target_ps}')"
        else:
            mutation_statement = (
                f"[IO.File]::Move('{target_ps}', '{renamed_ps}')"
            )
        powershell_preamble = "\n".join(
            (
                "$script:PayloadGetItemCount = 0",
                (
                    "$script:PayloadMutationAt = "
                    f"{get_item_count_before_second_scan + 1}"
                ),
                "function Get-Item {",
                "    param([string]$LiteralPath, [switch]$Force, $ErrorAction)",
                "    $script:PayloadGetItemCount++",
                "    if ($script:PayloadGetItemCount -eq $script:PayloadMutationAt) {",
                f"        {mutation_statement}",
                "    }",
                "    Microsoft.PowerShell.Management\\Get-Item "
                "-LiteralPath $LiteralPath -Force -ErrorAction Stop",
                "}",
            )
        )

    result = _run_exemplar_mark_complete(
        exemplar,
        installed_plugin,
        layout,
        tmp_path,
        runtime_version=version,
        environment_overrides=environment_overrides,
        powershell_preamble=powershell_preamble,
    )

    assert result.returncode != 0
    assert "tree changed during hashing" in result.stderr.lower()
    assert not (slot / ".install-complete.json").exists()


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_payload_hash_limit_boundaries_are_inclusive(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "a").write_bytes(b"x")

    accepted = _run_exemplar_payload_hash(
        exemplar,
        payload,
        tmp_path,
        max_entries=1,
        max_path_bytes=1,
        max_content_bytes=1,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert len(accepted.stdout.strip()) == 64

    for limit, message in (
        ({"max_entries": 0, "max_path_bytes": 1, "max_content_bytes": 1}, "entry limit"),
        ({"max_entries": 1, "max_path_bytes": 0, "max_content_bytes": 1}, "utf-8 limit"),
        (
            {"max_entries": 1, "max_path_bytes": 1, "max_content_bytes": 0},
            "regular-file limit",
        ),
    ):
        rejected = _run_exemplar_payload_hash(
            exemplar,
            payload,
            tmp_path / message.replace(" ", "-"),
            **limit,
        )
        assert rejected.returncode != 0
        assert message in rejected.stderr.lower()


@pytest.mark.parametrize(
    "exemplar",
    tuple(exemplar for exemplar in EXEMPLAR_INSTALLERS if exemplar[2] == "powershell"),
    ids=lambda exemplar: exemplar[0],
)
def test_powershell_exemplar_wide_payload_rejects_small_limit(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    for index in range(256):
        (payload / f"{index:03d}").write_bytes(b"x")

    result = _run_exemplar_payload_hash(
        exemplar,
        payload,
        tmp_path,
        max_entries=3,
        max_path_bytes=3,
        max_content_bytes=256,
    )

    assert result.returncode != 0
    assert "3-entry limit" in result.stderr.lower()


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
@pytest.mark.parametrize(
    "action",
    ("slot-provision", "slot-validate", "slot-complete", "slot-completion-validate"),
)
def test_exemplar_slot_actions_release_installed_payload_cwd_when_prestaged(
    exemplar: tuple[str, tuple[str, ...], str],
    action: str,
    tmp_path: Path,
) -> None:
    _, _, style = exemplar
    _, installed_plugin, home = _installed_exemplar(exemplar, tmp_path)
    runner = installed_plugin / "scripts" / "installation-context"
    if style == "powershell":
        probe = runner / "installation-context.ps1"
        probe.write_text(
            """param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Action,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedPayloadRoot,
    [string]$ExpectedPayloadVersion,
    [string]$SnapshotId,
    [string]$RuntimeVersion,
    [string]$DurableHome
)
@{
    provider = (Get-Location).Path
    process = [IO.Directory]::GetCurrentDirectory()
} | ConvertTo-Json -Compress
""",
            encoding="utf-8",
        )
    else:
        probe = runner / "installation-context.sh"
        probe.write_text(
            '#!/usr/bin/env bash\nprintf \'{"cwd":"%s"}\\n\' "$PWD"\n',
            encoding="utf-8",
        )
        probe.chmod(0o755)
    layout = _receipt_layout(tmp_path)

    result = _run_exemplar_slot_action(
        exemplar,
        action,
        layout,
        tmp_path,
        environment_overrides={"COPILOT_PLUGIN_INSTALL_STAGED": "1"},
    )

    assert result.returncode == 0, result.stderr
    cwd = json.loads(result.stdout)
    if style == "powershell":
        assert Path(cwd["provider"]) == home
        assert Path(cwd["process"]) == home
    else:
        assert Path(cwd["cwd"]) == home


@pytest.mark.parametrize(
    "exemplar",
    SECURITY_EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
@pytest.mark.parametrize(
    "action",
    ("slot-provision", "slot-validate", "slot-complete", "slot-completion-validate"),
)
def test_exemplar_slot_actions_do_not_adopt_ambient_context(
    exemplar: tuple[str, tuple[str, ...], str],
    action: str,
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _ = exemplar
    plugin_root = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (plugin_root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=installed_plugin,
    )
    _stamp_with_python(layout, snapshot_id=version)

    result = _run_exemplar_slot_action(
        exemplar,
        action,
        layout,
        tmp_path,
        include_context=False,
        environment_overrides={
            "COPILOT_EXTENSIONS_CONTEXT": str(layout["install"]),
        },
    )

    assert result.returncode == 2
    assert "ambient COPILOT_EXTENSIONS_CONTEXT is not authorization" in (
        result.stdout + result.stderr
    )
    assert not (Path(layout["plugin_root"]) / "versions").exists()


@pytest.mark.parametrize(
    "exemplar",
    SECURITY_EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
@pytest.mark.parametrize("mismatch", ("root", "version"))
def test_exemplar_slot_actions_reject_foreign_snapshot_payload(
    exemplar: tuple[str, tuple[str, ...], str],
    mismatch: str,
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _ = exemplar
    source_plugin = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (source_plugin / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("version = ")
    )
    _, installed_plugin, _ = _installed_exemplar(exemplar, tmp_path)
    foreign_payload = tmp_path / "foreign-payload"
    foreign_payload.mkdir()
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version="9.9.9" if mismatch == "version" else version,
        snapshot_id=version,
        payload_root=foreign_payload if mismatch == "root" else installed_plugin,
    )
    _stamp_with_python(layout, snapshot_id=version)

    result = _run_exemplar_slot_action(
        exemplar,
        "slot-provision",
        layout,
        tmp_path,
    )

    assert result.returncode != 0
    assert f"Expected snapshot payload {mismatch}" in result.stderr
    assert not (Path(layout["plugin_root"]) / "versions").exists()


@pytest.mark.parametrize(
    "exemplar",
    SECURITY_EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_slot_actions_reject_spoofed_staging_payload_identity(
    exemplar: tuple[str, tuple[str, ...], str],
    tmp_path: Path,
) -> None:
    plugin_id, prefix, _ = exemplar
    source_plugin = Path(prefix[-1]).parents[1]
    version = next(
        line.split('"')[1]
        for line in (source_plugin / "pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("version = ")
    )
    _, _, _ = _installed_exemplar(exemplar, tmp_path)
    foreign_payload = tmp_path / "foreign-payload"
    foreign_payload.mkdir()
    layout = _receipt_layout(
        tmp_path,
        plugin_id=plugin_id,
        payload_version=version,
        snapshot_id=version,
        payload_root=foreign_payload,
    )
    _stamp_with_python(layout, snapshot_id=version)

    result = _run_exemplar_slot_action(
        exemplar,
        "slot-provision",
        layout,
        tmp_path,
        environment_overrides={
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
            "COPILOT_PLUGIN_STAGED_FROM": str(foreign_payload),
        },
    )

    assert result.returncode != 0
    assert "Expected snapshot payload root" in result.stderr
    assert not (Path(layout["plugin_root"]) / "versions").exists()


@pytest.mark.parametrize("runner", ADAPTER_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.installation_context_smoke
def test_snapshot_stamp_and_validate_are_idempotent_and_cell_local(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    first = json.loads(_run(runner, "snapshot-stamp", layout).stdout)
    provenance = _provenance_path(layout)
    assert first["action"] == "snapshot-stamp"
    assert first["status"] == "ready"
    assert first["reason"] == "snapshot-provenance-published"
    assert first["snapshotChanged"] is True
    assert first["operative"] is False
    assert Path(first["provenance"]) == provenance.resolve()
    assert provenance.parent.parent == Path(layout["snapshots"])
    assert provenance.read_bytes().endswith(b"\n")
    assert not provenance.read_bytes().startswith(b"\xef\xbb\xbf")

    second = json.loads(_run(runner, "snapshot-stamp", layout).stdout)
    assert second["reason"] == "snapshot-provenance-current"
    assert second["snapshotChanged"] is False
    validated = json.loads(_run(runner, "snapshot-validate", layout).stdout)
    assert validated["action"] == "snapshot-validate"
    assert validated["sourceFingerprint"].startswith("sha256:")
    assert validated["namespaceGeneration"] == 1
    assert validated["installGeneration"] == 2
    assert validated["payload"]["originReceipt"] is None


def test_importable_python_snapshot_api_matches_cli(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    stamped = module.stamp_snapshot_provenance(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        expected_namespace_generation=1,
        expected_install_generation=2,
        snapshot_id="1.0.0",
        durable_home=layout["durable"],
        environment={},
    )
    assert stamped["snapshotChanged"] is True
    validated = module.validate_snapshot_provenance(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        durable_home=layout["durable"],
        environment={},
    )
    assert validated["provenance"] == stamped["provenance"]


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_actions_publish_validate_and_reuse_without_activation(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot = json.loads(_run(runner, "snapshot-stamp", layout).stdout)
    plugin_root = Path(layout["plugin_root"])
    activation_paths = (
        plugin_root / "current-version",
        plugin_root / "last-known-good",
        plugin_root / "installation-activation.json",
    )

    first = json.loads(_run_slot(runner, "slot-provision", layout).stdout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))

    assert first["slotChanged"] is True
    assert first["activated"] is False
    assert first["operative"] is False
    assert first["slotEmpty"] is True
    assert first["namespaceState"] == "active"
    assert first["installState"] == "active"
    assert ownership == {
        "schema": "copilot-extensions.runtime-slot-ownership",
        "version": 1,
        "marketplaceId": layout["marketplace_id"],
        "pluginId": layout["plugin_id"],
        "sourceFingerprint": snapshot["sourceFingerprint"],
        "runtime": {
            "version": "3.4.5",
            "root": str(plugin_root / "versions" / "3.4.5"),
        },
        "snapshot": {
            "id": "1.0.0",
            "root": snapshot["snapshotRoot"],
            "provenance": snapshot["provenance"],
            "provenanceSha256": hashlib.sha256(
                Path(snapshot["provenance"]).read_bytes()
            ).hexdigest(),
        },
        "namespaceReceipt": {
            "path": snapshot["namespaceReceipt"],
            "generation": 1,
        },
        "installReceipt": {
            "path": snapshot["installReceipt"],
            "generation": 2,
        },
        "createdAt": ownership["createdAt"],
    }
    marker_bytes = marker.read_bytes()
    (Path(first["slotRoot"]) / "payload.txt").write_text(
        "built later\n",
        encoding="utf-8",
    )

    validated = json.loads(_run_slot(runner, "slot-validate", layout).stdout)
    reused = json.loads(_run_slot(runner, "slot-provision", layout).stdout)

    assert validated["reason"] == "runtime-slot-ownership-valid"
    assert validated["slotEmpty"] is False
    assert reused["reason"] == "runtime-slot-ownership-current"
    assert reused["slotChanged"] is False
    assert marker.read_bytes() == marker_bytes
    assert all(not path.exists() for path in activation_paths)


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_new_publication_requires_current_snapshot(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 3
    _write_json(install_path, install)

    result = _run_slot(runner, "slot-provision", layout, check=False)

    assert result.returncode != 0
    assert "Snapshot provenance install generation is stale" in result.stderr
    assert not (Path(layout["plugin_root"]) / "versions").exists()


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
def test_owned_runtime_slot_survives_receipt_advance_and_rejects_regression(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)
    first = json.loads(_run_slot(runner, "slot-provision", layout).stdout)
    namespace_path = Path(layout["namespace"])
    namespace = json.loads(namespace_path.read_text(encoding="utf-8"))
    namespace["generation"] = 2
    namespace["state"] = "inactive"
    _write_json(namespace_path, namespace)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 3
    install["state"] = "inactive"
    install["payload"]["version"] = "2.0.0"
    _write_json(install_path, install)

    validated = json.loads(_run_slot(runner, "slot-validate", layout).stdout)
    reused = json.loads(_run_slot(runner, "slot-provision", layout).stdout)

    assert validated["namespaceGeneration"] == 1
    assert validated["installGeneration"] == 2
    assert validated["namespaceState"] == "inactive"
    assert validated["installState"] == "inactive"
    assert reused["slotChanged"] is False
    assert reused["ownership"] == first["ownership"]

    install["generation"] = 1
    _write_json(install_path, install)
    rejected = _run_slot(runner, "slot-validate", layout, check=False)
    assert rejected.returncode != 0
    assert "Current receipt generation predates the owned runtime slot" in rejected.stderr


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("marker_kind", ["missing", "malformed"])
def test_runtime_slot_preserves_markerless_or_malformed_existing_slot(
    runner: Runner,
    marker_kind: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)
    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"
    slot.mkdir(parents=True)
    marker = slot / ".runtime-slot-ownership.json"
    if marker_kind == "malformed":
        marker.write_text('{"schema":"other","version":1}\n', encoding="utf-8")
    payload = slot / "existing.txt"
    payload.write_text("preserve me\n", encoding="utf-8")
    before = _tree_snapshot(slot)

    result = _run_slot(runner, "slot-provision", layout, check=False)

    assert result.returncode != 0
    expected = "Runtime slot ownership must exist"
    if marker_kind == "malformed":
        expected = (
            "unsupported schema or version"
            if runner[0] == "python"
            else "unknown or missing fields"
        )
    assert expected in result.stderr
    assert _tree_snapshot(slot) == before


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_rejects_copied_cross_plugin_ownership(
    runner: Runner,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, plugin_id="agent-source")
    target_layout = _receipt_layout(tmp_path, plugin_id="agent-target")
    _stamp_with_python(source_layout)
    _stamp_with_python(target_layout)
    source = _provision_slot_with_python(source_layout)
    target_slot = Path(target_layout["plugin_root"]) / "versions" / "3.4.5"
    target_slot.mkdir(parents=True)
    copied_marker = target_slot / ".runtime-slot-ownership.json"
    shutil.copyfile(source["ownership"], copied_marker)
    before = copied_marker.read_bytes()

    result = _run_slot(runner, "slot-provision", target_layout, check=False)

    assert result.returncode != 0
    assert "does not match the validated snapshot" in result.stderr
    assert copied_marker.read_bytes() == before


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("malformed_generation", [True, 1.0, "1"])
def test_runtime_slot_validation_rejects_noninteger_ownership_generations(
    runner: Runner,
    malformed_generation: object,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))
    ownership["namespaceReceipt"]["generation"] = malformed_generation
    _write_json(marker, ownership)

    result = _run_slot(runner, "slot-validate", layout, check=False)

    assert result.returncode != 0
    assert "runtime slot ownership namespace generation" in result.stderr.lower()


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_validation_rejects_unknown_ownership_fields(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))
    ownership["unexpected"] = "value"
    _write_json(marker, ownership)

    result = _run_slot(runner, "slot-validate", layout, check=False)

    assert result.returncode != 0
    expected = (
        "does not match the validated snapshot"
        if runner[0] == "python"
        else "unknown or missing fields"
    )
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("producer", "consumer"),
    INTEROPERABILITY_PAIRS,
)
def test_runtime_slot_ownership_interoperates_across_runners(
    producer: Runner,
    consumer: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)

    published = json.loads(_run_slot(producer, "slot-provision", layout).stdout)
    validated = json.loads(_run_slot(consumer, "slot-validate", layout).stdout)

    assert validated["ownership"] == published["ownership"]
    assert validated["reason"] == "runtime-slot-ownership-valid"


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_rejects_snapshot_provenance_tampering(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    _run_slot(runner, "slot-provision", layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["payload"]["version"] = "9.9.9"
    _write_json(provenance_path, provenance)

    result = _run_slot(runner, "slot-validate", layout, check=False)

    assert result.returncode != 0
    assert "does not match the validated snapshot" in result.stderr


@pytest.mark.skipif(BASH is None, reason="Bash is unavailable")
def test_posix_slot_publication_failure_releases_owned_empty_reservation(
    tmp_path: Path,
) -> None:
    posix = next(runner for runner in ALL_RUNNERS if runner[0] == "posix")
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_ln = fake_bin / "ln"
    fake_ln.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_ln.chmod(fake_ln.stat().st_mode | stat.S_IXUSR)
    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"

    failed = _run_slot(
        posix,
        "slot-provision",
        layout,
        environment_overrides={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )

    assert failed.returncode != 0
    assert "Cannot publish runtime slot ownership" in failed.stderr
    assert not slot.exists()
    assert json.loads(_run_slot(posix, "slot-provision", layout).stdout)["slotChanged"]


@pytest.mark.skipif(BASH is None, reason="Bash is unavailable")
def test_posix_slot_digest_failure_releases_owned_empty_reservation(
    tmp_path: Path,
) -> None:
    posix = next(runner for runner in ALL_RUNNERS if runner[0] == "posix")
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    real_sha256sum = shutil.which("sha256sum")
    assert real_sha256sum is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_sha256sum = fake_bin / "sha256sum"
    fake_sha256sum.write_text(
        "#!/bin/sh\n"
        'capture="${TMPDIR:-/tmp}/fake-sha256sum.$$"\n'
        'trap \'rm -f "$capture"\' EXIT\n'
        'cat >"$capture"\n'
        "if grep -q 'copilot-extensions.snapshot-provenance' \"$capture\"; then\n"
        "  exit 1\n"
        "fi\n"
        f'exec "{real_sha256sum}" <"$capture"\n',
        encoding="utf-8",
    )
    fake_sha256sum.chmod(fake_sha256sum.stat().st_mode | stat.S_IXUSR)
    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"

    failed = _run_slot(
        posix,
        "slot-provision",
        layout,
        environment_overrides={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )

    assert failed.returncode != 0
    assert not slot.exists()
    assert json.loads(_run_slot(posix, "slot-provision", layout).stdout)["slotChanged"]


@pytest.mark.parametrize("runner", ADAPTER_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_validation_uses_canonical_path_equality(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))
    slot = Path(first["slotRoot"])
    ownership["runtime"]["root"] = str(slot.parent / ".." / "versions" / slot.name)
    _write_json(marker, ownership)

    result = json.loads(_run_slot(runner, "slot-validate", layout).stdout)

    assert result["reason"] == "runtime-slot-ownership-valid"


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_supports_nested_versions_root(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["roots"]["versions"] = "runtime/versions"
    _write_json(install_path, install)
    _run(runner, "snapshot-stamp", layout)

    result = json.loads(_run_slot(runner, "slot-provision", layout).stdout)

    assert Path(result["slotRoot"]) == (
        Path(layout["plugin_root"]) / "runtime" / "versions" / "3.4.5"
    )


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("file_component", ["versions", "runtime"])
def test_runtime_slot_rejects_file_in_versions_root_chain(
    runner: Runner,
    file_component: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    if file_component == "runtime":
        install["roots"]["versions"] = "runtime/versions"
        _write_json(install_path, install)
    (Path(layout["plugin_root"]) / file_component).write_text(
        "not a directory\n",
        encoding="utf-8",
    )
    _run(runner, "snapshot-stamp", layout)

    result = _run_slot(runner, "slot-provision", layout, check=False)

    assert result.returncode != 0
    assert "ordinary directories" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
@pytest.mark.parametrize("runner", ADAPTER_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("linked_path", ["versions", "slot"])
def test_runtime_slot_rejects_linked_path_components(
    runner: Runner,
    linked_path: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    plugin_root = Path(layout["plugin_root"])
    target = tmp_path / f"outside-{linked_path}"
    target.mkdir()
    _run(runner, "snapshot-stamp", layout)
    if linked_path == "versions":
        (plugin_root / "versions").symlink_to(target, target_is_directory=True)
    else:
        versions = plugin_root / "versions"
        versions.mkdir()
        (versions / "3.4.5").symlink_to(target, target_is_directory=True)

    result = _run_slot(runner, "slot-provision", layout, check=False)

    assert result.returncode != 0
    assert not (target / ".runtime-slot-ownership.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_rejects_linked_ownership_marker(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"
    slot.mkdir(parents=True)
    target = tmp_path / "outside-ownership.json"
    target.write_text("{}\n", encoding="utf-8")
    (slot / ".runtime-slot-ownership.json").symlink_to(target)

    result = _run_slot(runner, "slot-provision", layout, check=False)

    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize("runner", EXHAUSTIVE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "runtime_version",
    [
        "../escape",
        "/absolute",
        r"C:\escape",
        ".",
        "..",
        "CON",
        ".hidden",
        "trailing.",
        "a" * 129,
    ],
)
def test_runtime_slot_rejects_nonportable_runtime_versions(
    runner: Runner,
    runtime_version: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)

    result = _run_slot(
        runner,
        "slot-provision",
        layout,
        runtime_version=runtime_version,
        check=False,
    )

    assert result.returncode != 0
    assert "runtime version" in result.stderr.lower()


@pytest.mark.parametrize("runner", ADAPTER_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_serializes_concurrent_publishers(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda _: _run_slot(
                    runner,
                    "slot-provision",
                    layout,
                    direct=True,
                ),
                range(2),
            )
        )
    results = [json.loads(result.stdout) for result in completed]

    assert sorted(result["slotChanged"] for result in results) == [False, True]
    assert len({result["ownership"] for result in results}) == 1
    assert Path(results[0]["ownership"]).is_file()


def test_python_api_provisions_and_reuses_nonactivating_owned_runtime_slot(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot = _stamp_with_python(layout)
    expected_root = Path(layout["payload"])
    plugin_root = Path(layout["plugin_root"])
    activation_paths = (
        plugin_root / "current-version",
        plugin_root / "last-known-good",
        plugin_root / "installation-activation.json",
    )

    first = _provision_slot_with_python(
        layout,
        expected_payload_root=expected_root,
        expected_payload_version="1.0.0",
    )
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))

    assert first["slotChanged"] is True
    assert first["activated"] is False
    assert first["operative"] is False
    assert first["slotEmpty"] is True
    assert first["namespaceState"] == "active"
    assert first["installState"] == "active"
    assert Path(first["slotRoot"]) == plugin_root / "versions" / "3.4.5"
    assert ownership == {
        "schema": "copilot-extensions.runtime-slot-ownership",
        "version": 1,
        "marketplaceId": layout["marketplace_id"],
        "pluginId": layout["plugin_id"],
        "sourceFingerprint": snapshot["sourceFingerprint"],
        "runtime": {
            "version": "3.4.5",
            "root": str(plugin_root / "versions" / "3.4.5"),
        },
        "snapshot": {
            "id": "1.0.0",
            "root": snapshot["snapshotRoot"],
            "provenance": snapshot["provenance"],
            "provenanceSha256": hashlib.sha256(
                Path(snapshot["provenance"]).read_bytes()
            ).hexdigest(),
        },
        "namespaceReceipt": {
            "path": snapshot["namespaceReceipt"],
            "generation": 1,
        },
        "installReceipt": {
            "path": snapshot["installReceipt"],
            "generation": 2,
        },
        "createdAt": ownership["createdAt"],
    }
    assert all(not path.exists() for path in activation_paths)
    marker_bytes = marker.read_bytes()
    (Path(first["slotRoot"]) / "payload.txt").write_text(
        "built later\n",
        encoding="utf-8",
    )

    second = _provision_slot_with_python(
        layout,
        expected_payload_root=expected_root,
        expected_payload_version="1.0.0",
    )
    module = _load_python_module()
    validated = module.validate_runtime_slot_ownership(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        runtime_version="3.4.5",
        expected_payload_root=expected_root,
        expected_payload_version="1.0.0",
        durable_home=layout["durable"],
        environment={},
    )

    assert second["slotChanged"] is False
    assert second["slotEmpty"] is False
    assert second["ownership"] == str(marker)
    assert validated["reason"] == "runtime-slot-ownership-valid"
    assert marker.read_bytes() == marker_bytes
    assert all(not path.exists() for path in activation_paths)

    foreign_root = tmp_path / "foreign-payload"
    foreign_root.mkdir()
    with pytest.raises(
        module.InstallationContextError,
        match="Expected snapshot payload root",
    ):
        _provision_slot_with_python(
            layout,
            expected_payload_root=foreign_root,
            expected_payload_version="1.0.0",
            module=module,
        )
    with pytest.raises(
        module.InstallationContextError,
        match="Expected snapshot payload version",
    ):
        module.validate_runtime_slot_ownership(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            expected_payload_root=expected_root,
            expected_payload_version="9.9.9",
            durable_home=layout["durable"],
            environment={},
        )


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.installation_context_smoke
def test_runtime_slot_actions_bind_expected_snapshot_payload_identity(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    expected_root = Path(layout["payload"])

    if not EXHAUSTIVE_ADAPTERS:
        foreign_root = tmp_path / "foreign-payload"
        foreign_root.mkdir()
        wrong_root = _run_slot(
            runner,
            "slot-provision",
            layout,
            expected_payload_root=foreign_root,
            expected_payload_version="1.0.0",
            check=False,
        )
        assert wrong_root.returncode != 0
        assert "Expected snapshot payload root" in wrong_root.stderr
        versions = Path(layout["plugin_root"]) / "versions"
        assert not versions.exists()

        _provision_slot_with_python(
            layout,
            expected_payload_root=expected_root,
            expected_payload_version="1.0.0",
        )
        accepted = json.loads(
            _run_slot(
                runner,
                "slot-provision",
                layout,
                expected_payload_root=expected_root,
                expected_payload_version="1.0.0",
            ).stdout
        )
        assert accepted["slotChanged"] is False
        versions_before = _tree_snapshot(versions)
        wrong_version = _run_slot(
            runner,
            "slot-validate",
            layout,
            expected_payload_root=expected_root,
            expected_payload_version="9.9.9",
            check=False,
        )
        assert wrong_version.returncode != 0
        assert "Expected snapshot payload version" in wrong_version.stderr
        assert _tree_snapshot(versions) == versions_before
        return

    result = json.loads(
        _run_slot(
            runner,
            "slot-provision",
            layout,
            expected_payload_root=expected_root,
            expected_payload_version="1.0.0",
        ).stdout
    )
    assert result["slotChanged"] is True
    reused = json.loads(
        _run_slot(
            runner,
            "slot-provision",
            layout,
            expected_payload_root=expected_root,
            expected_payload_version="1.0.0",
        ).stdout
    )
    assert reused["slotChanged"] is False

    foreign_root = tmp_path / "foreign-payload"
    foreign_root.mkdir()
    wrong_root = _run_slot(
        runner,
        "slot-provision",
        layout,
        expected_payload_root=foreign_root,
        expected_payload_version="1.0.0",
        check=False,
    )
    assert wrong_root.returncode != 0
    assert "Expected snapshot payload root" in wrong_root.stderr

    wrong_version = _run_slot(
        runner,
        "slot-validate",
        layout,
        expected_payload_root=expected_root,
        expected_payload_version="9.9.9",
        check=False,
    )
    assert wrong_version.returncode != 0
    assert "Expected snapshot payload version" in wrong_version.stderr


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("flag_name", "message"),
    (
        ("expected-payload-root", "Expected snapshot payload root must be absolute"),
        (
            "expected-payload-version",
            "Expected snapshot payload version must be a non-empty string",
        ),
    ),
)
@pytest.mark.parametrize("empty_value", ("", "   "), ids=("empty", "whitespace"))
def test_slot_actions_reject_explicit_empty_payload_expectations(
    runner: Runner,
    flag_name: str,
    message: str,
    empty_value: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    _, _prefix, style = runner
    command = _command(runner, "slot-provision", layout)
    command.extend([_flag(style, flag_name), empty_value])
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "flag_name",
    ("expected-payload-root", "expected-payload-version"),
)
def test_non_slot_actions_reject_payload_expectation(
    runner: Runner,
    flag_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    _, _, style = runner
    command = _command(runner, "snapshot-validate", layout)
    command.extend([_flag(style, flag_name), "1.0.0"])
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )

    assert result.returncode != 0


def test_python_slot_provision_rejects_stale_snapshot_before_creating_slot(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 3
    _write_json(install_path, install)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Snapshot provenance install generation is stale",
    ):
        _provision_slot_with_python(layout, module=module)

    assert not (Path(layout["plugin_root"]) / "versions").exists()


def test_python_owned_slot_remains_valid_after_receipt_generation_advances(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 3
    install["state"] = "inactive"
    install["payload"]["version"] = "2.0.0"
    _write_json(install_path, install)
    module = _load_python_module()

    validated = module.validate_runtime_slot_ownership(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        runtime_version="3.4.5",
        durable_home=layout["durable"],
        environment={},
    )
    reused = _provision_slot_with_python(layout, module=module)

    assert validated["installGeneration"] == 2
    assert validated["installState"] == "inactive"
    assert reused["slotChanged"] is False
    assert reused["ownership"] == first["ownership"]


def test_python_owned_slot_rejects_receipt_generation_regression(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    _provision_slot_with_python(layout)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 1
    _write_json(install_path, install)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Current receipt generation predates the owned runtime slot",
    ):
        module.validate_runtime_slot_ownership(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )


def test_python_slot_provision_preserves_conflicting_existing_slot(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"
    slot.mkdir(parents=True)
    payload = slot / "existing.txt"
    payload.write_text("preserve me\n", encoding="utf-8")
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Runtime slot ownership must exist",
    ):
        _provision_slot_with_python(layout, module=module)

    assert payload.read_text(encoding="utf-8") == "preserve me\n"
    assert not (slot / ".runtime-slot-ownership.json").exists()


def test_python_slot_provision_preserves_malformed_ownership(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    marker.write_text('{"schema":"other","version":1}\n', encoding="utf-8")
    before = marker.read_bytes()
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="unsupported schema or version",
    ):
        _provision_slot_with_python(layout, module=module)

    assert marker.read_bytes() == before


@pytest.mark.parametrize("malformed_generation", [True, 1.0, "1"])
def test_python_slot_validation_rejects_malformed_ownership_generation(
    tmp_path: Path,
    malformed_generation: object,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))
    ownership["namespaceReceipt"]["generation"] = malformed_generation
    _write_json(marker, ownership)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="runtime slot ownership namespace generation must be an integer",
    ):
        module.validate_runtime_slot_ownership(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )


def test_python_slot_validation_uses_canonical_path_equality(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    first = _provision_slot_with_python(layout)
    marker = Path(first["ownership"])
    ownership = json.loads(marker.read_text(encoding="utf-8"))
    slot = Path(first["slotRoot"])
    ownership["runtime"]["root"] = str(slot.parent / ".." / "versions" / slot.name)
    _write_json(marker, ownership)
    module = _load_python_module()

    validated = module.validate_runtime_slot_ownership(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        runtime_version="3.4.5",
        durable_home=layout["durable"],
        environment={},
    )

    assert validated["reason"] == "runtime-slot-ownership-valid"


def test_python_slot_provision_rejects_copied_cross_plugin_ownership(
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, plugin_id="agent-source")
    target_layout = _receipt_layout(tmp_path, plugin_id="agent-target")
    _stamp_with_python(source_layout)
    _stamp_with_python(target_layout)
    source = _provision_slot_with_python(source_layout)
    target_slot = Path(target_layout["plugin_root"]) / "versions" / "3.4.5"
    target_slot.mkdir(parents=True)
    copied_marker = target_slot / ".runtime-slot-ownership.json"
    shutil.copyfile(source["ownership"], copied_marker)
    before = copied_marker.read_bytes()
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="does not match the validated snapshot and installation receipts",
    ):
        _provision_slot_with_python(target_layout, module=module)

    assert copied_marker.read_bytes() == before


def test_python_slot_provision_never_replaces_a_racing_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    module = _load_python_module()
    original_publish = module._rename_directory_no_replace

    def publish_after_competitor(source: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "foreign.txt").write_text("preserve me\n", encoding="utf-8")
        original_publish(source, destination)

    monkeypatch.setattr(
        module,
        "_rename_directory_no_replace",
        publish_after_competitor,
    )

    with pytest.raises(
        module.InstallationContextError,
        match="appeared during publication",
    ):
        _provision_slot_with_python(layout, module=module)

    slot = Path(layout["plugin_root"]) / "versions" / "3.4.5"
    assert (slot / "foreign.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert not (slot / ".runtime-slot-ownership.json").exists()
    assert not list(slot.parent.parent.glob(".runtime-slot-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
def test_python_slot_provision_rejects_linked_slot(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    versions = Path(layout["plugin_root"]) / "versions"
    target = tmp_path / "outside-slot"
    target.mkdir()
    versions.mkdir()
    (versions / "3.4.5").symlink_to(target, target_is_directory=True)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Runtime slot may not be a symbolic link",
    ):
        _provision_slot_with_python(layout, module=module)

    assert not (target / ".runtime-slot-ownership.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
def test_python_slot_provision_rejects_linked_versions_root(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["roots"]["versions"] = "linked-versions"
    _write_json(install_path, install)
    target = Path(layout["plugin_root"]) / "real-versions"
    target.mkdir()
    (Path(layout["plugin_root"]) / "linked-versions").symlink_to(
        target,
        target_is_directory=True,
    )
    _stamp_with_python(layout)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Versions root may not traverse a symbolic link",
    ):
        _provision_slot_with_python(layout, module=module)

    assert not (target / "3.4.5").exists()


def test_python_slot_provision_supports_nested_versions_root(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["roots"]["versions"] = "runtime/versions"
    _write_json(install_path, install)
    _stamp_with_python(layout)

    result = _provision_slot_with_python(layout)

    assert Path(result["slotRoot"]) == (
        Path(layout["plugin_root"]) / "runtime" / "versions" / "3.4.5"
    )


@pytest.mark.parametrize("file_component", ["versions", "runtime"])
def test_python_slot_provision_rejects_file_in_versions_root_chain(
    tmp_path: Path,
    file_component: str,
) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    if file_component == "runtime":
        install["roots"]["versions"] = "runtime/versions"
        _write_json(install_path, install)
    (Path(layout["plugin_root"]) / file_component).write_text(
        "not a directory\n",
        encoding="utf-8",
    )
    _stamp_with_python(layout)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match="Versions root path components must be ordinary directories",
    ):
        _provision_slot_with_python(layout, module=module)


@pytest.mark.parametrize(
    "runtime_version",
    [
        "../escape",
        "/absolute",
        r"C:\escape",
        ".",
        "..",
        "CON",
        ".hidden",
        "trailing.",
        "a" * 129,
    ],
)
def test_python_slot_provision_rejects_nonportable_runtime_versions(
    tmp_path: Path,
    runtime_version: str,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    module = _load_python_module()

    with pytest.raises(
        module.InstallationContextError,
        match=r"[Rr]untime version",
    ):
        _provision_slot_with_python(
            layout,
            runtime_version=runtime_version,
            module=module,
        )


def test_python_slot_provision_serializes_concurrent_publishers(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    module = _load_python_module()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: _provision_slot_with_python(layout, module=module),
                range(2),
            )
        )

    assert sorted(result["slotChanged"] for result in results) == [False, True]
    assert len({result["ownership"] for result in results}) == 1
    assert Path(results[0]["ownership"]).is_file()


def test_python_cli_provisions_and_validates_runtime_slot(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    common = [
        "--context",
        str(layout["install"]),
        "--durable-home",
        str(layout["durable"]),
        "--expected-marketplace-id",
        str(layout["marketplace_id"]),
        "--expected-plugin-id",
        str(layout["plugin_id"]),
        "--snapshot-id",
        "1.0.0",
        "--runtime-version",
        "3.4.5",
    ]

    provision = subprocess.run(
        [sys.executable, str(PYTHON_SCRIPT), "slot-provision", *common],
        text=True,
        capture_output=True,
        check=False,
    )
    validate = subprocess.run(
        [sys.executable, str(PYTHON_SCRIPT), "slot-validate", *common],
        text=True,
        capture_output=True,
        check=False,
    )

    assert provision.returncode == 0, provision.stderr
    assert validate.returncode == 0, validate.stderr
    assert json.loads(provision.stdout)["slotChanged"] is True
    assert json.loads(validate.stdout)["reason"] == "runtime-slot-ownership-valid"


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_publishes_validates_and_replays_without_activation(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    plugin_root = Path(layout["plugin_root"])
    watched = (
        plugin_root / "current-version",
        plugin_root / "last-known-good",
        plugin_root / "installation-activation.json",
        plugin_root / "state",
        plugin_root / "run",
        plugin_root / "logs",
        plugin_root / "cache",
        plugin_root / "launchers",
    )
    before = _tree_snapshot(plugin_root)

    first = json.loads(_run_completion(runner, "slot-complete", layout).stdout)
    completion_path = Path(first["completion"])
    receipt = json.loads(completion_path.read_text(encoding="utf-8"))
    content_digest = _snapshot_content_digest(Path(layout["snapshot_root"]))

    assert first["created"] is True
    assert first["activated"] is False
    assert first["operative"] is False
    assert first["completedAt"] == "2026-01-02T03:04:05Z"
    assert first["payloadSha256"] == content_digest
    assert receipt == first["receipt"]
    assert receipt["schema"] == "copilot.extensions/runtime-slot-completion/v1"
    assert receipt["runtime"] == {
        "version": "3.4.5",
        "root": str(plugin_root / "versions" / "3.4.5"),
    }
    assert receipt["build"] == {
        "receipt": str(build_path),
        "receiptSha256": hashlib.sha256(build_path.read_bytes()).hexdigest(),
        "payloadSha256": content_digest,
        "pid": 123,
    }
    assert receipt["snapshot"]["contentSha256"] == content_digest
    assert receipt["completedAt"] == "2026-01-02T03:04:05Z"
    assert all(not path.exists() for path in watched)
    after = _tree_snapshot(plugin_root)
    changed = {
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    }
    assert changed == {"versions/3.4.5/.runtime-slot-completion.json"}

    marker_bytes = completion_path.read_bytes()
    validated = json.loads(
        _run_completion(runner, "slot-completion-validate", layout).stdout
    )
    replayed = json.loads(_run_completion(runner, "slot-complete", layout).stdout)

    assert validated["reason"] == "runtime-slot-completion-valid"
    assert validated["receipt"] == receipt
    assert replayed["reason"] == "runtime-slot-completion-current"
    assert replayed["created"] is False
    assert completion_path.read_bytes() == marker_bytes
    assert all(not path.exists() for path in watched)


@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_content_digest_is_exact_and_cross_runner_deterministic(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    (snapshot_root / ".hidden").write_bytes(b"hidden\n")
    nested = snapshot_root / "nested"
    nested.mkdir()
    (nested / "snapshot-provenance.json").write_bytes(b"nested sidecar name\n")
    (nested / "line\nbreak.txt").write_bytes(b"newline path\n")
    (nested / "z.txt").write_bytes(b"last\n")
    (snapshot_root / "\u03a9.txt").write_bytes(b"unicode path\n")
    (snapshot_root / "empty").mkdir()
    _prepare_completion_slot(layout)
    expected = _snapshot_content_digest(snapshot_root)

    published = json.loads(_run_completion(runner, "slot-complete", layout).stdout)

    assert published["payloadSha256"] == expected
    assert published["receipt"]["snapshot"]["contentSha256"] == expected
    assert published["receipt"]["build"]["payloadSha256"] == expected


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_content_mutation_invalidates_completion(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    published = json.loads(_run_completion(runner, "slot-complete", layout).stdout)
    marker = Path(published["completion"])
    marker_bytes = marker.read_bytes()
    (Path(layout["snapshot_root"]) / "payload-content.txt").write_text(
        "mutated snapshot\n",
        encoding="utf-8",
    )

    validated = _run_completion(
        runner,
        "slot-completion-validate",
        layout,
        check=False,
    )
    replayed = _run_completion(runner, "slot-complete", layout, check=False)

    assert validated.returncode != 0
    assert replayed.returncode != 0
    assert "validated snapshot" in validated.stderr.lower()
    assert marker.read_bytes() == marker_bytes


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file behavior")
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("entry_kind", ("symlink", "fifo"))
def test_snapshot_content_hashing_rejects_links_and_non_regular_entries(
    runner: Runner,
    entry_kind: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    if entry_kind == "symlink":
        target = tmp_path / "outside-content.txt"
        target.write_text("outside\n", encoding="utf-8")
        (snapshot_root / "linked-content").symlink_to(target)
    else:
        os.mkfifo(snapshot_root / "named-pipe")
    _stamp_with_python(layout)
    _provision_slot_with_python(layout)
    _build_receipt(layout, payload_hash="a" * 64)

    result = _run_completion(runner, "slot-complete", layout, check=False)

    assert result.returncode != 0
    if entry_kind == "symlink":
        assert "symbolic links or reparse points" in result.stderr.lower()
    else:
        assert "ordinary files or directories" in result.stderr.lower()
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


def test_posix_snapshot_hashing_fails_closed_on_partial_find_output(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    real_find = shutil.which("find")
    assert real_find is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_find = fake_bin / "find"
    fake_find.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "if [[ \"$*\" == *'-mindepth 1 -print0'* ]]; then",
                "  printf '%s\\0' \"$1/payload-content.txt\"",
                "  exit 7",
                "fi",
                f"exec {shlex.quote(real_find)} \"$@\"",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_find.chmod(0o755)

    result = _run_completion(
        ALL_RUNNERS[1],
        "slot-complete",
        layout,
        environment_overrides={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        check=False,
    )

    assert result.returncode != 0
    assert "enumerate all snapshot contents" in result.stderr.lower()
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement primitives")
@pytest.mark.parametrize("replacement", ("symlink", "fifo"))
def test_python_build_receipt_open_rejects_replacement_to_unsafe_object(
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    module = _load_python_module()
    original_open = module.os.open
    replaced = False

    def racing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if (
            not replaced
            and dir_fd is None
            and Path(path) == build_path
        ):
            replaced = True
            build_path.unlink()
            if replacement == "symlink":
                target = tmp_path / "outside-build.json"
                target.write_text(
                    json.dumps(
                        {
                            "version": "3.4.5",
                            "completed_at": "2026-01-02T03:04:05Z",
                            "pid": 999,
                            "payload_hash": _snapshot_content_digest(
                                Path(layout["snapshot_root"])
                            ),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                build_path.symlink_to(target)
            else:
                os.mkfifo(build_path)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", racing_open)
    with pytest.raises(module.InstallationContextError):
        module.complete_runtime_slot(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_payload_root=layout["payload"],
            expected_payload_version="1.0.0",
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )
    assert replaced
    assert not build_path.with_name(".runtime-slot-completion.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement primitives")
@pytest.mark.parametrize("replacement", ("symlink", "fifo"))
def test_python_snapshot_hash_open_rejects_replacement_to_unsafe_object(
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_file = Path(layout["snapshot_root"]) / "payload-content.txt"
    module = _load_python_module()
    original_open = module.os.open
    replaced = False

    def racing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if (
            not replaced
            and (
                (dir_fd is not None and os.fspath(path) == snapshot_file.name)
                or (dir_fd is None and Path(path) == snapshot_file)
            )
        ):
            replaced = True
            snapshot_file.unlink()
            if replacement == "symlink":
                target = tmp_path / "outside-snapshot.txt"
                target.write_text("outside\n", encoding="utf-8")
                snapshot_file.symlink_to(target)
            else:
                os.mkfifo(snapshot_file)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", racing_open)
    with pytest.raises(module.InstallationContextError):
        module._snapshot_content_sha256(Path(layout["snapshot_root"]))
    assert replaced


@pytest.mark.parametrize("artifact", ("provenance", "ownership"))
def test_python_completion_rejects_atomic_regular_replacement_after_validation(
    artifact: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    module = _load_python_module()
    artifact_path = (
        _provenance_path(layout)
        if artifact == "provenance"
        else (
            Path(layout["plugin_root"])
            / "versions"
            / "3.4.5"
            / ".runtime-slot-ownership.json"
        )
    )
    original_sha256_file = module._sha256_file
    replaced = False

    def replace_before_digest(path: Path) -> str:
        nonlocal replaced
        if not replaced and Path(path) == artifact_path:
            replaced = True
            replacement = artifact_path.with_name(f".{artifact_path.name}.next")
            replacement.write_bytes(artifact_path.read_bytes())
            os.replace(replacement, artifact_path)
        return original_sha256_file(path)

    monkeypatch.setattr(module, "_sha256_file", replace_before_digest)
    with pytest.raises(
        module.InstallationContextError,
        match="changed after it was validated",
    ):
        module.complete_runtime_slot(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_payload_root=layout["payload"],
            expected_payload_version="1.0.0",
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )

    assert replaced
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


def test_python_immutable_completion_read_rejects_atomic_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    module = _load_python_module()
    published = module.complete_runtime_slot(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        expected_payload_root=layout["payload"],
        expected_payload_version="1.0.0",
        snapshot_id="1.0.0",
        runtime_version="3.4.5",
        durable_home=layout["durable"],
        environment={},
    )
    marker = Path(published["completion"])
    replacement = marker.with_name(".replacement-completion.json")
    replacement_bytes = b'{"replaced":true}\n'
    replacement.write_bytes(replacement_bytes)
    original_open = module.os.open
    original_read = module.os.read
    marker_descriptor: int | None = None
    replaced = False

    def racing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal marker_descriptor
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is None and Path(path) == marker:
            marker_descriptor = descriptor
        return descriptor

    def racing_read(descriptor: int, length: int) -> bytes:
        nonlocal replaced
        if descriptor == marker_descriptor and not replaced:
            replaced = True
            os.replace(replacement, marker)
        return original_read(descriptor, length)

    monkeypatch.setattr(module.os, "open", racing_open)
    monkeypatch.setattr(module.os, "read", racing_read)
    with pytest.raises(
        module.InstallationContextError,
        match="changed while it was being read",
    ):
        module.validate_runtime_slot_completion(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_payload_root=layout["payload"],
            expected_payload_version="1.0.0",
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )

    assert replaced
    assert marker.read_bytes() == replacement_bytes


@pytest.mark.parametrize("mutation", ("add", "remove", "rename"))
def test_python_slot_completion_reconfirms_tree_before_publication(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    module = _load_python_module()
    original_digest = module._snapshot_content_sha256
    snapshot_root = Path(layout["snapshot_root"])
    calls = 0

    def racing_digest(root: Path, **kwargs: object) -> str:
        nonlocal calls
        digest = original_digest(root, **kwargs)
        calls += 1
        if calls == 1:
            target = snapshot_root / "payload-content.txt"
            if mutation == "add":
                (snapshot_root / "added.txt").write_text(
                    "added\n",
                    encoding="utf-8",
                )
            elif mutation == "remove":
                target.unlink()
            else:
                target.rename(snapshot_root / "renamed-content.txt")
        return digest

    monkeypatch.setattr(module, "_snapshot_content_sha256", racing_digest)
    with pytest.raises(
        module.InstallationContextError,
        match="changed before completion publication",
    ):
        module.complete_runtime_slot(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_payload_root=layout["payload"],
            expected_payload_version="1.0.0",
            snapshot_id="1.0.0",
            runtime_version="3.4.5",
            durable_home=layout["durable"],
            environment={},
        )

    assert calls == 2
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


@pytest.mark.skipif(BASH is None, reason="Bash 4.4 is unavailable")
@pytest.mark.parametrize("mutation", ("add", "remove", "rename"))
def test_posix_slot_completion_reconfirms_tree_before_publication(
    mutation: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    real_find = shutil.which("find")
    assert real_find is not None
    snapshot_root = Path(layout["snapshot_root"])
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    count_path = tmp_path / "find-count"
    target = snapshot_root / "payload-content.txt"
    if mutation == "add":
        mutation_command = (
            f"printf 'added\\n' >{shlex.quote(str(snapshot_root / 'added.txt'))}"
        )
    elif mutation == "remove":
        mutation_command = f"rm -f -- {shlex.quote(str(target))}"
    else:
        mutation_command = (
            f"mv -- {shlex.quote(str(target))} "
            f"{shlex.quote(str(snapshot_root / 'renamed-content.txt'))}"
        )
    fake_find = fake_bin / "find"
    fake_find.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"count_file={shlex.quote(str(count_path))}",
                "count=0",
                '[[ ! -f "$count_file" ]] || count="$(cat "$count_file")"',
                "count=$((count + 1))",
                'printf "%s\\n" "$count" >"$count_file"',
                f"if [[ \"$count\" -eq 3 ]]; then {mutation_command}; fi",
                f"exec {shlex.quote(real_find)} \"$@\"",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_find.chmod(0o755)

    result = _run_completion(
        ALL_RUNNERS[1],
        "slot-complete",
        layout,
        environment_overrides={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        check=False,
    )

    assert result.returncode != 0
    assert "snapshot content" in result.stderr.lower()
    assert "changed" in result.stderr.lower()
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


def test_snapshot_digest_limits_are_inclusive_and_count_provenance(
    tmp_path: Path,
) -> None:
    module = _load_python_module()
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "a").write_bytes(b"x")

    assert module._snapshot_content_sha256(
        root,
        max_entries=1,
        max_path_bytes=1,
        max_content_bytes=1,
    )
    with pytest.raises(module.InstallationContextError, match="0-entry limit"):
        module._snapshot_content_sha256(root, max_entries=0)
    with pytest.raises(module.InstallationContextError, match="0-byte UTF-8"):
        module._snapshot_content_sha256(root, max_path_bytes=0)
    with pytest.raises(module.InstallationContextError, match="0-byte regular-file"):
        module._snapshot_content_sha256(root, max_content_bytes=0)

    (root / "a").unlink()
    (root / "snapshot-provenance.json").write_bytes(b"x")
    assert module._snapshot_content_sha256(
        root,
        max_entries=1,
        max_path_bytes=len("snapshot-provenance.json"),
        max_content_bytes=1,
    ) == hashlib.sha256().hexdigest()
    with pytest.raises(module.InstallationContextError, match="0-entry limit"):
        module._snapshot_content_sha256(root, max_entries=0)
    with pytest.raises(module.InstallationContextError, match="0-byte regular-file"):
        module._snapshot_content_sha256(root, max_content_bytes=0)


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("evidence", "message"),
    (
        ("missing", "must exist"),
        ("malformed", "invalid json"),
        ("extra-key", "unknown or missing fields"),
        ("wrong-version", "version must match"),
        ("bad-time", "completed_at"),
        ("bool-pid", BUILD_PID_ERROR),
        ("negative-pid", BUILD_PID_ERROR),
        ("fractional-pid", BUILD_PID_ERROR),
        ("exponent-pid", BUILD_PID_ERROR),
        ("overflow-pid", BUILD_PID_ERROR),
        ("uppercase-hash", "lowercase 64-hex"),
        ("short-hash", "lowercase 64-hex"),
        ("forged-hash", "does not match the snapshot content digest"),
    ),
)
def test_runtime_slot_completion_rejects_invalid_build_evidence_without_replacement(
    runner: Runner,
    evidence: str,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    if evidence == "missing":
        build_path.unlink()
    elif evidence == "malformed":
        build_path.write_bytes(b"{")
    else:
        build = json.loads(build_path.read_text(encoding="utf-8"))
        if evidence == "extra-key":
            build["unexpected"] = True
        elif evidence == "wrong-version":
            build["version"] = "9.9.9"
        elif evidence == "bad-time":
            build["completed_at"] = "2026-02-30T00:00:00Z"
        elif evidence == "bool-pid":
            build["pid"] = True
        elif evidence == "negative-pid":
            build["pid"] = -1
        elif evidence == "fractional-pid":
            build["pid"] = 1.5
        elif evidence == "exponent-pid":
            build_path.write_text(
                json.dumps(
                    {
                        "version": build["version"],
                        "completed_at": build["completed_at"],
                    },
                    indent=2,
                )[:-2]
                + ',\n  "pid": 1e3,\n'
                + f'  "payload_hash": "{build["payload_hash"]}"\n'
                + "}\n",
                encoding="utf-8",
            )
            build = None
        elif evidence == "overflow-pid":
            build["pid"] = 9223372036854775808
        elif evidence == "uppercase-hash":
            build["payload_hash"] = str(build["payload_hash"]).upper()
        elif evidence == "short-hash":
            build["payload_hash"] = "a" * 63
        else:
            build["payload_hash"] = "b" * 64
        if build is not None:
            _write_json(build_path, build)
    evidence_bytes = build_path.read_bytes() if build_path.exists() else None

    result = _run_completion(runner, "slot-complete", layout, check=False)

    assert result.returncode != 0
    assert message in result.stderr.lower()
    assert not build_path.with_name(".runtime-slot-completion.json").exists()
    if evidence_bytes is not None:
        assert build_path.read_bytes() == evidence_bytes


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("marker_kind", ("malformed", "conflicting"))
def test_runtime_slot_completion_preserves_existing_malformed_or_conflicting_marker(
    runner: Runner,
    marker_kind: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    marker = (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    )
    if marker_kind == "malformed":
        marker.write_bytes(b"{")
    else:
        published = json.loads(
            _run_completion(RUNNERS[0], "slot-complete", layout).stdout
        )
        receipt = published["receipt"]
        receipt["build"]["payloadSha256"] = "b" * 64
        _write_json(marker, receipt)
    before = marker.read_bytes()

    result = _run_completion(runner, "slot-complete", layout, check=False)

    assert result.returncode != 0
    assert marker.read_bytes() == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("legacy_change", ("rewrite", "remove"))
def test_runtime_slot_completion_ignores_legacy_evidence_after_publication(
    runner: Runner,
    legacy_change: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    published = json.loads(_run_completion(runner, "slot-complete", layout).stdout)
    marker = Path(published["completion"])
    marker_bytes = marker.read_bytes()
    if legacy_change == "rewrite":
        build = json.loads(build_path.read_text(encoding="utf-8"))
        build["payload_hash"] = "b" * 64
        build["pid"] = -1
        _write_json(build_path, build)
    else:
        build_path.unlink()

    validated = _run_completion(
        runner,
        "slot-completion-validate",
        layout,
    )
    replayed = _run_completion(runner, "slot-complete", layout)

    assert json.loads(validated.stdout)["receipt"] == published["receipt"]
    assert json.loads(replayed.stdout)["created"] is False
    assert marker.read_bytes() == marker_bytes


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("pid", (0, 9223372036854775807))
def test_runtime_slot_completion_accepts_pid_bounds(
    runner: Runner,
    pid: int,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    build_path = (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".install-complete.json"
    )
    build = json.loads(build_path.read_text(encoding="utf-8"))
    build["pid"] = pid
    _write_json(build_path, build)

    published = json.loads(_run_completion(runner, "slot-complete", layout).stdout)
    validated = json.loads(
        _run_completion(runner, "slot-completion-validate", layout).stdout
    )

    assert published["receipt"]["build"]["pid"] == pid
    assert validated["receipt"]["build"]["pid"] == pid


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("pid_kind", "pid"),
    (
        ("boolean", True),
        ("negative", -1),
        ("fractional", 1.5),
        ("overflow", 9223372036854775808),
        ("exponent", None),
    ),
)
def test_runtime_slot_completion_rejects_invalid_immutable_receipt_pid(
    runner: Runner,
    pid_kind: str,
    pid: object,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    published = json.loads(_run_completion(RUNNERS[0], "slot-complete", layout).stdout)
    marker = Path(published["completion"])
    receipt = published["receipt"]
    if pid_kind == "exponent":
        marker.write_text(
            json.dumps(receipt, indent=2).replace('"pid": 123', '"pid": 1e3') + "\n",
            encoding="utf-8",
        )
    else:
        receipt["build"]["pid"] = pid
        _write_json(marker, receipt)

    result = _run_completion(
        runner,
        "slot-completion-validate",
        layout,
        check=False,
    )

    assert result.returncode != 0
    assert IMMUTABLE_PID_ERROR in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("container", ("snapshot", "build"))
def test_runtime_slot_completion_rejects_non_strict_nested_shapes(
    runner: Runner,
    container: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    published = json.loads(_run_completion(RUNNERS[0], "slot-complete", layout).stdout)
    marker = Path(published["completion"])
    receipt = published["receipt"]
    receipt[container]["unexpected"] = True
    _write_json(marker, receipt)

    result = _run_completion(
        runner,
        "slot-completion-validate",
        layout,
        check=False,
    )

    assert result.returncode != 0
    assert "unknown or missing fields" in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
def test_runtime_slot_completion_requires_current_receipts_for_first_publication(
    runner: Runner,
    receipt_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["generation"] += 1
    _write_json(receipt_path, receipt)

    result = _run_completion(runner, "slot-complete", layout, check=False)

    assert result.returncode != 0
    assert (
        f"snapshot provenance {receipt_name} generation is stale"
        in result.stderr.lower()
    )
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_validation_is_historical_but_rejects_regression(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    published = json.loads(_run_completion(runner, "slot-complete", layout).stdout)
    namespace_path = Path(layout["namespace"])
    namespace = json.loads(namespace_path.read_text(encoding="utf-8"))
    namespace["generation"] = 2
    namespace["state"] = "inactive"
    _write_json(namespace_path, namespace)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["generation"] = 3
    install["state"] = "inactive"
    install["payload"]["version"] = "2.0.0"
    _write_json(install_path, install)

    validated = json.loads(
        _run_completion(
            runner,
            "slot-completion-validate",
            layout,
            expected_payload_version="1.0.0",
        ).stdout
    )
    replay = json.loads(
        _run_completion(
        runner,
        "slot-complete",
        layout,
        expected_payload_version="1.0.0",
        ).stdout
    )

    assert validated["completion"] == published["completion"]
    assert validated["namespaceGeneration"] == 1
    assert validated["installGeneration"] == 2
    assert replay["created"] is False
    assert replay["receipt"] == published["receipt"]

    install["generation"] = 1
    _write_json(install_path, install)
    regressed = _run_completion(
        runner,
        "slot-completion-validate",
        layout,
        expected_payload_version="1.0.0",
        check=False,
    )
    assert regressed.returncode != 0
    assert "predates the owned runtime slot" in regressed.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_rejects_copied_foreign_ownership_and_completion(
    runner: Runner,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, plugin_id="agent-source")
    target_layout = _receipt_layout(tmp_path, plugin_id="agent-target")
    _prepare_completion_slot(source_layout)
    source_completion = json.loads(
        _run_completion(RUNNERS[0], "slot-complete", source_layout).stdout
    )
    _prepare_completion_slot(target_layout)
    target_slot = Path(target_layout["plugin_root"]) / "versions" / "3.4.5"

    target_ownership = target_slot / ".runtime-slot-ownership.json"
    target_ownership_bytes = target_ownership.read_bytes()
    shutil.copyfile(source_completion["ownership"], target_ownership)
    foreign_ownership = _run_completion(
        runner,
        "slot-complete",
        target_layout,
        check=False,
    )
    assert foreign_ownership.returncode != 0
    assert not (target_slot / ".runtime-slot-completion.json").exists()
    target_ownership.write_bytes(target_ownership_bytes)

    target_completion = target_slot / ".runtime-slot-completion.json"
    shutil.copyfile(source_completion["completion"], target_completion)
    completion_bytes = target_completion.read_bytes()
    foreign_completion = _run_completion(
        runner,
        "slot-completion-validate",
        target_layout,
        check=False,
    )
    assert foreign_completion.returncode != 0
    assert target_completion.read_bytes() == completion_bytes


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_rejects_same_version_build_from_other_snapshot(
    runner: Runner,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, plugin_id="agent-source")
    target_layout = _receipt_layout(tmp_path, plugin_id="agent-target")
    (Path(source_layout["snapshot_root"]) / "payload-content.txt").write_text(
        "source snapshot\n",
        encoding="utf-8",
    )
    source_build = _prepare_completion_slot(source_layout)
    target_build = _prepare_completion_slot(target_layout)
    shutil.copyfile(source_build, target_build)

    result = _run_completion(runner, "slot-complete", target_layout, check=False)

    assert result.returncode != 0
    assert "does not match the snapshot content digest" in result.stderr.lower()
    assert not target_build.with_name(".runtime-slot-completion.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("linked_artifact", ("build", "completion"))
def test_runtime_slot_completion_rejects_linked_artifacts(
    runner: Runner,
    linked_artifact: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    slot = build_path.parent
    if linked_artifact == "build":
        content = build_path.read_bytes()
        build_path.unlink()
        target = tmp_path / "outside-build.json"
        target.write_bytes(content)
        build_path.symlink_to(target)
        action = "slot-complete"
    else:
        published = json.loads(
            _run_completion(RUNNERS[0], "slot-complete", layout).stdout
        )
        completion = Path(published["completion"])
        content = completion.read_bytes()
        completion.unlink()
        target = tmp_path / "outside-completion.json"
        target.write_bytes(content)
        completion.symlink_to(target)
        action = "slot-completion-validate"

    result = _run_completion(runner, action, layout, check=False)

    assert result.returncode != 0
    assert "symbolic link or reparse point" in result.stderr.lower()
    assert target.read_bytes() == content
    if linked_artifact == "build":
        assert not (slot / ".runtime-slot-completion.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "unrelated_artifact",
    (".install-complete.json", ".runtime-slot-completion.json"),
)
def test_slot_validate_semantics_ignore_completion_artifacts(
    runner: Runner,
    unrelated_artifact: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provisioned = _provision_slot_with_python(layout)
    slot = Path(provisioned["slotRoot"])
    target = tmp_path / f"outside-{unrelated_artifact.lstrip('.')}"
    target.write_text("{}\n", encoding="utf-8")
    (slot / unrelated_artifact).symlink_to(target)

    result = json.loads(_run_slot(runner, "slot-validate", layout).stdout)

    assert result["reason"] == "runtime-slot-ownership-valid"
    assert result["slotEmpty"] is False
    assert target.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize(
    ("producer", "consumer"),
    INTEROPERABILITY_PAIRS,
)
@pytest.mark.installation_context_smoke
def test_runtime_slot_completion_interoperates_across_runners(
    producer: Runner,
    consumer: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)

    published = json.loads(_run_completion(producer, "slot-complete", layout).stdout)
    validated = json.loads(
        _run_completion(consumer, "slot-completion-validate", layout).stdout
    )

    assert validated["completion"] == published["completion"]
    assert validated["receipt"] == published["receipt"]


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_serializes_concurrent_publishers(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda _: _run_completion(
                    runner,
                    "slot-complete",
                    layout,
                    direct=True,
                ),
                range(2),
            )
        )
    results = [json.loads(result.stdout) for result in completed]

    assert sorted(result["created"] for result in results) == [False, True]
    assert len({result["completion"] for result in results}) == 1
    assert results[0]["receipt"] == results[1]["receipt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX byte-oriented filenames")
def test_posix_completion_rejects_non_utf8_snapshot_path(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    snapshot_root = os.fsencode(Path(layout["snapshot_root"]))
    invalid_path = os.path.join(snapshot_root, b"invalid-\xff.txt")
    try:
        descriptor = os.open(invalid_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as error:
        pytest.skip(f"filesystem does not support invalid-byte filenames: {error}")
    try:
        os.write(descriptor, b"invalid path\n")
    finally:
        os.close(descriptor)

    result = _run_completion(ALL_RUNNERS[1], "slot-complete", layout, check=False)

    assert result.returncode != 0
    assert "snapshot content path is not valid utf-8" in result.stderr.lower()
    assert not (
        Path(layout["plugin_root"])
        / "versions"
        / "3.4.5"
        / ".runtime-slot-completion.json"
    ).exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_completion_accepts_literal_replacement_character_in_snapshot_path(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    (Path(layout["snapshot_root"]) / "valid-\ufffd.txt").write_text(
        "valid UTF-8 path\n",
        encoding="utf-8",
    )
    _prepare_completion_slot(layout)

    result = json.loads(_run_completion(runner, "slot-complete", layout).stdout)

    assert result["created"] is True


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_completion_captures_one_concurrently_replaced_build_receipt(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    build_path = _prepare_completion_slot(layout)
    payload_hash = _snapshot_content_digest(Path(layout["snapshot_root"]))
    candidates = (
        {
            "version": "3.4.5",
            "completed_at": "2026-01-02T03:04:05Z",
            "pid": 11,
            "payload_hash": payload_hash,
        },
        {
            "version": "3.4.5",
            "completed_at": "2026-01-02T03:04:06Z",
            "pid": 22,
            "payload_hash": payload_hash,
        },
    )
    candidate_bytes = tuple(
        (json.dumps(candidate, indent=2) + "\n").encode("utf-8")
        for candidate in candidates
    )
    build_path.write_bytes(candidate_bytes[0])
    stop = Event()

    def rewrite() -> None:
        index = 1
        staging = build_path.with_name(".install-complete.next")
        while not stop.is_set():
            staging.write_bytes(candidate_bytes[index])
            os.replace(staging, build_path)
            index = 1 - index

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(rewrite)
        try:
            published = json.loads(
                _run_completion(runner, "slot-complete", layout).stdout
            )
        finally:
            stop.set()
            future.result(timeout=10)

    recorded = published["receipt"]["build"]
    matched = [
        candidate
        for candidate, content in zip(candidates, candidate_bytes, strict=True)
        if recorded["receiptSha256"] == hashlib.sha256(content).hexdigest()
        and recorded["pid"] == candidate["pid"]
        and published["receipt"]["completedAt"] == candidate["completed_at"]
    ]
    assert len(matched) == 1
    validated = json.loads(
        _run_completion(runner, "slot-completion-validate", layout).stdout
    )
    assert validated["receipt"] == published["receipt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX date compatibility")
def test_posix_completion_accepts_bsd_date_interface(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    real_date = shutil.which("date")
    assert real_date is not None
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -e",
                'if [[ "$1" == "-u" && "$2" == "-d" ]]; then exit 1; fi',
                'if [[ "$1" == "-j" && "$2" == "-u" && "$3" == "-f" ]]; then',
                f"  exec {shlex.quote(real_date)} -u -d \"$5\" \"$6\"",
                "fi",
                'if [[ "$1" == "-j" && "$2" == "-u" && "$3" == "-r" ]]; then',
                f"  exec {shlex.quote(real_date)} -u -d \"@$4\" \"$5\"",
                "fi",
                "exit 2",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_date.chmod(0o755)

    result = json.loads(
        _run_completion(
            ALL_RUNNERS[1],
            "slot-complete",
            layout,
            environment_overrides={
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
        ).stdout
    )

    assert result["created"] is True


def test_python_api_publishes_and_validates_runtime_slot_completion(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    module = _load_python_module()
    arguments = {
        "context": layout["install"],
        "expected_marketplace_id": layout["marketplace_id"],
        "expected_plugin_id": layout["plugin_id"],
        "expected_payload_root": layout["payload"],
        "expected_payload_version": "1.0.0",
        "snapshot_id": "1.0.0",
        "runtime_version": "3.4.5",
        "durable_home": layout["durable"],
        "environment": {},
    }

    first = module.complete_runtime_slot(**arguments)
    replay = module.complete_runtime_slot(**arguments)
    validated = module.validate_runtime_slot_completion(**arguments)

    assert first["created"] is True
    assert replay["created"] is False
    assert validated["receipt"] == first["receipt"]
    assert validated["activated"] is False
    assert validated["operative"] is False


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_tracks_selected_last_known_good(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    plugin_root = Path(layout["plugin_root"])
    _prepare_completion_slot(layout, runtime_version="1.0.0")
    _run_completion(runner, "slot-complete", layout, runtime_version="1.0.0")

    initial = json.loads(
        _run_cutover(
            runner,
            layout,
            runtime_version="1.0.0",
            expect_current_absent=True,
        ).stdout
    )

    assert initial["currentVersion"] == "1.0.0"
    assert initial["lastKnownGoodVersion"] == "1.0.0"
    assert initial["activated"] is False
    assert initial["operative"] is False

    _prepare_completion_slot(layout, runtime_version="2.0.0")
    _run_completion(runner, "slot-complete", layout, runtime_version="2.0.0")
    updated = json.loads(
        _run_cutover(
            runner,
            layout,
            runtime_version="2.0.0",
            expected_current_version="1.0.0",
        ).stdout
    )

    assert updated["previousVersion"] == "1.0.0"
    assert updated["currentVersion"] == "2.0.0"
    assert updated["lastKnownGoodVersion"] == "2.0.0"
    assert (plugin_root / "current-version").read_text(encoding="utf-8") == "2.0.0\n"
    assert (plugin_root / "last-known-good").read_text(encoding="utf-8") == "2.0.0\n"

    before_repeat = {
        name: (plugin_root / name).read_bytes()
        for name in ("current-version", "last-known-good")
    }
    repeated = json.loads(
        _run_cutover(
            runner,
            layout,
            runtime_version="2.0.0",
            expected_current_version="2.0.0",
        ).stdout
    )
    assert repeated["cutoverChanged"] is False
    assert repeated["reason"] == "runtime-slot-cutover-current"
    assert {
        name: (plugin_root / name).read_bytes()
        for name in ("current-version", "last-known-good")
    } == before_repeat

    rolled_back = json.loads(
        _run_cutover(
            runner,
            layout,
            runtime_version="1.0.0",
            expected_current_version="2.0.0",
        ).stdout
    )

    assert rolled_back["previousVersion"] == "2.0.0"
    assert rolled_back["currentVersion"] == "1.0.0"
    assert rolled_back["lastKnownGoodVersion"] == "1.0.0"


@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("stale_kind", "expected_namespace_generation", "expected_current_version"),
    (
        ("generation-changed", 3, "1.0.0"),
        ("current-version-changed", 1, "9.9.9"),
    ),
)
def test_runtime_slot_cutover_cas_mismatch_does_not_mutate_markers(
    runner: Runner,
    stale_kind: str,
    expected_namespace_generation: int,
    expected_current_version: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    plugin_root = Path(layout["plugin_root"])
    _prepare_completion_slot(layout, runtime_version="1.0.0")
    _run_completion(runner, "slot-complete", layout, runtime_version="1.0.0")
    _run_cutover(
        runner,
        layout,
        runtime_version="1.0.0",
        expect_current_absent=True,
    )
    before = {
        name: (plugin_root / name).read_bytes()
        for name in ("current-version", "last-known-good")
    }
    _prepare_completion_slot(layout, runtime_version="2.0.0")
    _run_completion(runner, "slot-complete", layout, runtime_version="2.0.0")

    result = json.loads(
        _run_cutover(
            runner,
            layout,
            runtime_version="2.0.0",
            expected_namespace_generation=expected_namespace_generation,
            expected_current_version=expected_current_version,
        ).stdout
    )

    assert result["status"] == "revalidation-required"
    assert result["reason"] == stale_kind
    assert result["cutoverChanged"] is False
    assert {
        name: (plugin_root / name).read_bytes()
        for name in ("current-version", "last-known-good")
    } == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("marker_name", ("current-version", "last-known-good"))
def test_runtime_slot_cutover_rejects_malformed_markers(
    runner: Runner,
    marker_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    marker = Path(layout["plugin_root"]) / marker_name
    marker.write_text("3.4.5\n\n", encoding="utf-8")
    before = marker.read_bytes()

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=(marker_name == "last-known-good"),
        expected_current_version=(
            "3.4.5" if marker_name == "current-version" else None
        ),
        check=False,
    )

    assert result.returncode != 0
    assert "exactly one runtime version" in result.stderr.lower()
    assert marker.read_bytes() == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_rejects_oversized_marker(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    marker = Path(layout["plugin_root"]) / "last-known-good"
    marker.write_bytes(b"x" * 131)
    before = marker.read_bytes()

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=True,
        check=False,
    )

    assert result.returncode != 0
    assert marker.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic-link behavior")
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("marker_name", ("current-version", "last-known-good"))
def test_runtime_slot_cutover_rejects_linked_markers(
    runner: Runner,
    marker_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    marker = Path(layout["plugin_root"]) / marker_name
    target = tmp_path / f"outside-{marker_name}"
    target.write_text("3.4.5\n", encoding="utf-8")
    marker.symlink_to(target)

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=(marker_name == "last-known-good"),
        expected_current_version=(
            "3.4.5" if marker_name == "current-version" else None
        ),
        check=False,
    )

    assert result.returncode != 0
    assert "symbolic link or reparse point" in result.stderr.lower()
    assert marker.is_symlink()
    assert target.read_text(encoding="utf-8") == "3.4.5\n"


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("marker_name", ("current-version", "last-known-good"))
def test_runtime_slot_cutover_rejects_directory_markers(
    runner: Runner,
    marker_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    marker = Path(layout["plugin_root"]) / marker_name
    marker.mkdir()

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=(marker_name == "last-known-good"),
        expected_current_version=(
            "3.4.5" if marker_name == "current-version" else None
        ),
        check=False,
    )

    assert result.returncode != 0
    assert marker.is_dir()
    assert list(marker.iterdir()) == []


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_serializes_concurrent_winners(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    for version in ("1.0.0", "2.0.0", "3.0.0"):
        _prepare_completion_slot(layout, runtime_version=version)
        _run_completion(runner, "slot-complete", layout, runtime_version=version)
    _run_cutover(
        runner,
        layout,
        runtime_version="1.0.0",
        expect_current_absent=True,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda version: _run_cutover(
                    runner,
                    layout,
                    runtime_version=version,
                    expected_current_version="1.0.0",
                    direct=True,
                ),
                ("2.0.0", "3.0.0"),
            )
        )
    results = [json.loads(result.stdout) for result in completed]

    assert sorted(result["status"] for result in results) == [
        "ready",
        "revalidation-required",
    ]
    winner = next(result for result in results if result["status"] == "ready")
    loser = next(
        result for result in results
        if result["status"] == "revalidation-required"
    )
    assert loser["reason"] == "current-version-changed"
    assert winner["lastKnownGoodVersion"] == winner["runtimeVersion"]
    assert (
        Path(layout["plugin_root"]) / "current-version"
    ).read_text(encoding="utf-8").strip() == winner["runtimeVersion"]


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_requires_immutable_completion(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    _provision_slot_with_python(layout)

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=True,
        check=False,
    )

    assert result.returncode != 0
    assert "runtime slot completion must exist" in result.stderr.lower()
    assert not (Path(layout["plugin_root"]) / "current-version").exists()
    assert not (Path(layout["plugin_root"]) / "last-known-good").exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_requires_active_current_receipts(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["state"] = "removing"
    _write_json(install_path, install)

    result = _run_cutover(
        runner,
        layout,
        expect_current_absent=True,
        check=False,
    )

    assert result.returncode != 0
    assert "requires active namespace and install receipts" in result.stderr.lower()
    assert not (Path(layout["plugin_root"]) / "current-version").exists()
    assert not (Path(layout["plugin_root"]) / "last-known-good").exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_normalizes_expected_generations(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)

    result = json.loads(
        _run_cutover(
            runner,
            layout,
            expected_namespace_generation="01",
            expected_install_generation="002",
            expect_current_absent=True,
        ).stdout
    )

    assert result["status"] == "ready"
    assert result["namespaceGeneration"] == 1
    assert result["installGeneration"] == 2


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_accepts_existing_crlf_markers(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    plugin_root = Path(layout["plugin_root"])
    for name in ("current-version", "last-known-good"):
        (plugin_root / name).write_bytes(b"3.4.5\r\n")

    result = json.loads(
        _run_cutover(
            runner,
            layout,
            expected_current_version="3.4.5",
        ).stdout
    )

    assert result["status"] == "ready"
    assert result["cutoverChanged"] is False
    for name in ("current-version", "last-known-good"):
        assert (plugin_root / name).read_bytes() == b"3.4.5\r\n"


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_cutover_uses_versions_root_parent(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    install_path = Path(layout["install"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["roots"]["versions"] = "runtime/versions"
    _write_json(install_path, install)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)

    result = json.loads(
        _run_cutover(
            runner,
            layout,
            expect_current_absent=True,
        ).stdout
    )

    runtime_root = Path(layout["plugin_root"]) / "runtime"
    assert Path(result["currentMarker"]) == runtime_root / "current-version"
    assert Path(result["lastKnownGoodMarker"]) == runtime_root / "last-known-good"
    assert (runtime_root / "current-version").read_text(encoding="utf-8") == "3.4.5\n"
    assert not (Path(layout["plugin_root"]) / "current-version").exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("expectation_shape", ("both", "neither"))
def test_runtime_slot_cutover_cli_requires_exactly_one_current_expectation(
    runner: Runner,
    expectation_shape: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(runner, "slot-complete", layout)
    command = _command(
        runner,
        "slot-cutover",
        layout,
        expected_payload_root=Path(layout["payload"]),
        expected_payload_version=_payload_version(layout),
        expected_current_version=(
            "3.4.5" if expectation_shape == "both" else None
        ),
    )
    absent_flag = _flag(runner[2], "expect-current-absent")
    if expectation_shape == "both":
        command.append(absent_flag)
    else:
        command.remove(absent_flag)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        check=False,
    )

    assert result.returncode != 0


def test_python_api_requires_exact_runtime_slot_cutover_expectation(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    _run_completion(RUNNERS[0], "slot-complete", layout)
    module = _load_python_module()
    arguments = {
        "context": layout["install"],
        "expected_marketplace_id": layout["marketplace_id"],
        "expected_plugin_id": layout["plugin_id"],
        "expected_payload_root": layout["payload"],
        "expected_payload_version": "1.0.0",
        "snapshot_id": "1.0.0",
        "runtime_version": "3.4.5",
        "expected_namespace_generation": 1,
        "expected_install_generation": 2,
        "durable_home": layout["durable"],
        "environment": {},
    }

    with pytest.raises(
        module.InstallationContextError,
        match="Specify exactly one",
    ):
        module.cutover_runtime_slot(**arguments)
    with pytest.raises(
        module.InstallationContextError,
        match="Specify exactly one",
    ):
        module.cutover_runtime_slot(
            **arguments,
            expected_current_version="1.0.0",
            expect_current_absent=True,
        )
    with pytest.raises(
        module.InstallationContextError,
        match="signed 64-bit maximum",
    ):
        module.cutover_runtime_slot(
            **{
                **arguments,
                "expected_namespace_generation": 9223372036854775808,
            },
            expect_current_absent=True,
        )


@pytest.mark.parametrize("runner", FAST_PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "action",
    ("slot-complete", "slot-completion-validate"),
)
@pytest.mark.parametrize(
    "flag_name",
    ("expected-payload-root", "expected-payload-version"),
)
def test_completion_actions_require_explicit_payload_expectations(
    runner: Runner,
    action: str,
    flag_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _prepare_completion_slot(layout)
    command = _command(
        runner,
        action,
        layout,
        expected_payload_root=Path(layout["payload"]),
        expected_payload_version="1.0.0",
    )
    _, _, style = runner
    flag = _flag(style, flag_name)
    index = command.index(flag)
    del command[index : index + 2]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "COPILOT_EXTENSIONS_CONTEXT": str(layout["install"]),
        },
        check=False,
    )

    assert result.returncode != 0
    assert flag.lower() in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    "argument",
    ("expected_namespace_generation", "expected_install_generation"),
)
def test_importable_python_snapshot_api_rejects_generation_overflow(
    argument: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    generations = {
        "expected_namespace_generation": 1,
        "expected_install_generation": 2,
    }
    generations[argument] = 9223372036854775808
    with pytest.raises(
        module.InstallationContextError,
        match="portable signed 64-bit maximum",
    ):
        module.stamp_snapshot_provenance(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            snapshot_id="1.0.0",
            durable_home=layout["durable"],
            environment={},
            **generations,
        )
    assert not _provenance_path(layout).exists()


def test_python_reparse_detection_supports_pre_312_pathlib(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_python_module()
    path = tmp_path / "junction"
    path.mkdir()
    monkeypatch.setattr(module.Path, "is_symlink", lambda _self: False)
    if hasattr(module.Path, "is_junction"):
        monkeypatch.setattr(module.Path, "is_junction", lambda _self: False)
    monkeypatch.setattr(
        module.os,
        "lstat",
        lambda _path: SimpleNamespace(
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        ),
    )
    assert module._is_link_or_junction(path) is True


def test_python_snapshot_leaf_name_defends_when_link_detection_degrades(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    requested_root = Path(layout["snapshot_root"])
    other_root = Path(layout["snapshots"]) / "other"
    shutil.move(requested_root, other_root)
    requested_root.symlink_to(other_root, target_is_directory=True)
    monkeypatch.setattr(module, "_is_link_or_junction", lambda _path: False)
    with pytest.raises(
        module.InstallationContextError,
        match="requested snapshot id",
    ):
        module.stamp_snapshot_provenance(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_namespace_generation=1,
            expected_install_generation=2,
            snapshot_id="1.0.0",
            durable_home=layout["durable"],
            environment={},
        )


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
def test_original_payload_replacement_does_not_make_stage_path_identity(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    payload_file = Path(layout["payload"]) / "content.txt"
    payload_file.write_text("replacement\n", encoding="utf-8")
    validated = json.loads(_run(runner, "snapshot-validate", layout).stdout)
    assert validated["status"] == "ready"
    assert validated["payload"]["root"] == str(Path(layout["payload"]).resolve())


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("source", "fingerprint"), "sha256:" + "0" * 64, "fingerprint"),
        (("marketplaceId",), "other--0123456789abcdef", "Expected marketplace"),
        (("pluginId",), "agent-other", "Expected plugin"),
        (("payload", "root"), "/other/payload", "payload"),
        (("payload", "version"), "2.0.0", "payload"),
        (("payload", "origin"), "installed", "payload"),
        (("payload", "originReceipt"), "/other/origin.json", "payload"),
        (("namespaceReceipt", "path"), "/other/namespace.json", "namespace receipt"),
        (("namespaceReceipt", "generation"), 2, "namespace generation"),
        (("installReceipt", "path"), "/other/install.json", "install receipt"),
        (("installReceipt", "generation"), 3, "install generation"),
        (("snapshot", "id"), "other", "snapshot directory"),
        (("snapshot", "root"), "/other/snapshot", "snapshot.root"),
    ],
)
def test_snapshot_identity_mismatches_fail_closed(
    runner: Runner,
    path: tuple[str, ...],
    replacement: object,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    target = provenance
    for key in path[:-1]:
        target = target[key]
    if isinstance(replacement, str) and replacement.startswith("/other/"):
        replacement = str(tmp_path / replacement.removeprefix("/other/"))
    target[path[-1]] = replacement
    _write_json(provenance_path, provenance)
    before = _tree_snapshot(Path(layout["durable"]))
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert _tree_snapshot(Path(layout["durable"])) == before


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("source", None, "source is missing"),
        ("snapshot", "not-an-object", "snapshot identity is missing"),
        ("namespaceReceipt", [], "receipt references are missing"),
        ("payload", [], "payload identity is missing"),
    ),
)
def test_snapshot_container_fields_require_json_objects(
    runner: Runner,
    field: str,
    replacement: object,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance[field] = replacement
    _write_json(provenance_path, provenance)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize(
    ("runner", "content", "message"),
    _runner_case_matrix(
        (
            ("invalid-json", b"{", "invalid json"),
            (
                "duplicate-key",
                b'{"schema":"copilot-extensions.snapshot-provenance",'
                b'"schema":"copilot-extensions.snapshot-provenance","version":1}',
                "duplicate",
            ),
            (
                "bom",
                b"\xef\xbb\xbf"
                b'{"schema":"copilot-extensions.snapshot-provenance","version":1}',
                "invalid",
            ),
            (
                "string-version",
                b'{"schema":"copilot-extensions.snapshot-provenance","version":"1"}',
                "version",
            ),
            (
                "unsupported-version",
                b'{"schema":"copilot-extensions.snapshot-provenance","version":2}',
                "version",
            ),
        ),
        {"invalid-json", "duplicate-key", "bom"},
    ),
)
def test_malformed_snapshot_sidecars_are_rejected_without_replacement(
    runner: Runner,
    content: bytes,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    provenance.write_bytes(content)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert provenance.read_bytes() == content


@pytest.mark.parametrize(
    ("runner", "snapshot_id"),
    _runner_case_matrix(
        (
            ("parent-posix", "../other"),
            ("parent-windows", "..\\other"),
            ("nested-posix", "nested/child"),
            ("nested-windows", "nested\\child"),
            ("absolute", "/absolute"),
            ("newline", "1.0.0\n"),
            ("carriage-return", "1.0.0\r"),
        ),
        {"parent-posix", "parent-windows", "absolute", "newline"},
    ),
)
def test_snapshot_path_attacks_are_rejected_without_mutation(
    runner: Runner,
    snapshot_id: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    before = _tree_snapshot(Path(layout["durable"]))
    result = _run(
        runner,
        "snapshot-stamp",
        layout,
        snapshot_id=snapshot_id,
        check=False,
    )
    assert result.returncode != 0
    assert "snapshot id" in result.stderr.lower()
    assert _tree_snapshot(Path(layout["durable"])) == before


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("materialization", ("missing", "empty"))
def test_snapshot_stamp_requires_preexisting_materialized_content(
    runner: Runner,
    materialization: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    (snapshot_root / "payload-content.txt").unlink()
    if materialization == "missing":
        snapshot_root.rmdir()
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "materialized" in result.stderr.lower()
    assert not _provenance_path(layout).exists()


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_validation_rejects_sidecar_only_snapshot(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    (Path(layout["snapshot_root"]) / "payload-content.txt").unlink()
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "materialized" in result.stderr.lower()


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
def test_stale_receipt_generation_rejects_republication_without_overwrite(
    runner: Runner,
    receipt_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    original = provenance.read_bytes()
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["generation"] += 1
    _write_json(receipt_path, receipt)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "generation changed" in result.stderr.lower()
    assert provenance.read_bytes() == original


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
@pytest.mark.parametrize("state", ("inactive", "orphaned"))
def test_inactive_or_orphaned_receipts_reject_snapshot_validation(
    runner: Runner,
    receipt_name: str,
    state: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["state"] = state
    _write_json(receipt_path, receipt)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "active namespace and install receipts" in result.stderr.lower()


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "created_at",
    ("not-a-time", "2026-02-30T00:00:00Z", "2026-01-01T00:00:00+00:00"),
)
def test_snapshot_created_at_must_be_exact_valid_utc_timestamp(
    runner: Runner,
    created_at: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["createdAt"] = created_at
    _write_json(provenance_path, provenance)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "createdat" in result.stderr.lower()


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("target_kind", ("cell", "plugin"))
def test_copied_cross_cell_or_cross_plugin_sidecar_is_rejected(
    runner: Runner,
    target_kind: str,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, vector_index=0)
    _stamp_with_python(source_layout)
    if target_kind == "cell":
        target_layout = _receipt_layout(tmp_path, vector_index=1)
    else:
        target_layout = _receipt_layout(tmp_path, vector_index=0, plugin_id="agent-other")
    target = _provenance_path(target_layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_provenance_path(source_layout), target)
    result = _run(runner, "snapshot-validate", target_layout, check=False)
    assert result.returncode != 0
    assert (
        "expected marketplace" in result.stderr.lower()
        or "expected plugin" in result.stderr.lower()
    )


@pytest.mark.parametrize("runner", REFERENCE_RUNNERS, ids=lambda runner: runner[0])
def test_rewritten_foreign_sidecar_still_fails_receipt_anchoring(
    runner: Runner,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, vector_index=0)
    target_layout = _receipt_layout(tmp_path, vector_index=1)
    _stamp_with_python(source_layout)
    provenance = json.loads(
        _provenance_path(source_layout).read_text(encoding="utf-8")
    )
    target_namespace = json.loads(
        Path(target_layout["namespace"]).read_text(encoding="utf-8")
    )
    provenance["marketplaceId"] = target_layout["marketplace_id"]
    provenance["pluginId"] = target_layout["plugin_id"]
    provenance["source"] = target_namespace["source"]
    provenance["snapshot"] = {
        "id": "1.0.0",
        "root": str(Path(target_layout["snapshot_root"]).resolve()),
    }
    _write_json(_provenance_path(target_layout), provenance)
    result = _run(runner, "snapshot-validate", target_layout, check=False)
    assert result.returncode != 0
    assert "namespace receipt" in result.stderr.lower()


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_plugin_root_symlink_cannot_escape_the_cell(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    plugin_root = Path(layout["plugin_root"])
    outside = tmp_path / "outside-plugin-root"
    shutil.move(plugin_root, outside)
    try:
        plugin_root.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "plugin root" in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("component", "label"),
    (
        ("marketplaces", "marketplaces root"),
        ("cell", "marketplace cell root"),
        ("plugins", "cell plugins root"),
        ("plugin_root", "plugin root"),
    ),
)
def test_context_validation_rejects_linked_physical_ownership_chain(
    runner: Runner,
    component: str,
    label: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    paths = {
        "marketplaces": Path(layout["durable"]) / "marketplaces",
        "cell": Path(layout["cell"]),
        "plugins": Path(layout["cell"]) / "plugins",
        "plugin_root": Path(layout["plugin_root"]),
    }
    linked_path = paths[component]
    outside = tmp_path / f"outside-{component}"
    shutil.move(linked_path, outside)
    try:
        linked_path.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run_context_validate(runner, layout)
    assert result.returncode != 0
    assert label in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
def test_context_validation_rejects_linked_receipt_files(
    runner: Runner,
    receipt_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    receipt = Path(layout[receipt_name])
    outside = tmp_path / f"outside-{receipt_name}.json"
    shutil.move(receipt, outside)
    try:
        receipt.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable: {error}")
    original = outside.read_bytes()
    result = _run_context_validate(runner, layout)
    assert result.returncode != 0
    assert f"{receipt_name}.json may not" in result.stderr.lower()
    assert outside.read_bytes() == original


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_root_symlink_cannot_escape_the_plugin(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    outside = tmp_path / "outside-snapshot"
    shutil.move(snapshot_root, outside)
    try:
        snapshot_root.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "snapshot root" in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_sidecar_symlink_is_rejected_without_touching_target(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    outside = tmp_path / "outside-provenance.json"
    shutil.move(provenance, outside)
    try:
        provenance.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable: {error}")
    original = outside.read_bytes()
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "snapshot provenance" in result.stderr.lower()
    assert outside.read_bytes() == original


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    ("argument", "value", "message"),
    (
        ("expected_namespace_generation", "1.5", "non-negative integer"),
        ("expected_install_generation", "2.5", "non-negative integer"),
        (
            "expected_namespace_generation",
            "9223372036854775808",
            "portable signed 64-bit maximum",
        ),
    ),
)
def test_powershell_rejects_non_decimal_int64_generation_arguments(
    argument: str,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    generations: dict[str, int | str] = {
        "expected_namespace_generation": 1,
        "expected_install_generation": 2,
    }
    generations[argument] = value
    result = _run(
        next(runner for runner in ALL_RUNNERS if runner[0] == "powershell"),
        "snapshot-stamp",
        layout,
        check=False,
        **generations,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize(
    ("runner", "value", "message"),
    _runner_case_matrix(
        (
            ("signed", "+1", "generation"),
            ("leading-space", " 1", "generation"),
            ("separator", "1_0", "generation"),
            ("non-ascii", "\u0661", "generation"),
            (
                "int64-overflow",
                "9223372036854775808",
                "portable signed 64-bit maximum",
            ),
            (
                "decimal-overflow",
                "10000000000000000000",
                "portable signed 64-bit maximum",
            ),
        ),
        {"signed", "non-ascii", "int64-overflow"},
    ),
)
def test_generation_arguments_reject_non_ascii_decimal_or_overflow(
    runner: Runner,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    result = _run(
        runner,
        "snapshot-stamp",
        layout,
        expected_namespace_generation=value,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_generation_arguments_normalize_leading_zeroes(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    result = json.loads(
        _run(
            runner,
            "snapshot-stamp",
            layout,
            expected_namespace_generation="01",
            expected_install_generation="002",
        ).stdout
    )
    assert result["reason"] == "snapshot-provenance-published"


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda runner: runner[0])
def test_concurrent_snapshot_publication_has_one_atomic_winner_and_retry(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    processes = [
        subprocess.Popen(
            _command(runner, "snapshot-stamp", layout),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        for _ in range(2)
    ]
    results = [(*process.communicate(timeout=30), process.returncode) for process in processes]
    payloads: list[dict[str, object]] = []
    for stdout, stderr, returncode in results:
        if returncode == 0:
            payloads.append(json.loads(stdout))
            continue
        assert "remained busy" in stderr.lower(), results
        payloads.append(json.loads(_run(runner, "snapshot-stamp", layout).stdout))
    assert sum(payload["snapshotChanged"] is True for payload in payloads) == 1
    assert sum(payload["snapshotChanged"] is False for payload in payloads) == 1
    provenance = _provenance_path(layout)
    assert json.loads(provenance.read_text(encoding="utf-8"))["snapshot"]["id"] == "1.0.0"


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_powershell_lock_owner_invalid_utf8_is_classified(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    lock = (
        Path(layout["durable"])
        / "marketplaces"
        / ".locks"
        / f"{layout['marketplace_id']}.genesis"
    )
    lock.mkdir(parents=True)
    (lock / "owner.json").write_bytes(b"\xff")
    runner = next(
        candidate for candidate in ALL_RUNNERS if candidate[0] == "powershell"
    )

    result = _run(runner, "snapshot-stamp", layout, check=False)

    assert result.returncode != 0
    assert "invalid utf-8 in installation lock owner receipt" in result.stderr.lower()
    assert "decoderfallbackexception" not in result.stderr.lower()
