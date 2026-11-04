"""Windows lifecycle regression tests for incomplete runtime recovery."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from .installer_test_support import (
    assert_installer_isolated,
    guard_installer_payload,
    isolate_installer_environment,
    snapshot_tree_manifest,
)

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL = PLUGIN / "scripts" / "install.ps1"
PWSH = shutil.which("pwsh")
VERSION = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

pytestmark = [
    pytest.mark.skipif(
        os.name != "nt" or PWSH is None,
        reason="Windows PowerShell lifecycle coverage",
    ),
    pytest.mark.timeout(180),
]


def _write_fake_uv(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    driver = tmp_path / "fake_uv.py"
    driver.write_text(
        """
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
if "--show-settings" in args:
    print('''PipInstallSettings { settings: PipSettings {
        index_locations: IndexLocations { indexes: [], flat_index: [], },
        exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
    }, }''')
    raise SystemExit(0)
with Path(os.environ["TEST_UV_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
mode = os.environ["TEST_UV_MODE"]
if args == ["--version"]:
    print("uv test")
    raise SystemExit(0)
if mode == "fail-all":
    raise SystemExit(23)
if args[:2] == ["python", "find"]:
    if mode in ("bootstrap-run", "bootstrap-fails"):
        raise SystemExit(23)
    print(os.environ["TEST_BASE_PYTHON"])
    raise SystemExit(0)
if args and args[0] == "run" and "-c" in args:
    assert args[-1] == "import sys; print(sys.executable)"
    if mode == "bootstrap-fails":
        raise SystemExit(23)
    print(os.environ["TEST_BASE_PYTHON"])
    raise SystemExit(0)
if args and args[0] == "run":
    script_index = next(i for i, arg in enumerate(args) if arg.endswith(".py"))
    result = subprocess.run(
        [os.environ["TEST_BASE_PYTHON"], *args[script_index:]],
        check=False,
    )
    raise SystemExit(result.returncode)
if args and args[0] == "venv":
    target = Path(args[1])
    result = subprocess.run(
        [
            os.environ["TEST_BASE_PYTHON"],
            "-m",
            "venv",
            "--without-pip",
            "--copies",
            str(target),
        ],
        check=False,
    )
    raise SystemExit(result.returncode)
if args[:2] == ["pip", "install"]:
    if mode == "sleep-pip":
        Path(os.environ["TEST_UV_SIGNAL"]).write_text("started", encoding="ascii")
        import time
        time.sleep(4)
        raise SystemExit(29)
    if mode == "fail-pip":
        raise SystemExit(29)
    python = args[args.index("--python") + 1]
    site = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    package = Path(site) / "agent_codespaces"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import sys\\nif '--help' in sys.argv: print(sys.prefix)\\n"
        "raise SystemExit(" + ("31" if mode == "fail-health" else "0") + ")\\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    (fake_bin / "uv.cmd").write_text(
        '@"%TEST_BASE_PYTHON%" "%TEST_FAKE_UV%" %*\n@exit /b %ERRORLEVEL%\n',
        encoding="ascii",
    )
    return fake_bin


def _environment(
    home: Path,
    fake_bin: Path,
    driver: Path,
    mode: str,
    *,
    uv_only: bool = False,
) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("COPILOT_PLUGIN_INSTALL_STAGED", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "USERPROFILE": str(home),
            "OS": "Windows_Test",
            "TEST_BASE_PYTHON": sys.executable,
            "TEST_FAKE_UV": str(driver),
            "TEST_UV_MODE": mode,
            "TEST_UV_SIGNAL": str(fake_bin.parent / "uv-started"),
            "TEST_UV_LOG": str(fake_bin.parent / "uv.jsonl"),
        }
    )
    isolate_installer_environment(env, home, fake_bin, PWSH)
    if not uv_only:
        (fake_bin / "python.cmd").write_text(
            '@"%TEST_BASE_PYTHON%" %*\n@exit /b %ERRORLEVEL%\n', encoding="ascii",
        )
    return env


def _run(
    install: Path,
    action: str,
    *,
    home: Path,
    fake_bin: Path,
    mode: str,
    force: bool = False,
    uv_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    if install == INSTALL:
        payload = fake_bin.parent / "source"
        if not payload.exists():
            _copy_plugin_tree(payload)
        install = payload / "scripts" / "install.ps1"
    args = [PWSH, "-NoProfile", "-File", str(install), action]
    if force:
        args.append("-Force")
    result = subprocess.run(
        args,
        cwd=home,
        env=_environment(
            home,
            fake_bin,
            fake_bin.parent / "fake_uv.py",
            mode,
            uv_only=uv_only,
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert_installer_isolated(fake_bin)
    if action == "install" and result.returncode == 0:
        log = fake_bin.parent / "uv.jsonl"
        assert log.is_file(), result.stdout
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert ["--version"] in calls, result.stdout
    return result


def _slot(home: Path) -> Path:
    return home / ".agent-codespaces" / "versions" / VERSION


def _copy_plugin_tree(dst: Path) -> None:
    ignored = shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info",
    )
    shutil.copytree(
        PLUGIN,
        dst,
        ignore=ignored,
    )
    repo = PLUGIN.parents[1]
    sys.path.insert(0, str(repo / "tools"))
    try:
        import materialize_main
    finally:
        sys.path.pop(0)
    logs = materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=dst, canonical_root=repo,
    )
    logs += materialize_main.materialize_installer_engine_ref_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=dst, canonical_root=repo,
    )
    assert not any(line.startswith("SKIP") for line in logs), logs
    guard_installer_payload(dst)


def test_uv_only_clean_host_installs_without_precreating_slot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)

    result = _run(
        INSTALL,
        "install",
        home=home,
        fake_bin=fake_bin,
        mode="success",
        uv_only=True,
    )

    assert result.returncode == 0, result.stdout
    assert (_slot(home) / ".install-complete.json").is_file()


@pytest.mark.parametrize("mode", ["bootstrap-run", "bootstrap-fails"])
def test_uv_only_construction_bootstrap_is_fail_closed(tmp_path: Path, mode: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode=mode, uv_only=True)
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert ["python", "find", "3.11"] in calls
    assert any(call[:2] == ["run", "--no-project"] and "-c" in call for call in calls)
    if mode == "bootstrap-fails":
        assert result.returncode != 0
        assert "Construction requires a bootstrap Python interpreter" in result.stdout
        assert not list((home / ".agent-codespaces").rglob(".install-complete.json"))
        assert not any(call[0] in ("venv", "pip") for call in calls)
    else:
        assert result.returncode == 0, result.stdout
        assert (_slot(home) / ".install-complete.json").is_file()


def _repair_case(tmp_path: Path, *, qualified: bool = False):
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    identity = None
    version = VERSION
    if qualified:
        payload = tmp_path / "source"
        _copy_plugin_tree(payload)
        helper = payload / "scripts" / "versioned_runtime_source.py"
        spec = importlib.util.spec_from_file_location("codespaces_source_fixture", helper)
        source = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = source
        spec.loader.exec_module(source)
        digest = source.assembly_hash(payload)
        identity = source.SourceIdentity(
            "agent-codespaces", VERSION, "a" * 40, "b" * 40, digest,
            source.runtime_version(VERSION, "a" * 40, digest),
        )
        (payload / "runtime-source.json").write_text(json.dumps(identity.document()))
        version = identity.runtime_version
    slot = home / ".agent-codespaces" / "versions" / version
    (slot / "Scripts").mkdir(parents=True)
    (slot / "Scripts" / "python.exe").write_text("broken interpreter", encoding="ascii")
    (slot / "partial.txt").write_text("preserve incomplete generation", encoding="ascii")
    runtime = slot.parents[1]
    for name in ("current-version", "last-known-good"):
        (runtime / name).write_text(version, encoding="ascii")
    config = runtime / "config.yaml"
    config.write_text("default_idle_timeout: 42\n", encoding="ascii")
    before = snapshot_tree_manifest(slot)
    return SimpleNamespace(
        home=home, fake_bin=fake_bin, version=version, identity=identity,
        slot=slot, runtime=runtime, config=config, before=before,
    )


def _assert_repair(case, result):
    runtime, slot, config = case.runtime, case.slot, case.config
    assert result.returncode == 0, result.stdout
    selected = (runtime / "current-version").read_text().strip()
    assert selected.startswith(case.version + (".repair." if case.identity else "+repair.")), result.stdout
    repaired = runtime / "versions" / selected
    assert json.loads((repaired / ".install-complete.json").read_text())["version"] == selected
    assert (runtime / "last-known-good").read_text().strip() == selected
    manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_version"] == selected
    assert manifest["source"]["version"] == VERSION
    assert Path(manifest["venv"]) == repaired
    if case.identity:
        assert manifest["runtime_source"] == case.identity.document()
    assert snapshot_tree_manifest(slot) == case.before
    assert config.read_text() == "default_idle_timeout: 42\n"
    cli = subprocess.run(
        [str(repaired / "Scripts" / "python.exe"), "-I", "-B", "-m", "agent_codespaces", "--help"],
        text=True, capture_output=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert Path(cli.stdout.strip()) == repaired
    command = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(case.home / ".local" / "bin" / "agent-codespaces.ps1"), "--help"],
        cwd=case.home, env=_environment(
            case.home, case.fake_bin, case.fake_bin.parent / "fake_uv.py", "success", uv_only=True,
        ),
        text=True, capture_output=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert command.returncode == 0, command.stdout + command.stderr
    assert Path(command.stdout.strip()) == repaired
    assert_installer_isolated(case.fake_bin)
    return selected


def _construction_calls(case):
    calls = (case.fake_bin.parent / "uv.jsonl").read_text().splitlines()
    return [line for line in calls if json.loads(line)[0] in ("venv", "pip")]


@pytest.mark.parametrize("action", ["install", "update"])
def test_normal_entry_repairs_selected_generation_and_reuses_it(tmp_path: Path, action: str) -> None:
    case = _repair_case(tmp_path)
    result = _run(
        INSTALL, action, home=case.home, fake_bin=case.fake_bin, mode="success", uv_only=True,
    )
    selected = _assert_repair(case, result)
    writes = _construction_calls(case)
    repeated = _run(
        INSTALL, action, home=case.home, fake_bin=case.fake_bin, mode="success", uv_only=True,
    )
    assert _assert_repair(case, repeated) == selected
    assert _construction_calls(case) == writes


@pytest.fixture(scope="module")
def source_repair_installation(tmp_path_factory):
    case = _repair_case(tmp_path_factory.mktemp("codespaces-source-repair"), qualified=True)
    result = _run(
        INSTALL, "update", home=case.home, fake_bin=case.fake_bin, mode="success", uv_only=True,
    )
    assert result.returncode == 0, result.stdout
    return case, result


def test_source_entry_repairs_without_relabeling_source_receipt(source_repair_installation):
    case, result = source_repair_installation
    _assert_repair(case, result)


def test_source_entry_reuses_healthy_repair(source_repair_installation):
    case, _ = source_repair_installation
    selected = (case.runtime / "current-version").read_text().strip()
    writes = _construction_calls(case)
    repeated = _run(
        INSTALL, "update", home=case.home, fake_bin=case.fake_bin, mode="success", uv_only=True,
    )
    assert _assert_repair(case, repeated) == selected
    assert _construction_calls(case) == writes


def _seed_healthy_slot(home: Path) -> Path:
    slot = _slot(home)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--without-pip",
            "--copies",
            str(slot),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    python = slot / "Scripts" / "python.exe"
    site = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    package = Path(site) / "agent_codespaces"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import sys\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": VERSION,
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    return slot


@pytest.mark.parametrize("mode", ["fail-pip", "fail-health"])
def test_failed_repair_preserves_selected_incomplete_generation(tmp_path: Path, mode: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    slot = _slot(home)
    (slot / "Scripts").mkdir(parents=True)
    (slot / "Scripts" / "python.exe").write_text("not a Python runtime", encoding="ascii")
    (slot / "partial.txt").write_text("corpse", encoding="ascii")
    runtime = home / ".agent-codespaces"
    (runtime / "current-version").write_text(VERSION, encoding="ascii")
    (runtime / "last-known-good").write_text(VERSION, encoding="ascii")
    before = {
        path: path.read_bytes()
        for path in (
            slot / "Scripts" / "python.exe", slot / "partial.txt",
            runtime / "current-version", runtime / "last-known-good",
        )
    }

    result = _run(INSTALL, "update", home=home, fake_bin=fake_bin, mode=mode)

    assert result.returncode != 0, result.stdout
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (slot / "pyvenv.cfg").exists()
    assert not (slot / ".install-complete.json").exists()
    assert list((runtime / "versions").iterdir()) == [slot]
    assert not (runtime / "deploy-manifest.json").exists()


def test_install_repairs_slot_with_malformed_completion_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    slot = _seed_healthy_slot(home)
    (slot / ".install-complete.json").write_text("{not-json", encoding="ascii")
    (slot / "stale.txt").write_text("discard me", encoding="ascii")
    before = {
        path: path.read_bytes()
        for path in (slot / ".install-complete.json", slot / "stale.txt", slot / "pyvenv.cfg")
    }

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode="success")

    assert result.returncode == 0, result.stdout
    assert (slot.parents[1] / "current-version").read_text().strip().startswith(VERSION + "+repair.")
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize(
    ("mode", "seed_slot", "force"),
    [
        ("fail-all", False, False),
        ("fail-pip", True, False),
        ("fail-pip", True, True),
    ],
)
def test_install_stage_failures_return_nonzero(
    tmp_path: Path,
    mode: str,
    seed_slot: bool,
    force: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    if seed_slot:
        slot = _slot(home)
        slot.mkdir(parents=True)
        (slot / "partial.txt").write_text("unowned incomplete candidate", encoding="ascii")

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode=mode, force=force)

    assert result.returncode != 0, result.stdout
    assert "[FAIL]" in result.stdout
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    if mode == "fail-pip":
        assert any(call[:2] == ["pip", "install"] for call in calls), result.stdout
    else:
        assert any(call[0] == "venv" for call in calls), result.stdout


def test_failed_activation_returns_nonzero(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    selector = home / ".agent-codespaces" / "current-version"
    selector.mkdir(parents=True)

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode="success")

    assert result.returncode != 0, result.stdout
    assert "Runtime activation failed" in result.stdout
    assert selector.is_dir()


def test_first_use_propagates_provision_failure(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    stamped = _run(INSTALL, "stamp", home=home, fake_bin=fake_bin, mode="fail-all")
    assert stamped.returncode == 0, stamped.stdout

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(home / ".local" / "bin" / "agent-codespaces.ps1"),
            "version",
        ],
        cwd=home,
        env=_environment(home, fake_bin, fake_bin.parent / "fake_uv.py", "fail-all"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0, result.stdout
    assert "provisioning failed" in result.stdout
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert calls[0] == ["--version"], result.stdout
    assert any(call[0] == "venv" for call in calls), result.stdout
    assert_installer_isolated(fake_bin)


def test_marketplace_staged_uninstall_removes_runtime_and_binstubs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "example-marketplace"
        / "agent-codespaces"
    )
    _copy_plugin_tree(installed)
    stamped = _run(installed / "scripts" / "install.ps1", "stamp", home=home, fake_bin=fake_bin, mode="success")
    assert stamped.returncode == 0, stamped.stdout

    result = _run(
        installed / "scripts" / "install.ps1",
        "uninstall",
        home=home,
        fake_bin=fake_bin,
        mode="success",
    )

    assert result.returncode == 0, result.stdout
    assert not (home / ".agent-codespaces").exists()
    assert_installer_isolated(fake_bin)
    assert not (home / ".local" / "bin" / "agent-codespaces.ps1").exists()
    assert not (home / ".local" / "bin" / "agent-codespaces.cmd").exists()


def test_marketplace_staged_uninstall_waits_without_holding_runtime_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "example-marketplace"
        / "agent-codespaces"
    )
    _copy_plugin_tree(installed)
    env = _environment(home, fake_bin, fake_bin.parent / "fake_uv.py", "sleep-pip")
    install_process = subprocess.Popen(
        [PWSH, "-NoProfile", "-File", str(installed / "scripts" / "install.ps1"), "install"],
        cwd=home,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    signal = tmp_path / "uv-started"
    deadline = time.monotonic() + 60
    while not signal.exists() and install_process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert signal.exists(), install_process.communicate(timeout=10)[0]

    uninstall = _run(
        installed / "scripts" / "install.ps1",
        "uninstall",
        home=home,
        fake_bin=fake_bin,
        mode="success",
    )
    install_output = install_process.communicate(timeout=30)[0]

    assert install_process.returncode != 0, install_output
    assert uninstall.returncode == 0, uninstall.stdout
    assert not (home / ".agent-codespaces").exists()
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert ["--version"] in calls, install_output
    assert any(call[:2] == ["pip", "install"] for call in calls), install_output
    assert_installer_isolated(fake_bin)
