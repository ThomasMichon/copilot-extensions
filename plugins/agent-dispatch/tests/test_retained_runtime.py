"""Qualify strict historical restoration without touching a user's installation."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

import pytest

from tests.test_installer_required_cutover import _run, ps_function, sh_function

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def retained() -> Any:
    spec = importlib.util.spec_from_file_location("_retained_runtime_test", SCRIPTS / "retained_runtime.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def installation(tmp_path: Path, retained: Any) -> tuple[Path, Path, Path, Any]:
    root = tmp_path / "runtime"
    payload = tmp_path / "payload"
    slot = root / "versions" / "1.0.0"
    slot.mkdir(parents=True)
    payload.mkdir()
    (slot / "package.py").write_text("original", encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-dispatch", "version": "1.0.0"}), encoding="utf-8",
    )
    manager = retained.runtime_manager()
    manager.mark_complete(root, "1.0.0", payload_hash="a" * 64)
    manager.activate(root, "1.0.0", link_free=True)
    manifest = {
        "schema_version": 3, "service": "agent-dispatch", "runtime": "python",
        "venv": str(slot),
        "source": {
            "kind": "marketplace", "plugin": "agent-dispatch", "version": "1.0.0",
            "path": str(payload), "dirty": False, "content_hash": "a" * 64,
        },
    }
    (root / "deploy-manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return root, payload, slot, manager


def test_capture_binds_exact_assembly_and_ignores_only_disposable_bytecode(
    tmp_path: Path, retained: Any,
) -> None:
    root, payload, slot, manager = installation(tmp_path, retained)
    before = (slot / ".install-complete.json").read_bytes()
    record = retained.capture(root, manager)
    (slot / "__pycache__").mkdir()
    (slot / "__pycache__" / "package.pyc").write_bytes(b"cache")
    assert retained.validate(root, record, manager) == ("1.0.0", payload, slot)
    assert (slot / ".install-complete.json").read_bytes() == before
    assert record["manifest"] == (root / "deploy-manifest.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", [
    "payload", "slot", "completion", "root", "manifest-version", "local", "editable", "missing",
])
def test_changed_or_unbound_assembly_is_refused(
    tmp_path: Path, retained: Any, mutation: str,
) -> None:
    root, payload, slot, manager = installation(tmp_path, retained)
    record = retained.capture(root, manager)
    manifest = json.loads(record["manifest"])
    if mutation == "payload":
        (payload / "source.py").write_text("changed", encoding="utf-8")
    elif mutation == "slot":
        (slot / "package.py").write_text("changed", encoding="utf-8")
    elif mutation == "completion":
        (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    elif mutation == "root":
        record["root"] = str(tmp_path)
    elif mutation == "manifest-version":
        manifest["source"]["version"] = "../outside"
        record["manifest"] = json.dumps(manifest)
    elif mutation == "local":
        manifest["source"]["kind"] = "local"
        record["manifest"] = json.dumps(manifest)
    elif mutation == "editable":
        info = slot / "lib" / "dependency.dist-info"
        info.mkdir(parents=True)
        (info / "direct_url.json").write_text('{"dir_info":{"editable":true}}', encoding="utf-8")
    elif mutation == "missing":
        del record["slot_hash"]
    with pytest.raises(ValueError):
        retained.validate(root, record, manager)


def test_capture_does_not_claim_an_unselected_manifest(tmp_path: Path, retained: Any) -> None:
    root, _payload, _slot, manager = installation(tmp_path, retained)
    (root / "current-version").write_text("2.0.0", encoding="utf-8")
    with pytest.raises(ValueError, match="selected"):
        retained.capture(root, manager)


@pytest.mark.skipif(os.name == "nt", reason="POSIX child controller; PowerShell lifecycle is tested separately")
def test_restore_holds_real_retention_and_pins_input_until_child_returns(
    tmp_path: Path, retained: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _payload, slot, manager = installation(tmp_path, retained)
    record = retained.capture(root, manager)
    record_path = tmp_path / "supplied.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    controller = tmp_path / "controller"
    controller.mkdir()
    # The actual child attempts GC while the parent holds the retained slot.
    child = (
        "import importlib.util,json,pathlib,sys\n"
        f"p=pathlib.Path({str(SCRIPTS / 'versioned_runtime.py')!r})\n"
        "s=importlib.util.spec_from_file_location('vr_child',p)\n"
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
        f"root=pathlib.Path({str(root)!r})\n"
        f"pathlib.Path({str(record_path)!r}).write_text('{{}}')\n"
        "record=pathlib.Path(sys.argv[1])\n"
        "assert json.loads(record.read_text())['schema']\n"
        "assert m.gc(root,protect_pids=False)==[]\n"
        f"assert pathlib.Path({str(slot)!r}).exists()\n"
        "print('retained-through-child')\n"
    )
    implementation = controller / "child.py"
    implementation.write_text(child, encoding="utf-8")
    (controller / "install.sh").write_text(
        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(implementation))} "$3"\n',
        encoding="utf-8",
    )
    (root / "current-version").write_text("", encoding="utf-8")
    (root / "last-known-good").write_text("", encoding="utf-8")
    monkeypatch.setattr(retained, "SCRIPT_DIR", controller)
    assert retained.restore(root, record_path, record, manager, "sh", "/bin/bash") == 0
    assert slot.exists()


@pytest.mark.parametrize("shell", ["sh", "ps1"])
def test_retained_runtime_reuses_slot_without_construction_completion_or_gc(
    tmp_path: Path, shell: str,
) -> None:
    if shell == "sh":
        python = tmp_path / "python"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        script = "\n".join([
            "set -euo pipefail",
            f"INSTALL_DIR={shlex.quote(str(tmp_path))}; LOCAL_BIN=$INSTALL_DIR/bin",
            f"VENV_PYTHON={shlex.quote(str(python))}; LINK_PYTHON=$VENV_PYTHON",
            "SRC_VERSION=1.0.0; VERSIONED_RUNTIME=1; FORCE=1; RETAINED_RUNTIME=record",
            "_step() { :; }; _ok() { :; }; _skip() { :; }; _fail() { echo \"$1\"; }",
            "_versioned_current() { echo 2.0.0; }",
            "_versioned_mark_complete() { echo UNEXPECTED-COMPLETION; return 99; }",
            "_versioned_gc() { echo UNEXPECTED-GC; return 99; }",
            "_assert_source_runtime() { echo UNEXPECTED-BUILD; return 99; }",
            "_runtime_publication_begin_install() { :; }",
            "_versioned_activate() { echo selected; }",
            '_runtime_publication_activate() { "$@"; }',
            "_activation_superseded_now() { return 1; }",
            "deploy_binstub() { echo binstub; }; _write_manifest() { echo manifest; }",
            "_runtime_publication_discard() { :; }; _register_pivot() { :; }",
            "_supervisor_audit() { :; }",
            sh_function("_ensure_runtime"),
            "_ensure_runtime",
        ])
    else:
        script = "\n".join([
            "$ErrorActionPreference='Stop'; $RetainedRuntime='record'",
            f"$InstallDir='{tmp_path}'; $VenvPython='{sys.executable}'",
            "$script:GlobalActivationLockTimeoutSeconds=1",
            "function Enter-PluginSnapshotLock { return [Threading.Mutex]::new($true) }",
            "function Invoke-VersionedActivate { Write-Host selected; return $true }",
            "function Test-ActivationSupersededNow { return $false }",
            "function Deploy-SelfProvisioningBinstub { Write-Host binstub }",
            "function Write-Manifest { Write-Host manifest }",
            # Native Python only executes the health import; provide a tiny module.
            f"$VenvPython='{tmp_path / 'python.cmd' if os.name == 'nt' else tmp_path / 'python'}'",
            ps_function("Install-RetainedRuntime"),
            ps_function("Install-Runtime"),
            "Install-Runtime",
        ])
        python = tmp_path / ("python.cmd" if os.name == "nt" else "python")
        python.write_text("@exit /b 0\r\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
    result = _run(tmp_path, shell, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNEXPECTED" not in result.stdout
    assert all(word in result.stdout.splitlines() for word in ("selected", "binstub", "manifest"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX public installer; native PowerShell seams covered above")
@pytest.mark.parametrize("cutover_ok", [False, True])
def test_public_restore_runs_current_controller_against_a_retained_older_assembly(
    tmp_path: Path, retained: Any, cutover_ok: bool,
) -> None:
    root, payload, slot, manager = installation(tmp_path, retained)
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    native_log = tmp_path / "native-actions"
    (payload / "src" / "agent_dispatch").mkdir(parents=True)
    (slot / "bin").mkdir()
    interpreter = slot / "bin" / "python"
    # Execute the real stdlib helpers, but keep runtime imports and coordinator
    # behavior synthetic. No installer/service operation can reach the host.
    interpreter.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == -m ]]; then\n'
        f"  printf '%s\\n' '{json.dumps({'ok': cutover_ok})}'\n"
        f"  exit {0 if cutover_ok else 1}\n"
        "fi\n"
        'for a in "$@"; do\n'
        '  [[ "$a" != "import agent_dispatch, agent_dispatch.embody, agent_dispatch.__main__" ]] || exit 0\n'
        '  [[ "$a" != *read_active_endpoint* ]] || exit 0\n'
        "done\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    (binaries / "systemctl").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(native_log))}\n"
        'case "$*" in *is-active*) echo active;; *is-enabled*) echo enabled;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (binaries / "systemctl").chmod(0o755)
    record = retained.capture(root, manager)
    original_marker = (slot / ".install-complete.json").read_bytes()
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    newer = root / "versions" / "2.0.0"
    newer.mkdir()
    manager.mark_complete(root, "2.0.0", payload_hash="b" * 64)
    manager.activate(root, "2.0.0", link_free=True)
    (root / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_LABELS=test-only\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ, "HOME": str(home), "PATH": f"{binaries}:{os.environ['PATH']}",
        "AGENT_DISPATCH_SUPERVISE_MACHINE": "test-host",
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "0",
    }
    env.pop(retained.OWNER_ENV, None)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPTS / "install.sh"), "restore",
         "--retained-runtime", str(record_path), "--install-dir", str(root)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert (result.returncode == 0) is cutover_ok, result.stdout + result.stderr
    assert manager.current_version(root) == "1.0.0"
    assert (root / "deploy-manifest.json").read_text(encoding="utf-8") == record["manifest"]
    assert (root / "payload-dir").read_text(encoding="utf-8").strip() == str(payload)
    assert (slot / ".install-complete.json").read_bytes() == original_marker
    assert newer.is_dir()
    actions = native_log.read_text(encoding="utf-8") if native_log.exists() else ""
    assert not any(
        "restart agent-dispatch" in line and "supervisor" not in line
        for line in actions.splitlines()
    )
    if cutover_ok:
        assert (root / "supervise-service.sh").is_file()
        assert list((home / ".config/systemd/user").glob("agent-dispatch*.service"))
        assert "restart agent-dispatch-supervisor" in actions
    else:
        assert not (root / "supervise-service.sh").exists()
        assert "restart" not in actions
