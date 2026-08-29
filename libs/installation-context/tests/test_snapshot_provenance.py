"""Cross-runner tests for immutable snapshot provenance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1]
PYTHON_SCRIPT = LIB / "installation_context.py"
POSIX_SCRIPT = LIB / "installation-context.sh"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
FIXTURES = LIB / "fixtures" / "source-identities.json"
Runner = tuple[str, tuple[str, ...], str]


def _supported_bash() -> str | None:
    if os.name == "nt":
        return None
    candidate = shutil.which("bash")
    if candidate is None:
        return None
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
    return candidate if result.returncode == 0 else None


BASH = _supported_bash()


RUNNERS: tuple[Runner, ...] = (
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
    if action == "snapshot-stamp":
        command.extend(
            [
                _flag(style, "expected-namespace-generation"),
                str(expected_namespace_generation),
                _flag(style, "expected-install-generation"),
                str(expected_install_generation),
            ]
        )
    if action in {"slot-provision", "slot-validate"}:
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
    environment_overrides: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    if environment_overrides:
        environment.update(environment_overrides)
    result = subprocess.run(
        _command(
            runner,
            action,
            layout,
            snapshot_id=snapshot_id,
            runtime_version=runtime_version,
            expected_namespace_generation=expected_namespace_generation,
            expected_install_generation=expected_install_generation,
            expected_payload_root=expected_payload_root,
            expected_payload_version=expected_payload_version,
        ),
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
    )


def _run_context_validate(
    runner: Runner,
    layout: dict[str, Path | str],
) -> subprocess.CompletedProcess[str]:
    _, prefix, style = runner
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    return subprocess.run(
        [
            *prefix,
            "validate",
            _flag(style, "context"),
            str(layout["install"]),
            _flag(style, "durable-home"),
            str(layout["durable"]),
            _flag(style, "expected-marketplace-id"),
            str(layout["marketplace_id"]),
            _flag(style, "expected-plugin-id"),
            str(layout["plugin_id"]),
        ],
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
    return json.loads(_run(RUNNERS[0], "snapshot-stamp", layout, snapshot_id=snapshot_id).stdout)


def _provenance_path(
    layout: dict[str, Path | str],
    snapshot_id: str = "1.0.0",
) -> Path:
    return Path(layout["snapshots"]) / snapshot_id / "snapshot-provenance.json"


def _provision_slot_with_python(
    layout: dict[str, Path | str],
    *,
    runtime_version: str = "3.4.5",
    module: Any | None = None,
) -> dict[str, object]:
    module = module or _load_python_module()
    return module.provision_runtime_slot(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        runtime_version=runtime_version,
        durable_home=layout["durable"],
        environment={},
    )


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


def _run_exemplar_slot_action(
    exemplar: tuple[str, tuple[str, ...], str],
    action: str,
    layout: dict[str, Path | str],
    tmp_path: Path,
    *,
    include_context: bool = True,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    _, prefix, style = exemplar
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
        shutil.copytree(source_plugin, installed_plugin)
    installed_script = installed_plugin / source_script.relative_to(source_plugin)
    return (*prefix[:-1], str(installed_script)), installed_plugin, home


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_slot_actions_delegate_without_legacy_or_activation_mutation(
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
    assert (
        _legacy_footprint_snapshot(installed_plugin, tmp_path / "home")
        == legacy_before
    )


@pytest.mark.parametrize(
    "exemplar",
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_slot_actions_release_installed_payload_cwd_when_prestaged(
    exemplar: tuple[str, tuple[str, ...], str],
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
        "slot-provision",
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
    EXEMPLAR_INSTALLERS,
    ids=lambda exemplar: f"{exemplar[0]}-{exemplar[2]}",
)
def test_exemplar_slot_actions_do_not_adopt_ambient_context(
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
    _stamp_with_python(layout, snapshot_id=version)

    result = _run_exemplar_slot_action(
        exemplar,
        "slot-provision",
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
    EXEMPLAR_INSTALLERS,
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
    EXEMPLAR_INSTALLERS,
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("producer", RUNNERS, ids=lambda runner: f"from-{runner[0]}")
@pytest.mark.parametrize("consumer", RUNNERS, ids=lambda runner: f"to-{runner[0]}")
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
    posix = next(runner for runner in RUNNERS if runner[0] == "posix")
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
    posix = next(runner for runner in RUNNERS if runner[0] == "posix")
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    real_sha256sum = shutil.which("sha256sum")
    assert real_sha256sum is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_sha256sum = fake_bin / "sha256sum"
    fake_sha256sum.write_text(
        "#!/bin/sh\n"
        'if [ "$#" -gt 0 ]; then exit 1; fi\n'
        f'exec "{real_sha256sum}"\n',
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_serializes_concurrent_publishers(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _run(runner, "snapshot-stamp", layout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda _: _run_slot(runner, "slot-provision", layout),
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
    plugin_root = Path(layout["plugin_root"])
    activation_paths = (
        plugin_root / "current-version",
        plugin_root / "last-known-good",
        plugin_root / "installation-activation.json",
    )

    first = _provision_slot_with_python(layout)
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

    second = _provision_slot_with_python(layout)
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

    assert second["slotChanged"] is False
    assert second["slotEmpty"] is False
    assert second["ownership"] == str(marker)
    assert validated["reason"] == "runtime-slot-ownership-valid"
    assert marker.read_bytes() == marker_bytes
    assert all(not path.exists() for path in activation_paths)


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_runtime_slot_actions_bind_expected_snapshot_payload_identity(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    expected_root = Path(layout["payload"])

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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
    _, prefix, style = runner
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{", "invalid json"),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance",'
            b'"schema":"copilot-extensions.snapshot-provenance","version":1}',
            "duplicate",
        ),
        (
            b"\xef\xbb\xbf"
            b'{"schema":"copilot-extensions.snapshot-provenance","version":1}',
            "invalid",
        ),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance","version":"1"}',
            "version",
        ),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance","version":2}',
            "version",
        ),
    ],
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "snapshot_id",
    (
        "../other",
        "..\\other",
        "nested/child",
        "nested\\child",
        "/absolute",
        "1.0.0\n",
        "1.0.0\r",
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
        next(runner for runner in RUNNERS if runner[0] == "powershell"),
        "snapshot-stamp",
        layout,
        check=False,
        **generations,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("+1", "generation"),
        (" 1", "generation"),
        ("1_0", "generation"),
        ("\u0661", "generation"),
        ("9223372036854775808", "portable signed 64-bit maximum"),
        ("10000000000000000000", "portable signed 64-bit maximum"),
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
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
    runner = next(candidate for candidate in RUNNERS if candidate[0] == "powershell")

    result = _run(runner, "snapshot-stamp", layout, check=False)

    assert result.returncode != 0
    assert "invalid utf-8 in installation lock owner receipt" in result.stderr.lower()
    assert "decoderfallbackexception" not in result.stderr.lower()
