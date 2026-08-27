"""Python and dependency-light POSIX installation-context parity tests."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
PYTHON_SCRIPT = LIB / "installation_context.py"
POSIX_SCRIPT = LIB / "installation-context.sh"
FIXTURES = LIB / "fixtures" / "source-identities.json"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
POWERSHELL_COMMAND = (
    (
        str(POWERSHELL),
        "-NoProfile",
        "-File",
        str(LIB / "installation-context.ps1"),
    )
    if POWERSHELL is not None
    else None
)
RUNNERS = (
    ("python", (sys.executable, str(PYTHON_SCRIPT))),
    ("posix", (str(POSIX_SCRIPT),)),
)
PYTHON_COMMAND = RUNNERS[0][1]


def _vectors() -> list[dict[str, object]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["vectors"]


def _run(
    command: tuple[str, ...],
    *arguments: object,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    process_env.pop("COPILOT_PLUGIN_ROOT", None)
    if env:
        process_env.update(env)
    result = subprocess.run(
        [*command, *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=process_env,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"{command[0]} failed ({result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _settings(path: Path, key: str, descriptor: dict[str, str]) -> None:
    _write_json(path, {"extraKnownMarketplaces": {key: {"source": descriptor}}})


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


def _receipt_layout(tmp_path: Path) -> dict[str, Path | str]:
    vector = _vectors()[0]
    normalized = vector["normalized"]
    assert isinstance(normalized, dict)
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


def _stamp_arguments(
    tmp_path: Path,
    *,
    expected_namespace_generation: int = 0,
    expected_install_generation: int = 0,
) -> tuple[list[object], dict[str, Path | str]]:
    vector = _vectors()[0]
    payload = tmp_path / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    durable = tmp_path / "durable"
    values: dict[str, Path | str] = {
        "payload": payload,
        "durable": durable,
        "marketplace_id": str(vector["marketplaceId"]),
        "plugin_id": "agent-example",
    }
    return (
        [
            "stamp",
            "--payload-root",
            payload,
            "--durable-home",
            durable,
            "--plugin-id",
            values["plugin_id"],
            "--marketplace-key",
            vector["marketplaceKey"],
            "--source-json",
            json.dumps(vector["descriptor"], separators=(",", ":")),
            "--payload-version",
            "1.0.0",
            "--payload-origin",
            "explicit",
            "--expected-namespace-generation",
            expected_namespace_generation,
            "--expected-install-generation",
            expected_install_generation,
        ],
        values,
    )


def test_python_stamp_creates_and_idempotently_validates_receipts(tmp_path: Path) -> None:
    arguments, values = _stamp_arguments(tmp_path)
    first = json.loads(_run(PYTHON_COMMAND, *arguments).stdout)
    assert first["action"] == "stamp"
    assert first["namespaceChanged"] is True
    assert first["installChanged"] is True
    assert first["namespaceGeneration"] == 1
    assert first["generation"] == 1
    assert first["operative"] is False

    namespace = Path(first["namespaceReceipt"])
    install = Path(first["installReceipt"])
    assert namespace.read_bytes().endswith(b"\n")
    assert install.read_bytes().endswith(b"\n")
    assert not namespace.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not install.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(namespace.read_text(encoding="utf-8"))["marketplaceId"] == values[
        "marketplace_id"
    ]

    repeat_arguments, _ = _stamp_arguments(
        tmp_path,
        expected_namespace_generation=1,
        expected_install_generation=1,
    )
    second = json.loads(_run(PYTHON_COMMAND, *repeat_arguments).stdout)
    assert second["namespaceChanged"] is False
    assert second["installChanged"] is False
    assert second["namespaceGeneration"] == 1
    assert second["generation"] == 1
    assert not list(Path(values["durable"]).rglob("*.tmp-*"))


def test_python_stamp_updates_receipt_with_generation_compare_and_swap(
    tmp_path: Path,
) -> None:
    arguments, _ = _stamp_arguments(tmp_path)
    first = json.loads(_run(PYTHON_COMMAND, *arguments).stdout)
    update_arguments, _ = _stamp_arguments(
        tmp_path,
        expected_namespace_generation=1,
        expected_install_generation=1,
    )
    update_arguments.extend(["--install-state", "inactive"])
    updated = json.loads(_run(PYTHON_COMMAND, *update_arguments).stdout)
    assert updated["namespaceGeneration"] == 1
    assert updated["generation"] == 2
    assert updated["state"] == "inactive"

    stale = _run(PYTHON_COMMAND, *update_arguments, check=False)
    assert stale.returncode != 0
    assert "generation changed" in stale.stderr
    receipt = json.loads(Path(first["installReceipt"]).read_text(encoding="utf-8"))
    assert receipt["generation"] == 2
    assert receipt["state"] == "inactive"


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_stamp_blocks_a_live_install_owner(
    tmp_path: Path,
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    arguments, values = _stamp_arguments(tmp_path / runner_name)
    lock = (
        Path(values["durable"])
        / "marketplaces"
        / str(values["marketplace_id"])
        / ".locks"
        / f"{values['plugin_id']}.install.lock"
    )
    lock.mkdir(parents=True)
    _write_json(
        lock / "owner.json",
        {
            "schema": "copilot-extensions.installation-lock",
            "version": 1,
            "kind": "install",
            "marketplaceId": values["marketplace_id"],
            "pluginId": values["plugin_id"],
            "token": "live-owner",
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "acquiredAt": "2026-01-01T00:00:00Z",
        },
    )
    result = _run(command, *arguments, check=False)
    assert result.returncode != 0
    assert "busy" in result.stderr
    assert not (
        Path(values["durable"])
        / "marketplaces"
        / str(values["marketplace_id"])
        / "plugins"
        / str(values["plugin_id"])
        / "install.json"
    ).exists()


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_stamp_fails_closed_on_a_dead_install_owner(
    tmp_path: Path,
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    arguments, values = _stamp_arguments(tmp_path / runner_name)
    lock = (
        Path(values["durable"])
        / "marketplaces"
        / str(values["marketplace_id"])
        / ".locks"
        / f"{values['plugin_id']}.install.lock"
    )
    lock.mkdir(parents=True)
    _write_json(
        lock / "owner.json",
        {
            "schema": "copilot-extensions.installation-lock",
            "version": 1,
            "kind": "install",
            "marketplaceId": values["marketplace_id"],
            "pluginId": values["plugin_id"],
            "token": "dead-owner",
            "host": socket.gethostname(),
            "pid": 2147483647,
            "acquiredAt": "2026-01-01T00:00:00Z",
        },
    )
    result = _run(command, *arguments, check=False)
    assert result.returncode != 0
    assert "stale owner" in result.stderr
    assert lock.exists()


@pytest.mark.parametrize(("runner_name", "runner_command"), RUNNERS)
def test_concurrent_first_stamp_leaves_one_untorn_receipt(
    tmp_path: Path,
    runner_name: str,
    runner_command: tuple[str, ...],
) -> None:
    arguments, values = _stamp_arguments(tmp_path / runner_name)
    command = [*runner_command, *(str(argument) for argument in arguments)]
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    processes = [
        subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    return_codes = [process.returncode for process in processes]
    assert sorted(return_codes) == [0, 1], results
    failure = results[return_codes.index(1)][1]
    assert "generation changed" in failure

    install = (
        Path(values["durable"])
        / "marketplaces"
        / str(values["marketplace_id"])
        / "plugins"
        / str(values["plugin_id"])
        / "install.json"
    )
    validated = _run(
        runner_command,
        "validate",
        "--context",
        install,
        "--durable-home",
        values["durable"],
        "--expected-marketplace-id",
        values["marketplace_id"],
        "--expected-plugin-id",
        values["plugin_id"],
    )
    assert json.loads(validated.stdout)["generation"] == 1


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_stamp_creation_and_idempotence_match_across_runners(
    tmp_path: Path,
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    arguments, _ = _stamp_arguments(tmp_path / runner_name)
    first = json.loads(_run(command, *arguments).stdout)
    assert first["namespaceChanged"] is True
    assert first["installChanged"] is True
    assert first["namespaceGeneration"] == 1
    assert first["generation"] == 1
    repeat_arguments, _ = _stamp_arguments(
        tmp_path / runner_name,
        expected_namespace_generation=1,
        expected_install_generation=1,
    )
    second = json.loads(_run(command, *repeat_arguments).stdout)
    assert second["namespaceChanged"] is False
    assert second["installChanged"] is False
    assert second["namespaceGeneration"] == 1
    assert second["generation"] == 1


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_stamp_generation_conflict_matches_across_runners(
    tmp_path: Path,
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    arguments, _ = _stamp_arguments(tmp_path / runner_name)
    _run(command, *arguments)
    stale = _run(command, *arguments, check=False)
    assert stale.returncode != 0
    assert "generation changed" in stale.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_source_identity_matches_portable_vectors(
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    del runner_name
    for vector in _vectors():
        result = _run(
            command,
            "source-id",
            "--source-json",
            json.dumps(vector["descriptor"], separators=(",", ":")),
            "--marketplace-key",
            vector["marketplaceKey"],
        )
        actual = json.loads(result.stdout)
        assert actual["kind"] == vector["normalized"]["kind"]
        assert actual["canonical"] == vector["normalized"]["canonical"]
        assert actual["ref"] == vector["normalized"]["ref"]
        assert actual["record"] == vector["record"]
        assert actual["sha256"] == vector["sha256"]
        assert actual["marketplaceId"] == vector["marketplaceId"]


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_all_implementations_match_the_full_source_vector_corpus() -> None:
    assert POWERSHELL_COMMAND is not None
    for vector in _vectors():
        commands_and_flags = [
            (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
            *[
                (command, "--source-json", "--marketplace-key")
                for _, command in RUNNERS
            ],
        ]
        outputs = []
        for command, source_flag, key_flag in commands_and_flags:
            result = _run(
                command,
                "source-id",
                source_flag,
                json.dumps(vector["descriptor"], separators=(",", ":")),
                key_flag,
                vector["marketplaceKey"],
            )
            outputs.append(json.loads(result.stdout))
        assert outputs[0] == outputs[1] == outputs[2]


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_unicode_json_input_and_output_are_utf8(
    runner_name: str,
    command: tuple[str, ...],
) -> None:
    del runner_name
    result = _run(
        command,
        "source-id",
        "--source-json",
        json.dumps({"source": "opaque", "id": "urn:example:caf\u00e9"}),
        "--marketplace-key",
        "Unicode",
    )
    assert json.loads(result.stdout)["canonical"] == "opaque:urn:example:caf\u00e9"


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "descriptor",
    [
        {
            "source": "git",
            "url": "https://build@example.com/Org/A%2fB%20C.git?ignored=1#fragment",
        },
        {"source": "git", "url": "git@Example.com:Org/Repo.git"},
        {"source": "github", "url": "HTTPS://GITHUB.COM/Example/Repo.git"},
        {"source": "directory", "stableId": "portable-marketplace"},
        {"source": "opaque", "id": "urn:example:caf\u00e9"},
    ],
)
def test_python_and_posix_source_normalization_matches_powershell(
    descriptor: dict[str, str],
) -> None:
    source_json = json.dumps(descriptor, separators=(",", ":"))
    powershell = _run(
        (
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(LIB / "installation-context.ps1"),
        ),
        "source-id",
        "-SourceJson",
        source_json,
        "-MarketplaceKey",
        "Parity",
    )
    expected = json.loads(powershell.stdout)
    for _, command in RUNNERS:
        actual = json.loads(
            _run(
                command,
                "source-id",
                "--source-json",
                source_json,
                "--marketplace-key",
                "Parity",
            ).stdout
        )
        assert actual == expected


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_non_string_source_fields_fail_closed_on_all_platforms() -> None:
    descriptor = {"source": "github", "repo": "example/example", "ref": True}
    source_json = json.dumps(descriptor, separators=(",", ":"))
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "source-id",
                "-SourceJson",
                source_json,
                "-MarketplaceKey",
                "Types",
            ),
        ),
        *[
            (
                command,
                (
                    "source-id",
                    "--source-json",
                    source_json,
                    "--marketplace-key",
                    "Types",
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        assert command is not None
        result = _run(command, *arguments, check=False)
        assert result.returncode != 0
        assert "must be a string" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "descriptor",
    [
        {"source": "opaque", "id": "x", "Ref": "r"},
        {
            "source": "github",
            "repo": "example/example",
            "Canonical": "github:other/other",
        },
        {"Source": "github", "Repo": "example/example"},
        {"source": "directory", "StableId": "portable"},
    ],
)
def test_case_variant_source_properties_fail_closed_everywhere(
    descriptor: dict[str, str],
) -> None:
    assert POWERSHELL_COMMAND is not None
    source_json = json.dumps(descriptor, separators=(",", ":"))
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_json,
            key_flag,
            "Case",
            check=False,
        )
        assert result.returncode != 0
        assert "exact case" in result.stderr


def test_literal_dollar_in_directory_path_is_not_environment_expansion(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "lit$ICBASE"
    directory.mkdir()
    descriptor = {"source": "directory", "path": str(directory)}
    outputs = []
    for _, command in RUNNERS:
        result = _run(
            command,
            "source-id",
            "--source-json",
            json.dumps(descriptor),
            "--marketplace-key",
            "Literal",
            env={"ICBASE": ""},
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0]["canonical"] == f"directory:{directory.resolve()}"


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "url",
    [
        "https://ho\tst.example/a",
        "https://host.example/a\nb",
        "https://bad host.example/a",
    ],
)
def test_invalid_git_hosts_and_control_characters_fail_closed_everywhere(
    url: str,
) -> None:
    assert POWERSHELL_COMMAND is not None
    descriptor = json.dumps({"source": "git", "url": url}, separators=(",", ":"))
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            descriptor,
            key_flag,
            "Invalid",
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a%zz%41b",
        "https://example.com/a%2",
        "https://example.com/100%",
        "https://example.com/a%%41",
    ],
)
def test_malformed_percent_escapes_fail_closed_everywhere(url: str) -> None:
    assert POWERSHELL_COMMAND is not None
    source_json = json.dumps({"source": "git", "url": url}, separators=(",", ":"))
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_json,
            key_flag,
            "Malformed",
            check=False,
        )
        assert result.returncode != 0
        assert "malformed percent-escape" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_source_files_with_utf8_bom_fail_closed_everywhere(tmp_path: Path) -> None:
    assert POWERSHELL_COMMAND is not None
    source_file = tmp_path / "source.json"
    source_file.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"source": "opaque", "id": "bom"}).encode()
    )
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceFile", "-MarketplaceKey"),
        *[
            (command, "--source-file", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_file,
            key_flag,
            "BOM",
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "content",
    [
        b'{"source":"opaque","id":"first","id":"second"}',
        b'{"source":"opaque","id":"raw\nnewline"}',
        b'{"source":"opaque","id":"raw\x01control"}',
        b'{"source":"opaque","id":"invalid-\xff"}',
    ],
)
def test_strict_json_language_is_shared_by_all_implementations(
    content: bytes,
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    source_file = tmp_path / "source.json"
    source_file.write_bytes(content)
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceFile", "-MarketplaceKey"),
        *[
            (command, "--source-file", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_file,
            key_flag,
            "Strict",
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_unexpected_settings_json_type_fails_with_controlled_error(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    copilot, payload, durable = _installed_layout(tmp_path)
    (copilot / "settings.json").write_text("[]\n", encoding="utf-8")
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        copilot,
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        check=False,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_nul_source_values_fail_closed_everywhere() -> None:
    assert POWERSHELL_COMMAND is not None
    source_json = json.dumps({"source": "opaque", "id": "a\0b"})
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_json,
            key_flag,
            "NUL",
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_readable_slug_is_ascii_and_locale_independent() -> None:
    assert POWERSHELL_COMMAND is not None
    source_json = json.dumps({"source": "opaque", "id": "slug"})
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    outputs = []
    for command, source_flag, key_flag in commands_and_flags:
        outputs.append(
            json.loads(
                _run(
                    command,
                    "source-id",
                    source_flag,
                    source_json,
                    key_flag,
                    "\u0130stanbul",
                ).stdout
            )
        )
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0]["marketplaceId"].startswith("stanbul--")


def test_python_and_posix_installed_resolution_match(tmp_path: Path) -> None:
    copilot, payload, durable = _installed_layout(
        tmp_path,
        {"source": "github", "repo": "example-org/example-marketplace.git"},
    )
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    outputs = []
    for _, command in RUNNERS:
        result = _run(
            command,
            "resolve",
            "--copilot-home",
            copilot,
            "--payload-root",
            payload,
            "--durable-home",
            durable,
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0]["marketplaceId"] == _vectors()[0]["marketplaceId"]
    assert outputs[0]["operative"] is False
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert before == after
    assert not durable.exists()


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_conflicting_same_key_declarations_fail_closed(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
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
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        copilot,
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        "--project-root",
        project,
        check=False,
    )
    assert result.returncode != 0
    assert "Conflicting declarations" in result.stderr
    assert not durable.exists()


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_unparseable_conflicting_declaration_cannot_be_skipped(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    copilot, payload, durable = _installed_layout(
        tmp_path,
        {"source": "github", "repo": "example-org/one"},
    )
    project = tmp_path / "project"
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '{"extraKnownMarketplaces":{"example":{"source":'
        '{"source":"github","repo":"example-org/two"},'
        '"source":{"source":"github","repo":"example-org/three"}}}}',
        encoding="utf-8",
    )
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        copilot,
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        "--project-root",
        project,
        check=False,
    )
    assert result.returncode != 0
    assert not durable.exists()


def test_python_and_posix_directory_resolution_match(tmp_path: Path) -> None:
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
    outputs = []
    for _, command in RUNNERS:
        result = _run(
            command,
            "resolve",
            "--copilot-home",
            tmp_path / "copilot",
            "--payload-root",
            payload,
            "--durable-home",
            tmp_path / "durable",
            "--plugin-id",
            "agent-example",
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0]["source"]["canonical"] == f"directory:{marketplace.resolve()}"
    assert outputs[0]["locator"]["marketplaceRoot"] == str(marketplace.resolve())


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_directory_marketplace_symlink_escape_fails_closed_everywhere(
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    marketplace = tmp_path / "marketplace"
    outside = tmp_path / "outside" / "agent-example"
    outside.mkdir(parents=True)
    plugin_link = marketplace / "plugins" / "agent-example"
    plugin_link.parent.mkdir(parents=True)
    try:
        plugin_link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"cannot create symlink: {error}")
    _write_json(
        marketplace / "marketplace.json",
        {
            "name": "Escaping",
            "plugins": [{"name": "agent-example", "source": "plugins/agent-example"}],
        },
    )
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "resolve",
                "-CopilotHome",
                tmp_path / "copilot",
                "-PayloadRoot",
                plugin_link,
                "-DurableHome",
                tmp_path / "durable",
                "-PluginId",
                "agent-example",
            ),
        ),
        *[
            (
                command,
                (
                    "resolve",
                    "--copilot-home",
                    tmp_path / "copilot",
                    "--payload-root",
                    plugin_link,
                    "--durable-home",
                    tmp_path / "durable",
                    "--plugin-id",
                    "agent-example",
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        result = _run(command, *arguments, check=False)
        assert result.returncode != 0
        assert "Cannot establish marketplace provenance" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    "url",
    [
        "https://[:::]/a",
        "https://[dead:beef]/a",
        "https://example.com:9223372036854775808/a",
        "https://example.com:18446744073709551616/a",
    ],
)
def test_invalid_ipv6_and_overflow_ports_fail_closed_everywhere(url: str) -> None:
    assert POWERSHELL_COMMAND is not None
    source_json = json.dumps({"source": "git", "url": url})
    commands_and_flags = [
        (POWERSHELL_COMMAND, "-SourceJson", "-MarketplaceKey"),
        *[
            (command, "--source-json", "--marketplace-key")
            for _, command in RUNNERS
        ],
    ]
    for command, source_flag, key_flag in commands_and_flags:
        result = _run(
            command,
            "source-id",
            source_flag,
            source_json,
            key_flag,
            "Invalid",
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_unrecognized_payload_has_actionable_provenance_error(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    payload = tmp_path / "payload"
    payload.mkdir()
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        tmp_path / "copilot",
        "--durable-home",
        tmp_path / "durable",
        "--payload-root",
        payload,
        "--plugin-id",
        "agent-example",
        check=False,
    )
    assert result.returncode != 0
    assert "Cannot establish marketplace provenance" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_project_directory_declaration_and_local_alias(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    copilot, payload, durable = _installed_layout(tmp_path)
    project = tmp_path / "project"
    marketplace = project / ".ai"
    marketplace.mkdir(parents=True)
    _settings(
        project / ".claude" / "settings.local.json",
        "example",
        {"source": "local", "path": "./.ai"},
    )
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        copilot,
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        "--project-root",
        project,
    )
    actual = json.loads(result.stdout)
    assert actual["source"]["kind"] == "directory"
    assert actual["source"]["canonical"] == f"directory:{marketplace.resolve()}"


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_missing_or_invalid_source_evidence_fails_closed(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    copilot, payload, durable = _installed_layout(tmp_path)
    missing = _run(
        command,
        "resolve",
        "--copilot-home",
        copilot,
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        check=False,
    )
    assert missing.returncode != 0
    assert "No user or explicit project" in missing.stderr

    payload_file = tmp_path / "payload.txt"
    payload_file.write_text("not a directory", encoding="utf-8")
    invalid_payload = _run(
        command,
        "resolve",
        "--payload-root",
        payload_file,
        "--plugin-id",
        "agent-example",
        "--source-json",
        json.dumps({"source": "opaque", "id": "file-payload"}),
        check=False,
    )
    assert invalid_payload.returncode != 0
    assert "must be an existing directory" in invalid_payload.stderr

    for descriptor in (
        {"source": "directory"},
        {"source": "directory", "stableId": "   "},
        {"source": "directory", "canonical": "directory-id:"},
    ):
        invalid_directory = _run(
            command,
            "source-id",
            "--source-json",
            json.dumps(descriptor),
            "--marketplace-key",
            "directory",
            check=False,
        )
        assert invalid_directory.returncode != 0
        assert "requires a non-empty" in invalid_directory.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
@pytest.mark.parametrize(
    ("plugin_root", "plugin_source"),
    [
        ("../marketplace/payloads", "agent-example"),
        ("payloads", "../payloads/agent-example"),
    ],
)
def test_directory_marketplace_rejects_manifest_path_escape(
    runner_name: str,
    command: tuple[str, ...],
    plugin_root: str,
    plugin_source: str,
    tmp_path: Path,
) -> None:
    del runner_name
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
    result = _run(
        command,
        "resolve",
        "--copilot-home",
        tmp_path / "copilot",
        "--payload-root",
        payload,
        "--durable-home",
        tmp_path / "durable",
        "--plugin-id",
        "agent-example",
        check=False,
    )
    assert result.returncode != 0
    assert "may not escape" in result.stderr or "must be relative" in result.stderr


def test_python_and_posix_receipt_validation_match(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    outputs = []
    for _, command in RUNNERS:
        result = _run(
            command,
            "validate",
            "--context",
            layout["install"],
            "--durable-home",
            layout["durable"],
            "--expected-marketplace-id",
            layout["marketplace_id"],
            "--expected-plugin-id",
            layout["plugin_id"],
            "--expected-payload-root",
            layout["payload"],
            "--expected-cell-root",
            layout["cell"],
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0]["pluginRoot"] == str(Path(layout["plugin_root"]).resolve())
    assert outputs[0]["generation"] == 2


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_namespace_receipt_requires_canonical_source_projection(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    namespace = json.loads(Path(layout["namespace"]).read_text(encoding="utf-8"))
    namespace["source"].pop("canonical")
    namespace["source"]["repo"] = "example-org/example-marketplace"
    _write_json(Path(layout["namespace"]), namespace)
    result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "canonical identity" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_powershell_namespace_receipt_requires_canonical_source_projection(
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    layout = _receipt_layout(tmp_path)
    namespace = json.loads(Path(layout["namespace"]).read_text(encoding="utf-8"))
    namespace["source"].pop("canonical")
    namespace["source"]["repo"] = "example-org/example-marketplace"
    _write_json(Path(layout["namespace"]), namespace)
    result = _run(
        POWERSHELL_COMMAND,
        "validate",
        "-Context",
        layout["install"],
        "-DurableHome",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "canonical identity" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pluginId", "agent-example\n"),
        ("payloadVersion", True),
        ("stateRoot", True),
    ],
)
def test_receipt_string_fields_are_lossless_and_typed_everywhere(
    field: str,
    value: object,
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    layout = _receipt_layout(tmp_path)
    receipt = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    if field == "pluginId":
        receipt["pluginId"] = value
    elif field == "payloadVersion":
        receipt["payload"]["version"] = value
    else:
        receipt["roots"]["state"] = value
    _write_json(Path(layout["install"]), receipt)
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "validate",
                "-Context",
                layout["install"],
                "-DurableHome",
                layout["durable"],
            ),
        ),
        *[
            (
                command,
                (
                    "validate",
                    "--context",
                    layout["install"],
                    "--durable-home",
                    layout["durable"],
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        result = _run(command, *arguments, check=False)
        assert result.returncode != 0


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("plugin_id", ["CON", "NUL.txt", "aux", "COM1", "lpt9.log"])
def test_windows_device_names_are_not_portable_plugin_ids(
    plugin_id: str,
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    payload = tmp_path / "payload"
    payload.mkdir()
    source_json = json.dumps({"source": "opaque", "id": "portable"})
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "resolve",
                "-PayloadRoot",
                payload,
                "-PluginId",
                plugin_id,
                "-SourceJson",
                source_json,
            ),
        ),
        *[
            (
                command,
                (
                    "resolve",
                    "--payload-root",
                    payload,
                    "--plugin-id",
                    plugin_id,
                    "--source-json",
                    source_json,
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        result = _run(command, *arguments, check=False)
        assert result.returncode != 0
        assert "Invalid filesystem-safe plugin id" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_explicit_context_requires_bound_identity_and_honors_expectations(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    unbound = _run(
        command,
        "resolve",
        "--durable-home",
        layout["durable"],
        env={"COPILOT_EXTENSIONS_CONTEXT": str(layout["install"])},
        check=False,
    )
    assert unbound.returncode != 0
    assert "requires an expected plugin id" in unbound.stderr

    wrong_payload = tmp_path / "wrong-payload"
    wrong_payload.mkdir()
    mismatch = _run(
        command,
        "resolve",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        "--expected-plugin-id",
        layout["plugin_id"],
        "--expected-payload-root",
        wrong_payload,
        check=False,
    )
    assert mismatch.returncode != 0
    assert "Expected payload" in mismatch.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_inherited_payload_root_binds_explicit_context(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    result = _run(
        command,
        "resolve",
        "--durable-home",
        layout["durable"],
        "--plugin-id",
        layout["plugin_id"],
        env={
            "COPILOT_EXTENSIONS_CONTEXT": str(layout["install"]),
            "COPILOT_PLUGIN_ROOT": str(layout["payload"]),
        },
    )
    assert json.loads(result.stdout)["action"] == "resolve"


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
@pytest.mark.parametrize(
    ("argument", "value_name", "message"),
    [
        ("--expected-payload-root", "wrong_payload", "Expected payload"),
        ("--expected-plugin-id", "wrong_plugin", "Expected plugin"),
        ("--expected-cell-root", "wrong_cell", "Expected cell"),
    ],
)
def test_expected_identity_mismatches_are_rejected(
    runner_name: str,
    command: tuple[str, ...],
    argument: str,
    value_name: str,
    message: str,
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    values: dict[str, Path | str] = {
        "wrong_payload": tmp_path / "other-payload",
        "wrong_plugin": "other-plugin",
        "wrong_cell": tmp_path / "other-cell",
    }
    value = values[value_name]
    if isinstance(value, Path):
        value.mkdir()
    result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        argument,
        value,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_conflicting_inherited_payload_root_is_rejected(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    other_payload = tmp_path / "other-payload"
    other_payload.mkdir()
    result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        env={"COPILOT_PLUGIN_ROOT": str(other_payload)},
        check=False,
    )
    assert result.returncode != 0
    assert "COPILOT_PLUGIN_ROOT conflicts" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_relative_inherited_payload_root_is_rejected_everywhere(
    tmp_path: Path,
) -> None:
    assert POWERSHELL_COMMAND is not None
    layout = _receipt_layout(tmp_path)
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "validate",
                "-Context",
                layout["install"],
                "-DurableHome",
                layout["durable"],
            ),
        ),
        *[
            (
                command,
                (
                    "validate",
                    "--context",
                    layout["install"],
                    "--durable-home",
                    layout["durable"],
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        result = _run(
            command,
            *arguments,
            env={"COPILOT_PLUGIN_ROOT": "relative-payload"},
            check=False,
        )
        assert result.returncode != 0
        assert "COPILOT_PLUGIN_ROOT must be absolute" in result.stderr


@pytest.mark.skipif(POWERSHELL_COMMAND is None, reason="PowerShell is not installed")
def test_validate_payload_root_alias_is_honored_everywhere(tmp_path: Path) -> None:
    assert POWERSHELL_COMMAND is not None
    layout = _receipt_layout(tmp_path)
    wrong_payload = tmp_path / "wrong-payload"
    wrong_payload.mkdir()
    commands_and_arguments = [
        (
            POWERSHELL_COMMAND,
            (
                "validate",
                "-Context",
                layout["install"],
                "-DurableHome",
                layout["durable"],
                "-PayloadRoot",
                wrong_payload,
            ),
        ),
        *[
            (
                command,
                (
                    "validate",
                    "--context",
                    layout["install"],
                    "--durable-home",
                    layout["durable"],
                    "--payload-root",
                    wrong_payload,
                ),
            )
            for _, command in RUNNERS
        ],
    ]
    for command, arguments in commands_and_arguments:
        result = _run(command, *arguments, check=False)
        assert result.returncode != 0
        assert "Expected payload" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_home_arguments_must_be_absolute(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    payload = tmp_path / "payload"
    payload.mkdir()
    result = _run(
        command,
        "resolve",
        "--payload-root",
        payload,
        "--plugin-id",
        "agent-example",
        "--source-json",
        json.dumps({"source": "opaque", "id": "example"}),
        "--copilot-home",
        "relative-copilot",
        check=False,
    )
    assert result.returncode != 0
    assert "must be absolute" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_receipt_integer_fields_reject_json_strings(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    receipt = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    receipt["generation"] = "2"
    _write_json(Path(layout["install"]), receipt)
    result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "generation" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 1.0),
        ("root", "..\\evil"),
    ],
)
def test_receipt_numeric_and_cross_platform_path_types_are_strict(
    runner_name: str,
    command: tuple[str, ...],
    field: str,
    value: object,
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    receipt = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    if field == "version":
        receipt["version"] = value
    else:
        receipt["roots"]["versions"] = value
    _write_json(Path(layout["install"]), receipt)
    result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        check=False,
    )
    assert result.returncode != 0
    assert "version" in result.stderr or "roots.versions" in result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_copied_receipt_and_escaping_root_are_rejected(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
    layout = _receipt_layout(tmp_path)
    copied = tmp_path / "copied" / "install.json"
    copied.parent.mkdir()
    shutil.copyfile(layout["install"], copied)
    copied_result = _run(
        command,
        "validate",
        "--context",
        copied,
        "--durable-home",
        layout["durable"],
        check=False,
    )
    assert copied_result.returncode != 0
    assert "exact canonical receipt location" in copied_result.stderr

    receipt = json.loads(Path(layout["install"]).read_text(encoding="utf-8"))
    receipt["roots"]["state"] = "../shared"
    _write_json(Path(layout["install"]), receipt)
    escaping_result = _run(
        command,
        "validate",
        "--context",
        layout["install"],
        "--durable-home",
        layout["durable"],
        check=False,
    )
    assert escaping_result.returncode != 0
    assert "roots.state" in escaping_result.stderr


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_existing_source_requires_explicit_rebind(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
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
    result = _run(
        command,
        "resolve",
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        "--plugin-id",
        "agent-example",
        "--marketplace-key",
        "new-key",
        "--source-json",
        json.dumps(vector["descriptor"], separators=(",", ":")),
        check=False,
    )
    assert result.returncode != 0
    assert "explicit rebind or new-cell intent is required" in result.stderr
    assert not (durable / "marketplaces" / f"new-key--{str(vector['sha256'])[:16]}").exists()


@pytest.mark.parametrize(("runner_name", "command"), RUNNERS)
def test_existing_source_scan_rejects_forged_fingerprint(
    runner_name: str,
    command: tuple[str, ...],
    tmp_path: Path,
) -> None:
    del runner_name
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
    result = _run(
        command,
        "resolve",
        "--payload-root",
        payload,
        "--durable-home",
        durable,
        "--plugin-id",
        "agent-example",
        "--marketplace-key",
        "new-key",
        "--source-json",
        json.dumps(vector["descriptor"], separators=(",", ":")),
        check=False,
    )
    assert result.returncode != 0
    assert (
        "fingerprint does not match" in result.stderr
        or "id does not match its normalized source" in result.stderr
    )


@pytest.mark.parametrize(
    "awk_command",
    [
        pytest.param(shutil.which("mawk"), id="mawk"),
        pytest.param(shutil.which("gawk"), id="gawk"),
        pytest.param(shutil.which("awk"), id="default-awk"),
    ],
)
def test_posix_source_resolution_does_not_require_python_or_jq(
    awk_command: str | None,
    tmp_path: Path,
) -> None:
    if awk_command is None:
        pytest.skip("requested awk implementation is unavailable")
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    (tool_dir / "awk").symlink_to(awk_command)
    required = (
        "basename",
        "dirname",
        "mktemp",
        "realpath",
        "rm",
        "sed",
        "sha256sum",
        "tr",
        "wc",
    )
    for name in required:
        source = shutil.which(name)
        if source is None:
            pytest.skip(f"required POSIX test tool is unavailable: {name}")
        (tool_dir / name).symlink_to(source)
    result = _run(
        (shutil.which("bash") or "/bin/bash", str(POSIX_SCRIPT)),
        "source-id",
        "--source-json",
        json.dumps(_vectors()[0]["descriptor"], separators=(",", ":")),
        "--marketplace-key",
        _vectors()[0]["marketplaceKey"],
        env={"PATH": str(tool_dir)},
    )
    assert json.loads(result.stdout)["marketplaceId"] == _vectors()[0]["marketplaceId"]
    unicode_result = _run(
        (shutil.which("bash") or "/bin/bash", str(POSIX_SCRIPT)),
        "source-id",
        "--source-json",
        json.dumps({"source": "opaque", "id": "urn:example:caf\u00e9"}),
        "--marketplace-key",
        "Unicode",
        env={"PATH": str(tool_dir), "LC_ALL": "C"},
    )
    assert json.loads(unicode_result.stdout)["canonical"] == "opaque:urn:example:caf\u00e9"


def test_posix_json_query_separator_cannot_forge_nested_paths(tmp_path: Path) -> None:
    payload = tmp_path / "marketplace" / "payloads" / "agent-example"
    payload.mkdir(parents=True)
    manifest = tmp_path / "marketplace" / "marketplace.json"
    manifest.write_text(
        '{"name":"forged","metadata\\u001cpluginRoot":"payloads",'
        '"plugins":[{"name":"agent-example","source":"payloads/agent-example"}]}',
        encoding="utf-8",
    )
    result = _run(
        (str(POSIX_SCRIPT),),
        "resolve",
        "--copilot-home",
        tmp_path / "copilot",
        "--durable-home",
        tmp_path / "durable",
        "--payload-root",
        payload,
        "--plugin-id",
        "agent-example",
        check=False,
    )
    assert result.returncode != 0
    assert "reserved query separator" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_newline_bearing_source_values_match_powershell() -> None:
    descriptor = {"source": "opaque", "id": "urn:example:value", "ref": "line\n"}
    source_json = json.dumps(descriptor, separators=(",", ":"))
    expected = json.loads(
        _run(
            (
                str(POWERSHELL),
                "-NoProfile",
                "-File",
                str(LIB / "installation-context.ps1"),
            ),
            "source-id",
            "-SourceJson",
            source_json,
            "-MarketplaceKey",
            "Newline",
        ).stdout
    )
    for _, command in RUNNERS:
        assert json.loads(
            _run(
                command,
                "source-id",
                "--source-json",
                source_json,
                "--marketplace-key",
                "Newline",
            ).stdout
        ) == expected
