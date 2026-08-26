"""Tests for the non-operative installation-context foundation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
SCRIPT = LIB / "installation-context.ps1"
FIXTURES = LIB / "fixtures" / "source-identities.json"
POWERSHELL_HOSTS = list(
    dict.fromkeys(
        host
        for host in (shutil.which("pwsh"), shutil.which("powershell"))
        if host is not None
    )
)
POWERSHELL = POWERSHELL_HOSTS[0] if POWERSHELL_HOSTS else None


def _record(kind: str, canonical: str, ref: str) -> str:
    fields = (
        ("version", "1"),
        ("kind", kind),
        ("source", canonical),
        ("ref", ref),
    )
    return "".join(
        f"{name}:{len(value.encode('utf-8'))}:{value}\n" for name, value in fields
    )


def _slug(value: str) -> str:
    result = []
    previous_dash = False
    for character in value.lower():
        if character.isascii() and character.isalnum():
            result.append(character)
            previous_dash = False
        elif result and not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-") or "marketplace"


def _vectors() -> list[dict[str, object]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["vectors"]


def _run_ps(
    *arguments: object,
    env: dict[str, str] | None = None,
    check: bool = True,
    host: str | None = None,
) -> subprocess.CompletedProcess[str]:
    selected_host = host or POWERSHELL
    assert selected_host
    command = [selected_host, "-NoProfile", "-File", str(SCRIPT)]
    command.extend(str(argument) for argument in arguments)
    process_env = os.environ.copy()
    process_env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    process_env.pop("COPILOT_PLUGIN_ROOT", None)
    if env:
        process_env.update(env)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=process_env,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"PowerShell failed ({result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _settings(path: Path, key: str, descriptor: dict[str, str]) -> None:
    _write_json(
        path,
        {"extraKnownMarketplaces": {key: {"source": descriptor}}},
    )


def _installed_layout(
    tmp_path: Path,
    descriptor: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    copilot_home = tmp_path / "copilot"
    payload = copilot_home / "installed-plugins" / "example" / "agent-example"
    payload.mkdir(parents=True)
    durable = tmp_path / "durable"
    if descriptor is not None:
        _settings(copilot_home / "settings.json", "example", descriptor)
    return copilot_home, payload, durable


def _resolve(
    copilot_home: Path,
    payload: Path,
    durable: Path,
    *extra: object,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_ps(
        "resolve",
        "-CopilotHome",
        copilot_home,
        "-PayloadRoot",
        payload,
        "-DurableHome",
        durable,
        *extra,
        check=check,
        env=env,
    )


def _receipt_layout(tmp_path: Path) -> dict[str, Path | str]:
    vector = _vectors()[0]
    marketplace_id = str(vector["marketplaceId"])
    plugin_id = "agent-example"
    durable = tmp_path / "durable"
    cell = durable / "marketplaces" / marketplace_id
    plugin_root = cell / "plugins" / plugin_id
    payload = tmp_path / "payload"
    payload.mkdir()
    plugin_root.mkdir(parents=True)
    namespace = cell / "namespace.json"
    install = plugin_root / "install.json"
    normalized = vector["normalized"]
    assert isinstance(normalized, dict)
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
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
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
                "version": "1.0.0",
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
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    )
    return {
        "marketplace_id": marketplace_id,
        "plugin_id": plugin_id,
        "durable": durable,
        "cell": cell,
        "plugin_root": plugin_root,
        "payload": payload,
        "namespace": namespace,
        "install": install,
    }


def test_fixture_constants_are_independently_reproducible() -> None:
    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert fixture["encoding"] == "UTF-8-no-BOM"
    assert fixture["lineEnding"] == "LF"
    assert fixture["fieldOrder"] == ["version", "kind", "source", "ref"]
    for vector in fixture["vectors"]:
        normalized = vector["normalized"]
        record = _record(
            normalized["kind"],
            normalized["canonical"],
            normalized["ref"],
        )
        digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
        assert record == vector["record"]
        assert record.startswith("version:1:1\n")
        assert digest == vector["sha256"]
        expected_id = f"{_slug(vector['marketplaceKey'])}--{digest[:16]}"
        assert expected_id == vector["marketplaceId"]
        assert not FIXTURES.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("powershell_host", POWERSHELL_HOSTS or [None])
def test_powershell_matches_portable_source_vectors(
    powershell_host: str | None,
) -> None:
    if powershell_host is None:
        pytest.skip("PowerShell is not installed")
    for vector in _vectors():
        result = _run_ps(
            "source-id",
            "-SourceJson",
            json.dumps(vector["descriptor"], separators=(",", ":")),
            "-MarketplaceKey",
            vector["marketplaceKey"],
            host=powershell_host,
        )
        actual = json.loads(result.stdout)
        assert actual["kind"] == vector["normalized"]["kind"]
        assert actual["canonical"] == vector["normalized"]["canonical"]
        assert actual["ref"] == vector["normalized"]["ref"]
        assert actual["record"] == vector["record"]
        assert actual["sha256"] == vector["sha256"]
        assert actual["marketplaceId"] == vector["marketplaceId"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_resolves_installed_payload_and_only_computes_target_paths(
    tmp_path: Path,
) -> None:
    copilot, payload, durable = _installed_layout(
        tmp_path,
        {"source": "github", "repo": "example-org/example-marketplace.git"},
    )
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    result = _resolve(copilot, payload, durable)
    actual = json.loads(result.stdout)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert actual["marketplaceId"] == _vectors()[0]["marketplaceId"]
    assert actual["pluginId"] == "agent-example"
    assert actual["locator"]["kind"] == "installed"
    assert Path(actual["pluginRoot"]) == (
        durable
        / "marketplaces"
        / str(_vectors()[0]["marketplaceId"])
        / "plugins"
        / "agent-example"
    ).resolve()
    assert actual["operative"] is False
    assert before == after
    assert not durable.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_conflicting_same_key_declarations_fail_closed(tmp_path: Path) -> None:
    copilot, payload, durable = _installed_layout(
        tmp_path,
        {"source": "github", "repo": "example-org/one"},
    )
    project = tmp_path / "project"
    project.mkdir()
    _settings(
        project / ".github" / "copilot" / "settings.json",
        "example",
        {"source": "github", "repo": "example-org/two"},
    )
    result = _resolve(
        copilot,
        payload,
        durable,
        "-ProjectRoot",
        project,
        check=False,
    )
    assert result.returncode != 0
    assert "Conflicting declarations" in result.stderr
    assert not durable.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_project_directory_declaration_is_relative_to_project_root(
    tmp_path: Path,
) -> None:
    copilot, payload, durable = _installed_layout(tmp_path)
    project = tmp_path / "project"
    marketplace = project / ".ai"
    marketplace.mkdir(parents=True)
    _settings(
        project / ".github" / "copilot" / "settings.json",
        "example",
        {"source": "directory", "path": "./.ai"},
    )
    result = _resolve(
        copilot,
        payload,
        durable,
        "-ProjectRoot",
        project,
    )
    actual = json.loads(result.stdout)
    assert actual["source"]["canonical"] == f"directory:{marketplace.resolve()}"


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_claude_local_settings_and_local_source_alias_are_supported(
    tmp_path: Path,
) -> None:
    copilot, payload, durable = _installed_layout(tmp_path)
    project = tmp_path / "project"
    marketplace = project / ".ai"
    marketplace.mkdir(parents=True)
    _settings(
        project / ".claude" / "settings.local.json",
        "example",
        {"source": "local", "path": "./.ai"},
    )
    result = _resolve(
        copilot,
        payload,
        durable,
        "-ProjectRoot",
        project,
    )
    actual = json.loads(result.stdout)
    assert actual["source"]["kind"] == "directory"
    assert actual["source"]["canonical"] == f"directory:{marketplace.resolve()}"


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_missing_installed_provenance_fails_closed(tmp_path: Path) -> None:
    copilot, payload, durable = _installed_layout(tmp_path)
    result = _resolve(copilot, payload, durable, check=False)
    assert result.returncode != 0
    assert "No user or explicit project" in result.stderr
    assert not durable.exists()


@pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="Missing-drive behavior requires native Windows",
)
def test_must_exist_rejects_missing_drive_root(tmp_path: Path) -> None:
    missing_drive = next(
        (
            f"{letter}:\\"
            for letter in "ZYXWVUTSRQPONMLKJIHGFED"
            if not Path(f"{letter}:\\").exists()
        ),
        None,
    )
    if missing_drive is None:
        pytest.skip("no unused drive letter is available")
    result = _run_ps(
        "resolve",
        "-PayloadRoot",
        missing_drive,
        "-PluginId",
        "agent-example",
        "-SourceJson",
        json.dumps({"source": "opaque", "id": "missing-drive"}),
        check=False,
    )
    assert result.returncode != 0
    assert "Path does not exist" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_directory_marketplace_manifest_must_resolve_payload(tmp_path: Path) -> None:
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "payloads" / "plugins" / "agent-example"
    payload.mkdir(parents=True)
    _write_json(
        marketplace / ".plugin" / "marketplace.json",
        {
            "name": "Example Directory",
            "metadata": {"pluginRoot": "payloads"},
            "plugins": [{"name": "agent-example", "source": "plugins/agent-example"}],
        },
    )
    copilot = tmp_path / "copilot"
    durable = tmp_path / "durable"
    result = _resolve(
        copilot,
        payload,
        durable,
        "-PluginId",
        "agent-example",
    )
    actual = json.loads(result.stdout)
    assert actual["source"]["kind"] == "directory"
    assert actual["source"]["canonical"].startswith("directory:")
    assert actual["locator"]["kind"] == "directory"
    assert Path(actual["locator"]["marketplaceRoot"]) == marketplace.resolve()
    assert not durable.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    ("plugin_root", "plugin_source"),
    [
        ("../marketplace/payloads", "agent-example"),
        ("payloads", "../payloads/agent-example"),
    ],
)
def test_directory_marketplace_rejects_manifest_path_escape(
    tmp_path: Path,
    plugin_root: str,
    plugin_source: str,
) -> None:
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "payloads" / "agent-example"
    payload.mkdir(parents=True)
    _write_json(
        marketplace / "marketplace.json",
        {
            "name": "Escaping Directory",
            "metadata": {"pluginRoot": plugin_root},
            "plugins": [{"name": "agent-example", "source": plugin_source}],
        },
    )
    result = _resolve(
        tmp_path / "copilot",
        payload,
        tmp_path / "durable",
        "-PluginId",
        "agent-example",
        check=False,
    )
    assert result.returncode != 0
    assert "may not escape" in result.stderr or "must be relative" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_canonical_receipt_validates_and_context_pointer_is_not_identity(
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    result = _run_ps(
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        "-ExpectedMarketplaceId",
        layout["marketplace_id"],
        "-ExpectedPluginId",
        layout["plugin_id"],
        "-ExpectedPayloadRoot",
        layout["payload"],
        "-ExpectedCellRoot",
        layout["cell"],
    )
    actual = json.loads(result.stdout)
    assert actual["pluginRoot"] == str(Path(layout["plugin_root"]).resolve())
    assert actual["sourceFingerprint"] == f"sha256:{_vectors()[0]['sha256']}"
    assert actual["generation"] == 2


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_copied_or_mislocated_receipt_is_rejected(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    copied = tmp_path / "copied" / "install.json"
    copied.parent.mkdir()
    shutil.copyfile(layout["install"], copied)
    result = _run_ps(
        "validate",
        "-Context",
        copied,
        "-DurableHome",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "exact canonical receipt location" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_resolve_rejects_unbound_inherited_context(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    result = _run_ps(
        "resolve",
        "-DurableHome",
        layout["durable"],
        env={"COPILOT_EXTENSIONS_CONTEXT": str(layout["install"])},
        check=False,
    )
    assert result.returncode != 0
    assert "requires an expected plugin id" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_escaping_relative_root_is_rejected(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    receipt = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    receipt["roots"]["state"] = "../shared"
    _write_json(Path(layout["install"]), receipt)
    result = _run_ps(
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "roots.state" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    ("argument", "value_name", "message"),
    [
        ("-ExpectedPayloadRoot", "wrong_payload", "Expected payload"),
        ("-ExpectedPluginId", "wrong_plugin", "Expected plugin"),
        ("-ExpectedCellRoot", "wrong_cell", "Expected cell"),
    ],
)
def test_expected_payload_plugin_and_cell_mismatches_are_rejected(
    tmp_path: Path,
    argument: str,
    value_name: str,
    message: str,
) -> None:
    layout = _receipt_layout(tmp_path)
    values = {
        "wrong_payload": tmp_path / "other-payload",
        "wrong_plugin": "other-plugin",
        "wrong_cell": tmp_path / "other-cell",
    }
    if isinstance(values[value_name], Path):
        values[value_name].mkdir()
    result = _run_ps(
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        argument,
        values[value_name],
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_conflicting_copilot_plugin_root_is_rejected(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    other_payload = tmp_path / "other-payload"
    other_payload.mkdir()
    result = _run_ps(
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        env={"COPILOT_PLUGIN_ROOT": str(other_payload)},
        check=False,
    )
    assert result.returncode != 0
    assert "COPILOT_PLUGIN_ROOT conflicts" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_existing_source_cell_requires_explicit_rebind_intent(
    tmp_path: Path,
) -> None:
    vector = _vectors()[0]
    payload = tmp_path / "payload"
    payload.mkdir()
    durable = tmp_path / "durable"
    old_id = f"former-key--{str(vector['sha256'])[:16]}"
    _write_json(
        durable / "marketplaces" / old_id / "namespace.json",
        {
            "schema": "copilot-extensions.marketplace-namespace",
            "version": 1,
            "marketplaceId": old_id,
            "source": {
                **vector["normalized"],
                "fingerprint": f"sha256:{vector['sha256']}",
            },
            "locators": [],
            "generation": 1,
            "state": "active",
        },
    )
    result = _run_ps(
        "resolve",
        "-PayloadRoot",
        payload,
        "-DurableHome",
        durable,
        "-PluginId",
        "agent-example",
        "-MarketplaceKey",
        "new-key",
        "-SourceJson",
        json.dumps(vector["descriptor"], separators=(",", ":")),
        check=False,
    )
    assert result.returncode != 0
    assert "explicit rebind or new-cell intent is required" in result.stderr
    assert not (durable / "marketplaces" / "new-key").exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_existing_source_scan_rejects_forged_fingerprint(tmp_path: Path) -> None:
    vector = _vectors()[0]
    payload = tmp_path / "payload"
    payload.mkdir()
    durable = tmp_path / "durable"
    forged_id = f"forged--{str(vector['sha256'])[:16]}"
    _write_json(
        durable / "marketplaces" / forged_id / "namespace.json",
        {
            "schema": "copilot-extensions.marketplace-namespace",
            "version": 1,
            "marketplaceId": forged_id,
            "source": {
                "kind": "opaque",
                "canonical": "opaque:not-the-requested-source",
                "ref": "",
                "fingerprint": f"sha256:{vector['sha256']}",
            },
            "locators": [],
            "generation": 1,
            "state": "active",
        },
    )
    result = _run_ps(
        "resolve",
        "-PayloadRoot",
        payload,
        "-DurableHome",
        durable,
        "-PluginId",
        "agent-example",
        "-MarketplaceKey",
        "new-key",
        "-SourceJson",
        json.dumps(vector["descriptor"], separators=(",", ":")),
        check=False,
    )
    assert result.returncode != 0
    assert (
        "fingerprint does not match" in result.stderr
        or "id does not match its normalized source" in result.stderr
    )


@pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="Windows junction behavior requires native Windows",
)
def test_existing_junction_root_cannot_escape_plugin_root(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    state = Path(layout["plugin_root"]) / "state"
    command = ["cmd.exe", "/d", "/c", "mklink", "/J", str(state), str(outside)]
    created = subprocess.run(command, capture_output=True, text=True, check=False)
    if created.returncode != 0:
        pytest.skip(f"cannot create junction: {created.stderr or created.stdout}")
    result = _run_ps(
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "escapes pluginRoot" in result.stderr


@pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="Windows path case behavior requires native Windows",
)
def test_windows_directory_identity_is_case_stable(tmp_path: Path) -> None:
    marketplace = tmp_path / "CaseSensitiveSpelling"
    marketplace.mkdir()
    descriptor = {"source": "directory", "path": str(marketplace)}
    first = _run_ps(
        "source-id",
        "-SourceJson",
        json.dumps(descriptor),
        "-MarketplaceKey",
        "case",
    )
    second = _run_ps(
        "source-id",
        "-SourceJson",
        json.dumps(
            {"source": "directory", "path": str(marketplace).swapcase()},
        ),
        "-MarketplaceKey",
        "case",
    )
    assert json.loads(first.stdout)["marketplaceId"] == json.loads(second.stdout)[
        "marketplaceId"
    ]
