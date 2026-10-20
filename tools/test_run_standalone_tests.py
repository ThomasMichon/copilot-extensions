from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import venv
import zipfile
from pathlib import Path

import pytest

from _promotion_workflow_test_support import source_workflow

SCRIPT = Path(__file__).with_name("run-standalone-tests.py")
SPEC = importlib.util.spec_from_file_location("run_standalone_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_unreaped_standalone_tree_retains_sandbox_and_releases_lease(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    root = tmp_path / "fleet-manager"
    (root / "tests").mkdir(parents=True)
    python = runner.default_python("fleet-manager")
    python.parent.mkdir(parents=True)
    python.touch()
    events = []
    retained = []
    sandbox_type = runner.TestSandbox

    def sandbox(*args, **kwargs):
        instance = sandbox_type(*args, **kwargs)
        retained.append(instance)
        return instance

    def run(*args, **kwargs):
        (kwargs["sandbox"] / "still-owned").write_text("preserve")
        raise runner.UnreapedProcessError("forced teardown failure")

    monkeypatch.setattr(runner, "TestSandbox", sandbox)
    monkeypatch.setattr(runner, "acquire", lambda *args: type(
        "Lease", (), {"release": lambda self: events.append("released")},
    )())
    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner, "cleanup_sandbox",
                        lambda temporary: pytest.fail("unreaped sandbox was cleaned"))
    assert runner.main(["fleet-manager", "--timeout", "10"]) == 1
    temporary = retained[0]
    try:
        assert temporary.retained
        assert not temporary._finalizer.alive
        assert (Path(temporary.name) / "still-owned").read_text() == "preserve"
        assert "[KEEP] Unreaped process tree: sandbox retained at" in capsys.readouterr().err
        assert events == ["released"]
    finally:
        shutil.rmtree(temporary.name)


@pytest.mark.parametrize("component,package", [
    ("fleet-manager", "fleet_manager"),
    ("services/index", "agent_index_service"),
])
def test_retained_preparation_builds_actual_standalone_resource_hooks(
    tmp_path, monkeypatch, component, package,
):
    checkout = tmp_path / "checkout"
    project = checkout / component
    original = SCRIPT.parent.parent / component
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "setup.py", "_build_runtime_resource.py", "MANIFEST.in"):
        if (original / name).is_file():
            shutil.copy2(original / name, project / name)
    shutil.copytree(original / "src", project / "src")
    resources = checkout / "libs" / "versioned-runtime"
    shutil.copytree(SCRIPT.parent.parent / "libs" / "versioned-runtime", resources)
    expected = {
        destination: (resources / source).read_bytes()
        for source, destination in {
            "versioned_runtime.py": "_versioned_runtime.py",
            "versioned_runtime_leases.py": "versioned_runtime_leases.py",
            "versioned_runtime_construction.py": "versioned_runtime_construction.py",
            "versioned_runtime_source.py": "versioned_runtime_source.py",
            "versioned_runtime_publication.py": "versioned_runtime_publication.py",
        }.items()
    }
    output = tmp_path / "artifacts"
    output.mkdir()
    actual_run = subprocess.run
    retained = []

    def uv(command, **kwargs):
        if "--show-settings" in command:
            return subprocess.CompletedProcess(command, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        retained.append(Path(kwargs["cwd"]))
        assert retained[0] != checkout
        for name in ("standalone_build.py", "versioned_runtime.py",
                     "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                     "versioned_runtime_source.py", "versioned_runtime_publication.py"):
            (resources / name).write_text("raise RuntimeError('mutable original reopened')\n")
        candidate = Path(next(argument for argument in command if argument.endswith(str(Path(component)))))
        result = actual_run(
            [sys.executable, "-I", "-c",
             "from setuptools.build_meta import build_wheel; "
             f"build_wheel({str(output)!r})"],
            cwd=candidate, capture_output=True, text=True, timeout=30,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    monkeypatch.setattr(runner.subprocess, "run", uv)
    runner.run_uv_install(
        "uv", ["--no-config", "--python", sys.executable, str(project)],
        cwd=checkout, trusted_source_roots=(checkout,), retained_resource_roots=(resources,),
    )
    assert len(retained) == 1 and not retained[0].exists()
    with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
        for name, data in expected.items():
            assert wheel.read(f"{package}/{name}") == data


def test_fleet_manager_preparation_has_no_service_or_plugin_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_uv_install", lambda uv, args, **kwargs: runner.subprocess.run(
        [uv, "pip", "install", *args], **{key: value for key, value in kwargs.items() if key not in ("trusted_source_roots", "retained_resource_roots")},
    ))
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    python = runner.default_python("fleet-manager")
    def prepare_command(command, **kwargs):
        calls.append(command)
        if command[:2] == ["uv", "venv"]:
            assert Path(command[-1]) == python.parent.parent
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", prepare_command)
    python.parent.parent.mkdir(parents=True)
    runner.prepare("fleet-manager", python)
    assert calls[-1] == [
        "uv", "pip", "install", "--python", str(python), str(tmp_path / "fleet-manager") + "[dev]",
    ]
    assert "fleet-manager" in runner.STANDALONE_CONSUMERS


@pytest.mark.parametrize("smoke", [False, True])
def test_prepare_composes_local_core_and_declared_service_extras(tmp_path, monkeypatch, smoke):
    monkeypatch.setattr(runner, "run_uv_install", lambda uv, args, **kwargs: runner.subprocess.run(
        [uv, "pip", "install", *args], **{key: value for key, value in kwargs.items() if key not in ("trusted_source_roots", "retained_resource_roots")},
    ))
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    metadata = tmp_path / "plugins" / "agent-index" / "pyproject.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '[project]\nversion = "0.1.0-dev210"\n'
        '[tool.uv.sources]\nagent-procutil = {path="../../libs/agent-procutil", editable=true}\n',
    )
    procutil = tmp_path / "libs" / "agent-procutil"
    procutil.mkdir(parents=True)
    (procutil / "pyproject.toml").write_text('[project]\nname="agent-procutil"\n')

    def install(command, **kwargs):
        calls.append(command)
        if "--override" in command:
            override = Path(command[command.index("--override") + 1])
            assert override.parent.parent == tmp_path
            core_extra = "" if smoke else "[store,server]"
            assert override.read_text() == (
                f"agent-index{core_extra} @ {metadata.parent.as_uri()}\n"
                f"agent-procutil @ {(tmp_path / 'libs' / 'agent-procutil').as_uri()}\n"
            )

    monkeypatch.setattr(runner.subprocess, "run", install)
    python = tmp_path / ".test-venvs" / "service" / "bin" / "python"
    python.parent.parent.mkdir(parents=True)

    runner.prepare("services/index", python, smoke=smoke)

    assert calls[0] == ["uv", "venv", "--allow-existing", str(python.parent.parent)]
    assert calls[1][:5] == ["uv", "pip", "install", "--python", str(python)]
    core_extra = "" if smoke else "[store,server]"
    service_extra = "[test]" if smoke else "[native,test]"
    assert str(tmp_path / "plugins" / "agent-index") + core_extra in calls[1]
    assert str(tmp_path / "libs" / "zdd") not in calls[1]
    assert str(procutil) not in calls[1]
    assert calls[1][-1] == str(tmp_path / "services/index") + service_extra
    assert not Path(calls[1][calls[1].index("--override") + 1]).exists()


def test_manager_preparation_retains_source_payload_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_uv_install", lambda uv, args, **kwargs: runner.subprocess.run(
        [uv, "pip", "install", *args], **{key: value for key, value in kwargs.items() if key not in ("trusted_source_roots", "retained_resource_roots")},
    ))
    monkeypatch.setattr(runner, "REPO", tmp_path)
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    python = tmp_path / ".test-venvs" / "worktree-manager" / "bin" / "python"
    python.parent.mkdir(parents=True)
    (python.parent.parent / "pyvenv.cfg").write_text("home = fixture-base\n")
    python.touch()
    runner.prepare("worktree-manager", python)
    assert calls[-1][-2:] == ["--editable", str(tmp_path / "worktree-manager") + "[dev]"]


@pytest.mark.parametrize("component", [
    "fleet-manager", "worktree-manager", "services/index", "services/dispatch",
    "services/chat",
])
def test_fresh_preparation_attributes_complete_local_graph(tmp_path, monkeypatch, component):
    projects = {
        "fleet-manager": "fleet-manager",
        "worktree-manager": "worktree-manager",
        "services/index": "agent-index-service",
        "services/dispatch": "agent-dispatch-service",
        "services/chat": "agent-chat-service",
        "plugins/agent-index": "agent-index",
        "plugins/agent-dispatch": "agent-dispatch",
        "plugins/agent-index/libs/dropin-registry": "agent-dropin-registry",
        "libs/zdd": "agent-zdd",
        "libs/agent-procutil": "agent-procutil",
        "libs/dropin-registry": "agent-dropin-registry",
        "libs/plugin-activation": "agent-plugin-activation",
        "libs/plugin-resolve": "agent-plugin-resolve",
        "libs/single-instance-lease": "agent-single-instance-lease",
    }
    for relative, name in projects.items():
        project = tmp_path / relative
        project.mkdir(parents=True, exist_ok=True)
        (project / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
            f'[project]\nname="{name}"\nversion="1.0"\n'
            '[project.optional-dependencies]\ndev=[]\nnative=[]\ntest=[]\nstore=[]\nserver=[]\n',
            encoding="utf-8",
        )
        with (project / "pyproject.toml").open("a", encoding="utf-8") as manifest:
            manifest.write('[tool.setuptools.packages.find]\nwhere=["src"]\n')
        (project / "src").mkdir()
    with (tmp_path / "plugins" / "agent-index" / "pyproject.toml").open("a") as manifest:
        manifest.write(
            '[tool.uv.sources]\n'
            'agent-procutil = {path="../../libs/agent-procutil", editable=true}\n',
        )
    (tmp_path / "libs" / "versioned-runtime").mkdir()
    monkeypatch.setattr(runner, "REPO", tmp_path)
    python = runner.default_python(component)
    python.parent.parent.mkdir(parents=True)
    admitted = runner.run_uv_install
    probes = []
    installs = []

    def prepare(uv, arguments, **kwargs):
        assert kwargs["trusted_source_roots"] == (tmp_path,)
        assert kwargs["retained_resource_roots"] == (tmp_path / "libs" / "versioned-runtime",)
        return admitted(uv, arguments, **kwargs)

    def command(arguments, **kwargs):
        if arguments[1] == "venv":
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        elif "--show-settings" in arguments:
            probes.append(arguments)
            return subprocess.CompletedProcess(arguments, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        else:
            installs.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(runner, "run_uv_install", prepare)
    monkeypatch.setattr(runner.subprocess, "run", command)
    runner.prepare(component, python, smoke=component == "services/index")
    assert len(probes) == 2 and len(installs) == 1
    assert "--exclude-newer" in installs[0] and "--reinstall" in installs[0]
    extra = "[test]" if component == "services/index" else "[dev]"
    assert any(argument.endswith(component.replace("/", os.sep) + extra) for argument in installs[0])
    if component in ("services/index", "services/dispatch"):
        assert "--override" in installs[0]


@pytest.mark.parametrize("component", [
    "services/index", "agent-dispatch-service",
])
def test_prepare_rejects_host_interpreter_before_admission(tmp_path, monkeypatch, component):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    called = []
    monkeypatch.setattr(runner, "acquire", lambda wait, root: called.append(wait))

    with pytest.raises(SystemExit) as exc:
        runner.main([
            component, "--prepare", "--python", str(tmp_path / "host" / "python")
        ])

    assert exc.value.code == 2
    assert called == []


@pytest.mark.parametrize("legacy,canonical", [
    ("agent-dispatch-service", "services/dispatch"),
    ("agent-chat-service", "services/chat"),
])
def test_service_alias_uses_canonical_preparation_lease_and_tests(
    tmp_path, monkeypatch, legacy, canonical,
):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    root = tmp_path / canonical
    (root / "tests").mkdir(parents=True)
    python = runner.default_python(canonical)
    events = []

    class Lease:
        def release(self):
            events.append(("release",))

    def acquire(wait, environment_root):
        assert environment_root == python.parent.parent.resolve()
        assert environment_root.is_dir()
        events.append(("lease", wait))
        return Lease()

    def prepare(component, selected_python, *, smoke):
        assert component == canonical
        assert selected_python == python
        assert smoke is False
        events.append(("prepare", smoke))
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        (python.parent.parent / "pyvenv.cfg").touch()

    def run(command, **kwargs):
        assert command[:5] == [str(python), "-I", "-m", "pytest", "-q"]
        assert kwargs["cwd"] == root
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert all(Path(path).is_relative_to(root / "tests") for path in command[5:-2])
        assert command[5] == str(root / "tests")
        events.append(("run",))
        return 0

    monkeypatch.setattr(runner, "acquire", acquire)
    monkeypatch.setattr(runner, "prepare", prepare)
    monkeypatch.setattr(runner, "run_contained", run)
    args = [legacy, "--prepare", "--admission-wait", "60"]
    assert runner.main(args) == 0
    assert events == [("lease", 60.0), ("prepare", False), ("run",), ("release",)]
    assert not (tmp_path / ".test-venvs" / sys.platform / legacy).exists()
    assert legacy not in runner.STANDALONE_CONSUMERS


def fixture_python(environment):
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [str(python), "-I", "-c",
         "import json, sysconfig; print(json.dumps(sysconfig.get_path('purelib')))"],
        capture_output=True, text=True, check=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    site = Path(json.loads(result.stdout))
    assert site.is_absolute()
    site = site.resolve(strict=True)
    assert site.is_relative_to(environment.resolve(strict=True)) and site.is_dir()
    dependency_paths = list(dict.fromkeys([
        str(Path(pytest.__file__).resolve().parent.parent),
        sysconfig.get_path("purelib"), sysconfig.get_path("platlib"),
    ]))
    (site / "test-harness.pth").write_text(
        "\n".join(dependency_paths) + "\n", encoding="utf-8",
    )
    return python


def test_fixture_python_uses_child_install_scheme(tmp_path, monkeypatch):
    real_get_path = sysconfig.get_path

    def parent_get_path(name, *args, **kwargs):
        if name == "purelib" and kwargs.get("vars") and not kwargs.get("scheme"):
            return str(tmp_path / "parent-only-install-scheme")
        return real_get_path(name, *args, **kwargs)

    monkeypatch.setattr(sysconfig, "get_path", parent_get_path)
    python = fixture_python(tmp_path / "child")
    result = subprocess.run(
        [str(python), "-I", "-c", "import pytest; print(pytest.__file__)"],
        capture_output=True, text=True, check=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert Path(result.stdout.strip()).resolve() == Path(pytest.__file__).resolve()


@pytest.mark.parametrize("escape", ["dotdot", "symlink"])
def test_fixture_python_rejects_resolved_site_outside_environment(tmp_path, monkeypatch, escape):
    environment = tmp_path / "child"
    environment.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    reported = environment / ".." / "outside"
    if escape == "symlink":
        reported = environment / "site-link"
        try:
            reported.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Directory symlink creation is unavailable")
    real_run = subprocess.run

    def report_site(argv, **kwargs):
        if "get_path('purelib')" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(str(reported)))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", report_site)
    with pytest.raises(AssertionError):
        fixture_python(environment)
    assert not (outside / "test-harness.pth").exists()


def test_fixture_python_accepts_environment_beneath_symlinked_parent(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    parent = tmp_path / "parent-link"
    try:
        parent.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable")
    environment = parent / "child"
    python = fixture_python(environment)
    assert python.exists()
    assert len(list(actual.rglob("test-harness.pth"))) == 1


def exercise_native_legacy_service_cli(tmp_path, legacy, canonical):
    root = tmp_path / canonical
    tests = root / "tests"
    tests.mkdir(parents=True)
    environment = tmp_path / ".test-venvs" / sys.platform / canonical
    fixture_python(environment)
    (tests / "test_contract.py").write_text(
        "import os, sys, pytest\nfrom pathlib import Path\n"
        "def test_contract():\n"
        f"    assert Path.cwd() == Path({str(root)!r})\n"
        f"    assert Path(sys.prefix).resolve() == Path({str(environment.resolve())!r})\n"
        f"    assert Path(pytest.__file__).resolve() == Path({str(Path(pytest.__file__).resolve())!r})\n"
        "    assert os.environ['COPILOT_EXTENSIONS_TEST_CONTAINED'] == '1'\n"
        "    Path('executed.txt').write_text('canonical')\n",
        encoding="utf-8",
    )
    runner.REPO = tmp_path
    assert runner.main([legacy, "--timeout", "10"]) == 0
    assert (root / "executed.txt").read_text() == "canonical"
    assert not (tmp_path / ".test-venvs" / sys.platform / legacy).exists()


@pytest.mark.parametrize("legacy,canonical", runner.COMPONENT_ALIASES.items())
def test_native_legacy_service_cli_runs_canonical_tests_without_separate_environments(
    tmp_path, legacy, canonical,
):
    python = fixture_python(tmp_path / "harness")
    script = (
        "import importlib.util, sys, sysconfig, pytest\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
        "assert Path(pytest.__file__).resolve().parent.parent != "
        "Path(sysconfig.get_path('purelib')).resolve()\n"
        f"spec = importlib.util.spec_from_file_location('fixture_tests', {str(Path(__file__).resolve())!r})\n"
        "fixture = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(fixture)\n"
        f"fixture.exercise_native_legacy_service_cli(Path({str(tmp_path)!r}), "
        f"{legacy!r}, {canonical!r})\n"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script], capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_prepare_rejects_managed_launcher_with_host_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / ".test-venvs" / "fake" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    released = []

    class Lease:
        def release(self):
            released.append(True)

    monkeypatch.setattr(runner, "acquire", lambda *_: Lease())
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", "--prepare", "--python", str(python)])
    assert exc.value.code == 2
    assert released == [True]


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("component", ["services/index", "agent-index-service"])
def test_run_is_contained_and_releases_interpreter_environment_lease(
    tmp_path, monkeypatch, smoke, component,
):
    root = tmp_path / "services/index"
    (root / "tests").mkdir(parents=True)
    python = tmp_path / "test-python"
    python.touch()
    monkeypatch.setattr(runner, "REPO", tmp_path)
    released = []

    class Lease:
        def release(self):
            released.append(True)

    environment_root = tmp_path / "actual-environment"
    environment_root.mkdir()
    acquisitions = []
    monkeypatch.setattr(
        runner, "acquire",
        lambda wait, root: acquisitions.append((wait, root)) or Lease(),
    )
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        assert command[1:4] == ["-I", "-m", "pytest"]
        if smoke:
            assert command[5:18] == [
                str(root / "tests" / "test_cli.py"), str(root / "tests" / "test_config.py"),
                str(root / "tests" / "test_release.py"),
                str(root / "tests" / "test_release_cli.py"),
                str(root / "tests" / "test_staging.py"),
                str(root / "tests" / "test_staging_cli.py"),
                str(root / "tests" / "test_staging_cleanup_review.py"),
                str(root / "tests" / "test_staging_processes.py"),
                str(root / "tests" / "test_runtime_resource_distribution.py"),
                str(root / "tests" / "test_distribution.py"),
                str(root / "tests" / "test_release_distribution.py"),
                str(root / "tests" / "test_container.py"),
                str(root / "tests" / "test_fleet_image.py"),
            ]
        else:
            assert command[5] == str(root / "tests")
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert Path(kwargs["env"]["HOME"]) != Path.home()
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    args = [
        component, "--python", str(python),
        "--environment-root", str(environment_root),
    ]
    if smoke:
        args.append("--smoke")
    assert runner.main(args) == 0
    assert captured["cwd"] == root
    assert released == [True]
    assert acquisitions == [(0.0, environment_root)]


def test_legacy_index_name_prepares_canonical_component(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = runner.default_python("services/index")
    python.parent.mkdir(parents=True)
    (python.parent.parent / "pyvenv.cfg").write_text("home = fixture-base\n")
    python.touch()
    prepared = []
    released = []

    class Lease:
        def release(self):
            released.append(True)

    monkeypatch.setattr(runner, "acquire", lambda *_: Lease())
    monkeypatch.setattr(
        runner, "prepare",
        lambda component, interpreter, **kwargs: prepared.append((component, interpreter, kwargs)),
    )
    monkeypatch.setattr(runner, "run_contained", lambda *_a, **_k: 0)
    assert runner.main(["agent-index-service", "--prepare", "--admission-wait", "60"]) == 0
    assert prepared == [("services/index", python, {"smoke": False})]
    assert released == [True]


def test_unknown_component_is_rejected_before_admission(monkeypatch):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["unknown-service"])
    assert exc.value.code == 2


@pytest.mark.parametrize("component", [
    "worktree-manager", "services/dispatch", "agent-dispatch-service",
])
def test_smoke_rejects_undefined_component_before_admission(monkeypatch, component):
    monkeypatch.setattr(runner, "acquire", lambda wait, root: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main([component, "--smoke"])
    assert exc.value.code == 2


def test_venv_interpreter_symlink_is_not_resolved_out_of_its_environment(tmp_path, monkeypatch):
    root = tmp_path / "worktree-manager"
    (root / "tests").mkdir(parents=True)
    base = tmp_path / "base-python"
    base.touch()
    python = tmp_path / ".test-venvs" / "worktree-manager" / "bin" / "python"
    python.parent.mkdir(parents=True)
    (python.parent.parent / "pyvenv.cfg").write_text("home = fixture-base\n")
    try:
        python.symlink_to(base)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "acquire", lambda wait, root: type("Lease", (), {"release": lambda self: None})())
    commands = []
    monkeypatch.setattr(runner, "run_contained", lambda command, **kwargs: commands.append(command) or 0)
    assert runner.main(["worktree-manager", "--python", str(python)]) == 0
    assert commands[0][0] == str(python)


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1"])
def test_nonfinite_or_nonpositive_timeout_rejected_before_admission(monkeypatch, timeout):
    monkeypatch.setattr(runner, "acquire", lambda wait, root: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", f"--timeout={timeout}"])
    assert exc.value.code == 2


@pytest.mark.parametrize("wait", ["nan", "inf", "-inf", "-1", "bad"])
def test_invalid_admission_wait_rejected_before_preparation(monkeypatch, wait):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    monkeypatch.setattr(runner, "prepare", lambda *_a, **_k: pytest.fail("unexpected preparation"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", "--prepare", f"--admission-wait={wait}"])
    assert exc.value.code == 2


def test_nonstandard_custom_launcher_requires_explicit_root_without_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "invalid-python"
    python.touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))

    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate executed"))
    assert runner.main(["services/index", "--python", str(python)]) == 1


@pytest.mark.parametrize("arguments", [
    ["--environment-root", "environment"],
    ["--python", "launcher", "--environment-root", "environment", "--prepare"],
])
def test_explicit_root_does_not_bypass_preparation_guard(monkeypatch, arguments):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["services/index", *arguments])
    assert exc.value.code == 2


def test_standard_root_discovery_never_executes_candidate(tmp_path, monkeypatch):
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.parent.mkdir()
    (python.parent.parent / "pyvenv.cfg").touch()
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate executed"))
    assert runner.interpreter_environment(python) == python.parent.parent.resolve()
    assert not python.exists()


def test_explicit_root_cannot_override_discoverable_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.parent.mkdir()
    (python.parent.parent / "pyvenv.cfg").touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main([
            "services/index", "--python", str(python),
            "--environment-root", str(tmp_path / "wrong-environment"),
        ])
    assert exc.value.code == 2


@pytest.mark.parametrize("directory", ["bin", "Scripts"])
def test_opaque_standard_named_launcher_accepts_explicit_environment(tmp_path, monkeypatch, directory):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    python = tmp_path / "wrappers" / directory / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    actual = tmp_path / "actual-environment"
    actual.mkdir()
    acquired = []

    class Lease:
        def release(self):
            pass

    monkeypatch.setattr(runner, "acquire", lambda wait, root: acquired.append(root) or Lease())
    monkeypatch.setattr(runner, "run_contained", lambda *_a, **_k: 0)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: pytest.fail("candidate probed"))
    assert runner.main([
        "services/index", "--python", str(python), "--environment-root", str(actual),
    ]) == 0
    assert acquired == [actual]
    acquired.clear()
    assert runner.main(["services/index", "--python", str(python)]) == 1
    assert acquired == []


def test_custom_absent_root_is_not_created(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services/index" / "tests").mkdir(parents=True)
    root = tmp_path / "absent-custom-root"
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as exc:
        runner.main([
            "services/index", "--python", str(tmp_path / "launcher"),
            "--environment-root", str(root),
        ])
    assert exc.value.code == 2
    assert not root.exists()


def test_custom_symlink_without_configuration_requires_explicit_address(tmp_path, monkeypatch):
    python = tmp_path / "environment" / "bin" / "python"
    python.parent.mkdir(parents=True)
    # Portable symlink-contract simulation: following the executable would
    # reach the base interpreter, not identify the missing-config venv.
    real_resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve",
        lambda path, *args, **kwargs: (
            Path(sys.executable) if path == python
            else real_resolve(path, *args, **kwargs)
        ),
    )
    with pytest.raises(ValueError, match="provide --environment-root"):
        runner.interpreter_environment(python)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX outer containment regression uses Linux procfs")
def test_posix_owned_child_survives_worker_exit_only_until_outer_cleanup(tmp_path):
    from plugin_test_containment import Limits, isolated_environment, run_contained

    repo = SCRIPT.parent.parent
    receipt = tmp_path / "identity.json"
    script = f"""
import json,os,sys,subprocess
from pathlib import Path
sys.path[:0] = {[
    str(repo / "services/index" / "tests"),
    str(repo / "libs" / "agent-procutil" / "src"),
]!r}
from _service_process import owned_python
with owned_python(['-c', 'import time; time.sleep(120)'],
                  cwd={str(tmp_path)!r}, stdout=subprocess.DEVNULL) as child:
    Path({str(receipt)!r}).write_text(json.dumps({{
        'pid': child.pid, 'group': os.getpgid(child.pid), 'owner_group': os.getpgrp(),
    }}))
    os._exit(0)
"""
    sandbox = tmp_path / "sandbox"
    env = isolated_environment(os.environ, sandbox)
    assert run_contained(
        [sys.executable, "-c", script], cwd=repo, env=env, sandbox=sandbox,
        limits=Limits(wall_seconds=15, poll_seconds=0.1),
    ) == 0
    identity = json.loads(receipt.read_text())
    assert identity["group"] == identity["owner_group"]
    state = Path(f"/proc/{identity['pid']}/stat")
    assert not state.exists() or state.read_text().split(") ", 1)[1].startswith("Z ")


def test_ci_path_gates_smoke_and_promotion_keeps_exhaustive():
    root = SCRIPT.parent.parent
    _assert_ci_path_gates_smoke_and_promotion_keeps_exhaustive(
        source_workflow(root, "ci.yml")["jobs"],
        source_workflow(root, "validate-and-promote.yml")["jobs"],
    )


def _assert_ci_path_gates_smoke_and_promotion_keeps_exhaustive(ci, promotion):
    import shlex
    from packaging.requirements import Requirement

    discovery = next(step["run"] for step in ci["discover"]["steps"] if step.get("id") == "set")
    detector = next(
        step["run"] for step in ci["checks"]["steps"] if step.get("id") == "version-bump-engine"
    )
    assert r"standalone_consumers\.py" in detector
    for path in ("services/index/", "plugins/agent-index/", "libs/", "tools/"):
        assert path in discovery
    for name in ("test-runner-linux", "test-runner-windows"):
        command = next(
            step["run"] for step in ci[name]["steps"]
            if step.get("name") == "Test runner admission and isolation"
        )
        install = shlex.split(command.split("&&", 1)[0])
        assert "tools/seasoned_uv.py" in install and "--system" in install
        requirements = {
            requirement.name: requirement
            for requirement in map(Requirement, install[install.index("--system") + 1:])
        }
        assert {"pytest", "pytest-timeout", "pyyaml", "setuptools", "wheel"} <= requirements.keys()
        assert "83.0.0" in requirements["setuptools"].specifier
        assert "82.9.0" not in requirements["setuptools"].specifier
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert ci[name]["needs"] == "discover"
        assert "needs.discover.outputs.standalone_service == 'true'" in ci[name]["if"]
        command = next(step["run"] for step in ci[name]["steps"] if "run" in step)
        assert 'MODE="--smoke"' in command
        assert "workflow_dispatch" in command
        full = next(step["run"] for step in promotion[name]["steps"] if "run-standalone-tests" in step.get("run", ""))
        assert "--smoke" not in full


def test_sandbox_cleanup_retries_only_transient_windows_lock(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.time, "sleep", lambda delay: None)
    attempts = []

    class Temporary:
        def cleanup(self):
            attempts.append(True)
            if len(attempts) == 1:
                error = OSError("directory not empty")
                error.winerror = 145
                raise error

    runner.cleanup_sandbox(Temporary())
    assert len(attempts) == 2


def test_sandbox_cleanup_does_not_hide_permanent_failure(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")

    class Temporary:
        def cleanup(self):
            raise OSError("permanent failure")

    with pytest.raises(OSError, match="permanent failure"):
        runner.cleanup_sandbox(Temporary())


@pytest.mark.skipif(runner.os.name != "nt", reason="Windows extended-length cleanup")
def test_sandbox_cleanup_removes_deep_owned_paths():
    temporary = runner.tempfile.TemporaryDirectory(prefix="standalone-cleanup-test-")
    root = Path(temporary.name)
    deep = root
    for index in range(8):
        deep = deep / (f"nested-{index}-" + "a" * 30)
    extended = Path("\\\\?\\" + str(deep.resolve()))
    extended.mkdir(parents=True)
    blob = extended / "readonly-git-object"
    blob.write_text("fixture")
    blob.chmod(stat.S_IREAD)
    assert not blob.stat().st_mode & stat.S_IWRITE
    runner.cleanup_sandbox(temporary)
    assert not root.exists()


def _access_denied(path, *, winerror=5):
    error = PermissionError("fixture access denied", str(path))
    error.winerror = winerror
    return error


def test_readonly_cleanup_retries_only_the_owned_file(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    blob = root / "object"
    blob.write_text("fixture")
    blob.chmod(stat.S_IREAD)
    error = _access_denied(blob)
    runner._remove_readonly_file(root.resolve(), os.unlink, str(blob), (type(error), error, None))
    assert not blob.exists()


@pytest.mark.parametrize("reason", ["outside", "writable", "sharing", "non-unlink", "hardlinked"])
def test_readonly_cleanup_preserves_unowned_or_non_readonly_failures(tmp_path, reason):
    root = tmp_path / "owned"
    root.mkdir()
    blob = (tmp_path if reason == "outside" else root) / "object"
    blob.write_text("fixture")
    if reason == "hardlinked":
        os.link(blob, tmp_path / "external-link")
    if reason != "writable":
        blob.chmod(stat.S_IREAD)
    before = blob.stat().st_mode
    error = _access_denied(blob, winerror=32 if reason == "sharing" else 5)
    function = os.rmdir if reason == "non-unlink" else os.unlink
    try:
        with pytest.raises(PermissionError) as failure:
            runner._remove_readonly_file(root.resolve(), function, str(blob), (type(error), error, None))
        assert failure.value is error
        assert blob.exists() and blob.stat().st_mode == before
    finally:
        blob.chmod(before | stat.S_IWRITE)


def test_readonly_cleanup_never_changes_a_symlink_target(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("fixture")
    link = root / "link"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("native symlink creation requires privilege")
        raise
    outside.chmod(stat.S_IREAD)
    before = outside.stat().st_mode
    error = _access_denied(link)
    try:
        with pytest.raises(PermissionError):
            runner._remove_readonly_file(root.resolve(), os.unlink, str(link), (type(error), error, None))
        assert link.is_symlink() and outside.stat().st_mode == before
    finally:
        outside.chmod(before | stat.S_IWRITE)


def test_readonly_cleanup_allows_hardlinks_only_when_every_link_is_owned(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    blob = root / "object"
    blob.write_text("fixture")
    second = root / "second-object"
    os.link(blob, second)
    blob.chmod(stat.S_IREAD)
    error = _access_denied(blob)
    link_counts = {}
    runner._remove_readonly_file(
        root.resolve(), os.unlink, str(blob), (type(error), error, None), link_counts=link_counts,
    )
    assert not blob.exists() and second.read_text() == "fixture"
    assert second.stat().st_nlink == 1
    second.chmod(stat.S_IREAD)
    second_error = _access_denied(second)
    runner._remove_readonly_file(
        root.resolve(), os.unlink, str(second),
        (type(second_error), second_error, None), link_counts=link_counts,
    )
    assert not second.exists()


@pytest.mark.skipif(runner.os.name != "nt", reason="Windows read-only hardlink cleanup")
@pytest.mark.parametrize("link_count", [2, 3, 16])
def test_real_sandbox_cleanup_removes_all_owned_readonly_hardlinks(link_count):
    temporary = runner.tempfile.TemporaryDirectory(prefix="standalone-hardlink-cleanup-test-")
    root = Path(temporary.name)
    first = root / "object"
    first.write_text("fixture")
    links = [first]
    for index in range(1, link_count):
        folder = root / f"directory-{index}"
        folder.mkdir()
        link = folder / "object"
        os.link(first, link)
        links.append(link)
    first.chmod(stat.S_IREAD)
    assert first.stat().st_nlink == link_count
    assert all(not link.stat().st_mode & stat.S_IWRITE for link in links)
    try:
        runner.cleanup_sandbox(temporary)
        assert not root.exists()
    finally:
        for link in links:
            if link.exists():
                link.chmod(stat.S_IREAD | stat.S_IWRITE)
        temporary.cleanup()


@pytest.mark.skipif(runner.os.name != "nt", reason="Windows redirected cleanup root")
def test_sandbox_cleanup_refuses_a_redirected_root_before_changing_permissions(tmp_path):
    temporary = runner.tempfile.TemporaryDirectory(prefix="standalone-cleanup-test-")
    root = Path(temporary.name)
    outside = tmp_path / "outside"
    outside.mkdir()
    blob = outside / "object"
    blob.write_text("fixture")
    root.rmdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        temporary.cleanup()
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("native symlink creation requires privilege")
        raise
    blob.chmod(stat.S_IREAD)
    before = blob.stat().st_mode
    try:
        with pytest.raises(OSError, match="redirected"):
            runner.cleanup_sandbox(temporary)
        assert blob.exists() and blob.stat().st_mode == before
    finally:
        root.unlink()
        temporary.cleanup()
        blob.chmod(before | stat.S_IWRITE)


@pytest.mark.parametrize(("path", "expected"), [
    ("C:\\temp\\owned", "\\\\?\\C:\\temp\\owned"),
    ("\\\\server\\share\\owned", "\\\\?\\UNC\\server\\share\\owned"),
    ("\\\\?\\C:\\temp\\owned", "\\\\?\\C:\\temp\\owned"),
    ("\\\\?\\UNC\\server\\share\\owned", "\\\\?\\UNC\\server\\share\\owned"),
])
def test_extended_windows_cleanup_normalizes_unc_without_double_prefix(path, expected):
    assert runner.extended_windows_path(path) == expected
