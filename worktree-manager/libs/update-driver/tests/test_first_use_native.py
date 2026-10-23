"""Native early refusal never starts acquisition or creates driver/plugin state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs
from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive


def native_command(root, payload, installation, project, *, source=None, client_only=False):
    source = source or Path(__file__).resolve().parents[1]
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        return [
            str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(source / "scripts" / "first-use.ps1"), "-Root", str(root),
            "-Payload", str(payload), "-RuntimeRoot", str(installation), "-Project", str(project),
            *(("-ServiceMode", "client-only") if client_only else ()),
        ]
    if sys.platform != "linux":
        pytest.skip("native first-use bootstrap is not qualified here")
    return [
        shutil.which("bash"), str(source / "scripts" / "first-use.sh"),
        str(root), str(payload), str(installation), str(project),
        *(("--client-only",) if client_only else ()),
    ]


@pytest.mark.parametrize("same_root", [False, True])
def test_native_live_owner_refuses_before_launch_or_queue_mutation(owning_payload, area, same_root):
    payload, installation, project = owning_payload
    root = area / "owner"
    destination = root if same_root else area / "foreign"
    request = enqueue(
        RequestSpec("native-nested-admission-control"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(project)),
    )

    def executor(transaction):
        before = sorted(path.name for path in root.iterdir())
        result = subprocess.run(
            native_command(destination, payload, installation, project),
            env={**os.environ, **transaction.participant_env()}, cwd=project,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode != 0
        assert "first-use submission is unsupported" in result.stderr
        # participant_env creates the live endpoint, not a first-use operation.
        after = sorted(path.name for path in root.iterdir())
        assert [name for name in after if not name.startswith("w-")] == before
        assert len(status(root=root)) == 1
        if not same_root:
            assert not destination.exists()
        transaction.assert_owned()
        return 0

    drain(root=root, executor=executor)
    assert status(request.id, root=root).state == "completed"
    assert not installation.exists()


@pytest.mark.parametrize("reason", ["no-self", "managed", "malformed", "wrong-root"])
def test_native_refusal_is_before_foundation_or_plugin_mutation(
    owning_payload, area, monkeypatch, reason,
):
    payload, installation, project = owning_payload
    root = area / "cold"
    if reason == "no-self":
        monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-vault", legacy_root=installation,
            project_root=project,
        )
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    else:
        root = payload / "wrong-root"
    before = {str(p.relative_to(installation)): p.read_bytes()
              for p in installation.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_command(root, payload, installation, project),
        env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not root.exists(), result.stdout + result.stderr
    assert {str(p.relative_to(installation)): p.read_bytes()
            for p in installation.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("omit_installer", [False, True])
def test_native_preflight_reaches_exact_source_check_without_writes(
    owning_payload, area, omit_installer,
):
    payload, installation, project = owning_payload
    if omit_installer:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        del manifest["installer"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    if os.name == "nt":
        command += ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64]
    else:
        command += ["--run-owner", "sha256:" + "0" * 64]
    result = subprocess.run(
        command, env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, (
        result.stdout, result.stderr,
    )
    assert not root.exists() and not installation.exists()


@pytest.mark.parametrize("field,value", [
    ("schema", "unsupported"), ("schema", True),
    ("version", 3), ("version", True), ("version", "2"), ("version", 2.0),
    ("installer", "other"), ("installer", None),
    ("noSelfProvisionEnv", "OTHER_NO_SELFPROVISION"), ("noSelfProvisionEnv", True),
    ("command", "other"), ("installationContext", "optional"), ("provisionMode", "snapshot"),
])
def test_unsupported_native_recipe_refuses_before_driver_state(
    owning_payload, area, field, value,
):
    payload, installation, project = owning_payload
    path = payload / "payload-invocation.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    root = area / "cold"
    result = subprocess.run(
        native_command(root, payload, installation, project),
        env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "does not declare the fixed first-use provision recipe" in result.stderr
    assert not root.exists() and not installation.exists()


@pytest.mark.parametrize("kind", ["relative", "dot-dot", "linked-payload", "linked-runtime"])
def test_invalid_native_execution_root_never_creates_directories(
    owning_payload, area, kind,
):
    payload, installation, project = owning_payload
    if kind == "relative":
        root = Path("relative-root")
        targets = [project / root]
    elif kind == "dot-dot":
        root = area / "unused" / ".." / "cold"
        targets = [area / "unused", area / "cold"]
    else:
        if os.name == "nt":
            pytest.skip("Linux symlink-root admission regression; Windows reparse refusal is qualified separately")
        target = payload if kind == "linked-payload" else installation
        target.mkdir(exist_ok=True)
        link = area / "linked-root"
        link.symlink_to(target, target_is_directory=True)
        root = link / "cold"
        targets = [target / "cold"]
    before_payload = sorted(str(path.relative_to(payload)) for path in payload.rglob("*"))
    before_runtime = sorted(str(path.relative_to(installation)) for path in installation.rglob("*"))
    result = subprocess.run(
        native_command(root, payload, installation, project), cwd=project,
        env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert all(not path.exists() for path in targets), result.stdout + result.stderr
    assert sorted(str(path.relative_to(payload)) for path in payload.rglob("*")) == before_payload
    assert sorted(str(path.relative_to(installation)) for path in installation.rglob("*")) == before_runtime


@pytest.mark.parametrize("mixed_case", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name != "nt", reason="PowerShell case-insensitive binding"),
)])
@pytest.mark.parametrize("wait", [False, True])
def test_actual_native_supervisor_forwards_fixed_fields_and_keeps_root_owned_diagnostics(
    owning_payload, area, mixed_case, wait,
):
    payload, installation, project = owning_payload
    library = Path(__file__).resolve().parents[2]
    diagnostic = "caf\u00e9-\u6d4b\u8bd5"
    inputs = area / ("foundation-" + diagnostic)
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    (source / "scripts" / "foundation-produce.ps1").write_text(
        "param([string]$Root,[string]$SourceHash)\n"
        "[IO.File]::WriteAllText((Join-Path $Root 'foundation-entered'), $SourceHash)\n"
        "$log = (Get-ChildItem -LiteralPath $Root -Filter 'first-use-*.log').FullName\n"
        "[IO.File]::AppendAllText($log, ('caf' + [char]0x00e9 + '-' + "
        "[char]0x6d4b + [char]0x8bd5 + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))\n"
        "exit 37\n", encoding="utf-8",
    )
    (source / "scripts" / "foundation-produce.sh").write_text(
        '#!/usr/bin/env bash\nprintf "%s" "$2" >"$1/foundation-entered"\n'
        f"printf '%s\\n' '{diagnostic}' >&2\n"
        "printf 'fixture foundation refusal\\n' >&2\nexit 37\n",
        encoding="utf-8",
    )
    root = area / "cold"
    command = native_command(root, payload, installation, project, source=source)
    if mixed_case:
        command += ["-Mode", "LaUnCh", "-ServiceMode", "Client-Only"]
    if wait:
        command += ["-Wait" if os.name == "nt" else "--wait"]
    result = subprocess.run(
        command,
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", timeout=60,
        **no_window_kwargs(),
    )
    assert (result.returncode != 0 if wait else result.returncode == 0), result.stdout + result.stderr
    if wait:
        assert "Foundation construction failed" in result.stderr or "fixture foundation refusal" in result.stderr
        assert diagnostic in result.stderr, result.stderr
        assert "launched" not in result.stdout
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        logs = list(root.glob("first-use-*.log"))
        text = "\n".join(path.read_text(errors="replace") for path in logs)
        if (root / "foundation-entered").exists() and (
            "Foundation construction failed" in text or "fixture foundation refusal" in text
        ):
            break
        time.sleep(.05)
    else:
        pytest.fail(result.stdout + result.stderr + text)
    assert len(logs) == 1 and logs[0].parent == root
    assert not list(project.glob(".native-daemon-*"))
    assert not (root / "queue.sqlite3").exists() and not installation.exists()
