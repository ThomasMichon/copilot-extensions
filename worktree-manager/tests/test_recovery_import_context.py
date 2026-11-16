"""Published Main's vendored app closure must not replace the toolkit's process owner."""

from __future__ import annotations

import json
import importlib
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Callable

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "worktree-manager" / "scripts"))
import installer_engine  # noqa: E402

sys.path.insert(0, str(REPOSITORY / "tools"))
feed_fixtures = importlib.import_module("test_installer_wheel_acquisition")
feeds = feed_fixtures.feeds

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.contract("manager.source_recovery_repository_imports"),
]


@pytest.fixture(scope="module")
def published_toolkit(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("published-recovery")
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "tests")
    scripts = root / "worktree-manager" / "scripts"
    shutil.copytree(REPOSITORY / "worktree-manager" / "scripts", scripts, ignore=ignore)
    packages = installer_engine._package_roots(REPOSITORY / "libs" / "update-driver")
    for package in packages:
        shutil.copytree(package, root / "libs" / package.name, ignore=ignore)
    for directory, module in (
        (root / "worktree-manager", "worktree_manager"),
        (root / "plugins" / "agent-worktrees", "agent_worktrees"),
    ):
        for package in packages:
            shutil.copytree(package, directory / "libs" / package.name, ignore=ignore)
        source = directory / "src" / module
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
        if module == "worktree_manager":
            shutil.copytree(
                REPOSITORY / "worktree-manager" / "src" / module, source,
                dirs_exist_ok=True, ignore=ignore,
            )
        (directory / "pyproject.toml").write_text(
            '[project]\nname = "' + module.replace("_", "-") + '"\nversion = "1.0.0"\n'
            '[tool.uv.sources]\n'
            'agent-update-driver = { path = "libs/update-driver" }\n',
            encoding="utf-8",
        )
    tools = root / "tools"
    tools.mkdir()
    for name in ("recover_source.py", "source_preparation_process.py"):
        shutil.copyfile(REPOSITORY / "tools" / name, tools / name)
    shutil.copyfile(Path(__file__).with_name("recovery_import_probe.py"), tools / "import_probe.py")
    return root


@pytest.mark.timeout(120)
@pytest.mark.parametrize("child_exit", [0, 17])
def test_published_recovery_child_uses_repository_owner_and_settles(
    published_toolkit: Path, tmp_path: Path, child_exit: int,
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UPDATE_DRIVER_")}
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(published_toolkit / "tools" / "import_probe.py"),
         "parent", str(published_toolkit), str(queue), str(child_exit)],
        capture_output=True, text=True, timeout=100, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["state"] == "completed" and report["exit_code"] == child_exit, report
    assert not report["origin_failure"], report
    expected = published_toolkit / "libs" / "agent-procutil" / "src" / "agent_procutil"
    assert all(Path(path).parent == expected for path in report["child"]["origins"].values())


@pytest.mark.timeout(60)
def test_recovery_refuses_an_already_loaded_vendored_process_owner(
    published_toolkit: Path, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(published_toolkit / "tools" / "import_probe.py"),
         "preloaded-vendor", str(published_toolkit), str(tmp_path), "0"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 1
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["error_type"] == "ValueError"
    assert "repository process utilities" in report["error"]


def test_recovery_keeps_prepared_engine_snapshot_in_its_named_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(REPOSITORY / "tools"))
    monkeypatch.syspath_prepend(str(REPOSITORY / "plugins" / "agent-worktrees" / "src"))
    recovery: ModuleType = importlib.import_module("recover_source")
    repair = importlib.import_module("worktree_manager.runtime_source_repair")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    identity = {
        "runtime_source": {
            "plugin": "agent-worktrees", "package_version": "1.0.0",
            "source_commit": "a" * 40, "source_tree": "b" * 40,
        },
        "runtime_version": "1.0.0+source.fixture",
    }
    native = {
        "identity": identity, "runtime_version": "1.0.0+source.fixture.profile." + "d" * 16,
        "payload_hash": "e" * 64,
    }
    record = {
        "payload": str(payload), "identity": identity,
        "install_source": str(snapshot), "native": native,
    }
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setattr(recovery.shutil, "which", lambda *_args: "uv")
    monkeypatch.setattr(recovery, "entry", lambda *_args: {"argument": "--install-source"})
    monkeypatch.setattr(recovery, "prepare_new", lambda *_args: {
        "worktree-manager": record, "agent-worktrees": record,
    })
    monkeypatch.setattr(repair, "qualify_engine", lambda *_args, **_kwargs: None)

    def consume(_repository: Path, selected: Any) -> None:
        assert selected.application is None
        assert selected.install_source == snapshot
        assert json.loads(selected.native) == native

    monkeypatch.setattr(repair, "fresh_engine_plan", consume)
    monkeypatch.setattr(recovery, "native_engine", lambda *_args: 0)
    monkeypatch.setattr(recovery, "install_manager", lambda *_args: 19)
    report: dict[str, Any] = {
        "manager_root": str(tmp_path / "manager"), "effects": [], "repository": "fixture",
    }
    assert recovery.apply(
        {"project": None}, SimpleNamespace(), tmp_path, tmp_path, {}, report,
    ) == 19
    assert report["effects"][-1] == {"stage": "manager-installation", "exit_code": 19}


def recovery_probe(
    toolkit: Path, queue: Path, code: str, *, settings: dict[str, str] | None = None,
) -> dict[str, Any]:
    queue.mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UPDATE_DRIVER_")}
    uv = shutil.which("uv")
    assert uv is not None
    environment["PATH"] = str(Path(uv).resolve().parent) + os.pathsep + environment.get("PATH", "")
    environment["UV_NO_INDEX"] = "1"
    if settings is not None:
        environment = {
            key: value for key, value in environment.items()
            if not key.startswith(("UV_", "PIP_"))
        }
        environment.update(settings)
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(toolkit / "tools" / "import_probe.py"),
         "recovery-parent", str(toolkit), str(queue), code],
        capture_output=True, text=True, timeout=100, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = json.loads(result.stdout.splitlines()[-1])
    return report


@pytest.mark.timeout(120)
@pytest.mark.parametrize("code", [0, 17, -1])
def test_actual_recovery_custody_keeps_returned_exits_and_unconfirmed_failures_distinct(
    published_toolkit: Path, tmp_path: Path, code: int,
) -> None:
    report = recovery_probe(published_toolkit, tmp_path / "queue", str(code))
    assert report["request_count"] == 1, report
    if code >= 0:
        assert report["state"] == "completed" and report["exit_code"] == code, report
        assert report["caught"] is None, report
    else:
        assert report["state"] == "interrupted" and report["exit_code"] is None, report
        assert report["report"]["native_state"] == "interrupted", report
        assert report["report"]["native_exit_code"] is None, report
        outcome = report["report"]["child_outcome"]
        assert outcome["stage"] == "fixture-application", report
        assert outcome["error"] == "controlled source fixture failure", report
        assert outcome["exit_code"] == 1 and outcome["effects"] == [], report
        assert report["report"]["cleanup_authorized"] is False, report


@pytest.mark.timeout(150)
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.effect("network")
def test_cold_tool_dependencies_reach_the_real_recovery_child_before_admission(
    published_toolkit: Path, tmp_path: Path, valid: bool,
    feeds: Callable[..., dict[str, Any]],
) -> None:
    toolkit = tmp_path / "toolkit"
    shutil.copytree(published_toolkit, toolkit)
    wheels = importlib.import_module("wheel_input_fixture")
    filename = "recovery_tool_fixture-1.0-py3-none-any.whl"
    content = wheels.wheel_bytes(
        name="recovery_tool_fixture", prefix="recovery_tool_fixture-1.0.dist-info/",
        metadata=b"Metadata-Version: 2.3\nName: recovery-tool-fixture\nVersion: 1.0\n\n",
    )
    state = feeds(certificate=feed_fixtures.CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    metadata = {
        "meta": {"api-version": "1.2"}, "name": "recovery-tool-fixture",
        "files": [{
            "filename": filename, "url": "/files/" + filename,
            "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
            "upload-time": "2000-01-01T00:00:00Z" if valid else "2999-01-01T00:00:00Z",
        }],
    }
    state["routes"]["/simple/recovery-tool-fixture/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"},
        json.dumps(metadata).encode(),
    )
    state["routes"]["/files/" + filename] = (200, {}, content)
    config = toolkit / "fixture-uv.toml"
    config.write_text(
        "system-certs=true\n[[index]]\nname='fixture'\ndefault=true\nauthenticate='never'\n"
        "url=" + json.dumps(state["origin"] + "/simple") + "\n", encoding="utf-8",
    )
    manifest = toolkit / "worktree-manager" / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        "[tool.uv.sources]",
        "dependencies=['recovery-tool-fixture==1.0']\n[tool.uv.sources]",
    ), encoding="utf-8")
    report = recovery_probe(toolkit, tmp_path / "queue", "tool", settings={
        "UV_CONFIG_FILE": str(config), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never", "SSL_CERT_FILE": str(feed_fixtures.CERTIFICATE),
        "NO_PROXY": "127.0.0.1,localhost",
    })
    assert any(
        path == "/simple/recovery-tool-fixture/" for path, _headers in state["requests"]
    ), json.dumps(report, indent=2)
    if valid:
        assert report["state"] == "completed" and report["exit_code"] == 0, json.dumps(report, indent=2)
        assert report["report"]["tool_value"] == 1, report
        assert any(path == "/files/" + filename for path, _headers in state["requests"])
    else:
        assert report["state"] == "unadmitted" and report["request_count"] == 0, report
        assert report["report"]["stage"] == "tool-preparation", report
        assert report["caught"] and report["report"]["effects"] == [], report
        assert not any(path == "/files/" + filename for path, _headers in state["requests"])


@pytest.mark.timeout(180)
@pytest.mark.effect("network")
def test_actual_cold_tools_cover_source_preparation_and_native_cli_imports(tmp_path: Path) -> None:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UPDATE_DRIVER_")}
    uv = shutil.which("uv")
    assert uv is not None
    environment["PATH"] = str(Path(uv).resolve().parent) + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(Path(__file__).with_name("recovery_import_probe.py")),
         "tooling-only", str(REPOSITORY), str(tmp_path), "0"],
        capture_output=True, text=True, timeout=160, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert "local_install_profile" in report["loaded"]
    assert "agent_worktrees.__main__" in report["loaded"]
    assert report["isolated"] == report["no_site"] == 1
