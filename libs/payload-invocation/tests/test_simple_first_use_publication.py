"""Exercise the actual shared-template adopters before cold first-use writes."""
from __future__ import annotations

import os
import json
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_command_binding import (
    POWERSHELL, ROOT, _snapshot, _write, binding_fixture as binding_fixture,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot, _run


def _release_payload(destination: Path, plugin: str) -> Path:
    source = ROOT / "plugins" / plugin
    payload = destination / plugin
    shutil.copytree(
        source, payload,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "tests", "build", "dist",
        ),
    )
    previous_path = sys.path[:]
    try:
        materializer = runpy.run_path(str(ROOT / "tools/materialize_main.py"))
        messages = materializer["materialize_uv_editable_ref_into"](
            source_consumer_dir=source, dest_consumer_dir=payload, canonical_root=ROOT,
        )
        messages += materializer["mie"].materialize_ref_into(
            source_consumer_dir=source, dest_consumer_dir=payload, canonical_root=ROOT,
        )
    finally:
        sys.path[:] = previous_path
    assert not any(message.startswith("SKIP ") for message in messages), messages
    # Match the top-level fixture exclusion without pruning runtime/native assets.
    libraries = payload / "libs"
    if libraries.is_dir():
        for library in libraries.iterdir():
            tests = library / "tests"
            if tests.is_dir():
                shutil.rmtree(tests)
    return payload


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("older_source", [False, True])
def test_ssh_restamp_preserves_selected_snapshot_identity(
    binding_fixture: dict, tmp_path: Path, older_source: bool,
) -> None:
    released = _release_payload(tmp_path / "release", "agent-ssh")
    environment = _adopter(binding_fixture, "agent-ssh", released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    root = binding_fixture["legacy"]
    environment["AGENT_SSH_HOME"] = str(root)
    shell = "powershell" if os.name == "nt" else "bash"
    command = [*_command(binding_fixture, shell, "install"), "stamp"]
    first = _run(binding_fixture, environment, command)
    assert first.returncode == 0, first.stdout + first.stderr
    selected = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    previous = {name: (root / name).read_bytes() for name in ("payload-dir", "stamped-version")}
    provenance = (selected / "snapshot-provenance.json").read_bytes()
    if older_source:
        project = binding_fixture["payload"] / "pyproject.toml"
        project.write_text(re.sub(
            r'(?m)^version\s*=\s*"[^"]+"', 'version = "0.0.0"',
            project.read_text(encoding="utf-8"), count=1,
        ), encoding="utf-8")
    second = _run(binding_fixture, environment, command)
    assert second.returncode == 0, second.stdout + second.stderr
    assert all((root / name).read_bytes() == value for name, value in previous.items())
    assert (selected / "snapshot-provenance.json").read_bytes() == provenance


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize(("plugin", "state"), [
    (plugin, state)
    for state in (
        "managed", "standalone", "healthy", "old-resolver", "missing-admission",
        "invalid", "foreign", "future", "opt-out",
    )
    for plugin in ("agent-pull-requests", "agent-vault", "agent-ssh", "agent-codespaces")
] + [
    ("agent-codespaces", "healthy-empty"), ("agent-codespaces", "healthy-single"),
    ("agent-codespaces", "healthy-unicode"),
])
def test_shared_actual_stamp_first_use_admission(
    binding_fixture: dict, tmp_path: Path, plugin: str, state: str,
) -> None:
    released = _release_payload(tmp_path / "release", plugin)
    environment = _adopter(binding_fixture, plugin, released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    if state == "healthy-unicode":
        root = binding_fixture["home"] / "custom-\u00e9\u6d4b\u8bd5"
        root.mkdir()
        binding_fixture["legacy"] = root
        binding_fixture["arguments"]["legacy_root"] = root
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    environment[plugin.upper().replace("-", "_") + "_HOME"] = str(binding_fixture["legacy"])
    shell = "powershell" if os.name == "nt" else "bash"
    stamp_command = [*_command(binding_fixture, shell, "install"), "stamp"]
    if state == "healthy-unicode":
        stamp_command += [
            "-InstallDir" if os.name == "nt" else "--install-dir", str(binding_fixture["legacy"]),
        ]
    stamped = (
        subprocess.run(
            stamp_command, env=environment, cwd=binding_fixture["home"],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if state == "healthy-unicode" else
        _run(binding_fixture, environment, stamp_command)
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    root = binding_fixture["legacy"]
    policy_state = state if state in ("standalone", "invalid", "foreign", "future") else "managed"
    _policy(binding_fixture, root, binding_fixture["arguments"], policy_state)
    snapshot = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    if snapshot.is_relative_to(root):
        assert (snapshot / "snapshot-provenance.json").is_file()
        if plugin == "agent-ssh":
            provenance = json.loads((snapshot / "snapshot-provenance.json").read_text(encoding="utf-8"))
            assert provenance["version"] == 2
            assert provenance["snapshotId"] == snapshot.name
            assert provenance["payloadVersion"] == (root / "stamped-version").read_text().strip()
            assert snapshot.name != provenance["payloadVersion"]
        binding_fixture["payload"].rename(binding_fixture["payload"].with_name("publisher-unavailable"))
    installer = snapshot / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    installer.write_text(
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot '..\\installer-ran'), 'yes'); exit 37\n"
        if os.name == "nt" else
        '#!/usr/bin/env bash\nprintf yes > "$(dirname "$0")/../installer-ran"\nexit 37\n',
        encoding="utf-8",
    )
    if plugin in {"agent-vault", "agent-pull-requests", "agent-ssh"} and state == "standalone":
        producer = snapshot / "libs" / "update-driver" / "scripts" / (
            "foundation-produce.ps1" if os.name == "nt" else "foundation-produce.sh"
        )
        producer.write_text(
            "param([string]$Root,[string]$SourceHash)\n"
            "[IO.File]::WriteAllText((Join-Path $Root 'foundation-entered'), $SourceHash)\nexit 37\n"
            if os.name == "nt" else
            '#!/usr/bin/env bash\nprintf "%s" "$2" >"$1/foundation-entered"\n'
            "printf 'fixture foundation refusal\\n' >&2\nexit 37\n", encoding="utf-8",
        )
    if state == "old-resolver":
        resolver = root / "bin" / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")
        resolver.write_text(resolver.read_text(encoding="utf-8").replace(
            "# Trace contract: normal/read-only/ready-v1", "# Older resolver without deferred tracing",
        ), encoding="utf-8")
    elif state == "missing-admission":
        suffix = "ps1" if os.name == "nt" else "sh"
        (snapshot / f"scripts/installation-context/plugin-publication-probe.{suffix}").unlink()
    elif state.startswith("healthy"):
        python = _completed_slot(binding_fixture)
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / (plugin.replace("-", "_") + ".py")).write_text(
            'import json, os, sys\nprint("healthy selected runtime")\n'
            + (f'assert os.environ["AGENT_CODESPACES_HOME"] == {str(root)!r}\n'
               if plugin == "agent-codespaces" else "")
            + 'print(json.dumps(sys.argv[1:]))\n', encoding="utf-8",
        )
        if plugin == "agent-codespaces":
            environment["AGENT_CODESPACES_HOME"] = str(binding_fixture["home"] / "not-selected")
    elif state == "opt-out":
        environment[plugin.upper().replace("-", "_") + "_NO_SELFPROVISION"] = "1"
    for key in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        environment.pop(key, None)
    wrapper = binding_fixture["home"] / ".local/bin" / plugin
    if state == "healthy-unicode" and os.name == "nt":
        assert wrapper.with_suffix(".ps1").read_bytes().startswith(b"\xef\xbb\xbf")
    forwarded = (
        ["--help"] if plugin != "agent-codespaces" else
        [] if state == "healthy-empty" else ["version"] if state == "healthy-single" else
        ["ssh", "--remote-cmd", 'echo "spaces & pipes | semicolons ; bangs !"']
    )
    command = (
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(wrapper.with_suffix(".ps1")), *forwarded]
        if os.name == "nt" else [str(wrapper), *forwarded]
    )
    before = _snapshot(root)
    result = subprocess.run(
        command, env=environment, cwd=snapshot, capture_output=True, text=True,
        timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    marker = snapshot / "installer-ran"
    if state == "standalone":
        if plugin in {"agent-vault", "agent-pull-requests", "agent-ssh"}:
            driver = binding_fixture["home"] / ".copilot-extensions" / "update-driver"
            assert result.returncode != 0, result.stdout + result.stderr
            assert (driver / "foundation-entered").is_file(), result.stdout + result.stderr
            assert not marker.exists()
            assert not (root / ".provision.lock").exists()
            return
        assert result.returncode == 37, result.stdout + result.stderr
        assert marker.read_text(encoding="utf-8") == "yes"
        assert (root / "logs/boot-trace.jsonl").is_file()
        if plugin != "agent-codespaces" or os.name != "nt":
            assert (root / ".provision.lock").is_file()
        return
    if state.startswith("healthy"):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "healthy selected runtime" in result.stdout
        assert json.loads(result.stdout.splitlines()[-1]) == forwarded, result.stderr
        assert not marker.exists()
        assert (root / "logs/boot-trace.jsonl").is_file()
        assert not (root / ".provision.lock").exists()
        return
    after = _snapshot(root)
    changed = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
    assert not changed, f"Runtime tree changed: {changed[:12]}\n{result.stdout}{result.stderr}"
    assert result.returncode != 0
    diagnostic = {
        "old-resolver": "read-only runtime resolver is unavailable",
        "missing-admission": "snapshot admission helper is unavailable",
        "invalid": "deployment-policy-invalid",
        "foreign": "deployment-policy-invalid",
        "future": "deployment-policy-version-unsupported",
        "opt-out": "NO_SELFPROVISION set",
    }.get(state, "deployment-managed")
    assert diagnostic in result.stdout + result.stderr, result.stdout + result.stderr


@pytest.mark.portfolio_tier("T3")
def test_codespaces_actual_first_use_serializes_one_build(
    binding_fixture: dict, tmp_path: Path,
) -> None:
    released = _release_payload(tmp_path / "release", "agent-codespaces")
    environment = _adopter(binding_fixture, "agent-codespaces", released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    root = binding_fixture["legacy"]
    environment["AGENT_CODESPACES_HOME"] = str(root)
    shell = "powershell" if os.name == "nt" else "bash"
    stamped = _run(
        binding_fixture, environment, [*_command(binding_fixture, shell, "install"), "stamp"],
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    _policy(binding_fixture, root, binding_fixture["arguments"], "standalone")
    python = _completed_slot(binding_fixture)
    slot = python.parent.parent
    site = slot / (
        "Lib/site-packages" if os.name == "nt" else
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    (site / "agent_codespaces.py").write_text('print("serialized runtime")\n', encoding="utf-8")
    marker = slot / ".install-complete.json"
    pending = marker.with_suffix(".pending")
    marker.rename(pending)
    snapshot = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    calls = root / "installer-calls"
    suffix = "ps1" if os.name == "nt" else "sh"
    installer = snapshot / f"scripts/install.{suffix}"
    if os.name == "nt":
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        installer.write_text(
            f"[IO.File]::AppendAllText({quote(calls)}, \"build`n\")\n"
            "Start-Sleep -Seconds 1\n"
            f"[IO.File]::Copy({quote(pending)}, {quote(marker)})\nexit 0\n",
            encoding="utf-8",
        )
    else:
        installer.write_text(
            "#!/usr/bin/env bash\n"
            f"printf 'build\\n' >> {shlex.quote(str(calls))}\nsleep 1\n"
            f"cp {shlex.quote(str(pending))} {shlex.quote(str(marker))}\n",
            encoding="utf-8",
        )
    for key in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        environment.pop(key, None)
    wrapper = binding_fixture["home"] / ".local/bin/agent-codespaces"
    command = (
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(wrapper.with_suffix(".ps1")), "version"]
        if os.name == "nt" else [str(wrapper), "version"]
    )
    processes = [
        subprocess.Popen(
            command, env=environment, cwd=snapshot, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) for _ in range(2)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stdout + stderr
        assert "serialized runtime" in stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["build"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("plugin", ["agent-pull-requests", "agent-vault", "agent-ssh", "agent-codespaces"])
@pytest.mark.parametrize("failure", [
    "missing-resolver", "command-directory", "legacy-command-directory",
    "resolver-bin-file", "resolver-directory",
])
def test_actual_restamp_failure_preserves_previous_selection(
    binding_fixture: dict, tmp_path: Path, plugin: str, failure: str,
) -> None:
    released = _release_payload(tmp_path / "release", plugin)
    environment = _adopter(binding_fixture, plugin, released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    environment[plugin.upper().replace("-", "_") + "_HOME"] = str(binding_fixture["legacy"])
    shell = "powershell" if os.name == "nt" else "bash"
    command = [*_command(binding_fixture, shell, "install"), "stamp"]
    first = _run(binding_fixture, environment, command)
    assert first.returncode == 0, first.stdout + first.stderr
    root = binding_fixture["legacy"]
    selected = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    (selected / "previous-selection-sentinel").write_text("previous complete snapshot\n", encoding="utf-8")
    if failure == "legacy-command-directory" and (selected / "snapshot-provenance.json").is_file():
        (selected / "snapshot-provenance.json").unlink()
    if failure == "missing-resolver":
        (binding_fixture["payload"] / "scripts/resolve-runtime.sh").unlink()
    elif failure == "resolver-bin-file":
        (root / "bin").rename(root / "previous-bin")
        (root / "bin").write_text("not a directory\n", encoding="utf-8")
    elif failure == "resolver-directory":
        resolver = root / "bin/resolve-runtime.sh"
        resolver.rename(resolver.with_suffix(".retained"))
        resolver.mkdir()
    else:
        wrapper = binding_fixture["home"] / ".local/bin" / plugin
        if os.name == "nt":
            wrapper = wrapper.with_suffix(".ps1")
        wrapper.unlink()
        wrapper.mkdir()
        (wrapper / "sentinel").write_text("not a command\n", encoding="utf-8")
    markers = {
        name: (root / name).read_bytes() for name in (
            "payload-dir", "stamped-version", "current-version", "last-known-good",
        ) if (root / name).exists()
    }
    command_root = binding_fixture["home"] / ".local/bin"
    commands = {path.name: path.read_bytes() for path in command_root.iterdir() if path.is_file()}
    before = _snapshot(selected)
    second = _run(binding_fixture, environment, command)
    assert second.returncode != 0, second.stdout + second.stderr
    assert "Stamped:" not in second.stdout
    assert all((root / name).read_bytes() == value for name, value in markers.items())
    assert _snapshot(selected) == before
    assert {path.name: path.read_bytes() for path in command_root.iterdir() if path.is_file()} == commands


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize(("plugin", "invocation"), [
    ("agent-vault", "cold"), ("agent-codespaces", "cold"),
    ("agent-codespaces", "healthy"), ("agent-codespaces", "healthy-numeric"),
    ("agent-codespaces", "provision"),
])
@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("managed", [False, True])
def test_payload_uses_selected_publication_policy(
    binding_fixture: dict, tmp_path: Path, plugin: str, invocation: str, namespaced: bool, managed: bool,
) -> None:
    released = _release_payload(tmp_path / "release", plugin) if invocation == "provision" else None
    environment = _adopter(binding_fixture, plugin, released)
    root = binding_fixture["legacy"]
    arguments = binding_fixture["arguments"]
    if namespaced:
        primitive = binding_fixture["primitive"]
        cell = primitive.stamp_context(
            payload_version="1.0.0", payload_origin="installed",
            expected_namespace_generation=0, expected_install_generation=0,
            **{key: arguments[key] for key in ("payload_root", "plugin_id", "environment")},
        )
        primitive.compare_and_swap_activation(
            context=cell["installReceipt"], expected_marketplace_id=cell["marketplaceId"],
            expected_plugin_id=plugin, expected_namespace_generation=cell["namespaceGeneration"],
            expected_install_generation=cell["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=root, environment=binding_fixture["environment"],
        )
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
        root = Path(cell["pluginRoot"])
        arguments = {**arguments, "context": cell["installReceipt"]}
        environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
        _write(binding_fixture["legacy"] / "runtime-deployment.json", {"managed": not managed})
    _policy(binding_fixture, root, arguments, "managed" if managed else "standalone")
    prefix = plugin.upper().replace("-", "_")
    environment.update({
        prefix + "_HOME": str(binding_fixture["legacy"]) + os.sep,
        prefix + "_PAYLOAD_ROOT": str(binding_fixture["payload"]),
    })
    shell = "powershell" if os.name == "nt" else "bash"
    if invocation == "provision":
        _codespaces_actual_provision_preserves_global_commands(
            binding_fixture, environment, root, managed=managed, namespaced=namespaced,
        )
        return
    if invocation.startswith("healthy"):
        python = _completed_slot({**binding_fixture, "legacy": root})
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / "agent_codespaces.py").write_text(
            'import json, os, sys\n'
            f'assert os.environ["AGENT_CODESPACES_HOME"] == {str(root)!r}\n'
            'assert all(isinstance(value, str) for value in sys.argv)\n'
            'print(json.dumps(sys.argv[1:]))\n', encoding="utf-8",
        )
        forwarded = ["ssh", "--remote-cmd", 'echo "spaces & pipes | semicolons ; bangs !"']
        command = [*_command(binding_fixture, shell, "runtime-gate"), *forwarded]
        if invocation == "healthy-numeric":
            forwarded = ["prune", "--max", "5"]
            if os.name == "nt":
                script = str(binding_fixture["payload"] / "scripts/runtime-gate.ps1").replace("'", "''")
                command = [
                    POWERSHELL, "-NoLogo", "-NoProfile", "-Command",
                    f"$limit = 5; & '{script}' prune --max $limit; exit $LASTEXITCODE",
                ]
            else:
                command = [*_command(binding_fixture, shell, "runtime-gate"), *forwarded]
        legacy_before = _snapshot(binding_fixture["legacy"])
        result = _run(
            binding_fixture, environment, command,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == forwarded
        assert not (root / ".provision.lock").exists()
        assert not (root / ".provision-status").exists()
        if namespaced:
            assert _snapshot(binding_fixture["legacy"]) == legacy_before
        return
    installer = binding_fixture["payload"] / "scripts" / (
        "install.ps1" if os.name == "nt" else "install.sh"
    )
    installer.write_text(
        "[Console]::Error.WriteLine('installer diagnostic'); "
        f"[IO.File]::WriteAllText((Join-Path $env:{prefix}_HOME 'installer-ran'), 'yes'); exit 37\n"
        if os.name == "nt" else
        '#!/usr/bin/env bash\nprintf "installer diagnostic\\n" >&2\n'
        f'printf yes > "${prefix}_HOME/installer-ran"\nexit 37\n',
        encoding="utf-8",
    )
    before = _snapshot(root)
    legacy_before = _snapshot(binding_fixture["legacy"])
    result = _run(binding_fixture, environment, _command(binding_fixture, shell, "runtime-gate"))
    if namespaced:
        assert _snapshot(binding_fixture["legacy"]) == legacy_before
    if managed:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "deployment-managed" in result.stdout + result.stderr
        assert _snapshot(root) == before
    elif plugin == "agent-vault":
        assert result.returncode == 126, result.stdout + result.stderr
        expected = "cold namespaced provisioning" if namespaced else "packaged installation driver is unavailable"
        assert expected in result.stderr, result.stdout + result.stderr
        assert _snapshot(root) == before
        assert not (root / "installer-ran").exists()
    else:
        assert result.returncode == 37, result.stdout + result.stderr
        assert (root / "installer-ran").read_text(encoding="utf-8") == "yes"
        assert "installer diagnostic" in result.stderr
        assert "failed rc=37" in (root / ".provision-status").read_text(encoding="utf-8-sig")
        assert (root / "logs/boot-trace.jsonl").is_file()


def _codespaces_actual_provision_preserves_global_commands(
    fixture: dict, environment: dict, root: Path, *, managed: bool, namespaced: bool,
) -> None:
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    payload = fixture["payload"]
    project = payload / "pyproject.toml"
    project.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', 'version = "1.0.0"',
        project.read_text(encoding="utf-8"), count=1,
    ), encoding="utf-8")
    python = _completed_slot({**fixture, "legacy": root})
    site = python.parent.parent / (
        "Lib/site-packages" if os.name == "nt" else
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    (site / "agent_codespaces.py").write_text(
        'import json,sys\nprint("provisioned runtime")\nprint(json.dumps(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    complete = python.parent.parent / ".install-complete.json"
    pending = complete.with_suffix(".pending")
    complete.rename(pending)
    environment.update(
        TEST_PENDING_COMPLETION=str(pending), TEST_READY_COMPLETION=str(complete),
    )
    shell = "powershell" if os.name == "nt" else "bash"
    installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    text = installer.read_text(encoding="utf-8")
    # Replace construction and service effects, not the installer dispatch or publishers.
    overrides = (
        "function Deploy-Venv { return $true }\n"
        "function Deploy-Package { return $true }\n"
        "function Invoke-VersionedActivate {\n"
        "    [IO.File]::Move($env:TEST_PENDING_COMPLETION, $env:TEST_READY_COMPLETION)\n"
        "    return $true\n}\n"
        "function Sync-ConnectionOwnerService {}\n"
        if os.name == "nt" else
        "deploy_venv() { return 0; }\n"
        "deploy_package() { return 0; }\n"
        '_versioned_activate() { mv "$TEST_PENDING_COMPLETION" "$TEST_READY_COMPLETION"; }\n'
        "_sync_owner_service() { :; }\n"
    )
    anchor = "# -- Dispatch --------------------------------------------------------------"
    assert text.count(anchor) == 1
    installer.write_text(text.replace(anchor, overrides + "\n" + anchor), encoding="utf-8")
    command_root = fixture["home"] / ".local/bin"
    command_root.mkdir(parents=True, exist_ok=True)
    for name in ("agent-codespaces", "agent-codespaces.ps1", "agent-codespaces.cmd"):
        (command_root / name).write_bytes(b"previous global command\n")
    if namespaced:
        _policy(fixture, fixture["legacy"], fixture["arguments"], "managed")
    commands_before = _snapshot(command_root)
    legacy_before = _snapshot(fixture["legacy"])
    selected_before = _snapshot(root)
    result = _run(
        fixture, environment, [*_command(fixture, shell, "runtime-gate"), "version"],
    )
    assert _snapshot(command_root) == commands_before, result.stdout + result.stderr
    # Successful installation's legacy self-staging remains tracked separately in #7612.
    if namespaced and managed:
        assert _snapshot(fixture["legacy"]) == legacy_before
    if managed:
        assert result.returncode == 3, result.stdout + result.stderr
        assert _snapshot(root) == selected_before
        assert not complete.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert complete.is_file()
        assert json.loads(result.stdout.splitlines()[-1]) == ["version"]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.skipif(os.name == "nt", reason="POSIX resolver return status")
def test_codespaces_payload_resolver_failure_remains_visible(binding_fixture: dict) -> None:
    environment = _adopter(binding_fixture, "agent-codespaces")
    environment["AGENT_CODESPACES_PAYLOAD_ROOT"] = str(binding_fixture["payload"])
    environment["AGENT_CODESPACES_HOME"] = str(binding_fixture["legacy"])
    (binding_fixture["payload"] / "scripts/resolve-runtime.sh").write_text(
        "# Trace contract: normal/read-only/ready-v1\nreturn 7\n", encoding="utf-8",
    )
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, "bash", "runtime-gate"),
    )
    assert result.returncode == 126, result.stdout + result.stderr
    assert "runtime resolver failed" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
